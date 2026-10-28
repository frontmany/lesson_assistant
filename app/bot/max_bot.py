"""Бот MAX. Владелец: Фёдор.

API: https://platform-api2.max.ru, токен в заголовке Authorization.
Документация: https://dev.max.ru/docs
Официального SDK для Python нет, ходим через httpx;
схема API — в репозитории max-messenger/api-schema.

Что делает бот:
- узнаёт человека по аккаунту MAX: id участников лежат в составе класса
  (roster.json, в Сферуме они приходят из школьной системы), роль сам человек
  не выбирает;
- учителю — кнопка «Начать урок», ученику — «Войти в урок»: обе открывают
  мини-приложение бота и передают ему номер урока. Кто открыл, мини-приложение
  узнаёт по данным запуска MAX и тому же составу класса (#24);
- того, кого нет в составе класса, не пускает и называет его id MAX, чтобы
  его можно было добавить;
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


class Bot:
    """Логика бота без сети: обновление MAX на входе, вызовы MaxApi на выходе."""

    def __init__(self, api: MaxApi, roster: Roster, bot_name: str) -> None:
        self.api = api
        self.roster = roster
        self.bot_name = bot_name
        everyone = [roster.teacher, *roster.students]
        self.people = {p.max_user_id: p for p in everyone if p.max_user_id is not None}

    def handle(self, update: dict) -> None:
        kind = update.get("update_type")
        if kind == "bot_started":
            self.greet(update["user"]["user_id"])
        elif kind == "message_created":
            sender = update["message"].get("sender") or {}
            if sender and not sender.get("is_bot"):
                self.greet(sender["user_id"])
        elif kind == "message_callback":
            # кнопки выбора роли из прошлой версии бота остались в старых сообщениях
            cb = update["callback"]
            self.api.answer(cb["callback_id"], "Эта кнопка больше не нужна")
            self.greet(cb["user"]["user_id"])

    def greet(self, user_id: int) -> None:
        roster = self.roster
        person = self.people.get(user_id)
        if person is None:
            self.api.send(
                user_id,
                f"Вас нет в списке класса {roster.class_name}. Если это ошибка, "
                f"напишите учителю и назовите ваш id в MAX: {user_id}.",
            )
        elif person.id == roster.teacher.id:
            self.api.send(
                user_id,
                f"Вы учитель {roster.class_name}, {roster.subject.lower()}. Когда будете готовы, нажмите «Начать урок».",
                [[self.open_app("Начать урок")]],
            )
        else:
            self.api.send(
                user_id,
                f"Вы {person.name}, {roster.class_name}. Когда учитель начнёт урок, нажмите «Войти в урок».",
                [[self.open_app("Войти в урок")]],
            )

    def open_app(self, text: str) -> dict:
        """Кнопка, которая открывает мини-приложение бота; payload приходит в start_param."""
        return {"type": "open_app", "text": text, "web_app": self.bot_name, "payload": self.roster.lesson_id}


def run_polling() -> None:
    roster = Roster.model_validate_json((settings.demo_lesson_dir / "roster.json").read_text(encoding="utf-8"))
    with client() as http:
        me = http.get("/me").json()
        log.info("бот %s (%s) слушает обновления", me.get("name"), me.get("username"))
        api = MaxApi(http)
        bot = Bot(api, roster, settings.max_bot_name or me["username"])
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
