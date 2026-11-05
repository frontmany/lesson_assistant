"""Сборка конвейера: источник → VAD → ASR → классификатор → скоринг → события.

Владелец: Ярослав.
"""

import asyncio
import bisect
import logging
import time
import wave
from collections import deque
from collections.abc import AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.config import settings
from app.models import LEVEL_LABELS, LessonEvent, Roster, ScaleUpdate, Student, Utterance

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
# Кадры того, кого нет в составе урока: раз в столько секунд проверяем, не вошёл ли он в класс
STRANGER_RECHECK_S = 10
@dataclass
class _Lesson:
    """Идущий урок: конвейер, его состав и виртуальный класс, если он есть."""

    roster: Roster
    stop: asyncio.Event  # «завершить урок»
    listening: asyncio.Event  # ассистент в комнате: виртуальный класс может начинать
    task: asyncio.Task | None = None
    virtual: asyncio.Task | None = None
    virtual_stop: asyncio.Event | None = None


_lessons: dict[str, _Lesson] = {}

# Выключатель аналитики (#22): у кого в уроке она выключена. ALL — у всего класса.
# Живёт, пока урок не закончится: учитель может выключить и до входа в звонок
ALL = "*"
_off: dict[str, set[str]] = {}


def set_analytics(lesson_id: str, enabled: bool, student_id: str | None = None) -> None:
    off = _off.setdefault(lesson_id, set())
    if student_id is None:
        # выключить у класса — ни у кого не считать; включить у класса — у всех снова считать
        off.clear()
        if not enabled:
            off.add(ALL)
    elif enabled:
        off.discard(student_id)
    else:
        off.add(student_id)


def analytics_off(lesson_id: str, student_id: str) -> bool:
    off = _off.get(lesson_id, ())
    return ALL in off or student_id in off


def analytics_off_list(lesson_id: str) -> list[str]:
    return sorted(_off.get(lesson_id, ()))


def start(lesson_id: str, roster: Roster) -> bool:
    """Запустить конвейер урока в фоне. False — он уже работает."""
    if is_running(lesson_id):
        return False
    lesson = _Lesson(roster, asyncio.Event(), asyncio.Event())
    lesson.task = asyncio.create_task(run_call(lesson_id, lesson))
    lesson.task.add_done_callback(lambda t: _log_end(lesson_id, t))
    _lessons[lesson_id] = lesson
    return True


def is_running(lesson_id: str) -> bool:
    lesson = _lessons.get(lesson_id)
    return lesson is not None and not lesson.task.done()


def when_finished(lesson_id: str, callback: Callable[[str], None]) -> None:
    """Вызвать callback(lesson_id), когда конвейер урока закончится: «Завершить урок» или пустая комната."""
    lesson = _lessons.get(lesson_id)
    if lesson is not None and lesson.task is not None:
        lesson.task.add_done_callback(lambda _: callback(lesson_id))


def roster_of(lesson_id: str) -> Roster | None:
    """Состав идущего урока: с виртуальными учениками, если урок начат с ними."""
    return _lessons[lesson_id].roster if is_running(lesson_id) else None


async def end(lesson_id: str) -> bool:
    """Учитель завершил урок: ассистент выходит из комнаты, очередь дораспознаётся.

    Возвращает, когда конвейер закончил и урок завершён (finish_lesson).
    False — урок не идёт: не начат или уже завершён, повторный вызов безопасен.
    """
    if not is_running(lesson_id):
        return False
    lesson = _lessons[lesson_id]
    lesson.stop.set()
    await asyncio.gather(lesson.task, return_exceptions=True)
    return True


def play_virtual(lesson_id: str, demo: Roster, tracks: list[Path]) -> bool:
    """Завести в идущий урок виртуальный класс: записанные дорожки заходят ботами.

    Боты начинают говорить, когда ассистент уже в комнате, и уходят вместе
    с уроком. False — урок не идёт или виртуальный класс уже играет.
    """
    from app.sources.virtual_class import play

    if not is_running(lesson_id):
        return False
    lesson = _lessons[lesson_id]
    if lesson.virtual and not lesson.virtual.done():
        return False

    async def run() -> None:
        # ждём, пока ассистент войдёт в комнату, иначе начало записи пропадёт
        waits = [asyncio.create_task(lesson.listening.wait()), asyncio.create_task(lesson.virtual_stop.wait())]
        await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
        for task in waits:
            task.cancel()
        if not lesson.virtual_stop.is_set():
            await play(lesson_id, demo, tracks, lesson.virtual_stop)

    lesson.virtual_stop = asyncio.Event()
    lesson.virtual = asyncio.create_task(run())
    lesson.virtual.add_done_callback(lambda t: _log_end(lesson_id, t))
    return True


