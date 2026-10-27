"""Сборка конвейера: источник → VAD → ASR → классификатор → скоринг → события.

Владелец: Ярослав.
"""

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from app.models import Roster, Utterance

if TYPE_CHECKING:
    from app.pipeline.vad import PhraseDetector

log = logging.getLogger(__name__)

# Распознавание идёт в одном отдельном потоке: модель одна, а цикл событий
# в это время принимает кадры. Несколько потоков и cpu_threads — в #26
_asr = ThreadPoolExecutor(max_workers=1, thread_name_prefix="asr")
_running: dict[str, asyncio.Task] = {}


def start(lesson_id: str, roster: Roster) -> bool:
    """Запустить конвейер урока в фоне. False — он уже работает."""
    task = _running.get(lesson_id)
    if task and not task.done():
        return False
    task = asyncio.create_task(run_call(lesson_id, roster))
    task.add_done_callback(lambda t: _log_end(lesson_id, t))
    _running[lesson_id] = task
    return True


def _log_end(lesson_id: str, task: asyncio.Task) -> None:
    if not task.cancelled() and task.exception():
        log.error("конвейер урока %s упал", lesson_id, exc_info=task.exception())


async def run_call(lesson_id: str, roster: Roster) -> None:
    """Слушать звонок урока до его конца и рассылать события по WebSocket.

    TODO(Ярослав): classify.classify и scoring — события ScaleUpdate.
    """
    from app.sources.call import listen
    from app.ws import lesson as ws

    ws.set_live(lesson_id, True)
    try:
        await transcribe_stream(listen(lesson_id), roster, lambda event: ws.publish(lesson_id, event))
    finally:
        ws.set_live(lesson_id, False)


async def transcribe_stream(
    frames: AsyncIterator[tuple[str, bytes]],
    roster: Roster,
    emit: Callable[[Utterance], None],
    transcribe: Callable[[np.ndarray], str] | None = None,
) -> None:
    """Кадры участников → фразы → текст → события Utterance.

    Кадры тех, кого нет в составе класса, отбрасываются. Реплики учителя
    распознаются для контекста классификатора, но в панель не уходят.
    """
    from app.pipeline import asr
    from app.pipeline.classify import classify
    from app.pipeline.vad import MIN_SILENCE_MS, PhraseDetector

    loop = asyncio.get_running_loop()
    if transcribe is None:
        transcribe = asr.transcribe
        await loop.run_in_executor(_asr, asr.model)  # загрузить модель до первой фразы

    started = time.monotonic()
    students = {s.id for s in roster.students}
    known = students | {roster.teacher.id}
    detectors: dict[str, PhraseDetector] = {}
    context: list[dict] = []
    pending: set[asyncio.Task] = set()

    async def recognize(who: str, pcm: np.ndarray, t: float, detected: float) -> None:
        queued = time.monotonic()
        text = await loop.run_in_executor(_asr, transcribe, pcm)
        done = time.monotonic()
        if not text:
            return
        # речь кончилась за MIN_SILENCE_MS до того, как фраза найдена
        log.info(
            "%s, %.1f с: «%s» (задержка от конца речи %.2f с: пауза %.2f, очередь %.2f, распознавание %.2f)",
            who, t, text, done - detected + MIN_SILENCE_MS / 1000,
            MIN_SILENCE_MS / 1000, queued - detected, done - queued,
        )
        if who in students:
            try:
                kind = classify(text, who, t, roster, context)
            except NotImplementedError:
                kind = "other"
            emit(Utterance(student_id=who, t=t, text=text, kind=kind, addressed=False))
        context.append({"student_id": who, "t": t, "text": text})

    try:
        async for who, pcm in frames:
            if who not in known:
                continue
            if who not in detectors:
                detectors[who] = PhraseDetector()
            for phrase in detectors[who].feed(pcm):
                now = time.monotonic()
                t = round(now - started - phrase.duration, 1)
                task = asyncio.create_task(recognize(who, phrase.pcm, max(t, 0.0), now))
                pending.add(task)
                task.add_done_callback(pending.discard)
        await asyncio.gather(*pending)
    finally:
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)


def run_lesson(roster: Roster, tracks: list[Path]) -> None:
    """Прогнать записанный урок без звонка и напечатать транскрипт, шкалы и сводку.

    Нужно для отладки конвейера и замеров качества (analytics/).
    TODO(Ярослав): тот же конвейер, что в run_call, но кадры читаются из WAV.
    """
    raise NotImplementedError("pipeline/runner.py: run_lesson")
