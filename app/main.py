"""Точка входа: API, WebSocket и раздача мини-приложения."""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes import router as api_router
from app.config import ROOT, settings
from app.ws.lesson import router as ws_router

app = FastAPI(title="Ассистент Сферум AI")


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "source_mode": settings.source_mode}


app.include_router(api_router, prefix="/api")
app.include_router(ws_router)

# Мини-приложение монтируется последним, чтобы не перекрывать /api и /ws
app.mount("/", StaticFiles(directory=ROOT / "webapp", html=True), name="webapp")
