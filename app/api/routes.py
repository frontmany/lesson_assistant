"""REST API урока."""

from fastapi import APIRouter, HTTPException

from app import mocks
from app.call import participant_token
from app.config import settings
from app.models import AnalyticsState, JoinInfo, Roster, StudentCard, Summary
from app.pipeline import runner

router = APIRouter()


def _roster(lesson_id: str) -> Roster:
    # TODO(Ярослав): брать состав из data/demo_lesson/roster.json или из БД
    return Roster.model_validate(mocks.load("roster.json"))


@router.get("/lessons/{lesson_id}/roster", response_model=Roster)
def roster(lesson_id: str):
    return _roster(lesson_id)


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


@router.post("/lessons/{lesson_id}/start")
async def start(lesson_id: str):
    """Учитель начал урок: ассистент заходит в звонок и начинает слушать."""
    started = runner.start(lesson_id, _roster(lesson_id))
    return {"lesson_id": lesson_id, "started": started}


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
