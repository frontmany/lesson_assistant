// Стартовая страница мини-приложения (#24): кто открыл и что открыть.
// Кнопка бота передаёт урок — сразу ведём в него. Без урока — «Мои классы» по
// design/classes.html: учитель открывает класс (js/class.js) и создаёт новый,
// ученик входит в идущий урок; всем — «Посмотреть демо».

import { openClass } from "./class.js";
import { ask } from "./confirm.js";
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
  // урок идёт — это главное, предмет не нужен
  sub.textContent = state; // без предмета: строка только про состояние класса
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

// Роль на этом устройстве: teacher, student или none (нажал «Выйти»). Без неё — по классам человека
function storedRole() {
  try {
    return localStorage.getItem("role");
  } catch {
    return null; // хранилище недоступно: роль по классам
  }
}

function setRole(role) {
  try {
    localStorage.setItem("role", role);
  } catch {
    // см. storedRole()
  }
}

async function demo() {
  try {
    const { lesson_id: lesson } = await api("/api/demo", { method: "POST" });
    go("teacher.html", lesson, true);
  } catch (err) {
    say(err.message);
  }
}

// Выбор роли — как приветствие бота: «Я учитель» или «Я ученик»
function welcome() {
  show("welcomeView");
  $("asTeacher").onclick = () => { setRole("teacher"); home(); };
  $("asStudent").onclick = () => { setRole("student"); home(); };
}

async function home() {
  const all = await api("/api/me/classes").catch(() => []);
  let role = storedRole();
  if (!role) role = all.some((c) => c.role === "teacher") ? "teacher" : all.length ? "student" : "none";
  if (role === "none") return welcome();
  // в роли — только её классы: ученик не видит классы, где он учитель, и наоборот
  const mine = all.filter((c) => c.role === role);
  show("home");
  $("classes").replaceChildren(...mine.map(row));
  $("classes").hidden = mine.length === 0;
  $("homeEmpty").hidden = mine.length > 0;
  // без классов пустая прокрутка сдвигала «Классов пока нет» вниз: прячем её, если и сообщения нет
  $("homeScroll").hidden = !mine.length && $("text").hidden;
  $("actions").hidden = false;

  $("demo").onclick = demo;
  // учитель создаёт класс, ученик входит в класс по коду
  $("create").textContent = role === "teacher" ? "Создать класс" : "Войти по коду";
  $("create").onclick = () => (role === "teacher" ? openForm() : openCode());
  // «Выйти» — к выбору роли; бот присылает в чат то же приветствие
  $("exit").hidden = false;
  $("exit").onclick = async () => {
    const teaching = role === "teacher" && mine.some((c) => c.lesson);
    const yes = await ask({
      title: "Выйти?",
      text: teaching ? "Идущие уроки завершатся, а потом можно будет заново выбрать роль" : "Потом можно будет заново выбрать роль",
      ok: "Выйти",
    });
    if (!yes) return;
    setRole("none");
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

// Класс удалён: список классов и несколько секунд «Вернуть» — сервер хранит класс 30 дней
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

// Класс открывают с одними и теми же действиями: со списка, после создания и после правки
function openClassScreen(id) {
  openClass(id, {
    onBack: home,
    // урок уже идёт — возвращаемся в него, иначе начинаем новый
    onStart: (cid, lesson) => (lesson ? go("teacher.html", lesson) : startLesson(cid).catch((err) => say(err.message))),
    onError: say,
    onDeleted: classDeleted,
    onEdit: (c) => openForm(c),
  });
}

// «Создать класс» и карандаш «Изменить класс» — одна форма: класс и предмет, как в боте («10А, история»).
// После создания открывается пустой класс с «Пригласить», после правки — тот же класс
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
  // start_param передаёт кнопка бота; ?start= — то же самое для разработки
  const start = (await signIn()) ?? new URLSearchParams(location.search).get("start");
  if (start === "demo") {
    // «Посмотреть демо» в боте: свой демо-урок, сразу в звонок
    const { lesson_id: lesson } = await api("/api/demo", { method: "POST" });
    go("teacher.html", lesson, true);
  } else if (start?.startsWith("c")) {
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
