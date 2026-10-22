"""Распознавание речи. Владелец: Ярослав.

faster-whisper, модель из WHISPER_MODEL (по умолчанию small, int8 на CPU).
Цель: шкала на экране меньше чем через 1,5 секунды после реплики.
Если не успеваем — модель base и короче чанки.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np


def transcribe(pcm: np.ndarray) -> str:
    """Распознать один отрезок речи на русском.

    TODO(Ярослав): загрузить WhisperModel один раз при старте
    (settings.whisper_model, settings.whisper_download_root), language="ru".
    """
    raise NotImplementedError("pipeline/asr.py: transcribe")