async def stop_virtual(lesson_id: str) -> bool:
    """Боты виртуального класса выходят, урок идёт дальше. False — их нет."""
    lesson = _lessons.get(lesson_id)
    if lesson is None or lesson.virtual is None or lesson.virtual.done():
        return False
    lesson.virtual_stop.set()
    await asyncio.gather(lesson.virtual, return_exceptions=True)
    return True


def _log_end(lesson_id: str, task: asyncio.Task) -> None:
    if not task.cancelled() and task.exception():
        log.error("урок %s: фоновая задача упала", lesson_id, exc_info=task.exception())


async def run_call(lesson_id: str, lesson: _Lesson) -> None:
    """Слушать звонок урока до его конца и рассылать события по WebSocket.

    Урок кончается, когда выставлен stop (POST /end) или комната пуста
    5 минут (sources.call.EMPTY_ROOM_S). Тогда ассистент выходит из комнаты,
    виртуальный класс тоже, конвейер дораспознаёт очередь и вызывает finish_lesson.
    """
    from app.db import results
    from app.pipeline.scoring import Scoreboard, lesson_norm
    from app.sources.call import listen
    from app.ws import lesson as ws

    history = await asyncio.get_running_loop().run_in_executor(None, results.history_for, lesson_id)
    board = Scoreboard([s.id for s in lesson.roster.students], lesson_norm(history), settings.lesson_minutes * 60)

    def find_student(participant_id: str) -> Student | None:
        from app.db.classes import lesson_roster

        fresh = lesson_roster(lesson_id)
        return next((s for s in fresh.students if s.id == participant_id), None) if fresh else None

    ws.set_live(lesson_id, True)
    try:
        utterances = await transcribe_stream(
            listen(lesson_id, lesson.stop, ready=lesson.listening),
            lesson.roster,
            lambda event: ws.publish(lesson_id, event),
            find_student=find_student,
            board=board,
            off=lambda student_id: analytics_off(lesson_id, student_id),
        )
    finally:
        ws.set_live(lesson_id, False)
        await stop_virtual(lesson_id)
    await finish_lesson(lesson_id, lesson.roster, utterances, board)


async def finish_lesson(lesson_id: str, roster: Roster, utterances: list[dict], board) -> None:
    """Урок закончен, ассистент вышел, все реплики распознаны.

    Единственная точка конца урока: итоги урока пишутся в базу (db.results),
    отсюда же строится сводка, а бот рассылает её учителю и карточки ученикам (#21).
    utterances — реплики урока по времени: {"student_id", "t", "end", "text"}
    и "kind" у учеников (см. transcribe_stream); board — шкала урока (scoring.Scoreboard).
    """
    from app.bot import max_bot
    from app.db import results
    from app.db.classes import lesson_roster
    from app.pipeline import summary

    loop = asyncio.get_running_loop()
    # у кого аналитика выключена, тех нет ни в репликах, ни в ступенях урока (#22)
    utterances = [u for u in utterances if not u.get("off")]
    # шкала забывает ученика только на его реплике или раз в FLAGS_EVERY_S: если урок
    # завершили сразу после выключения, проверяем выключатель здесь
    off_now = {s.id for s in roster.students if analytics_off(lesson_id, s.id)}
    _off.pop(lesson_id, None)
    ended = max((u["end"] for u in utterances), default=0.0)
    levels = {
        s.id: (board.level(s.id), board.score(s.id), board.flags(s.id, ended))
        for s in roster.students if s.id in board.students() and s.id not in off_now
    }
    saved = await loop.run_in_executor(
        None, results.save_lesson, lesson_id, utterances, levels,
        board.activity(), board.base_norm, board.quiet(), roster.virtual_teacher is not None,
    )
    log.info(
        "урок %s завершён, реплик: %d, %s", lesson_id, len(utterances),
        "итоги в базе" if saved else "урока нет в базе, итоги не сохранены",
    )
    if not saved:
        return

    def report() -> None:
        # запросы к базе, GigaChat и боту блокирующие: целиком в отдельном потоке
        nonlocal roster
        # кто вошёл в класс во время урока, того нет в составе на начало урока. Берём
        # только тех, кто был на шкале: в демо состав класса — записанные ученики
        # без приставки virtual-, на уроке их не было
        fresh = lesson_roster(lesson_id)
        known = {s.id for s in roster.students}
        if fresh:
            joined = [s for s in fresh.students if s.id not in known and s.id in levels]
            roster = roster.model_copy(update={"students": roster.students + joined})
        streaks = results.silent_streaks(lesson_id, list(levels))
        teacher_summary = summary.build_summary(roster, utterances, levels, streaks)
        cards = summary.build_cards(roster, utterances, levels)
        results.save_summary(lesson_id, teacher_summary)
        results.save_cards(lesson_id, cards)
        log.info("сводка урока %s готова, карточек: %d", lesson_id, len(cards))
        max_bot.lesson_finished(roster, teacher_summary, cards, max(1, round(ended / 60)))

    try:
        await loop.run_in_executor(None, report)
    except Exception:
        log.exception("сводка урока %s не собрана", lesson_id)


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


