"""Звонок урока на LiveKit, одна комната на урок"""

import logging

from livekit import api

from app.config import settings

log = logging.getLogger(__name__)

ASSISTANT_IDENTITY = "assistant"


def room_name(lesson_id: str) -> str:
    return f"lesson-{lesson_id}"


def _token(identity: str, name: str, grants: api.VideoGrants) -> str:
    return (
        api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_identity(identity)
        .with_name(name)
        .with_grants(grants)
        .to_jwt()
    )


# Показ экрана только у учителя
STUDENT_SOURCES = ["microphone", "camera"]
TEACHER_SOURCES = [*STUDENT_SOURCES, "screen_share", "screen_share_audio"]


def participant_token(lesson_id: str, identity: str, name: str, teacher: bool = False) -> str:
    grants = api.VideoGrants(
        room_join=True,
        room=room_name(lesson_id),
        can_publish=True,
        can_subscribe=True,
        can_publish_sources=TEACHER_SOURCES if teacher else STUDENT_SOURCES,
    )
    return _token(identity, name, grants)


def assistant_token(lesson_id: str) -> str:
    grants = api.VideoGrants(
        room_join=True,
        room=room_name(lesson_id),
        hidden=True,
        can_publish=False,
        can_subscribe=True,
    )
    return _token(ASSISTANT_IDENTITY, "Ассистент", grants)


async def close_room(lesson_id: str) -> bool:
    url = settings.livekit_url.replace("ws://", "http://", 1).replace("wss://", "https://", 1)
    try:
        async with api.LiveKitAPI(url, settings.livekit_api_key, settings.livekit_api_secret) as lk:
            await lk.room.delete_room(api.DeleteRoomRequest(room=room_name(lesson_id)))
    except Exception as exc:
        log.warning("комната урока %s не закрыта: %s", lesson_id, exc)
        return False
    return True


def bot_token(lesson_id: str, identity: str, name: str) -> str:
    grants = api.VideoGrants(
        room_join=True,
        room=room_name(lesson_id),
        can_publish=True,
        can_subscribe=False,
    )
    return _token(identity, name, grants)
