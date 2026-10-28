"""Кто из пользователей MAX кем выбрал себя в боте. Владелец: Фёдор.

Пока это JSON-файл (BOT_STORE в .env), в git он не попадает.
Когда появится база (app/db), привязки переедут туда.
"""

import json
from pathlib import Path


class Bindings:
    """Пользователь MAX → участник урока из roster.json. Один участник — один пользователь."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._by_user: dict[str, str] = {}
        if path.exists():
            self._by_user = json.loads(path.read_text(encoding="utf-8"))

    def participant(self, user_id: int) -> str | None:
        return self._by_user.get(str(user_id))

    def owner(self, participant_id: str) -> int | None:
        for user, participant in self._by_user.items():
            if participant == participant_id:
                return int(user)
        return None

    def bind(self, user_id: int, participant_id: str) -> bool:
        """Привязать пользователя к участнику. False — участник уже занят другим пользователем."""
        owner = self.owner(participant_id)
        if owner is not None and owner != user_id:
            return False
        self._by_user[str(user_id)] = participant_id
        self._save()
        return True

    def unbind(self, user_id: int) -> None:
        if self._by_user.pop(str(user_id), None) is not None:
            self._save()

    def _save(self) -> None:
        self._path.write_text(json.dumps(self._by_user, ensure_ascii=False, indent=1), encoding="utf-8")
