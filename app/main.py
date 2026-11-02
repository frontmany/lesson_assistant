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
    return {"status": "ok", "source_mode": settings.source_mode}


app.include_router(api_router, prefix="/api")
app.include_router(ws_router)

# Мини-приложение монтируется последним, чтобы не перекрывать /api и /ws
app.mount("/", StaticFiles(directory=ROOT / "webapp", html=True), name="webapp")
