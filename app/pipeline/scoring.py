"""Активность, ступени шкалы и нормировка. Правила — Илья (#19), код — Ярослав.

Правила из концепции:
- активность — не время речи, а использованная возможность;
- основной вес у содержательного ответа и инициативы, объём речи — с низким весом и потолком;
- четыре ступени, шкала только растёт, потолок берётся нормальным участием;
- пороги нормируются по самому уроку: медиана активности на ученика за последние
  пять занятий этого класса по этому предмету;
- по ходу урока пороги могут только смягчаться, не больше чем вдвое;
- если темп урока ниже трети нормы, шкала не показывается и урок не идёт в норму;
- первые два занятия — дефолтные пороги;
- «давно не отвечал» и «тихий, но точный» — флаги для учителя, не для ученика.
"""

import statistics
from dataclasses import dataclass, field

from app.models import Flag, UtteranceKind

WEIGHTS: dict[UtteranceKind, float] = {
    "answer": 3.0,
    "initiative": 3.0,
    # меньше ответа: иначе два ответа одним словом весят как два содержательных
    "response": 2.0,
    "other": 0.5,
}
# Засчитанные реплики: с первой из них ученик на второй ступени
COUNTED: set[UtteranceKind] = {"answer", "initiative", "response"}
OTHER_CAP = 1.0
# Объём речи: 0,5 балла за каждые полные 30 секунд, не больше 1 балла за урок
SPEECH_POINTS = 0.5
SPEECH_STEP_S = 30.0
SPEECH_CAP = 1.0

# Пороги ступеней 3 и 4 в долях нормы урока. Ступень 2 — первая засчитанная реплика
LEVEL_THRESHOLDS = (0.6, 1.0)

# Норма урока: баллы на ученика
DEFAULT_NORM = 6.0  # первые два занятия, на 40-минутный урок
NORM_LESSONS = 5
MIN_LESSONS = 2
# Поправка по ходу урока: с середины урока, если темп ниже нормы больше
# чем на четверть, и не больше чем вдвое вниз. Зазор в четверть — чтобы
# норма не реагировала на обычные паузы перед ответами
ADJUST_FROM_SHARE = 0.5
SOFTEN_BELOW_SHARE = 0.75
MAX_SOFTENING = 2.0
# Темп ниже трети нормы — «тихий» урок: шкалы нет, в норму не идёт
QUIET_SHARE = 1 / 3

# «Давно не отвечал»: четверть урока без засчитанных реплик, но не меньше 10 минут.
# На коротком фрагменте урока, как демо на 5–7 минут, флага не бывает
LONG_SILENCE_SHARE = 0.25
LONG_SILENCE_MIN_S = 600.0
# «Тихий, но точный»: не больше двух засчитанных реплик, и среди них ответ от 8 слов
PRECISE_MAX_REPLIES = 2
PRECISE_MIN_WORDS = 8


def points(kinds: list[UtteranceKind], speech_s: float) -> float:
    """Баллы ученика за урок по типам реплик и секундам речи."""
    counted = sum(WEIGHTS[k] for k in kinds if k in COUNTED)
    other = min(OTHER_CAP, WEIGHTS["other"] * kinds.count("other"))
    speech = min(SPEECH_CAP, SPEECH_POINTS * (speech_s // SPEECH_STEP_S))
    return counted + other + speech


def level_for(score: float, norm: float, replied: bool = True) -> int:
    """Ступень от 1 до 4 по накопленным баллам и норме урока.

    replied — была ли засчитанная реплика. Без неё ученик остаётся на первой
    ступени, сколько бы ни говорил не по делу.
    """
    if not replied:
        return 1
    level = 2
    for threshold in LEVEL_THRESHOLDS:
        if score >= threshold * norm:
            level += 1
    return level


def lesson_norm(history: list[float]) -> float:
    """Медиана активности на ученика за последние пять занятий.

    history — активность прошлых уроков от старых к новым, без «тихих» уроков:
    они в норму не идут. Первые два занятия — норма по умолчанию.
    """
    if len(history) < MIN_LESSONS:
        return DEFAULT_NORM
    return statistics.median(history[-NORM_LESSONS:])


def lesson_activity(scores: list[float]) -> float:
    """Активность урока: средние баллы на ученика, который был на уроке."""
    return sum(scores) / len(scores) if scores else 0.0


def is_quiet(activity: float, norm: float) -> bool:
    return activity < norm * QUIET_SHARE


def softened_norm(base: float, current: float, activity: float, elapsed_share: float) -> float:
    """Норма с поправкой на темп урока: только вниз и не ниже половины базовой.

    current — норма, действующая сейчас: раз смягчённая, она не возвращается назад.
    """
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
    """Шкала класса на одном уроке: реплики → баллы, ступени и флаги."""

    def __init__(self, student_ids: list[str], norm: float, lesson_s: float):
        self.base_norm = norm
        self.norm = norm
        self.lesson_s = lesson_s
        self._students = {sid: _Student() for sid in student_ids}

    def add(self, student_id: str, kind: UtteranceKind, text: str, t: float, end: float) -> int:
        """Учесть реплику и вернуть ступень ученика."""
        s = self._students[student_id]
        s.kinds.append(kind)
        s.texts.append(text)
        s.speech_s += max(0.0, end - t)
        if kind in COUNTED:
            s.last_reply_end = end
        self.tick(end)
        return self.level(student_id)

    def tick(self, now: float) -> None:
        """Подстроить норму под темп урока. Ступени при этом только растут."""
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
