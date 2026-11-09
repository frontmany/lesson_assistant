"""Конвейер от кадров до реплик без звонка и модели"""

import asyncio
import dataclasses
import threading
import time
import wave

import numpy as np
import pytest

pytest.importorskip("faster_whisper")

from app import mocks  # noqa: E402
from app.models import Roster  # noqa: E402
from app.pipeline.runner import _Job, next_job, transcribe_stream  # noqa: E402
from app.pipeline.vad import PAD_MS  # noqa: E402
from tests.test_vad import FRAME, as_int16, silence, vowel  # noqa: E402

ROSTER = Roster.model_validate(mocks.load("roster.json"))


async def frames(tracks: dict[str, np.ndarray]):
    pcm = {who: as_int16(audio) for who, audio in tracks.items()}
    length = max(len(a) for a in pcm.values())
    for i in range(0, length, FRAME):
        for who, audio in pcm.items():
            if i < len(audio):
                yield who, audio[i : i + FRAME].tobytes()


def run_all(tracks: dict[str, np.ndarray], transcribe=None) -> list:
    events = []
    heard = []

    def fake_transcribe(pcm: np.ndarray, role: str) -> str:
        heard.append(len(pcm))
        return f"фраза {len(heard)}"

    asyncio.run(transcribe_stream(frames(tracks), ROSTER, events.append, transcribe or fake_transcribe, norm=4.5))
    return events


def run(tracks: dict[str, np.ndarray], transcribe=None) -> list:
    return [e for e in run_all(tracks, transcribe) if e.type == "utterance"]


def test_student_phrase_becomes_utterance_with_id():
    talk = np.concatenate([silence(0.5), vowel(1.5), silence(1)])
    events = run({"artem_k": talk, "ivan_i": silence(len(talk) / 16000)})
    assert len(events) == 1
    event = events[0]
    assert event.type == "utterance"
    assert event.student_id == "artem_k"
    assert event.text == "фраза 1"
    assert event.kind == "other"


def test_teacher_and_strangers_do_not_reach_panel():
    talk = np.concatenate([silence(0.5), vowel(1.5), silence(1)])
    assert run({ROSTER.teacher.id: talk, "stranger": talk}) == []


def test_phrases_are_found_outside_event_loop(monkeypatch):
    from app.pipeline import vad

    threads = set()
    feed = vad.PhraseDetector.feed

    def spy(self, pcm):
        threads.add(threading.current_thread().name)
        return feed(self, pcm)

    monkeypatch.setattr(vad.PhraseDetector, "feed", spy)
    talk = np.concatenate([silence(0.5), vowel(1.5), silence(1)])
    assert len(run({"artem_k": talk})) == 1
    assert threads and all(name.startswith("vad") for name in threads)


def test_students_are_recognized_before_teacher():
    order = []

    def slow_transcribe(pcm: np.ndarray, role: str) -> str:
        seconds = len(pcm) / 16000
        order.append(seconds)
        time.sleep(0.2)
        return "текст"

    teacher = np.concatenate([silence(0.3), vowel(1), silence(0.7), vowel(1.5), silence(2.5)])
    student = np.concatenate([silence(3), vowel(2), silence(1)])

    async def scenario():
        await transcribe_stream(
            frames({ROSTER.teacher.id: teacher, "artem_k": student}), ROSTER, lambda e: None, slow_transcribe, norm=4.5
        )

    asyncio.run(scenario())
    assert len(order) == 3
    teacher_second = min(range(3), key=lambda i: abs(order[i] - (1.5 + 2 * PAD_MS / 1000)))
    student_phrase = min(range(3), key=lambda i: abs(order[i] - (2 + 2 * PAD_MS / 1000)))
    assert student_phrase < teacher_second


def test_unfinished_phrase_is_recognized_when_frames_end():
    events = run({"artem_k": np.concatenate([silence(0.5), vowel(1.5), silence(1), vowel(1)])})
    assert [e.student_id for e in events] == ["artem_k", "artem_k"]


def test_end_finishes_queue_and_closes_lesson(monkeypatch):
    from app.pipeline import asr, runner
    from app.sources import call
    from app.ws import lesson as ws

    talk = as_int16(np.concatenate([silence(0.5), vowel(1.5), silence(1), vowel(1)]))
    finished = []

    async def fake_listen(lesson_id, stop, **kwargs):
        for i in range(0, len(talk), FRAME):
            yield "artem_k", talk[i : i + FRAME].tobytes()
        await stop.wait()

    def slow_transcribe(pcm, *args):
        time.sleep(0.3)
        return "текст"

    async def fake_finish(lesson_id, roster, utterances, board):
        finished.append((lesson_id, utterances))

    monkeypatch.setattr(call, "listen", fake_listen)
    monkeypatch.setattr(asr, "model", lambda role: None)
    monkeypatch.setattr(asr, "transcribe", slow_transcribe)
    monkeypatch.setattr(runner, "finish_lesson", fake_finish)

    async def scenario():
        assert runner.start("end-test", ROSTER)
        assert not runner.start("end-test", ROSTER)
        await asyncio.sleep(0.5)
        assert "end-test" in ws._live
        assert await runner.end("end-test")
        assert not runner.is_running("end-test")
        assert "end-test" not in ws._live
        assert not await runner.end("end-test")

    asyncio.run(scenario())
    assert len(finished) == 1
    lesson_id, utterances = finished[0]
    assert lesson_id == "end-test"
    assert [u["student_id"] for u in utterances] == ["artem_k", "artem_k"]


