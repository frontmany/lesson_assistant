"""Тип реплики. Правила — #19.

Типы (app.models.UtteranceKind):
- answer — содержательный ответ по теме;
- initiative — сам задал вопрос или вызвался;
- response — ответил, когда к нему обратились;
- other — всё остальное.

Правила проверяются по порядку, срабатывает первое:
1. response — в последней реплике учителя, закончившейся не раньше чем за
   ADDRESS_WINDOW_S секунд до начала реплики ученика, есть имя или фамилия
   этого ученика в любом падеже, и между ними не говорил другой ученик.
   «Не знаю», «да», «нет» и междометия (EMPTY_REPLIES) откликом не считаются (#98, п. 9).
   Фамилию берём из полного имени в базе (#76); при тёзках и прозвучавшей
   фамилии одного из них отклик только у него;
2. initiative — реплика вопрос (см. «Вопрос» ниже) от INITIATIVE_MIN_WORDS слов
   (кроме «Можно я…») или начинается с обращения к учителю по имени-отчеству.
   Не больше MAX_INITIATIVES за урок: дальше вопросы ученика — other (#98, п. 10);
3. answer — реплика начинается не позже чем через ADDRESS_WINDOW_S секунд
   после вопроса учителя, и в ней не меньше ANSWER_MIN_WORDS слов. Короче —
   тоже ответ («Колумб.»), если между вопросом и ответом никто из учеников
   не говорил и это не «да», «нет», «не знаю» или междометие (#98, п. 9);
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

from app.models import Roster, Student, UtteranceKind

# Сколько секунд после конца реплики учителя ответ ещё считается ответом на неё
ADDRESS_WINDOW_S = 8.0
# Паузы внутри одной реплики учителя: его фразы с такими паузами — один ход.
# Фраза с именем ученика («Иван.» — пауза — «Какой налог…?») входит в ход и через
# паузу подольше, пока она не длиннее ADDRESS_WINDOW_S (#98, п. 11)
TURN_GAP_S = 2.0
# Пауза, после которой реплика того же ученика всё ещё продолжение прошлой
CONTINUE_S = 3.0
ANSWER_MIN_WORDS = 4
# Инициатива: вопрос от трёх слов («Можно выйти?», «Что?» — не инициатива), но
# «Можно я…» — да; и не больше двух за урок, чтобы болтовня не выводила на 4-ю ступень
INITIATIVE_MIN_WORDS = 3
MAX_INITIATIVES = 2
# Пустые реплики: ни отклик, ни ответ
EMPTY_REPLIES = {
    "да", "нет", "не знаю", "незнаю", "не помню", "ага", "угу", "неа", "не",
    "ну", "э", "эм", "ээ", "мм", "хм", "а", "ой", "ок", "окей", "ладно", "понятно", "ясно",
}
# Обращение: основа имени или фамилии не короче MIN_STEM букв и падежное окончание.
# Именно окончание, а не «любые буквы»: иначе «Маша» нашлась бы в «машине», «Катя» — в «катере»
MIN_STEM = 3
ENDINGS = {
    "", "а", "я", "у", "ю", "е", "и", "ы", "ой", "ей", "ом", "ем", "ым", "им", "ою", "ею",
    "ого", "его", "ому", "ему", "ая", "яя", "ую", "юю", "ий", "ый", "ие", "ые", "ых", "их", "ов", "ев",
}

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


def _names(student: Student) -> list[str]:
    """«Иван Петров» → ["иван", "ваня"]: имя без фамилии и его краткие формы."""
    first = _norm((student.full_name or student.name).split()[0])
    return [first, *SHORT_NAMES.get(first, [])]


def _surname(student: Student) -> str | None:
    """«Иван Петров» → «петров». У «Иван П.» фамилии нет: в составе урока только инициал."""
    words = (student.full_name or student.name).split()
    last = _norm(words[-1]).rstrip(".") if len(words) > 1 else ""
    return last if len(last) >= MIN_STEM else None


def _stems(word: str) -> list[str]:
    """Основы для падежей: «петрова» → «петров», «мария» → «мари»,
    «дмитрий» → «дмитри» (Дмитрия), «чайковский» → «чайковск» (Чайковского)."""
    stems = [word]
    if word[-1] in "аеиоуыэюяйь":
        stems.append(word[:-1])
    if word[-2:] in ("ий", "ый", "ой", "ая", "яя"):
        stems.append(word[:-2])
    return [s for s in stems if len(s) >= MIN_STEM]


def _fits(word: str, base: str) -> bool:
    # «Петров», «Петрова», «Петровым», «Марию», «Чайковского»: основа и падежное окончание
    return any(word.startswith(s) and word[len(s):] in ENDINGS for s in _stems(base))


def _addressed(text: str, student: Student, students: list[Student]) -> bool:
    """Учитель обратился к ученику: по фамилии или по имени (#76).

    Слово, похожее на чью-то фамилию, — обращение по фамилии, а не по имени:
    «Иванов» не зовёт Ивана. Если в классе тёзки и прозвучала фамилия одного
    из них, по имени зовут его, а не остальных. Одно имя без фамилии при
    тёзках — отклик тому, кто ответил.
    """
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
    """Последний ход учителя до момента before: его фразы подряд без чужих реплик между ними.

    names_student(text) — есть ли во фразе обращение к ученику: такая фраза входит
    в ход и через паузу дольше TURN_GAP_S, но не дольше ADDRESS_WINDOW_S.
    """
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

    # 1. Отклик: учитель назвал ученика по имени или по фамилии
    empty = _is_empty(text)
    if in_window and not others_spoke and not empty and student and _addressed(turn_text, student, roster.students):
        return "response"

    # 2. Инициатива: свой вопрос от трёх слов или «Можно я…», обращение к учителю; не больше двух за урок
    stripped = text.strip()
    words = _words(stripped)
    asked = sum(1 for item in earlier if item["student_id"] == student_id and item.get("kind") == "initiative")
    own_question = _is_question(stripped) and (len(words) >= INITIATIVE_MIN_WORDS or words[:2] == ["можно", "я"])
    if asked < MAX_INITIATIVES and (own_question or _norm(stripped).startswith(_norm(roster.teacher.name))):
        return "initiative"

    # 3. Ответ на вопрос учителя: от четырёх слов или коротко, но сразу и по делу
    if in_window and any(_is_question(item["text"]) for item in turn) and (
        len(words) >= ANSWER_MIN_WORDS or (words and not empty and not others_spoke)
    ):
        return "answer"

    # 4. Продолжение своей прошлой реплики
    if earlier and earlier[-1]["student_id"] == student_id and t - _end(earlier[-1]) <= CONTINUE_S:
        prev = earlier[-1]
        return prev.get("kind") or classify(prev["text"], student_id, prev["t"], roster, earlier[:-1])

    return "other"
