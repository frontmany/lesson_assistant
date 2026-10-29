"""Сборка конвейера: источник → VAD → ASR → классификатор → скоринг → события.

Владелец: Ярослав.
"""

import asyncio
import bisect
import json
import logging
import time
from collections import deque
from collections.abc import AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.config import settings
from app.models import LEVEL_LABELS, LessonEvent, Roster, ScaleUpdate, Utterance

log = logging.getLogger(__name__)

# Поиск речи и распознавание идут каждый в своём потоке, а цикл событий
# в это время принимает кадры и рассылает события. Несколько потоков
# распознавания и cpu_threads — в #26
_vad = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vad")
_asr = ThreadPoolExecutor(max_workers=1, thread_name_prefix="asr")
# Сколько кадров ждёт поиска речи: около секунды звука от 30 участников.
# Если поток не успевает, старые кадры сбрасываются, как и в sources/call.py
BACKLOG_FRAMES = 1000
# Как часто проверять флаги без новых реплик: «давно не отвечал» — это минуты
FLAGS_EVERY_S = 30
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
    utterances — реплики урока по времени: {"student_id", "t", "end", "text"}
    и "kind" у учеников (см. transcribe_stream).
    """
    log.info("урок %s завершён, реплик: %d", lesson_id, len(utterances))


@dataclass
class _Job:
    """Найденная фраза, которая ждёт распознавания."""

    who: str
    pcm: np.ndarray
    t: float  # начало речи, секунды от начала урока
    end: float  # конец речи
    detected: float  # time.monotonic(), когда фраза найдена


def next_job(jobs: list[_Job], students: set[str]) -> _Job:
    """Какую фразу распознавать следующей.

    Ученики раньше учителя: их реплики ждут на экране, а учитель нужен
    только как контекст. Но фраза учителя, которая кончилась до начала фразы
    ученика, и есть этот контекст: в ней учитель мог назвать ученика по
    имени или задать вопрос. Её распознаём раньше, иначе классификатор не
    увидит обращения и отклик станет «другим».
    """
    waiting = [j for j in jobs if j.who in students]
    teacher = [j for j in jobs if j.who not in students]
    if not waiting:
        return min(teacher, key=lambda j: j.end)
    first = min(waiting, key=lambda j: j.end)
    before = [j for j in teacher if j.end <= first.t]
    return min(before, key=lambda j: j.end) if before else first


def load_history(path: Path | None = None) -> list[float]:
    """Активность прошлых уроков класса, от старых к новым: для нормы урока.

    Урок в history_seed.json: {"date": "2026-10-01", "activity": 5.5, "quiet": false},
    activity — баллы на ученика (scoring.lesson_activity). «Тихие» уроки в норму не идут.
    TODO(#23): прошлые уроки этого класса из БД, history_seed.json — для нового класса.
    """
    path = path or settings.demo_lesson_dir / "history_seed.json"
    lessons = json.loads(path.read_text(encoding="utf-8")).get("lessons", [])
    return [float(lesson["activity"]) for lesson in lessons if not lesson.get("quiet")]


async def transcribe_stream(
    frames: AsyncIterator[tuple[str, bytes]],
    roster: Roster,
    emit: Callable[[LessonEvent], None],
    transcribe: Callable[[np.ndarray], str] | None = None,
    norm: float | None = None,
) -> list[dict]:
    """Кадры участников → фразы → текст → тип реплики и шкала → события.

    Задачи: приём кадров, поиск фраз, распознавание и проверка флагов. Поиск
    фраз идёт в своём потоке пачками: на 30 учеников это около тысячи
    прогонов Silero в секунду, и в цикле событий они задерживали бы приём
    кадров и WebSocket. Порядок распознавания — next_job. Кадры тех, кого
    нет в составе класса, отбрасываются.

    Когда кадры кончились, недоговорённые фразы дораспознаются, и функция
    возвращает реплики урока по времени: {"student_id", "t", "end", "text"}
    и "kind" у учеников. norm — норма урока, по умолчанию из прошлых уроков.
    """
    from app.pipeline import asr
    from app.pipeline.classify import classify
    from app.pipeline.scoring import Scoreboard, lesson_norm
    from app.pipeline.vad import Phrase, PhraseDetector

    loop = asyncio.get_running_loop()
    if transcribe is None:
        transcribe = asr.transcribe
        await loop.run_in_executor(_asr, asr.model)  # загрузить модель до первой фразы

    started = time.monotonic()
    students = [s.id for s in roster.students]
    is_student = set(students)
    known = is_student | {roster.teacher.id}
    context: list[dict] = []

    def lesson_time() -> float:
        return time.monotonic() - started

    board = Scoreboard(
        students,
        norm if norm is not None else lesson_norm(load_history()),
        settings.lesson_minutes * 60,
    )
    shown: dict[str, tuple[int, list]] = {}  # что сейчас на шкале у каждого ученика

    def show_scale(now: float) -> None:
        """Разослать ступени и флаги, которые изменились с прошлого раза."""
        for sid in students:
            level, flags = board.level(sid), board.flags(sid, now)
            if shown.get(sid) != (level, flags):
                shown[sid] = (level, flags)
                emit(ScaleUpdate(student_id=sid, level=level, label=LEVEL_LABELS[level], flags=flags))

    # Поток _vad. Время урока у каждого участника — его звук: позиция детектора
    # плюс сдвиг. Сдвиг ставится по первому кадру и только растёт: если кадры
    # пришли позже, чем шёл звук (переподключение, сброшенные кадры), часы
    # участника догоняют время урока. Дрожание сети их не двигает назад.
    detectors: dict[str, PhraseDetector] = {}
    shifts: dict[str, float] = {}

    def to_job(who: str, phrase: Phrase) -> _Job:
        shift = shifts[who]
        start = max(round(shift + phrase.speech_start, 2), 0.0)
        return _Job(who, phrase.pcm, start, round(shift + phrase.speech_end, 2), time.monotonic())

    def find_phrases(batch: list[tuple[str, bytes, float]]) -> list[_Job]:
        found = []
        for who, pcm, at in batch:
            if who not in detectors:
                detectors[who] = PhraseDetector()
            phrases = detectors[who].feed(pcm)
            # at — время урока, когда пришёл кадр; звук в нём кончается на position
            shifts[who] = max(shifts.get(who, float("-inf")), at - detectors[who].position)
            found += [to_job(who, phrase) for phrase in phrases]
        return found

    def flush() -> list[_Job]:
        return [to_job(who, phrase) for who, d in detectors.items() if (phrase := d.flush()) is not None]

    backlog: deque[tuple[str, bytes, float]] = deque()
    arrived = asyncio.Event()
    reading = True
    jobs: list[_Job] = []
    busy = False
    changed = asyncio.Condition()

    async def add_jobs(found: list[_Job]) -> None:
        if found:
            async with changed:
                jobs.extend(found)
                changed.notify_all()

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
                backlog.append((who, pcm, lesson_time()))
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
            if batch:
                await add_jobs(await loop.run_in_executor(_vad, find_phrases, batch))
        # кадров больше не будет: кто говорил до последнего, тоже попадёт в урок
        await add_jobs(await loop.run_in_executor(_vad, flush))

    async def handle(job: _Job) -> None:
        picked = time.monotonic()
        text = await loop.run_in_executor(_asr, transcribe, job.pcm)
        done = time.monotonic()
        if not text:
            return
        log.info(
            "%s, %.1f–%.1f с: «%s» (от конца речи %.2f с: поиск фразы %.2f, очередь %.2f, распознавание %.2f)",
            job.who, job.t, job.end, text, done - started - job.end,
            job.detected - started - job.end, picked - job.detected, done - picked,
        )
        item = {"student_id": job.who, "t": job.t, "end": job.end, "text": text}
        if job.who in is_student:
            kind = classify(text, job.who, job.t, roster, context)
            item["kind"] = kind
            emit(Utterance(student_id=job.who, t=job.t, text=text, kind=kind, addressed=kind == "response"))
            board.add(job.who, kind, text, job.t, job.end)
            show_scale(lesson_time())
        # распознаём не по порядку (next_job), а классификатор ждёт реплики по времени
        bisect.insort(context, item, key=lambda i: i["t"])

    async def recognize() -> None:
        nonlocal busy
        while True:
            async with changed:
                await changed.wait_for(lambda: jobs)
                job = next_job(jobs, is_student)
                jobs.remove(job)
                busy = True
            try:
                await handle(job)
            finally:
                async with changed:
                    busy = False
                    changed.notify_all()

    async def watch_flags() -> None:
        # «давно не отвечал» появляется, когда ученик молчит, а не когда он говорит
        while True:
            await asyncio.sleep(FLAGS_EVERY_S)
            board.tick(lesson_time())
            show_scale(lesson_time())

    show_scale(0.0)  # все начинают с первой ступени
    reader = asyncio.create_task(read())
    detector = asyncio.create_task(detect())
    tasks = [reader, detector, asyncio.create_task(recognize()), asyncio.create_task(watch_flags())]
    try:
        await asyncio.gather(reader, detector)
        async with changed:
            await changed.wait_for(lambda: not jobs and not busy)
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