def job(who: str, t: float, end: float) -> _Job:
    return _Job(who, np.zeros(1, dtype=np.float32), t, end, 0.0)


def test_next_job_students_first_but_after_their_context():
    students = {"artem_k", "ivan_i"}
    question = job("teacher", 0, 4)
    overlap = job("teacher", 5, 9)
    answer = job("artem_k", 5, 8)
    later = job("ivan_i", 6, 10)
    assert next_job([overlap, later, answer, question], students) is question
    assert next_job([overlap, later, answer], students) is answer
    assert next_job([overlap, later], students) is later
    assert next_job([overlap], students) is overlap


def test_teacher_question_is_recognized_before_answer_and_counts():
    teacher = np.concatenate([silence(0.3), vowel(1.5), silence(5)])
    student = np.concatenate([silence(2.5), vowel(2), silence(2.3)])

    def by_length(pcm: np.ndarray, role: str) -> str:
        return "Артём, что Пётр сделал с флотом?" if len(pcm) < 2.2 * 16000 else "Ну, там флот строили."

    events = run_all({ROSTER.teacher.id: teacher, "artem_k": student}, by_length)
    said = [e for e in events if e.type == "utterance"]
    assert len(said) == 1
    answer = said[0]
    assert answer.kind == "response"
    assert answer.addressed is True
    assert answer.t == pytest.approx(2.5, abs=0.2)

    scale = [e for e in events if e.type == "scale"]
    assert {e.student_id for e in scale[: len(ROSTER.students)]} == {s.id for s in ROSTER.students}
    assert all(e.level == 1 for e in scale[: len(ROSTER.students)])
    assert scale[-1].student_id == "artem_k" and scale[-1].level >= 2


def test_utterances_keep_end_and_kind():
    talk = np.concatenate([silence(0.5), vowel(1.5), silence(1)])
    out = []

    async def scenario():
        out.extend(await transcribe_stream(frames({"artem_k": talk}), ROSTER, lambda e: None, lambda pcm, role: "Флот.", norm=4.5))

    asyncio.run(scenario())
    assert len(out) == 1
    item = out[0]
    assert item["t"] == pytest.approx(0.5, abs=0.2)
    assert item["end"] == pytest.approx(2.0, abs=0.2)
    assert item["kind"] == "other"


def test_long_silence_flag_appears_without_new_replies(monkeypatch):
    from app.pipeline import runner, scoring

    monkeypatch.setattr(runner, "settings", dataclasses.replace(runner.settings, lesson_minutes=0.01))
    monkeypatch.setattr(runner, "FLAGS_EVERY_S", 0.05)
    monkeypatch.setattr(scoring, "LONG_SILENCE_MIN_S", 0)
    events = []

    async def quiet_frames():
        for _ in range(30):
            yield "artem_k", as_int16(silence(0.03)).tobytes()
            await asyncio.sleep(0.02)

    asyncio.run(transcribe_stream(quiet_frames(), ROSTER, events.append, lambda pcm, role: "", norm=4.5))
    flagged = {e.student_id for e in events if e.type == "scale" and "long_silence" in e.flags}
    assert flagged == {s.id for s in ROSTER.students}


def test_answer_in_several_phrases_counts_once():
    teacher = np.concatenate([silence(0.3), vowel(1.5), silence(9)])
    student = np.concatenate([silence(2.5), vowel(1.2), silence(0.8), vowel(1.2), silence(0.8), vowel(1.2), silence(3)])

    def by_length(pcm: np.ndarray, role: str) -> str:
        return "Артём, что Пётр сделал с флотом?" if len(pcm) > 2 * 16000 else "Флот строили."

    events = run_all({ROSTER.teacher.id: teacher, "artem_k": student}, by_length)
    said = [e for e in events if e.type == "utterance"]
    assert [e.kind for e in said] == ["response"] * 3
    artem = [e.level for e in events if e.type == "scale" and e.student_id == "artem_k"]
    assert artem[-1] == 2


def test_failing_phrase_does_not_stop_lesson():
    talk = np.concatenate([silence(0.5), vowel(1.5), silence(1), vowel(1.5), silence(1), vowel(1.5), silence(1)])
    calls = []

    def flaky(pcm: np.ndarray, role: str) -> str:
        calls.append(len(pcm))
        if len(calls) == 2:
            raise RuntimeError("модель упала")
        return f"фраза {len(calls)}"

    events = run({"artem_k": talk}, flaky)
    assert [e.text for e in events] == ["фраза 1", "фраза 3"]


