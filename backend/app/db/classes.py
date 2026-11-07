"""Классы, участники и уроки (#44).

Класс постоянный: учитель создаёт его в боте, ученики входят по коду класса
один раз и дальше получают все его уроки. Удаление мягкое: класс 30 дней
можно вернуть, вышедший ученик остаётся в базе, чтобы его реплики в прошлых
уроках не потерялись.

id участника в уроке (identity в LiveKit, student_id в событиях) — «m<members.id>».
Демо-урок идёт без класса: состав из data/demo_lesson/roster.json, а учитель —
тот, кто попросил демо.
"""

import re
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app import db
from app.config import settings
from app.db import Base
from app.models import Roster, Student

# Код класса: шесть знаков без похожих (нет 0 и O, 1 и I), его диктуют на уроке
CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
CODE_LENGTH = 6
RESTORE_DAYS = 30
# Урок, в который учитель так и не вошёл, через столько часов не переиспользуем
LESSON_STALE_H = 3
# Урок из roster.json: на нём работают разработка, демо-запись и тесты
DEMO_LESSON_ID = "10a-history-05"

# Код могут набрать русскими буквами: похожими на латинские («К7Х2М9»)
# или на русской раскладке («Л7Ч2Ь9»). Пробуем оба прочтения
_LOOKALIKE = str.maketrans("АВЕКМНОРСТУХ", "ABEKMHOPCTYX")
_LAYOUT = str.maketrans("ЙЦУКЕНГШЩЗФЫВАПРОЛДЯЧСМИТЬ", "QWERTYUIOPASDFGHJKLZXCVBNM")


def now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Class(Base):
    __tablename__ = "classes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(40))
    subject: Mapped[str] = mapped_column(String(60))
    invite_code: Mapped[str] = mapped_column(String(CODE_LENGTH), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)


class InviteCode(Base):
    """Все коды класса, включая заменённые: на старый код бот отвечает «ссылка устарела»."""

    __tablename__ = "invite_codes"

    code: Mapped[str] = mapped_column(String(CODE_LENGTH), primary_key=True)
    class_id: Mapped[int] = mapped_column(ForeignKey("classes.id"))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)


class Member(Base):
    __tablename__ = "members"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    class_id: Mapped[int] = mapped_column(ForeignKey("classes.id"), index=True)
    max_user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    role: Mapped[str] = mapped_column(String(10))  # teacher | student
    name: Mapped[str] = mapped_column(String(80))
    joined_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    left_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)

    @property
    def participant_id(self) -> str:
        return f"m{self.id}"


class Lesson(Base):
    __tablename__ = "lessons"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)  # комната lesson-<id>
    class_id: Mapped[int | None] = mapped_column(ForeignKey("classes.id"), default=None, index=True)
    demo: Mapped[bool] = mapped_column(Boolean, default=False)
    # учитель демо-урока: у демо нет класса, а значит и учителя в members
    demo_teacher_max_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    demo_teacher_name: Mapped[str | None] = mapped_column(String(80), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    live_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)  # учитель вошёл в звонок
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)


# ---------- имена и коды ----------

def short_name(full: str) -> str:
    """«Иван Иванов» → «Иван И.»: так ученик подписан в классе и в звонке."""
    words = full.split()
    if len(words) < 2:
        return full.strip()
    return f"{words[0]} {words[-1][0].upper()}."


def code_candidates(text: str) -> list[str]:
    """Как человек мог набрать код: латиницей, похожими русскими буквами, на русской раскладке."""
    raw = re.sub(r"[\s\-]", "", text).upper()
    out = []
    for table in (_LOOKALIKE, _LAYOUT):
        code = raw.translate(table)
        if len(code) == CODE_LENGTH and code not in out:
            out.append(code)
    return out


def _new_code(s: Session) -> str:
    while True:
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        if s.get(InviteCode, code) is None:
            return code


def _restorable(c: Class) -> bool:
    return c.deleted_at is not None and now() - c.deleted_at < timedelta(days=RESTORE_DAYS)


# ---------- классы ----------

def create_class(teacher_max_id: int, teacher_name: str, name: str, subject: str) -> Class:
    with db.session() as s:
        code = _new_code(s)
        c = Class(name=name, subject=subject, invite_code=code)
        s.add(c)
        s.flush()
        s.add(InviteCode(code=code, class_id=c.id))
        s.add(Member(class_id=c.id, max_user_id=teacher_max_id, role="teacher", name=teacher_name))
        _set_role(s, teacher_max_id, "teacher")  # создал класс — значит, учитель
        return c


def get_class(class_id: int) -> Class | None:
    with db.session() as s:
        return s.get(Class, class_id)


def find_by_code(text: str) -> tuple[str, Class | None]:
    """Код из сообщения или ссылки: ("ok" | "old" | "deleted" | "none", класс)."""
    with db.session() as s:
        for code in code_candidates(text):
            row = s.get(InviteCode, code)
            if row is None:
                continue
            c = s.get(Class, row.class_id)
            if c.deleted_at is not None:
                return "deleted", c
            return ("old" if row.revoked_at else "ok"), c
    return "none", None


