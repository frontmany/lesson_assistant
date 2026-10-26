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


async def listen(lesson_id: str) -> AsyncIterator[tuple[str, bytes]]:
    """Отдавать пары (id участника, PCM int16) по мере того, как люди говорят.

    Каждая дорожка читается своей задачей. Когда участник выходит, его задача
    заканчивается вместе с дорожкой, а при повторном входе приходит новая
    дорожка и новая задача, так что кадры идут снова. В лог раз в несколько
    секунд пишется, от кого сколько кадров пришло.
    """
    queue: asyncio.Queue[tuple[str, bytes]] = asyncio.Queue()
    room = rtc.Room()
    pumps: dict[str, asyncio.Task] = {}  # sid дорожки -> задача чтения
    frames: Counter[str] = Counter()  # id участника -> кадров с прошлой записи в лог

    async def pump(stream: rtc.AudioStream, identity: str) -> None:
        try:
            async for event in stream:
                frames[identity] += 1
                await queue.put((identity, bytes(event.frame.data)))
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

    await room.connect(settings.livekit_url, assistant_token(lesson_id))
    reporter = asyncio.create_task(report())
    try:
        while True:
            yield await queue.get()
    finally:
        reporter.cancel()
        for task in pumps.values():
            task.cancel()
        await room.disconnect()
