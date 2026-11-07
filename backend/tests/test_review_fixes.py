"""Замечания облачного ревью: конец урока, «Выйти», уведомления, разметка сводки, ответы GigaChat, правила шкалы."""

import asyncio
import threading

import httpx
import pytest

from app import call
from app.bot import max_bot
from app.db import classes as db
from app.models import Roster, Student, Summary
from app.pipeline import llm, runner, summary
from app.pipeline.classify import classify
from app.pipeline.scoring import Scoreboard
from tests.test_auth import MARIA, client, production, sign_in  # noqa: F401 — production: вход как в MAX
from tests.test_bot import FakeApi
from tests.test_classify import ROSTER, said


# ---------- 1. конец урока не ждёт сводку ----------


def test_lesson_ends_before_summary_is_sent(monkeypatch):
    """Сводка (минуты в GigaChat) — отдельная задача: задача урока, которую ждут /end и mark_ended, её не ждёт."""
    c = db.create_class(1, "Елена Викторовна", "10А", "история")
    db.join(c.id, 10, "Иван Иванов")
    lesson = db.lesson_for_class(c.id)
    roster = db.lesson_roster(lesson.id)
    sid = roster.students[0].id
    board = Scoreboard([sid], 6.0, 2400)
    release = threading.Event()
    sent = []
    monkeypatch.setattr(max_bot, "lesson_finished", lambda *a: (release.wait(5), sent.append(a)))

    async def go():
        task = await runner.finish_lesson(lesson.id, roster, [], board)
        assert task is not None and not task.done() and not sent  # урок закончен, сводка ещё идёт
        release.set()
        await task
        assert sent

    asyncio.run(go())


# ---------- 2. упавший конвейер урок не заканчивает ----------


def test_crashed_pipeline_does_not_end_lesson(monkeypatch):
    ended = []

    async def go(fail: bool):
        async def work():
            if fail:
                raise RuntimeError("LiveKit оборвался")

        task = asyncio.create_task(work())
        monkeypatch.setitem(runner._lessons, "L", type("L", (), {"task": task})())
        runner.when_finished("L", ended.append)
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)

    asyncio.run(go(fail=True))
    assert ended == []  # учитель войдёт снова, и урок начнётся заново
    asyncio.run(go(fail=False))
    assert ended == ["L"]


# ---------- 3–4. «Выйти» закрывает звонок и не держит цикл событий ----------


def test_exit_closes_call_for_students(monkeypatch):
    c = db.create_class(MARIA, "Мария Смирнова", "10А", "история")
    lesson_id = db.lesson_for_class(c.id).id
    db.mark_live(lesson_id)
    closed = []

    async def close_room(lid):
        closed.append(lid)
        return True

    monkeypatch.setattr(call, "close_room", close_room)
    monkeypatch.setattr(max_bot, "role_again", lambda uid: threading.current_thread() is threading.main_thread()
                        and pytest.fail("запрос к MAX — в цикле событий"))
    assert client.post("/api/me/exit", headers=sign_in(MARIA)).json()["ended"] == [lesson_id]
    assert closed == [lesson_id]


# ---------- 5–6. уведомления ученикам ----------


class Flaky(FakeApi):
    """MAX отказывает одному ученику: он заблокировал бота."""

    def send(self, user_id, text, buttons=None, markdown=False):
        if user_id == 10:
            raise httpx.HTTPStatusError("403", request=httpx.Request("POST", "http://x"), response=httpx.Response(403))
        super().send(user_id, text, buttons)


def test_one_blocked_student_does_not_break_teachers_action():
    api = Flaky()
    b = max_bot.Bot(api, "ideya_bot")
    c = db.create_class(1, "Елена Викторовна", "10А", "история")
    db.join(c.id, 10, "Иван Иванов")
    db.join(c.id, 11, "Мария Смирнова")
    b.delete(1, c.id)
    assert any(u == 11 and "удалил класс" in t for u, t, _ in api.sent)  # остальные узнали
    assert any(u == 1 and "удалён" in t for u, t, _ in api.sent)  # учитель получил «Вернуть»


def test_app_tells_students_like_bot(monkeypatch):
    api = FakeApi()
    monkeypatch.setattr(max_bot, "_bot", max_bot.Bot(api, "ideya_bot"))
    c = db.create_class(MARIA, "Мария Смирнова", "10А", "история")
    member, _ = db.join(c.id, 10, "Иван Иванов")
    headers = sign_in(MARIA)
    assert client.delete(f"/api/classes/{c.id}/students/{member.id}", headers=headers).status_code == 200
    assert api.sent[-1][:2] == (10, max_bot.student_removed_text(c))
    assert client.post(f"/api/classes/{c.id}/students/{member.id}/restore", headers=headers).status_code == 200
    assert api.sent[-1][:2] == (10, max_bot.student_returned_text(c))
    assert client.delete(f"/api/classes/{c.id}", headers=headers).status_code == 200
    assert api.sent[-1][:2] == (10, max_bot.class_deleted_text(c))


# ---------- 7. разметка в сводке ----------


def test_student_name_cannot_inject_markdown():
    roster = Roster(
        lesson_id="L", class_name="10А", subject="история", topic="Реформы",
        teacher=Student(id="t", name="Елена Викторовна"),
        students=[Student(id="s1", name="[Подробнее](https://evil.example)"), Student(id="s2", name="Аня_2010")],
    )
    text = max_bot.summary_text(roster, Summary(lesson_id="L", carried=["s2"], dropped=["s1"], weak_topics=[], ask_next=[]), 30)
    assert "[Подробнее](https" not in text
    assert r"\[Подробнее\]\(https://evil.example\)" in text and r"Аня\_2010" in text


# ---------- 8. строка вместо списка от GigaChat ----------


def test_string_instead_of_list_falls_back_to_template(monkeypatch):
    monkeypatch.setattr(llm, "enabled", lambda: True)
    monkeypatch.setattr(summary, "_ask", lambda text: {"carried": [], "dropped": [], "weak_topics": "Табель о рангах", "ask_next": []})
    roster = ROSTER
    levels = {s.id: (1, 0.0, []) for s in roster.students}
    s = summary.build_summary(roster, [], levels, {})
    assert "Т" not in s.weak_topics  # не по буквам
    assert s == summary.template_summary(roster, [], levels, {})


# ---------- 9–10. правила шкалы ----------


def test_two_letter_name_is_recognised():
    roster = ROSTER.model_copy(update={"students": [*ROSTER.students, Student(id="yan", name="Ян К.")]})
    ctx = [said("teacher", 0, 2, "Ян, расскажи про Азовские походы.")]
    assert classify("Пётр хотел выход к морю.", "yan", 3, roster, ctx) == "response"
    ctx = [said("teacher", 0, 2, "Спросим Яна.")]
    assert classify("Пётр хотел выход к морю.", "yan", 3, roster, ctx) == "response"


def test_question_split_by_pause_is_one_initiative():
    ctx = [
        said("artem_k", 0, 2, "А почему Пётр", "initiative"),
        said("artem_k", 3, 5, "перенёс столицу?", "initiative"),  # хвост того же вопроса после паузы
    ]
    assert classify("А зачем ему был нужен флот?", "artem_k", 60, ROSTER, ctx) == "initiative"
