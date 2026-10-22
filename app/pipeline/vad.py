"""Поиск речи в потоке. Владелец: Ярослав.

Silero VAD уже входит в faster-whisper как ONNX-модель
(faster_whisper.vad.get_speech_timestamps), PyTorch не нужен.
Цель из презентации — буферы по 30 мс.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

SAMPLE_RATE = 16_000


def speech_segments(pcm: np.ndarray, sample_rate: int = SAMPLE_RATE) -> list[tuple[float, float]]:
    """Вернуть отрезки речи (начало, конец) в секундах.

    TODO(Ярослав): реализовать на faster_whisper.vad.
    """
    raise NotImplementedError("pipeline/vad.py: speech_segments")
