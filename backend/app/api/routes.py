"""REST API урока"""

import asyncio
import logging
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import auth, mocks
from app.auth import User, current_user
from app.bot import max_bot
from app import call
from app.call import participant_token
from app.config import settings
from app.db import classes, results
from app.models import AnalyticsState, JoinInfo, Roster, StudentCard, Summary
from app.pipeline import runner
from app.sources import virtual_class

router = APIRouter()
log = logging.getLogger(__name__)


def _roster(lesson_id: str) -> Roster:
    roster = classes.lesson_roster(lesson_id)
    if roster is None:
        raise HTTPException(404, "Такого урока нет")
    return roster


def _lesson_roster(lesson_id: str, virtual: bool) -> Roster:
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


@dataclass(frozen=True)
class Seat:
    participant_id: str
    name: str
    role: str


def seat(lesson_id: str, user: User) -> Seat | None:
    """Кто этот человек в уроке, None если не участник класса"""
    roster = _roster(lesson_id)
    people = [(roster.teacher, "teacher"), *((s, "student") for s in roster.students)]
    for person, role in people:
        mine = person.id == user.dev_participant if user.max_user_id is None else person.max_user_id == user.max_user_id
        if mine:
            return Seat(person.id, person.name, role)
    if user.max_user_id is None and user.dev_participant is None:
        # без id в адресе это учитель
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


class InitData(BaseModel):
    init_data: str


class Ticket(BaseModel):
    ticket: str


@router.post("/auth/max")
def auth_max(body: InitData):
    """Данные запуска MAX меняются на сессию приложения"""
    data = auth.check_init_data(body.init_data, settings.max_bot_token) if settings.max_bot_token else None
    if data is None:
        raise HTTPException(401, "Не удалось проверить данные MAX, откройте урок из чата с ботом заново")
    user = data["user"]
    name = " ".join(filter(None, [user.get("first_name"), user.get("last_name")])) or "Без имени"
    return {"token": auth.session_for(user["id"], name), "start_param": data.get("start_param")}


@router.post("/auth/ticket")
def auth_ticket(user: User = Depends(current_user)):
    """Одноразовая ссылка, чтобы открыть урок в браузере"""
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
    lesson = classes.get_lesson(lesson_id)
    demo = lesson is not None and lesson.demo
    ended = lesson is not None and lesson.ended_at is not None and not demo and not runner.is_running(lesson_id)
    return {
        "participant_id": who.participant_id, "name": who.name, "role": who.role,
        "analytics": who.role == "teacher" or not runner.analytics_off(lesson_id, who.participant_id),
        "ended": ended, "demo": demo,
    }


@router.get("/lessons/{lesson_id}/roster", response_model=Roster, dependencies=[Depends(member)])
def roster(lesson_id: str):
    return runner.roster_of(lesson_id) or _roster(lesson_id)


@router.get("/lessons/{lesson_id}/join", response_model=JoinInfo)
def join(lesson_id: str, who: Seat = Depends(member)):
    return JoinInfo(
        url=settings.livekit_public_url,
        token=participant_token(lesson_id, who.participant_id, who.name, teacher=who.role == "teacher"),
        participant_id=who.participant_id,
        name=who.name,
        role=who.role,
    )


class ClassCode(BaseModel):
    code: str


@router.post("/classes/join")
def join_class(body: ClassCode, user: User = Depends(current_user)):
    if user.max_user_id is None:
        raise HTTPException(403, "Войти в класс можно из MAX")
    status, c = classes.find_by_code(body.code)
    if status == "none":
        raise HTTPException(404, "Такого кода нет. Проверьте и введите ещё раз")
    member = classes.membership(c.id, user.max_user_id) if c else None
    if member is not None:
        if member.role == "teacher":
            raise HTTPException(409, f"Это код вашего класса {c.name}")
        return {"id": c.id}
    if status == "deleted":
        raise HTTPException(410, "Этот класс удалён. Спросите у учителя")
    if status == "old":
        raise HTTPException(410, "Этот код устарел. Попросите у учителя новый")
    max_bot.join_from_app(user.max_user_id, c.id, user.name or "Ученик")
    return {"id": c.id}


