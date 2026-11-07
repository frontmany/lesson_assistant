"""Сводка учителю и карточки ученикам (#21). Промпты — Илья, вызов LLM — Фёдор.

Два запроса к GigaChat на урок: сводка по prompts/teacher_summary.md и карточки
всего класса по prompts/student_card.md. Ответ проверяется по моделям Summary
и StudentCard: чужие id, лишние пункты и пустые строки отбрасываются. Без
LLM_API_KEY, при ошибке GigaChat или ответе не по схеме сводка и карточки
строятся по шаблону из ступеней урока, чтобы демо работало всегда.
"""

import json
import logging
import re

from pydantic import ValidationError

from app.config import ROOT
from app.models import AskNext, Roster, StudentCard, Summary
from app.pipeline import llm

log = logging.getLogger(__name__)

PROMPTS = ROOT / "prompts"
SYSTEM = "Ты помощник учителя. Отвечай только JSON по заданной схеме, на русском языке."
KIND_LABELS = {"answer": "ответ", "initiative": "инициатива", "response": "отклик на обращение", "other": "прочее"}
MAX_CARRIED = 3
MAX_TOPICS = 3
MAX_ASK = 3
MAX_POINTS = 2

# ступень, баллы и флаги ученика к концу урока: как их считает runner.finish_lesson
Levels = dict[str, tuple[int, float, list[str]]]


def prompt(name: str, **fields: str) -> str:
    """Текст промпта после черты «---» с подставленными полями.

    Подставляем заменой, а не str.format: в промпте есть фигурные скобки примера JSON.
    """
    text = (PROMPTS / name).read_text(encoding="utf-8").split("\n---\n", 1)[1]
    for key, value in fields.items():
        text = text.replace("{" + key + "}", value)
    return text.strip()


def _clock(seconds: float) -> str:
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"


def transcript(roster: Roster, utterances: list[dict]) -> str:
    """Реплики урока строками: «[3:05] Иван И. (ivan_i, ответ): …», у учителя без типа."""
    names = {s.id: s.name for s in roster.students}
    teachers = {roster.teacher.id} | ({roster.virtual_teacher.id} if roster.virtual_teacher else set())
    lines = []
    for u in utterances:
        who = u["student_id"]
        if who in teachers or "kind" not in u:
            speaker = "Учитель"
        else:
            speaker = f"{names.get(who, 'Ученик')} ({who}, {KIND_LABELS.get(u['kind'], 'прочее')})"
        lines.append(f"[{_clock(u['t'])}] {speaker}: {u['text']}")
    return "\n".join(lines) or "(реплик нет)"


def _strings(items, limit: int) -> list[str]:
    out = [re.sub(r"\s+", " ", str(i)).strip().rstrip(".") for i in items or [] if str(i).strip()]
    return out[:limit]


# ---------- сводка ----------


def build_summary(roster: Roster, utterances: list[dict], levels: Levels, streaks: dict[str, int]) -> Summary:
    """Сводка учителю: GigaChat, а без него или при ошибке — шаблон."""
    if llm.enabled():
        try:
            return _llm_summary(roster, utterances, levels, streaks)
        except llm.LlmError as exc:
            log.warning("сводка урока %s по шаблону: %s", roster.lesson_id, exc)
        except Exception:
            # ответ не той формы (строка вместо списка, словарь вместо id): без сводки учителя не оставляем
            log.exception("сводка урока %s по шаблону: ответ GigaChat не разобрался", roster.lesson_id)
    return template_summary(roster, utterances, levels, streaks)


