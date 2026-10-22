"""События урока в реальном времени."""

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app import mocks

router = APIRouter()


@router.websocket("/ws/lessons/{lesson_id}")
async def lesson_events(ws: WebSocket, lesson_id: str):
    """Пока конвейера нет, проигрываем события из webapp/mock/events.json.

    Поле `after` в моке — пауза в секундах перед событием, клиенту оно не уходит.
    TODO(Ярослав): подписать клиента на настоящие события конвейера.
    """
    await ws.accept()
    try:
        for event in mocks.load("events.json"):
            await asyncio.sleep(event.pop("after", 1.0))
            await ws.send_json(event)
        await ws.close()
    except WebSocketDisconnect:
        pass
