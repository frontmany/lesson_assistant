"""Бот MAX.

API: https://platform-api2.max.ru, токен в заголовке Authorization.
Документация: https://dev.max.ru/docs
Официального SDK для Python нет, ходим через httpx;
схема API — в репозитории max-messenger/api-schema.

Что делает бот (#44, тексты и шаги — design/bot-registration.html):
- незнакомому: «Я учитель», «Я ученик» или «Посмотреть демо»;
- учитель создаёт класс («10А, история») и получает код класса и ссылку
  max.ru/<бот>?start=<код>; код можно продиктовать на уроке;
- ученик входит по ссылке или по коду, подтверждает имя, учителю приходит
  уведомление с кнопкой «Убрать из класса»;
- откат: ученик выходит сам, учитель убирает и возвращает ученика, меняет
  ссылку, удаляет класс и 30 дней может его вернуть;
- «Начать урок» открывает мини-приложение; когда учитель вошёл в звонок,
  ученики получают «Урок начался» (сервер зовёт lesson_started);
- демо: свой урок, где человек — учитель, а класс заходит записью (#18).

Обновления бот получает через long polling (GET /updates), и локально, и на сервере.
Бот работает внутри сервера, в своём потоке (start_in_background).

Отдельно, без сервера: python -m app.bot.max_bot
"""

import logging
import re
import ssl
import threading
import time

from pathlib import Path

import certifi
import httpx

from app.config import ROOT, settings
from app.db import classes as db

log = logging.getLogger(__name__)

# Сертификат API MAX выпущен удостоверяющим центром Минцифры, которому
# по умолчанию не доверяют ни Python, ни Windows. Добавляем его корневой
# сертификат (официальный, с gu-st.ru) только для запросов бота.
MINCIFRY_ROOT_CA = ROOT / "certs" / "russian_trusted_root_ca.pem"

UPDATE_TYPES = "message_created,message_callback,bot_started"
# Обложка для первого сообщения /start: квадратная — MAX показывает картинку почти квадратом и срезает края (design/bot/start-card.png)
COVER = Path(__file__).parent / "assets" / "cover.png"
# Что показывает бот — первым сообщением на /start, до «Кто вы?»
INTRO = (
    "Это демонстрационный бот Сферум AI, помощника учителя на уроках в Сферуме.\n\n"
    "Учитель создаёт класс и подключает учеников, класс интегрируется со звонком Сферума. "
    "На уроке появляется невидимый ассистент: он анализирует происходящее и обновляет шкалу участия "
    "каждого ученика. Учитель видит участие всего класса, а ученик видит только свою шкалу. "
    "После урока учитель получает сводку: кто вытянул урок, кто выпал и кого спросить в следующий раз. "
    "Ученик получает личную карточку о своём прогрессе.\n\n"
    "Быстрее всего начать с «Посмотреть демо»"
)
# Команды в подсказке «/» в MAX
COMMANDS = [{"name": "start", "description": "Что умеет бот и с чего начать"}]
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

    @staticmethod
    def _body(text: str, buttons=None, markdown: bool = False, image: str | None = None) -> dict:
        """Тело сообщения: текст, картинка по токену и кнопки. Пустые вложения убирают старые кнопки."""
        attachments = [{"type": "image", "payload": {"token": image}}] if image else []
        if buttons:
            attachments.append({"type": "inline_keyboard", "payload": {"buttons": buttons}})
        body: dict = {"text": text, "attachments": attachments}
        if markdown:
            body["format"] = "markdown"  # **жирный** — названия блоков сводки
        return body

    @staticmethod
    def _mid(res: httpx.Response) -> str | None:
        try:
            return res.json()["message"]["body"]["mid"]
        except (ValueError, KeyError, TypeError):
            return None

    def send(self, user_id: int, text: str, buttons: list[list[dict]] | None = None, markdown: bool = False) -> str | None:
        """Новое сообщение. Возвращает его id: по нему потом снимаются кнопки."""
        res = self.http.post("/messages", params={"user_id": user_id}, json=self._body(text, buttons, markdown))
        res.raise_for_status()
        return self._mid(res)

    def edit(self, mid: str, text: str, buttons=None, markdown: bool = False, image: str | None = None) -> None:
        """Изменить отправленное сообщение. Без buttons кнопки снимаются, текст и картинка остаются."""
        self.http.put("/messages", params={"message_id": mid},
                      json=self._body(text, buttons, markdown, image)).raise_for_status()

    def upload_image(self, path: Path) -> str:
        """Загрузить картинку в MAX: токен для вложения {"type": "image"}.

        POST /uploads?type=image даёт адрес, туда файл уходит полем data; в ответе
        {"photos": {<id>: {"token": ...}}}.
        """
        url = self.http.post("/uploads", params={"type": "image"}).json()["url"]
        with path.open("rb") as f:
            res = self.http.post(url, files={"data": (path.name, f, "image/png")})
        res.raise_for_status()
        body = res.json()
        if body.get("token"):
            return body["token"]
        return next(iter(body["photos"].values()))["token"]

    def send_image(self, user_id: int, token: str, text: str, buttons: list[list[dict]] | None = None,
                   retries: int = 4) -> str | None:
        """Сообщение с картинкой (и кнопками). MAX обрабатывает файл после загрузки: пока не готов —
        attachment.not.ready, повторяем с растущей паузой."""
        body = self._body(text, buttons, image=token)
        for attempt in range(retries + 1):
            res = self.http.post("/messages", params={"user_id": user_id}, json=body)
            if res.status_code < 400:
                return self._mid(res)
            if "attachment.not.ready" not in res.text or attempt == retries:
                res.raise_for_status()
            time.sleep(0.5 * 2 ** attempt)
        return None

    def set_commands(self, commands: list[dict]) -> None:
        """Список команд для подсказки «/» в MAX."""
        self.http.patch("/me/commands", json={"commands": commands}).raise_for_status()

    def answer(self, callback_id: str, notification: str | None = None, message: str | None = None,
               buttons=None, markdown: bool = False, image: str | None = None, user_id: int | None = None) -> None:
        """Ответить на нажатие кнопки: заменить сообщение, где её нажали, следующим шагом.

        message — новый текст этого сообщения, buttons — его новые кнопки (без них кнопок нет).
        user_id — чей это экран: API он не нужен, нужен журналу и тестам.
        """
        body: dict = {"notification": notification} if notification else {}
        if message is not None:
            body["message"] = self._body(message, buttons, markdown, image)
        self.http.post("/answers", params={"callback_id": callback_id}, json=body).raise_for_status()


