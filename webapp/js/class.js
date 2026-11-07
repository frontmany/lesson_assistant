// Экран класса на стартовой странице (design/classes.html): ученики по алфавиту,
// «Убрать из класса» сразу и «Вернуть» несколько секунд, «Пригласить» со ссылкой
// и кодом, «Начать урок». Только учителю этого класса — сервер проверяет сам.

import { ask } from "./confirm.js";
import { api } from "./lesson.js";

const $ = (id) => document.getElementById(id);
const UNDO_MS = 5000;

function plural(n, one, few, many) {
  if (n % 10 === 1 && n % 100 !== 11) return one;
  if (n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 12 || n % 100 > 14)) return few;
  return many;
}

const initials = (name) => name.split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();

function show(view) {
  for (const id of ["home", "classView", "inviteView", "createView", "welcomeView", "codeView"]) $(id).hidden = id !== view;
}

let undoTimer;

export async function openClass(classId, { onBack, onStart, onError, onDeleted, onEdit }) {
  let c;
  try {
    c = await api(`/api/classes/${classId}`);
  } catch (err) {
    return onError(err.message);
  }
  show("classView");
  $("className").textContent = c.name;
  $("classBack").onclick = () => { closeMenu(); show("home"); onBack(); };
  // урок класса уже идёт — кнопка возвращает в него
  $("classStart").textContent = c.lesson ? "Вернуться в урок" : "Начать урок";
  $("classStart").onclick = () => onStart(c.id, c.lesson);
  $("inviteBtn").onclick = () => openInvite(c);
  $("classEdit").onclick = () => { closeMenu(); onEdit(c); };
  // после вопроса: на списке классов ещё несколько секунд «Вернуть»
  $("classDelete").onclick = async () => {
    closeMenu();
    const yes = await ask({ title: `Удалить класс «${c.name}»?`, text: "Ученики больше не увидят его и его уроки", ok: "Удалить" });
    if (!yes) return;
    try {
      await api(`/api/classes/${c.id}`, { method: "DELETE" });
    } catch (err) {
      return toast(err.message);
    }
    onDeleted(c);
  };

  function render() {
    const n = c.students.length;
    $("classCount").textContent = n ? `${n} ${plural(n, "ученик", "ученика", "учеников")} · ${c.subject}` : c.subject;
    $("studentsScroll").hidden = !n;
    $("classEmpty").hidden = Boolean(n);
    // пустой класс урок не начинает: остаётся только «Пригласить»
    $("classStart").hidden = !n;
    $("students").replaceChildren(...c.students.map(row));
  }

  function row(s) {
    const li = document.createElement("li");
    li.innerHTML = `<div class="row"><span class="ava" aria-hidden="true"></span><span class="main"><span class="name"></span></span>`
      + `<button class="ib" aria-haspopup="menu"><svg class="i"><use href="#i-dots"/></svg></button></div>`;
    li.querySelector(".ava").textContent = initials(s.name);
    li.querySelector(".name").textContent = s.name;
    const dots = li.querySelector("button");
    dots.setAttribute("aria-label", `Меню: ${s.name}`);
    dots.onclick = (e) => {
      e.stopPropagation();
      const open = li.querySelector(".menu");
      closeMenu();
      if (open) return;
      const menu = Object.assign(document.createElement("div"), { className: "menu" });
      menu.setAttribute("role", "menu");
      const remove = Object.assign(document.createElement("button"), { textContent: "Убрать из класса" });
      remove.setAttribute("role", "menuitem");
      remove.onclick = () => removeStudent(s);
      menu.append(remove);
      li.append(menu);
    };
    return li;
  }

  // после вопроса; ошибку ещё исправит «Вернуть»
  async function removeStudent(s) {
    closeMenu();
    if (!(await ask({ title: `Убрать из класса: ${s.name}?`, text: "Ученик больше не попадёт на уроки этого класса", ok: "Убрать" }))) return;
    const at = c.students.indexOf(s);
    try {
      await api(`/api/classes/${c.id}/students/${s.id}`, { method: "DELETE" });
    } catch (err) {
      return toast(err.message);
    }
    c.students.splice(at, 1);
    render();
    toast(`Убрано из класса: ${s.name}`, async () => {
      await api(`/api/classes/${c.id}/students/${s.id}/restore`, { method: "POST" });
      c.students.splice(at, 0, s); // на прежнее место в списке
      render();
    });
  }

  render();
}

function closeMenu() {
  document.querySelectorAll(".menu").forEach((m) => m.remove());
}
document.addEventListener("click", closeMenu);

function toast(text, undo) {
  clearTimeout(undoTimer);
  $("undoText").textContent = text;
  $("undoBtn").hidden = !undo;
  $("undoBtn").onclick = async () => {
    $("undo").hidden = true;
    await undo().catch((err) => toast(err.message));
  };
  $("undo").hidden = false;
  undoTimer = setTimeout(() => ($("undo").hidden = true), UNDO_MS);
}

function openInvite(c) {
  show("inviteView");
  $("inviteTitle").textContent = `Пригласить в ${c.name}, ${c.subject}`; // с предметом: класс «10» без него читается как обрезанный
  $("inviteBack").onclick = () => show("classView");
  // без имени бота ссылки нет: остаётся код, его вводят в чате с ботом
  $("linkCard").hidden = !c.link;
  $("copyLink").hidden = !c.link;
  $("inviteLink").textContent = c.link ? c.link.replace(/^https:\/\//, "") : "";
  $("inviteCode").textContent = c.code;
  $("copyLink").onclick = (e) => copyButton(e.currentTarget, c.link, "Ссылка скопирована");
  $("copyCode").onclick = (e) => copyButton(e.currentTarget, c.code, "Код скопирован");
}

async function copyButton(btn, text, done) {
  const label = btn.dataset.label ?? btn.textContent;
  btn.dataset.label = label;
  btn.textContent = (await copy(text)) ? done : "Не удалось скопировать";
  setTimeout(() => (btn.textContent = label), 2500);
}

async function copy(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // во фрейме MAX буфер обмена через clipboard бывает закрыт: старый способ
    const area = Object.assign(document.createElement("textarea"), { value: text });
    area.style.cssText = "position:fixed;opacity:0";
    document.body.append(area);
    area.select();
    const ok = document.execCommand("copy");
    area.remove();
    return ok;
  }
}
