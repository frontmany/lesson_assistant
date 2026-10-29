"""Баллы, ступени, норма урока и флаги: правила из #19."""

import csv

import pytest

from app.pipeline.classify import classify
from app.pipeline.scoring import (
    DEFAULT_NORM,
    Scoreboard,
    is_quiet,
    lesson_activity,
    lesson_norm,
    level_for,
    points,
    softened_norm,
)
from tests.test_classify import DEMO, ROSTER


def test_points_weight_answers_and_cap_talk():
    assert points(["answer", "initiative", "response"], 0) == 8
    assert points(["other"] * 10, 0) == 1  # болтовня не больше 1 балла
    assert points([], 29) == 0
    assert points([], 59) == 0.5
    assert points([], 600) == 1  # объём речи не больше 1 балла


def test_level_needs_a_counted_reply():
    assert level_for(2, 4.5, replied=False) == 1
    assert level_for(2, 4.5) == 2


@pytest.mark.parametrize(("score", "level"), [(2, 2), (2.7, 3), (4.4, 3), (4.5, 4), (20, 4)])
def test_levels_are_shares_of_norm(score, level):
    assert level_for(score, 4.5) == level


def test_first_two_lessons_use_default_norm():
    assert lesson_norm([]) == DEFAULT_NORM
    assert lesson_norm([3.0]) == DEFAULT_NORM
    assert lesson_norm([3.0, 5.0]) == 4.0


def test_norm_is_median_of_last_five_lessons():
    # бурная дискуссия не сдвигает норму, старые уроки не учитываются
    assert lesson_norm([100, 100, 4, 5, 30, 6, 4]) == 5


def test_activity_is_points_per_student():
    assert lesson_activity([6, 0, 3]) == 3
    assert lesson_activity([]) == 0


def test_quiet_lesson_is_below_third_of_norm():
    assert is_quiet(1.9, 6)
    assert not is_quiet(2, 6)


def test_norm_does_not_move_in_first_half():
    assert softened_norm(6, 6, activity=0, elapsed_share=0.4) == 6


def test_norm_ignores_small_dips_in_pace():
    assert softened_norm(6, 6, activity=2.4, elapsed_share=0.5) == 6  # темп 4,8 — меньше четверти отставания


def test_norm_softens_to_pace_but_not_below_half():
    assert softened_norm(6, 6, activity=2, elapsed_share=0.5) == 4
    assert softened_norm(6, 6, activity=0.5, elapsed_share=0.5) == 3


def test_norm_never_grows_back_during_lesson():
    assert softened_norm(6, 4, activity=6, elapsed_share=1) == 4


def test_levels_only_grow():
    board = Scoreboard(["ivan_i", "maria_s"], norm=4.5, lesson_s=300)
    assert board.add("ivan_i", "answer", "Он искал союзников против Османской империи.", 10, 15) == 3
    assert board.add("ivan_i", "answer", "Ввёл рекрутские наборы в 1705 году.", 20, 25) == 4
    board.norm = 100  # даже если норма вдруг выросла, ступень не падает
    assert board.level("ivan_i") == 4


def test_long_silence_is_quarter_of_lesson_but_at_least_ten_minutes():
    board = Scoreboard(["dmitry_p"], norm=6, lesson_s=40 * 60)
    assert board.flags("dmitry_p", 9 * 60) == []
    assert board.flags("dmitry_p", 10 * 60) == ["long_silence"]
    long = Scoreboard(["dmitry_p"], norm=6, lesson_s=60 * 60)
    assert long.flags("dmitry_p", 14 * 60) == []
    assert long.flags("dmitry_p", 15 * 60) == ["long_silence"]


def test_no_long_silence_on_short_fragment():
    board = Scoreboard(["dmitry_p"], norm=4.5, lesson_s=300)
    assert board.flags("dmitry_p", 300) == []


def test_reply_resets_long_silence():
    board = Scoreboard(["artem_k"], norm=6, lesson_s=40 * 60)
    board.add("artem_k", "response", "Флот строили.", 1200, 1202)
    assert board.flags("artem_k", 1700) == []
    assert board.flags("artem_k", 1802) == ["long_silence"]


def test_quiet_but_precise_needs_few_replies_and_a_long_answer():
    board = Scoreboard(["alisa_n", "artem_k"], norm=4.5, lesson_s=300)
    board.add("alisa_n", "answer", "У каждой коллегии была своя отрасль, и решения принимались голосованием.", 100, 110)
    board.add("artem_k", "response", "Табель о рангах.", 100, 102)
    assert "quiet_but_precise" in board.flags("alisa_n", 110)
    assert "quiet_but_precise" not in board.flags("artem_k", 110)


def test_demo_lesson_scale():
    """Демо-урок с нормой 4,5: картина из таблицы правил в #19."""
    students = [s.id for s in ROSTER.students]
    board = Scoreboard(students, norm=4.5, lesson_s=300)
    context: list[dict] = []
    for row in csv.DictReader(open(DEMO / "ground_truth.csv", encoding="utf-8")):
        who, t, end = row["speaker_id"], float(row["start_s"]), float(row["end_s"])
        item = {"student_id": who, "t": t, "end": end, "text": row["text"]}
        if who in students:
            item["kind"] = classify(row["text"], who, t, ROSTER, context)
            board.add(who, item["kind"], row["text"], t, end)
        context.append(item)

    assert board.norm == 4.5  # темп урока нормальный, норма не смягчалась
    scores = {sid: board.score(sid) for sid in students}
    assert scores == {"ivan_i": 11.5, "maria_s": 6, "artem_k": 4, "sofya_l": 2.5, "alisa_n": 3, "dmitry_p": 0}
    levels = {sid: board.level(sid) for sid in students}
    assert levels == {"ivan_i": 4, "maria_s": 4, "artem_k": 3, "sofya_l": 2, "alisa_n": 3, "dmitry_p": 1}
    flags = {sid: board.flags(sid, 300) for sid in students}
    # пять минут — фрагмент урока: «давно не отвечал» на нём не появляется
    assert flags == {
        "ivan_i": [],
        "maria_s": [],
        "artem_k": [],
        "sofya_l": [],
        "alisa_n": ["quiet_but_precise"],
        "dmitry_p": [],
    }
    assert not board.quiet()


def test_continuation_adds_speech_not_points():
    board = Scoreboard(["sofya_l"], norm=6, lesson_s=40 * 60)
    board.add("sofya_l", "response", "Нет.", 95, 96)
    for i in range(10):
        board.extend("sofya_l", "Это вообще очень интересный вопрос.", 97 + 3 * i, 99 + 3 * i)
    assert board.score("sofya_l") == 2  # один отклик; 20 секунд речи — меньше 30, баллов за объём нет
    assert board.level("sofya_l") == 2
