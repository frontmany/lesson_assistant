"""Бот MAX. Владелец: Фёдор.

API: https://platform-api2.max.ru, токен в заголовке Authorization.
Документация: https://dev.max.ru/docs
Официального SDK для Python нет, ходим через httpx;
схема API — в репозитории max-messenger/api-schema.

Что делает бот:
- /start → кнопки «Я учитель» и «Я ученик», привязка аккаунта MAX к ученику из roster.json;
- кнопка открытия мини-приложения (настраивается на business.max.ru/self);
- после урока: учителю — сводка, каждому ученику — его карточка.

Для разработки — long polling (GET /updates), для боевого режима — webhook
по HTTPS (POST /subscriptions). Одновременно работает только один способ.

Запуск: python -m app.bot.max_bot
"""

import httpx

from app.config import settings


def client() -> httpx.Client:
    return httpx.Client(
        base_url=settings.max_api_url,
        headers={"Authorization": settings.max_bot_token},
        timeout=30,
    )


def send_message(user_id: int, text: str) -> None:
    """TODO(Фёдор): POST /messages, параметры — в справочнике методов dev.max.ru."""
    raise NotImplementedError("bot/max_bot.py: send_message")


def run_polling() -> None:
    """TODO(Фёдор): цикл GET /updates и обработка /start и кнопок."""
    raise NotImplementedError("bot/max_bot.py: run_polling")


if __name__ == "__main__":
    if not settings.max_bot_token:
        raise SystemExit("Нет MAX_BOT_TOKEN в .env: токен выдаёт бот «Идея фикс»")
    with client() as c:
        print(c.get("/me").json())
    run_polling()