def callback(text: str, payload: str) -> dict:
    return {"type": "callback", "text": text, "payload": payload}


def label(c) -> str:
    """«10А, история»: одноимённые классы различаются предметом (#98, п. 16)."""
    return f"{c.name}, {c.subject}"


def plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def students_count(n: int) -> str:
    return f"{n} {plural(n, 'ученик', 'ученика', 'учеников')}"


class Bot:
    """Логика бота без сети: обновление MAX на входе, вызовы MaxApi на выходе."""

    def __init__(self, api: MaxApi, bot_name: str) -> None:
        self.api = api
        self.bot_name = bot_name
        self._cover: str | None = None  # токен загруженной обложки: грузим один раз на запуск
        # Кнопки живые только у последнего сообщения человеку: по нажатию следующий шаг заменяет
        # нажатое сообщение, а перед новым сообщением у прежнего кнопки снимаются.
        # _last — последнее сообщение с кнопками каждому: id, текст, разметка, картинка
        self._last: dict[int, dict] = {}
        self._cb: dict | None = None  # нажатие, на которое ещё отвечаем: id, кто, какое сообщение
        self._lock = threading.RLock()  # бот отвечает из потока опроса и из потоков сервера

    # ---------- отправка: кнопки только у последнего сообщения ----------

    def say(self, user_id: int, text: str, buttons=None, markdown: bool = False) -> None:
        """Сообщение человеку. В ответ на его нажатие — вместо нажатого сообщения, иначе — новое."""
        with self._lock:
            cb = self._cb
            if cb and cb["user"] == user_id and not cb["done"]:
                cb["done"] = True
                if (self._last.get(user_id) or {}).get("mid") != cb["mid"]:
                    self._strip(user_id)  # нажали старое сообщение: у последнего кнопки тоже снимаем
                self.api.answer(cb["id"], message=text, buttons=buttons, markdown=markdown, user_id=user_id)
                self._remember(user_id, cb["mid"], text, buttons, markdown)
                return
            self._strip(user_id)
            mid = self.api.send(user_id, text, buttons, markdown=markdown)
            self._remember(user_id, mid, text, buttons, markdown)

    def _remember(self, user_id: int, mid, text: str, buttons, markdown: bool = False, image: str | None = None) -> None:
        if buttons and mid:
            self._last[user_id] = {"mid": mid, "text": text, "markdown": markdown, "image": image}
        else:
            self._last.pop(user_id, None)

    def _strip(self, user_id: int) -> None:
        """Снять кнопки с прошлого сообщения человеку: текст и картинка остаются."""
        last = self._last.pop(user_id, None)
        if not last:
            return
        try:
            self.api.edit(last["mid"], last["text"], markdown=last["markdown"], image=last["image"])
        except httpx.HTTPError as exc:
            log.warning("не снял кнопки у сообщения %s: %s", last["mid"], exc)

    # ---------- входящие ----------

    def handle(self, update: dict) -> None:
        kind = update.get("update_type")
        if kind == "bot_started":
            user = update["user"]
            payload = update.get("payload")
            if payload:
                self.code(user, payload)
            else:
                self.start(user["user_id"])
        elif kind == "message_created":
            sender = update["message"].get("sender") or {}
            if sender and not sender.get("is_bot"):
                self.text(sender, (update["message"].get("body") or {}).get("text") or "")
        elif kind == "message_callback":
            cb = update["callback"]
            mid = ((update.get("message") or {}).get("body") or {}).get("mid")
            self.press(cb["user"], cb["callback_id"], cb.get("payload") or "", mid)

    def text(self, user: dict, body: str) -> None:
        user_id = user["user_id"]
        body = body.strip()
        if body.split("@")[0].lower() == "/start":
            db.set_dialog(user_id, None)
            return self.start(user_id)
        step, class_id = db.dialog_step(user_id)
        if step == "class_name":
            # нажал «Я учитель» по ошибке и прислал код класса — значит, он ученик
            if db.code_candidates(body) and db.find_by_code(body)[0] != "none":
                return self.code(user, body)
            return self.create_class(user, body)
        if step == "code":
            # ученик прислал ссылку вместо кода — берём код из неё (#98, п. 18)
            return self.code(user, body.rsplit("start=", 1)[-1])
        if step == "name" and body:
            db.set_dialog(user_id, None)
            return self.join(user_id, class_id, body)
        # знакомый человек прислал код или ссылку — как вход по ссылке
        code = body.rsplit("start=", 1)[-1]
        if db.code_candidates(code) and db.find_by_code(code)[0] != "none":
            return self.code(user, code)
        self.greet(user_id)

    def press(self, user: dict, callback_id: str, payload: str, mid: str | None = None) -> None:
        """Нажатие кнопки. Следующий шаг встаёт на место нажатого сообщения (mid), поэтому
        в чате не копятся сообщения с живыми кнопками."""
        user_id = user["user_id"]
        action, _, arg = payload.partition(":")
        handler = {
            "teacher": lambda: self.as_teacher(user_id),
            "newclass": lambda: self.ask_class_name(user_id, first=False),
            "student": lambda: self.as_student(user_id),
            "demo": lambda: self.demo(user),  # кнопка из старых сообщений: теперь «Посмотреть демо» открывает приложение
            "back": lambda: self.back(user_id),
            "me": lambda: self.join(user_id, int(arg), full_name(user)),
            "rename": lambda: self.ask_name(user_id, int(arg)),
            "kick": lambda: self.kick(user_id, int(arg)),
            "unkick": lambda: self.unkick(user_id, int(arg)),
            "leave": lambda: self.ask_leave(user_id, int(arg)),
            "leave_yes": lambda: self.leave(user_id, int(arg)),
            "invite": lambda: self.for_class(user_id, "invite", arg, self.invite),
            "newlink": lambda: self.for_class(user_id, "newlink", arg, self.ask_new_link),
            "newlink_yes": lambda: self.new_link(user_id, int(arg)),
            "delete": lambda: self.for_class(user_id, "delete", arg, self.ask_delete),
            "delete_yes": lambda: self.delete(user_id, int(arg)),
            "restore": lambda: self.restore(user_id, int(arg)),
            "start": lambda: self.pick_lesson(user_id),
            "class": lambda: self.student_class(user_id, int(arg)),
        }.get(action)
        with self._lock:
            self._cb = {"id": callback_id, "user": user_id, "mid": mid, "done": False}
            try:
                if action == "cancel":
                    # «Отмена» и «Остаться»: вопрос меняется на ответ, кнопок больше нет
                    self.say(user_id, self.cancelled(arg))
                elif action == "begin":
                    self.begin(user_id)
                elif handler is None:
                    self.greet(user_id)  # кнопка из старого сообщения, которой больше нет
                else:
                    handler()
            finally:
                cb, self._cb = self._cb, None
                if not cb["done"]:
                    # шаг ничего не показал этому человеку: просто закрываем нажатие
                    try:
                        self.api.answer(callback_id, user_id=user_id)
                    except httpx.HTTPError as exc:
                        log.warning("не ответил на нажатие %s: %s", callback_id, exc)

    def begin(self, user_id: int) -> None:
        """«Начать» под вступлением: вступление с обложкой остаётся, без кнопки, а меню — новым сообщением."""
        cb = self._cb
        cb["done"] = True
        self.api.answer(cb["id"], message=INTRO, image=self._cover, user_id=user_id)
        if (self._last.get(user_id) or {}).get("mid") == cb["mid"]:
            self._last.pop(user_id, None)
        else:
            self._strip(user_id)
        self.greet(user_id)

    # ---------- кто вы ----------

    def start(self, user_id: int) -> None:
        """/start и первый вход: что это за бот (с обложкой) и «Начать» — по ней «Кто вы?» или меню."""
        begin = [[callback("Начать", "begin")]]
        try:
            if self._cover is None:
                self._cover = self.api.upload_image(COVER)
            with self._lock:
                self._strip(user_id)
                mid = self.api.send_image(user_id, self._cover, INTRO, begin)
                self._remember(user_id, mid, INTRO, begin, image=self._cover)
        except Exception as exc:  # обложка не загрузилась — пояснение всё равно нужно
            log.warning("обложка /start не отправлена: %s", exc)
            self.say(user_id, INTRO, begin)

    def greet(self, user_id: int) -> None:
        """Меню по текущей роли — той же, что в мини-приложении: учителю его классы, ученику его.
        Учитель, который где-то ещё и ученик, не получает оба меню сразу."""
        mine = db.classes_of(user_id)
        role = db.role_of(user_id)
        if role == "teacher":
            teaching = [c for c, m in mine if m.role == "teacher"]
            return self.teacher_menu(user_id, teaching) if teaching else self.ask_class_name(user_id, first=True)
        if role == "student":
            learning = [c for c, m in mine if m.role == "student"]
            if len(learning) == 1:
                return self.student_class(user_id, learning[0].id)
            if learning:
                return self.choose(user_id, [(label(c), f"class:{c.id}") for c in learning])
            return self.ask_code(user_id)
        self.choose_role(user_id)

    def choose_role(self, user_id: int) -> None:
        """Шаг 1: кто вы. И «Выйти» в мини-приложении — выбрать роль заново.

        Бот спрашивает «Кто вы?» — значит, роли нет: «Назад» после «Я ученик»
        не должен оставить приложение в роли ученика.
        """
        db.set_role(user_id, "none")
        self.say(user_id, "Здравствуйте! Я помогаю вести уроки в MAX. Кто вы?", [
            [callback("Я учитель", "teacher"), callback("Я ученик", "student")],
            # сразу открывает демо в мини-приложении, без второго сообщения «Открыть демо»
            [self.open_app("Посмотреть демо", "demo")],
        ])

    def as_teacher(self, user_id: int) -> None:
        """«Я учитель»: роль видна и в мини-приложении, дальше — название класса."""
        db.set_role(user_id, "teacher")
        self.ask_class_name(user_id, first=not db.classes_of(user_id))

    def as_student(self, user_id: int) -> None:
        db.set_role(user_id, "student")
        self.ask_code(user_id)

    def teacher_menu(self, user_id: int, teaching: list) -> None:
        """Шаг 12: классы учителя и что с ними делать."""
        lines = [f"{c.name}, {c.subject} — {students_count(len(db.students_of(c.id)))}" for c in teaching]
        startable = [c for c in teaching if db.students_of(c.id)]
        row = []
        if len(startable) == 1:
            row.append(self.open_app("Начать урок", f"c{startable[0].id}"))
        elif startable:
            row.append(callback("Начать урок", "start"))
        if startable:
            hint = "Чтобы начать урок, нажмите «Начать урок»: откроется звонок, а ученики получат приглашение."
        else:
            hint = "Урок можно начать, когда в классе будет хотя бы один ученик."
        self.say(user_id, "Ваши классы:\n" + "\n".join(lines) + "\n\n" + hint, [b for b in [
            row,
            [callback("Пригласить", "invite"), callback("Новый класс", "newclass")],
            [callback("Удалить класс", "delete")],
        ] if b])

    def choose(self, user_id: int, buttons: list[tuple[str, str]]) -> None:
        """Шаг 11: человек в нескольких классах."""
        self.say(user_id, "Вы в нескольких классах. Выберите класс.",
                      [[callback(text, payload) for text, payload in buttons[i:i + 3]] for i in range(0, len(buttons), 3)])

    def for_class(self, user_id: int, action: str, arg: str, then) -> None:
        """Действие учителя над классом: сразу, если класс один или уже выбран, иначе шаг 11."""
        if arg:
            return then(user_id, int(arg))
        teaching = [c for c, m in db.classes_of(user_id) if m.role == "teacher"]
        if len(teaching) == 1:
            return then(user_id, teaching[0].id)
        if teaching:
            self.choose(user_id, [(label(c), f"{action}:{c.id}") for c in teaching])

    def pick_lesson(self, user_id: int) -> None:
        startable = [c for c, m in db.classes_of(user_id) if m.role == "teacher" and db.students_of(c.id)]
        self.say(user_id, "Вы в нескольких классах. Выберите класс.",
                      [[self.open_app(label(c), f"c{c.id}") for c in startable[i:i + 3]] for i in range(0, len(startable), 3)])

    # ---------- учитель: класс ----------

    def ask_class_name(self, user_id: int, first: bool) -> None:
        db.set_dialog(user_id, "class_name")
        self.say(user_id, f"Создадим {'первый ' if first else ''}класс. Напишите название и предмет через запятую. Например: 10А, история.",
                      [[callback("Назад", "back")]])

    def create_class(self, user: dict, body: str) -> None:
        name, _, subject = (part.strip() for part in body.partition(","))
        if not name or not subject or len(name) > 40 or len(subject) > 60:
            return self.say(user["user_id"], "Напишите название и предмет через запятую. Например: 10А, история.")
        db.set_dialog(user["user_id"], None)
        c = db.create_class(user["user_id"], full_name(user), name, subject.lower())
        self.say(user["user_id"], f"Создан класс {c.name}, {c.subject}.")
        self.invite(user["user_id"], c.id)

    def link(self, code: str) -> str:
        return f"https://max.ru/{self.bot_name}?start={code}"

    def invite(self, user_id: int, class_id: int) -> None:
        """Шаг 3: ссылка-приглашение и код класса."""
        c = db.get_class(class_id)
        self.say(
            user_id,
            f"Ссылка для учеников: {self.link(c.invite_code)}\nКод класса: {c.invite_code}\n"
            "Отправьте ссылку классу или продиктуйте код на уроке. "
            "Ссылка и код работают, пока вы не создадите новые.\n"
            "Когда ученик войдёт, я напишу, и урок можно будет начать.",
            [[callback("Новая ссылка", f"newlink:{c.id}")]],
        )

    def ask_new_link(self, user_id: int, class_id: int) -> None:
        c = db.get_class(class_id)
        self.say(user_id, f"Создать новую ссылку и код для {label(c)}? Старые перестанут работать.",
                      [[callback("Создать ссылку", f"newlink_yes:{class_id}"), callback("Отмена", f"cancel:newlink-{class_id}")]])

    def new_link(self, user_id: int, class_id: int) -> None:
        if not self.teaches(user_id, class_id):
            return
        c = db.new_code(class_id)
        self.say(user_id, f"Новая ссылка для {c.name}: {self.link(c.invite_code)}\nНовый код: {c.invite_code}\n"
                               "Отправьте её тем, кого ещё нет в классе. Кто уже вошёл, остаётся.")

    def ask_delete(self, user_id: int, class_id: int) -> None:
        c = db.get_class(class_id)
        self.say(user_id, f"Удалить класс {label(c)}? Ученики потеряют доступ.",
                      [[callback("Удалить класс", f"delete_yes:{class_id}"), callback("Отмена", f"cancel:delete-{class_id}")]])

    def delete(self, user_id: int, class_id: int) -> None:
        if not self.teaches(user_id, class_id):
            return
        students = db.students_of(class_id)
        db.delete_class(class_id)
        c = db.get_class(class_id)
        self.tell_students(students, class_deleted_text(c))
        self.say(user_id, f"Класс {label(c)} удалён. Вернуть можно в течение {db.RESTORE_DAYS} дней.",
                      [[callback("Вернуть", f"restore:{class_id}")]])

    def restore(self, user_id: int, class_id: int) -> None:
        c = db.get_class(class_id)
        if db.teacher_of(class_id).max_user_id != user_id:
            return
        if db.restore_class(class_id):
            self.say(user_id, f"Класс {label(c)} снова с вами: ученики и ссылка остались прежними.")
            self.tell_students(db.students_of(class_id), class_restored_text(c))
        else:
            self.say(user_id, f"Вернуть {c.name} уже нельзя: прошло {db.RESTORE_DAYS} дней.")

    def cancelled(self, what: str) -> str:
        """Чем заменить вопрос, на который ответили «Отмена» или «Остаться»."""
        action, _, class_id = what.partition("-")
        c = db.get_class(int(class_id)) if class_id.isdigit() else None
        if c is None:
            return "Хорошо, ничего не меняем."
        return {
            "newlink": f"Ссылка и код {label(c)} остались прежними.",
            "delete": f"Класс {label(c)} остался.",
            "leave": f"Вы остались в классе {label(c)}.",
        }.get(action, "Хорошо, ничего не меняем.")

    def teaches(self, user_id: int, class_id: int) -> bool:
        m = db.membership(class_id, user_id)
        return m is not None and m.role == "teacher"

    # ---------- ученик: вход ----------

    def ask_code(self, user_id: int) -> None:
        db.set_dialog(user_id, "code")
        self.say(user_id, "Введите код класса. Его назовёт учитель.", [[callback("Назад", "back")]])

    def back(self, user_id: int) -> None:
        """Нажал не ту кнопку: бот перестаёт ждать название или код и возвращает назад —
        к меню своей роли, а если в этой роли классов нет, к выбору «Кто вы?»."""
        db.set_dialog(user_id, None)
        role = db.role_of(user_id)
        if not any(m.role == role for _, m in db.classes_of(user_id)):
            return self.choose_role(user_id)
        self.greet(user_id)

    def code(self, user: dict, text: str) -> None:
        """Код из ссылки или сообщения: шаги 4, 5 и 14."""
        user_id = user["user_id"]
        status, c = db.find_by_code(text)
        if status == "none":
            db.set_dialog(user_id, "code")
            return self.say(user_id, "Такого кода нет. Проверьте и введите ещё раз.", [[callback("Назад", "back")]])
        db.set_dialog(user_id, None)
        # удалённый класс — раньше членства: иначе его ссылка отвечала бы «Вы в классе» (#98, п. 19)
        if status == "deleted":
            return self.say(user_id, "Этот класс удалён. Спросите у учителя.")
        member = db.membership(c.id, user_id) if c else None
        if member is not None:
            # уже в классе: ни «ссылка устарела», ни повторный вход ему не нужны
            if member.role == "teacher":
                return self.say(user_id, f"Это код вашего класса {c.name}.")
            return self.student_class(user_id, c.id)
        if status == "old":
            return self.say(user_id, "Эта ссылка устарела. Попросите у учителя новую ссылку или код.",
                                 [[callback("Ввести код", "student")]])
        self.say(user_id, f"Вы входите в класс {label(c)}. Вас зовут {full_name(user)}?",
                      [[callback("Это я", f"me:{c.id}"), callback("Изменить имя", f"rename:{c.id}")]])

    def ask_name(self, user_id: int, class_id: int) -> None:
        db.set_dialog(user_id, "name", class_id)
        self.say(user_id, "Напишите имя и фамилию.")

    def join(self, user_id: int, class_id: int, name: str) -> None:
        c = db.get_class(class_id)
        if c is None or c.deleted_at is not None:
            return self.say(user_id, "Этот класс удалён. Спросите у учителя.")
        before = db.membership(class_id, user_id)
        member, new = db.join(class_id, user_id, name[:80])
        if before is not None and before.name != member.name:
            self.say(user_id, f"Готово, теперь в классе {label(c)} вы — {member.name}.")
        else:
            self.say(user_id, f"Готово, вы в классе {label(c)}. Когда начнётся урок, я напишу.")
        if new:
            # шаг 6: учителю
            teacher = db.teacher_of(class_id)
            # с первым учеником урок становится возможен: кнопка тут же, а не только в меню
            self.say(teacher.max_user_id, f"{db.short_name(member.name)} теперь в классе {c.name}. Можно начинать урок.",
                          [[self.open_app("Начать урок", f"c{class_id}")], [callback("Убрать из класса", f"kick:{member.id}")]])
        lesson = db.live_lesson(class_id)
        if lesson is not None:
            self.lesson_started(user_id, c, lesson.id)

    # ---------- откат ----------

    def kick(self, user_id: int, member_id: int) -> None:
        m = db.get_member(member_id)
        if m is None or not self.teaches(user_id, m.class_id):
            return
        db.remove_member(member_id)
        c = db.get_class(m.class_id)
        self.tell_students([m], student_removed_text(c))
        self.say(user_id, f"Убрано из класса {label(c)}: {db.short_name(m.name)}",
                      [[callback("Вернуть", f"unkick:{member_id}")]])

    def unkick(self, user_id: int, member_id: int) -> None:
        m = db.get_member(member_id)
        if m is None or not self.teaches(user_id, m.class_id):
            return
        db.restore_member(member_id)
        c = db.get_class(m.class_id)
        self.tell_students([m], student_returned_text(c))
        self.say(user_id, f"{db.short_name(m.name)} снова в классе {label(c)}.")

    def tell_students(self, members, text: str) -> None:
        """Сообщение ученикам класса. Кто заблокировал бота или на ком упал MAX, не обрывает
        рассылку остальным и подтверждение учителю."""
        for m in members:
            try:
                self.say(m.max_user_id, text)
            except httpx.HTTPError as exc:
                log.warning("не написал ученику %s: %s", m.max_user_id, exc)

    def ask_leave(self, user_id: int, class_id: int) -> None:
        c = db.get_class(class_id)
        self.say(user_id, f"Выйти из класса {label(c)}?",
                      [[callback("Выйти", f"leave_yes:{class_id}"), callback("Остаться", f"cancel:leave-{class_id}")]])

    def leave(self, user_id: int, class_id: int) -> None:
        m = db.membership(class_id, user_id)
        if m is None or m.role != "student":
            return
        db.remove_member(m.id)
        c = db.get_class(class_id)
        self.say(user_id, f"Вы вышли из {c.name}. Чтобы вернуться, попросите у учителя ссылку.")
        self.say(db.teacher_of(class_id).max_user_id, f"{db.short_name(m.name)} теперь не в классе {c.name}.")

    # ---------- уроки ----------

    def student_class(self, user_id: int, class_id: int) -> None:
        """Шаг 13: знакомый ученик пишет боту."""
        c = db.get_class(class_id)
        lesson = db.live_lesson(class_id)
        if lesson is None:
            return self.say(user_id, f"Вы в классе {c.name}, {c.subject}. Урок пока не идёт. Я напишу, когда начнётся.",
                                 [[callback("Выйти из класса", f"leave:{class_id}")]])
        self.say(user_id, f"Вы в классе {c.name}, {c.subject}. Урок идёт.", [
            [self.open_app("Войти в урок", f"l{lesson.id}")],
            [callback("Выйти из класса", f"leave:{class_id}")],
        ])

    def lesson_started(self, user_id: int, c, lesson_id: str) -> None:
        """Шаг 10."""
        self.say(
            user_id, f"Урок начался: {c.name}, {c.subject}.",
            [[self.open_app("Войти в урок", f"l{lesson_id}")]],
        )

    def demo(self, user: dict) -> None:
        lesson = db.demo_lesson(user["user_id"], full_name(user))
        self.say(
            user["user_id"],
            "Демо-урок: вы смотрите урок глазами учителя, класс и учитель — запись урока истории. "
            "Ассистент слушает каждого ученика, и в панели класса растут шкалы участия. "
            "5 минут. Говорить не нужно. В конце нажмите «Завершить урок» — сводка придёт в чат.",
            [[self.open_app("Открыть демо", f"l{lesson.id}")]],
        )

    def summary(self, user_id: int, text: str, class_id: int | None) -> None:
        """Сводка учителю после урока (#21): под ней «Начать урок» следующего урока класса."""
        buttons = [[self.open_app("Начать урок", f"c{class_id}")]] if class_id is not None else None
        self.say(user_id, text, buttons, markdown=True)

    def card(self, user_id: int, text: str) -> None:
        """Карточка ученику: кнопок нет, её читают и закрывают."""
        self.say(user_id, text, markdown=True)

    def open_app(self, text: str, payload: str) -> dict:
        """Кнопка, которая открывает мини-приложение бота; payload приходит в start_param.

        c<id> — учитель начинает урок класса, l<id> — открыть урок.
        """
        return {"type": "open_app", "text": text, "web_app": self.bot_name, "payload": payload}


