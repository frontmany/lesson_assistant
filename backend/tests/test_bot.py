"""Бот MAX без сети: обновления на входе, сообщения на выходе (#44, шаги — design/bot-registration.html)."""

import pytest

from app.bot import max_bot
from app.bot.max_bot import Bot, students_count
from app.db import classes as db

BOT = "ideya_fiks_161_bot"
TEACHER, IVAN, MARIA = 100, 111, 222


class FakeApi:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str, list | None]] = []
        self.answers: list[tuple[str, str]] = []
        self.edits: list[tuple[str, str, list | None]] = []

    def send(self, user_id, text, buttons=None, markdown=False):
        self.sent.append((user_id, text, buttons))
        return f"mid{len(self.sent)}"

    def edit(self, mid, text, buttons=None, markdown=False, image=None):
        self.edits.append((mid, text, buttons))

    def answer(self, callback_id, notification=None, message=None, buttons=None, markdown=False, image=None, user_id=None):
        self.answers.append((callback_id, notification or message))
        if message is not None and user_id is not None:
            # нажатое сообщение заменено следующим шагом: в переписке человека это его последнее сообщение
            self.sent.append((user_id, message, buttons))

    uploads = 0
    upload_fails = False

    def upload_image(self, path):
        if self.upload_fails:
            raise OSError("MAX не принял картинку")
        self.uploads += 1
        assert path.exists()
        return f"token-{self.uploads}"

    def send_image(self, user_id, token, text, buttons=None):
        self.sent.append((user_id, text, buttons))
        self.images = [*getattr(self, "images", []), (user_id, token)]
        return f"mid{len(self.sent)}"

    def to(self, user_id):
        return [(text, buttons) for u, text, buttons in self.sent if u == user_id]


def user(user_id, first="Иван", last="Иванов"):
    return {"user_id": user_id, "first_name": first, "last_name": last, "is_bot": False}


def started(user_id, payload=None, **name):
    return {"update_type": "bot_started", "user": user(user_id, **name), "payload": payload}


def text(user_id, body, is_bot=False, **name):
    return {"update_type": "message_created",
            "message": {"sender": {**user(user_id, **name), "is_bot": is_bot}, "body": {"text": body}}}


def press(user_id, payload, mid=None, **name):
    update = {"update_type": "message_callback",
              "callback": {"callback_id": "cb", "payload": payload, "user": user(user_id, **name)}}
    if mid:
        update["message"] = {"body": {"mid": mid}}  # сообщение, где нажали кнопку
    return update


def buttons(message):
    return [b for row in (message[1] or []) for b in row]


def payload_of(message, title):
    return next(b.get("payload") for b in buttons(message) if b["text"] == title)


@pytest.fixture
def bot():
    api = FakeApi()
    return Bot(api, BOT), api


def make_class(b, title="10А, история"):
    teacher = {"first": "Елена", "last": "Викторовна"}
    b.handle(press(TEACHER, "teacher", **teacher))
    b.handle(text(TEACHER, title, **teacher))
    return db.classes_of(TEACHER)[-1][0]


def join(b, user_id, code, **name):
    b.handle(started(user_id, code, **name))
    b.handle(press(user_id, f"me:{db.find_by_code(code)[1].id}", **name))


def test_stranger_chooses_teacher_student_or_demo(bot):
    b, api = bot
    b.handle(started(MARIA))
    b.handle(press(MARIA, "begin"))  # «Начать» под вступлением
    message = api.to(MARIA)[-1]
    assert message[0] == "Здравствуйте! Я помогаю вести уроки в MAX. Кто вы?"
    assert [x["text"] for x in buttons(message)] == ["Я учитель", "Я ученик", "Посмотреть демо"]
    # демо открывается в мини-приложении сразу, без второго сообщения
    assert buttons(message)[2]["type"] == "open_app" and buttons(message)[2]["payload"] == "demo"


