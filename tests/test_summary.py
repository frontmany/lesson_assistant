"""Сводка учителю и карточки ученикам после урока (#21): GigaChat, шаблон и рассылка ботом."""

import asyncio
import dataclasses

import pytest

from analytics.summary_demo import demo_lesson
from app.bot import max_bot
from app.db import classes, results
from app.models import AskNext, StudentCard, Summary
from app.pipeline import llm, runner, summary
from app.pipeline.scoring import Scoreboard
from tests.test_results import lesson_of_class


@pytest.fixture
def demo():
    return demo_lesson()


@pytest.fixture
def gigachat(monkeypatch):
    """Подменить GigaChat: ответы по очереди, словарь — JSON, исключение — ошибка."""
    monkeypatch.setattr(llm, "settings", dataclasses.replace(llm.settings, llm_api_key="ключ"))
    answers: list = []
    asked: list[str] = []

    def chat_json(system, user):
        asked.append(user)
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(llm, "chat_json", chat_json)
    return answers, asked


def test_template_summary_without_key(demo):
    roster, utterances, levels = demo
    s = summary.build_summary(roster, utterances, levels, {"dmitry_p": 5})
    assert s.carried == ["ivan_i", "maria_s"]  # ступень 4, по убыванию баллов
    assert s.dropped == ["dmitry_p"]
    assert s.weak_topics == []  # без LLM слабые темы не определить
    assert [a.student_id for a in s.ask_next] == ["dmitry_p"]
    assert s.ask_next[0].question.endswith("?")  # вопрос учителя с урока, последнее предложение
    assert "." not in s.ask_next[0].question[:-1]


def test_prompt_gets_lesson_and_history(demo, gigachat):
    roster, utterances, levels = demo
    answers, asked = gigachat
    answers.append({"carried": [], "dropped": [], "weak_topics": [], "ask_next": []})
    summary.build_summary(roster, utterances, levels, {"alisa_n": 2})
    text = asked[0]
    assert "{" + "utterances}" not in text and "Реформы Петра I" in text
    assert "alisa_n: 2" in text
    assert "Учитель: Здравствуйте" in text and "(ivan_i, ответ)" in text


def test_llm_answer_is_checked(demo, gigachat):
    roster, utterances, levels = demo
    answers, _ = gigachat
    answers.append({
        "carried": ["ivan_i", "чужой", "maria_s", "alisa_n", "artem_k"],
        "dropped": ["dmitry_p", "dmitry_p"],
        "weak_topics": ["Коллегии.", " ", "Сенат", "Флот", "Налоги"],
        "ask_next": [
            {"student_id": "dmitry_p", "topic": "", "question": "Что такое подушная подать?"},
            {"student_id": "чужой", "topic": "Флот", "question": "Где база флота?"},
            {"student_id": "artem_k", "topic": "Флот", "question": " "},
        ],
    })
    s = summary.build_summary(roster, utterances, levels, {})
    assert s.carried == ["ivan_i", "maria_s", "alisa_n"]  # не больше трёх, только ученики урока
    assert s.dropped == ["dmitry_p"]
    assert s.weak_topics == ["Коллегии", "Сенат", "Флот"]
    assert s.ask_next == [AskNext(student_id="dmitry_p", topic="Реформы Петра I", question="Что такое подушная подать?")]


def test_one_retry_then_template(demo, gigachat):
    roster, utterances, levels = demo
    answers, asked = gigachat
    answers.extend([llm.LlmError("не JSON"), {"carried": ["alisa_n"], "dropped": [], "weak_topics": [], "ask_next": []}])
    assert summary.build_summary(roster, utterances, levels, {}).carried == ["alisa_n"]
    answers.extend([llm.LlmError("не JSON"), llm.LlmError("опять")])
    assert summary.build_summary(roster, utterances, levels, {}).carried == ["ivan_i", "maria_s"]  # шаблон
    assert len(asked) == 4