# ---------- сводка и карточка: тексты по design/bot-summary.html ----------

MAX_NAMES = 4  # дальше «и ещё N»: полный список — в мини-приложении


_MD = re.compile(r"([\\`*_~\[\]()#>|])")


def md(text: str) -> str:
    """Чужой текст в сообщении с разметкой: имя ученика, тема, ответ GigaChat. Знаки разметки
    экранируем, иначе «[Подробнее](ссылка)» в имени стало бы ссылкой в сводке учителю,
    а «Аня_2010» сломала бы жирные заголовки."""
    return _MD.sub(r"\\\1", text)


def _names(ids: list[str], names: dict[str, str]) -> str:
    shown = [names[i] for i in ids if i in names]
    rest = len(shown) - MAX_NAMES
    return ", ".join(shown[:MAX_NAMES]) + (f" и ещё {rest}" if rest > 0 else "")


def _title(roster) -> str:
    subject = roster.subject[:1].lower() + roster.subject[1:]
    return md(f"{roster.class_name}, {subject}")


def summary_text(roster, summary, minutes: int) -> str:
    """Сводка учителю одним сообщением: пустые блоки не показываем, «кого спросить» — внизу, ближе к кнопке."""
    names = {s.id: md(s.name) for s in roster.students}
    about = [md(roster.topic)] if roster.topic else []
    about += [f"{minutes} {plural(minutes, 'минута', 'минуты', 'минут')}", students_count(len(roster.students))]
    blocks = [f"**Итоги урока: {_title(roster)}**\n" + ", ".join(about)]
    if summary.carried:
        blocks.append(f"**Вытянули урок**\n{_names(summary.carried, names)}")
    if summary.dropped:
        blocks.append(f"**Выпали**\n{_names(summary.dropped, names)}")
    if summary.weak_topics:
        blocks.append("**Класс не уверен**\n" + ", ".join(md(t) for t in summary.weak_topics))
    asks = [a for a in summary.ask_next if a.student_id in names]
    for i, a in enumerate(asks):
        head = "**Спросить в следующий раз**\n" if i == 0 else ""
        blocks.append(f"{head}{names[a.student_id]} — {md(a.topic)}\n«{md(a.question)}»")
    if len(blocks) == 1:
        blocks.append("Для сводки данных пока мало: нужны хотя бы несколько реплик учеников.")
    return "\n\n".join(blocks)


