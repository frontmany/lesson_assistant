"""REST API урока.

Кто делает запрос, сервер знает по сессии мини-приложения (app/auth.py, #24):
в урок пускает только участников его класса, а начать, завершить урок и
выключить аналитику может только учитель класса.
"""

import asyncio
import logging
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import auth, mocks
from app.auth import User, current_user
from app.bot import max_bot
from app.call import participant_token
from app.config import settings
from app.db import classes, results
from app.models import AnalyticsState, JoinInfo, Roster, StudentCard, Summary
from app.pipeline import runner
from app.sources import virtual_class

router = APIRouter()
log = logging.getLogger(__name__)


def _roster(lesson_id: str) -> Roster:
    """Состав урока из базы: класс урока и его действующие участники (#44)."""
    roster = classes.lesson_roster(lesson_id)
    if roster is None:
        raise HTTPException(404, "Такого урока нет")
    return roster


def _lesson_roster(lesson_id: str, virtual: bool) -> Roster:
    """Состав урока. С виртуальным классом у урока класса — его ученики и ученики
    из записи, у демо-урока — только ученики из записи."""
    roster = _roster(lesson_id)
    if not virtual:
        return roster
    demo, _ = _demo()
    lesson = classes.get_lesson(lesson_id)
    keep_class = lesson is not None and lesson.class_id is not None
    return virtual_class.lesson_roster(roster, demo, keep_class)


def _demo() -> tuple[Roster, list]:
    try:
        return virtual_class.load_tracks(settings.demo_lesson_dir)
    except FileNotFoundError as exc:
        raise HTTPException(503, f"Записи урока нет: {exc}") from exc


# ---------- кто это ----------

@dataclass(frozen=True)
class Seat:
    """Место человека в уроке: его id в звонке и роль."""

    participant_id: str
    name: str
    role: str  # teacher | student


def seat(lesson_id: str, user: User) -> Seat | None:
    """Кто этот человек в уроке. None — не участник класса этого урока."""
    roster = _roster(lesson_id)
    people = [(roster.teacher, "teacher"), *((s, "student") for s in roster.students)]
    for person, role in people:
        mine = person.id == user.dev_participant if user.max_user_id is None else person.max_user_id == user.max_user_id
        if mine:
            return Seat(person.id, person.name, role)
    if user.max_user_id is None and user.dev_participant is None:
        # DEV_AUTH без ?id= — это учитель: так открывается teacher.html при разработке
        return Seat(roster.teacher.id, roster.teacher.name, "teacher")
    return None


def member(lesson_id: str, user: User = Depends(current_user)) -> Seat:
    found = seat(lesson_id, user)
    if found is None:
        raise HTTPException(403, "Вы не в классе этого урока")
    return found


def teacher(lesson_id: str, user: User = Depends(current_user)) -> Seat:
    found = member(lesson_id, user)
    if found.role != "teacher":
        raise HTTPException(403, "Это может только учитель класса")
    return found


# ---------- вход ----------

class InitData(BaseModel):
    init_data: str


class Ticket(BaseModel):
    ticket: str


@router.post("/auth/max")
def auth_max(body: InitData):
    """Данные запуска MAX → сессия мини-приложения. start_param — что открыть: c<класс> или l<урок>."""
    data = auth.check_init_data(body.init_data, settings.max_bot_token) if settings.max_bot_token else None
    if data is None:
        raise HTTPException(401, "Не удалось проверить данные MAX, откройте урок из чата с ботом заново")
    user = data["user"]
    name = " ".join(filter(None, [user.get("first_name"), user.get("last_name")])) or "Без имени"
    return {"token": auth.session_for(user["id"], name), "start_param": data.get("start_param")}


@router.post("/auth/ticket")
def auth_ticket(user: User = Depends(current_user)):
    """Одноразовая ссылка, чтобы открыть урок в браузере, когда в MAX нет микрофона."""
    if user.max_user_id is None:
        raise HTTPException(400, "В режиме разработки ссылка не нужна: участник уже в адресе")
    return {"ticket": auth.ticket_for(user)}


