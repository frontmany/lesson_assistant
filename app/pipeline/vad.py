"""Поиск речи в потоке. Владелец: Ярослав.

Silero VAD уже входит в faster-whisper как ONNX-модель
(faster_whisper.vad.get_speech_timestamps), PyTorch не нужен.
Кадры из звонка идут по 30 мс, Silero смотрит окнами по 32 мс (512 отсчётов).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np
from faster_whisper.vad import VadOptions, get_speech_timestamps, get_vad_model

SAMPLE_RATE = 16_000
WINDOW = 512  # окно Silero, 32 мс
CONTEXT = 64  # хвост предыдущего окна, который Silero видит вместе с текущим
WINDOW_MS = WINDOW * 1000 // SAMPLE_RATE

THRESHOLD = 0.5  # выше — речь
NEG_THRESHOLD = 0.35  # ниже — тишина, между порогами продолжается то, что было
MIN_SILENCE_MS = 300  # такая пауза заканчивает фразу; это часть бюджета в 1,5 с
MIN_SPEECH_MS = 250  # фразы короче — щелчки и шум, их не распознаём
MAX_PHRASE_S = 15  # длиннее режем, чтобы реплика не ждала конца монолога
PAD_MS = 300  # запас тишины по краям фразы: Silero замечает речь на пару окон позже её начала


def to_float(pcm: bytes | np.ndarray) -> np.ndarray:
    """PCM int16 (байты или массив) → float32 в [-1, 1], как ждут Silero и Whisper."""
    if isinstance(pcm, bytes):
        pcm = np.frombuffer(pcm, dtype=np.int16)
    if pcm.dtype == np.int16:
        return pcm.astype(np.float32) / 32768
    return pcm.astype(np.float32, copy=False)


def speech_segments(pcm: bytes | np.ndarray, sample_rate: int = SAMPLE_RATE) -> list[tuple[float, float]]:
    """Вернуть отрезки речи (начало, конец) в секундах для целой записи."""
    options = VadOptions(
        threshold=THRESHOLD,
        neg_threshold=NEG_THRESHOLD,
        min_speech_duration_ms=MIN_SPEECH_MS,
        max_speech_duration_s=MAX_PHRASE_S,
        min_silence_duration_ms=MIN_SILENCE_MS,
        speech_pad_ms=PAD_MS,
    )
    stamps = get_speech_timestamps(to_float(pcm), options, sampling_rate=sample_rate)
    return [(s["start"] / sample_rate, s["end"] / sample_rate) for s in stamps]


@dataclass
class Phrase:
    pcm: np.ndarray  # float32, 16 кГц
    start: float  # секунды от первого кадра участника
    end: float

    @property
    def duration(self) -> float:
        return len(self.pcm) / SAMPLE_RATE


class PhraseDetector:
    """Потоковый поиск фраз одного участника.

    get_speech_timestamps каждый раз начинает с чистого состояния модели,
    поэтому для потока окна прогоняются здесь по одному, а состояние Silero
    и хвост предыдущего окна переносятся между вызовами. Это опирается на
    устройство SileroVADModel в faster-whisper 1.1.1 (encoder и decoder).
    """

    def __init__(self) -> None:
        self._model = get_vad_model()
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros(CONTEXT, dtype=np.float32)
        self._rest = np.zeros(0, dtype=np.float32)  # отсчёты, которых не хватило на окно
        self._windows = 0  # сколько окон пройдено с начала

        pad = PAD_MS // WINDOW_MS
        self._before: deque[np.ndarray] = deque(maxlen=pad)  # тишина перед фразой
        self._phrase: list[np.ndarray] = []
        self._phrase_start = 0
        self._speech = 0  # окон речи во фразе
        self._silence = 0  # окон тишины подряд в конце фразы

    def prob(self, window: np.ndarray) -> float:
        """Вероятность речи в одном окне; состояние модели переходит к следующему."""
        x = np.concatenate([self._context, window])[None, :]
        encoded = self._model.encoder_session.run(None, {"input": x})[0].reshape(1, 128)
        out, self._state = self._model.decoder_session.run(None, {"input": encoded, "state": self._state})
        self._context = window[-CONTEXT:]
        return float(out.ravel()[0])

    def feed(self, pcm: bytes | np.ndarray) -> list[Phrase]:
        """Принять очередной кадр, вернуть законченные фразы (обычно ни одной)."""
        audio = np.concatenate([self._rest, to_float(pcm)])
        whole = len(audio) // WINDOW * WINDOW
        self._rest = audio[whole:]
        done = []
        for window in audio[:whole].reshape(-1, WINDOW):
            phrase = self._step(window, self.prob(window))
            if phrase is not None:
                done.append(phrase)
        return done

    def flush(self) -> Phrase | None:
        """Урок кончился посреди фразы: отдать то, что успели сказать."""
        if not self._phrase:
            return None
        return self._finish(trailing=self._silence)

    def _step(self, window: np.ndarray, p: float) -> Phrase | None:
        self._windows += 1
        if not self._phrase:
            if p >= THRESHOLD:
                self._phrase = [*self._before, window]
                self._phrase_start = self._windows - len(self._phrase)
                self._speech, self._silence = 1, 0
            else:
                self._before.append(window)
            return None

        self._phrase.append(window)
        if p >= THRESHOLD:
            self._speech += 1
            self._silence = 0
        elif p < NEG_THRESHOLD:
            self._silence += 1

        if self._silence * WINDOW_MS >= MIN_SILENCE_MS:
            return self._finish(trailing=self._silence)
        if len(self._phrase) * WINDOW_MS >= MAX_PHRASE_S * 1000:
            return self._finish(trailing=0)
        return None

    def _finish(self, trailing: int) -> Phrase | None:
        # в хвосте оставляем PAD_MS тишины, остальное — уже пауза после фразы
        keep = len(self._phrase) - max(0, trailing - PAD_MS // WINDOW_MS)
        windows = self._phrase[:keep]
        speech = self._speech
        start = self._phrase_start
        self._before.clear()
        self._before.extend(self._phrase[keep:])
        self._phrase = []
        if speech * WINDOW_MS < MIN_SPEECH_MS:
            return None
        to_s = WINDOW / SAMPLE_RATE
        return Phrase(np.concatenate(windows), start * to_s, (start + len(windows)) * to_s)
