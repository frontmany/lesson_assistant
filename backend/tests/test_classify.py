"""Тип реплики, правила и сверка с эталоном демо урока"""

import csv
import json
from pathlib import Path

from app.models import Roster, Student
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
    assert kind("Готов.", "ivan_i", 6, [said("teacher", 0, 5, "Ваня, ты готов?")]) == "response"
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
    ctx = [said("teacher", 0, 1, "Софья,"), said("teacher", 2.5, 5, "чем коллегии отличались от приказов?")]
    assert kind("Ну, коллегии были другие.", "sofya_l", 6, ctx) == "response"


def test_question_is_initiative():
    assert kind("А зачем было менять?", "maria_s", 3, []) == "initiative"


def test_question_without_mark_is_initiative():
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
    """Нужно не меньше 70 процентов совпадений с эталоном"""
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


CLASS = Roster(
    lesson_id="7b",
    class_name="7Б",
    subject="история",
    topic="",
    teacher=Student(id="teacher", name="Марина Сергеевна"),
    students=[
        Student(id="petrov", name="Иван П.", full_name="Иван Петров"),
        Student(id="sidorov", name="Иван С.", full_name="Иван Сидоров"),
        Student(id="smirnova", name="Ольга С.", full_name="Ольга Смирнова"),
        Student(id="ivanov", name="Пётр И.", full_name="Пётр Иванов"),
        Student(id="maria", name="Мария К.", full_name="Мария Котова"),
    ],
)


def in_class(text: str, who: str, teacher_says: str) -> str:
    return classify(text, who, 6, CLASS, [said("teacher", 0, 5, teacher_says)])


def test_surname_in_any_case_is_response():
    assert in_class("В Индию.", "sidorov", "Сидоров, куда плыл Колумб?") == "response"
    assert in_class("В Индию.", "sidorov", "Спросим Сидорова.") == "response"
    assert in_class("В Индию.", "smirnova", "Что скажет Смирнова? Смирновой слово.") == "response"


def test_surname_of_namesake_takes_the_address():
    assert in_class("В Индию.", "petrov", "Сидоров, куда плыл Колумб?") != "response"
    assert in_class("В Индию.", "sidorov", "Иван Сидоров, куда плыл Колумб?") == "response"
    assert in_class("В Индию.", "petrov", "Иван Сидоров, куда плыл Колумб?") != "response"


def test_name_alone_with_namesakes_goes_to_who_answered():
    assert in_class("В Индию.", "petrov", "Иван, куда плыл Колумб?") == "response"
    assert in_class("В Индию.", "sidorov", "Ваня, куда плыл Колумб?") == "response"


def test_surname_made_from_name_does_not_call_the_name():
    assert in_class("В Индию.", "ivanov", "Иванов, куда плыл Колумб?") == "response"
    assert in_class("В Индию.", "petrov", "Иванов, куда плыл Колумб?") != "response"


def test_name_cases_and_lookalike_words():
    assert in_class("Готова.", "maria", "Спросим Марию.") == "response"
    assert in_class("Готова.", "maria", "Какую машину построил Леонардо?") != "response"
    assert in_class("Готова.", "petrov", "Что такое ванна Архимеда?") != "response"


def test_class_roster_has_full_names_but_api_does_not_show_them():
    from app.db import classes

    c = classes.create_class(1, "Марина Сергеевна", "7Б", "история")
    classes.join(c.id, 10, "Иван Петров")
    lesson = classes.lesson_for_class(c.id)
    roster = classes.lesson_roster(lesson.id)
    assert (roster.students[0].name, roster.students[0].full_name) == ("Иван П.", "Иван Петров")
    assert "full_name" not in roster.model_dump()["students"][0]


def test_dont_know_after_address_is_not_response():
    ctx = [said("teacher", 0, 5, "Артём, что Пётр сделал с флотом?")]
    assert kind("Не знаю.", "artem_k", 6, ctx) == "other"
    assert kind("Нет.", "artem_k", 6, ctx) == "other"


def test_one_word_answer_right_after_question_counts():
    ctx = [said("teacher", 0, 5, "Кто открыл Америку?")]
    assert kind("Колумб.", "artem_k", 6, ctx) == "answer"
    assert kind("Да.", "artem_k", 6, ctx) == "other"
    assert kind("Не знаю.", "artem_k", 6, ctx) == "other"
    ctx.append(said("ivan_i", 5.5, 6.5, "Колумб.", "answer"))
    assert kind("Колумб.", "artem_k", 7, ctx) == "other"


def test_initiative_needs_three_words_except_may_i():
    assert kind("Можно выйти?", "artem_k", 6, []) == "other"
    assert kind("Что?", "artem_k", 6, []) == "other"
    assert kind("Можно я отвечу?", "artem_k", 6, []) == "initiative"
    assert kind("А почему Пётр перенёс столицу?", "artem_k", 6, []) == "initiative"


def test_no_more_than_two_initiatives_per_lesson():
    ctx = [
        said("artem_k", 0, 2, "А почему Пётр перенёс столицу?", "initiative"),
        said("artem_k", 30, 32, "А зачем ему был нужен флот?", "initiative"),
    ]
    assert kind("А сколько лет строили Петербург?", "artem_k", 60, ctx) == "other"
    assert kind("А сколько лет строили Петербург?", "ivan_i", 60, ctx) == "initiative"


def test_teacher_turn_with_name_survives_pause():
    ctx = [said("teacher", 0, 1, "Артём."), said("teacher", 5, 8, "Что Пётр сделал с флотом?")]
    assert kind("Построил его.", "artem_k", 9, ctx) == "response"
    ctx = [said("teacher", 0, 1, "Хорошо."), said("teacher", 5, 8, "Что Пётр сделал с флотом?")]
    assert kind("Построил его.", "artem_k", 9, ctx) == "answer"
