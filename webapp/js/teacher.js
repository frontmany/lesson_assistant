// Экран звонка учителя (#14) по макету Варвары design/teacher-call.html. Код: Фёдор.
// Состояния: до входа → подключаемся → в уроке (связь может пропасть) → урок завершён.
// Плитки строятся по участникам LiveKit, панель класса — по событиям урока из WebSocket.

import { DisconnectReason, joinCall } from "./call.js";
import { LESSON_ID, loadRoster, subscribe } from "./lesson.js";
import { inMax, micAccess, openInBrowser, openMaxSettings, openSystemSettings } from "./mic.js";

const { RoomEvent, Track } = window.LivekitClient;

const FLAG_TEXT = { long_silence: "давно не отвечал", quiet_but_precise: "редко, но развёрнуто" };
const FLAG_RANK = { long_silence: 0, quiet_but_precise: 1 };
const COMPACT_FROM = 9; // с девяти учеников строки панели сжаты, над списком фильтры
const TOAST_MS = 4000;

// Что сказать, если микрофона нет: зависит от того, что ответил MAX или браузер (js/mic.js)
const NOMIC = {
  "max-settings": ["Разрешите доступ к микрофону в настройках MAX и попробуйте снова", "Открыть настройки MAX", openMaxSettings],
  "system-settings": ["У MAX нет доступа к микрофону. Включите его в настройках телефона и попробуйте снова", "Открыть настройки телефона", openSystemSettings],
  update: ["Обновите MAX до последней версии или откройте урок в браузере", "Открыть в браузере", openInBrowser],
  browser: ["В этой версии MAX микрофон для уроков пока не работает. Откройте урок в браузере", "Открыть в браузере", openInBrowser],
  denied: ["Разрешите доступ к микрофону в настройках браузера и попробуйте снова", null, null],
};

const $ = (id) => document.getElementById(id);
const mobile = matchMedia("(max-width: 700px)");

let roster = await loadRoster();
// TODO(Фёдор, #24): учитель — из данных запуска MAX, а не из состава урока
const teacherId = roster.teacher.id;

const prefs = { mic: true, cam: false };
const students = {}; // id → { level, flags, off, last }
let room = null;
let leaving = false;
let startedAt = 0;
let page = 1;
let panelOpen = !mobile.matches;
let filter = "all";
let menuFor = null;
let reconnecting = false;
const videos = new Map(); // sid дорожки → <video>, чтобы не пересоздавать видео при каждой перерисовке

document.title = `Урок: ${roster.class_name}, ${roster.subject.toLowerCase()}`;
$("previewAvatar").textContent = initials(roster.teacher.name);
resetStudents();

// ---------- до входа ----------

function lobby(kind = "ready", detail = "") {
  show("lobby");
  const actions = $("lobbyActions");
  actions.replaceChildren();
  if (kind === "connecting") {
    setLobbyText("Подключаемся к уроку", "Обычно это занимает 3–5 секунд");
    actions.append(button("primary", "Подключаемся…", null, true, true));
  } else if (kind in NOMIC) {
    const [text, action, run] = NOMIC[kind];
    setLobbyText("Нет доступа к микрофону", `${text}. Без микрофона ученики вас не услышат`);
    if (action) actions.append(button("primary", action, run));
    actions.append(button(action ? "secondary" : "primary", "Попробовать снова", () => enter()));
    actions.append(button("link", "Войти без микрофона", () => enter({ mic: false })));
  } else {
    setLobbyText("Готовы войти?", detail || "Проверьте микрофон и камеру");
    $("lobbyText").classList.toggle("lobby-error", Boolean(detail));
    actions.append(button("primary", "Войти в урок", () => enter()));
  }
  setIconButton($("pvMic"), kind in NOMIC ? false : prefs.mic, "mic", "микрофон");
  setIconButton($("pvCam"), prefs.cam, "cam", "камеру");
}

