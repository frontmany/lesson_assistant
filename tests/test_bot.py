"""Логика бота MAX без сети: обновления на входе, вызовы API на выходе."""

from app.bot.max_bot import Bot
from app.config import settings
from app.models import Roster

ROSTER = Roster.model_validate_json((settings.demo_lesson_dir / "roster.json").read_text(encoding="utf-8"))
TEACHER = ROSTER.teacher.max_user_id
IVAN, STRANGER = 111, 999


class FakeApi:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str, list | None]] = []
        self.answers: list[tuple[str, str]] = []

    def send(self, user_id, text, buttons=None):
        self.sent.append((user_id, text, buttons))

    def answer(self, callback_id, notification):
        self.answers.append((callback_id, notification))


def make():
    roster = ROSTER.model_copy(deep=True)
    roster.students[0].max_user_id = IVAN  # Иван И.
    api = FakeApi()
    return Bot(api, roster, "ideya_fiks_161_bot"), api


def started(user_id):
    return {"update_type": "bot_started", "user": {"user_id": user_id, "first_name": "x", "is_bot": False}, "chat_id": 1}


def text(user_id, body, is_bot=False):
    return {"update_type": "message_created", "message": {"sender": {"user_id": user_id, "is_bot": is_bot}, "body": {"text": body}}}


def test_teacher_is_recognized_and_gets_button_that_opens_mini_app():
    bot, api = make()
    bot.handle(started(TEACHER))
    user, message, buttons = api.sent[-1]
    assert user == TEACHER
    assert message.startswith("Вы учитель 10А")
    assert buttons == [[{"type": "open_app", "text": "Начать урок", "web_app": "ideya_fiks_161_bot", "payload": ROSTER.lesson_id}]]


def test_student_is_recognized_without_choosing_a_role():
    bot, api = make()
    bot.handle(text(IVAN, "/start"))
    _, message, buttons = api.sent[-1]
    assert message.startswith("Вы Иван И., 10А")
    assert buttons[0][0]["text"] == "Войти в урок"


def test_any_message_gets_the_same_answer():
    bot, api = make()
    bot.handle(text(IVAN, "привет"))
    assert api.sent[-1][1].startswith("Вы Иван И.")


def test_stranger_is_not_let_in_and_learns_own_id():
    bot, api = make()
    bot.handle(started(STRANGER))
    _, message, buttons = api.sent[-1]
    assert "нет в списке класса" in message
    assert str(STRANGER) in message
    assert buttons is None


def test_bots_are_ignored():
    bot, api = make()
    bot.handle(text(STRANGER, "/start", is_bot=True))
    assert api.sent == []


def test_old_role_buttons_are_answered():
    bot, api = make()
    old = {"update_type": "message_callback", "callback": {"callback_id": "cb", "payload": "role:teacher", "timestamp": 0,
                                                           "user": {"user_id": TEACHER, "first_name": "x", "is_bot": False}}}
    bot.handle(old)
    assert api.answers == [("cb", "Эта кнопка больше не нужна")]
    assert api.sent[-1][1].startswith("Вы учитель")


def test_max_ids_are_not_exposed_through_api():
    assert "max_user_id" not in ROSTER.model_dump()["teacher"]
