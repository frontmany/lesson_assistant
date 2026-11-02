// Стартовая страница мини-приложения (#24): кто открыл и что открыть.
// Кнопка бота передаёт урок — сразу ведём в него. Без урока показываем классы
// человека: учителю «Начать урок», ученику «Войти в урок», если урок идёт; всем —
// демо и чат с ботом, где создают класс и входят в него.

import { api, signIn } from "./lesson.js";

const $ = (id) => document.getElementById(id);
const inMax = Boolean(window.WebApp?.initData);

// данные запуска (#WebAppData=…) несём дальше: экран урока проверит их сам
const go = (page, lesson) => location.replace(`${page}?lesson=${lesson}${location.hash}`);

function say(text) {
  $("text").textContent = text;
  $("text").hidden = !text;
}

async function startLesson(classId) {
  const { lesson_id: lesson } = await api(`/api/classes/${classId}/lessons`, { method: "POST" });
  go("teacher.html", lesson);
}

function plural(n, one, few, many) {
  if (n % 10 === 1 && n % 100 !== 11) return one;
  if (n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 12 || n % 100 > 14)) return few;
  return many;
}

function card(c) {
  const el = document.createElement("article");
  el.className = "card";
  const info = document.createElement("div");
  const name = Object.assign(document.createElement("div"), { className: "card-name", textContent: `${c.name}, ${c.subject}` });
  const meta = document.createElement("div");
  meta.className = "card-meta";
  info.append(name, meta);
  el.append(info);
  const action = (text, onclick) => {
    const b = Object.assign(document.createElement("button"), { textContent: text });
    b.onclick = () => onclick().catch((err) => say(err.message));
    el.append(b);
  };
  if (c.role === "teacher") {
    meta.textContent = c.students
      ? `${c.students} ${plural(c.students, "ученик", "ученика", "учеников")}`
      : "Пока никого: пригласите учеников в чате с ботом";
    if (c.lesson) action("Вернуться в урок", async () => go("teacher.html", c.lesson));
    else if (c.students) action("Начать урок", () => startLesson(c.id));
  } else {
    meta.textContent = c.lesson ? "Урок идёт" : "Урок пока не идёт";
    if (c.lesson) action("Войти в урок", async () => go("student.html", c.lesson));
  }
  return el;
}

async function home() {
  const mine = await api("/api/me/classes").catch(() => []);
  $("classes").replaceChildren(...mine.map(card));
  $("classes").hidden = mine.length === 0;
  if (!mine.length) say("Создать класс или войти в него по коду можно в чате с ботом. А как идёт урок, покажет демо.");
  $("actions").hidden = false;

  $("demo").onclick = async () => {
    try {
      const { lesson_id: lesson } = await api("/api/demo", { method: "POST" });
      go("teacher.html", lesson);
    } catch (err) {
      say(err.message);
    }
  };
  const { bot } = await api("/api/app").catch(() => ({}));
  $("chat").hidden = !bot;
  $("chat").onclick = () => {
    const url = `https://max.ru/${bot}`;
    // MAX web — это браузер: новая вкладка надёжнее моста; в приложении MAX — его ссылка
    if (!inMax || window.WebApp.platform === "web") window.open(url, "_blank", "noopener");
    else window.WebApp.openMaxLink(url);
  };
}

try {
  // start_param передаёт кнопка бота; ?start= — то же самое для разработки
  const start = (await signIn()) ?? new URLSearchParams(location.search).get("start");
  if (start?.startsWith("c")) {
    await startLesson(start.slice(1));
  } else if (start?.startsWith("l")) {
    const lesson = start.slice(1);
    const me = await api(`/api/lessons/${lesson}/me`);
    go(me.role === "teacher" ? "teacher.html" : "student.html", lesson);
  } else if (inMax) {
    await home();
  } else {
    say("Откройте мини-приложение в MAX: в чате с ботом «Идея фикс».");
    $("dev").hidden = false;
  }
} catch (err) {
  $("title").textContent = "Не удалось открыть урок";
  say(err.message);
}