def new_code(class_id: int) -> Class:
    """Новая ссылка и код; старые перестают принимать новых учеников."""
    with db.session() as s:
        c = s.get(Class, class_id)
        s.get(InviteCode, c.invite_code).revoked_at = now()
        c.invite_code = _new_code(s)
        s.add(InviteCode(code=c.invite_code, class_id=c.id))
        return c


def update_class(class_id: int, name: str, subject: str) -> None:
    """Изменить название и предмет: ученики, ссылка и уроки остаются."""
    with db.session() as s:
        c = s.get(Class, class_id)
        c.name, c.subject = name, subject


def delete_class(class_id: int) -> None:
    with db.session() as s:
        s.get(Class, class_id).deleted_at = now()


def restore_class(class_id: int) -> bool:
    """Вернуть удалённый класс с учениками и ссылкой. False — 30 дней прошло."""
    with db.session() as s:
        c = s.get(Class, class_id)
        if not _restorable(c):
            return False
        c.deleted_at = None
        return True


# ---------- участники ----------

def _active(s: Session, class_id: int, max_user_id: int) -> Member | None:
    return s.scalar(select(Member).where(
        Member.class_id == class_id, Member.max_user_id == max_user_id, Member.left_at.is_(None)))


def membership(class_id: int, max_user_id: int) -> Member | None:
    with db.session() as s:
        return _active(s, class_id, max_user_id)


def join(class_id: int, max_user_id: int, name: str) -> tuple[Member, bool]:
    """Ученик входит в класс. (участник, вошёл ли только что)."""
    with db.session() as s:
        member = _active(s, class_id, max_user_id)
        if member is not None:
            # «Изменить имя» после «Это я»: уже вошедший меняет имя, а не остаётся со старым (#98, п. 17)
            member.name = name
            return member, False
        member = Member(class_id=class_id, max_user_id=max_user_id, role="student", name=name)
        s.add(member)
        _set_role(s, max_user_id, "student")  # вошёл в класс по коду — значит, ученик
        s.flush()
        return member, True


def get_member(member_id: int) -> Member | None:
    with db.session() as s:
        return s.get(Member, member_id)


def remove_member(member_id: int) -> Member:
    """Ученик вышел сам или его убрал учитель."""
    with db.session() as s:
        m = s.get(Member, member_id)
        m.left_at = m.left_at or now()
        return m


def restore_member(member_id: int) -> Member:
    """«Вернуть»: тот же участник, его прошлые реплики остаются его."""
    with db.session() as s:
        m = s.get(Member, member_id)
        if _active(s, m.class_id, m.max_user_id) is None:
            m.left_at = None
        return m


def teacher_of(class_id: int) -> Member:
    with db.session() as s:
        return s.scalar(select(Member).where(Member.class_id == class_id, Member.role == "teacher"))


def students_of(class_id: int) -> list[Member]:
    with db.session() as s:
        return list(s.scalars(select(Member).where(
            Member.class_id == class_id, Member.role == "student", Member.left_at.is_(None)
        ).order_by(Member.name)))


def classes_of(max_user_id: int) -> list[tuple[Class, Member]]:
    """Классы человека, где он сейчас учитель или ученик, без удалённых."""
    with db.session() as s:
        rows = s.execute(select(Class, Member).join(Member, Member.class_id == Class.id).where(
            Member.max_user_id == max_user_id, Member.left_at.is_(None), Class.deleted_at.is_(None),
        ).order_by(Class.created_at))
        return [(c, m) for c, m in rows]


# ---------- уроки ----------

def _lesson_id(s: Session) -> str:
    while True:
        lesson_id = secrets.token_hex(4)
        if s.get(Lesson, lesson_id) is None:
            return lesson_id


def lesson_for_class(class_id: int) -> Lesson:
    """Урок класса для «Начать урок»: идущий или только что созданный, иначе новый."""
    with db.session() as s:
        recent = now() - timedelta(hours=LESSON_STALE_H)
        lesson = s.scalar(select(Lesson).where(
            Lesson.class_id == class_id, Lesson.ended_at.is_(None),
            (Lesson.live_at.is_not(None)) | (Lesson.created_at > recent),
        ).order_by(Lesson.created_at.desc()))
        if lesson is None:
            lesson = Lesson(id=_lesson_id(s), class_id=class_id)
            s.add(lesson)
        return lesson


def demo_lesson(max_user_id: int, name: str) -> Lesson:
    """Свой демо-урок: человек учитель, класс заходит записью (#18)."""
    with db.session() as s:
        lesson = Lesson(id=_lesson_id(s), demo=True, demo_teacher_max_id=max_user_id, demo_teacher_name=name)
        s.add(lesson)
        return lesson


