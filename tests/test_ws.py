"""WebSocket урока: снимок при подключении, мок и закрытие соединения."""

import asyncio
import time

import anyio
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import ScaleUpdate, Utterance
from app.ws import lesson as ws_lesson

client = TestClient(app)
LESSON = "ws-test"


def scale(student_id: str, level: int) -> ScaleUpdate:
    return ScaleUpdate(student_id=student_id, level=level, label="ступень")


def said(student_id: str, text: str) -> Utterance:
    return Utterance(student_id=student_id, t=1.0, text=text, kind="answer", addressed=False)


@pytest.fixture
def live_lesson():
    ws_lesson.set_live(LESSON, True)
    yield
    ws_lesson.set_live(LESSON, False)


def test_snapshot_on_connect_has_latest_scale_and_utterance(live_lesson):
    ws_lesson.publish(LESSON, scale("ivan_i", 1))
    ws_lesson.publish(LESSON, said("ivan_i", "Первая реплика"))
    ws_lesson.publish(LESSON, scale("ivan_i", 2))
    ws_lesson.publish(LESSON, said("ivan_i", "Вторая реплика"))
    ws_lesson.publish(LESSON, scale("artem_k", 1))

    with client.websocket_connect(f"/ws/lessons/{LESSON}") as ws:
        events = [ws.receive_json() for _ in range(3)]

    got = {(e["type"], e["student_id"]): e for e in events}
    assert len(got) == 3  # по одному событию каждого вида на ученика
    assert got[("scale", "ivan_i")]["level"] == 2
    assert got[("utterance", "ivan_i")]["text"] == "Вторая реплика"
    assert got[("scale", "artem_k")]["level"] == 1


def test_new_events_follow_snapshot(live_lesson):
    ws_lesson.publish(LESSON, scale("ivan_i", 1))
    with client.websocket_connect(f"/ws/lessons/{LESSON}") as ws:
        assert ws.receive_json()["level"] == 1
        ws.portal.call(ws_lesson.publish, LESSON, scale("ivan_i", 2))
        assert ws.receive_json()["level"] == 2


def test_ended_lesson_has_no_snapshot():
    ws_lesson.set_live(LESSON, True)
    ws_lesson.publish(LESSON, said("ivan_i", "Реплика с прошлого урока"))
    ws_lesson.set_live(LESSON, False)
    assert LESSON not in ws_lesson._snapshots
    with client.websocket_connect(f"/ws/lessons/{LESSON}") as ws:
        first = ws.receive_json()  # только мок
    assert first.get("text") != "Реплика с прошлого урока"


def test_mock_stops_when_lesson_starts():
    # мок шлёт первое событие через секунду; урок начался раньше, и оно уже не придёт
    with client.websocket_connect(f"/ws/lessons/{LESSON}") as ws:
        ws.portal.call(ws_lesson.set_live, LESSON, True)
        try:
            time.sleep(1.3)
            ws.portal.call(ws_lesson.publish, LESSON, said("ivan_i", "Настоящая реплика"))
            assert ws.receive_json()["text"] == "Настоящая реплика"
        finally:
            ws.portal.call(ws_lesson.set_live, LESSON, False)


def test_stop_survives_cancel_while_waiting():
    # причина редких падений test_ws_streams_mock_events: сервер отменяет обработчик,
    # пока тот дожидается своих задач в finally
    async def slow_to_cancel():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            # задача уже отменяется, и вторая отмена её не торопит; наружу уходит
            # первая отмена, без метки anyio: из-за неё и ломался gather
            try:
                await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                pass
            raise

    async def scenario():
        with anyio.CancelScope() as scope:
            task = asyncio.create_task(slow_to_cancel())
            await asyncio.sleep(0)
            asyncio.get_running_loop().call_later(0.01, scope.cancel)
            await ws_lesson._stop([task])
        await asyncio.gather(task, return_exceptions=True)
        return scope.cancelled_caught

    assert asyncio.run(scenario())