@router.post("/me/exit")
async def exit_app(user: User = Depends(current_user)):
    if user.max_user_id is None:
        return {"ended": []}
    loop = asyncio.get_running_loop()
    ended = await loop.run_in_executor(None, classes.open_lessons_of_teacher, user.max_user_id)

    async def close(lesson_id: str) -> None:
        await runner.stop_virtual(lesson_id)
        await asyncio.gather(runner.end(lesson_id), call.close_room(lesson_id))
        classes.mark_ended(lesson_id)

    await asyncio.gather(*(close(lesson_id) for lesson_id in ended))
    await loop.run_in_executor(None, classes.set_role, user.max_user_id, "none")
    # запрос к MAX синхронный, выполняем его вне цикла событий
    await loop.run_in_executor(None, max_bot.role_again, user.max_user_id)
    return {"ended": ended}


class RoleChoice(BaseModel):
    role: str


@router.get("/me/role")
def my_role(user: User = Depends(current_user)):
    return {"role": classes.role_of(user.max_user_id) if user.max_user_id is not None else "none"}


@router.put("/me/role")
def choose_role(body: RoleChoice, user: User = Depends(current_user)):
    if body.role not in ("teacher", "student"):
        raise HTTPException(422, "Роль — учитель или ученик")
    if user.max_user_id is None:
        raise HTTPException(403, "Роль выбирают в MAX")
    classes.set_role(user.max_user_id, body.role)
    return {"role": body.role}


@router.get("/me/classes")
def my_classes(user: User = Depends(current_user)):
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
    return {"lesson_id": classes.demo_lesson(user.max_user_id, user.name or None).id}


def _taught_class(class_id: int, user: User, deleted: bool = False):
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
    name, subject = body.name.strip(), body.subject.strip()
    if not name or not subject or len(name) > 40 or len(subject) > 60:
        raise HTTPException(422, "Укажите класс и предмет. Например: 10А и история")
    return name, subject.lower()


@router.post("/classes")
def create_class(body: NewClass, user: User = Depends(current_user)):
    if user.max_user_id is None:
        raise HTTPException(403, "Создать класс можно из MAX")
    name, subject = _class_fields(body)
    c = classes.create_class(user.max_user_id, user.name or "Учитель", name, subject)
    return {"id": c.id}


@router.patch("/classes/{class_id}")
def update_class(class_id: int, body: NewClass, user: User = Depends(current_user)):
    _taught_class(class_id, user)
    classes.update_class(class_id, *_class_fields(body))
    return {"updated": class_id}


@router.get("/classes/{class_id}")
def class_card(class_id: int, user: User = Depends(current_user)):
    c = _taught_class(class_id, user)
    return {
        "id": c.id, "name": c.name, "subject": c.subject,
        "code": c.invite_code, "link": max_bot.invite_link(c.invite_code),
        "students": [{"id": m.id, "name": classes.short_name(m.name)} for m in classes.students_of(c.id)],
        "lesson": live.id if (live := classes.live_lesson(c.id)) else None,
    }


@router.delete("/classes/{class_id}")
def delete_class(class_id: int, user: User = Depends(current_user)):
    _taught_class(class_id, user)
    if classes.live_lesson(class_id):
        raise HTTPException(409, "Идёт урок: удалить класс можно после него")
    students = classes.students_of(class_id)
    classes.delete_class(class_id)
    max_bot.tell(students, max_bot.class_deleted_text(classes.get_class(class_id)))
    return {"deleted": class_id}


@router.post("/classes/{class_id}/restore")
def restore_class(class_id: int, user: User = Depends(current_user)):
    _taught_class(class_id, user, deleted=True)
    if not classes.restore_class(class_id):
        raise HTTPException(410, "Класс удалён больше 30 дней назад")
    max_bot.tell(classes.students_of(class_id), max_bot.class_restored_text(classes.get_class(class_id)))
    return {"restored": class_id}


