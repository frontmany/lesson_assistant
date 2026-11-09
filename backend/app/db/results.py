"""Итоги уроков, реплики, ступени, норма и сводки"""

import json
import re
from datetime import datetime
from pathlib import Path

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, delete, select
from sqlalchemy.orm import Mapped, mapped_column

from app import db
from app.config import settings
from app.db import Base
from app.db.classes import Lesson, now
from app.models import StudentCard, Summary


class UtteranceRow(Base):
    __tablename__ = "utterances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lesson_id: Mapped[str] = mapped_column(ForeignKey("lessons.id"), index=True)
    participant_id: Mapped[str] = mapped_column(String(40))
    member_id: Mapped[int | None] = mapped_column(ForeignKey("members.id"), default=None, index=True)
    t: Mapped[float] = mapped_column(Float)
    end: Mapped[float] = mapped_column(Float)
    text: Mapped[str] = mapped_column(Text)
    kind: Mapped[str | None] = mapped_column(String(12), default=None)
    addressed: Mapped[bool] = mapped_column(Boolean, default=False)


class LevelRow(Base):
    __tablename__ = "levels"

    lesson_id: Mapped[str] = mapped_column(ForeignKey("lessons.id"), primary_key=True)
    participant_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    member_id: Mapped[int | None] = mapped_column(ForeignKey("members.id"), default=None, index=True)
    level: Mapped[int] = mapped_column(Integer)
    score: Mapped[float] = mapped_column(Float)
    flags: Mapped[str] = mapped_column(String(80), default="")


class LessonResult(Base):
    __tablename__ = "lesson_results"

    lesson_id: Mapped[str] = mapped_column(ForeignKey("lessons.id"), primary_key=True)
    activity: Mapped[float] = mapped_column(Float)
    norm: Mapped[float] = mapped_column(Float)
    quiet: Mapped[bool] = mapped_column(Boolean)
    counts_for_norm: Mapped[bool] = mapped_column(Boolean)
    finished_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class SummaryRow(Base):
    __tablename__ = "summaries"

    lesson_id: Mapped[str] = mapped_column(ForeignKey("lessons.id"), primary_key=True)
    payload: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class CardRow(Base):
    __tablename__ = "cards"

    lesson_id: Mapped[str] = mapped_column(ForeignKey("lessons.id"), primary_key=True)
    participant_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    payload: Mapped[str] = mapped_column(Text)


def member_id(participant_id: str) -> int | None:
    match = re.fullmatch(r"m(\d+)", participant_id)
    return int(match[1]) if match else None


def stored(lesson_id: str) -> bool:
    with db.session() as s:
        return s.get(Lesson, lesson_id) is not None


def save_lesson(
    lesson_id: str,
    utterances: list[dict],
    levels: dict[str, tuple[int, float, list[str]]],
    activity: float,
    norm: float,
    quiet: bool,
    virtual: bool,
) -> bool:
    with db.session() as s:
        lesson = s.get(Lesson, lesson_id)
        if lesson is None:
            return False
        for table in (UtteranceRow, LevelRow, LessonResult):
            s.execute(delete(table).where(table.lesson_id == lesson_id))
        s.add_all(
            UtteranceRow(
                lesson_id=lesson_id,
                participant_id=u["student_id"],
                member_id=member_id(u["student_id"]),
                t=u["t"],
                end=u["end"],
                text=u["text"],
                kind=u.get("kind"),
                addressed=u.get("kind") == "response",
            )
            for u in utterances
        )
        s.add_all(
            LevelRow(
                lesson_id=lesson_id,
                participant_id=pid,
                member_id=member_id(pid),
                level=level,
                score=score,
                flags=",".join(flags),
            )
            for pid, (level, score, flags) in levels.items()
        )
        s.add(
            LessonResult(
                lesson_id=lesson_id,
                activity=activity,
                norm=norm,
                quiet=quiet,
                counts_for_norm=not (quiet or virtual or lesson.demo or lesson.class_id is None),
            )
        )
        return True


