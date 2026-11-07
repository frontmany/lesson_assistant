// Экран звонка учителя (#14) по макету Варвары design/teacher-call.html. Код: Фёдор.
// Состояния: до входа → подключаемся → в уроке (связь может пропасть) → урок завершён.
// Плитки строятся по участникам LiveKit, панель класса — по событиям урока из WebSocket.

import { DisconnectReason, joinCall } from "./call.js";
import { ask } from "./confirm.js";
import { LESSON_ID, api, botLink, failText, loadRoster, signIn, subscribe, whoAmI } from "./lesson.js";
import { createStage, mobile } from "./stage.js";
import { inMax, micAccess, openInBrowser, openMaxSettings, openSystemSettings } from "./mic.js";

const { RoomEvent, Track } = window.LivekitClient;

const FLAG_TEXT = { long_silence: "давно не отвечал", quiet_but_precise: "редко, но развёрнуто" };
const FLAG_RANK = { long_silence: 0, quiet_but_precise: 1 };
const COMPACT_FROM = 9; // с девяти учеников строки панели сжаты, над списком фильтры
const TOAST_MS = 4000;

// Что предложить, если микрофона нет: одна строка пояснения и кнопка (js/mic.js)
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
// Учитель — тот, кого сервер узнал по данным запуска MAX (#24); ученика отправляем на его экран
const me = await whoAmI().catch((err) => fail(err));
if (me.role !== "teacher") location.replace(`student.html${location.search}`);
const teacherId = me.participant_id;
let roster = await loadRoster().catch((err) => fail(err));

const prefs = { mic: true, cam: false };
const students = {}; // id → { level, flags, off, last }
let room = null;
let leaving = false;
let startedAt = 0;
let panelOpen = !mobile.matches;
let filter = "all";
let menuFor = null;
let reconnecting = false;

document.title = `Урок: ${roster.class_name}, ${roster.subject.toLowerCase()}`;
// выключатели аналитики, если учитель ставил их до перезагрузки страницы (#22)
api(`/api/lessons/${LESSON_ID}/analytics`).then(({ all_off: allOff, off }) => {
  for (const s of roster.students) if (allOff || off.includes(s.id)) students[s.id].off = true;
  if (room) renderPanel();
}, () => {});
$("previewAvatar").textContent = initials(roster.teacher.name);
resetStudents();

// ---------- до входа ----------