def test_teacher_creates_class_and_gets_code_and_link(bot):
    b, api = bot
    b.handle(press(TEACHER, "teacher"))
    assert api.to(TEACHER)[-1][0] == "Создадим первый класс. Напишите название и предмет через запятую. Например: 10А, история."
    b.handle(text(TEACHER, "10А,  История "))
    c = db.classes_of(TEACHER)[0][0]
    assert (c.name, c.subject) == ("10А", "история")
    created, invite = api.to(TEACHER)[-2:]
    assert created[0] == "Создан класс 10А, история."
    assert f"https://max.ru/{BOT}?start={c.invite_code}" in invite[0]
    assert f"Код класса: {c.invite_code}" in invite[0]


def test_back_returns_to_role_choice(bot):
    b, api = bot
    b.handle(press(MARIA, "teacher"))
    assert [x["text"] for x in buttons(api.to(MARIA)[-1])] == ["Назад"]
    b.handle(press(MARIA, "back"))
    assert api.to(MARIA)[-1][0] == "Здравствуйте! Я помогаю вести уроки в MAX. Кто вы?"
    # бот больше не ждёт название: обычный текст не создаёт класс
    b.handle(text(MARIA, "10А, история"))
    assert db.classes_of(MARIA) == []
    b.handle(press(MARIA, "student"))
    b.handle(press(MARIA, "back"))
    assert api.to(MARIA)[-1][0] == "Здравствуйте! Я помогаю вести уроки в MAX. Кто вы?"


def test_student_who_pressed_teacher_sends_code(bot):
    b, api = bot
    c = make_class(b)
    b.handle(press(IVAN, "teacher"))
    b.handle(text(IVAN, c.invite_code))
    assert api.to(IVAN)[-1][0] == "Вы входите в класс 10А, история. Вас зовут Иван Иванов?"
    assert len(db.classes_of(TEACHER)) == 1 and db.classes_of(IVAN) == []


def test_class_name_without_subject_is_asked_again(bot):
    b, api = bot
    b.handle(press(TEACHER, "teacher"))
    b.handle(text(TEACHER, "10А"))
    assert "через запятую" in api.to(TEACHER)[-1][0]
    assert db.classes_of(TEACHER) == []


def test_student_joins_by_link_and_teacher_is_notified(bot):
    b, api = bot
    c = make_class(b)
    b.handle(started(IVAN, c.invite_code))
    question = api.to(IVAN)[-1]
    assert question[0] == "Вы входите в класс 10А, история. Вас зовут Иван Иванов?"
    b.handle(press(IVAN, payload_of(question, "Это я")))
    assert api.to(IVAN)[-1][0] == "Готово, вы в классе 10А, история. Когда начнётся урок, я напишу."
    note = api.to(TEACHER)[-1]
    assert note[0] == "Иван И. теперь в классе 10А. Можно начинать урок."
    assert [x["text"] for x in buttons(note)] == ["Начать урок", "Убрать из класса"]
    assert buttons(note)[0]["payload"] == f"c{c.id}"


def test_student_types_code_with_russian_lookalikes(bot):
    b, api = bot
    c = make_class(b)
    b.handle(press(IVAN, "student"))
    assert api.to(IVAN)[-1][0] == "Введите код класса. Его назовёт учитель."
    b.handle(text(IVAN, "ZZZZZZ"))
    assert api.to(IVAN)[-1][0] == "Такого кода нет. Проверьте и введите ещё раз."
    lookalike = c.invite_code.lower().translate(str.maketrans("abekmhpctyx", "авекмнрстух"))
    b.handle(text(IVAN, lookalike))
    assert api.to(IVAN)[-1][0].startswith("Вы входите в класс 10А")


def test_code_readings():
    assert "K7X2M9" in db.code_candidates("Л7Ч2Ь9")  # русская раскладка
    assert "K7X2M9" in db.code_candidates("к7х2м9")  # похожие русские буквы
    assert "K7X2M9" in db.code_candidates(" k7x-2m9 ")


def test_student_can_change_name(bot):
    b, api = bot
    c = make_class(b)
    b.handle(started(IVAN, c.invite_code))
    b.handle(press(IVAN, f"rename:{c.id}"))
    assert api.to(IVAN)[-1][0] == "Напишите имя и фамилию."
    b.handle(text(IVAN, "Ваня Петров"))
    assert db.students_of(c.id)[0].name == "Ваня Петров"
    assert api.to(TEACHER)[-1][0].startswith("Ваня П. теперь в классе 10А.")