def _llm_summary(roster: Roster, utterances: list[dict], levels: Levels, streaks: dict[str, int]) -> Summary:
    names = {s.id: s.name for s in roster.students if s.id in levels}
    text = prompt(
        "teacher_summary.md",
        class_name=roster.class_name,
        subject=roster.subject,
        topic=roster.topic or "не указана, определи по транскрипту",
        students="; ".join(f"{sid} — {name}" for sid, name in names.items()),
        levels="; ".join(f"{sid}: {levels[sid][0]}" for sid in names),
        history="; ".join(f"{sid}: {n}" for sid, n in streaks.items() if sid in names) or "истории нет",
        utterances=transcript(roster, utterances),
    )
    data = _ask(text)
    try:
        ask = [AskNext.model_validate(a) for a in data.get("ask_next") or [] if isinstance(a, dict)]
    except ValidationError as exc:
        raise llm.LlmError(f"ask_next не по схеме: {exc}") from exc

    def known(ids) -> list[str]:
        return [i for i in dict.fromkeys(ids or []) if i in names]

    return Summary(
        lesson_id=roster.lesson_id,
        carried=known(data.get("carried"))[:MAX_CARRIED],
        dropped=known(data.get("dropped")),
        weak_topics=_strings(data.get("weak_topics"), MAX_TOPICS),
        ask_next=[
            a.model_copy(update={"topic": a.topic.strip() or roster.topic, "question": a.question.strip()})
            for a in ask if a.student_id in names and a.question.strip()
        ][:MAX_ASK],
    )


def _ask(text: str) -> dict:
    """Запрос с одной повторной попыткой: GigaChat изредка отвечает не JSON."""
    try:
        return llm.chat_json(SYSTEM, text)
    except llm.LlmError as exc:
        log.info("GigaChat ответил не по схеме, повторяю: %s", exc)
        return llm.chat_json(SYSTEM, text)


def template_summary(roster: Roster, utterances: list[dict], levels: Levels, streaks: dict[str, int]) -> Summary:
    """Сводка без LLM: по ступеням урока. Слабые темы так не определить — их нет."""
    ids = [s.id for s in roster.students if s.id in levels]
    by_score = sorted(ids, key=lambda sid: -levels[sid][1])
    carried = [sid for sid in by_score if levels[sid][0] == 4][:MAX_CARRIED]
    dropped = [sid for sid in ids if levels[sid][0] == 1]
    # спросить тех, кто выпал, начиная с тех, кто молчит дольше; вопрос — из вопросов учителя,
    # без обращения к другому ученику: «Иван, какой налог…?» Дмитрию задать как есть нельзя
    questions = [
        q for q in (_question_for_class(u["text"], roster) for u in utterances if "kind" not in u and u["text"].strip().endswith("?"))
        if q
    ]
    ask = sorted(dropped, key=lambda sid: -streaks.get(sid, 0))[:MAX_ASK]
    return Summary(
        lesson_id=roster.lesson_id,
        carried=carried,
        dropped=dropped,
        weak_topics=[],
        ask_next=[
            AskNext(student_id=sid, topic=roster.topic or roster.subject, question=q)
            for sid, q in zip(ask, reversed(questions))
        ],
    )


# обращение к классу в начале вопроса учителя: «Ребята, кто знает…?» → «Кто знает…?»
ADDRESS_WORDS = {"ребята", "дети", "класс", "друзья", "итак", "так", "хорошо", "ладно", "верно", "отлично"}


def _question_for_class(text: str, roster: Roster) -> str:
    """Вопрос учителя, который можно задать любому: без обращения в начале и без имён учеников.

    «Иван, какой налог Пётр ввёл вместо подворного?» → «Какой налог Пётр ввёл вместо подворного?».
    Обращение снимается, только если это имя ученика или слово вроде «Ребята»:
    «Пётр Первый, когда родился?» остаётся как есть.
    Пусто — вопрос не подходит: короткий («Вопросы есть?») или называет ученика внутри фразы,
    в любом падеже («Что Ивана спросили?»).
    """
    stems = [_name_stem(s.name) for s in roster.students]
    stems = [x for x in stems if len(x) >= 3]

    def is_student(word: str) -> bool:
        w = _norm_word(word)
        return any(w.startswith(x) and len(w) - len(x) <= 3 for x in stems)

    q = _last_sentence(text)
    head, comma, rest = q.partition(",")
    words = head.split()
    if comma and rest.strip() and 1 <= len(words) <= 2 and all(
        is_student(w) or _norm_word(w) in ADDRESS_WORDS for w in words
    ):
        rest = rest.strip()
        q = rest[0].upper() + rest[1:]
    if len(q.split()) < 3 or any(is_student(w) for w in re.findall(r"\w+", q)):
        return ""
    return q


