"""Распознавание речи. Владелец: Ярослав.

faster-whisper, модель из WHISPER_MODEL (по умолчанию base, int8 на CPU; замеры в analytics/README.md).
Цель: шкала на экране меньше чем через 1,5 секунды после реплики.
small точнее на шумных записях, но вдвое-втрое медленнее: включать, если запас по времени позволит.
"""

from __future__ import annotations

import functools
import logging

import numpy as np
from faster_whisper import WhisperModel

from app.config import settings
from app.pipeline.vad import to_float

log = logging.getLogger(__name__)


@functools.cache
def model() -> WhisperModel:
    """Модель загружается один раз на процесс: первый вызов долгий, остальные мгновенные."""
    log.info("загружаю модель распознавания %s (%s)", settings.whisper_model, settings.whisper_compute_type)
    return WhisperModel(
        settings.whisper_model,
        device="cpu",
        compute_type=settings.whisper_compute_type,
        download_root=settings.whisper_download_root,
    )


def transcribe(pcm: bytes | np.ndarray) -> str:
    """Распознать один отрезок речи на русском.

    Отрезок уже найден VAD, поэтому свой VAD Whisper не включаем, а контекст
    прошлых фраз не передаём: реплики разных людей не связаны. Повторы с
    другой температурой выключены: на неуверенной фразе они растягивали
    распознавание до 2–3 секунд, а это весь бюджет задержки.
    """
    segments, _ = model().transcribe(
        to_float(pcm),
        language="ru",
        beam_size=1,
        temperature=0,
        vad_filter=False,
        condition_on_previous_text=False,
        without_timestamps=True,
    )
    return " ".join(s.text.strip() for s in segments).strip()
