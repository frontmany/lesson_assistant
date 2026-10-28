"""Контракты: форматы событий между сервером, мини-приложением и ботом.

Меняешь формат — меняй в одном PR эту модель, моки в webapp/mock/
и docs/contracts.md. tests/test_contracts.py следит, чтобы они совпадали.
"""

from typing import Literal

from pydantic import BaseModel, Field

# Четыре ступени шкалы ученика: подписи из концепции, едины для 5–11 классов
LEVEL_LABELS = {
    1: "Пока тихо",
    2: "Есть ответ",
    3: "Участвуешь",
    4: "Активно на уроке",
}

UtteranceKind = Literal["answer", "initiative", "response", "other"]
Flag = Literal["long_silence", "quiet_but_precise"]


class Student(BaseModel):
    id: str
    name: str
    # аккаунт MAX: по нему бот и мини-приложение узнают человека. В настоящем
    # Сферуме приходит из школьной системы вместе с составом класса. Наружу
    # через API не отдаётся
    max_user_id: int | None = Field(default=None, exclude=True)


class Roster(BaseModel):
    lesson_id: str
    class_name: str
    subject: str
    topic: str
    teacher: Student
    students: list[Student]


class JoinInfo(BaseModel):
    """Вход в звонок урока: API → мини-приложение."""

    url: str = Field(description="адрес LiveKit для браузера")
    token: str
    participant_id: str
    name: str
    role: Literal["teacher", "student"]


class Utterance(BaseModel):
    """Реплика: сервер → панель учителя."""

    type: Literal["utterance"] = "utterance"
    student_id: str
    t: float = Field(description="секунда от начала урока")
    text: str
    kind: UtteranceKind
    addressed: bool = Field(description="ответ на обращение учителя")


class ScaleUpdate(BaseModel):
    """Изменение шкалы: сервер → учитель и сам ученик. Шкала только растёт."""

    type: Literal["scale"] = "scale"
    student_id: str
    level: int = Field(ge=1, le=4)
    label: str
    flags: list[Flag] = []


class AnalyticsState(BaseModel):
    """Выключатель аналитики: для всего класса (student_id = None) или одного ученика."""

    type: Literal["analytics"] = "analytics"
    enabled: bool
    student_id: str | None = None


class AskNext(BaseModel):
    student_id: str
    topic: str
    question: str


class Summary(BaseModel):
    """Сводка учителю после урока: API → бот и мини-приложение."""

    type: Literal["summary"] = "summary"
    lesson_id: str
    carried: list[str] = Field(description="кто вытягивал урок")
    dropped: list[str] = Field(description="кто выпал")
    weak_topics: list[str]
    ask_next: list[AskNext]


class StudentCard(BaseModel):
    """Личная карточка ученика после урока: только своё, без оценок и рейтинга."""

    type: Literal["card"] = "card"
    lesson_id: str
    student_id: str
    level: int = Field(ge=1, le=4)
    went_well: list[str]
    to_repeat: list[str]


LessonEvent = Utterance | ScaleUpdate | AnalyticsState