def card_text(roster, card) -> str:
    """Карточка ученика: про него на «ты», без ступени — в карточке она читалась бы как оценка."""
    blocks = [f"**Твоя карточка: {_title(roster)}**" + (f"\n{md(roster.topic)}" if roster.topic else "")]
    if card.went_well:
        blocks.append("**Что получилось**\n" + "\n".join(md(x) for x in card.went_well))
    if card.to_repeat:
        blocks.append("**Что повторить**\n" + "\n".join(md(x) for x in card.to_repeat))
    blocks.append("Карточка не показывается классу.")
    return "\n\n".join(blocks)


def bot_link() -> str | None:
    """Чат с ботом: экран урока в браузере ведёт сюда, когда ссылка устарела. None — имя бота неизвестно."""
    name = _bot.bot_name if _bot is not None else settings.max_bot_name
    return f"https://max.ru/{name}" if name else None


def invite_link(code: str) -> str | None:
    """Ссылка-приглашение в класс, как в боте. None — имя бота неизвестно: бот не запущен и MAX_BOT_NAME пуст."""
    name = _bot.bot_name if _bot is not None else settings.max_bot_name
    return f"https://max.ru/{name}?start={code}" if name else None


def full_name(user: dict) -> str:
    """Имя из профиля MAX: «Иван Иванов»."""
    parts = [user.get("first_name") or "", user.get("last_name") or ""]
    name = re.sub(r"\s+", " ", " ".join(parts)).strip()
    return name or user.get("name") or "Без имени"


