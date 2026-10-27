"""Конвейер от кадров до реплик, без звонка и без модели распознавания."""

import asyncio

import numpy as np
import pytest

pytest.importorskip("faster_whisper")

from app import mocks  # noqa: E402
from app.models import Roster  # noqa: E402
from app.pipeline.runner import transcribe_stream  # noqa: E402
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
