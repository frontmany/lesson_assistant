"""Распознавание речи. Владелец: Ярослав.

faster-whisper в int8 на CPU или GigaAM v3 через onnx-asr (имя модели
начинается с «gigaam»), у учителя и учеников разные модели (#55).
Учитель говорит чётко, от него нужны только имена и вопросы: Whisper small,
имена класса подсказкой. Ученикам важнее качество: перепутанные слова и
потерянный «?» стоят им инициативы, поэтому у них GigaAM v3 (сравнение на
живых фразах — analytics/README.md). Цель: шкала на экране не позже чем через
20 секунд после реплики, главное — чтобы очередь не росла к концу урока.
"""

from __future__ import annotations

import functools
import logging
from pathlib import Path
from typing import Literal

import numpy as np
from faster_whisper import WhisperModel
from faster_whisper.transcribe import Segment

from app.config import settings
from app.models import Roster
from app.pipeline.classify import SHORT_NAMES
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
MAX_TOKENS = 350  # Whisper принимает 448 вместе с подсказкой; фразу длиннее 30 секунд он делит сам

Role = Literal["teacher", "student"]
BEAM_SIZE: dict[Role, int] = {"teacher": 1, "student": 5}


def model_name(role: Role) -> str:
    return settings.whisper_model_teacher if role == "teacher" else settings.whisper_model_student


def is_gigaam(name: str) -> bool:
    return name.startswith("gigaam")


@functools.cache
def _load(name: str):
    """Модель загружается один раз на процесс: первый вызов долгий, остальные мгновенные."""
    if is_gigaam(name):
        import onnx_asr

        # первый раз скачивается в эту папку, дальше берётся из неё без сети
        log.info("загружаю модель распознавания %s (onnx)", name)
        return onnx_asr.load_model(name, Path(settings.whisper_download_root) / name)
    log.info("загружаю модель распознавания %s (%s)", name, settings.whisper_compute_type)
    return WhisperModel(
        name,
        device="cpu",
        compute_type=settings.whisper_compute_type,
        download_root=settings.whisper_download_root,
    )


def model(role: Role):
    return _load(model_name(role))


def class_names(roster: Roster) -> str:
    """Имена учеников подсказкой для учителя: «Иван, Ваня, Артём, ...»."""
    names: list[str] = []
    for student in roster.students:
        first = student.name.split()[0]
        forms = SHORT_NAMES.get(first.lower().replace("ё", "е"), [])
        names += [first, *(form.capitalize() for form in forms)]
    return ", ".join(dict.fromkeys(names))


def is_hallucination(segment: Segment) -> bool:
    if segment.compression_ratio > MAX_COMPRESSION_RATIO:
        return True
    return segment.no_speech_prob > NO_SPEECH_PROB and segment.avg_logprob < MIN_AVG_LOGPROB


def transcribe(pcm: bytes | np.ndarray, role: Role = "student", hotwords: str | None = None) -> str:
    """Распознать один отрезок речи на русском моделью для этой роли.

    Отрезок уже найден VAD, поэтому свой VAD Whisper не включаем, а контекст
    прошлых фраз не передаём: реплики разных людей не связаны. Повторы с
    другой температурой выключены: на неуверенной фразе они растягивали
    распознавание в несколько раз. Вместо них галлюцинации гасятся запретом
    повторов и отбрасываются по признакам выше. hotwords — слова, которых
    модель ждёт в этой фразе.
    """
    audio = to_float(pcm)
    if is_gigaam(model_name(role)):
        # GigaAM сама ставит знаки и не галлюцинирует повторами, как Whisper; подсказок у неё нет
        return model(role).recognize(audio, sample_rate=SAMPLE_RATE).strip()
    segments, _ = model(role).transcribe(audio, **options(role, audio, hotwords))
    return join_segments(segments)


def options(role: Role, audio: np.ndarray, hotwords: str | None = None) -> dict:
    return {
        "language": "ru",
        "beam_size": BEAM_SIZE[role],
        "hotwords": hotwords,
        "temperature": 0,
        "no_repeat_ngram_size": 4,
        "max_new_tokens": min(MAX_TOKENS, max(10, int(len(audio) / SAMPLE_RATE * TOKENS_PER_SECOND))),
        "vad_filter": False,
        "condition_on_previous_text": False,
        "without_timestamps": True,
    }


def join_segments(segments) -> str:
    """Текст фразы без отрезков с признаками галлюцинации."""
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