# ---------- запуск ----------

_bot: Bot | None = None


# Ученики не должны ждать урока, которого не будет (#98, п. 19): одни тексты и для бота,
# и для мини-приложения — что бы учитель ни нажал, ученики узнают одинаково
def class_deleted_text(c) -> str:
    return f"Учитель удалил класс {label(c)}. Уроков в нём больше не будет."


def class_restored_text(c) -> str:
    return f"Учитель вернул класс {label(c)}. Когда начнётся урок, я напишу."


def student_removed_text(c) -> str:
    return f"Учитель убрал вас из класса {label(c)}. Если это ошибка, напишите учителю."


def student_returned_text(c) -> str:
    return f"Учитель вернул вас в класс {label(c)}. Когда начнётся урок, я напишу."


def tell(members, text: str) -> None:
    """Из мини-приложения: то же сообщение ученикам, что шлёт бот. Без бота — ничего."""
    if _bot is not None:
        _bot.tell_students(members, text)


def lesson_started(lesson_id: str) -> None:
    """Учитель вошёл в звонок: позвать учеников класса (шаг 10). Без бота — ничего."""
    lesson = db.get_lesson(lesson_id)
    if _bot is None or lesson is None or lesson.class_id is None:
        return
    c = db.get_class(lesson.class_id)
    for m in db.students_of(c.id):
        try:
            _bot.lesson_started(m.max_user_id, c, lesson_id)
        except httpx.HTTPError as exc:
            log.warning("не позвал %s на урок: %s", m.max_user_id, exc)


