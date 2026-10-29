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
_running: dict[str, tuple[asyncio.Task, asyncio.Event]] = {}  # урок -> конвейер и сигнал «завершить»


def start(lesson_id: str, roster: Roster) -> bool:
    """Запустить конвейер урока в фоне. False — он уже работает."""
    if is_running(lesson_id):
        return False
    stop = asyncio.Event()
    task = asyncio.create_task(run_call(lesson_id, roster, stop))
    task.add_done_callback(lambda t: _log_end(lesson_id, t))
    _running[lesson_id] = (task, stop)
    return True


def is_running(lesson_id: str) -> bool:
    return lesson_id in _running and not _running[lesson_id][0].done()


async def end(lesson_id: str) -> bool:
    """Учитель завершил урок: ассистент выходит из комнаты, очередь дораспознаётся.

    Возвращает, когда конвейер закончил и урок завершён (finish_lesson).
    False — урок не идёт: не начат или уже завершён, повторный вызов безопасен.
    """
    if not is_running(lesson_id):
        return False
    task, stop = _running[lesson_id]
    stop.set()
    await asyncio.gather(task, return_exceptions=True)
    return True


def _log_end(lesson_id: str, task: asyncio.Task) -> None:
    if not task.cancelled() and task.exception():
        log.error("конвейер урока %s упал", lesson_id, exc_info=task.exception())


async def run_call(lesson_id: str, roster: Roster, stop: asyncio.Event) -> None:
    """Слушать звонок урока до его конца и рассылать события по WebSocket.

    Урок кончается, когда выставлен stop (POST /end) или комната пуста
    5 минут (sources.call.EMPTY_ROOM_S). Тогда ассистент выходит из комнаты,
    конвейер дораспознаёт очередь и вызывает finish_lesson.

    TODO(Ярослав): classify.classify и scoring — события ScaleUpdate.
    """
    from app.sources.call import listen
    from app.ws import lesson as ws

    ws.set_live(lesson_id, True)
    try:
        utterances = await transcribe_stream(
            listen(lesson_id, stop), roster, lambda event: ws.publish(lesson_id, event)
        )
    finally:
        ws.set_live(lesson_id, False)
    await finish_lesson(lesson_id, roster, utterances)


async def finish_lesson(lesson_id: str, roster: Roster, utterances: list[dict]) -> None:
    """Урок закончен, ассистент вышел, все реплики распознаны.

    Единственная точка конца урока: отсюда #23 строит сводку и пишет урок
    в БД, а бот рассылает сводку учителю и карточки ученикам (#21).
    utterances — реплики урока по порядку: {"student_id", "t", "text"}.
    """
    log.info("урок %s завершён, реплик: %d", lesson_id, len(utterances))


async def transcribe_stream(
    frames: AsyncIterator[tuple[str, bytes]],
    roster: Roster,
    emit: Callable[[Utterance], None],
    transcribe: Callable[[np.ndarray], str] | None = None,
) -> list[dict]:
    """Кадры участников → фразы → текст → события Utterance.

    Когда кадры кончились, недоговорённые фразы дораспознаются, и функция
    возвращает все реплики урока по порядку распознавания.

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

    def flush() -> list[tuple[str, Phrase]]:
        return [(who, phrase) for who, d in detectors.items() if (phrase := d.flush()) is not None]

    def queue_phrase(who: str, phrase: Phrase) -> None:
        now = time.monotonic()
        t = max(round(now - started - phrase.duration, 1), 0.0)
        priority = 0 if who in students else 1
        phrases.put_nowait((priority, next(order), who, phrase.pcm, t, now))

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
                queue_phrase(who, phrase)
        # кадров больше не будет: кто говорил до последнего, тоже попадёт в урок
        for who, phrase in await loop.run_in_executor(_vad, flush):
            queue_phrase(who, phrase)

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
    return context


def run_lesson(roster: Roster, tracks: list[Path]) -> None:
    """Прогнать записанный урок без звонка и напечатать транскрипт, шкалы и сводку.

    Нужно для отладки конвейера и замеров качества (analytics/).
    TODO(Ярослав): тот же конвейер, что в run_call, но кадры читаются из WAV.
    """
    raise NotImplementedError("pipeline/runner.py: run_lesson")
