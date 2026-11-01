"""Общее для тестов: у каждого теста своя пустая база SQLite в памяти и режим разработки."""

import dataclasses

import pytest
from sqlalchemy.pool import StaticPool

from app import auth, db
from app.api import routes
from app.config import settings
from app.ws import lesson as ws_lesson


@pytest.fixture(autouse=True)
def fresh_db():
    # StaticPool: одна база в памяти на все потоки, иначе у каждого соединения своя
    engine = db.create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    previous = db.engine
    db.use(engine)
    db.init(engine)
    yield
    db.use(previous)
    engine.dispose()


@pytest.fixture
def use_settings(monkeypatch):
    """Подменить настройки там, где их читают вход и API."""
    def apply(**changes):
        changed = dataclasses.replace(settings, **changes)
        for module in (auth, routes, ws_lesson):
            monkeypatch.setattr(module, "settings", changed)
        return changed
    return apply


@pytest.fixture(autouse=True)
def dev_auth(use_settings):
    # старые тесты API называют участника в адресе, как при разработке; тесты входа выключают это сами
    use_settings(dev_auth=True)
