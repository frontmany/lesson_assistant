"""Точка входа сервера"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import db
from app.api.routes import router as api_router
from app.bot import max_bot
from app.config import WEBAPP, settings
from app.ws.lesson import router as ws_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
for name in ("httpx", "httpx2"):
    logging.getLogger(name).setLevel(logging.WARNING)

@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init()
    stop = max_bot.start_in_background()
    yield
    if stop is not None:
        stop.set()


app = FastAPI(title="Ассистент Сферум AI", lifespan=lifespan)


@app.get("/api/health")
def health() -> dict:
    from app.bot.max_bot import bot_link

    return {"status": "ok", "source_mode": settings.source_mode, "dev_auth": settings.dev_auth, "bot": bot_link()}


app.include_router(api_router, prefix="/api")
app.include_router(ws_router)

# Приложение монтируется последним, чтобы не перекрыть api и ws
class Webapp(StaticFiles):
    """Браузер сверяет файлы с сервером при каждом открытии"""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


app.mount("/", Webapp(directory=WEBAPP, html=True), name="webapp")
