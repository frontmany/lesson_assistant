// Экран ученика в звонке (#22) по макету Варвары design/student-call.html. Код: Фёдор.
// Учитель крупно, своя плитка в углу, одноклассники лентой без шкал, своя шкала
// из четырёх ступеней (только растёт, можно скрыть). Учитель может выключить
// ученику аналитику — тогда вместо шкалы «Аналитика выключена».

import { DisconnectReason, joinCall } from "./call.js";
import { LEVEL_LABELS, botLink, failText, loadRoster, signIn, subscribe, whoAmI } from "./lesson.js";
import { inMax, micAccess, openInBrowser, openMaxSettings, openSystemSettings } from "./mic.js";

const { RoomEvent, Track } = window.LivekitClient;

const TOAST_MS = 4000;
const NOMIC = {
  "max-settings": ["Разрешите микрофон для урока в настройках MAX", "Открыть настройки MAX", openMaxSettings],
  "system-settings": ["Разрешите MAX доступ к микрофону в настройках телефона", "Открыть настройки телефона", openSystemSettings],
  update: ["Обновите MAX или откройте урок в браузере", "Открыть в браузере", openInBrowser],
  browser: ["В браузере микрофон работает", "Открыть в браузере", openInBrowser],
  denied: ["Разрешите микрофон в настройках браузера", null, null],
};

const $ = (id) => document.getElementById(id);

// ошибка входа (устаревшая ссылка в браузере, нет сети) — не тупик: текст и кнопки (#98, п. 4)
await signIn().catch((err) => fail(err));
// Ученик — тот, кого сервер узнал по данным запуска MAX (#24); учителя отправляем на его экран
const me = await whoAmI().catch((err) => fail(err));
if (me.role === "teacher") location.replace(`teacher.html${location.search}`);
const studentId = me.participant_id;
let roster = await loadRoster().catch((err) => fail(err));

const prefs = { mic: true, cam: false };
let room = null;
let leaving = false;
let startedAt = 0;
let reached = 1; // шкала только растёт
let analytics = me.analytics;
let reconnecting = false;
const videos = new Map();

$("previewAvatar").textContent = initials(me.name);
// Плашка шкалы одна на весь урок: при перерисовке ряда она переезжает в новую свою плитку
const meterPill = $("scale");
buildSteps();
renderScale();

// ---------- до входа ----------

function lobby(kind = "ready", detail = "") {
  show("lobby");
  const actions = $("lobbyActions");
  actions.replaceChildren();
  if (kind === "checking") {
    setLobbyText("Проверяем микрофон", "");
    actions.append(button("primary", "Проверяем…", null, true, true));
  } else if (kind === "connecting") {
    setLobbyText("Подключаемся к уроку", "");
    actions.append(button("primary", "Подключаемся…", null, true, true));
  } else if (kind in NOMIC) {
    // без серой подсказки под заголовком: что делать, говорит сама кнопка
    const [, action, run] = NOMIC[kind];
    setLobbyText("Нет доступа к микрофону", "");
    if (action) actions.append(button("primary", action, run));
    if (run !== openInBrowser) actions.append(button(action ? "secondary" : "primary", "Попробовать снова", () => enter()));
    actions.append(button("link", "Войти без микрофона", () => enter({ mic: false })));
  } else if (kind === "ended" || kind === "over") {
    // ended — учитель только что завершил урок и закрыл звонок; over — открыта кнопка закончившегося урока
    setLobbyText(kind === "ended" ? "Урок завершён" : "Урок уже закончился", "Карточка придёт в чат с ботом");
    actions.append(button("primary", "К классам", toClasses));
    $("preview").hidden = true;
    return;
  } else {
    // без прошедшего времени и рода: пол ученика неизвестен
    // концепция: о том, что ассистент слушает, ученику говорят явно, а не строкой в соглашении
    setLobbyText(kind === "left" ? "Ты не в звонке" : "Войти в урок?", detail || "Ассистент слушает урок, чтобы вести шкалу участия");
    actions.append(button("primary", kind === "left" ? "Вернуться в урок" : "Войти в урок", () => enter()));
  }
  setIconButton($("pvMic"), kind in NOMIC ? false : prefs.mic, "mic", "микрофон");
  setIconButton($("pvCam"), prefs.cam, "cam", "камеру");
}

function setLobbyText(title, text) {
  $("lobbyTitle").textContent = title;
  $("lobbyText").textContent = text;
  $("lobbyText").hidden = !text;
}

$("pvMic").onclick = () => {
  prefs.mic = !prefs.mic;
  setIconButton($("pvMic"), prefs.mic, "mic", "микрофон");
};
$("pvCam").onclick = () => {
  prefs.cam = !prefs.cam;
  setIconButton($("pvCam"), prefs.cam, "cam", "камеру");
};

