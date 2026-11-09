"""Активность, ступени шкалы и норма урока"""

import statistics
from dataclasses import dataclass, field

from app.models import Flag, UtteranceKind

WEIGHTS: dict[UtteranceKind, float] = {
    "answer": 3.0,
    "initiative": 3.0,
    # меньше ответа, иначе ответы одним словом весят как содержательные
    "response": 2.0,
    "other": 0.5,
}
COUNTED: set[UtteranceKind] = {"answer", "initiative", "response"}
OTHER_CAP = 1.0
SPEECH_POINTS = 0.5
SPEECH_STEP_S = 30.0
SPEECH_CAP = 1.0

# Пороги ступеней 3 и 4 в долях нормы
LEVEL_THRESHOLDS = (0.6, 1.0)

DEFAULT_NORM = 6.0  # на первые два занятия
NORM_LESSONS = 5
MIN_LESSONS = 2
# Норма смягчается с середины урока, не больше чем вдвое
ADJUST_FROM_SHARE = 0.5
SOFTEN_BELOW_SHARE = 0.75
MAX_SOFTENING = 2.0
# Темп ниже трети нормы значит тихий урок
QUIET_SHARE = 1 / 3

# Давно не отвечал, четверть урока без засчитанных реплик
LONG_SILENCE_SHARE = 0.25
LONG_SILENCE_MIN_S = 600.0
PRECISE_MAX_REPLIES = 2
PRECISE_MIN_WORDS = 8


def points(kinds: list[UtteranceKind], speech_s: float) -> float:
    counted = sum(WEIGHTS[k] for k in kinds if k in COUNTED)
    other = min(OTHER_CAP, WEIGHTS["other"] * kinds.count("other"))
    speech = min(SPEECH_CAP, SPEECH_POINTS * (speech_s // SPEECH_STEP_S))
    return counted + other + speech


def level_for(score: float, norm: float, replied: bool = True) -> int:
    if not replied:
        return 1
    level = 2
    for threshold in LEVEL_THRESHOLDS:
        if score >= threshold * norm:
            level += 1
    return level


def lesson_norm(history: list[float]) -> float:
    if len(history) < MIN_LESSONS:
        return DEFAULT_NORM
    return statistics.median(history[-NORM_LESSONS:])


def lesson_activity(scores: list[float]) -> float:
    return sum(scores) / len(scores) if scores else 0.0


def is_quiet(activity: float, norm: float) -> bool:
    return activity < norm * QUIET_SHARE


def softened_norm(base: float, current: float, activity: float, elapsed_share: float) -> float:
    if elapsed_share < ADJUST_FROM_SHARE:
        return current
    pace = activity / elapsed_share
    if pace >= base * SOFTEN_BELOW_SHARE:
        return current
    return min(current, max(base / MAX_SOFTENING, pace))


@dataclass
class _Student:
    kinds: list[UtteranceKind] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)
    speech_s: float = 0.0
    last_reply_end: float | None = None
    level: int = 1


class Scoreboard:
    def __init__(self, student_ids: list[str], norm: float, lesson_s: float):
        self.base_norm = norm
        self.norm = norm
        self.lesson_s = lesson_s
        self._students = {sid: _Student() for sid in student_ids}
        # отложенная шкала возвращается, если аналитику включат снова
        self._parked: dict[str, _Student] = {}

    def add(self, student_id: str, kind: UtteranceKind, text: str, t: float, end: float) -> int:
        s = self._students[student_id]
        s.kinds.append(kind)
        s.texts.append(text)
        s.speech_s += max(0.0, end - t)
        if kind in COUNTED:
            s.last_reply_end = end
        self.tick(end)
        return self.level(student_id)

    def forget(self, student_id: str) -> None:
        s = self._students.pop(student_id, None)
        if s is not None:
            self._parked[student_id] = s

    def students(self) -> set[str]:
        return set(self._students)

    def add_student(self, student_id: str) -> None:
        if student_id not in self._students:
            self._students[student_id] = self._parked.pop(student_id, None) or _Student()

    def extend(self, student_id: str, text: str, t: float, end: float, kind: UtteranceKind = "other") -> int:
        """Продолжение прибавляет время речи, а не баллы за тип"""
        s = self._students[student_id]
        if not s.kinds:
            return self.add(student_id, kind, text, t, end)
        s.texts[-1] = f"{s.texts[-1]} {text}"
        s.speech_s += max(0.0, end - t)
        if s.kinds[-1] not in COUNTED and kind in COUNTED:
            s.kinds[-1] = kind
        if s.kinds[-1] in COUNTED:
            s.last_reply_end = end
        self.tick(end)
        return self.level(student_id)

    def tick(self, now: float) -> None:
        self.norm = softened_norm(self.base_norm, self.norm, self.activity(), now / self.lesson_s)
        for sid in self._students:
            self.level(sid)

    def score(self, student_id: str) -> float:
        s = self._students[student_id]
        return points(s.kinds, s.speech_s)

    def level(self, student_id: str) -> int:
        s = self._students[student_id]
        replied = any(k in COUNTED for k in s.kinds)
        s.level = max(s.level, level_for(self.score(student_id), self.norm, replied))
        return s.level

    def activity(self) -> float:
        return lesson_activity([self.score(sid) for sid in self._students])

    def quiet(self) -> bool:
        return is_quiet(self.activity(), self.base_norm)

    def flags(self, student_id: str, now: float) -> list[Flag]:
        s = self._students[student_id]
        flags: list[Flag] = []
        since = s.last_reply_end if s.last_reply_end is not None else 0.0
        if now - since >= max(LONG_SILENCE_SHARE * self.lesson_s, LONG_SILENCE_MIN_S):
            flags.append("long_silence")
        replies = [(k, text) for k, text in zip(s.kinds, s.texts) if k in COUNTED]
        if 0 < len(replies) <= PRECISE_MAX_REPLIES and any(
            k == "answer" and len(text.split()) >= PRECISE_MIN_WORDS for k, text in replies
        ):
            flags.append("quiet_but_precise")
        return flags
