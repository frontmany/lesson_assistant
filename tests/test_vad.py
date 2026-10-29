"""Поиск речи на синтетическом сигнале.

Silero принимает за речь гласную «и»: импульсы голосовых связок около 110 Гц,
пропущенные через форманты 300, 2300 и 3000 Гц, с огибающей слогов 4 Гц.
Тишину, шум и ровный тон он речью не считает.
"""

import numpy as np
import pytest

pytest.importorskip("faster_whisper")

from app.pipeline.vad import MAX_PHRASE_S, PAD_MS, SAMPLE_RATE, PhraseDetector, speech_segments  # noqa: E402

FRAME = SAMPLE_RATE * 30 // 1000  # кадр из звонка, 30 мс
PAD = PAD_MS / 1000


def around(start: float, end: float):
    """Границы фразы: речь плюс запас тишины по краям."""
    return pytest.approx(start - PAD, abs=0.15), pytest.approx(end + PAD, abs=0.15)


def vowel(seconds: float) -> np.ndarray:
    t = np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE
    f0 = 110 * (1 + 0.05 * np.sin(2 * np.pi * 5 * t))
    pulses = np.zeros_like(t)
    pulses[np.nonzero(np.diff(np.floor(np.cumsum(f0) / SAMPLE_RATE)))[0]] = 1
    freqs = np.fft.rfftfreq(len(t), 1 / SAMPLE_RATE)
    formants = sum(1 / (1 + ((freqs - fc) / bw) ** 2) for fc, bw in ((300, 60), (2300, 100), (3000, 120)))
    out = np.fft.irfft(np.fft.rfft(pulses) * formants, len(t)) * 0.5 * (1 - np.cos(2 * np.pi * 4 * t))
    return (out / np.abs(out).max() * 0.5).astype(np.float32)


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * SAMPLE_RATE), dtype=np.float32)


def as_int16(audio: np.ndarray) -> np.ndarray:
    return (audio * 32767).astype(np.int16)


def phrases(audio: np.ndarray):
    """Подать запись детектору кадрами по 30 мс, как из звонка."""
    detector = PhraseDetector()
    pcm = as_int16(audio)
    found = []
    for i in range(0, len(pcm), FRAME):
        found += detector.feed(pcm[i : i + FRAME].tobytes())
    return found


@pytest.mark.parametrize(
    "audio",
    [
        silence(2),
        np.random.default_rng(0).normal(0, 0.05, 2 * SAMPLE_RATE).astype(np.float32),
        (0.3 * np.sin(2 * np.pi * 440 * np.arange(2 * SAMPLE_RATE) / SAMPLE_RATE)).astype(np.float32),
    ],
    ids=["тишина", "шум", "тон"],
)
def test_no_speech_in_silence_noise_and_tone(audio):
    assert speech_segments(audio) == []
    assert phrases(np.concatenate([audio, silence(1)])) == []


def test_offline_segments():
    audio = np.concatenate([silence(0.5), vowel(1.5), silence(1), vowel(1), silence(0.5)])
    segments = speech_segments(as_int16(audio))
    assert len(segments) == 2
    assert segments[0] == around(0.5, 2.0)
    assert segments[1] == around(3.0, 4.0)


def test_stream_splits_phrases_on_pause():
    audio = np.concatenate([silence(0.5), vowel(1.5), silence(1), vowel(1), silence(1)])
    found = phrases(audio)
    assert len(found) == 2
    first, second = found
    assert (first.start, first.end) == around(0.5, 2.0)
    assert (second.start, second.end) == around(3.0, 4.0)
    assert first.duration == pytest.approx(first.end - first.start, abs=0.01)
    # сама речь — без запаса тишины
    assert first.speech_start == pytest.approx(0.5, abs=0.15)
    assert first.speech_end == pytest.approx(2.0, abs=0.15)


def test_short_pause_does_not_split():
    found = phrases(np.concatenate([vowel(1), silence(0.15), vowel(1), silence(1)]))
    assert len(found) == 1


def test_phrase_waits_for_pause():
    # речь ещё идёт — фраза не закончена
    assert phrases(np.concatenate([silence(0.3), vowel(2)])) == []


def test_click_is_not_a_phrase():
    assert phrases(np.concatenate([silence(0.5), vowel(0.1), silence(1)])) == []


def test_long_monologue_is_cut():
    found = phrases(np.concatenate([vowel(MAX_PHRASE_S + 3), silence(1)]))
    assert len(found) == 2
    assert found[0].duration == pytest.approx(MAX_PHRASE_S, abs=0.1)


def test_flush_returns_unfinished_phrase():
    detector = PhraseDetector()
    pcm = as_int16(np.concatenate([silence(0.5), vowel(1.5)]))
    for i in range(0, len(pcm), FRAME):
        assert detector.feed(pcm[i : i + FRAME].tobytes()) == []
    phrase = detector.flush()
    assert phrase is not None
    assert phrase.duration == pytest.approx(1.5 + PAD, abs=0.15)
    assert detector.flush() is None