@router.delete("/classes/{class_id}/students/{member_id}")
def remove_student(class_id: int, member_id: int, user: User = Depends(current_user)):
    _taught_class(class_id, user)
    member = _class_student(class_id, member_id)
    classes.remove_member(member.id)
    max_bot.tell([member], max_bot.student_removed_text(classes.get_class(class_id)))
    return {"removed": member_id}


@router.post("/classes/{class_id}/students/{member_id}/restore")
def restore_student(class_id: int, member_id: int, user: User = Depends(current_user)):
    _taught_class(class_id, user)
    member = _class_student(class_id, member_id)
    classes.restore_member(member.id)
    max_bot.tell([member], max_bot.student_returned_text(classes.get_class(class_id)))
    return {"restored": member_id}


@router.post("/classes/{class_id}/lessons")
def lesson_for_class(class_id: int, user: User = Depends(current_user)):
    c = classes.get_class(class_id)
    if c is None or c.deleted_at is not None:
        raise HTTPException(404, "Такого класса нет")
    m = classes.membership(class_id, user.max_user_id) if user.max_user_id is not None else None
    if user.max_user_id is not None and (m is None or m.role != "teacher"):
        raise HTTPException(403, "Это может только учитель класса")
    return {"lesson_id": classes.lesson_for_class(class_id).id}


@router.post("/lessons/{lesson_id}/start", dependencies=[Depends(teacher)])
async def start(lesson_id: str, virtual: bool = False):
    lesson = classes.get_lesson(lesson_id)
    if lesson is not None and lesson.demo:
        return {"lesson_id": lesson_id, "started": _start_virtual(lesson_id)}
    if lesson is not None and lesson.ended_at is not None:
        # повторный прогон затёр бы итоги урока
        raise HTTPException(409, "Урок уже завершён: начните новый урок из чата с ботом")
    started = runner.start(lesson_id, _lesson_roster(lesson_id, virtual))
    if started:
        runner.when_finished(lesson_id, classes.mark_ended)
    if classes.mark_live(lesson_id):
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
    return {"lesson_id": lesson_id, "playing": _start_virtual(lesson_id)}


@router.delete("/lessons/{lesson_id}/virtual-class", dependencies=[Depends(teacher)])
async def stop_virtual_class(lesson_id: str):
    stopped = await runner.stop_virtual(lesson_id)
    return {"lesson_id": lesson_id, "stopped": stopped}


@router.post("/lessons/{lesson_id}/end", dependencies=[Depends(teacher)])
async def end(lesson_id: str):
    ended, _ = await asyncio.gather(runner.end(lesson_id), call.close_room(lesson_id))
    classes.mark_ended(lesson_id)
    return {"lesson_id": lesson_id, "ended": ended}


@router.get("/lessons/{lesson_id}/summary", response_model=Summary, dependencies=[Depends(teacher)])
def summary(lesson_id: str):
    return results.get_summary(lesson_id) or mocks.load("summary.json")


@router.get("/lessons/{lesson_id}/cards/{student_id}", response_model=StudentCard)
def card(lesson_id: str, student_id: str, who: Seat = Depends(member)):
    if who.role != "teacher" and who.participant_id != student_id:
        raise HTTPException(403, "Это карточка другого ученика")
    card = results.get_card(lesson_id, student_id)
    if card is not None:
        return card
    if results.get_summary(lesson_id) is not None:
        raise HTTPException(404, "Карточки по этому уроку нет")
    return next(c for c in mocks.load("cards.json") if c["student_id"] == student_id)


@router.post("/lessons/{lesson_id}/analytics", response_model=AnalyticsState, dependencies=[Depends(teacher)])
def set_analytics(lesson_id: str, state: AnalyticsState):
    runner.set_analytics(lesson_id, state.enabled, state.student_id)
    from app.ws import lesson as ws

    ws.publish(lesson_id, state)
    return state


@router.get("/lessons/{lesson_id}/analytics", dependencies=[Depends(teacher)])
def analytics_state(lesson_id: str):
    off = runner.analytics_off_list(lesson_id)
    return {"all_off": runner.ALL in off, "off": [sid for sid in off if sid != runner.ALL]}
