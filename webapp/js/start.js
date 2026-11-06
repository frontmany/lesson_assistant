// Стартовая страница мини-приложения (#24): кто открыл и что открыть.
// Кнопка бота передаёт урок — сразу ведём в него. Без урока — «Мои классы» по
// design/classes.html: учитель открывает класс (js/class.js) и создаёт новый,
// ученик входит в идущий урок; всем — «Посмотреть демо».

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

const NEXT = '<svg class="i go" aria-hidden="true"><use href="#i-next"/></svg>';

// Строка класса как в design/classes.html: «10А» в кружке, «24 ученика · история», стрелка.
// Учителю строка открывает экран класса, ученику — идущий урок; без урока ученику открывать нечего
function row(c) {
  const li = document.createElement("li");
  const opens = c.role === "teacher" || c.lesson;
  const el = document.createElement(opens ? "button" : "div");
  el.className = "row";
  el.innerHTML = `<span class="ava" aria-hidden="true"></span><span class="main"><span class="name"></span><span class="sub"></span></span>${opens ? NEXT : ""}`;
  el.querySelector(".ava").textContent = c.name.slice(0, 3);
  el.querySelector(".name").textContent = c.name;
  const sub = el.querySelector(".sub");
  let state;
  if (c.lesson) state = "Урок идёт";
  else if (c.role === "teacher") state = c.students ? `${c.students} ${plural(c.students, "ученик", "ученика", "учеников")}` : "Нет учеников";
  else state = "Урок пока не идёт";
  sub.textContent = `${state} · ${c.subject}`;
  sub.classList.toggle("live", Boolean(c.lesson));
  if (c.role === "teacher") {
    el.onclick = () => openClass(c.id, {
      onBack: home,
      // урок уже идёт — возвращаемся в него, иначе начинаем новый
      onStart: (id, lesson) => (lesson ? go("teacher.html", lesson) : startLesson(id).catch((err) => say(err.message))),
      onError: say,
    });
  } else if (c.lesson) {
    el.onclick = () => go("student.html", c.lesson);
  }
  li.append(el);
  return li;
}

function show(view) {
  for (const id of ["home", "classView", "inviteView", "createView"]) $(id).hidden = id !== view;
}

async function home() {
  show("home");
  const mine = await api("/api/me/classes").catch(() => []);
  $("classes").replaceChildren(...mine.map(row));
  $("classes").hidden = mine.length === 0;
  $("homeEmpty").hidden = mine.length > 0;
  $("actions").hidden = false;

  $("demo").onclick = async () => {
    try {
      const { lesson_id: lesson } = await api("/api/demo", { method: "POST" });
      go("teacher.html", lesson, true);
    } catch (err) {
      say(err.message);
    }
  };
  $("create").onclick = openCreate;
}

// «Создать класс»: класс и предмет, как в боте («10А, история»). Созданный класс сразу
// открывается — пустой, с «Пригласить»
function openCreate() {
  show("createView");
  $("createError").hidden = true;
  $("newName").value = "";
  $("newSubject").value = "";
  $("newName").focus();
  $("createBack").onclick = () => home();
  $("createView").onsubmit = async (e) => {
    e.preventDefault();
    try {
      const { id } = await api("/api/classes", {
        method: "POST", body: { name: $("newName").value, subject: $("newSubject").value },
      });
      openClass(id, { onBack: home, onStart: (cid) => startLesson(cid).catch((err) => say(err.message)), onError: say });
    } catch (err) {
      $("createError").textContent = err.message;
      $("createError").hidden = false;
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
