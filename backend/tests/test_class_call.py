"""Звонок по составу класса из базы (#45): виртуальный класс и ученик, вошедший во время урока."""

import asyncio

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import mocks
from app.db import classes as db
from app.main import app
from app.models import Roster, Student
from app.pipeline import runner
from app.sources import virtual_class

DEMO = Roster.model_validate(mocks.load("roster.json"))
client = TestClient(app)


@pytest.fixture
def recording(monkeypatch):
    """Запись урока есть, а боты и конвейер не запускаются: проверяем, что сервер решил."""
    started = {}
    monkeypatch.setattr(virtual_class, "load_tracks", lambda lesson_dir: (DEMO, []))
    monkeypatch.setattr(runner, "is_running", lambda lesson_id: lesson_id in started)
    monkeypatch.setattr(runner, "start", lambda lesson_id, roster: started.setdefault(lesson_id, roster) is roster)
    monkeypatch.setattr(runner, "when_finished", lambda lesson_id, callback: None)
    monkeypatch.setattr(runner, "play_virtual", lambda lesson_id, demo, tracks: True)
    monkeypatch.setattr(runner, "roster_of", lambda lesson_id: started.get(lesson_id))
    return started


def class_lesson(*students):
    c = db.create_class(1, "Елена Викторовна", "10А", "история")
    for i, name in enumerate(students):
        db.join(c.id, 10 + i, name)
    return db.lesson_for_class(c.id).id


def test_empty_class_lesson_starts_without_recording(recording):
    # пустой класс урок с записью не начинает: показать урок в одиночку — «Посмотреть демо»
    lesson_id = class_lesson()
    client.post(f"/api/lessons/{lesson_id}/start")
    assert recording[lesson_id].students == []
    assert recording[lesson_id].virtual_teacher is None


def test_class_with_students_starts_without_recording(recording):
    lesson_id = class_lesson("Иван Иванов")
    client.post(f"/api/lessons/{lesson_id}/start")
    assert [s.name for s in recording[lesson_id].students] == ["Иван И."]
    assert recording[lesson_id].virtual_teacher is None


def test_virtual_class_joins_live_students_of_class(recording):
    lesson_id = class_lesson("Иван Иванов")
    client.post(f"/api/lessons/{lesson_id}/virtual-class")
    students = recording[lesson_id].students
    assert [s.name for s in students if not s.virtual] == ["Иван И."]
    assert len([s for s in students if s.virtual]) == len(DEMO.students)


def test_demo_lesson_has_only_recorded_students(recording):
    lesson = db.demo_lesson(1, "Елена Викторовна")
    client.post(f"/api/lessons/{lesson.id}/start")
    assert all(s.virtual for s in recording[lesson.id].students)
    assert len(recording[lesson.id].students) == len(DEMO.students)


def test_two_classes_get_separate_rooms():
    import jwt

    from app.config import settings

    first, second = class_lesson("Иван Иванов"), class_lesson("Мария Смирнова")
    assert first != second
    rooms = set()
    for lesson_id in (first, second):
        token = client.get(f"/api/lessons/{lesson_id}/join").json()["token"]
        rooms.add(jwt.decode(token, settings.livekit_api_secret, algorithms=["HS256"], options={"verify_aud": False})["video"]["room"])
    assert rooms == {f"lesson-{first}", f"lesson-{second}"}


def test_member_of_other_class_gets_no_token():
    first = class_lesson("Иван Иванов")
    class_lesson("Мария Смирнова")
    roster = db.lesson_roster(first)
    assert client.get(f"/api/lessons/{first}/join", params={"id": roster.students[0].id}).status_code == 200
    assert client.get(f"/api/lessons/{first}/join", params={"id": "m999"}).status_code == 403


def test_student_who_joined_class_during_lesson_is_counted():
    pytest.importorskip("faster_whisper")
    from tests.test_runner import ROSTER, frames
    from tests.test_vad import silence, vowel

    roster = ROSTER.model_copy(deep=True)
    newcomer = Student(id="m42", name="Пётр П.")
    lookups = []

    def find_student(participant_id):
        lookups.append(participant_id)
        return newcomer if participant_id == "m42" else None

    # первые две секунды кадры приходят, пока его ищут в составе; говорит он позже
    talk = np.concatenate([silence(2), vowel(1.5), silence(1)])
    events = []

    async def slow_frames():
        async for item in frames({"m42": talk, "stranger": talk}):
            yield item
            await asyncio.sleep(0.001)

    asyncio.run(runner.transcribe_stream(
        slow_frames(), roster, events.append, lambda pcm, *a: "Можно я отвечу?", norm=4.5, find_student=find_student,
    ))
    said = [e for e in events if e.type == "utterance"]
    assert [e.student_id for e in said] == ["m42"]
    assert "m42" in [s.id for s in roster.students]  # состав урока для панели тоже обновился
    assert any(e.type == "scale" and e.student_id == "m42" and e.level >= 2 for e in events)
    # постороннего ищем не на каждом кадре, а раз в STRANGER_RECHECK_S
    assert lookups.count("stranger") == 1


def test_finished_lesson_does_not_start_again(recording):
    # новый прогон завершённого урока затёр бы его итоги
    lesson_id = class_lesson("Иван Иванов")
    db.mark_ended(lesson_id)
    assert client.post(f"/api/lessons/{lesson_id}/start").status_code == 409
    assert lesson_id not in recording
