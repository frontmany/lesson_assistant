"""Общее для тестов: у каждого теста своя пустая база SQLite в памяти."""

import pytest
from sqlalchemy.pool import StaticPool

from app import db


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