function setLobbyText(title, text) {
  $("lobbyTitle").textContent = title;
  $("lobbyText").textContent = text;
  $("lobbyText").hidden = !text;
  $("lobbyText").classList.remove("lobby-error");
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
    const access = await micAccess();
    if (access !== "ok") return lobby(access);
  }
  lobby("connecting");
  try {
    room = await joinCall(teacherId, { mic, cam: prefs.cam, onState });
  } catch (err) {
    room = null;
    if (err.name === "NotAllowedError") return lobby(inMax() ? "browser" : "denied");
    return lobby("ready", `Не удалось войти: ${err.message}. Попробуйте ещё раз`);
  }
  leaving = false;
  startedAt = Date.now();
  // Учитель в звонке: ассистент начинает слушать, ученики получают «Урок начался».
  // Демо-урок сервер начинает сразу с записанным классом
  await fetch(`/api/lessons/${LESSON_ID}/start`, { method: "POST" })
    .catch((err) => console.warn("урок не начался на сервере", err));
  // Состав мог поменяться: виртуальный класс заменяет учеников записанными
  roster = await loadRoster();
  resetStudents();
  watchRoom();
  show("call");
  if (!mic) toast("Микрофон выключен. Ученики вас не слышат");
  render();
}

// ---------- в уроке ----------

function watchRoom() {
  const redraw = () => requestAnimationFrame(renderStage);
  for (const event of [
    RoomEvent.ParticipantConnected, RoomEvent.ParticipantDisconnected,
    RoomEvent.TrackSubscribed, RoomEvent.TrackUnsubscribed,
    RoomEvent.TrackMuted, RoomEvent.TrackUnmuted,
    RoomEvent.LocalTrackPublished, RoomEvent.LocalTrackUnpublished,
    RoomEvent.ActiveSpeakersChanged,
  ]) room.on(event, redraw);
}

function onState(state, reason) {
  reconnecting = state === "reconnecting" || state === "signalReconnecting";
  if (state === "disconnected" && !leaving && room) {
    room = null;
    return lobby("ready", reason === DisconnectReason.DUPLICATE_IDENTITY
      ? "Вы вошли в урок в другом окне, здесь звонок закрыт"
      : "Связь с уроком потеряна. Войдите ещё раз");
  }
  render();
}

function people() {
  // Скрытый ассистент в комнате не виден: LiveKit не присылает его участникам
  return room ? [room.localParticipant, ...room.remoteParticipants.values()] : [];
}

function personName(p) {
  if (p === room.localParticipant) return "Вы";
  const s = roster.students.find((x) => x.id === p.identity);
  return s?.name ?? (p.name || p.identity);
}

function videoFor(pub) {
  const track = pub?.track;
  if (!track || pub.isMuted) return null;
  if (!videos.has(track.sid)) videos.set(track.sid, track.attach());
  return videos.get(track.sid);
}

function tile(p, i) {
  const self = p === room.localParticipant;
  const el = div(`tile${p.isSpeaking ? " speaking" : ""}${self ? " self" : ""}`);
  el.style.setProperty("--h", (i * 47 + 200) % 360);
  const video = videoFor(p.getTrackPublication(Track.Source.Camera));
  if (video) {
    el.classList.add("cam");
    el.append(video);
  } else {
    el.append(span("avatar", initials(self ? roster.teacher.name : personName(p))));
  }
  const mic = p.getTrackPublication(Track.Source.Microphone);
  if (!mic || mic.isMuted) el.append(iconSpan("micoff", "mic-off"));
  // Подписи на плитке нет: имя только во всплывающей подсказке и для экранного чтения
  el.title = personName(p);
  el.setAttribute("aria-label", personName(p));
  return el;
}