def test_teacher_removes_and_returns_student(bot):
    b, api = bot
    c = make_class(b)
    join(b, IVAN, c.invite_code)
    b.handle(press(TEACHER, payload_of(api.to(TEACHER)[-1], "Убрать из класса")))
    assert db.students_of(c.id) == []
    removed = api.to(TEACHER)[-1]
    assert removed[0] == "Убрано из класса 10А, история: Иван И."
    b.handle(press(TEACHER, payload_of(removed, "Вернуть")))
    assert [m.name for m in db.students_of(c.id)] == ["Иван Иванов"]


def test_only_teacher_can_remove(bot):
    b, _ = bot
    c = make_class(b)
    join(b, IVAN, c.invite_code)
    b.handle(press(MARIA, f"kick:{db.students_of(c.id)[0].id}"))
    assert db.students_of(c.id) != []


def test_student_leaves_and_teacher_learns(bot):
    b, api = bot
    c = make_class(b)
    join(b, IVAN, c.invite_code)
    b.handle(text(IVAN, "привет"))
    known = api.to(IVAN)[-1]
    assert known[0] == "Вы в классе 10А, история. Урок пока не идёт. Я напишу, когда начнётся."
    b.handle(press(IVAN, payload_of(known, "Выйти из класса")))
    assert api.to(IVAN)[-1][0] == "Выйти из класса 10А, история?"
    b.handle(press(IVAN, f"leave_yes:{c.id}"))
    assert api.to(IVAN)[-1][0] == "Вы вышли из 10А. Чтобы вернуться, попросите у учителя ссылку."
    assert api.to(TEACHER)[-1][0] == "Иван И. теперь не в классе 10А."
    assert db.students_of(c.id) == []


def test_new_link_makes_old_code_outdated(bot):
    b, api = bot
    c = make_class(b)
    old = c.invite_code
    b.handle(press(TEACHER, f"newlink:{c.id}"))
    assert api.to(TEACHER)[-1][0] == "Создать новую ссылку и код для 10А, история? Старые перестанут работать."
    b.handle(press(TEACHER, f"newlink_yes:{c.id}"))
    new = db.get_class(c.id).invite_code
    assert new != old and f"Новый код: {new}" in api.to(TEACHER)[-1][0]
    b.handle(started(IVAN, old))
    outdated = api.to(IVAN)[-1]
    assert outdated[0] == "Эта ссылка устарела. Попросите у учителя новую ссылку или код."
    assert [x["text"] for x in buttons(outdated)] == ["Ввести код"]


def test_member_with_old_link_is_not_told_it_is_outdated(bot):
    b, api = bot
    c = make_class(b)
    join(b, IVAN, c.invite_code)
    b.handle(press(TEACHER, f"newlink_yes:{c.id}"))
    b.handle(started(IVAN, c.invite_code))
    assert api.to(IVAN)[-1][0].startswith("Вы в классе 10А")


def test_class_is_deleted_and_restored(bot):
    b, api = bot
    c = make_class(b)
    join(b, IVAN, c.invite_code)
    b.handle(press(TEACHER, "delete"))
    assert api.to(TEACHER)[-1][0] == "Удалить класс 10А, история? Ученики потеряют доступ."
    b.handle(press(TEACHER, f"delete_yes:{c.id}"))
    assert api.to(TEACHER)[-1][0] == "Класс 10А, история удалён. Вернуть можно в течение 30 дней."
    b.handle(started(MARIA, c.invite_code))
    assert api.to(MARIA)[-1][0] == "Этот класс удалён. Спросите у учителя."
    b.handle(press(TEACHER, f"restore:{c.id}"))
    assert db.classes_of(TEACHER)[0][0].id == c.id
    assert [m.name for m in db.students_of(c.id)] == ["Иван Иванов"]


