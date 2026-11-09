"""Тип реплики, правила проверяются по порядку и срабатывает первое"""

import re

from app.models import Roster, Student, UtteranceKind

# Окно ответа после реплики учителя, секунды
ADDRESS_WINDOW_S = 8.0
# Паузы внутри одного хода учителя
TURN_GAP_S = 2.0
# Пауза, после которой реплика ученика всё ещё продолжение
CONTINUE_S = 3.0
ANSWER_MIN_WORDS = 4
# Инициатива это вопрос от трёх слов, не больше двух за урок
INITIATIVE_MIN_WORDS = 3
MAX_INITIATIVES = 2
# Пустые реплики не считаются ни откликом, ни ответом
EMPTY_REPLIES = {
    "да", "нет", "не знаю", "незнаю", "не помню", "ага", "угу", "неа", "не",
    "ну", "э", "эм", "ээ", "мм", "хм", "а", "ой", "ок", "окей", "ладно", "понятно", "ясно",
}
# Основа имени и падежное окончание, чтобы Маша не нашлась в машине
MIN_STEM = 3
ENDINGS = {
    "", "а", "я", "у", "ю", "е", "и", "ы", "ой", "ей", "ом", "ем", "ым", "им", "ою", "ею",
    "ого", "его", "ому", "ему", "ая", "яя", "ую", "юю", "ий", "ый", "ие", "ые", "ых", "их", "ов", "ев",
}

# Краткие формы имён
SHORT_NAMES = {
    "александр": ["саша"],
    "александра": ["саша"],
    "алексей": ["леша"],
    "анастасия": ["настя"],
    # Тёма без ё совпадает со словом тема
    "артем": [],
    "дмитрий": ["дима"],
    "екатерина": ["катя"],
    "елизавета": ["лиза"],
    "иван": ["ваня"],
    "максим": ["макс"],
    "мария": ["маша"],
    "михаил": ["миша"],
    "никита": [],
    "софья": ["соня", "софия"],
}

QUESTION_WORDS = {
    "почему", "зачем", "отчего", "сколько", "кто", "кого", "кому", "кем",
    "чем", "чего", "куда", "откуда", "разве", "неужели", "можно",
    "какой", "какая", "какое", "какие", "каким", "какую", "каких", "какого", "какому", "какими",
    "который", "которая", "которое", "которые",
}
AMBIGUOUS_WORDS = {"как", "что", "когда", "где"}
# Вводные слова перед вопросительным словом
LEAD_WORDS = {"а", "и", "но", "ну", "так", "вот", "то", "есть", "получается", "скажите", "подскажите", "извините"}

_WORD = re.compile(r"[а-яa-z0-9-]+")


def _norm(text: str) -> str:
    return text.lower().replace("ё", "е")


def _words(text: str) -> list[str]:
    return _WORD.findall(_norm(text))


def _names(student: Student) -> list[str]:
    first = _norm((student.full_name or student.name).split()[0])
    return [first, *SHORT_NAMES.get(first, [])]


def _surname(student: Student) -> str | None:
    words = (student.full_name or student.name).split()
    last = _norm(words[-1]).rstrip(".") if len(words) > 1 else ""
    return last if len(last) >= MIN_STEM else None


def _stems(word: str) -> list[str]:
    cut = []
    if word[-1] in "аеиоуыэюяйь":
        cut.append(word[:-1])
    if word[-2:] in ("ий", "ый", "ой", "ая", "яя"):
        cut.append(word[:-2])
    return [word, *(s for s in cut if len(s) >= MIN_STEM)]


def _fits(word: str, base: str) -> bool:
    return any(word.startswith(s) and word[len(s):] in ENDINGS for s in _stems(base))