def join_from_app(max_user_id: int, class_id: int, name: str) -> None:
    """Ученик вошёл по коду в мини-приложении: как «Это я» в боте — ему «Готово», учителю «… теперь в классе».
    Без бота — только запись в базу."""
    if _bot is None:
        db.join(class_id, max_user_id, name[:80])
        return
    try:
        _bot.join(max_user_id, class_id, name)
    except httpx.HTTPError as exc:
        log.warning("не сообщил о входе %s в класс %s: %s", max_user_id, class_id, exc)
        db.join(class_id, max_user_id, name[:80])


def role_again(max_user_id: int) -> None:
    """«Выйти» в мини-приложении: в чате снова выбор роли, бот больше ничего не ждёт. Без бота — ничего."""
    db.set_dialog(max_user_id, None)
    if _bot is None:
        return
    try:
        _bot.choose_role(max_user_id)
    except httpx.HTTPError as exc:
        log.warning("не прислал выбор роли %s: %s", max_user_id, exc)


def lesson_finished(roster, summary, cards, minutes: int) -> None:
    """Урок закончен: учителю — сводка, каждому ученику — только его карточка. Без бота — ничего.

    Ученики из записи (виртуальные) аккаунта MAX не имеют: им карточки не уходят.
    """
    if _bot is None:
        return
    lesson = db.get_lesson(roster.lesson_id)
    class_id = lesson.class_id if lesson else None
    if roster.teacher.max_user_id:
        try:
            _bot.summary(roster.teacher.max_user_id, summary_text(roster, summary, minutes), class_id)
        except httpx.HTTPError as exc:
            log.warning("не отправил сводку урока %s: %s", roster.lesson_id, exc)
    accounts = {s.id: s.max_user_id for s in roster.students if s.max_user_id}
    for card in cards:
        if card.student_id not in accounts:
            continue
        try:
            _bot.card(accounts[card.student_id], card_text(roster, card))
        except httpx.HTTPError as exc:
            log.warning("не отправил карточку %s: %s", card.student_id, exc)


