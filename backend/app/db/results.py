"""Итоги уроков: реплики, ступени, норма и сводки (#23).

Ссылаются на уроки и участников из classes.py (#44): lesson_id — это
lessons.id, а участник хранится двумя полями. participant_id — id в уроке
(«m17» у ученика класса, «ivan_i» и «virtual-ivan_i» у демо и записи),
member_id — members.id, если участник из класса. Только текст, аудио нет.

Норма урока — медиана активности прошлых уроков этого класса. Демо-уроки,
уроки с виртуальным классом и «тихие» уроки в норму не идут. У нового класса
прошлых уроков нет, и норма берётся из data/demo_lesson/history_seed.json.
"""

import json
import re
from datetime import datetime
from pathlib import Path

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, delete, select
from sqlalchemy.orm import Mapped, mapped_column

from app import db
from app.config import settings
from app.db import Base
from app.db.classes import Lesson, now
from app.models import StudentCard, Summary


class UtteranceRow(Base):
    """Реплика урока: ученики с типом, учитель — как контекст классификатора."""

    __tablename__ = "utterances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lesson_id: Mapped[str] = mapped_column(ForeignKey("lessons.id"), index=True)
    participant_id: Mapped[str] = mapped_column(String(40))
    member_id: Mapped[int | None] = mapped_column(ForeignKey("members.id"), default=None, index=True)
    t: Mapped[float] = mapped_column(Float)  # секунды от начала урока
    end: Mapped[float] = mapped_column(Float)
    text: Mapped[str] = mapped_column(Text)
    kind: Mapped[str | None] = mapped_column(String(12), default=None)  # у реплик учителя нет
    addressed: Mapped[bool] = mapped_column(Boolean, default=False)


class LevelRow(Base):
    """Ступень ученика к концу урока: шкала за урок только растёт, поэтому это её максимум."""

    __tablename__ = "levels"

    lesson_id: Mapped[str] = mapped_column(ForeignKey("lessons.id"), primary_key=True)
    participant_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    member_id: Mapped[int | None] = mapped_column(ForeignKey("members.id"), default=None, index=True)
    level: Mapped[int] = mapped_column(Integer)
    score: Mapped[float] = mapped_column(Float)
    flags: Mapped[str] = mapped_column(String(80), default="")  # через запятую


class LessonResult(Base):
    """Итог урока для нормы следующих уроков класса."""

    __tablename__ = "lesson_results"

    lesson_id: Mapped[str] = mapped_column(ForeignKey("lessons.id"), primary_key=True)
    activity: Mapped[float] = mapped_column(Float)  # баллы на ученика (scoring.lesson_activity)
    norm: Mapped[float] = mapped_column(Float)  # норма, по которой шёл урок
    quiet: Mapped[bool] = mapped_column(Boolean)
    counts_for_norm: Mapped[bool] = mapped_column(Boolean)
    finished_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class SummaryRow(Base):
    """Сводка учителю после урока (#21) — как её отдаёт API."""

    __tablename__ = "summaries"

    lesson_id: Mapped[str] = mapped_column(ForeignKey("lessons.id"), primary_key=True)
    payload: Mapped[str] = mapped_column(Text)  # Summary в JSON
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class CardRow(Base):
    """Карточка ученика после урока (#21): её видит только сам ученик."""

    __tablename__ = "cards"

    lesson_id: Mapped[str] = mapped_column(ForeignKey("lessons.id"), primary_key=True)
    participant_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    payload: Mapped[str] = mapped_column(Text)  # StudentCard в JSON


def member_id(participant_id: str) -> int | None:
    """«m17» → 17; у демо и записи участников в классе нет."""
    match = re.fullmatch(r"m(\d+)", participant_id)
    return int(match[1]) if match else None


def stored(lesson_id: str) -> bool:
    """Урок есть в базе. Урок из roster.json (10a-history-05) — нет, его итоги не храним."""
    with db.session() as s:
        return s.get(Lesson, lesson_id) is not None


def save_lesson(
    lesson_id: str,
    utterances: list[dict],
    levels: dict[str, tuple[int, float, list[str]]],
    activity: float,
    norm: float,
    quiet: bool,
    virtual: bool,
) -> bool:
    """Записать итоги урока одной транзакцией. Повторная запись заменяет прежнюю.

    levels — ученик → (ступень, баллы, флаги). False — урока нет в базе.
    """
    with db.session() as s:
        lesson = s.get(Lesson, lesson_id)
        if lesson is None:
            return False
        for table in (UtteranceRow, LevelRow, LessonResult):
            s.execute(delete(table).where(table.lesson_id == lesson_id))
        s.add_all(
            UtteranceRow(
                lesson_id=lesson_id,
                participant_id=u["student_id"],
                member_id=member_id(u["student_id"]),
                t=u["t"],
                end=u["end"],
                text=u["text"],
                kind=u.get("kind"),
                addressed=u.get("kind") == "response",
            )
            for u in utterances
        )
        s.add_all(
            LevelRow(
                lesson_id=lesson_id,
                participant_id=pid,
                member_id=member_id(pid),
                level=level,
                score=score,
                flags=",".join(flags),
            )
            for pid, (level, score, flags) in levels.items()
        )
        s.add(
            LessonResult(
                lesson_id=lesson_id,
                activity=activity,
                norm=norm,
                quiet=quiet,
                counts_for_norm=not (quiet or virtual or lesson.demo or lesson.class_id is None),
            )
        )
        return True


