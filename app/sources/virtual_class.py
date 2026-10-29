"""Виртуальный класс: записанный урок заходит в звонок ботами. Владелец: Ярослав.

Две роли. Инструмент разработки и замеров: каждая дорожка заходит в комнату
LiveKit отдельным участником и «говорит» свою запись, для ассистента это
неотличимо от живых людей, поэтому так проверяется весь путь: звонок →
скрытый участник → конвейер. И демо: учитель или жюри показывают урок
в одиночку, а записанный класс заходит вместо учеников (#45).

Идея взята из медиабота Callifornia: синтетические участники звонка.

Одна дорожка = один участник. Имя файла = id из roster.json
(teacher.wav, ivan_i.wav, ...). Формат: WAV, 16 кГц, моно, 16 бит.
Все дорожки одинаковой длины и начинаются в одну секунду.

identity бота — «virtual-» + id: живой учитель с id «teacher» сидит в той же
комнате, а LiveKit выкидывает второго участника с тем же identity.

Запуск: python -m app.cli virtual-class data/demo_lesson --lesson <id>
"""

import asyncio
import logging
import wave
from pathlib import Path

import numpy as np
from livekit import rtc

from app.call import bot_token
from app.config import settings
from app.models import Roster, Student

log = logging.getLogger(__name__)

PREFIX = "virtual-"
SAMPLE_RATE = 16_000
FRAME = SAMPLE_RATE * 30 // 1000  # кадр 30 мс


def virtual_id(participant_id: str) -> str:
    return PREFIX + participant_id


def lesson_roster(roster: Roster, demo: Roster) -> Roster:
    """Состав урока с виртуальным классом: живой учитель и ученики из записи.

    Учеников класса в составе нет: в демо они не нужны, а в панели учителя
    получилось бы 12 человек. Без этого конвейер отбросил бы ботов как
    посторонних. Учитель из записи идёт отдельно: для конвейера он учитель,
    а не ученик. TODO(#45): живые ученики класса и виртуальные вместе, когда
    состав урока берётся из базы.
    """
    virtual = [Student(id=virtual_id(s.id), name=s.name, virtual=True) for s in demo.students]
    teacher = Student(id=virtual_id(demo.teacher.id), name=demo.teacher.name, virtual=True)
    return roster.model_copy(update={"students": virtual, "virtual_teacher": teacher})


def load_tracks(lesson_dir: Path) -> tuple[Roster, list[Path]]:
    """Состав записанного урока и его дорожки.

    Дорожки лежат в Git LFS: без `git lfs pull` вместо них указатели по сотне
    байт, и проигрывать нечего — тогда ошибка с подсказкой.
    """
    roster = Roster.model_validate_json((lesson_dir / "roster.json").read_text(encoding="utf-8"))
    tracks = sorted((lesson_dir / "tracks").glob("*.wav"))
    pointers = [t.name for t in tracks if t.read_bytes()[:7] == b"version"]
    if not tracks or pointers:
        raise FileNotFoundError(
            f"нет дорожек в {lesson_dir / 'tracks'}"
            + (f": {', '.join(pointers)} — указатели Git LFS, выполните git lfs pull" if pointers else "")
        )
    return roster, tracks


def read_track(path: Path) -> np.ndarray:
    with wave.open(str(path)) as wav:
        if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) != (SAMPLE_RATE, 1, 2):
            raise ValueError(f"{path.name}: нужен WAV 16 кГц, моно, 16 бит")
        return np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16)


async def play(lesson_id: str, roster: Roster, tracks: list[Path], stop: asyncio.Event | None = None) -> None:
    """Завести в комнату урока по боту на дорожку и проиграть запись в реальном времени.

    Боты сначала все входят в комнату, потом одновременно начинают говорить,
    чтобы реплики шли с теми же паузами, что в записи. Заканчивается, когда
    кончилась запись или выставлен stop; боты выходят из комнаты.
    """
    stop = stop or asyncio.Event()
    names = {p.id: p.name for p in [roster.teacher, *roster.students]}
    everyone_in = asyncio.Barrier(len(tracks))

    async def bot(track: Path) -> None:
        pid = track.stem
        pcm = read_track(track)
        room = rtc.Room()
        token = bot_token(lesson_id, virtual_id(pid), names.get(pid, pid))
        await room.connect(settings.livekit_url, token, rtc.RoomOptions(auto_subscribe=False))
        try:
            source = rtc.AudioSource(SAMPLE_RATE, 1)
            track_ = rtc.LocalAudioTrack.create_audio_track("mic", source)
            options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
            await room.local_participant.publish_track(track_, options)
            await everyone_in.wait()
            for i in range(0, len(pcm), FRAME):
                if stop.is_set():
                    break
                chunk = pcm[i : i + FRAME]
                if len(chunk) < FRAME:
                    chunk = np.pad(chunk, (0, FRAME - len(chunk)))
                # capture_frame ждёт, когда очередь источника заполнена: так звук идёт в реальном времени
                await source.capture_frame(rtc.AudioFrame(chunk.tobytes(), SAMPLE_RATE, 1, FRAME))
            if not stop.is_set():
                await source.wait_for_playout()
        finally:
            await room.disconnect()

    log.info("виртуальный класс заходит в урок %s: %s", lesson_id, ", ".join(t.stem for t in tracks))
    bots = [asyncio.create_task(bot(t)) for t in tracks]
    try:
        await asyncio.gather(*bots)
    finally:
        for task in bots:
            task.cancel()
        await asyncio.gather(*bots, return_exceptions=True)
        log.info("виртуальный класс вышел из урока %s", lesson_id)