@router.post("/auth/browser")
def auth_browser(body: Ticket):
    token = auth.redeem_ticket(body.ticket)
    if token is None:
        raise HTTPException(401, "Ссылка устарела: откройте урок из чата с ботом ещё раз")
    return {"token": token}


@router.get("/lessons/{lesson_id}/me")
def me(lesson_id: str, who: Seat = Depends(member)):
    """Кто я в этом уроке: мини-приложение открывает экран учителя или ученика.

    analytics — ведётся ли аналитика этого участника (учитель может выключить её ученику).
    """
    return {
        "participant_id": who.participant_id, "name": who.name, "role": who.role,
        "analytics": who.role == "teacher" or not runner.analytics_off(lesson_id, who.participant_id),
    }


# ---------- урок ----------

@router.get("/lessons/{lesson_id}/roster", response_model=Roster, dependencies=[Depends(member)])
def roster(lesson_id: str):
    """Состав идущего урока, с виртуальными учениками, если они есть; иначе состав класса."""
    return runner.roster_of(lesson_id) or _roster(lesson_id)


@router.get("/lessons/{lesson_id}/join", response_model=JoinInfo)
def join(lesson_id: str, who: Seat = Depends(member)):
    """Адрес LiveKit и токен для входа в звонок урока: под своим id и только участнику класса."""
    return JoinInfo(
        url=settings.livekit_public_url,
        token=participant_token(lesson_id, who.participant_id, who.name),
        participant_id=who.participant_id,
        name=who.name,
        role=who.role,
    )


@router.get("/me/classes")
def my_classes(user: User = Depends(current_user)):
    """Классы вошедшего для стартовой страницы: учителю — «Начать урок», ученику — идущий урок.

    lesson — идущий урок класса (учитель уже в звонке), иначе null.
    """
    if user.max_user_id is None:
        return []
    out = []
    for c, m in classes.classes_of(user.max_user_id):
        live = classes.live_lesson(c.id)
        out.append({
            "id": c.id, "name": c.name, "subject": c.subject, "role": m.role,
            "students": len(classes.students_of(c.id)), "lesson": live.id if live else None,
        })
    return out


@router.post("/demo")
def demo(user: User = Depends(current_user)):
    """Свой демо-урок: вошедший — учитель, класс заходит записью. Для стартовой страницы, как «Посмотреть демо» в боте."""
    return {"lesson_id": classes.demo_lesson(user.max_user_id, user.name or None).id}


def _taught_class(class_id: int, user: User, deleted: bool = False):
    """Класс, которым управляет вошедший учитель: иначе 404 или 403. deleted — и удалённый (для «Вернуть»)."""
    c = classes.get_class(class_id)
    if c is None or (c.deleted_at is not None and not deleted):
        raise HTTPException(404, "Такого класса нет")
    m = classes.membership(class_id, user.max_user_id) if user.max_user_id is not None else None
    if m is None or m.role != "teacher":
        raise HTTPException(403, "Это может только учитель класса")
    return c


def _class_student(class_id: int, member_id: int):
    m = classes.get_member(member_id)
    if m is None or m.class_id != class_id or m.role != "student":
        raise HTTPException(404, "Такого ученика в классе нет")
    return m


class NewClass(BaseModel):
    name: str
    subject: str


def _class_fields(body: NewClass) -> tuple[str, str]:
    """Класс и предмет с теми же проверками, что в боте: «10А, история»."""
    name, subject = body.name.strip(), body.subject.strip()
    if not name or not subject or len(name) > 40 or len(subject) > 60:
        raise HTTPException(422, "Укажите класс и предмет. Например: 10А и история")
    return name, subject.lower()


@router.post("/classes")
def create_class(body: NewClass, user: User = Depends(current_user)):
    """«Создать класс» в мини-приложении: как в боте, вошедший становится учителем класса."""
    if user.max_user_id is None:
        raise HTTPException(403, "Создать класс можно из MAX")
    name, subject = _class_fields(body)
    c = classes.create_class(user.max_user_id, user.name or "Учитель", name, subject)
    return {"id": c.id}