function renderStage() {
  if (!room) return;
  const all = people();
  const live = new Set();
  for (const p of all) for (const pub of p.trackPublications.values()) if (pub.track) live.add(pub.track.sid);
  for (const sid of videos.keys()) if (!live.has(sid)) videos.delete(sid);

  const sharer = all.find((p) => p.getTrackPublication(Track.Source.ScreenShare)?.track);
  $("share").hidden = !sharer;
  $("tiles").hidden = Boolean(sharer);
  $("stage").classList.toggle("dim", reconnecting);
  $("banner").hidden = !reconnecting;

  if (sharer) {
    const video = videoFor(sharer.getTrackPublication(Track.Source.ScreenShare));
    $("shared").replaceChildren(...(video ? [video] : []), $("sharedBy"));
    $("sharedBy").textContent = sharer === room.localParticipant ? "Вы показываете экран" : `${personName(sharer)} показывает экран`;
    const strip = mobile.matches || all.length <= 4
      ? all.map(tile)
      : [...all.slice(0, 3).map(tile), moreTile(all.length - 3)];
    $("strip").replaceChildren(...strip);
    $("pager").hidden = true;
  } else {
    const size = mobile.matches ? 6 : 16;
    const pages = Math.max(1, Math.ceil(all.length / size));
    page = Math.min(page, pages);
    const visible = all.slice((page - 1) * size, page * size);
    const c = visible.length;
    const cols = mobile.matches ? (c === 1 ? 1 : 2) : c === 1 ? 1 : c <= 4 ? 2 : c <= 9 ? 3 : 4;
    const few = !mobile.matches && c <= 2;
    const tiles = $("tiles");
    tiles.className = `tiles${few ? " few" : ""}${few && c === 1 ? " one" : ""}`;
    tiles.style.gridTemplateColumns = `repeat(${cols}, minmax(0, ${few ? (c === 1 ? "960px" : "420px") : "1fr"}))`;
    tiles.style.gridTemplateRows = `repeat(${Math.ceil(c / cols)}, minmax(0, 1fr))`;
    tiles.replaceChildren(...visible.map((p, i) => tile(p, (page - 1) * size + i)));
    $("pager").hidden = pages === 1;
    $("pageNo").textContent = `${page} / ${pages}`;
    $("pageInfo").textContent = `· показаны ${c} из ${all.length}`;
    $("prev").disabled = page === 1;
    $("next").disabled = page === pages;
  }
  renderControls();
}

function moreTile(n) {
  const el = div("tile more-tile");
  const inner = div("");
  inner.append(`+${n}`, Object.assign(document.createElement("small"), { textContent: "ещё в звонке" }));
  el.append(inner);
  return el;
}

$("prev").onclick = () => { page--; renderStage(); };
$("next").onclick = () => { page++; renderStage(); };

// ---------- панель класса ----------

function resetStudents() {
  for (const s of roster.students) students[s.id] ??= { level: null, flags: [], off: false, last: "" };
}

function renderPanel() {
  const list = roster.students;
  const started = list.some((s) => students[s.id].level !== null);
  const compact = list.length >= COMPACT_FROM;
  $("panel").hidden = !panelOpen || list.length === 0;
  // Свёрнутую панель выдвигают стрелкой на её месте: справа, на телефоне снизу
  $("expand").hidden = panelOpen || list.length === 0;
  $("expand").querySelector("use").setAttribute("href", mobile.matches ? "#chev-u" : "#chev-l");
  $("count").textContent = `Учеников: ${list.length}`;
  $("collapse").querySelector("use").setAttribute("href", mobile.matches ? "#chev-d" : "#chev-r");

  const hint = reconnecting
    ? "Шкалы участия обновятся, когда вернётся связь"
    : !started ? "Шкалы участия появятся, когда ученики заговорят" : "";
  $("hint").hidden = !hint;
  $("hint").textContent = hint;

  const count = (flag) => list.filter((s) => students[s.id].flags.includes(flag)).length;
  $("chips").hidden = !compact || !started;
  $("chips").replaceChildren(
    chip("all", `Все ${list.length}`),
    chip("long_silence", `Давно не отвечали ${count("long_silence")}`),
    chip("quiet_but_precise", `Редко, но развёрнуто ${count("quiet_but_precise")}`),
  );

  const rank = (s) => Math.min(2, ...students[s.id].flags.map((f) => FLAG_RANK[f] ?? 2));
  const shown = (started ? [...list].sort((a, b) => rank(a) - rank(b)) : list)
    .filter((s) => filter === "all" || !started || students[s.id].flags.includes(filter));
  $("plist").replaceChildren(...shown.map((s) => row(s, compact, started)));
}

