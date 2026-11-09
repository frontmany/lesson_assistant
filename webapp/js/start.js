// Стартовая страница приложения

import { openClass } from "./class.js";
import { ask } from "./confirm.js";
import { api, signedIn, signIn } from "./lesson.js";

const $ = (id) => document.getElementById(id);
const inMax = Boolean(window.WebApp?.initData);

const go = (page, lesson, now = false) =>
  location.replace(`${page}?lesson=${lesson}${now ? "&now=1" : ""}${location.hash}`);

function say(text) {
  $("text").textContent = text;
  $("text").hidden = !text;
  $("homeScroll").hidden = false;
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
  sub.textContent = state;
  sub.classList.toggle("live", Boolean(c.lesson));
  if (c.role === "teacher") {
    el.onclick = () => openClassScreen(c.id);
  } else if (c.lesson) {
    el.onclick = () => go("student.html", c.lesson);
  }
  li.append(el);
  return li;
}

function show(view) {
  for (const id of ["home", "classView", "inviteView", "createView", "welcomeView", "codeView"]) $(id).hidden = id !== view;
}

// Роль хранится на сервере и общая с ботом
const setRole = (role) => api("/api/me/role", { method: "PUT", body: { role } });

async function demo() {
  try {
    const { lesson_id: lesson } = await api("/api/demo", { method: "POST" });
    go("teacher.html", lesson, true);
  } catch (err) {
    say(err.message);
  }
}

function welcome() {
  show("welcomeView");
  const pick = (role) => async () => {
    try {
      await setRole(role);
    } catch (err) {
      return say(err.message);
    }
    home();
  };
  $("asTeacher").onclick = pick("teacher");
  $("asStudent").onclick = pick("student");
}

async function home() {
  const [all, { role }] = await Promise.all([
    api("/api/me/classes").catch(() => []),
    api("/api/me/role").catch(() => ({ role: "none" })),
  ]);
  if (role === "none") return welcome();
  const mine = all.filter((c) => c.role === role);
  show("home");
  $("classes").replaceChildren(...mine.map(row));
  $("classes").hidden = mine.length === 0;
  $("homeEmpty").hidden = mine.length > 0;
  $("homeScroll").hidden = !mine.length && $("text").hidden;
  $("actions").hidden = false;

  $("demo").onclick = demo;
  $("create").textContent = role === "teacher" ? "Создать класс" : "Войти по коду";
  $("create").onclick = () => (role === "teacher" ? openForm() : openCode());
  $("exit").hidden = false;
  $("exit").onclick = async () => {
    const teaching = role === "teacher" && mine.some((c) => c.lesson);
    const yes = await ask({
      title: "Выйти?",
      text: teaching ? "Идущие уроки завершатся, а потом можно будет заново выбрать роль" : "Потом можно будет заново выбрать роль",
      ok: "Выйти",
    });
    if (!yes) return;
    await api("/api/me/exit", { method: "POST" }).catch(() => {});
    welcome();
  };
}

function openCode() {
  show("codeView");
  $("codeError").hidden = true;
  $("classCode").value = "";
  $("classCode").focus();
  $("codeBack").onclick = () => home();
  $("codeView").onsubmit = async (e) => {
    e.preventDefault();
    try {
      await api("/api/classes/join", { method: "POST", body: { code: $("classCode").value } });
      home();
    } catch (err) {
      $("codeError").textContent = err.message;
      $("codeError").hidden = false;
    }
  };
}

let toastTimer;
async function classDeleted(c) {
  await home();
  clearTimeout(toastTimer);
  $("homeToastText").textContent = `Класс ${c.name} удалён`;
  $("homeToast").hidden = false;
  $("homeToastBtn").onclick = async () => {
    $("homeToast").hidden = true;
    await api(`/api/classes/${c.id}/restore`, { method: "POST" }).catch((err) => say(err.message));
    home();
  };
  toastTimer = setTimeout(() => ($("homeToast").hidden = true), 5000);
}

function openClassScreen(id) {
  openClass(id, {
    onBack: home,
    onStart: (cid, lesson) => (lesson ? go("teacher.html", lesson) : startLesson(cid).catch((err) => say(err.message))),
    onError: say,
    onDeleted: classDeleted,
    onEdit: (c) => openForm(c),
  });
}

function openForm(c = null) {
  show("createView");
  $("formTitle").textContent = c ? "Изменить класс" : "Новый класс";
  $("formSubmit").textContent = c ? "Сохранить" : "Создать";
  $("createError").hidden = true;
  $("newName").value = c?.name ?? "";
  $("newSubject").value = c?.subject ?? "";
  $("newName").focus();
  $("createBack").onclick = () => (c ? openClassScreen(c.id) : home());
  $("createView").onsubmit = async (e) => {
    e.preventDefault();
    const body = { name: $("newName").value, subject: $("newSubject").value };
    try {
      const id = c
        ? (await api(`/api/classes/${c.id}`, { method: "PATCH", body }), c.id)
        : (await api("/api/classes", { method: "POST", body })).id;
      openClassScreen(id);
    } catch (err) {
      $("createError").textContent = err.message;
      $("createError").hidden = false;
    }
  };
}

try {
  // home=1 не даёт снова открыть урок по start_param
  const params = new URLSearchParams(location.search);
  const launch = await signIn();
  const start = params.has("home") ? null : launch ?? params.get("start");
  if (start === "demo") {
    const { lesson_id: lesson } = await api("/api/demo", { method: "POST" });
    go("teacher.html", lesson, true);
  } else if (start?.startsWith("c")) {
    await startLesson(start.slice(1));
  } else if (start?.startsWith("l")) {
    const lesson = start.slice(1);
    const me = await api(`/api/lessons/${lesson}/me`);
    go(me.role === "teacher" ? "teacher.html" : "student.html", lesson, me.role === "teacher");
  } else if (inMax || signedIn()) {
    await home();
  } else {
    say("Откройте мини-приложение в MAX: в чате с ботом «Идея фикс».");
    const health = await fetch("/api/health").then((r) => r.json()).catch(() => ({}));
    $("dev").hidden = !health.dev_auth;
  }
} catch (err) {
  $("title").textContent = "Не удалось открыть урок";
  say(err.message);
}
