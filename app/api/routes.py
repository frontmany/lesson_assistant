"""REST API урока."""

import asyncio
import logging

from fastapi import APIRouter, HTTPException

from app import mocks
from app.bot import max_bot
from app.call import participant_token
from app.config import settings
from app.db import classes
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
    """Состав урока: класс или, если урок с виртуальным классом, ученики из записи."""
    roster = _roster(lesson_id)
    if not virtual:
        return roster
    demo, _ = _demo()
    return virtual_class.lesson_roster(roster, demo)


def _demo() -> tuple[Roster, list]:
    try:
        return virtual_class.load_tracks(settings.demo_lesson_dir)
    except FileNotFoundError as exc:
        raise HTTPException(503, f"Записи урока нет: {exc}") from exc


@router.get("/lessons/{lesson_id}/roster", response_model=Roster)
def roster(lesson_id: str):
    """Состав идущего урока, с виртуальными учениками, если они есть; иначе состав класса."""
    return runner.roster_of(lesson_id) or _roster(lesson_id)


@router.get("/lessons/{lesson_id}/join", response_model=JoinInfo)
def join(lesson_id: str, participant_id: str):
    """Адрес LiveKit и токен для входа в звонок урока.

    TODO(Фёдор): брать участника не из параметра, а из проверенных
    данных запуска MAX (window.WebApp.initData), иначе любой войдёт за другого.
    """
    roster = _roster(lesson_id)
    people = {roster.teacher.id: ("teacher", roster.teacher.name)}
    people |= {s.id: ("student", s.name) for s in roster.students}
    if participant_id not in people:
        raise HTTPException(404, "Участника нет в составе класса")

    role, name = people[participant_id]
    return JoinInfo(
        url=settings.livekit_public_url,
        token=participant_token(lesson_id, participant_id, name),
        participant_id=participant_id,
        name=name,
        role=role,
    )


# TODO(Фёдор, #24): «Начать урок», старт, конец урока и виртуальный класс — только учителю класса
@router.post("/classes/{class_id}/lessons")
def lesson_for_class(class_id: int):
    """«Начать урок» в боте: урок класса, идущий или новый. Мини-приложение открывает его звонок."""
    c = classes.get_class(class_id)
    if c is None or c.deleted_at is not None:
        raise HTTPException(404, "Такого класса нет")
    return {"lesson_id": classes.lesson_for_class(class_id).id}


@router.post("/lessons/{lesson_id}/start")
async def start(lesson_id: str, virtual: bool = False):
    """Учитель вошёл в звонок: ассистент заходит и слушает, ученики класса получают «Урок начался».

    Демо-урок начинается сразу с записанным классом. virtual=true — в составе
    урока ученики из записи: так ассистент засчитает ботов, которых заводит
    `python -m app.cli virtual-class`.
    """
    lesson = classes.get_lesson(lesson_id)
    if lesson is not None and lesson.demo:
        result = await start_virtual_class(lesson_id)
        return {"lesson_id": lesson_id, "started": result["playing"]}
    started = runner.start(lesson_id, _lesson_roster(lesson_id, virtual))
    if started:
        runner.when_finished(lesson_id, classes.mark_ended)
    if classes.mark_live(lesson_id):
        # рассылка ходит в API MAX, цикл событий она не держит
        asyncio.get_running_loop().run_in_executor(None, max_bot.lesson_started, lesson_id)
    return {"lesson_id": lesson_id, "started": started}


@router.post("/lessons/{lesson_id}/virtual-class")
async def start_virtual_class(lesson_id: str):
    """Записанный урок заходит в звонок ботами вместо учеников (#18, демо для #45).

    Урок начинается, если ещё не идёт, и сразу с учениками из записи. Если урок
    уже идёт без них, ассистент не засчитал бы ботов, поэтому ответ 409.
    """
    demo, tracks = _demo()
    if runner.is_running(lesson_id) and runner.roster_of(lesson_id).virtual_teacher is None:
        raise HTTPException(409, "Урок уже идёт без виртуального класса: завершите его и начните заново")
    if runner.start(lesson_id, virtual_class.lesson_roster(_roster(lesson_id), demo)):
        runner.when_finished(lesson_id, classes.mark_ended)
    classes.mark_live(lesson_id)
    playing = runner.play_virtual(lesson_id, demo, tracks)
    return {"lesson_id": lesson_id, "playing": playing}


@router.delete("/lessons/{lesson_id}/virtual-class")
async def stop_virtual_class(lesson_id: str):
    """Боты виртуального класса выходят из звонка, урок идёт дальше."""
    stopped = await runner.stop_virtual(lesson_id)
    return {"lesson_id": lesson_id, "stopped": stopped}


@router.post("/lessons/{lesson_id}/end")
async def end(lesson_id: str):
    """Учитель нажал «Завершить урок»: ответ приходит, когда очередь дораспознана и ассистент вышел."""
    ended = await runner.end(lesson_id)
    classes.mark_ended(lesson_id)
    return {"lesson_id": lesson_id, "ended": ended}


@router.get("/lessons/{lesson_id}/summary", response_model=Summary)
def summary(lesson_id: str):
    # TODO(Ярослав, Фёдор): заменить мок на pipeline.summary.build_summary
    return mocks.load("summary.json")


@router.get("/lessons/{lesson_id}/cards/{student_id}", response_model=StudentCard)
def card(lesson_id: str, student_id: str):
    # TODO(Ярослав, Фёдор): заменить мок на pipeline.summary.build_card
    return next(c for c in mocks.load("cards.json") if c["student_id"] == student_id)


@router.post("/lessons/{lesson_id}/analytics", response_model=AnalyticsState)
def set_analytics(lesson_id: str, state: AnalyticsState):
    # TODO(Ярослав): запомнить состояние и разослать его по WebSocket
    return state