def class_history(class_id: int) -> list[float]:
    """Активность прошлых уроков класса, которые идут в норму, от старых к новым."""
    with db.session() as s:
        rows = s.execute(
            select(LessonResult.activity)
            .join(Lesson, Lesson.id == LessonResult.lesson_id)
            .where(Lesson.class_id == class_id, LessonResult.counts_for_norm)
            .order_by(LessonResult.finished_at)
        )
        return [activity for (activity,) in rows]


def seed_history(path: Path | None = None) -> list[float]:
    """Прошлые уроки из history_seed.json — для класса, у которого своих ещё нет.

    Урок в файле: {"date": "2026-10-01", "activity": 5.5, "quiet": false},
    activity — баллы на ученика. «Тихие» уроки в норму не идут. Поля topic и
    replied (кто из демо-класса ответил) — для сводки, норма их не читает.
    """
    path = path or settings.demo_lesson_dir / "history_seed.json"
    if not path.exists():
        return []  # истории нет — норма по умолчанию (scoring.lesson_norm)
    lessons = json.loads(path.read_text(encoding="utf-8")).get("lessons", [])
    return [float(lesson["activity"]) for lesson in lessons if not lesson.get("quiet")]


def history_for(lesson_id: str) -> list[float]:
    """История для нормы урока: прошлые уроки его класса, а у нового класса — history_seed.json.

    Новый — пока своих уроков меньше двух: иначе норма прыгала бы 4,5 (seed) → 6 (по
    умолчанию для одного урока) → своя. Первые два урока — одна норма из seed (#98, п. 13).
    """
    from app.pipeline.scoring import MIN_LESSONS

    with db.session() as s:
        lesson = s.get(Lesson, lesson_id)
        class_id = lesson.class_id if lesson else None
    history = class_history(class_id) if class_id is not None else []
    return history if len(history) >= MIN_LESSONS else seed_history()


def silent_streaks(lesson_id: str, student_ids: list[str], lessons: int = 5) -> dict[str, int]:
    """Сколько прошлых уроков подряд, от последнего назад, ученик не дал засчитанной реплики.

    Для «спросить в следующий раз» в сводке. Смотрим не больше `lessons` уроков:
    у класса — его уроки, которые идут в норму, у демо — history_seed.json
    (поле replied, id без приставки virtual-). Ученика без истории в ответе нет.
    """
    with db.session() as s:
        lesson = s.get(Lesson, lesson_id)
        class_id = lesson.class_id if lesson else None
        past: list[set[str] | None] = []  # по урокам от нового к старому: кто ответил
        present: list[set[str]] = []  # кто был на уроке
        if class_id is not None:
            ids = s.scalars(
                select(LessonResult.lesson_id)
                .join(Lesson, Lesson.id == LessonResult.lesson_id)
                .where(Lesson.class_id == class_id, Lesson.id != lesson_id, LessonResult.counts_for_norm)
                .order_by(LessonResult.finished_at.desc())
                .limit(lessons)
            ).all()
            for past_id in ids:
                rows = s.scalars(select(LevelRow).where(LevelRow.lesson_id == past_id)).all()
                past.append({r.participant_id for r in rows if r.level >= 2})
                present.append({r.participant_id for r in rows})
    if class_id is None:
        path = settings.demo_lesson_dir / "history_seed.json"
        seeded = json.loads(path.read_text(encoding="utf-8")).get("lessons", []) if path.exists() else []
        for item in reversed(seeded[-lessons:]):
            if "replied" in item:
                past.append(set(item["replied"]))
                present.append(None)  # в файле состав не записан: считаем, что были все
    streaks: dict[str, int] = {}
    for sid in student_ids:
        key = sid.removeprefix("virtual-") if class_id is None else sid
        n = 0
        for replied, was_there in zip(past, present):
            if (was_there is not None and key not in was_there) or key in replied:
                break
            n += 1
        if past and (present[0] is None or key in present[0]):
            streaks[sid] = n
    return streaks


def save_summary(lesson_id: str, summary: Summary) -> None:
    with db.session() as s:
        row = s.get(SummaryRow, lesson_id)
        if row is None:
            s.add(SummaryRow(lesson_id=lesson_id, payload=summary.model_dump_json()))
        else:
            row.payload = summary.model_dump_json()


def get_summary(lesson_id: str) -> Summary | None:
    with db.session() as s:
        row = s.get(SummaryRow, lesson_id)
        return Summary.model_validate_json(row.payload) if row else None


def save_cards(lesson_id: str, cards: list[StudentCard]) -> None:
    with db.session() as s:
        s.execute(delete(CardRow).where(CardRow.lesson_id == lesson_id))
        s.add_all(CardRow(lesson_id=lesson_id, participant_id=c.student_id, payload=c.model_dump_json()) for c in cards)


def get_card(lesson_id: str, student_id: str) -> StudentCard | None:
    with db.session() as s:
        row = s.get(CardRow, (lesson_id, student_id))
        return StudentCard.model_validate_json(row.payload) if row else None


def lesson_utterances(lesson_id: str) -> list[UtteranceRow]:
    with db.session() as s:
        return list(s.scalars(select(UtteranceRow).where(UtteranceRow.lesson_id == lesson_id).order_by(UtteranceRow.t)))


def lesson_levels(lesson_id: str) -> dict[str, LevelRow]:
    with db.session() as s:
        return {row.participant_id: row for row in s.scalars(select(LevelRow).where(LevelRow.lesson_id == lesson_id))}
