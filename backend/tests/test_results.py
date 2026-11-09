"""Итоги уроков в базе"""

import asyncio
import dataclasses
import json

import numpy as np
import pytest

from app.db import classes, results
from app.models import AskNext, Summary
from app.pipeline.scoring import DEFAULT_NORM


def lesson_of_class(students=("Иван Иванов", "Артём Кузнецов")):
    c = classes.create_class(1, "Елена Викторовна", "10А", "история")
    members = [classes.join(c.id, 10 + i, name)[0] for i, name in enumerate(students)]
    lesson = classes.lesson_for_class(c.id)
    return c, lesson, members


def finish(lesson_id, activity, quiet=False, virtual=False):
    assert results.save_lesson(lesson_id, [], {}, activity, DEFAULT_NORM, quiet, virtual)
    classes.mark_ended(lesson_id)


def test_lesson_is_saved_with_members_and_kinds():
    c, lesson, (ivan, artem) = lesson_of_class()
    teacher = classes.teacher_of(c.id)
    utterances = [
        {"student_id": f"m{teacher.id}", "t": 0.0, "end": 4.0, "text": "Артём, что Пётр сделал с флотом?"},
        {"student_id": f"m{artem.id}", "t": 5.0, "end": 7.0, "text": "Флот строили.", "kind": "response"},
    ]
    levels = {f"m{artem.id}": (2, 2.0, []), f"m{ivan.id}": (1, 0.0, ["long_silence"])}
    assert results.save_lesson(lesson.id, utterances, levels, 1.0, 6.0, False, False)

    rows = results.lesson_utterances(lesson.id)
    assert [(r.member_id, r.kind, r.addressed) for r in rows] == [(teacher.id, None, False), (artem.id, "response", True)]
    saved = results.lesson_levels(lesson.id)
    assert (saved[f"m{artem.id}"].member_id, saved[f"m{artem.id}"].level) == (artem.id, 2)
    assert saved[f"m{ivan.id}"].flags == "long_silence"


def test_saving_again_replaces_previous_result():
    _, lesson, (ivan, _) = lesson_of_class()
    one = [{"student_id": f"m{ivan.id}", "t": 1.0, "end": 2.0, "text": "Раз.", "kind": "other"}]
    results.save_lesson(lesson.id, one * 3, {}, 1.0, 6.0, False, False)
    results.save_lesson(lesson.id, one, {}, 1.0, 6.0, False, False)
    assert len(results.lesson_utterances(lesson.id)) == 1


def test_lesson_outside_db_is_not_saved():
    assert not results.save_lesson(classes.DEMO_LESSON_ID, [], {}, 1.0, 6.0, False, False)


def test_demo_and_record_participants_have_no_member():
    assert results.member_id("m17") == 17
    assert results.member_id("ivan_i") is None
    assert results.member_id("virtual-ivan_i") is None


def test_new_class_takes_norm_from_seed(monkeypatch, tmp_path):
    (tmp_path / "history_seed.json").write_text(json.dumps({"lessons": [
        {"date": "2026-10-01", "activity": 5},
        {"date": "2026-10-03", "activity": 1, "quiet": True},
        {"date": "2026-10-08", "activity": 7},
    ]}), encoding="utf-8")
    monkeypatch.setattr(results, "settings", dataclasses.replace(results.settings, demo_lesson_dir=tmp_path))
    _, lesson, _ = lesson_of_class()
    assert results.history_for(lesson.id) == [5, 7]


def test_class_history_skips_quiet_virtual_demo_and_other_classes():
    c, first, _ = lesson_of_class()
    finish(first.id, 4)
    quiet = classes.lesson_for_class(c.id)
    finish(quiet.id, 0.5, quiet=True)
    virtual = classes.lesson_for_class(c.id)
    finish(virtual.id, 9, virtual=True)
    second = classes.lesson_for_class(c.id)
    finish(second.id, 6)
    demo = classes.demo_lesson(1, "Елена Викторовна")
    finish(demo.id, 10)
    _, other, _ = lesson_of_class()
    finish(other.id, 100)

    now = classes.lesson_for_class(c.id)
    assert results.history_for(now.id) == [4, 6]


