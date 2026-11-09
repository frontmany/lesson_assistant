"""Скрытый участник на живом LiveKit, без сервера тесты пропускаются"""

import asyncio
import math
import socket
from contextlib import asynccontextmanager
from urllib.parse import urlparse

import pytest

from app import mocks
from app.call import participant_token
from app.config import settings
from app.models import Roster
from app.sources.call import FRAME_MS, SAMPLE_RATE, listen

LESSON = "live-test"
FRAME_BYTES = SAMPLE_RATE * FRAME_MS // 1000 * 2


def _livekit_up() -> bool:
    url = urlparse(settings.livekit_url)
    try:
        socket.create_connection((url.hostname, url.port or 7880), timeout=1).close()
    except OSError:
        return False
    return True


pytestmark = pytest.mark.skipif(not _livekit_up(), reason="LiveKit не запущен")

rtc = pytest.importorskip("livekit.rtc")


@asynccontextmanager
async def speaker(identity: str):
    room = rtc.Room()
    await room.connect(settings.livekit_url, participant_token(LESSON, identity, identity))
    source = rtc.AudioSource(SAMPLE_RATE, 1)
    await room.local_participant.publish_track(
        rtc.LocalAudioTrack.create_audio_track("mic", source),
        rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE),
    )

    async def talk() -> None:
        samples = SAMPLE_RATE // 100
        t = 0
        while True:
            frame = rtc.AudioFrame.create(SAMPLE_RATE, 1, samples)
            data = frame.data.cast("h")
            for i in range(samples):
                data[i] = int(8000 * math.sin(2 * math.pi * 440 * (t + i) / SAMPLE_RATE))
            t += samples
            await source.capture_frame(frame)

    task = asyncio.create_task(talk())
    try:
        yield room
    finally:
        task.cancel()
        await room.disconnect()


async def collect(seconds: float) -> dict[str, int]:
    counts: dict[str, int] = {}

    async def run() -> None:
        async for who, pcm in listen(LESSON):
            assert len(pcm) == FRAME_BYTES
            counts[who] = counts.get(who, 0) + 1

    try:
        await asyncio.wait_for(run(), seconds)
    except asyncio.TimeoutError:
        pass
    return counts


def test_frames_from_each_speaker():
    async def scenario():
        async with speaker("artem_k"), speaker("teacher"):
            return await collect(4)

    counts = asyncio.run(scenario())
    assert counts.get("artem_k", 0) > 30
    assert counts.get("teacher", 0) > 30
    assert set(counts) == {"artem_k", "teacher"}


def test_frames_resume_after_rejoin():
    async def scenario():
        async with speaker("teacher"):
            listener = asyncio.create_task(collect(9))
            async with speaker("artem_k"):
                await asyncio.sleep(2)
            await asyncio.sleep(1)
            async with speaker("artem_k"):
                await asyncio.sleep(3)
            return await listener

    counts = asyncio.run(scenario())
    assert counts["artem_k"] > 100
    assert counts["teacher"] > 150


def test_people_do_not_see_assistant():
    async def scenario():
        async with speaker("artem_k") as room:
            listener = asyncio.create_task(collect(3))
            await asyncio.sleep(2)
            seen = set(room.remote_participants)
            await listener
            return seen

    assert asyncio.run(scenario()) == set()


def test_assistant_leaves_on_stop():
    async def scenario():
        stop = asyncio.Event()
        async with speaker("artem_k"):
            frames = 0

            async def run():
                nonlocal frames
                async for _ in listen(LESSON, stop):
                    frames += 1

            listener = asyncio.create_task(run())
            await asyncio.sleep(2)
            stop.set()
            await asyncio.wait_for(listener, 5)
            return frames

    assert asyncio.run(scenario()) > 30


def test_assistant_leaves_empty_room():
    async def scenario():
        started = asyncio.get_running_loop().time()
        async with speaker("artem_k"):
            listener = asyncio.create_task(collect_until_end(empty_room_s=2))
            await asyncio.sleep(1)
        await asyncio.wait_for(listener, 10)
        return asyncio.get_running_loop().time() - started

    assert asyncio.run(scenario()) < 8


async def collect_until_end(**kwargs) -> int:
    count = 0
    async for _ in listen(LESSON, **kwargs):
        count += 1
    return count


def test_virtual_class_plays_and_live_teacher_stays(tmp_path):
    from app.sources.virtual_class import play, virtual_id
    from tests.test_vad import vowel

    import wave

    for pid in ("teacher", "artem_k"):
        with wave.open(str(tmp_path / f"{pid}.wav"), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(SAMPLE_RATE)
            wav.writeframes((vowel(3) * 32767).astype("int16").tobytes())
    tracks = sorted(tmp_path.glob("*.wav"))
    roster = Roster.model_validate(mocks.load("roster.json"))

    async def scenario():
        counts: dict[str, int] = {}
        async with speaker("teacher") as teacher_room:

            async def run():
                async for who, _ in listen(LESSON):
                    counts[who] = counts.get(who, 0) + 1

            listener = asyncio.create_task(run())
            await asyncio.sleep(1)
            await play(LESSON, roster, tracks)
            await asyncio.sleep(0.5)
            still_there = teacher_room.connection_state == rtc.ConnectionState.CONN_CONNECTED
            listener.cancel()
            await asyncio.gather(listener, return_exceptions=True)
        return counts, still_there

    counts, still_there = asyncio.run(scenario())
    assert still_there
    assert counts.get(virtual_id("teacher"), 0) > 60
    assert counts.get(virtual_id("artem_k"), 0) > 60
    assert counts.get("teacher", 0) > 60


def test_two_lessons_do_not_hear_each_other():
    async def scenario():
        heard: dict[str, set[str]] = {"first": set(), "second": set()}

        async def listen_to(lesson_id, key):
            async for who, _ in listen(lesson_id):
                heard[key].add(who)

        async with speaker("artem_k"):
            listeners = [
                asyncio.create_task(listen_to(LESSON, "first")),
                asyncio.create_task(listen_to(LESSON + "-other", "second")),
            ]
            await asyncio.sleep(3)
            for task in listeners:
                task.cancel()
            await asyncio.gather(*listeners, return_exceptions=True)
        return heard

    heard = asyncio.run(scenario())
    assert heard == {"first": {"artem_k"}, "second": set()}
