"""Хранилище классов, уроков и итогов"""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


def _url(url: str) -> str:
    # драйвер psycopg 3
    return url.replace("postgresql://", "postgresql+psycopg://", 1)


def make_engine(url: str):
    args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(_url(url), connect_args=args, pool_pre_ping=True)


engine = make_engine(settings.database_url)
_sessions = sessionmaker(engine, expire_on_commit=False)


def init(bind=None) -> None:
    from app.db import classes, results  # noqa: F401

    Base.metadata.create_all(bind or engine)


def use(bind) -> None:
    global engine
    engine = bind
    _sessions.configure(bind=bind)


@contextmanager
def session() -> Iterator[Session]:
    with _sessions() as s, s.begin():
        yield s