def _name_stem(name: str) -> str:
    """Основа имени для поиска в любом падеже: «Мария» → «мари», «Дмитрий» → «дмитри», «Иван» → «иван»."""
    first = _norm_word(name.split()[0]) if name.split() else ""
    return first[:-1] if first[-1:] in "аяйьоеи" else first


def _norm_word(word: str) -> str:
    return word.lower().replace("ё", "е")


def _last_sentence(text: str) -> str:
    """Из реплики учителя — сам вопрос: «Верно. А флот? Артём, что Пётр сделал с флотом?» → последнее предложение."""
    return re.split(r"(?<=[.!?])\s+", text.strip())[-1]


# ---------- карточки ----------


def build_cards(roster: Roster, utterances: list[dict], levels: Levels) -> list[StudentCard]:
    """Карточки ученикам, у которых аналитика была включена. Пустые не отдаём."""
    cards: dict[str, StudentCard] = {}
    if llm.enabled():
        try:
            cards = _llm_cards(roster, utterances, levels)
        except llm.LlmError as exc:
            log.warning("карточки урока %s по шаблону: %s", roster.lesson_id, exc)
        except Exception:
            log.exception("карточки урока %s по шаблону: ответ GigaChat не разобрался", roster.lesson_id)
    template = {c.student_id: c for c in template_cards(roster, utterances, levels)}
    # кого модель пропустила, тем — шаблонная карточка
    merged = [cards.get(sid) or template.get(sid) for sid in levels]
    return [c for c in merged if c is not None and (c.went_well or c.to_repeat)]


def _said(utterances: list[dict], sid: str) -> list[str]:
    return [u["text"] for u in utterances if u["student_id"] == sid]


def _llm_cards(roster: Roster, utterances: list[dict], levels: Levels) -> dict[str, StudentCard]:
    names = {s.id: s.name for s in roster.students if s.id in levels}
    students = "\n".join(
        f"{sid} — {name} — " + (" / ".join(_said(utterances, sid)) or "молчал весь урок")
        for sid, name in names.items()
    )
    text = prompt(
        "student_card.md",
        subject=roster.subject,
        topic=roster.topic or "не указана, определи по транскрипту",
        utterances=transcript(roster, utterances),
        students=students,
    )
    cards = {}
    for item in _ask(text).get("cards") or []:
        if not isinstance(item, dict) or item.get("student_id") not in names:
            continue
        sid = item["student_id"]
        cards[sid] = StudentCard(
            lesson_id=roster.lesson_id,
            student_id=sid,
            level=levels[sid][0],
            # хвалить молчавшего не за что: такую похвалу модель придумала
            went_well=_strings(item.get("went_well"), MAX_POINTS) if _said(utterances, sid) else [],
            to_repeat=_strings(item.get("to_repeat"), MAX_POINTS),
        )
    return cards


def template_cards(roster: Roster, utterances: list[dict], levels: Levels) -> list[StudentCard]:
    """Карточки без LLM: засчитанные реплики ученика и тема урока."""
    labels = {"answer": "Ответ", "initiative": "Свой вопрос", "response": "Ответ, когда к тебе обратились"}
    cards = []
    for sid in levels:
        replies = [u for u in utterances if u["student_id"] == sid and u.get("kind") in labels]
        cards.append(StudentCard(
            lesson_id=roster.lesson_id,
            student_id=sid,
            level=levels[sid][0],
            went_well=[f"{labels[u['kind']]}: «{_short(u['text'])}»" for u in replies[:MAX_POINTS]],
            to_repeat=[roster.topic] if roster.topic else [],
        ))
    return cards


def _short(text: str, words: int = 8) -> str:
    parts = text.split()
    return " ".join(parts[:words]) + ("…" if len(parts) > words else "")


def dump(summary: Summary, cards: list[StudentCard]) -> str:
    """Сводка и карточки одним JSON: для примеров ответов в задаче."""
    return json.dumps(
        {"summary": summary.model_dump(), "cards": [c.model_dump() for c in cards]}, ensure_ascii=False, indent=2,
    )
