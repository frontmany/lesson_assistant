"""Отбраковка галлюцинаций Whisper, без модели."""

import pytest

pytest.importorskip("faster_whisper")

from faster_whisper.transcribe import Segment  # noqa: E402

from app.pipeline.asr import is_hallucination  # noqa: E402


def segment(text: str, compression_ratio: float, no_speech_prob: float, avg_logprob: float) -> Segment:
    return Segment(
        id=0, seek=0, start=0, end=1, text=text, tokens=[], avg_logprob=avg_logprob,
        compression_ratio=compression_ratio, no_speech_prob=no_speech_prob, words=None, temperature=0,
    )


def test_normal_phrase_is_kept():
    assert not is_hallucination(segment("Ну, там флот строили", 1.1, 0.05, -0.3))


def test_repeating_loop_is_dropped():
    # реплика, которую Whisper выдал на музыке во время проверки
    assert is_hallucination(segment("это не то, что " * 40, 9.5, 0.1, -0.2))


def test_text_where_whisper_hears_no_speech_is_dropped():
    assert is_hallucination(segment("Продолжение следует...", 1.0, 0.8, -1.4))


def test_quiet_but_confident_phrase_is_kept():
    # тихо сказано, но Whisper уверен в словах
    assert not is_hallucination(segment("Табель о рангах", 1.0, 0.7, -0.4))
