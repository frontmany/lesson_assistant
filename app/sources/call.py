"""Звук урока из звонка. Владелец: Ярослав.

Сервер заходит в комнату LiveKit скрытым участником и получает звук
каждого участника отдельным потоком: 16 кГц, моно, кадры по 30 мс.
Участник, которого нет в составе класса, не засчитывается никому:
такие кадры отбрасывает конвейер, а не этот модуль.
"""

import asyncio
from collections.abc import AsyncIterator

from livekit import rtc

from app.call import assistant_token
from app.config import settings

SAMPLE_RATE = 16_000
FRAME_MS = 30


async def listen(lesson_id: str) -> AsyncIterator[tuple[str, bytes]]:
    """Отдавать пары (id участника, PCM int16) по мере того, как люди говорят.

    TODO(Ярослав): проверить на живом сервере LiveKit, что кадры идут
    от каждого участника и не теряются при входе и выходе людей.
    """
    queue: asyncio.Queue[tuple[str, bytes]] = asyncio.Queue()
    room = rtc.Room()
    pumps: set[asyncio.Task] = set()

    async def pump(stream: rtc.AudioStream, identity: str) -> None:
        async for event in stream:
            await queue.put((identity, bytes(event.frame.data)))

    @room.on("track_subscribed")
    def on_track(track: rtc.Track, publication: rtc.RemoteTrackPublication, participant: rtc.RemoteParticipant):
        if track.kind != rtc.TrackKind.KIND_AUDIO:
            return
        stream = rtc.AudioStream(track, sample_rate=SAMPLE_RATE, num_channels=1, frame_size_ms=FRAME_MS)
        task = asyncio.create_task(pump(stream, participant.identity))
        pumps.add(task)
        task.add_done_callback(pumps.discard)

    await room.connect(settings.livekit_url, assistant_token(lesson_id))
    try:
        while True:
            yield await queue.get()
    finally:
        for task in pumps:
            task.cancel()
        await room.disconnect()
