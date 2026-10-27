"""Распознавание речи. Владелец: Ярослав.

faster-whisper, модель из WHISPER_MODEL (по умолчанию base, int8 на CPU; замеры в analytics/README.md).
Цель: шкала на экране меньше чем через 1,5 секунды после реплики.
small точнее на шумных записях, но вдвое-втрое медленнее: включать, если запас по времени позволит.
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