def save_phrase(directory: Path, job: _Job, text: str) -> None:
    """Записать фразу в wav и рядом распознанный текст: так собирается набор для замеров."""
    try:
        directory.mkdir(parents=True, exist_ok=True)
        name = f"{job.t:07.1f}-{job.who}"
        with wave.open(str(directory / f"{name}.wav"), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(16_000)
            out.writeframes((np.clip(job.pcm, -1, 1) * 32767).astype(np.int16).tobytes())
        (directory / f"{name}.txt").write_text(text, encoding="utf-8")
    except OSError:
        # набор — побочная задача, урок из-за неё не падает
        log.exception("не удалось сохранить фразу %s", job.who)


async def transcribe_stream(
    frames: AsyncIterator[tuple[str, bytes]],
    roster: Roster,
    emit: Callable[[LessonEvent], None],
    transcribe: Callable[[np.ndarray, str], str] | None = None,
    norm: float | None = None,
    find_student: Callable[[str], Student | None] | None = None,
    board=None,
    off: Callable[[str], bool] = lambda student_id: False,
) -> list[dict]:
    """Кадры участников → фразы → текст → тип реплики и шкала → события.

    Задачи: приём кадров, поиск фраз, распознавание и проверка флагов. Поиск
    фраз идёт в своём потоке пачками: на 30 учеников это около тысячи
    прогонов Silero в секунду, и в цикле событий они задерживали бы приём
    кадров и WebSocket. Порядок распознавания — next_job. Кадры тех, кого
    нет в составе класса, отбрасываются.

    Когда кадры кончились, недоговорённые фразы дораспознаются, и функция
    возвращает реплики урока по времени: {"student_id", "t", "end", "text"}
    и "kind" у учеников. board — шкала урока (scoring.Scoreboard); без неё
    шкала строится здесь с нормой norm, по умолчанию из history_seed.json.

    find_student(id) ищет в составе урока того, чьих кадров не ждали: ученик
    мог войти в класс уже во время урока. Найденный добавляется в roster.students
    и на шкалу; посторонних по-прежнему отбрасываем.

    off(id) — выключена ли аналитика ученика (#22). Его речь распознаётся только
    как контекст для правил: события, шкала и итоги урока его не касаются, а в
    возвращаемых репликах он помечен "off" и снят со шкалы урока.
    """
    from app.pipeline import asr
    from app.pipeline.classify import CONTINUE_S, classify
    from app.db.results import seed_history
    from app.pipeline.scoring import Scoreboard, lesson_norm
    from app.pipeline.vad import Phrase, PhraseDetector

    loop = asyncio.get_running_loop()
    if transcribe is None:
        names = asr.class_names(roster)

        def transcribe(pcm: np.ndarray, role: str) -> str:
            return asr.transcribe(pcm, role, names if role == "teacher" else None)

        # загрузить модели до первой фразы
        for role in ("teacher", "student"):
            await loop.run_in_executor(_asr, asr.model, role)

    started = time.monotonic()
    students = [s.id for s in roster.students]
    is_student = set(students)
    # учитель из записи виртуального класса — тоже учитель: контекст классификатора
    teachers = {roster.teacher.id} | ({roster.virtual_teacher.id} if roster.virtual_teacher else set())
    known = is_student | teachers
    context: list[dict] = []
    phrases_dir = None
    if settings.save_phrases_dir:
        phrases_dir = Path(settings.save_phrases_dir) / time.strftime("%Y%m%d-%H%M%S")
        log.warning("фразы урока сохраняются в %s: только для тестовых уроков команды", phrases_dir)

    def lesson_time() -> float:
        return time.monotonic() - started

    if board is None:
        board = Scoreboard(
            students,
            norm if norm is not None else lesson_norm(seed_history()),
            settings.lesson_minutes * 60,
        )
    shown: dict[str, tuple[int, list]] = {}  # что сейчас на шкале у каждого ученика

    def show_scale(now: float) -> None:
        """Разослать ступени и флаги, которые изменились с прошлого раза."""
        for sid in students:
            if off(sid):
                board.forget(sid)
                shown.pop(sid, None)
                continue
            board.add_student(sid)  # аналитику включили снова: шкала с первой ступени
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

    checked: dict[str, float] = {}  # кого не ждали → когда последний раз искали в составе

    async def admit(who: str) -> None:
        student = await loop.run_in_executor(None, find_student, who)
        if student is None or who in known:
            return
        log.info("%s вошёл в класс во время урока, его реплики засчитываются", who)
        roster.students.append(student)
        students.append(who)
        is_student.add(who)
        known.add(who)
        board.add_student(who)
        show_scale(lesson_time())

    async def read() -> None:
        nonlocal reading
        dropped = 0
        try:
            async for who, pcm in frames:
                if who not in known:
                    if find_student and time.monotonic() - checked.get(who, -STRANGER_RECHECK_S) >= STRANGER_RECHECK_S:
                        checked[who] = time.monotonic()
                        asyncio.create_task(admit(who))
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

    def continues(who: str, t: float) -> bool:
        """Фраза — продолжение прошлой реплики того же ученика: между ними никто не говорил."""
        before = [item for item in context if item["t"] < t]
        return bool(before) and before[-1]["student_id"] == who and t - before[-1]["end"] <= CONTINUE_S

    async def handle(job: _Job) -> None:
        picked = time.monotonic()
        role = "student" if job.who in is_student else "teacher"
        text = await loop.run_in_executor(_asr, transcribe, job.pcm, role)
        done = time.monotonic()
        if phrases_dir:
            await loop.run_in_executor(_asr, save_phrase, phrases_dir, job, text)
        if not text:
            return
        log.info(
            "%s, %.1f–%.1f с: «%s» (от конца речи %.2f с: поиск фразы %.2f, очередь %.2f, распознавание %.2f)",
            job.who, job.t, job.end, text, done - started - job.end,
            job.detected - started - job.end, picked - job.detected, done - picked,
        )
        who = job.who if job.who in is_student else roster.teacher.id
        item = {"student_id": who, "t": job.t, "end": job.end, "text": text}
        if job.who in is_student and off(job.who):
            # аналитика выключена: реплика остаётся контекстом для правил, но не считается
            item["off"] = True
            board.forget(job.who)
        elif job.who in is_student:
            board.add_student(job.who)
            kind = classify(text, job.who, job.t, roster, context)
            item["kind"] = kind
            emit(Utterance(student_id=job.who, t=job.t, text=text, kind=kind, addressed=kind == "response"))
            if continues(job.who, job.t):
                board.extend(job.who, text, job.t, job.end, kind)
            else:
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
            except Exception:
                # одна плохая фраза не должна останавливать урок: иначе очередь
                # перестала бы разбираться, а /end ждал бы её вечно
                log.exception("%s, %.1f–%.1f с: фраза пропущена", job.who, job.t, job.end)
            finally:
                async with changed:
                    busy = False
                    changed.notify_all()

    async def watch_flags() -> None:
        # «давно не отвечал» появляется, когда ученик молчит, а не когда он говорит
        while True:
            await asyncio.sleep(FLAGS_EVERY_S)
            try:
                board.tick(lesson_time())
                show_scale(lesson_time())
            except Exception:
                log.exception("не удалось обновить флаги шкалы")

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
