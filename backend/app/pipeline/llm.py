"""Запрос к GigaChat"""

import json
import logging
import re
import threading
import time
import uuid

import httpx

from app.bot.max_bot import ssl_context
from app.config import settings

log = logging.getLogger(__name__)

OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
API_URL = "https://api.giga.chat/v1"
SCOPE = "GIGACHAT_API_PERS"
DEFAULT_MODEL = "GigaChat-3-Ultra"
TIMEOUT_S = 60
TOKEN_MARGIN_S = 60  # токен живёт 30 минут, обновляем заранее


class LlmError(Exception):
    pass

def enabled() -> bool:
    return bool(settings.llm_api_key)


_token: tuple[str, float] | None = None
_lock = threading.Lock()


def _access_token(http: httpx.Client) -> str:
    global _token
    with _lock:
        if _token and _token[1] - TOKEN_MARGIN_S > time.time():
            return _token[0]
        response = http.post(
            OAUTH_URL,
            headers={"Authorization": f"Basic {settings.llm_api_key}", "RqUID": str(uuid.uuid4())},
            data={"scope": SCOPE},
        )
        response.raise_for_status()
        data = response.json()
        _token = (data["access_token"], data["expires_at"] / 1000)
        return _token[0]


def chat(system: str, user: str) -> str:
    try:
        with httpx.Client(timeout=TIMEOUT_S, verify=ssl_context()) as http:
            response = http.post(
                f"{settings.llm_api_url or API_URL}/chat/completions",
                headers={"Authorization": f"Bearer {_access_token(http)}"},
                json={
                    "model": settings.llm_model or DEFAULT_MODEL,
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                    "temperature": 0.2,
                },
            )
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        raise LlmError(f"GigaChat не ответил: {exc}") from exc
    usage = data.get("usage", {})
    log.info("GigaChat %s: %s токенов", data.get("model"), usage.get("total_tokens"))
    return data["choices"][0]["message"]["content"]


def chat_json(system: str, user: str) -> dict:
    """Модель иногда оборачивает JSON в блок кода"""
    text = chat(system, user)
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise LlmError(f"в ответе нет JSON: {text[:200]}")
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise LlmError(f"JSON не разобрался: {exc}") from exc