function row(s, compact, started) {
  const st = students[s.id];
  const el = div(`stu${compact ? " compact" : ""}${st.off ? " off" : ""}`);
  const line = div("stu-line");
  line.append(span("stu-name", s.name));
  const flags = started && !st.off ? st.flags.map((f) => span(`flag ${f}`, FLAG_TEXT[f])) : [];
  const bars = div("bars");
  for (let n = 1; n <= 4; n++) bars.append(span(`bar${st.level && n <= st.level ? " on" : ""}`, ""));
  const dots = document.createElement("button");
  dots.className = `iconbtn${menuFor === s.id ? " open" : ""}`;
  dots.setAttribute("aria-label", `Меню ученика ${s.name}`);
  dots.append(svg("more"));
  dots.onclick = (e) => {
    e.stopPropagation();
    menuFor = menuFor === s.id ? null : s.id;
    renderPanel();
  };

  if (!started) {
    line.append(dots);
    el.append(line);
  } else if (compact) {
    line.append(...flags, bars, dots);
    el.append(line);
  } else {
    // Ступень видна по шкале, словами её не подписываем; выключенную аналитику — подписываем
    if (st.off) line.append(span("stu-label", "аналитика выключена"));
    line.append(...flags); // метки справа от имени, а не под шкалой
    line.append(dots);
    el.append(line, bars);
    if (st.last && !st.off) el.append(Object.assign(document.createElement("p"), { className: "stu-last", textContent: `«${st.last}»` }));
  }
  if (menuFor === s.id) el.append(menu(s));
  return el;
}

function menu(s) {
  const st = students[s.id];
  const el = div("menu");
  el.setAttribute("role", "menu");
  el.onclick = (e) => e.stopPropagation();
  el.append(div("menu-title", s.name));
  const item = div("menu-item");
  const label = span("", "Аналитика для ученика");
  label.append(Object.assign(document.createElement("small"), { textContent: "Когда выключена, ученик не попадает в шкалу" }));
  const sw = document.createElement("button");
  sw.className = `switch${st.off ? " off" : ""}`;
  sw.setAttribute("role", "switch");
  sw.setAttribute("aria-checked", String(!st.off));
  sw.setAttribute("aria-label", "Аналитика для ученика");
  sw.append(span("track", ""));
  sw.onclick = () => setAnalytics(s.id, st.off);
  item.append(label, sw);
  el.append(item);
  return el;
}

async function setAnalytics(studentId, enabled) {
  students[studentId].off = !enabled;
  renderPanel();
  // TODO(Ярослав): сервер пока только отвечает тем же и не рассылает состояние по WebSocket
  await fetch(`/api/lessons/${LESSON_ID}/analytics`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ type: "analytics", enabled, student_id: studentId }),
  }).catch((err) => console.warn("выключатель аналитики не дошёл до сервера", err));
}

function chip(value, text) {
  const el = document.createElement("button");
  el.className = `chip${filter === value ? " on" : ""}`;
  el.textContent = text;
  el.onclick = () => { filter = value; renderPanel(); };
  return el;
}

document.addEventListener("click", () => {
  if (menuFor) { menuFor = null; renderPanel(); }
});

const togglePanel = () => { panelOpen = !panelOpen; render(); };
$("collapse").onclick = togglePanel;
$("expand").onclick = togglePanel;