def _addressed(text: str, student: Student, students: list[Student]) -> bool:
    """Учитель обратился к ученику по фамилии или по имени"""
    words = _words(text)
    surnames = {s.id: sn for s in students if (sn := _surname(s))}
    called = {sid for w in words for sid, sn in surnames.items() if _fits(w, sn)}
    if student.id in called:
        return True
    by_name = [w for w in words if not any(_fits(w, sn) for sn in surnames.values())]
    names = _names(student)
    if not any(_fits(w, n) for w in by_name for n in names):
        return False
    namesakes = {s.id for s in students if s.id != student.id and set(_names(s)) & set(names)}
    return not namesakes & called


def _is_question(text: str) -> bool:
    if "?" in text:
        return True
    words = _words(text)
    if "ли" in words[1:3]:
        return True
    rest = words
    while rest and rest[0] in LEAD_WORDS:
        rest = rest[1:]
    if not rest:
        return False
    return rest[0] in QUESTION_WORDS or (rest[0] in AMBIGUOUS_WORDS and "," not in text)


def _end(item: dict) -> float:
    return item.get("end", item["t"])


def _is_empty(text: str) -> bool:
    return " ".join(_words(text)) in EMPTY_REPLIES


def _teacher_turn(context: list[dict], teacher_id: str, before: float, names_student=None) -> list[dict]:
    turn: list[dict] = []
    for item in reversed(context):
        if item["t"] >= before:
            continue
        if item["student_id"] != teacher_id:
            if turn:
                break
            continue
        gap = turn[0]["t"] - _end(item) if turn else 0.0
        if gap > TURN_GAP_S and not (gap <= ADDRESS_WINDOW_S and names_student and names_student(item["text"])):
            break
        turn.insert(0, item)
    return turn


def _initiatives(context: list[dict], student_id: str) -> int:
    count = 0
    prev = None
    for item in context:
        own = item["student_id"] == student_id
        continues = (own and prev is not None and prev["student_id"] == student_id
                     and item["t"] - _end(prev) <= CONTINUE_S)
        if own and item.get("kind") == "initiative" and not continues:
            count += 1
        prev = item
    return count


def classify(text: str, student_id: str, t: float, roster: Roster, context: list[dict]) -> UtteranceKind:
    teacher_id = roster.teacher.id
    student = next((s for s in roster.students if s.id == student_id), None)
    earlier = [item for item in context if item["t"] < t]

    turn = _teacher_turn(
        earlier, teacher_id, t,
        lambda phrase: any(_addressed(phrase, s, roster.students) for s in roster.students),
    )
    turn_end = _end(turn[-1]) if turn else None
    after_turn = [item for item in earlier if turn_end is not None and item["t"] >= turn_end]
    others_spoke = any(item["student_id"] not in (teacher_id, student_id) for item in after_turn)
    in_window = turn_end is not None and t - turn_end <= ADDRESS_WINDOW_S
    turn_text = " ".join(item["text"] for item in turn)

    # Отклик, учитель назвал ученика
    empty = _is_empty(text)
    if in_window and not others_spoke and not empty and student and _addressed(turn_text, student, roster.students):
        return "response"

    # Инициатива
    stripped = text.strip()
    words = _words(stripped)
    asked = _initiatives(earlier, student_id)
    own_question = _is_question(stripped) and (len(words) >= INITIATIVE_MIN_WORDS or words[:2] == ["можно", "я"])
    if asked < MAX_INITIATIVES and (own_question or _norm(stripped).startswith(_norm(roster.teacher.name))):
        return "initiative"

    # Ответ на вопрос учителя
    if in_window and any(_is_question(item["text"]) for item in turn) and (
        len(words) >= ANSWER_MIN_WORDS or (words and not empty and not others_spoke)
    ):
        return "answer"

    # Продолжение своей прошлой реплики
    if earlier and earlier[-1]["student_id"] == student_id and t - _end(earlier[-1]) <= CONTINUE_S:
        prev = earlier[-1]
        return prev.get("kind") or classify(prev["text"], student_id, prev["t"], roster, earlier[:-1])

    return "other"
