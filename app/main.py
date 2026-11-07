"""Точка входа: API, WebSocket и раздача мини-приложения."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import db
from app.api.routes import router as api_router
from app.bot import max_bot
from app.config import ROOT, settings
from app.ws.lesson import router as ws_router

# uvicorn настраивает только свои логгеры, а журнал конвейера тоже нужен в docker compose logs
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
# каждый запрос к Hugging Face при загрузке модели
for name in ("httpx", "httpx2"):
    logging.getLogger(name).setLevel(logging.WARNING)

@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init()
    # бот живёт в том же процессе: делит с сервером базу и зовёт учеников на урок
    stop = max_bot.start_in_background()
    yield
    if stop is not None:
        stop.set()


app = FastAPI(title="Ассистент Сферум AI", lifespan=lifespan)


@app.get("/api/health")
def health() -> dict:
    # dev_auth: стартовая страница показывает ссылки для разработки, только когда вход без MAX открыт
    # bot — чат с ботом для кнопки «Открыть из чата с ботом» на экране урока в браузере
    from app.bot.max_bot import bot_link

    return {"status": "ok", "source_mode": settings.source_mode, "dev_auth": settings.dev_auth, "bot": bot_link()}


app.include_router(api_router, prefix="/api")
app.include_router(ws_router)

# Мини-приложение монтируется последним, чтобы не перекрывать /api и /ws
class Webapp(StaticFiles):
    """Файлы мини-приложения: браузер сверяет их с сервером при каждом открытии.

    Без этого MAX брал из кэша старый скрипт к новой странице, и она падала.
    Неизменённый файл заново не качается: на проверку сервер отвечает 304 по ETag.
    """

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


app.mount("/", Webapp(directory=ROOT / "webapp", html=True), name="webapp")