def class_history(class_id: int) -> list[float]:
    with db.session() as s:
        rows = s.execute(
            select(LessonResult.activity)
            .join(Lesson, Lesson.id == LessonResult.lesson_id)
            .where(Lesson.class_id == class_id, LessonResult.counts_for_norm)
            .order_by(LessonResult.finished_at)
        )
        return [activity for (activity,) in rows]


def seed_history(path: Path | None = None) -> list[float]:
    path = path or settings.demo_lesson_dir / "history_seed.json"
    if not path.exists():
        return []
    lessons = json.loads(path.read_text(encoding="utf-8")).get("lessons", [])
    return [float(lesson["activity"]) for lesson in lessons if not lesson.get("quiet")]


def history_for(lesson_id: str) -> list[float]:
    from app.pipeline.scoring import MIN_LESSONS

    with db.session() as s:
        lesson = s.get(Lesson, lesson_id)
        class_id = lesson.class_id if lesson else None
    history = class_history(class_id) if class_id is not None else []
    return history if len(history) >= MIN_LESSONS else seed_history()


def silent_streaks(lesson_id: str, student_ids: list[str], lessons: int = 5) -> dict[str, int]:
    with db.session() as s:
        lesson = s.get(Lesson, lesson_id)
        class_id = lesson.class_id if lesson else None
        past: list[set[str] | None] = []
        present: list[set[str]] = []
        if class_id is not None:
            ids = s.scalars(
                select(LessonResult.lesson_id)
                .join(Lesson, Lesson.id == LessonResult.lesson_id)
                .where(Lesson.class_id == class_id, Lesson.id != lesson_id, LessonResult.counts_for_norm)
                .order_by(LessonResult.finished_at.desc())
                .limit(lessons)
            ).all()
            for past_id in ids:
                rows = s.scalars(select(LevelRow).where(LevelRow.lesson_id == past_id)).all()
                past.append({r.participant_id for r in rows if r.level >= 2})
                present.append({r.participant_id for r in rows})
    if class_id is None:
        path = settings.demo_lesson_dir / "history_seed.json"
        seeded = json.loads(path.read_text(encoding="utf-8")).get("lessons", []) if path.exists() else []
        for item in reversed(seeded[-lessons:]):
            if "replied" in item:
                past.append(set(item["replied"]))
                present.append(None)
    streaks: dict[str, int] = {}
    for sid in student_ids:
        key = sid.removeprefix("virtual-") if class_id is None else sid
        n = 0
        for replied, was_there in zip(past, present):
            if (was_there is not None and key not in was_there) or key in replied:
                break
            n += 1
        if past and (present[0] is None or key in present[0]):
            streaks[sid] = n
    return streaks


def save_summary(lesson_id: str, summary: Summary) -> None:
    with db.session() as s:
        row = s.get(SummaryRow, lesson_id)
        if row is None:
            s.add(SummaryRow(lesson_id=lesson_id, payload=summary.model_dump_json()))
        else:
            row.payload = summary.model_dump_json()


def get_summary(lesson_id: str) -> Summary | None:
    with db.session() as s:
        row = s.get(SummaryRow, lesson_id)
        return Summary.model_validate_json(row.payload) if row else None


def save_cards(lesson_id: str, cards: list[StudentCard]) -> None:
    with db.session() as s:
        s.execute(delete(CardRow).where(CardRow.lesson_id == lesson_id))
        s.add_all(CardRow(lesson_id=lesson_id, participant_id=c.student_id, payload=c.model_dump_json()) for c in cards)


def get_card(lesson_id: str, student_id: str) -> StudentCard | None:
    with db.session() as s:
        row = s.get(CardRow, (lesson_id, student_id))
        return StudentCard.model_validate_json(row.payload) if row else None


def lesson_utterances(lesson_id: str) -> list[UtteranceRow]:
    with db.session() as s:
        return list(s.scalars(select(UtteranceRow).where(UtteranceRow.lesson_id == lesson_id).order_by(UtteranceRow.t)))


def lesson_levels(lesson_id: str) -> dict[str, LevelRow]:
    with db.session() as s:
        return {row.participant_id: row for row in s.scalars(select(LevelRow).where(LevelRow.lesson_id == lesson_id))}
