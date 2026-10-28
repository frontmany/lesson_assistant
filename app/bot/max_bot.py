"""Бот MAX. Владелец: Фёдор.

API: https://platform-api2.max.ru, токен в заголовке Authorization.
Документация: https://dev.max.ru/docs
Официального SDK для Python нет, ходим через httpx;
схема API — в репозитории max-messenger/api-schema.

Что делает бот:
- /start → кнопки «Я учитель» и «Я ученик»; ученик выбирает себя из roster.json,
  и пользователь MAX запоминается за этим участником (app/bot/store.py);
- учителю — кнопка «Начать урок», ученику — «Войти в урок»: обе открывают
  мини-приложение бота и передают ему номер урока. Кто открыл, мини-приложение
  узнаёт по данным запуска MAX и этой привязке (#24);
- после урока: учителю — сводка, каждому ученику — его карточка (#21).

Для разработки — long polling (GET /updates), для боевого режима — webhook
по HTTPS (POST /subscriptions). Одновременно работает только один способ.

Запуск: python -m app.bot.max_bot
"""

import logging
import ssl
import time

import certifi
import httpx

from app.bot.store import Bindings
from app.config import ROOT, settings
from app.models import Roster

log = logging.getLogger(__name__)

# Сертификат API MAX выпущен удостоверяющим центром Минцифры, которому
# по умолчанию не доверяют ни Python, ни Windows. Добавляем его корневой
# сертификат (официальный, с gu-st.ru) только для запросов бота.
MINCIFRY_ROOT_CA = ROOT / "certs" / "russian_trusted_root_ca.pem"

UPDATE_TYPES = "message_created,message_callback,bot_started"
POLL_TIMEOUT_S = 30


def ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context(cafile=certifi.where())
    context.load_verify_locations(cafile=MINCIFRY_ROOT_CA)
    return context


def client() -> httpx.Client:
    return httpx.Client(
        base_url=settings.max_api_url,
        headers={"Authorization": settings.max_bot_token},
        # long polling держит запрос до POLL_TIMEOUT_S секунд
        timeout=POLL_TIMEOUT_S + 10,
        verify=ssl_context(),
    )


class MaxApi:
    """Три метода API MAX, которые нужны боту."""

    def __init__(self, http: httpx.Client) -> None:
        self.http = http

    def updates(self, marker: int | None) -> dict:
        params = {"timeout": POLL_TIMEOUT_S, "types": UPDATE_TYPES}
        if marker is not None:
            params["marker"] = marker
        response = self.http.get("/updates", params=params)
        response.raise_for_status()
        return response.json()

    def send(self, user_id: int, text: str, buttons: list[list[dict]] | None = None) -> None:
        body: dict = {"text": text}
        if buttons:
            body["attachments"] = [{"type": "inline_keyboard", "payload": {"buttons": buttons}}]
        self.http.post("/messages", params={"user_id": user_id}, json=body).raise_for_status()

    def answer(self, callback_id: str, notification: str) -> None:
        """Ответить на нажатие кнопки всплывающим уведомлением."""
        self.http.post("/answers", params={"callback_id": callback_id}, json={"notification": notification}).raise_for_status()


def callback(text: str, payload: str) -> dict:
    return {"type": "callback", "text": text, "payload": payload}


