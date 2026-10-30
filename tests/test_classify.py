"""Тип реплики: правила из #19 и сверка с эталонной разметкой демо-урока."""

import csv
import json
from pathlib import Path

from app.models import Roster
from app.pipeline.classify import classify

DEMO = Path(__file__).parent.parent / "data" / "demo_lesson"
ROSTER = Roster.model_validate(json.loads((DEMO / "roster.json").read_text(encoding="utf-8")))


def said(who: str, t: float, end: float, text: str, kind: str | None = None) -> dict:
    item = {"student_id": who, "t": t, "end": end, "text": text}
    if kind:
        item["kind"] = kind
    return item


def kind(text: str, who: str, t: float, context: list[dict]) -> str:
    return classify(text, who, t, ROSTER, context)


def test_name_in_teacher_question_is_response():
    ctx = [said("teacher", 0, 5, "А флот? Артём, что Пётр сделал с флотом?")]
    assert kind("Ну, там флот строили.", "artem_k", 7, ctx) == "response"


def test_short_name_and_inflection_count_as_address():
    assert kind("Да.", "ivan_i", 6, [said("teacher", 0, 5, "Ваня, ты готов?")]) == "response"
    assert kind("Флот.", "artem_k", 6, [said("teacher", 0, 5, "Спросим Артёма.")]) == "response"


def test_word_tema_is_not_artem():
    ctx = [said("teacher", 0, 5, "Новая тема: реформы. Что вы о них знаете?")]
    assert kind("Пётр менял армию и флот.", "artem_k", 6, ctx) == "answer"


def test_address_expires_after_window():
    ctx = [said("teacher", 0, 5, "Артём, что Пётр сделал с флотом?")]
    assert kind("Флот.", "artem_k", 20, ctx) == "other"


def test_address_lost_when_another_student_answered_first():
    ctx = [
        said("teacher", 0, 5, "Артём, что Пётр сделал с флотом?"),
        said("ivan_i", 5.5, 7, "Построил Балтийский флот и основал Петербург.", "answer"),
    ]
    assert kind("Флот.", "artem_k", 8, ctx) == "other"


def test_name_in_earlier_phrase_of_same_teacher_turn():
    # учитель сделал паузу после имени: это две фразы, но один ход
    ctx = [said("teacher", 0, 1, "Софья,"), said("teacher", 2.5, 5, "чем коллегии отличались от приказов?")]
    assert kind("Ну, коллегии были другие.", "sofya_l", 6, ctx) == "response"


def test_question_is_initiative():
    assert kind("А зачем было менять?", "maria_s", 3, []) == "initiative"


def test_question_without_mark_is_initiative():
    # Whisper на живой речи теряет «?»: вопрос узнаётся по слову в начале
    assert kind("А почему столицу перенесли в Петербург", "maria_s", 3, []) == "initiative"
    assert kind("То есть как это работает", "maria_s", 3, []) == "initiative"
    assert kind("Правильно ли я понял про коллегии", "maria_s", 3, []) == "initiative"


def test_answer_starting_with_when_is_not_question():
    assert kind("Когда Пётр вернулся, он начал строить флот.", "maria_s", 3, []) == "other"
    assert kind("Потому что нужен был выход к морю.", "maria_s", 3, []) == "other"


def test_reply_to_teacher_question_without_mark_is_answer():
    ctx = [said("teacher", 0, 5, "Кто знает в чём было главное отличие")]
    assert kind("У каждой коллегии была своя отрасль.", "alisa_n", 7, ctx) == "answer"


def test_addressing_teacher_by_name_is_initiative():
    assert kind("Елена Викторовна, можно я отвечу.", "maria_s", 3, []) == "initiative"


def test_reply_to_open_question_is_answer():
    ctx = [said("teacher", 0, 5, "Кто знает, в чём было главное отличие?")]
    assert kind("У каждой коллегии была своя отрасль.", "alisa_n", 7, ctx) == "answer"


def test_short_reply_to_open_question_is_other():
    ctx = [said("teacher", 0, 5, "Кто знает, в чём было главное отличие?")]
    assert kind("Не знаю.", "alisa_n", 7, ctx) == "other"


def test_reply_to_statement_is_other():
    ctx = [said("teacher", 0, 5, "Точно, это и есть главное. Запишите.")]
    assert kind("Сейчас запишу всё в тетрадь.", "alisa_n", 7, ctx) == "other"


def test_long_answer_split_into_phrases_keeps_kind():
    ctx = [said("teacher", 0, 5, "Софья, чем коллегии отличались от приказов?")]
    first = said("sofya_l", 6, 30, "Ну, коллегии… Это вообще очень интересный вопрос", "response")
    assert kind("и это тоже надо учитывать.", "sofya_l", 32, [*ctx, first]) == "response"


def test_continuation_without_stored_kind_is_reclassified():
    ctx = [said("teacher", 0, 5, "Софья, чем коллегии отличались от приказов?"), said("sofya_l", 6, 30, "Ну, коллегии…")]
    assert kind("и это тоже надо учитывать.", "sofya_l", 32, ctx) == "response"


def test_without_end_time_start_is_used():
    ctx = [{"student_id": "teacher", "t": 0, "text": "Артём, что Пётр сделал с флотом?"}]
    assert kind("Флот.", "artem_k", 4, ctx) == "response"


def test_demo_lesson_matches_ground_truth():
    """Цель #19 — не меньше 70% совпадений на эталоне. Текст реплик берётся из эталона."""
    rows = list(csv.DictReader(open(DEMO / "ground_truth.csv", encoding="utf-8")))
    context: list[dict] = []
    hits = total = 0
    for row in rows:
        who, t, end = row["speaker_id"], float(row["start_s"]), float(row["end_s"])
        item = said(who, t, end, row["text"])
        if who != ROSTER.teacher.id:
            item["kind"] = kind(row["text"], who, t, context)
            total += 1
            hits += item["kind"] == row["kind"]
        context.append(item)
    assert total == 10
    assert hits / total >= 0.7
    assert hits == total
