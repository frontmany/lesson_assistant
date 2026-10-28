"""Сборка конвейера: источник → VAD → ASR → классификатор → скоринг → события.

Владелец: Ярослав.
"""

import asyncio
import itertools
import logging
import time
from collections import deque
from collections.abc import AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from app.models import Roster, Utterance

if TYPE_CHECKING:
    from app.pipeline.vad import PhraseDetector

log = logging.getLogger(__name__)

# Поиск речи и распознавание идут каждый в своём потоке, а цикл событий
# в это время принимает кадры и рассылает события. Несколько потоков
# распознавания и cpu_threads — в #26
_vad = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vad")
_asr = ThreadPoolExecutor(max_workers=1, thread_name_prefix="asr")
# Сколько кадров ждёт поиска речи: около секунды звука от 30 участников.
# Если поток не успевает, старые кадры сбрасываются, как и в sources/call.py
BACKLOG_FRAMES = 1000
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

    Три задачи: приём кадров, поиск фраз и распознавание. Поиск фраз идёт
    в своём потоке пачками: на 30 учеников это около тысячи прогонов Silero
    в секунду, и в цикле событий они задерживали бы приём кадров и WebSocket.
    Распознавание берёт фразы учеников раньше фраз учителя: учитель нужен
    только как контекст классификатора, а реплики учеников ждут на экране.
    Кадры тех, кого нет в составе класса, отбрасываются.
    """
    from app.pipeline import asr
    from app.pipeline.classify import classify
    from app.pipeline.vad import MIN_SILENCE_MS, Phrase, PhraseDetector

    loop = asyncio.get_running_loop()
    if transcribe is None:
        transcribe = asr.transcribe
        await loop.run_in_executor(_asr, asr.model)  # загрузить модель до первой фразы

    started = time.monotonic()
    students = {s.id for s in roster.students}
    known = students | {roster.teacher.id}
    detectors: dict[str, PhraseDetector] = {}  # трогает только поток _vad
    context: list[dict] = []

    backlog: deque[tuple[str, bytes]] = deque()
    arrived = asyncio.Event()
    reading = True
    phrases: asyncio.PriorityQueue = asyncio.PriorityQueue()
    order = itertools.count()  # при равном приоритете — кто раньше закончил фразу

    def find_phrases(batch: list[tuple[str, bytes]]) -> list[tuple[str, Phrase]]:
        found = []
        for who, pcm in batch:
            if who not in detectors:
                detectors[who] = PhraseDetector()
            found += [(who, phrase) for phrase in detectors[who].feed(pcm)]
        return found

    async def read() -> None:
        nonlocal reading
        dropped = 0
        try:
            async for who, pcm in frames:
                if who not in known:
                    continue
                if len(backlog) >= BACKLOG_FRAMES:
                    backlog.popleft()
                    dropped += 1
                    if dropped % BACKLOG_FRAMES == 1:
                        log.warning("поиск речи не успевает, сброшено старых кадров: %d", dropped)
                backlog.append((who, pcm))
                arrived.set()
        finally:
            reading = False
            arrived.set()

    async def detect() -> None:
        while reading or backlog:
            await arrived.wait()
            arrived.clear()
            batch = list(backlog)
            backlog.clear()
            if not batch:
                continue
            for who, phrase in await loop.run_in_executor(_vad, find_phrases, batch):
                now = time.monotonic()
                t = max(round(now - started - phrase.duration, 1), 0.0)
                priority = 0 if who in students else 1
                phrases.put_nowait((priority, next(order), who, phrase.pcm, t, now))

    async def recognize() -> None:
        while True:
            _, _, who, pcm, t, detected = await phrases.get()
            try:
                queued = time.monotonic()
                text = await loop.run_in_executor(_asr, transcribe, pcm)
                done = time.monotonic()
                if not text:
                    continue
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
            finally:
                phrases.task_done()

    reader = asyncio.create_task(read())
    detector = asyncio.create_task(detect())
    recognizer = asyncio.create_task(recognize())
    tasks = [reader, detector, recognizer]
    try:
        await asyncio.gather(reader, detector)
        await phrases.join()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def run_lesson(roster: Roster, tracks: list[Path]) -> None:
    """Прогнать записанный урок без звонка и напечатать транскрипт, шкалы и сводку.

    Нужно для отладки конвейера и замеров качества (analytics/).
    TODO(Ярослав): тот же конвейер, что в run_call, но кадры читаются из WAV.
    """
    raise NotImplementedError("pipeline/runner.py: run_lesson")
