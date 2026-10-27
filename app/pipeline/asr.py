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
from faster_whisper.transcribe import Segment

from app.config import settings
from app.pipeline.vad import SAMPLE_RATE, to_float

log = logging.getLogger(__name__)

# Признаки галлюцинации, пороги из самого Whisper. На музыке и неразборчивом
# звуке он повторяет одну фразу по кругу («это не то, что это не то, что…»)
# или выдумывает текст там, где сам считает, что речи нет.
MAX_COMPRESSION_RATIO = 2.4  # текст из повторов жмётся сильнее обычного
NO_SPEECH_PROB = 0.6
MIN_AVG_LOGPROB = -1.0
# Потолок длины текста: речь — около 6 токенов в секунду, вдвое больше не бывает
TOKENS_PER_SECOND = 12


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


def is_hallucination(segment: Segment) -> bool:
    if segment.compression_ratio > MAX_COMPRESSION_RATIO:
        return True
    return segment.no_speech_prob > NO_SPEECH_PROB and segment.avg_logprob < MIN_AVG_LOGPROB


def transcribe(pcm: bytes | np.ndarray) -> str:
    """Распознать один отрезок речи на русском.

    Отрезок уже найден VAD, поэтому свой VAD Whisper не включаем, а контекст
    прошлых фраз не передаём: реплики разных людей не связаны. Повторы с
    другой температурой выключены: на неуверенной фразе они растягивали
    распознавание до 2–3 секунд, а это весь бюджет задержки. Вместо них
    галлюцинации гасятся запретом повторов и отбрасываются по признакам выше.
    """
    audio = to_float(pcm)
    segments, _ = model().transcribe(
        audio,
        language="ru",
        beam_size=1,
        temperature=0,
        no_repeat_ngram_size=4,
        max_new_tokens=max(10, int(len(audio) / SAMPLE_RATE * TOKENS_PER_SECOND)),
        vad_filter=False,
        condition_on_previous_text=False,
        without_timestamps=True,
    )
    kept = []
    for segment in segments:
        if is_hallucination(segment):
            log.info(
                "отброшено как галлюцинация: «%s» (сжатие %.1f, нет речи %.2f, logprob %.2f)",
                segment.text.strip(), segment.compression_ratio, segment.no_speech_prob, segment.avg_logprob,
            )
        else:
            kept.append(segment.text.strip())
    return " ".join(kept).strip()
