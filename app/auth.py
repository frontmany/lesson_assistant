"""Кто открыл мини-приложение (#24). Владелец: Фёдор.

MAX передаёт мини-приложению данные запуска (initData) с подписью: HMAC-SHA256,
ключ — HMAC-SHA256("WebAppData", токен бота). Сервер проверяет подпись и выдаёт
свою сессию: дальше мини-приложение ходит с ней, а не с initData.

В браузере, куда урок открывают, если MAX не дал микрофон (js/mic.js), данных
запуска нет: туда ведёт одноразовая ссылка, которая меняется на сессию.

DEV_AUTH=1 — для разработки: участника можно назвать в адресе (?id=ivan_i).
На сервере выключено.

Документация: https://dev.max.ru/docs/webapps/validation
"""

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl

from fastapi import Header, HTTPException, Query

from app.config import settings

INIT_DATA_MAX_AGE_S = 24 * 3600  # старые данные запуска не принимаем
SESSION_S = 12 * 3600
TICKET_S = 10 * 60

_used_tickets: dict[str, float] = {}  # одноразовые ссылки: id → когда истекает


@dataclass(frozen=True)
class User:
    max_user_id: int | None
    name: str
    # DEV_AUTH: участник назван в адресе, а не узнан по MAX
    dev_participant: str | None = None


def check_init_data(init_data: str, bot_token: str, now: float | None = None) -> dict | None:
    """Данные запуска MAX, если подпись верна и они не старше суток; иначе None."""
    pairs = parse_qsl(init_data, keep_blank_values=True)
    keys = [k for k, _ in pairs]
    if keys.count("hash") != 1 or len(keys) != len(set(keys)):
        return None
    fields = dict(pairs)
    received = fields.pop("hash")
    launch_params = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, launch_params.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        return None
    try:
        age = (now or time.time()) - int(fields.get("auth_date", "0"))
        fields["user"] = json.loads(fields.get("user", "{}"))
    except ValueError:
        return None
    if age > INIT_DATA_MAX_AGE_S or not fields["user"].get("id"):
        return None
    return fields


def _secret() -> bytes:
    if settings.session_secret:
        return settings.session_secret.encode()
    # без своего секрета сессии подписываются производным от токена бота
    return hmac.new(b"session", settings.max_bot_token.encode() or _fallback, hashlib.sha256).digest()


_fallback = secrets.token_bytes(32)  # нет ни секрета, ни токена: сессии живут до перезапуска


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign(claims: dict, ttl_s: int) -> str:
    body = _b64(json.dumps({**claims, "exp": int(time.time()) + ttl_s}).encode())
    return f"{body}.{_b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())}"


def unsign(token: str) -> dict | None:
    body, _, sig = token.partition(".")
    expected = _b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
    if not sig or not hmac.compare_digest(sig, expected):
        return None
    claims = json.loads(_unb64(body))
    return claims if claims.get("exp", 0) > time.time() else None


def session_for(max_user_id: int, name: str) -> str:
    return sign({"kind": "session", "uid": max_user_id, "name": name}, SESSION_S)


def ticket_for(user: User) -> str:
    """Одноразовая ссылка в браузер: меняется на сессию один раз и не позже чем через 10 минут."""
    return sign({"kind": "ticket", "uid": user.max_user_id, "name": user.name, "jti": secrets.token_hex(8)}, TICKET_S)


def redeem_ticket(ticket: str) -> str | None:
    claims = unsign(ticket)
    if claims is None or claims.get("kind") != "ticket":
        return None
    now = time.time()
    for jti, exp in list(_used_tickets.items()):
        if exp < now:
            del _used_tickets[jti]
    if claims["jti"] in _used_tickets:
        return None
    _used_tickets[claims["jti"]] = claims["exp"]
    return session_for(claims["uid"], claims["name"])


def user_from_token(token: str | None) -> User | None:
    claims = unsign(token) if token else None
    if claims is None or claims.get("kind") != "session":
        return None
    return User(claims["uid"], claims["name"])


def current_user(authorization: str | None = Header(default=None), id: str | None = Query(default=None)) -> User:
    """Зависимость FastAPI: кто делает запрос. 401 — без сессии."""
    token = (authorization or "").removeprefix("Bearer ").strip()
    user = user_from_token(token)
    if user is not None:
        return user
    if settings.dev_auth:
        return User(None, "", dev_participant=id)
    raise HTTPException(401, "Откройте урок из чата с ботом")