def test_cards_never_praise_silence_and_fill_gaps(demo, gigachat):
    roster, utterances, levels = demo
    answers, _ = gigachat
    answers.append({"cards": [
        {"student_id": "ivan_i", "went_well": ["Ответ про рекрутские наборы."], "to_repeat": ["Сенат"]},
        {"student_id": "dmitry_p", "went_well": ["Внимание на уроке"], "to_repeat": ["Табель о рангах"]},
    ]})
    cards = {c.student_id: c for c in summary.build_cards(roster, utterances, levels)}
    assert cards["ivan_i"].went_well == ["Ответ про рекрутские наборы"]
    assert cards["dmitry_p"].went_well == []  # молчал: похвалу модель придумала
    # Алису модель пропустила: шаблонная карточка по её ответу
    assert cards["alisa_n"].went_well[0].startswith("Ответ: «")
    assert set(cards) == {s.id for s in roster.students}


def test_empty_card_is_not_made(demo):
    roster, utterances, levels = demo
    roster = roster.model_copy(update={"topic": ""})
    cards = {c.student_id for c in summary.template_cards(roster, utterances, levels)
             if c.went_well or c.to_repeat}
    assert "dmitry_p" not in cards  # молчал, а темы нет: пустая карточка хуже тишины
    assert {c.student_id for c in summary.build_cards(roster, utterances, levels)} == cards


def test_summary_text_hides_empty_blocks_and_cuts_long_lists(demo):
    roster, _, _ = demo
    full = Summary(
        lesson_id="x", carried=["ivan_i", "maria_s"], dropped=[], weak_topics=["Табель о рангах"],
        ask_next=[AskNext(student_id="dmitry_p", topic="Северная война", question="Что дал выход к Балтике?")],
    )
    text = max_bot.summary_text(roster, full, 38)
    assert text.startswith("**Итоги урока: 10А, история**\nРеформы Петра I, 38 минут, 6 учеников")
    assert "**Выпали**" not in text
    assert text.endswith("**Спросить в следующий раз**\nДмитрий П. — Северная война\n«Что дал выход к Балтике?»")

    many = full.model_copy(update={"carried": [s.id for s in roster.students]})
    assert "Иван И., Мария С., Артём К., Софья Л. и ещё 2" in max_bot.summary_text(roster, many, 40)

    empty = Summary(lesson_id="x", carried=[], dropped=[], weak_topics=[], ask_next=[])
    assert max_bot.summary_text(roster, empty, 1).endswith("нужны хотя бы несколько реплик учеников.")


def test_card_text():
    roster = demo_lesson()[0]
    card = StudentCard(lesson_id="x", student_id="artem_k", level=3, went_well=[], to_repeat=["Табель о рангах"])
    assert max_bot.card_text(roster, card) == (
        "**Твоя карточка: 10А, история**\nРеформы Петра I\n\n**Что повторить**\nТабель о рангах\n\n"
        "Карточка не показывается классу."
    )


def test_silent_streaks_of_demo_come_from_seed():
    lesson = classes.demo_lesson(1, "Елена Викторовна")
    streaks = results.silent_streaks(lesson.id, ["virtual-ivan_i", "virtual-alisa_n", "virtual-dmitry_p"])
    assert streaks == {"virtual-ivan_i": 0, "virtual-alisa_n": 2, "virtual-dmitry_p": 5}


def test_silent_streaks_of_class():
    c, _, (ivan, artem) = lesson_of_class()
    ids = [f"m{ivan.id}", f"m{artem.id}"]
    # от старого к новому: оба ответили, потом ответил только Иван
    for lesson_levels in ({ids[0]: (2, 3.0, []), ids[1]: (2, 2.0, [])}, {ids[0]: (3, 5.0, []), ids[1]: (1, 0.0, [])}):
        lesson = classes.lesson_for_class(c.id)
        assert results.save_lesson(lesson.id, [], lesson_levels, 2.0, 6.0, False, False)
        classes.mark_ended(lesson.id)
    now = classes.lesson_for_class(c.id)
    newcomer = "m999"  # в прошлых уроках его не было: истории нет
    assert results.silent_streaks(now.id, ids + [newcomer]) == {ids[0]: 0, ids[1]: 1}


class FakeApi:
    def __init__(self):
        self.sent: list[tuple] = []

    def send(self, user_id, text, buttons=None, markdown=False):
        self.sent.append((user_id, text, buttons, markdown))


