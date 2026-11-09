"""Форматы событий между сервером, приложением и ботом"""

from typing import Literal

from pydantic import BaseModel, Field

# Четыре ступени шкалы ученика
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
    # аккаунт MAX, наружу не отдаётся
    max_user_id: int | None = Field(default=None, exclude=True)
    # полное имя для обращения по фамилии, наружу не отдаётся
    full_name: str | None = Field(default=None, exclude=True)
    # ученик из записи, в норму не попадает
    virtual: bool = False


class Roster(BaseModel):
    lesson_id: str
    class_name: str
    subject: str
    topic: str
    teacher: Student
    students: list[Student]
    virtual_teacher: Student | None = None


class JoinInfo(BaseModel):
    url: str = Field(description="адрес LiveKit для браузера")
    token: str
    participant_id: str
    name: str
    role: Literal["teacher", "student"]


class Utterance(BaseModel):
    type: Literal["utterance"] = "utterance"
    student_id: str
    t: float = Field(description="секунда от начала урока")
    text: str
    kind: UtteranceKind
    addressed: bool = Field(description="ответ на обращение учителя")


class ScaleUpdate(BaseModel):
    type: Literal["scale"] = "scale"
    student_id: str
    level: int = Field(ge=1, le=4)
    label: str
    flags: list[Flag] = []


class AnalyticsState(BaseModel):
    type: Literal["analytics"] = "analytics"
    enabled: bool
    student_id: str | None = None


class AskNext(BaseModel):
    student_id: str
    topic: str
    question: str


class Summary(BaseModel):
    type: Literal["summary"] = "summary"
    lesson_id: str
    carried: list[str] = Field(description="кто вытягивал урок")
    dropped: list[str] = Field(description="кто выпал")
    weak_topics: list[str]
    ask_next: list[AskNext]


class StudentCard(BaseModel):
    type: Literal["card"] = "card"
    lesson_id: str
    student_id: str
    level: int = Field(ge=1, le=4)
    went_well: list[str]
    to_repeat: list[str]


LessonEvent = Utterance | ScaleUpdate | AnalyticsState