def test_no_seed_means_default_norm(monkeypatch, tmp_path):
    monkeypatch.setattr(results, "settings", dataclasses.replace(results.settings, demo_lesson_dir=tmp_path))
    assert results.seed_history() == []


def test_summary_is_stored():
    _, lesson, _ = lesson_of_class()
    summary = Summary(
        lesson_id=lesson.id, carried=["m2"], dropped=[], weak_topics=["Табель о рангах"],
        ask_next=[AskNext(student_id="m3", topic="Реформы", question="Зачем Петру флот?")],
    )
    results.save_summary(lesson.id, summary)
    assert results.get_summary(lesson.id) == summary
    assert results.get_summary("нет") is None


def test_pipeline_saves_lesson_with_class_norm(monkeypatch):
    pytest.importorskip("faster_whisper")
    from app.pipeline import asr, runner
    from app.sources import call
    from tests.test_runner import FRAME, as_int16, silence, vowel

    c, past, _ = lesson_of_class()
    finish(past.id, 3)
    second = classes.lesson_for_class(c.id)
    finish(second.id, 5)
    lesson = classes.lesson_for_class(c.id)
    roster = classes.lesson_roster(lesson.id)
    teacher_id = roster.teacher.id
    artem_id = next(s.id for s in roster.students if s.name.startswith("Артём"))
    ivan_id = next(s.id for s in roster.students if s.name.startswith("Иван"))

    question = as_int16(np.concatenate([silence(0.3), vowel(1.5), silence(6)]))
    answer = as_int16(np.concatenate([silence(2.5), vowel(2), silence(3.3)]))
    norms = []

    async def fake_listen(lesson_id, stop, **kwargs):
        for i in range(0, len(question), FRAME):
            yield teacher_id, question[i : i + FRAME].tobytes()
            yield artem_id, answer[i : i + FRAME].tobytes()
        await stop.wait()

    def by_length(pcm, *args):
        return "Артём, что Пётр сделал с флотом?" if len(pcm) < 2.2 * 16000 else "Ну, там флот строили."

    real_board = runner.transcribe_stream

    async def spy(*args, board=None, **kwargs):
        norms.append(board.base_norm)
        return await real_board(*args, board=board, **kwargs)

    monkeypatch.setattr(call, "listen", fake_listen)
    monkeypatch.setattr(asr, "model", lambda *a: None)
    monkeypatch.setattr(asr, "transcribe", by_length)
    monkeypatch.setattr(runner, "transcribe_stream", spy)

    async def scenario():
        assert runner.start(lesson.id, roster)
        await asyncio.sleep(1)
        assert await runner.end(lesson.id)

    asyncio.run(scenario())
    assert norms == [4.0]
    rows = results.lesson_utterances(lesson.id)
    assert [(r.participant_id, r.kind) for r in rows] == [(teacher_id, None), (artem_id, "response")]
    levels = results.lesson_levels(lesson.id)
    assert levels[artem_id].level == 2 and levels[ivan_id].level == 1
    assert results.class_history(c.id) == [3, 5]


def test_second_lesson_of_new_class_keeps_seed_norm(monkeypatch, tmp_path):
    (tmp_path / "history_seed.json").write_text(json.dumps({"lessons": [
        {"date": "2026-10-01", "activity": 4.5}, {"date": "2026-10-08", "activity": 4.5},
    ]}), encoding="utf-8")
    monkeypatch.setattr(results, "settings", dataclasses.replace(results.settings, demo_lesson_dir=tmp_path))
    c, first, _ = lesson_of_class()
    finish(first.id, 8)
    second = classes.lesson_for_class(c.id)
    assert results.history_for(second.id) == [4.5, 4.5]
    finish(second.id, 6)
    third = classes.lesson_for_class(c.id)
    assert results.history_for(third.id) == [8, 6]