def test_known_teacher_sees_classes_and_starts_lesson(bot):
    b, api = bot
    c = make_class(b)
    b.handle(text(TEACHER, "привет"))
    empty = api.to(TEACHER)[-1]
    assert empty[0].startswith("Ваши классы:\n10А, история — 0 учеников\n\nУрок можно начать, когда в классе")
    assert "Начать урок" not in [x["text"] for x in buttons(empty)]  # в пустом классе урок не начать
    join(b, IVAN, c.invite_code)
    b.handle(text(TEACHER, "привет"))
    menu = api.to(TEACHER)[-1]
    assert menu[0].startswith("Ваши классы:\n10А, история — 1 ученик\n\nЧтобы начать урок")
    start = next(x for x in buttons(menu) if x["text"] == "Начать урок")
    assert start == {"type": "open_app", "text": "Начать урок", "web_app": BOT, "payload": f"c{c.id}"}


def test_several_classes_are_chosen_first(bot):
    b, api = bot
    first = make_class(b)
    b.handle(press(TEACHER, "newclass"))
    assert api.to(TEACHER)[-1][0] == "Создадим класс. Напишите название и предмет через запятую. Например: 10А, история."
    b.handle(text(TEACHER, "9Б, история"))
    second = db.classes_of(TEACHER)[-1][0]
    b.handle(press(TEACHER, "invite"))
    choice = api.to(TEACHER)[-1]
    assert choice[0] == "Вы в нескольких классах. Выберите класс."
    assert [x["payload"] for x in buttons(choice)] == [f"invite:{first.id}", f"invite:{second.id}"]


def test_students_get_lesson_started_and_late_joiner_too(bot):
    b, api = bot
    c = make_class(b)
    join(b, IVAN, c.invite_code)
    lesson = db.lesson_for_class(c.id)
    db.mark_live(lesson.id)
    b.lesson_started(IVAN, c, lesson.id)
    message = api.to(IVAN)[-1]
    assert message[0] == "Урок начался: 10А, история."
    assert buttons(message)[0]["payload"] == f"l{lesson.id}"
    # кто вошёл в класс во время урока, сразу получает приглашение
    join(b, MARIA, c.invite_code, first="Мария", last="Смирнова")
    assert api.to(MARIA)[-1][0] == "Урок начался: 10А, история."


def test_demo_opens_own_lesson(bot):
    b, api = bot
    b.handle(press(MARIA, "demo", first="Мария", last="Смирнова"))
    roster = db.lesson_roster(payload_of(api.to(MARIA)[-1], "Открыть демо")[1:])
    assert roster.teacher.name == "Мария Смирнова"
    assert roster.teacher.max_user_id == MARIA
    assert len(roster.students) == 6


def test_cancel_replaces_question_with_answer(bot):
    b, api = bot
    c = make_class(b)
    b.handle(press(TEACHER, f"newlink:{c.id}"))
    b.handle(press(TEACHER, payload_of(api.to(TEACHER)[-1], "Отмена")))
    assert api.answers[-1] == ("cb", "Ссылка и код 10А, история остались прежними.")
    assert db.get_class(c.id).invite_code == c.invite_code
    b.handle(press(TEACHER, f"delete:{c.id}"))
    b.handle(press(TEACHER, payload_of(api.to(TEACHER)[-1], "Отмена")))
    assert api.answers[-1] == ("cb", "Класс 10А, история остался.")
    join(b, IVAN, c.invite_code)
    b.handle(press(IVAN, f"leave:{c.id}"))
    b.handle(press(IVAN, payload_of(api.to(IVAN)[-1], "Остаться")))
    assert api.answers[-1] == ("cb", "Вы остались в классе 10А, история.")
    assert db.students_of(c.id) != []


def test_bots_are_ignored(bot):
    b, api = bot
    b.handle(text(MARIA, "/start", is_bot=True))
    assert api.sent == []


def test_unknown_buttons_are_answered(bot):
    b, api = bot
    b.handle(press(MARIA, "role:teacher"))
    # кнопки больше нет: на её месте — приветствие с выбором роли
    assert api.answers == [("cb", "Здравствуйте! Я помогаю вести уроки в MAX. Кто вы?")]