def _whoami(http: httpx.Client, stop: threading.Event | None) -> dict | None:
    """Профиль бота. MAX может быть недоступен при старте сервера: ждём, а не роняем поток."""
    while stop is None or not stop.is_set():
        try:
            res = http.get("/me")
            res.raise_for_status()
            me = res.json()
            if settings.max_bot_name or me.get("username"):
                return me
            log.warning("API MAX не вернул имя бота и MAX_BOT_NAME не задан, повторю через 10 с")
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("API MAX недоступен при старте: %s, повторю через 10 с", exc)
        if stop is None:
            time.sleep(10)
        else:
            stop.wait(10)
    return None


def run_polling(stop: threading.Event | None = None) -> None:
    global _bot
    with client() as http:
        me = _whoami(http, stop)
        if me is None:
            return
        log.info("бот %s (%s) слушает обновления", me.get("name"), me.get("username"))
        api = MaxApi(http)
        _bot = Bot(api, settings.max_bot_name or me["username"])
        try:
            api.set_commands(COMMANDS)
        except httpx.HTTPError as exc:
            log.warning("команды бота не записаны: %s", exc)
        marker = None
        while stop is None or not stop.is_set():
            try:
                data = api.updates(marker)
            except (httpx.HTTPError, ValueError) as exc:
                # ValueError — ответ не JSON (прокси или работы на стороне MAX): поток не должен умереть
                log.warning("не получил обновления: %s, повторю через 3 с", exc)
                time.sleep(3)
                continue
            for update in data.get("updates", []):
                try:
                    _bot.handle(update)
                except Exception:
                    log.exception("не обработал обновление %s", update.get("update_type"))
            marker = data.get("marker", marker)


def start_in_background() -> threading.Event | None:
    """Бот в потоке сервера: делит с ним базу и уроки. None — токена нет, бот выключен."""
    if not settings.max_bot_token:
        log.info("MAX_BOT_TOKEN не задан: бот не запущен")
        return None
    stop = threading.Event()
    threading.Thread(target=run_polling, args=(stop,), name="max-bot", daemon=True).start()
    return stop


if __name__ == "__main__":
    from app import db as database

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if not settings.max_bot_token:
        raise SystemExit("Нет MAX_BOT_TOKEN в .env: токен выдаёт бот «Идея фикс»")
    database.init()
    try:
        run_polling()
    except KeyboardInterrupt:
        log.info("бот остановлен")
