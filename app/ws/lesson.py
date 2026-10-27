"""События урока в реальном времени."""

import asyncio
import logging
from collections import defaultdict

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from app import mocks

log = logging.getLogger(__name__)

router = APIRouter()

# Сколько событий ждёт отправки одному клиенту. Клиент, который не успевает
# их забирать, теряет старые события, а не тормозит конвейер.
CLIENT_QUEUE = 200

_clients: dict[str, set[asyncio.Queue]] = defaultdict(set)
_live: set[str] = set()  # уроки, в которых работает конвейер


def set_live(lesson_id: str, live: bool) -> None:
    """Конвейер урока запущен или остановлен: пока он не запущен, клиенты получают мок."""
    if live:
        _live.add(lesson_id)
    else:
        _live.discard(lesson_id)


def publish(lesson_id: str, event: BaseModel) -> None:
    """Разослать событие конвейера всем, кто открыл урок."""
    data = event.model_dump()
    for queue in _clients.get(lesson_id, ()):
        if queue.full():
            queue.get_nowait()
            log.warning("клиент урока %s не успевает, старое событие сброшено", lesson_id)
        queue.put_nowait(data)


async def _play_mock(ws: WebSocket) -> None:
    """Проиграть события из webapp/mock/events.json.

    Поле `after` в моке — пауза в секундах перед событием, клиенту оно не уходит.
    """
    for event in mocks.load("events.json"):
        await asyncio.sleep(event.pop("after", 1.0))
        await ws.send_json(event)


async def _send_live(ws: WebSocket, queue: asyncio.Queue, mock: asyncio.Task | None) -> None:
    while True:
        event = await queue.get()
        if mock is not None:
            # урок начался, пока шёл мок: дальше только настоящие события
            mock.cancel()
            mock = None
        await ws.send_json(event)


async def _wait_disconnect(ws: WebSocket) -> None:
    while True:
        await ws.receive_text()


@router.websocket("/ws/lessons/{lesson_id}")
async def lesson_events(ws: WebSocket, lesson_id: str):
    """События конвейера урока. Пока урок не начат, проигрывается мок, чтобы экраны работали без звонка."""
    await ws.accept()
    queue: asyncio.Queue = asyncio.Queue(maxsize=CLIENT_QUEUE)
    _clients[lesson_id].add(queue)
    mock = None if lesson_id in _live else asyncio.create_task(_play_mock(ws))
    tasks = [asyncio.create_task(_send_live(ws, queue, mock)), asyncio.create_task(_wait_disconnect(ws))]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except WebSocketDisconnect:
        pass
    finally:
        _clients[lesson_id].discard(queue)
        for task in [*tasks, *([mock] if mock else [])]:
            task.cancel()
        await asyncio.gather(*tasks, *([mock] if mock else []), return_exceptions=True)
