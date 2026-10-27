"""Точка входа: API, WebSocket и раздача мини-приложения."""

import logging

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes import router as api_router
from app.config import ROOT, settings
from app.ws.lesson import router as ws_router

# uvicorn настраивает только свои логгеры, а журнал конвейера тоже нужен в docker compose logs
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
# каждый запрос к Hugging Face при загрузке модели
for name in ("httpx", "httpx2"):
    logging.getLogger(name).setLevel(logging.WARNING)

app = FastAPI(title="Ассистент Сферум AI")


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "source_mode": settings.source_mode}


app.include_router(api_router, prefix="/api")
app.include_router(ws_router)

# Мини-приложение монтируется последним, чтобы не перекрывать /api и /ws
app.mount("/", StaticFiles(directory=ROOT / "webapp", html=True), name="webapp")