function lobby(kind = "ready", detail = "") {
  show("lobby");
  const actions = $("lobbyActions");
  actions.replaceChildren();
  if (kind === "connecting") {
    setLobbyText("Подключаемся к уроку", "");
    actions.append(button("primary", "Подключаемся…", null, true, true));
  } else if (kind === "checking") {
    setLobbyText("Проверяем микрофон", "");
    actions.append(button("primary", "Проверяем…", null, true, true));
  } else if (kind in NOMIC) {
    // без серой подсказки под заголовком: что делать, говорит сама кнопка
    const [, action, run] = NOMIC[kind];
    setLobbyText("Нет доступа к микрофону", "");
    if (action) actions.append(button("primary", action, run));
    // в браузер уводим одной кнопкой: повтор здесь ничего не даст
    if (run !== openInBrowser) actions.append(button(action ? "secondary" : "primary", "Попробовать снова", () => enter()));
    actions.append(button("link", "Войти без микрофона", () => enter({ mic: false })));
  } else if (kind === "over") {
    // открыта кнопка урока, который уже завершён: в звонок не зовём, иначе урок начался бы заново
    setLobbyText("Урок уже закончился", ""); // без серых пояснений под заголовком
    actions.append(button("primary", "К классам", toClasses));
    $("preview").hidden = true;
    return;
  } else {
    setLobbyText("Готовы войти?", detail); // под заголовком только ошибка входа, если была
    $("lobbyText").classList.toggle("lobby-error", Boolean(detail));
    actions.append(button("primary", "Войти в урок", () => enter(me.demo ? { mic: false } : {})));
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
    lobby("checking");
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
  await api(`/api/lessons/${LESSON_ID}/start`, { method: "POST" })
    .catch((err) => console.warn("урок не начался на сервере", err));
  // Состав мог поменяться: виртуальный класс заменяет учеников записанными.
  // Шкалы и метки из мока, пришедшие до начала урока, сбрасываем (#78)
  roster = await loadRoster();
  for (const id of Object.keys(students)) if (students[id].mock) delete students[id];
  resetStudents();
  watchRoom();
  show("call");
  if (!mic && !me.demo) toast("Микрофон выключен. Ученики вас не слышат");
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

function tile(p, i) {
  const self = p === room.localParticipant;
  const el = div(`tile${p.isSpeaking ? " speaking" : ""}${self ? " self" : ""}`);
  el.style.setProperty("--h", (i * 47 + 200) % 360);
  const video = stage.video(p.getTrackPublication(Track.Source.Camera));
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

// Плитки, страницы и показ экрана — общие с учеником (stage.js)
const stage = createStage({ tile, name: personName, redraw: () => renderStage() });

function renderStage() {
  if (!room) return;
  stage.render(people(), { self: room.localParticipant, reconnecting });
  renderControls();
}

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
  // имя не повторяем: меню открыто у карточки ученика. Для скринридера — в aria-label
  el.setAttribute("aria-label", `Меню: ${s.name}`);
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
  await api(`/api/lessons/${LESSON_ID}/analytics`, {
    method: "POST",
    body: { type: "analytics", enabled, student_id: studentId },
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

// Ученик вошёл в класс во время урока: конвейер его уже засчитывает, а в составе экрана его нет.
// Перечитываем состав один раз на пачку событий и применяем событие заново (#78)
let rosterReload = null;
function reloadRoster() {
  rosterReload ??= loadRoster()
    .then((fresh) => { roster = fresh; resetStudents(); render(); })
    .catch((err) => console.warn("состав урока не обновился", err))
    .finally(() => { rosterReload = null; });
  return rosterReload;
}

subscribe(onEvent);

function onEvent(event) {
  if (event.student_id && !students[event.student_id] && !event.mock) {
    reloadRoster().then(() => students[event.student_id] && onEvent(event));
    return;
  }
  if (event.type === "analytics") {
    for (const s of roster.students) if (event.student_id == null || event.student_id === s.id) students[s.id].off = !event.enabled;
  }
  const st = students[event.student_id];
  if (st && event.mock) st.mock = true;
  if (st && event.type === "scale") {
    st.level = Math.max(st.level ?? 1, event.level); // шкала только растёт
    st.flags = event.flags ?? [];
  }
  if (st && event.type === "utterance") st.last = event.text;
  if (room) renderPanel();
}

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

// «Завершить урок» — после вопроса: случайное касание не должно закончить урок всему классу
$("btnLeave").onclick = async () => {
  if (!(await ask({ title: "Завершить урок?", text: "Урок закончится у всего класса, сводку пришлёт бот", ok: "Завершить" }))) return;
  leaving = true;
  const minutes = Math.max(1, Math.round((Date.now() - startedAt) / 60000));
  await room.disconnect();
  room = null;
  show("ended");
  $("duration").textContent = `${minutes} ${plural(minutes, "минута", "минуты", "минут")}`;
  $("studentsCount").textContent = `Учеников: ${roster.students.length}`;
  // ассистент дораспознаёт очередь и выходит, сводку отправит бот (#21)
  api(`/api/lessons/${LESSON_ID}/end`, { method: "POST" }).catch((err) => console.warn("урок не завершился на сервере", err));
};

// После урока — обратно к классам на стартовую страницу. Закрыть мини-приложение
// или открыть чат нельзя: в MAX web и на десктопе мост эти команды не выполняет
$("backToClasses").onclick = () => toClasses();

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

// Не пустили в урок: человек не из этого класса или ссылка устарела
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

// К списку классов; данные запуска MAX (#WebAppData) несём с собой, а home=1 — чтобы
// стартовая страница не начала этот же урок снова по start_param из них
function toClasses() {
  location.replace(`index.html?home=1${location.hash}`);
}

$("lobbyBack").onclick = toClasses;

// Урок только что начали («Начать урок», демо): входим сразу. «Готовы войти?» — для тех, кто присоединяется
const params = new URLSearchParams(location.search);
if (me.ended) {
  lobby("over");
} else if (params.get("now") === "1") {
  params.delete("now");
  history.replaceState(null, "", `${location.pathname}?${params}${location.hash}`);
  // в демо говорить не нужно: без проверки микрофона, которая на iPhone и компьютере уводит в браузер
  enter({ mic: !me.demo });
} else {
  lobby();
}