def test_finished_lesson_is_saved_and_sent(monkeypatch):
    api = FakeApi()
    monkeypatch.setattr(max_bot, "_bot", max_bot.Bot(api, "ideya_bot"))
    c, lesson, (ivan, artem) = lesson_of_class()
    roster = classes.lesson_roster(lesson.id)
    teacher, ivan_id, artem_id = roster.teacher.id, f"m{ivan.id}", f"m{artem.id}"
    utterances = [
        {"student_id": teacher, "t": 0.0, "end": 3.0, "text": "Иван, зачем Пётр поехал в Европу?"},
        {"student_id": ivan_id, "t": 4.0, "end": 9.0, "text": "Искал союзников и учился строить корабли.", "kind": "response"},
    ]
    board = Scoreboard([ivan_id, artem_id], 6.0, 2400)
    board.add(ivan_id, "response", utterances[1]["text"], 4.0, 9.0)

    asyncio.run(runner.finish_lesson(lesson.id, roster, utterances, board))

    assert results.get_summary(lesson.id).dropped == [artem_id]
    assert results.get_card(lesson.id, ivan_id).went_well[0].startswith("Ответ, когда к тебе обратились")
    to_teacher = [m for m in api.sent if m[0] == 1]
    assert len(to_teacher) == 1 and to_teacher[0][1].startswith("**Итоги урока: 10А, история**")
    assert to_teacher[0][2][0][0]["payload"] == f"c{c.id}" and to_teacher[0][3]
    to_ivan = [m for m in api.sent if m[0] == 10]
    assert len(to_ivan) == 1 and "Карточка не показывается классу." in to_ivan[0][1]
    assert not [m for m in api.sent if m[0] == 11]  # у Артёма тема пуста и реплик нет: карточки нет


def test_demo_summary_counts_only_recorded_class(monkeypatch):
    """В демо ученики урока — запись с приставкой virtual-; состав класса без неё на уроке не был."""
    api = FakeApi()
    monkeypatch.setattr(max_bot, "_bot", max_bot.Bot(api, "ideya_bot"))
    lesson = classes.demo_lesson(1, "Елена Викторовна")
    base = classes.lesson_roster(lesson.id)
    students = [s.model_copy(update={"id": f"virtual-{s.id}", "virtual": True}) for s in base.students]
    roster = base.model_copy(update={"students": students})
    ivan = students[0].id
    board = Scoreboard([s.id for s in students], 4.5, 2400)
    utterances = [{"student_id": ivan, "t": 4.0, "end": 9.0, "text": "Искал союзников против Османской империи.", "kind": "answer"}]
    board.add(ivan, "answer", utterances[0]["text"], 4.0, 9.0)

    asyncio.run(runner.finish_lesson(lesson.id, roster, utterances, board))

    (to_teacher,) = api.sent  # ученикам из записи карточки не уходят
    assert to_teacher[0] == 1
    assert "6 учеников" in to_teacher[1]
    assert "**Выпали**\nМария С., Артём К., Софья Л., Алиса Н. и ещё 1" in to_teacher[1]
    assert to_teacher[2] is None  # у демо нет класса: кнопки «Начать урок» нет


def test_card_api_gives_stored_card_or_404(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    _, lesson, (ivan, artem) = lesson_of_class()
    ivan_id, artem_id = f"m{ivan.id}", f"m{artem.id}"
    results.save_summary(lesson.id, Summary(lesson_id=lesson.id, carried=[], dropped=[], weak_topics=[], ask_next=[]))
    results.save_cards(lesson.id, [StudentCard(lesson_id=lesson.id, student_id=ivan_id, level=2, went_well=["Ответ"], to_repeat=[])])
    got = client.get(f"/api/lessons/{lesson.id}/cards/{ivan_id}", params={"id": ivan_id})
    assert got.status_code == 200 and got.json()["went_well"] == ["Ответ"]
    assert client.get(f"/api/lessons/{lesson.id}/cards/{artem_id}", params={"id": artem_id}).status_code == 404
    assert client.get(f"/api/lessons/{lesson.id}/cards/{ivan_id}", params={"id": artem_id}).status_code == 403
