"""Чтение моков из webapp/mock/. Пока нет конвейера, сервер отдаёт их."""

import json

from app.config import ROOT

MOCK_DIR = ROOT / "webapp" / "mock"


def load(name: str):
    return json.loads((MOCK_DIR / name).read_text(encoding="utf-8"))