subscribe((event) => {
  if (event.type === "analytics") {
    for (const s of roster.students) if (event.student_id == null || event.student_id === s.id) students[s.id].off = !event.enabled;
  }
  const st = students[event.student_id];
  if (st && event.type === "scale") {
    st.level = Math.max(st.level ?? 1, event.level); // шкала только растёт
    st.flags = event.flags ?? [];
  }
  if (st && event.type === "utterance") st.last = event.text;
  if (room) renderPanel();
});

// ---------- кнопки звонка ----------

function renderControls() {
  const lp = room.localParticipant;
  setIconButton($("btnMic"), lp.isMicrophoneEnabled, "mic", "микрофон");
  setIconButton($("btnCam"), lp.isCameraEnabled, "cam", "камеру");
  $("btnScreen").hidden = !navigator.mediaDevices?.getDisplayMedia;
  $("btnScreen").classList.toggle("active", lp.isScreenShareEnabled);
  $("btnScreen").setAttribute("aria-label", lp.isScreenShareEnabled ? "Остановить показ экрана" : "Показать экран");
}

$("btnMic").onclick = async () => {
  const on = !room.localParticipant.isMicrophoneEnabled;
  if (on && (await micAccess()) !== "ok") return toast("Нет доступа к микрофону");
  try {
    await room.localParticipant.setMicrophoneEnabled(on);
  } catch {
    return toast("Нет доступа к микрофону");
  }
  if (!on) toast("Микрофон выключен. Ученики вас не слышат");
  renderStage();
};

$("btnCam").onclick = async () => {
  try {
    await room.localParticipant.setCameraEnabled(!room.localParticipant.isCameraEnabled);
  } catch {
    toast("Камера недоступна");
  }
  renderStage();
};

$("btnScreen").onclick = async () => {
  try {
    await room.localParticipant.setScreenShareEnabled(!room.localParticipant.isScreenShareEnabled);
  } catch {
    // окно выбора экрана закрыли — это не ошибка
  }
  renderStage();
};

// «Завершить урок» нажимают дважды: случайное касание не должно закончить урок всему классу.
// confirm() не годится: во встроенном браузере MAX он может не показаться
let armed = null;
$("btnLeave").onclick = async () => {
  if (!armed) {
    $("btnLeave").classList.add("armed");
    toast("Нажмите ещё раз, чтобы завершить урок");
    armed = setTimeout(() => { armed = null; $("btnLeave").classList.remove("armed"); }, TOAST_MS);
    return;
  }
  clearTimeout(armed);
  armed = null;
  $("btnLeave").classList.remove("armed");
  leaving = true;
  const minutes = Math.max(1, Math.round((Date.now() - startedAt) / 60000));
  await room.disconnect();
  room = null;
  show("ended");
  $("duration").textContent = `${minutes} ${plural(minutes, "минута", "минуты", "минут")}`;
  $("studentsCount").textContent = `Учеников: ${roster.students.length}`;
  $("backToChat").hidden = !inMax();
  // ассистент дораспознаёт очередь и выходит, сводку отправит бот (#21)
  fetch(`/api/lessons/${LESSON_ID}/end`, { method: "POST" }).catch((err) => console.warn("урок не завершился на сервере", err));
};

$("backToChat").onclick = () => window.WebApp?.close?.();

// ---------- общее ----------

function render() {
  if (!room) return;
  renderPanel();
  renderStage();
}

mobile.addEventListener("change", () => {
  panelOpen = !mobile.matches;
  render();
});

function show(screen) {
  $("lobby").hidden = screen !== "lobby";
  $("call").hidden = screen !== "call";
  $("controls").hidden = screen !== "call";
  $("ended").hidden = screen !== "ended";
  if (screen !== "call") $("toast").hidden = true;
}

let toastTimer;
function toast(text) {
  const el = $("toast");
  const icon = svg("mic-off");
  icon.style.display = text.startsWith("Микрофон выключен") ? "" : "none";
  el.replaceChildren(icon, text);
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), TOAST_MS);
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

function plural(n, one, few, many) {
  const m10 = n % 10, m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
  return many;
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

lobby();