def test_students_are_counted_in_russian():
    assert [students_count(n) for n in (1, 2, 5, 11, 21, 24)] == [
        "1 ученик", "2 ученика", "5 учеников", "11 учеников", "21 ученик", "24 ученика"]


def test_bot_waits_for_max_at_start():
    # MAX недоступен при старте сервера: поток бота ждёт, а не умирает
    import threading

    import httpx

    from app.bot import max_bot

    calls = []

    def answer(request):
        calls.append(request.url.path)
        if len(calls) == 1:
            raise httpx.ConnectError("нет сети")
        if len(calls) == 2:
            return httpx.Response(200, text="<html>работы</html>")
        return httpx.Response(200, json={"name": "Идея фикс", "username": "ideya_bot"})

    stop = threading.Event()
    http = httpx.Client(base_url="https://max.test", transport=httpx.MockTransport(answer))
    stop.wait = lambda timeout=None: False  # без настоящего ожидания
    assert max_bot._whoami(http, stop)["username"] == "ideya_bot"
    assert len(calls) == 3


# ---------- #98: пункты 15–19 ----------


def test_confirm_button_is_removed_after_press(bot):
    # «Создать ссылку» второй раз не нажать: вопрос меняется на ответ, кнопки убираются (п. 15)
    b, api = bot
    c = make_class(b)
    b.handle(press(TEACHER, f"newlink_yes:{c.id}"))
    # вопрос заменён новой ссылкой на месте, кнопки «Создать ссылку» больше нет
    assert api.answers[-1][1].startswith("Новая ссылка для 10А")
    assert api.to(TEACHER)[-1][1] is None


def test_rename_after_this_is_me_changes_name(bot):
    # «Изменить имя» после «Это я» — имя меняется (п. 17)
    b, api = bot
    c = make_class(b)
    join(b, IVAN, c.invite_code)
    b.handle(press(IVAN, f"rename:{c.id}"))
    b.handle(text(IVAN, "Ваня Иванов"))
    assert [m.name for m in db.students_of(c.id)] == ["Ваня Иванов"]
    assert api.to(IVAN)[-1][0] == "Готово, теперь в классе 10А, история вы — Ваня Иванов."


def test_link_is_accepted_on_code_step_and_wrong_code_has_back(bot):
    # ученик прислал ссылку вместо кода (п. 18)
    b, api = bot
    c = make_class(b)
    b.handle(press(IVAN, "student"))
    b.handle(text(IVAN, b.link(c.invite_code)))
    assert api.to(IVAN)[-1][0] == "Вы входите в класс 10А, история. Вас зовут Иван Иванов?"
    b.handle(press(MARIA, "student"))
    b.handle(text(MARIA, "ZZZZZZ"))
    wrong = api.to(MARIA)[-1]
    assert wrong[0] == "Такого кода нет. Проверьте и введите ещё раз."
    assert [x["text"] for x in buttons(wrong)] == ["Назад"]


def test_removed_student_and_class_of_deleted_class_are_told(bot):
    # убранный ученик и ученики удалённого класса узнают об этом (п. 19)
    b, api = bot
    c = make_class(b)
    join(b, IVAN, c.invite_code)
    member = db.students_of(c.id)[0]
    b.handle(press(TEACHER, f"kick:{member.id}"))
    assert api.to(IVAN)[-1][0] == "Учитель убрал вас из класса 10А, история. Если это ошибка, напишите учителю."
    b.handle(press(TEACHER, f"unkick:{member.id}"))
    assert api.to(IVAN)[-1][0] == "Учитель вернул вас в класс 10А, история. Когда начнётся урок, я напишу."
    b.handle(press(TEACHER, f"delete_yes:{c.id}"))
    assert api.to(IVAN)[-1][0] == "Учитель удалил класс 10А, история. Уроков в нём больше не будет."
    # ссылка удалённого класса не отвечает «Вы в классе» даже бывшему участнику
    b.handle(started(IVAN, c.invite_code))
    assert api.to(IVAN)[-1][0] == "Этот класс удалён. Спросите у учителя."



