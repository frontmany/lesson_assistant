"""Тип реплики. Правила — Илья (#19), код — Ярослав.

Типы (app.models.UtteranceKind):
- answer — содержательный ответ по теме;
- initiative — сам задал вопрос или вызвался;
- response — ответил, когда к нему обратились;
- other — всё остальное.

Правила проверяются по порядку, срабатывает первое:
1. response — в последней реплике учителя, закончившейся не раньше чем за
   ADDRESS_WINDOW_S секунд до начала реплики ученика, есть имя этого ученика,
   и между ними не говорил другой ученик;
2. initiative — реплика вопрос (см. «Вопрос» ниже) или начинается с обращения
   к учителю по имени-отчеству;
3. answer — реплика начинается не позже чем через ADDRESS_WINDOW_S секунд
   после вопроса учителя, и в ней не меньше ANSWER_MIN_WORDS слов;
4. продолжение — ученик снова заговорил не позже чем через CONTINUE_S секунд
   после своей прошлой реплики: тип берётся от неё. Длинный ответ делится
   на фразы по паузам, и без этого правила его хвост стал бы other;
5. other — всё остальное.

Вопрос — фраза со знаком «?» или с вопросительным словом в начале. Whisper
ставит «?» по интонации и на живой речи часто его теряет, поэтому одного
знака мало. Вопросительное слово ищется после вводных («а», «ну», «то есть»…):
- QUESTION_WORDS почти не начинают ответ: «почему», «зачем», «сколько»…;
- AMBIGUOUS_WORDS («как», «что», «когда», «где») начинают и ответ:
  «Когда Пётр вернулся, он…». Они считаются вопросом, только если во фразе
  нет запятой, то есть это не придаточное в начале ответа;
- частица «ли» в первых трёх словах: «Правильно ли я понял…».
Вопрос «да/нет» без вопросительного слова («Это правда работает») узнаётся
только по «?».

Слова темы урока не нужны: связь с вопросом учителя надёжнее, а предмет
системе знать не нужно. Качество ответа оценивается после урока, в сводке LLM.

context — реплики урока по порядку: {"student_id", "t", "text"} и, если есть,
"end" (конец реплики, секунды) и "kind" (тип, для реплик учеников). Реплики
учителя лежат там же, с id учителя. Без "end" конец реплики считается равным
её началу.
"""

import re

from app.models import Roster, UtteranceKind

# Сколько секунд после конца реплики учителя ответ ещё считается ответом на неё
ADDRESS_WINDOW_S = 8.0
# Паузы внутри одной реплики учителя: его фразы с такими паузами — один ход
TURN_GAP_S = 2.0
# Пауза, после которой реплика того же ученика всё ещё продолжение прошлой
CONTINUE_S = 3.0
ANSWER_MIN_WORDS = 4

# Краткие формы имён: учитель редко зовёт ученика полным именем
SHORT_NAMES = {
    "александр": ["саша"],
    "александра": ["саша"],
    "алексей": ["леша"],
    "анастасия": ["настя"],
    # «Тёма» не берём: без «ё» это обычное слово «тема»
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
# Вводные перед вопросительным словом: «А почему…», «То есть как…»
LEAD_WORDS = {"а", "и", "но", "ну", "так", "вот", "то", "есть", "получается", "скажите", "подскажите", "извините"}

_WORD = re.compile(r"[а-яa-z0-9-]+")


def _norm(text: str) -> str:
    return text.lower().replace("ё", "е")


def _words(text: str) -> list[str]:
    return _WORD.findall(_norm(text))


def _names(full_name: str) -> list[str]:
    """«Артём К.» → ["артем", "тема"]: имя без фамилии и его краткие формы."""
    first = _norm(full_name.split()[0])
    return [first, *SHORT_NAMES.get(first, [])]


def _mentions(text: str, names: list[str]) -> bool:
    # «Артём», «Артёма», «Артёму»: имя с окончанием до двух букв
    return any(w.startswith(n) and len(w) - len(n) <= 2 for w in _words(text) for n in names)


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


def _teacher_turn(context: list[dict], teacher_id: str, before: float) -> list[dict]:
    """Последний ход учителя до момента before: его фразы подряд без чужих реплик между ними."""
    turn: list[dict] = []
    for item in reversed(context):
        if item["t"] >= before:
            continue
        if item["student_id"] != teacher_id:
            if turn:
                break
            continue
        if turn and turn[0]["t"] - _end(item) > TURN_GAP_S:
            break
        turn.insert(0, item)
    return turn


def classify(text: str, student_id: str, t: float, roster: Roster, context: list[dict]) -> UtteranceKind:
    teacher_id = roster.teacher.id
    student = next((s for s in roster.students if s.id == student_id), None)
    earlier = [item for item in context if item["t"] < t]

    turn = _teacher_turn(earlier, teacher_id, t)
    turn_end = _end(turn[-1]) if turn else None
    after_turn = [item for item in earlier if turn_end is not None and item["t"] >= turn_end]
    others_spoke = any(item["student_id"] not in (teacher_id, student_id) for item in after_turn)
    in_window = turn_end is not None and t - turn_end <= ADDRESS_WINDOW_S
    turn_text = " ".join(item["text"] for item in turn)

    # 1. Отклик: учитель назвал ученика по имени
    if in_window and not others_spoke and student and _mentions(turn_text, _names(student.name)):
        return "response"

    # 2. Инициатива: свой вопрос или обращение к учителю
    stripped = text.strip()
    if _is_question(stripped) or _norm(stripped).startswith(_norm(roster.teacher.name)):
        return "initiative"

    # 3. Ответ на вопрос учителя
    if in_window and any(_is_question(item["text"]) for item in turn) and len(_words(text)) >= ANSWER_MIN_WORDS:
        return "answer"

    # 4. Продолжение своей прошлой реплики
    if earlier and earlier[-1]["student_id"] == student_id and t - _end(earlier[-1]) <= CONTINUE_S:
        prev = earlier[-1]
        return prev.get("kind") or classify(prev["text"], student_id, prev["t"], roster, earlier[:-1])

    return "other"
