"""События урока в реальном времени."""

import asyncio
import logging
from collections import defaultdict
from collections.abc import Coroutine

from fastapi import APIRouter, WebSocket
from pydantic import BaseModel

from app import mocks

log = logging.getLogger(__name__)

router = APIRouter()

# Сколько событий ждёт отправки одному клиенту. Клиент, который не успевает
# их забирать, теряет старые события, а не тормозит конвейер.
CLIENT_QUEUE = 200

_clients: dict[str, set[asyncio.Queue]] = defaultdict(set)
_mocks: dict[str, set[asyncio.Task]] = defaultdict(set)  # моки у клиентов, пока урок не начат
_live: set[str] = set()  # уроки, в которых работает конвейер
# Снимок идущего урока: последнее событие каждого типа по каждому ученику.
# Его получает клиент при подключении, иначе после перезагрузки шкалы пустые
_snapshots: dict[str, dict[tuple[str, str], dict]] = {}
SNAPSHOT_TYPES = {"scale", "utterance"}


def set_live(lesson_id: str, live: bool) -> None:
    """Конвейер урока запущен или остановлен: пока он не запущен, клиенты получают мок."""
    if live:
        _live.add(lesson_id)
        _snapshots[lesson_id] = {}
        # модели загружаются долго, и мок не должен выдавать себя за начавшийся урок
        for mock in _mocks.get(lesson_id, ()):
            mock.cancel()
    else:
        _live.discard(lesson_id)
        _snapshots.pop(lesson_id, None)


def _put(lesson_id: str, queue: asyncio.Queue, data: dict) -> None:
    if queue.full():
        queue.get_nowait()
        log.warning("клиент урока %s не успевает, старое событие сброшено", lesson_id)
    queue.put_nowait(data)


def publish(lesson_id: str, event: BaseModel) -> None:
    """Разослать событие конвейера всем, кто открыл урок, и запомнить его для снимка."""
    data = event.model_dump()
    if lesson_id in _snapshots and data["type"] in SNAPSHOT_TYPES:
        _snapshots[lesson_id][(data["type"], data["student_id"])] = data
    for queue in _clients.get(lesson_id, ()):
        _put(lesson_id, queue, data)


async def _play_mock(ws: WebSocket) -> None:
    """Проиграть события из webapp/mock/events.json.

    Поле `after` в моке — пауза в секундах перед событием, клиенту оно не уходит.
    """
    for event in mocks.load("events.json"):
        await asyncio.sleep(event.pop("after", 1.0))
        await ws.send_json(event)


async def _send_live(ws: WebSocket, queue: asyncio.Queue) -> None:
    while True:
        await ws.send_json(await queue.get())


async def _wait_disconnect(ws: WebSocket) -> None:
    while True:
        await ws.receive_text()


def _ignore_result(task: asyncio.Task) -> None:
    # клиент ушёл: WebSocketDisconnect и ошибка отправки здесь обычное дело
    if not task.cancelled():
        task.exception()


def _background(job: Coroutine) -> asyncio.Task:
    task = asyncio.create_task(job)
    task.add_done_callback(_ignore_result)
    return task


async def _stop(tasks: list[asyncio.Task]) -> None:
    """Отменить задачи и дождаться их.

    Через asyncio.wait, а не gather: если сервер отменит обработчик в этот
    момент, gather поднимет новый CancelledError без метки отмены anyio,
    и обработчик закончится отменённым вместо обычного выхода.
    """
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.wait(tasks)


@router.websocket("/ws/lessons/{lesson_id}")
async def lesson_events(ws: WebSocket, lesson_id: str):
    """События урока. Идёт урок — снимок и новые события; не начат — мок, чтобы экраны работали без звонка."""
    await ws.accept()
    queue: asyncio.Queue = asyncio.Queue(maxsize=CLIENT_QUEUE)
    _clients[lesson_id].add(queue)
    # очередь уже подписана, а ожиданий до этого места нет: снимок и новые события не разойдутся
    for event in _snapshots.get(lesson_id, {}).values():
        _put(lesson_id, queue, event)
    mock = None if lesson_id in _live else _background(_play_mock(ws))
    if mock:
        _mocks[lesson_id].add(mock)
    tasks = [_background(_send_live(ws, queue)), _background(_wait_disconnect(ws))]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        _clients[lesson_id].discard(queue)
        if mock:
            _mocks[lesson_id].discard(mock)
        await _stop([*tasks, *([mock] if mock else [])])
