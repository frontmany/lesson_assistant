"""Бот MAX. Владелец: Фёдор.

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

Для разработки — long polling (GET /updates), для боевого режима — webhook
по HTTPS (POST /subscriptions). Одновременно работает только один способ.
Бот работает внутри сервера, в своём потоке (start_in_background).

Отдельно, без сервера: python -m app.bot.max_bot
"""

import logging
import re
import ssl
import threading
import time

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

    def send(self, user_id: int, text: str, buttons: list[list[dict]] | None = None, markdown: bool = False) -> None:
        body: dict = {"text": text}
        if markdown:
            body["format"] = "markdown"  # **жирный** — названия блоков сводки
        if buttons:
            body["attachments"] = [{"type": "inline_keyboard", "payload": {"buttons": buttons}}]
        self.http.post("/messages", params={"user_id": user_id}, json=body).raise_for_status()

    def answer(self, callback_id: str, notification: str | None = None, message: str | None = None) -> None:
        """Ответить на нажатие кнопки: всплывающим уведомлением или заменой сообщения с кнопкой.

        message — новый текст того сообщения, где нажали кнопку; кнопки из него убираются.
        """
        body: dict = {"notification": notification} if notification else {}
        if message is not None:
            body["message"] = {"text": message, "attachments": []}
        self.http.post("/answers", params={"callback_id": callback_id}, json=body).raise_for_status()


