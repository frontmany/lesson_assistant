"""Отбраковка галлюцинаций Whisper, без модели."""

import pytest

pytest.importorskip("faster_whisper")

import dataclasses  # noqa: E402

import numpy as np  # noqa: E402
from faster_whisper.transcribe import Segment  # noqa: E402

from app.models import Roster, Student  # noqa: E402
from app.pipeline import asr  # noqa: E402
from app.pipeline.asr import is_hallucination  # noqa: E402


def segment(text: str, compression_ratio: float, no_speech_prob: float, avg_logprob: float) -> Segment:
    return Segment(
        id=0, seek=0, start=0, end=1, text=text, tokens=[], avg_logprob=avg_logprob,
        compression_ratio=compression_ratio, no_speech_prob=no_speech_prob, words=None, temperature=0,
    )


def test_normal_phrase_is_kept():
    assert not is_hallucination(segment("Ну, там флот строили", 1.1, 0.05, -0.3))


def test_repeating_loop_is_dropped():
    # реплика, которую Whisper выдал на музыке во время проверки
    assert is_hallucination(segment("это не то, что " * 40, 9.5, 0.1, -0.2))


def test_text_where_whisper_hears_no_speech_is_dropped():
    assert is_hallucination(segment("Продолжение следует...", 1.0, 0.8, -1.4))


def test_quiet_but_confident_phrase_is_kept():
    # тихо сказано, но Whisper уверен в словах
    assert not is_hallucination(segment("Табель о рангах", 1.0, 0.7, -0.4))


class FakeModel:
    def __init__(self, name):
        self.name = name
        self.options = None

    def transcribe(self, audio, **options):
        self.options = options
        return [segment(" Текст ", 1.0, 0.0, -0.2)], None


@pytest.fixture
def models(monkeypatch):
    loaded = {}

    def load(name):
        return loaded.setdefault(name, FakeModel(name))

    settings = dataclasses.replace(asr.settings, whisper_model_teacher="small", whisper_model_student="medium")
    monkeypatch.setattr(asr, "settings", settings)
    monkeypatch.setattr(asr, "_load", load)
    return loaded


def test_teacher_and_student_use_their_own_models(models):
    audio = np.zeros(16000, dtype=np.float32)
    assert asr.transcribe(audio, "teacher", "Артём, Иван") == "Текст"
    assert asr.transcribe(audio, "student") == "Текст"
    teacher, student = models["small"].options, models["medium"].options
    assert teacher["beam_size"] == 1 and teacher["hotwords"] == "Артём, Иван"
    assert student["beam_size"] == 5 and student["hotwords"] is None


def test_class_names_include_short_forms():
    roster = Roster(
        lesson_id="x", class_name="10А", subject="История", topic="т",
        teacher=Student(id="teacher", name="Елена Викторовна"),
        students=[Student(id="a", name="Артём К."), Student(id="i", name="Иван И."), Student(id="i2", name="Иван П.")],
    )
    assert asr.class_names(roster) == "Артём, Иван, Ваня"


def test_compare_counts_word_errors_and_ignores_case():
    from analytics.compare_asr import edit_distance, words

    assert words("Ёлка, это ёж?") == ["елка", "это", "еж"]
    assert edit_distance(["а", "б", "в"], ["а", "в", "г"]) == 2
    assert edit_distance([], ["а"]) == 1


def test_compare_measures_question_marks(monkeypatch):
    from analytics import compare_asr
    from analytics.compare_asr import Phrase, evaluate

    audio = np.zeros(16000, dtype=np.float32)
    phrases = [
        Phrase("1.wav", "Зачем нужен флот", "question", False, False, audio),
        Phrase("2.wav", "Это было после Полтавы", "question", True, False, audio),
        Phrase("3.wav", "Я не знаю", "other", False, False, audio),
    ]
    answers = iter(["прогрев", "Зачем нужен флот?", "Это было после Полтавы", "Я не знаю?"])
    monkeypatch.setattr(compare_asr, "recognizer", lambda name: lambda a: next(answers))
    row = evaluate("fake", phrases, show_text=False)
    assert row["Ошибки в словах"] == "0.0%"
    assert row["Вопросы со знаком"] == "1/2 (50%)"
    assert row["Из них только интонацией"] == "0/1 (0%)"
    assert row["Лишние «?»"] == "1/1 (100%)"


def test_long_phrase_stays_within_whisper_limit():
    assert asr.options("student", np.zeros(16000 * 60, dtype=np.float32))["max_new_tokens"] == asr.MAX_TOKENS


class FakeGigaAM:
    def __init__(self):
        self.calls = 0

    def recognize(self, audio, sample_rate):
        assert sample_rate == 16000
        self.calls += 1
        return " Зачем Петру нужен был флот? "


def test_gigaam_model_is_used_for_students(monkeypatch):
    giga = FakeGigaAM()
    whisper = FakeModel("small")
    settings = dataclasses.replace(asr.settings, whisper_model_teacher="small", whisper_model_student="gigaam-v3-e2e-ctc")
    monkeypatch.setattr(asr, "settings", settings)
    monkeypatch.setattr(asr, "_load", lambda name: giga if asr.is_gigaam(name) else whisper)
    audio = np.zeros(16000, dtype=np.float32)
    assert asr.transcribe(audio, "student") == "Зачем Петру нужен был флот?"
    assert giga.calls == 1 and whisper.options is None
    asr.transcribe(audio, "teacher", "Артём")
    assert whisper.options["hotwords"] == "Артём"