# ---------- /start: что это за бот ----------


def test_start_sends_intro_with_cover_and_begin_button(bot):
    from app.bot.max_bot import INTRO

    b, api = bot
    b.handle(started(MARIA))
    intro = api.to(MARIA)
    # одно сообщение: обложка, пояснение и «Начать»; «Кто вы?» — только по кнопке
    assert len(intro) == 1 and intro[0][0] == INTRO and "Сферум AI" in INTRO
    assert api.images == [(MARIA, "token-1")]
    assert [x["text"] for x in buttons(intro[0])] == ["Начать"]
    b.handle(press(MARIA, payload_of(intro[0], "Начать")))
    assert api.to(MARIA)[-1][0] == "Здравствуйте! Я помогаю вести уроки в MAX. Кто вы?"
    # /start текстом — то же самое, обложка не грузится второй раз
    b.handle(text(MARIA, "/start"))
    assert api.to(MARIA)[-1][0] == INTRO and api.uploads == 1


def test_start_resets_waiting_for_code(bot):
    b, api = bot
    b.handle(press(IVAN, "student"))
    b.handle(text(IVAN, "/start"))
    assert db.dialog_step(IVAN)[0] is None


def test_start_without_cover_still_explains(bot):
    from app.bot.max_bot import INTRO

    b, api = bot
    api.upload_fails = True
    b.handle(started(MARIA))
    assert api.to(MARIA)[0][0] == INTRO


def test_menu_follows_current_role(bot):
    # учитель, который где-то ещё и ученик, получает только меню учителя — и наоборот
    b, api = bot
    c = make_class(b)  # TEACHER — учитель 10А
    other = db.create_class(MARIA, "Мария Смирнова", "7Б", "физика")
    db.join(other.id, TEACHER, "Елена Викторовна")  # и ученик в чужом классе
    db.set_role(TEACHER, "teacher")
    before = len(api.to(TEACHER))
    b.handle(press(TEACHER, "begin"))
    new = [m for m in api.to(TEACHER)[before:] if m[0] != max_bot.INTRO]  # вступление остаётся, без кнопки
    assert len(new) == 1 and new[0][0].startswith("Ваши классы:")
    db.set_role(TEACHER, "student")
    b.handle(press(TEACHER, "begin"))
    assert api.to(TEACHER)[-1][0].startswith("Вы в классе 7Б, физика")



# ---------- кнопки живые только у последнего сообщения ----------


def test_navigation_replaces_pressed_message(bot):
    """Шаги по меню встают на место нажатого сообщения, а не копятся в чате."""
    b, api = bot
    b.handle(started(MARIA))
    b.handle(press(MARIA, "begin", mid="mid1"))
    count = len(api.sent)
    b.handle(press(MARIA, "teacher", mid="mid3"))
    assert api.answers[-1][1].startswith("Создадим первый класс")  # вместо «Кто вы?»
    b.handle(press(MARIA, "back", mid="mid3"))
    assert api.answers[-1][1] == "Здравствуйте! Я помогаю вести уроки в MAX. Кто вы?"
    assert len([m for m in api.sent[count:] if m[0] == MARIA]) == 2  # те же два экрана, новых сообщений нет


def test_new_message_takes_buttons_from_previous(bot):
    """Бот пишет новое сообщение — у прошлого сообщения этому человеку кнопки снимаются."""
    b, api = bot
    b.handle(press(TEACHER, "teacher", mid="m0"))  # «Напишите название» с кнопкой «Назад»
    b.handle(text(TEACHER, "10А, история"))  # ответ текстом — новые сообщения
    assert api.edits and api.edits[0][0] == "m0" and api.edits[0][2] is None


def test_intro_keeps_cover_without_button(bot):
    b, api = bot
    b.handle(started(MARIA))
    b.handle(press(MARIA, "begin", mid="mid1"))
    assert api.answers[-1] == ("cb", max_bot.INTRO)  # вступление на месте, без «Начать»
    assert api.to(MARIA)[-1][0].endswith("Кто вы?")