async function enter({ mic = prefs.mic } = {}) {
  if (mic) {
    lobby("checking");
    const access = await micAccess();
    if (access !== "ok") return lobby(access);
  }
  lobby("connecting");
  try {
    room = await joinCall(studentId, { mic, cam: prefs.cam, onState });
  } catch (err) {
    room = null;
    if (err.name === "NotAllowedError") return lobby(inMax() ? "browser" : "denied");
    return lobby("ready", `Не удалось войти: ${err.message}`);
  }
  leaving = false;
  startedAt = Date.now();
  roster = await loadRoster().catch(() => roster);
  for (const event of [
    RoomEvent.ParticipantConnected, RoomEvent.ParticipantDisconnected,
    RoomEvent.TrackSubscribed, RoomEvent.TrackUnsubscribed,
    RoomEvent.TrackMuted, RoomEvent.TrackUnmuted,
    RoomEvent.LocalTrackPublished, RoomEvent.LocalTrackUnpublished,
    RoomEvent.ActiveSpeakersChanged,
  ]) room.on(event, () => requestAnimationFrame(renderCall));
  show("call");
  if (!mic) toast("Микрофон выключен");
  renderCall();
}

function onState(state, reason) {
  reconnecting = state === "reconnecting" || state === "signalReconnecting";
  if (state === "disconnected" && !leaving && room) {
    room = null;
    // учитель нажал «Завершить урок»: сервер закрыл комнату у всего класса
    if (reason === DisconnectReason.ROOM_DELETED) return lobby("ended");
    return lobby("ready", reason === DisconnectReason.DUPLICATE_IDENTITY
      ? "Урок открыт в другом окне, здесь звонок закрыт"
      : "Связь с уроком потеряна");
  }
  if (room) renderCall();
}

// ---------- звонок ----------

function nameOf(p) {
  if (p === room.localParticipant) return "Вы";
  if (p.identity === roster.teacher.id || p.identity === roster.virtual_teacher?.id) return short(roster.teacher.name);
  return roster.students.find((s) => s.id === p.identity)?.name ?? (p.name || p.identity);
}

function isTeacher(p) {
  return p.identity === roster.teacher.id || p.identity === roster.virtual_teacher?.id;
}

function videoFor(p) {
  const pub = p.getTrackPublication(Track.Source.Camera);
  const track = pub?.track;
  if (!track || pub.isMuted) return null;
  if (!videos.has(track.sid)) videos.set(track.sid, track.attach());
  return videos.get(track.sid);
}

function micOff(p) {
  const pub = p.getTrackPublication(Track.Source.Microphone);
  return !pub || pub.isMuted;
}

function face(el, p, avatarText) {
  const video = videoFor(p);
  if (video) el.append(video);
  else {
    el.classList.add("nocam");
    el.append(span("avatar", initials(avatarText)));
  }
  if (micOff(p)) el.append(iconSpan("micoff", "mic-off"));
  // Подписи на плитке нет, как у учителя: имя во всплывающей подсказке и для экранного чтения
  el.title = nameOf(p);
  if (!el.hasAttribute("aria-label")) el.setAttribute("aria-label", nameOf(p));
}

function renderCall() {
  if (!room) return;
  const remote = [...room.remoteParticipants.values()];
  const teacher = remote.find(isTeacher);
  const classmates = remote.filter((p) => !isTeacher(p));

  const stage = [];
  const main = div("st-tile main");
  if (teacher) {
    face(main, teacher, roster.teacher.name);
    main.classList.toggle("speaking", teacher.isSpeaking);
  } else {
    main.classList.add("nocam");
    main.append(span("name", "Учитель ещё не вошёл"));
  }
  stage.push(main);
  $("stage").replaceChildren(...stage);

  // себя — первым в общем ряду с одноклассниками, а не поверх учителя
  const mine = div("mini me");
  face(mine, room.localParticipant, me.name);
  mine.append(meterPill); // своя шкала — внизу своей плитки
  $("strip").replaceChildren(mine, ...classmates.map((p) => {
    const el = div(`mini${p.isSpeaking ? " speaking" : ""}`);
    el.setAttribute("aria-label", `${p.isSpeaking ? "Говорит: " : ""}${nameOf(p)}`);
    face(el, p, nameOf(p));
    return el;
  }));

  $("banner").hidden = !reconnecting;
  setIconButton($("btnMic"), room.localParticipant.isMicrophoneEnabled, "mic", "микрофон");
  setIconButton($("btnCam"), room.localParticipant.isCameraEnabled, "cam", "камеру");
}

setInterval(() => {
  if (!room) return;
  const s = Math.floor((Date.now() - startedAt) / 1000);
  $("timer").textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}, 1000);

