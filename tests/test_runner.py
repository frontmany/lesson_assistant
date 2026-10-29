"""Конвейер от кадров до реплик, без звонка и без модели распознавания."""

import asyncio
import threading
import time

import numpy as np
import pytest

pytest.importorskip("faster_whisper")

from app import mocks  # noqa: E402
from app.models import Roster  # noqa: E402
from app.pipeline.runner import transcribe_stream  # noqa: E402
from app.pipeline.vad import PAD_MS  # noqa: E402
from tests.test_vad import FRAME, as_int16, silence, vowel  # noqa: E402

ROSTER = Roster.model_validate(mocks.load("roster.json"))


async def frames(tracks: dict[str, np.ndarray]):
    """Кадры всех участников вперемешку, как из звонка."""
    pcm = {who: as_int16(audio) for who, audio in tracks.items()}
    length = max(len(a) for a in pcm.values())
    for i in range(0, length, FRAME):
        for who, audio in pcm.items():
            if i < len(audio):
                yield who, audio[i : i + FRAME].tobytes()


def run(tracks: dict[str, np.ndarray]) -> list:
    events = []
    heard = []

    def fake_transcribe(pcm: np.ndarray) -> str:
        heard.append(len(pcm))
        return f"фраза {len(heard)}"

    asyncio.run(transcribe_stream(frames(tracks), ROSTER, events.append, fake_transcribe))
    return events


def test_student_phrase_becomes_utterance_with_id():
    talk = np.concatenate([silence(0.5), vowel(1.5), silence(1)])
    events = run({"artem_k": talk, "ivan_i": silence(len(talk) / 16000)})
    assert len(events) == 1
    event = events[0]
    assert event.type == "utterance"
    assert event.student_id == "artem_k"
    assert event.text == "фраза 1"
    assert event.kind == "other"  # классификатор ещё не написан


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
    # учитель закончил две фразы раньше ученика, но первым после уже начатой
    # фразы распознаётся ученик: учитель нужен только как контекст
    order = []

    def slow_transcribe(pcm: np.ndarray) -> str:
        seconds = len(pcm) / 16000
        order.append(seconds)
        time.sleep(0.2)
        return "текст"

    teacher = np.concatenate([silence(0.3), vowel(1), silence(0.7), vowel(1.5), silence(2.5)])
    student = np.concatenate([silence(3), vowel(2), silence(1)])

    async def scenario():
        await transcribe_stream(frames({ROSTER.teacher.id: teacher, "artem_k": student}), ROSTER, lambda e: None, slow_transcribe)

    asyncio.run(scenario())
    assert len(order) == 3
    # длины фраз с запасом по краям: учитель 1 с и 1,5 с, ученик 2 с
    teacher_second = min(range(3), key=lambda i: abs(order[i] - (1.5 + 2 * PAD_MS / 1000)))
    student_phrase = min(range(3), key=lambda i: abs(order[i] - (2 + 2 * PAD_MS / 1000)))
    assert student_phrase < teacher_second


def test_unfinished_phrase_is_recognized_when_frames_end():
    # урок завершили, пока ученик ещё говорил: паузы после фразы нет
    events = run({"artem_k": np.concatenate([silence(0.5), vowel(1.5), silence(1), vowel(1)])})
    assert [e.student_id for e in events] == ["artem_k", "artem_k"]


def test_end_finishes_queue_and_closes_lesson(monkeypatch):
    from app.pipeline import asr, runner
    from app.sources import call
    from app.ws import lesson as ws

    talk = as_int16(np.concatenate([silence(0.5), vowel(1.5), silence(0.5), vowel(1)]))
    finished = []

    async def fake_listen(lesson_id, stop):
        # звонок: ученик сказал две фразы и молчит, пока учитель не завершит урок
        for i in range(0, len(talk), FRAME):
            yield "artem_k", talk[i : i + FRAME].tobytes()
        await stop.wait()

    def slow_transcribe(pcm):
        time.sleep(0.3)
        return "текст"

    async def fake_finish(lesson_id, roster, utterances):
        finished.append((lesson_id, utterances))

    monkeypatch.setattr(call, "listen", fake_listen)
    monkeypatch.setattr(asr, "model", lambda: None)
    monkeypatch.setattr(asr, "transcribe", slow_transcribe)
    monkeypatch.setattr(runner, "finish_lesson", fake_finish)

    async def scenario():
        assert runner.start("end-test", ROSTER)
        assert not runner.start("end-test", ROSTER)  # второй раз не запускается
        await asyncio.sleep(0.5)
        assert "end-test" in ws._live
        assert await runner.end("end-test")
        assert not runner.is_running("end-test")
        assert "end-test" not in ws._live
        assert not await runner.end("end-test")  # повторный вызов ничего не ломает

    asyncio.run(scenario())
    assert len(finished) == 1
    lesson_id, utterances = finished[0]
    assert lesson_id == "end-test"
    # вторая фраза кончилась без паузы, но тоже дораспознана
    assert [u["student_id"] for u in utterances] == ["artem_k", "artem_k"]
