// Стартовая страница мини-приложения (#24): кто открыл и что открыть.
// Кнопка бота передаёт урок — сразу ведём в него. Без урока показываем классы
// человека: учителю «Начать урок», ученику «Войти в урок», если урок идёт; всем —
// демо и чат с ботом, где создают класс и входят в него.

import { openClass } from "./class.js";
import { api, signedIn, signIn } from "./lesson.js";

const $ = (id) => document.getElementById(id);
const inMax = Boolean(window.WebApp?.initData);

// данные запуска (#WebAppData=…) несём дальше: экран урока проверит их сам
// now=1: урок начинают, а не присоединяются — экран урока входит сразу, без «Готовы войти?»
const go = (page, lesson, now = false) =>
  location.replace(`${page}?lesson=${lesson}${now ? "&now=1" : ""}${location.hash}`);

function say(text) {
  $("text").textContent = text;
  $("text").hidden = !text;
}

async function startLesson(classId) {
  const { lesson_id: lesson } = await api(`/api/classes/${classId}/lessons`, { method: "POST" });
  go("teacher.html", lesson, true);
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
    return b;
  };
  // подписи как в design/start.html: идёт урок — это важнее числа учеников
  if (c.lesson) meta.classList.add("live");
  if (c.role === "teacher") {
    // «Открыть» — экран класса: ученики и «Пригласить»
    action("Открыть", async () =>
      openClass(c.id, { onBack: home, onStart: (id) => startLesson(id).catch((err) => say(err.message)), onError: say }),
    ).classList.add("open");
    if (c.lesson) meta.textContent = "Урок идёт";
    else if (c.students) meta.textContent = `${c.students} ${plural(c.students, "ученик", "ученика", "учеников")}`;
    else meta.textContent = "Пока нет учеников";
    if (c.lesson) action("Вернуться в урок", async () => go("teacher.html", c.lesson));
    else if (c.students) action("Начать урок", () => startLesson(c.id));
    else {
      // пустой класс: кнопка неактивна, а почему — объясняем только по нажатию
      const b = action("Начать урок", async () => hint(el, "Урок начинается, когда в классе есть хотя бы один ученик. Пригласить учеников — в «Открыть»"));
      b.classList.add("off");
      b.setAttribute("aria-disabled", "true");
    }
  } else {
    meta.textContent = c.lesson ? "Урок идёт" : "Урок пока не идёт";
    if (c.lesson) action("Войти в урок", async () => go("student.html", c.lesson));
  }
  return el;
}

function hint(card, text) {
  let p = card.querySelector(".card-hint");
  if (!p) {
    p = Object.assign(document.createElement("p"), { className: "card-hint" });
    card.append(p);
  }
  p.textContent = text;
  clearTimeout(p.timer);
  p.timer = setTimeout(() => p.remove(), 5000);
}

async function home() {
  const mine = await api("/api/me/classes").catch(() => []);
  $("classes").replaceChildren(...mine.map(card));
  $("classes").hidden = mine.length === 0;
  if (!mine.length) say("Классов пока нет. Создайте класс или войдите по коду в чате с ботом.");
  $("actions").hidden = false;

  $("demo").onclick = async () => {
    try {
      const { lesson_id: lesson } = await api("/api/demo", { method: "POST" });
      go("teacher.html", lesson, true);
    } catch (err) {
      say(err.message);
    }
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
    // учителю бот присылает l<id> только для демо: это начало урока, а ученик присоединяется
    go(me.role === "teacher" ? "teacher.html" : "student.html", lesson, me.role === "teacher");
  } else if (inMax || signedIn()) {
    await home();
  } else {
    say("Откройте мини-приложение в MAX: в чате с ботом «Идея фикс».");
    // ссылки на демо-класс без MAX работают только при DEV_AUTH=1: иначе их не показываем
    const health = await fetch("/api/health").then((r) => r.json()).catch(() => ({}));
    $("dev").hidden = !health.dev_auth;
  }
} catch (err) {
  $("title").textContent = "Не удалось открыть урок";
  say(err.message);
}
