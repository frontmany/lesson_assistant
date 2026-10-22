"""Тип реплики. Правила — Илья, код — Ярослав.

Типы (app.models.UtteranceKind):
- answer — содержательный ответ по теме;
- initiative — сам задал вопрос или вызвался;
- response — ответил, когда к нему обратились;
- other — всё остальное.

Черновик правил для MVP:
- учитель назвал имя ученика и задал вопрос, ученик заговорил в течение N секунд — response;
- реплика ученика заканчивается вопросом — initiative;
- реплика содержит слова темы урока — answer.
Качество ответа оценивается после урока, в сводке LLM, а не здесь.
"""

from app.models import Roster, UtteranceKind


def classify(text: str, student_id: str, t: float, roster: Roster, context: list[dict]) -> UtteranceKind:
    """TODO(Илья, Ярослав): реализовать правила выше и покрыть их тестами."""
    raise NotImplementedError("pipeline/classify.py: classify")