def callback(text: str, payload: str) -> dict:
    return {"type": "callback", "text": text, "payload": payload}


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

    # ---------- входящие ----------

    def handle(self, update: dict) -> None:
        kind = update.get("update_type")
        if kind == "bot_started":
            user = update["user"]
            payload = update.get("payload")
            if payload:
                self.code(user, payload)
            else:
                self.greet(user["user_id"])
        elif kind == "message_created":
            sender = update["message"].get("sender") or {}
            if sender and not sender.get("is_bot"):
                self.text(sender, (update["message"].get("body") or {}).get("text") or "")
        elif kind == "message_callback":
            cb = update["callback"]
            self.press(cb["user"], cb["callback_id"], cb.get("payload") or "")

    def text(self, user: dict, body: str) -> None:
        user_id = user["user_id"]
        step, class_id = db.dialog_step(user_id)
        body = body.strip()
        if step == "class_name":
            # нажал «Я учитель» по ошибке и прислал код класса — значит, он ученик
            if db.code_candidates(body) and db.find_by_code(body)[0] != "none":
                return self.code(user, body)
            return self.create_class(user, body)
        if step == "code":
            return self.code(user, body)
        if step == "name" and body:
            db.set_dialog(user_id, None)
            return self.join(user_id, class_id, body)
        # знакомый человек прислал код или ссылку — как вход по ссылке
        code = body.rsplit("start=", 1)[-1]
        if db.code_candidates(code) and db.find_by_code(code)[0] != "none":
            return self.code(user, code)
        self.greet(user_id)

    def press(self, user: dict, callback_id: str, payload: str) -> None:
        user_id = user["user_id"]
        action, _, arg = payload.partition(":")
        handler = {
            "teacher": lambda: self.ask_class_name(user_id, first=not db.classes_of(user_id)),
            "newclass": lambda: self.ask_class_name(user_id, first=False),
            "student": lambda: self.ask_code(user_id),
            "demo": lambda: self.demo(user),
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
        if action == "cancel":
            # «Отмена» и «Остаться»: вопрос в чате меняется на ответ, кнопки исчезают
            return self.api.answer(callback_id, message=self.cancelled(arg))
        self.api.answer(callback_id, "Готово" if handler else "Эта кнопка больше не нужна")
        if handler is None:
            return self.greet(user_id)
        handler()

    # ---------- кто вы ----------

    def greet(self, user_id: int) -> None:
        mine = db.classes_of(user_id)
        teaching = [c for c, m in mine if m.role == "teacher"]
        learning = [c for c, m in mine if m.role == "student"]
        if not mine:
            # шаг 1
            return self.api.send(user_id, "Здравствуйте! Я помогаю вести уроки в MAX. Кто вы?", [
                [callback("Я учитель", "teacher"), callback("Я ученик", "student")],
                [callback("Посмотреть демо", "demo")],
            ])
        if teaching:
            self.teacher_menu(user_id, teaching)
        if len(learning) == 1:
            self.student_class(user_id, learning[0].id)
        elif learning:
            self.choose(user_id, [(c.name, f"class:{c.id}") for c in learning])

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
        self.api.send(user_id, "Ваши классы:\n" + "\n".join(lines) + "\n\n" + hint, [b for b in [
            row,
            [callback("Пригласить", "invite"), callback("Новый класс", "newclass")],
            [callback("Удалить класс", "delete")],
        ] if b])

    def choose(self, user_id: int, buttons: list[tuple[str, str]]) -> None:
        """Шаг 11: человек в нескольких классах."""
        self.api.send(user_id, "Вы в нескольких классах. Выберите класс.",
                      [[callback(text, payload) for text, payload in buttons[i:i + 3]] for i in range(0, len(buttons), 3)])

    def for_class(self, user_id: int, action: str, arg: str, then) -> None:
        """Действие учителя над классом: сразу, если класс один или уже выбран, иначе шаг 11."""
        if arg:
            return then(user_id, int(arg))
        teaching = [c for c, m in db.classes_of(user_id) if m.role == "teacher"]
        if len(teaching) == 1:
            return then(user_id, teaching[0].id)
        if teaching:
            self.choose(user_id, [(c.name, f"{action}:{c.id}") for c in teaching])

    def pick_lesson(self, user_id: int) -> None:
        startable = [c for c, m in db.classes_of(user_id) if m.role == "teacher" and db.students_of(c.id)]
        self.api.send(user_id, "Вы в нескольких классах. Выберите класс.",
                      [[self.open_app(c.name, f"c{c.id}") for c in startable[i:i + 3]] for i in range(0, len(startable), 3)])

    # ---------- учитель: класс ----------

    def ask_class_name(self, user_id: int, first: bool) -> None:
        db.set_dialog(user_id, "class_name")
        self.api.send(user_id, f"Создадим {'первый ' if first else ''}класс. Напишите название и предмет через запятую. Например: 10А, история.",
                      [[callback("Назад", "back")]])

    def create_class(self, user: dict, body: str) -> None:
        name, _, subject = (part.strip() for part in body.partition(","))
        if not name or not subject or len(name) > 40 or len(subject) > 60:
            return self.api.send(user["user_id"], "Напишите название и предмет через запятую. Например: 10А, история.")
        db.set_dialog(user["user_id"], None)
        c = db.create_class(user["user_id"], full_name(user), name, subject.lower())
        self.api.send(user["user_id"], f"Создан класс {c.name}, {c.subject}.")
        self.invite(user["user_id"], c.id)

    def link(self, code: str) -> str:
        return f"https://max.ru/{self.bot_name}?start={code}"

    def invite(self, user_id: int, class_id: int) -> None:
        """Шаг 3: ссылка-приглашение и код класса."""
        # TODO(Фёдор): «Поделиться» и «Ученики» — когда будут экраны «Мои классы» в мини-приложении
        c = db.get_class(class_id)
        self.api.send(
            user_id,
            f"Ссылка для учеников: {self.link(c.invite_code)}\nКод класса: {c.invite_code}\n"
            "Отправьте ссылку классу или продиктуйте код на уроке. "
            "Ссылка и код работают, пока вы не создадите новые.\n"
            "Когда ученик войдёт, я напишу, и урок можно будет начать.",
            [[callback("Новая ссылка", f"newlink:{c.id}")]],
        )

    def ask_new_link(self, user_id: int, class_id: int) -> None:
        self.api.send(user_id, "Создать новую ссылку и код? Старые перестанут работать.",
                      [[callback("Создать ссылку", f"newlink_yes:{class_id}"), callback("Отмена", f"cancel:newlink-{class_id}")]])

    def new_link(self, user_id: int, class_id: int) -> None:
        if not self.teaches(user_id, class_id):
            return
        c = db.new_code(class_id)
        self.api.send(user_id, f"Новая ссылка для {c.name}: {self.link(c.invite_code)}\nНовый код: {c.invite_code}\n"
                               "Отправьте её тем, кого ещё нет в классе. Кто уже вошёл, остаётся.")

    def ask_delete(self, user_id: int, class_id: int) -> None:
        c = db.get_class(class_id)
        self.api.send(user_id, f"Удалить класс {c.name}? Ученики потеряют доступ.",
                      [[callback("Удалить класс", f"delete_yes:{class_id}"), callback("Отмена", f"cancel:delete-{class_id}")]])

    def delete(self, user_id: int, class_id: int) -> None:
        if not self.teaches(user_id, class_id):
            return
        db.delete_class(class_id)
        c = db.get_class(class_id)
        self.api.send(user_id, f"Класс {c.name} удалён. Вернуть можно в течение {db.RESTORE_DAYS} дней.",
                      [[callback("Вернуть", f"restore:{class_id}")]])

    def restore(self, user_id: int, class_id: int) -> None:
        c = db.get_class(class_id)
        if db.teacher_of(class_id).max_user_id != user_id:
            return
        if db.restore_class(class_id):
            self.api.send(user_id, f"Класс {c.name} снова с вами: ученики и ссылка остались прежними.")
        else:
            self.api.send(user_id, f"Вернуть {c.name} уже нельзя: прошло {db.RESTORE_DAYS} дней.")

    def cancelled(self, what: str) -> str:
        """Чем заменить вопрос, на который ответили «Отмена» или «Остаться»."""
        action, _, class_id = what.partition("-")
        c = db.get_class(int(class_id)) if class_id.isdigit() else None
        if c is None:
            return "Хорошо, ничего не меняем."
        return {
            "newlink": f"Ссылка и код {c.name} остались прежними.",
            "delete": f"Класс {c.name} остался.",
            "leave": f"Вы остались в классе {c.name}.",
        }.get(action, "Хорошо, ничего не меняем.")

    def teaches(self, user_id: int, class_id: int) -> bool:
        m = db.membership(class_id, user_id)
        return m is not None and m.role == "teacher"

    # ---------- ученик: вход ----------

    def ask_code(self, user_id: int) -> None:
        db.set_dialog(user_id, "code")
        self.api.send(user_id, "Введите код класса. Его назовёт учитель.", [[callback("Назад", "back")]])

    def back(self, user_id: int) -> None:
        """Нажал не ту кнопку: бот перестаёт ждать название или код и возвращает к выбору."""
        db.set_dialog(user_id, None)
        self.greet(user_id)

    def code(self, user: dict, text: str) -> None:
        """Код из ссылки или сообщения: шаги 4, 5 и 14."""
        user_id = user["user_id"]
        status, c = db.find_by_code(text)
        if status == "none":
            db.set_dialog(user_id, "code")
            return self.api.send(user_id, "Такого кода нет. Проверьте и введите ещё раз.")
        db.set_dialog(user_id, None)
        member = db.membership(c.id, user_id) if c else None
        if member is not None:
            # уже в классе: ни «ссылка устарела», ни повторный вход ему не нужны
            if member.role == "teacher":
                return self.api.send(user_id, f"Это код вашего класса {c.name}.")
            return self.student_class(user_id, c.id)
        if status == "deleted":
            return self.api.send(user_id, "Этот класс удалён. Спросите у учителя.")
        if status == "old":
            return self.api.send(user_id, "Эта ссылка устарела. Попросите у учителя новую ссылку или код.",
                                 [[callback("Ввести код", "student")]])
        self.api.send(user_id, f"Вы входите в класс {c.name}. Вас зовут {full_name(user)}?",
                      [[callback("Это я", f"me:{c.id}"), callback("Изменить имя", f"rename:{c.id}")]])

    def ask_name(self, user_id: int, class_id: int) -> None:
        db.set_dialog(user_id, "name", class_id)
        self.api.send(user_id, "Напишите имя и фамилию.")

    def join(self, user_id: int, class_id: int, name: str) -> None:
        c = db.get_class(class_id)
        if c is None or c.deleted_at is not None:
            return self.api.send(user_id, "Этот класс удалён. Спросите у учителя.")
        member, new = db.join(class_id, user_id, name[:80])
        self.api.send(user_id, f"Готово, вы в классе {c.name}. Когда начнётся урок, я напишу.")
        if new:
            # шаг 6: учителю
            teacher = db.teacher_of(class_id)
            # с первым учеником урок становится возможен: кнопка тут же, а не только в меню
            self.api.send(teacher.max_user_id, f"{db.short_name(member.name)} теперь в классе {c.name}. Можно начинать урок.",
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
        self.api.send(user_id, f"Убрано из класса {c.name}: {db.short_name(m.name)}",
                      [[callback("Вернуть", f"unkick:{member_id}")]])

    def unkick(self, user_id: int, member_id: int) -> None:
        m = db.get_member(member_id)
        if m is None or not self.teaches(user_id, m.class_id):
            return
        db.restore_member(member_id)
        c = db.get_class(m.class_id)
        self.api.send(user_id, f"{db.short_name(m.name)} снова в классе {c.name}.")

    def ask_leave(self, user_id: int, class_id: int) -> None:
        c = db.get_class(class_id)
        self.api.send(user_id, f"Выйти из класса {c.name}?",
                      [[callback("Выйти", f"leave_yes:{class_id}"), callback("Остаться", f"cancel:leave-{class_id}")]])

    def leave(self, user_id: int, class_id: int) -> None:
        m = db.membership(class_id, user_id)
        if m is None or m.role != "student":
            return
        db.remove_member(m.id)
        c = db.get_class(class_id)
        self.api.send(user_id, f"Вы вышли из {c.name}. Чтобы вернуться, попросите у учителя ссылку.")
        self.api.send(db.teacher_of(class_id).max_user_id, f"{db.short_name(m.name)} теперь не в классе {c.name}.")

    # ---------- уроки ----------

    def student_class(self, user_id: int, class_id: int) -> None:
        """Шаг 13: знакомый ученик пишет боту."""
        c = db.get_class(class_id)
        lesson = db.live_lesson(class_id)
        if lesson is None:
            return self.api.send(user_id, f"Вы в классе {c.name}, {c.subject}. Урок пока не идёт. Я напишу, когда начнётся.",
                                 [[callback("Выйти из класса", f"leave:{class_id}")]])
        self.api.send(user_id, f"Вы в классе {c.name}, {c.subject}. Урок идёт.", [
            [self.open_app("Войти в урок", f"l{lesson.id}")],
            [callback("Выйти из класса", f"leave:{class_id}")],
        ])

    def lesson_started(self, user_id: int, c, lesson_id: str) -> None:
        """Шаг 10."""
        self.api.send(user_id, f"Урок начался: {c.name}, {c.subject}.", [[self.open_app("Войти в урок", f"l{lesson_id}")]])

    def demo(self, user: dict) -> None:
        lesson = db.demo_lesson(user["user_id"], full_name(user))
        self.api.send(
            user["user_id"],
            "Демо-урок: вы учитель, а класс заходит записью урока истории. "
            "Ассистент слушает каждого ученика, и в панели класса растут шкалы участия.",
            [[self.open_app("Открыть демо", f"l{lesson.id}")]],
        )

    def summary(self, user_id: int, text: str, class_id: int | None) -> None:
        """Сводка учителю после урока (#21): под ней «Начать урок» следующего урока класса."""
        buttons = [[self.open_app("Начать урок", f"c{class_id}")]] if class_id is not None else None
        self.api.send(user_id, text, buttons, markdown=True)

    def card(self, user_id: int, text: str) -> None:
        """Карточка ученику: кнопок нет, её читают и закрывают."""
        self.api.send(user_id, text, markdown=True)

    def open_app(self, text: str, payload: str) -> dict:
        """Кнопка, которая открывает мини-приложение бота; payload приходит в start_param.

        c<id> — учитель начинает урок класса, l<id> — открыть урок.
        """
        return {"type": "open_app", "text": text, "web_app": self.bot_name, "payload": payload}


# ---------- сводка и карточка: тексты по design/bot-summary.html ----------

MAX_NAMES = 4  # дальше «и ещё N»: полный список — в мини-приложении


def _names(ids: list[str], names: dict[str, str]) -> str:
    shown = [names[i] for i in ids if i in names]
    rest = len(shown) - MAX_NAMES
    return ", ".join(shown[:MAX_NAMES]) + (f" и ещё {rest}" if rest > 0 else "")


def _title(roster) -> str:
    subject = roster.subject[:1].lower() + roster.subject[1:]
    return f"{roster.class_name}, {subject}"


def summary_text(roster, summary, minutes: int) -> str:
    """Сводка учителю одним сообщением: пустые блоки не показываем, «кого спросить» — внизу, ближе к кнопке."""
    names = {s.id: s.name for s in roster.students}
    about = [roster.topic] if roster.topic else []
    about += [f"{minutes} {plural(minutes, 'минута', 'минуты', 'минут')}", students_count(len(roster.students))]
    blocks = [f"**Итоги урока: {_title(roster)}**\n" + ", ".join(about)]
    if summary.carried:
        blocks.append(f"**Вытянули урок**\n{_names(summary.carried, names)}")
    if summary.dropped:
        blocks.append(f"**Выпали**\n{_names(summary.dropped, names)}")
    if summary.weak_topics:
        blocks.append("**Класс не уверен**\n" + ", ".join(summary.weak_topics))
    asks = [a for a in summary.ask_next if a.student_id in names]
    for i, a in enumerate(asks):
        head = "**Спросить в следующий раз**\n" if i == 0 else ""
        blocks.append(f"{head}{names[a.student_id]} — {a.topic}\n«{a.question}»")
    if len(blocks) == 1:
        blocks.append("Для сводки данных пока мало: нужны хотя бы несколько реплик учеников.")
    return "\n\n".join(blocks)


def card_text(roster, card) -> str:
    """Карточка ученика: про него на «ты», без ступени — в карточке она читалась бы как оценка."""
    blocks = [f"**Твоя карточка: {_title(roster)}**" + (f"\n{roster.topic}" if roster.topic else "")]
    if card.went_well:
        blocks.append("**Что получилось**\n" + "\n".join(card.went_well))
    if card.to_repeat:
        blocks.append("**Что повторить**\n" + "\n".join(card.to_repeat))
    blocks.append("Карточка не показывается классу.")
    return "\n\n".join(blocks)


def full_name(user: dict) -> str:
    """Имя из профиля MAX: «Иван Иванов»."""
    parts = [user.get("first_name") or "", user.get("last_name") or ""]
    name = re.sub(r"\s+", " ", " ".join(parts)).strip()
    return name or user.get("name") or "Без имени"


# ---------- запуск ----------

_bot: Bot | None = None


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
