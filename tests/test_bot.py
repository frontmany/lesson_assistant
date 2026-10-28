"""Логика бота MAX без сети: обновления на входе, вызовы API на выходе."""

from app.bot.max_bot import Bot
from app.bot.store import Bindings
from app.config import settings
from app.models import Roster

ROSTER = Roster.model_validate_json((settings.demo_lesson_dir / "roster.json").read_text(encoding="utf-8"))
FEDOR, IVAN = 215228189, 111


class FakeApi:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str, list | None]] = []
        self.answers: list[tuple[str, str]] = []

    def send(self, user_id, text, buttons=None):
        self.sent.append((user_id, text, buttons))

    def answer(self, callback_id, notification):
        self.answers.append((callback_id, notification))


def make(tmp_path):
    api = FakeApi()
    return Bot(api, ROSTER, Bindings(tmp_path / "users.json"), "ideya_fiks_161_bot"), api


def started(user_id):
    return {"update_type": "bot_started", "user": {"user_id": user_id, "first_name": "x", "is_bot": False}, "chat_id": 1}


def pressed(user_id, payload, callback_id="cb"):
    return {"update_type": "message_callback", "callback": {"callback_id": callback_id, "payload": payload, "timestamp": 0,
                                                            "user": {"user_id": user_id, "first_name": "x", "is_bot": False}}}


def text(user_id, body, is_bot=False):
    return {"update_type": "message_created", "message": {"sender": {"user_id": user_id, "is_bot": is_bot}, "body": {"text": body}}}


def payloads(buttons):
    return [b.get("payload") for row in buttons for b in row]


def test_start_asks_for_role(tmp_path):
    bot, api = make(tmp_path)
    bot.handle(started(FEDOR))
    user, _, buttons = api.sent[-1]
    assert user == FEDOR
    assert payloads(buttons) == ["role:teacher", "role:student"]


def test_teacher_gets_button_that_opens_mini_app(tmp_path):
    bot, api = make(tmp_path)
    bot.handle(pressed(FEDOR, "role:teacher"))
    assert api.answers == [("cb", "Готово")]
    _, message, buttons = api.sent[-1]
    assert "Начать урок" in message
    app = buttons[0][0]
    assert app == {"type": "open_app", "text": "Начать урок", "web_app": "ideya_fiks_161_bot", "payload": ROSTER.lesson_id}


def test_student_chooses_self_and_role_is_remembered(tmp_path):
    bot, api = make(tmp_path)
    bot.handle(pressed(IVAN, "role:student"))
    assert payloads(api.sent[-1][2]) == [f"student:{s.id}" for s in ROSTER.students]
    bot.handle(pressed(IVAN, "student:ivan_i"))
    assert api.sent[-1][2][0][0]["text"] == "Войти в урок"

    # бот перезапустился: привязка читается из файла
    again, api2 = make(tmp_path)
    again.handle(text(IVAN, "/start"))
    assert "Иван И." in api2.sent[-1][1]


def test_taken_place_is_refused(tmp_path):
    bot, api = make(tmp_path)
    bot.handle(pressed(IVAN, "student:ivan_i"))
    bot.handle(pressed(FEDOR, "role:student"))
    assert "Иван И. (занято)" in [b["text"] for row in api.sent[-1][2] for b in row]
    bot.handle(pressed(FEDOR, "student:ivan_i", callback_id="cb2"))
    assert api.answers[-1] == ("cb2", "Это место уже занято другим пользователем")
    assert bot.bindings.participant(FEDOR) is None


def test_change_role(tmp_path):
    bot, api = make(tmp_path)
    bot.handle(pressed(FEDOR, "role:teacher"))
    bot.handle(pressed(FEDOR, "role:change"))
    assert bot.bindings.participant(FEDOR) is None
    assert payloads(api.sent[-1][2]) == ["role:teacher", "role:student"]


def test_other_text_gets_hint_and_bots_are_ignored(tmp_path):
    bot, api = make(tmp_path)
    bot.handle(text(FEDOR, "привет"))
    assert "/start" in api.sent[-1][1]
    bot.handle(text(999, "/start", is_bot=True))
    assert len(api.sent) == 1


def test_unknown_button(tmp_path):
    bot, api = make(tmp_path)
    bot.handle(pressed(FEDOR, "student:nobody"))
    assert "/start" in api.answers[-1][1]
