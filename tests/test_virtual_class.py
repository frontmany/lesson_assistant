"""Виртуальный класс без звонка: состав урока, дорожки, конвейер и API."""

import asyncio
import wave

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import mocks
from app.main import app
from app.models import Roster
from app.sources.virtual_class import PREFIX, lesson_roster, load_tracks, read_track, virtual_id

ROSTER = Roster.model_validate(mocks.load("roster.json"))


def write_wav(path, seconds=1.0, rate=16000, channels=1):
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(np.zeros(int(seconds * rate) * channels, dtype=np.int16).tobytes())


def test_lesson_roster_has_only_recorded_students():
    roster = lesson_roster(ROSTER, ROSTER)
    # учеников класса нет: в панели шесть человек, а не двенадцать
    assert all(s.virtual for s in roster.students)
    assert [s.id for s in roster.students] == [PREFIX + s.id for s in ROSTER.students]
    assert [s.name for s in roster.students] == [s.name for s in ROSTER.students]
    # живой учитель остаётся учителем, учитель из записи — отдельно
    assert roster.teacher.id == "teacher"
    assert roster.virtual_teacher.id == virtual_id("teacher") and roster.virtual_teacher.virtual


def test_lfs_pointers_are_not_tracks(tmp_path):
    (tmp_path / "roster.json").write_text(ROSTER.model_dump_json(), encoding="utf-8")
    (tmp_path / "tracks").mkdir()
    (tmp_path / "tracks" / "teacher.wav").write_text("version https://git-lfs.github.com/spec/v1\n")
    with pytest.raises(FileNotFoundError, match="git lfs pull"):
        load_tracks(tmp_path)
    write_wav(tmp_path / "tracks" / "teacher.wav")
    roster, tracks = load_tracks(tmp_path)
    assert [t.name for t in tracks] == ["teacher.wav"]


def test_track_must_be_16k_mono(tmp_path):
    write_wav(tmp_path / "ok.wav")
    assert len(read_track(tmp_path / "ok.wav")) == 16000
    write_wav(tmp_path / "stereo.wav", channels=2)
    with pytest.raises(ValueError, match="16 кГц"):
        read_track(tmp_path / "stereo.wav")


def test_virtual_teacher_is_teacher_for_pipeline():
    pytest.importorskip("faster_whisper")
    from app.pipeline.runner import transcribe_stream
    from tests.test_runner import frames
    from tests.test_vad import silence, vowel

    roster = lesson_roster(ROSTER, ROSTER)
    teacher = np.concatenate([silence(0.3), vowel(1.5), silence(5)])
    student = np.concatenate([silence(2.5), vowel(2), silence(2.3)])

    def by_length(pcm):
        return "Артём, что Пётр сделал с флотом?" if len(pcm) < 2.2 * 16000 else "Ну, там флот строили."

    events = []

    async def scenario():
        tracks = {virtual_id("teacher"): teacher, virtual_id("artem_k"): student, "stranger": student}
        return await transcribe_stream(frames(tracks), roster, events.append, by_length, norm=4.5)

    context = asyncio.run(scenario())
    said = [e for e in events if e.type == "utterance"]
    # виртуальный Артём — ученик урока, учитель из записи — учитель: его обращение засчитано
    assert [(e.student_id, e.kind, e.addressed) for e in said] == [(virtual_id("artem_k"), "response", True)]
    assert [item["student_id"] for item in context] == ["teacher", virtual_id("artem_k")]


def test_class_students_are_not_in_virtual_lesson_roster():
    roster = lesson_roster(ROSTER, ROSTER)
    assert not {s.id for s in ROSTER.students} & {s.id for s in roster.students}
    assert len(roster.students) == len(ROSTER.students)


def test_api_without_recording_explains_lfs():
    client = TestClient(app)
    # в репозитории дорожки — указатели Git LFS, пока не выполнен git lfs pull
    response = client.post("/api/lessons/demo-x/virtual-class")
    if response.status_code == 503:
        assert "git lfs pull" in response.json()["detail"]
    assert client.delete("/api/lessons/demo-x/virtual-class").json() == {"lesson_id": "demo-x", "stopped": False}


def test_join_does_not_issue_tokens_for_virtual_students():
    client = TestClient(app)
    assert client.get("/api/lessons/demo-x/join", params={"participant_id": virtual_id("artem_k")}).status_code == 404
