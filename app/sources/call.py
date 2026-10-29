"""Звук урока из звонка. Владелец: Ярослав.

Сервер заходит в комнату LiveKit скрытым участником и получает звук
каждого участника отдельным потоком: 16 кГц, моно, кадры по 30 мс.
Участник, которого нет в составе класса, не засчитывается никому:
такие кадры отбрасывает конвейер, а не этот модуль.
"""

import asyncio
import logging
import time
from collections import Counter
from collections.abc import AsyncIterator

from livekit import rtc

from app.call import assistant_token
from app.config import settings

log = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
FRAME_MS = 30
STATS_EVERY_S = 5
# Сколько кадров ждёт конвейер: около секунды звука от 30 участников. Если
# распознавание не успевает, старые кадры сбрасываются, чтобы не росли
# память и задержка шкалы.
QUEUE_FRAMES = 1000
# Учитель закрыл MAX и не вернулся: столько секунд пустой комнаты — и урок кончился
EMPTY_ROOM_S = 300


async def listen(
    lesson_id: str, stop: asyncio.Event | None = None, empty_room_s: float = EMPTY_ROOM_S
) -> AsyncIterator[tuple[str, bytes]]:
    """Отдавать пары (id участника, PCM int16) по мере того, как люди говорят.

    Заканчивается, когда выставлен stop (учитель завершил урок) или комната
    пуста empty_room_s секунд. Тогда ассистент выходит из комнаты.

    Каждая дорожка читается своей задачей. Когда участник выходит, его задача
    заканчивается вместе с дорожкой, а при повторном входе приходит новая
    дорожка и новая задача, так что кадры идут снова. В лог раз в несколько
    секунд пишется, от кого сколько кадров пришло.
    """
    queue: asyncio.Queue[tuple[str, bytes] | None] = asyncio.Queue(maxsize=QUEUE_FRAMES)
    room = rtc.Room()
    pumps: dict[str, asyncio.Task] = {}  # sid дорожки -> задача чтения
    frames: Counter[str] = Counter()  # id участника -> кадров с прошлой записи в лог
    dropped: Counter[str] = Counter()  # id участника -> сброшенных кадров

    def put(identity: str, data: bytes) -> None:
        if queue.full():
            old = queue.get_nowait()
            if old is not None:
                dropped[old[0]] += 1
        queue.put_nowait((identity, data))

    async def pump(stream: rtc.AudioStream, identity: str) -> None:
        try:
            async for event in stream:
                frames[identity] += 1
                put(identity, bytes(event.frame.data))
        finally:
            await stream.aclose()

    async def report() -> None:
        last = time.monotonic()
        while True:
            await asyncio.sleep(STATS_EVERY_S)
            now = time.monotonic()
            if frames:
                parts = ", ".join(f"{who}: {n}" for who, n in sorted(frames.items()))
                log.info("кадры за %.1f с — %s", now - last, parts)
                frames.clear()
            if dropped:
                parts = ", ".join(f"{who}: {n}" for who, n in sorted(dropped.items()))
                log.warning("конвейер не успевает, сброшены старые кадры — %s", parts)
                dropped.clear()
            last = now

    @room.on("track_subscribed")
    def on_track(track: rtc.Track, publication: rtc.RemoteTrackPublication, participant: rtc.RemoteParticipant):
        if track.kind != rtc.TrackKind.KIND_AUDIO:
            return
        log.info("звук участника %s: дорожка %s", participant.identity, publication.sid)
        stream = rtc.AudioStream(track, sample_rate=SAMPLE_RATE, num_channels=1, frame_size_ms=FRAME_MS)
        old = pumps.pop(publication.sid, None)
        if old:
            old.cancel()
        pumps[publication.sid] = asyncio.create_task(pump(stream, participant.identity))

    @room.on("track_unsubscribed")
    def on_track_gone(track: rtc.Track, publication: rtc.RemoteTrackPublication, participant: rtc.RemoteParticipant):
        log.info("дорожка участника %s закончилась: %s", participant.identity, publication.sid)
        task = pumps.pop(publication.sid, None)
        if task:
            task.cancel()

    @room.on("participant_disconnected")
    def on_left(participant: rtc.RemoteParticipant):
        log.info("участник вышел: %s", participant.identity)

    stop = stop or asyncio.Event()

    async def watch_empty() -> None:
        empty_since = time.monotonic()
        while True:
            await asyncio.sleep(min(STATS_EVERY_S, empty_room_s / 4))
            if room.remote_participants:
                empty_since = time.monotonic()
            elif time.monotonic() - empty_since >= empty_room_s:
                log.info("комната %s пуста %.0f с, урок заканчивается", room.name, empty_room_s)
                stop.set()
                return

    async def wake_on_stop() -> None:
        await stop.wait()
        if queue.full():
            queue.get_nowait()
        queue.put_nowait(None)  # будит цикл ниже, даже если кадров нет

    await room.connect(settings.livekit_url, assistant_token(lesson_id))
    log.info("ассистент слушает комнату %s", room.name)
    helpers = [asyncio.create_task(job()) for job in (report, watch_empty, wake_on_stop)]
    try:
        while True:
            item = await queue.get()
            if item is None or stop.is_set():
                log.info("ассистент выходит из комнаты %s", room.name)
                return
            yield item
    finally:
        tasks = [*helpers, *pumps.values()]
        for task in tasks:
            task.cancel()
        # дождаться задач, чтобы aclose() потоков успел до disconnect()
        await asyncio.gather(*tasks, return_exceptions=True)
        await room.disconnect()
