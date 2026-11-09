"""Своя пустая база в памяти на каждый тест"""

import dataclasses

import pytest
from sqlalchemy.pool import StaticPool

from app import auth, db
from app.api import routes
from app.config import settings
from app.ws import lesson as ws_lesson


@pytest.fixture(autouse=True)
def fresh_db():
    engine = db.create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    previous = db.engine
    db.use(engine)
    db.init(engine)
    yield
    db.use(previous)
    engine.dispose()


@pytest.fixture
def use_settings(monkeypatch):
    def apply(**changes):
        changed = dataclasses.replace(settings, **changes)
        for module in (auth, routes, ws_lesson):
            monkeypatch.setattr(module, "settings", changed)
        return changed
    return apply


@pytest.fixture(autouse=True)
def no_llm(monkeypatch):
    # тесты не ходят в GigaChat
    from app.pipeline import llm

    monkeypatch.setattr(llm, "settings", dataclasses.replace(settings, llm_api_key=""))


@pytest.fixture(autouse=True)
def dev_auth(use_settings):
    use_settings(dev_auth=True)
