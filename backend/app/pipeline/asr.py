"""Распознавание речи, Whisper или GigaAM"""

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

# Признаки галлюцинации Whisper
MAX_COMPRESSION_RATIO = 2.4
NO_SPEECH_PROB = 0.6
MIN_AVG_LOGPROB = -1.0
TOKENS_PER_SECOND = 12
MAX_TOKENS = 350

Role = Literal["teacher", "student"]
BEAM_SIZE: dict[Role, int] = {"teacher": 1, "student": 5}


def model_name(role: Role) -> str:
    return settings.whisper_model_teacher if role == "teacher" else settings.whisper_model_student


def is_gigaam(name: str) -> bool:
    return name.startswith("gigaam")


@functools.cache
def _load(name: str):
    """Модель загружается один раз на процесс"""
    if is_gigaam(name):
        import onnx_asr

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
    """Распознать отрезок речи моделью для этой роли"""
    audio = to_float(pcm)
    if is_gigaam(model_name(role)):
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