def get_lesson(lesson_id: str) -> Lesson | None:
    with db.session() as s:
        return s.get(Lesson, lesson_id)


def live_lesson(class_id: int) -> Lesson | None:
    """Урок класса, в котором учитель уже в звонке."""
    with db.session() as s:
        return s.scalar(select(Lesson).where(
            Lesson.class_id == class_id, Lesson.live_at.is_not(None), Lesson.ended_at.is_(None)))


def mark_live(lesson_id: str) -> bool:
    """Учитель вошёл в звонок. True — впервые: пора звать учеников."""
    with db.session() as s:
        lesson = s.get(Lesson, lesson_id)
        if lesson is None or lesson.live_at is not None:
            return False
        lesson.live_at = now()
        return True


def open_lessons_of_teacher(max_user_id: int) -> list[str]:
    """Незавершённые уроки классов, где человек учитель: их закрывает «Выйти»."""
    with db.session() as s:
        return list(s.scalars(select(Lesson.id).join(Member, Member.class_id == Lesson.class_id).where(
            Member.max_user_id == max_user_id, Member.role == "teacher", Member.left_at.is_(None),
            Lesson.ended_at.is_(None),
        )))


def mark_ended(lesson_id: str) -> None:
    with db.session() as s:
        lesson = s.get(Lesson, lesson_id)
        if lesson is not None and lesson.ended_at is None:
            lesson.ended_at = now()


def _demo_roster() -> Roster:
    return Roster.model_validate_json((settings.demo_lesson_dir / "roster.json").read_text(encoding="utf-8"))


def lesson_roster(lesson_id: str) -> Roster | None:
    """Состав урока: учитель и действующие ученики класса. None — урока нет."""
    if lesson_id == DEMO_LESSON_ID:
        return _demo_roster()
    with db.session() as s:
        lesson = s.get(Lesson, lesson_id)
        if lesson is None:
            return None
        if lesson.demo:
            roster = _demo_roster()
            teacher = roster.teacher.model_copy(update={
                "name": lesson.demo_teacher_name or roster.teacher.name,
                "max_user_id": lesson.demo_teacher_max_id,
            })
            return roster.model_copy(update={"lesson_id": lesson_id, "teacher": teacher})
        c = s.get(Class, lesson.class_id)
    teacher = teacher_of(c.id)
    return Roster(
        lesson_id=lesson_id,
        class_name=c.name,
        subject=c.subject,
        topic="",
        teacher=Student(id=teacher.participant_id, name=teacher.name, max_user_id=teacher.max_user_id),
        students=[
            Student(id=m.participant_id, name=short_name(m.name), full_name=m.name, max_user_id=m.max_user_id)
            for m in students_of(c.id)
        ],
    )


# ---------- роль: общая для бота и мини-приложения ----------

ROLES = ("teacher", "student", "none")


class UserRole(Base):
    """Кто человек сейчас: учитель, ученик или none — нажал «Выйти» и роль ещё не выбрал.

    Одна на бота и мини-приложение: выбор «Я учитель» в чате видно в приложении и наоборот.
    Нет строки — человек роль не выбирал, её выводят из его классов.
    """

    __tablename__ = "user_roles"

    max_user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    role: Mapped[str] = mapped_column(String(10))


def _set_role(s: Session, max_user_id: int, role: str) -> None:
    r = s.get(UserRole, max_user_id)
    if r is None:
        s.add(UserRole(max_user_id=max_user_id, role=role))
    else:
        r.role = role


def set_role(max_user_id: int, role: str) -> None:
    with db.session() as s:
        _set_role(s, max_user_id, role)


def role_of(max_user_id: int) -> str:
    """Роль для стартовой страницы: выбранная, иначе по классам — учитель, ученик или none."""
    with db.session() as s:
        r = s.get(UserRole, max_user_id)
        if r is not None:
            return r.role
    mine = classes_of(max_user_id)
    if any(m.role == "teacher" for _, m in mine):
        return "teacher"
    return "student" if mine else "none"


# ---------- чего бот ждёт от человека ----------

class Dialog(Base):
    """Шаг разговора с ботом: название класса, код или имя. Переживает перезапуск сервера."""

    __tablename__ = "bot_dialogs"

    max_user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    step: Mapped[str] = mapped_column(String(20))  # class_name | code | name
    class_id: Mapped[int | None] = mapped_column(Integer, default=None)


def dialog_step(max_user_id: int) -> tuple[str | None, int | None]:
    with db.session() as s:
        d = s.get(Dialog, max_user_id)
        return (d.step, d.class_id) if d else (None, None)


def set_dialog(max_user_id: int, step: str | None, class_id: int | None = None) -> None:
    with db.session() as s:
        d = s.get(Dialog, max_user_id)
        if step is None:
            if d is not None:
                s.delete(d)
        elif d is None:
            s.add(Dialog(max_user_id=max_user_id, step=step, class_id=class_id))
        else:
            d.step, d.class_id = step, class_id
