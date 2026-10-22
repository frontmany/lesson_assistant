"""Моки в webapp/mock/ должны соответствовать моделям из app/models.py."""

import json

from pydantic import TypeAdapter

from app.config import ROOT
from app.mocks import load
from app.models import LEVEL_LABELS, LessonEvent, Roster, StudentCard, Summary


def test_roster_mock_matches_demo_lesson():
    mock = Roster.model_validate(load("roster.json"))
    demo = Roster.model_validate_json((ROOT / "data/demo_lesson/roster.json").read_text(encoding="utf-8"))
    assert mock == demo


def test_events_mock():
    roster = Roster.model_validate(load("roster.json"))
    ids = {s.id for s in roster.students}
    adapter = TypeAdapter(LessonEvent)
    for raw in load("events.json"):
        raw.pop("after", None)
        event = adapter.validate_python(raw)
        if event.student_id is not None:
            assert event.student_id in ids
        if event.type == "scale":
            assert event.label == LEVEL_LABELS[event.level]


def test_scale_never_goes_down():
    levels: dict[str, int] = {}
    for raw in load("events.json"):
        if raw["type"] != "scale":
            continue
        assert raw["level"] >= levels.get(raw["student_id"], 1)
        levels[raw["student_id"]] = raw["level"]


def test_summary_and_cards_mock():
    Summary.model_validate(load("summary.json"))
    for card in load("cards.json"):
        StudentCard.model_validate(card)


def test_mocks_are_utf8_json():
    for path in (ROOT / "webapp/mock").glob("*.json"):
        json.loads(path.read_text(encoding="utf-8"))
