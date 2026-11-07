"""Хранилище: классы, участники и уроки — classes.py, реплики, ступени и сводки — results.py.

Аудио на диск не пишется. По умолчанию SQLite (DATABASE_URL), в compose —
PostgreSQL. Таблицы создаются при старте сервера (init), без миграций:
схема описана в моделях SQLAlchemy.

Итоги уроков — реплики, ступени, норма, сводки — в results.py.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


def _url(url: str) -> str:
    # в .env адрес PostgreSQL обычный, а драйвер у нас psycopg 3
    return url.replace("postgresql://", "postgresql+psycopg://", 1)


def make_engine(url: str):
    # SQLite: бот работает в своём потоке, соединение делят потоки сервера
    args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(_url(url), connect_args=args, pool_pre_ping=True)


engine = make_engine(settings.database_url)
_sessions = sessionmaker(engine, expire_on_commit=False)


def init(bind=None) -> None:
    """Создать таблицы, которых ещё нет."""
    from app.db import classes, results  # noqa: F401 — модели регистрируются в Base

    Base.metadata.create_all(bind or engine)


def use(bind) -> None:
    """Подменить базу: тесты работают на своей SQLite в памяти."""
    global engine
    engine = bind
    _sessions.configure(bind=bind)


@contextmanager
def session() -> Iterator[Session]:
    """Сессия на одну операцию: при выходе изменения сохраняются, при ошибке откатываются."""
    with _sessions() as s, s.begin():
        yield s
