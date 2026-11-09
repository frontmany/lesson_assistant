"""Чтение моков приложения"""

import json

from app.config import WEBAPP

MOCK_DIR = WEBAPP / "mock"


def load(name: str):
    return json.loads((MOCK_DIR / name).read_text(encoding="utf-8"))