def test_failing_classifier_does_not_stop_lesson(monkeypatch):
    from app.pipeline import classify as rules

    real = rules.classify
    seen = []

    def flaky(text, student_id, t, roster, context):
        seen.append(text)
        if len(seen) == 1:
            raise ValueError("правило не разобрало реплику")
        return real(text, student_id, t, roster, context)

    monkeypatch.setattr(rules, "classify", flaky)
    talk = np.concatenate([silence(0.5), vowel(1.5), silence(1), vowel(1.5), silence(1)])
    assert [e.text for e in run({"artem_k": talk})] == ["фраза 2"]


def test_end_does_not_hang_after_failure(monkeypatch):
    from app.pipeline import asr, runner
    from app.sources import call

    talk = as_int16(np.concatenate([silence(0.5), vowel(1.5), silence(1), vowel(1.5), silence(1)]))
    finished = []
    calls = []

    async def fake_listen(lesson_id, stop, **kwargs):
        for i in range(0, len(talk), FRAME):
            yield "artem_k", talk[i : i + FRAME].tobytes()
        await stop.wait()

    def broken(pcm, *args):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("модель упала")
        return "вторая фраза"

    async def fake_finish(lesson_id, roster, utterances, board):
        finished.append(utterances)

    monkeypatch.setattr(call, "listen", fake_listen)
    monkeypatch.setattr(asr, "model", lambda role: None)
    monkeypatch.setattr(asr, "transcribe", broken)
    monkeypatch.setattr(runner, "finish_lesson", fake_finish)

    async def scenario():
        assert runner.start("fail-test", ROSTER)
        await asyncio.sleep(0.5)
        assert await asyncio.wait_for(runner.end("fail-test"), 5)

    asyncio.run(scenario())
    assert [[u["text"] for u in lesson] for lesson in finished] == [["вторая фраза"]]


def test_transcribe_gets_speaker_role():
    roles = {}

    def by_role(pcm: np.ndarray, role: str) -> str:
        roles[role] = roles.get(role, 0) + 1
        return "текст"

    talk = np.concatenate([silence(0.5), vowel(1.5), silence(1)])
    run_all({ROSTER.teacher.id: talk, "artem_k": talk, "stranger": talk}, by_role)
    assert roles == {"teacher": 1, "student": 1}


def test_default_transcribe_gives_names_only_to_teacher(monkeypatch):
    from app.pipeline import asr

    calls = []

    def fake(pcm, role, hotwords=None):
        calls.append((role, hotwords))
        return "текст"

    monkeypatch.setattr(asr, "model", lambda role: None)
    monkeypatch.setattr(asr, "transcribe", fake)
    talk = np.concatenate([silence(0.5), vowel(1.5), silence(1)])
    asyncio.run(
        transcribe_stream(frames({ROSTER.teacher.id: talk, "artem_k": talk}), ROSTER, lambda e: None, norm=4.5)
    )
    by_role = dict(calls)
    assert "Артём" in by_role["teacher"]
    assert by_role["student"] is None


def test_phrases_are_saved_only_by_flag(monkeypatch, tmp_path):
    from app.pipeline import runner

    talk = np.concatenate([silence(0.5), vowel(1.5), silence(1)])

    run({"artem_k": talk})
    assert list(tmp_path.iterdir()) == []

    monkeypatch.setattr(runner, "settings", dataclasses.replace(runner.settings, save_phrases_dir=str(tmp_path)))
    run({"artem_k": talk})
    wavs = list(tmp_path.rglob("*.wav"))
    assert len(wavs) == 1 and "artem_k" in wavs[0].name
    assert (wavs[0].parent / f"{wavs[0].stem}.txt").read_text(encoding="utf-8") == "фраза 1"
    with wave.open(str(wavs[0])) as saved:
        assert (saved.getframerate(), saved.getnchannels(), saved.getsampwidth()) == (16000, 1, 2)
        assert saved.getnframes() / 16000 == pytest.approx(1.5 + 2 * PAD_MS / 1000, abs=0.2)


def test_failing_save_does_not_lose_phrase(monkeypatch, tmp_path):
    from app.pipeline import runner

    blocker = tmp_path / "файл"
    blocker.write_text("")
    monkeypatch.setattr(runner, "settings", dataclasses.replace(runner.settings, save_phrases_dir=str(blocker)))
    talk = np.concatenate([silence(0.5), vowel(1.5), silence(1)])
    assert [e.text for e in run({"artem_k": talk})] == ["фраза 1"]


def test_student_with_analytics_off_is_not_counted():
    talk = np.concatenate([silence(0.5), vowel(1.5), silence(1)])
    events = []

    async def go():
        return await transcribe_stream(
            frames({"artem_k": talk, "ivan_i": talk}), ROSTER, events.append,
            lambda pcm, role: "Флот строили сами.", norm=4.5, off=lambda sid: sid == "artem_k",
        )

    utterances = asyncio.run(go())
    ids = {e.student_id for e in events}
    assert "artem_k" not in ids and "ivan_i" in ids
    artem = [u for u in utterances if u["student_id"] == "artem_k"]
    assert artem and all(u.get("off") for u in artem)