class Bot:
    """Логика бота без сети: обновление MAX на входе, вызовы MaxApi на выходе."""

    def __init__(self, api: MaxApi, roster: Roster, bindings: Bindings, bot_name: str) -> None:
        self.api = api
        self.roster = roster
        self.bindings = bindings
        self.bot_name = bot_name
        self.students = {s.id: s.name for s in roster.students}

    def handle(self, update: dict) -> None:
        kind = update.get("update_type")
        if kind == "bot_started":
            self.start(update["user"]["user_id"])
        elif kind == "message_created":
            message = update["message"]
            sender = message.get("sender") or {}
            if not sender or sender.get("is_bot"):
                return
            text = (message.get("body") or {}).get("text") or ""
            if text.strip().startswith("/start"):
                self.start(sender["user_id"])
            else:
                self.api.send(sender["user_id"], "Чтобы выбрать роль или войти в урок, отправьте /start.")
        elif kind == "message_callback":
            self.on_button(update["callback"])

    def start(self, user_id: int) -> None:
        participant = self.bindings.participant(user_id)
        if participant is None:
            self.ask_role(user_id)
        else:
            self.greet(user_id, participant)

    def ask_role(self, user_id: int) -> None:
        self.api.send(
            user_id,
            "Здравствуйте! Это ассистент урока в Сферуме. Кто вы на уроке?",
            [[callback("Я учитель", "role:teacher")], [callback("Я ученик", "role:student")]],
        )

    def ask_student(self, user_id: int) -> None:
        rows = []
        for sid, name in self.students.items():
            owner = self.bindings.owner(sid)
            taken = owner is not None and owner != user_id
            rows.append([callback(f"{name} (занято)" if taken else name, f"student:{sid}")])
        self.api.send(user_id, f"Выберите себя в списке класса {self.roster.class_name}.", rows)

    def on_button(self, cb: dict) -> None:
        callback_id = cb["callback_id"]
        user_id = cb["user"]["user_id"]
        payload = cb.get("payload") or ""
        if payload == "role:teacher":
            self.choose(callback_id, user_id, self.roster.teacher.id)
        elif payload == "role:student":
            self.api.answer(callback_id, "Выберите себя в списке")
            self.ask_student(user_id)
        elif payload.startswith("student:") and payload.removeprefix("student:") in self.students:
            self.choose(callback_id, user_id, payload.removeprefix("student:"))
        elif payload == "role:change":
            self.bindings.unbind(user_id)
            self.api.answer(callback_id, "Выберите роль заново")
            self.ask_role(user_id)
        else:
            self.api.answer(callback_id, "Эта кнопка больше не работает, отправьте /start")

    def choose(self, callback_id: str, user_id: int, participant_id: str) -> None:
        if not self.bindings.bind(user_id, participant_id):
            self.api.answer(callback_id, "Это место уже занято другим пользователем")
            return
        self.api.answer(callback_id, "Готово")
        self.greet(user_id, participant_id)

    def greet(self, user_id: int, participant_id: str) -> None:
        roster = self.roster
        if participant_id == roster.teacher.id:
            text = f"Вы учитель {roster.class_name}, {roster.subject.lower()}. Когда будете готовы, нажмите «Начать урок»."
            button = "Начать урок"
        else:
            text = f"Вы {self.students[participant_id]}, {roster.class_name}. Когда учитель начнёт урок, нажмите «Войти в урок»."
            button = "Войти в урок"
        self.api.send(user_id, text, [[self.open_app(button)], [callback("Сменить роль", "role:change")]])

    def open_app(self, text: str) -> dict:
        """Кнопка, которая открывает мини-приложение бота; payload приходит в start_param."""
        return {"type": "open_app", "text": text, "web_app": self.bot_name, "payload": self.roster.lesson_id}


def run_polling() -> None:
    roster = Roster.model_validate_json((settings.demo_lesson_dir / "roster.json").read_text(encoding="utf-8"))
    with client() as http:
        me = http.get("/me").json()
        log.info("бот %s (%s) слушает обновления", me.get("name"), me.get("username"))
        api = MaxApi(http)
        bot = Bot(api, roster, Bindings(settings.bot_store_path), settings.max_bot_name or me["username"])
        marker = None
        while True:
            try:
                data = api.updates(marker)
            except httpx.HTTPError as exc:
                log.warning("не получил обновления: %s, повторю через 3 с", exc)
                time.sleep(3)
                continue
            for update in data.get("updates", []):
                try:
                    bot.handle(update)
                except Exception:
                    log.exception("не обработал обновление %s", update.get("update_type"))
            marker = data.get("marker", marker)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if not settings.max_bot_token:
        raise SystemExit("Нет MAX_BOT_TOKEN в .env: токен выдаёт бот «Идея фикс»")
    try:
        run_polling()
    except KeyboardInterrupt:
        log.info("бот остановлен")