$("btnMic").onclick = async () => {
  const on = !room.localParticipant.isMicrophoneEnabled;
  if (on && (await micAccess()) !== "ok") return toast("Нет доступа к микрофону");
  try {
    await room.localParticipant.setMicrophoneEnabled(on);
  } catch {
    return toast("Нет доступа к микрофону");
  }
  if (!on) toast("Микрофон выключен");
  renderCall();
};

$("btnCam").onclick = async () => {
  try {
    await room.localParticipant.setCameraEnabled(!room.localParticipant.isCameraEnabled);
  } catch {
    toast("Камера недоступна");
  }
  renderCall();
};

$("btnLeave").onclick = async () => {
  leaving = true;
  await room.disconnect();
  room = null;
  lobby("left");
};

// ---------- своя шкала ----------

function buildSteps() {
  $("steps").replaceChildren(...Object.keys(LEVEL_LABELS).map((n) => {
    const li = document.createElement("li");
    li.dataset.n = n;
    return li;
  }));
}

// Шкала только растёт: сегменты до достигнутой ступени заполнены, сверху её название.
// Учитель выключил аналитику — шкала серая, вместо ступени «Аналитика выключена»
function renderScale() {
  const off = !analytics;
  const label = off ? "Аналитика выключена" : `Твоё участие: ${LEVEL_LABELS[reached]}`;
  meterPill.classList.toggle("off", off);
  meterPill.title = label;
  meterPill.setAttribute("aria-label", label);
  for (const li of $("steps").children) li.classList.toggle("on", !off && +li.dataset.n <= reached);
}

subscribe((event) => {
  if (event.type === "analytics" && (event.student_id == null || event.student_id === studentId)) {
    analytics = event.enabled;
    if (analytics) reached = 1; // аналитику включили снова: шкала с первой ступени, как у сервера
    renderScale();
  }
  if (event.type === "scale" && event.student_id === studentId && analytics) {
    if (event.level > reached) $("status").textContent = `Ступень: ${LEVEL_LABELS[event.level]}`;
    reached = Math.max(reached, event.level);
    renderScale();
  }
});

// ---------- общее ----------

function show(screen) {
  $("lobby").hidden = screen !== "lobby";
  $("call").hidden = screen !== "call";
}

let toastTimer;
function toast(text) {
  const el = $("toast");
  el.textContent = text;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), TOAST_MS);
}

function fail(err) {
  show("lobby");
  setLobbyText("Не удалось открыть урок", failText(err));
  const actions = $("lobbyActions");
  // устаревшую ссылку повтор не спасёт: урок открывается заново из чата с ботом
  actions.replaceChildren(...(err?.status === 401 ? [] : [button("primary", "Попробовать снова", () => location.reload())]));
  if (inMax()) actions.append(button(err?.status === 401 ? "primary" : "secondary", "К классам", toClasses));
  else {
    // в браузере урок заново открывается из чата с ботом
    botLink().then((link) => {
      if (link) actions.append(button(err?.status === 401 ? "primary" : "secondary", "Открыть из чата с ботом", () => location.assign(link)));
    });
  }
  $("preview").hidden = true;
  throw err;
}

function setIconButton(btn, on, icon, what) {
  btn.classList.toggle("off", !on);
  btn.querySelector("use").setAttribute("href", `#${icon}${on ? "" : "-off"}`);
  const label = `${on ? "Выключить" : "Включить"} ${what}`;
  btn.setAttribute("aria-label", label);
  btn.title = label;
}

function button(cls, text, onclick, disabled = false, spin = false) {
  const el = document.createElement("button");
  el.className = cls;
  if (spin) el.append(span("spin", ""));
  el.append(text);
  el.disabled = disabled;
  if (onclick) el.onclick = onclick;
  return el;
}

function initials(name) {
  return name.split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();
}

function short(name) {
  const [first, last] = name.split(/\s+/);
  return last ? `${first} ${last[0]}.` : first;
}

function div(cls, text) {
  const el = document.createElement("div");
  el.className = cls;
  if (text) el.textContent = text;
  return el;
}

function span(cls, text) {
  const el = document.createElement("span");
  el.className = cls;
  el.textContent = text;
  return el;
}

function svg(icon) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  el.setAttribute("class", "i");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#${icon}`);
  el.append(use);
  return el;
}

function iconSpan(cls, icon) {
  const el = span(cls, "");
  el.append(svg(icon));
  return el;
}

// К списку классов; данные запуска MAX (#WebAppData) несём с собой, а home=1 — чтобы
// стартовая страница не открыла этот же урок снова по start_param из них
function toClasses() {
  location.replace(`index.html?home=1${location.hash}`);
}

$("lobbyBack").onclick = toClasses;

lobby(me.ended ? "over" : "ready");
