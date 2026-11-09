"""Классы, участники и уроки в базе"""

from datetime import timedelta

from app import db as database
from app.db import classes as db


def test_codes_are_six_chars_without_lookalikes():
    c = db.create_class(1, "Елена Викторовна", "10А", "история")
    assert len(c.invite_code) == 6
    assert not set(c.invite_code) & set("0O1I")


def test_lesson_roster_comes_from_class():
    c = db.create_class(1, "Елена Викторовна", "10А", "история")
    ivan, _ = db.join(c.id, 2, "Иван Иванов")
    lesson = db.lesson_for_class(c.id)
    roster = db.lesson_roster(lesson.id)
    assert (roster.class_name, roster.subject) == ("10А", "история")
    assert roster.teacher.id.startswith("m") and roster.teacher.max_user_id == 1
    assert [(s.id, s.name) for s in roster.students] == [(f"m{ivan.id}", "Иван И.")]


def test_left_student_is_not_in_new_lessons():
    c = db.create_class(1, "Елена Викторовна", "10А", "история")
    ivan, _ = db.join(c.id, 2, "Иван Иванов")
    db.remove_member(ivan.id)
    assert db.lesson_roster(db.lesson_for_class(c.id).id).students == []


def test_lesson_is_reused_until_it_ends():
    c = db.create_class(1, "Елена Викторовна", "10А", "история")
    first = db.lesson_for_class(c.id)
    assert db.lesson_for_class(c.id).id == first.id
    assert db.mark_live(first.id) and not db.mark_live(first.id)
    db.mark_ended(first.id)
    assert db.lesson_for_class(c.id).id != first.id


def test_class_cannot_be_restored_after_30_days():
    c = db.create_class(1, "Елена Викторовна", "10А", "история")
    db.delete_class(c.id)
    with database.session() as s:
        s.get(db.Class, c.id).deleted_at -= timedelta(days=31)
    assert not db.restore_class(c.id)


def test_unknown_lesson_has_no_roster():
    assert db.lesson_roster("nope") is None


def test_demo_class_lesson_still_comes_from_roster_json():
    assert db.lesson_roster(db.DEMO_LESSON_ID).students[0].id == "ivan_i"


def test_api_start_lesson_of_class_and_roster():
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    c = db.create_class(1, "Елена Викторовна", "10А", "история")
    db.join(c.id, 2, "Иван Иванов")
    lesson_id = client.post(f"/api/classes/{c.id}/lessons").json()["lesson_id"]
    assert client.post(f"/api/classes/{c.id}/lessons").json()["lesson_id"] == lesson_id
    roster = client.get(f"/api/lessons/{lesson_id}/roster").json()
    assert [s["name"] for s in roster["students"]] == ["Иван И."]
    assert "max_user_id" not in roster["teacher"]
    assert client.get("/api/lessons/nope/roster").status_code == 404
    db.delete_class(c.id)
    assert client.post(f"/api/classes/{c.id}/lessons").status_code == 404
