"""Звонок урока на LiveKit: комнаты и токены входа.

Один урок = одна комната. identity участника = его id из состава класса,
поэтому сервер всегда знает, чей это звук.
"""

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


def participant_token(lesson_id: str, identity: str, name: str) -> str:
    """Токен учителя или ученика: говорит и слышит остальных."""
    grants = api.VideoGrants(
        room_join=True,
        room=room_name(lesson_id),
        can_publish=True,
        can_subscribe=True,
    )
    return _token(identity, name, grants)


def assistant_token(lesson_id: str) -> str:
    """Токен ассистента: скрытый участник, только слушает."""
    grants = api.VideoGrants(
        room_join=True,
        room=room_name(lesson_id),
        hidden=True,
        can_publish=False,
        can_subscribe=True,
    )
    return _token(ASSISTANT_IDENTITY, "Ассистент", grants)


async def close_room(lesson_id: str) -> bool:
    """Закрыть комнату урока: учеников выводит из звонка, их экран показывает «Урок завершён».

    False — LiveKit не ответил: урок на сервере всё равно завершён, ученики выйдут сами.
    """
    url = settings.livekit_url.replace("ws://", "http://", 1).replace("wss://", "https://", 1)
    try:
        async with api.LiveKitAPI(url, settings.livekit_api_key, settings.livekit_api_secret) as lk:
            await lk.room.delete_room(api.DeleteRoomRequest(room=room_name(lesson_id)))
    except Exception as exc:  # сеть, LiveKit недоступен или комнаты уже нет
        log.warning("комната урока %s не закрыта: %s", lesson_id, exc)
        return False
    return True


def bot_token(lesson_id: str, identity: str, name: str) -> str:
    """Токен участника виртуального класса: только говорит записанной дорожкой."""
    grants = api.VideoGrants(
        room_join=True,
        room=room_name(lesson_id),
        can_publish=True,
        can_subscribe=False,
    )
    return _token(identity, name, grants)