@router.patch("/classes/{class_id}")
def update_class(class_id: int, body: NewClass, user: User = Depends(current_user)):
    """Карандаш на экране класса: новое название и предмет."""
    _taught_class(class_id, user)
    classes.update_class(class_id, *_class_fields(body))
    return {"updated": class_id}


@router.get("/classes/{class_id}")
def class_card(class_id: int, user: User = Depends(current_user)):
    """Экран класса в мини-приложении (design/classes.html): ученики по алфавиту, ссылка и код для приглашения."""
    c = _taught_class(class_id, user)
    return {
        "id": c.id, "name": c.name, "subject": c.subject,
        "code": c.invite_code, "link": max_bot.invite_link(c.invite_code),
        "students": [{"id": m.id, "name": classes.short_name(m.name)} for m in classes.students_of(c.id)],
        "lesson": live.id if (live := classes.live_lesson(c.id)) else None,
    }


@router.delete("/classes/{class_id}")
def delete_class(class_id: int, user: User = Depends(current_user)):
    """Удалить класс: как в боте, 30 дней его можно вернуть («Вернуть» на экране)."""
    _taught_class(class_id, user)
    if classes.live_lesson(class_id):
        raise HTTPException(409, "Идёт урок: удалить класс можно после него")
    classes.delete_class(class_id)
    return {"deleted": class_id}


@router.post("/classes/{class_id}/restore")
def restore_class(class_id: int, user: User = Depends(current_user)):
    _taught_class(class_id, user, deleted=True)
    if not classes.restore_class(class_id):
        raise HTTPException(410, "Класс удалён больше 30 дней назад")
    return {"restored": class_id}


@router.delete("/classes/{class_id}/students/{member_id}")
def remove_student(class_id: int, member_id: int, user: User = Depends(current_user)):
    """«Убрать из класса»: сразу, без вопроса — на экране есть «Вернуть»."""
    _taught_class(class_id, user)
    classes.remove_member(_class_student(class_id, member_id).id)
    return {"removed": member_id}


@router.post("/classes/{class_id}/students/{member_id}/restore")
def restore_student(class_id: int, member_id: int, user: User = Depends(current_user)):
    _taught_class(class_id, user)
    classes.restore_member(_class_student(class_id, member_id).id)
    return {"restored": member_id}


@router.post("/classes/{class_id}/lessons")
def lesson_for_class(class_id: int, user: User = Depends(current_user)):
    """«Начать урок» в боте: урок класса, идущий или новый. Мини-приложение открывает его звонок."""
    c = classes.get_class(class_id)
    if c is None or c.deleted_at is not None:
        raise HTTPException(404, "Такого класса нет")
    m = classes.membership(class_id, user.max_user_id) if user.max_user_id is not None else None
    if user.max_user_id is not None and (m is None or m.role != "teacher"):
        raise HTTPException(403, "Это может только учитель класса")
    return {"lesson_id": classes.lesson_for_class(class_id).id}


@router.post("/lessons/{lesson_id}/start", dependencies=[Depends(teacher)])
async def start(lesson_id: str, virtual: bool = False):
    """Учитель вошёл в звонок: ассистент заходит и слушает, ученики класса получают «Урок начался».

    Демо-урок начинается сразу с записанным классом: так учитель или жюри
    показывают урок в одиночку. Пустой класс урок не начинает — для этого
    «Посмотреть демо» в боте и мини-приложении. virtual=true —
    в составе урока ученики из записи: так ассистент засчитает ботов, которых
    заводит `python -m app.cli virtual-class`.
    """
    lesson = classes.get_lesson(lesson_id)
    if lesson is not None and lesson.demo:
        return {"lesson_id": lesson_id, "started": _start_virtual(lesson_id)}
    if lesson is not None and lesson.ended_at is not None:
        # новый прогон завершённого урока затёр бы его итоги (results.save_lesson)
        raise HTTPException(409, "Урок уже завершён: начните новый урок из чата с ботом")
    started = runner.start(lesson_id, _lesson_roster(lesson_id, virtual))
    if started:
        runner.when_finished(lesson_id, classes.mark_ended)
    if classes.mark_live(lesson_id):
        # рассылка ходит в API MAX, цикл событий она не держит
        asyncio.get_running_loop().run_in_executor(None, max_bot.lesson_started, lesson_id)
    return {"lesson_id": lesson_id, "started": started}


