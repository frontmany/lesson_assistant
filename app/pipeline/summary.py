"""Сводка учителю и карточки ученикам. Промпты — Илья, вызов LLM — Фёдор.

Один запрос к LLM на весь класс (промпт в prompts/teacher_summary.md).
Без LLM_API_KEY сводка строится по шаблону из результатов скоринга,
чтобы демо работало всегда. Цель: готово через 30–45 секунд после урока.
"""

from app.models import StudentCard, Summary


def build_summary(lesson_id: str, utterances: list[dict], levels: dict[str, int]) -> Summary:
    """TODO(Фёдор): вызов LLM по prompts/teacher_summary.md и шаблонный запасной вариант."""
    raise NotImplementedError("pipeline/summary.py: build_summary")


def build_card(lesson_id: str, student_id: str, utterances: list[dict], level: int) -> StudentCard:
    """TODO(Фёдор): карточка по prompts/student_card.md, тон нейтральный, только рост."""
    raise NotImplementedError("pipeline/summary.py: build_card")