def _start_virtual(lesson_id: str) -> bool:
    demo, tracks = _demo()
    if runner.is_running(lesson_id) and runner.roster_of(lesson_id).virtual_teacher is None:
        raise HTTPException(409, "Урок уже идёт без виртуального класса: завершите его и начните заново")
    if runner.start(lesson_id, _lesson_roster(lesson_id, True)):
        runner.when_finished(lesson_id, classes.mark_ended)
    classes.mark_live(lesson_id)
    return runner.play_virtual(lesson_id, demo, tracks)


@router.post("/lessons/{lesson_id}/virtual-class", dependencies=[Depends(teacher)])
async def start_virtual_class(lesson_id: str):
    """Записанный урок заходит в звонок ботами вместо учеников (#18, демо для #45).

    Урок начинается, если ещё не идёт, и сразу с учениками из записи. Если урок
    уже идёт без них, ассистент не засчитал бы ботов, поэтому ответ 409.
    """
    return {"lesson_id": lesson_id, "playing": _start_virtual(lesson_id)}


@router.delete("/lessons/{lesson_id}/virtual-class", dependencies=[Depends(teacher)])
async def stop_virtual_class(lesson_id: str):
    """Боты виртуального класса выходят из звонка, урок идёт дальше."""
    stopped = await runner.stop_virtual(lesson_id)
    return {"lesson_id": lesson_id, "stopped": stopped}


@router.post("/lessons/{lesson_id}/end", dependencies=[Depends(teacher)])
async def end(lesson_id: str):
    """Учитель нажал «Завершить урок»: ответ приходит, когда очередь дораспознана и ассистент вышел."""
    ended = await runner.end(lesson_id)
    classes.mark_ended(lesson_id)
    return {"lesson_id": lesson_id, "ended": ended}


@router.get("/lessons/{lesson_id}/summary", response_model=Summary, dependencies=[Depends(teacher)])
def summary(lesson_id: str):
    # сводка из базы (#21); для урока, который ещё идёт или не из базы, — мок
    return results.get_summary(lesson_id) or mocks.load("summary.json")


@router.get("/lessons/{lesson_id}/cards/{student_id}", response_model=StudentCard)
def card(lesson_id: str, student_id: str, who: Seat = Depends(member)):
    if who.role != "teacher" and who.participant_id != student_id:
        raise HTTPException(403, "Это карточка другого ученика")
    card = results.get_card(lesson_id, student_id)
    if card is not None:
        return card
    if results.get_summary(lesson_id) is not None:
        # сводка есть, а карточки нет: аналитика была выключена или сказать нечего
        raise HTTPException(404, "Карточки по этому уроку нет")
    return next(c for c in mocks.load("cards.json") if c["student_id"] == student_id)


@router.post("/lessons/{lesson_id}/analytics", response_model=AnalyticsState, dependencies=[Depends(teacher)])
def set_analytics(lesson_id: str, state: AnalyticsState):
    """Учитель выключил или включил аналитику ученику (student_id) или всему классу (null).

    Выключенный ученик не попадает ни в шкалы, ни в итоги урока; ему и учителю
    уходит событие analytics.
    """
    runner.set_analytics(lesson_id, state.enabled, state.student_id)
    from app.ws import lesson as ws

    ws.publish(lesson_id, state)
    return state


@router.get("/lessons/{lesson_id}/analytics", dependencies=[Depends(teacher)])
def analytics_state(lesson_id: str):
    """У кого в уроке выключена аналитика: панель учителя восстанавливает выключатели после перезагрузки."""
    off = runner.analytics_off_list(lesson_id)
    return {"all_off": runner.ALL in off, "off": [sid for sid in off if sid != runner.ALL]}
