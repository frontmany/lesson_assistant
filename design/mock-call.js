// Подставные участники для настоящей сцены приложения

window.LivekitClient = { Track: { Source: { Camera: "camera", Microphone: "microphone", ScreenShare: "screen" } } };

export const $ = (id) => document.getElementById(id);
export const params = new URLSearchParams(location.search);

export const NAMES = [
  "Иван И.", "Мария С.", "Артём К.", "Софья Л.", "Алиса Н.", "Дмитрий П.", "Ева Р.", "Глеб Т.", "Дарья Ф.",
  "Егор В.", "Злата М.", "Кирилл Б.", "Лев О.", "Мила Ж.", "Никита З.", "Олеся Ч.", "Павел Ш.", "Рита Ю.",
  "Савва Я.", "Тимур Ц.", "Ульяна Х.", "Фёдор Э.", "Яна Г.", "Вера Д.", "Остап Е.", "Полина А.", "Роман У.",
  "Стефания К.", "Тихон Л.", "Арина М.",
];

let sid = 0;

function fakeVideo(hue) {
  const el = document.createElement("div");
  el.className = "fakecam";
  el.style.setProperty("--h", hue);
  return el;
}

function fakeScreen() {
  const el = document.createElement("div");
  el.className = "fakescreen";
  el.innerHTML = "<h3>Реформы Петра I</h3><p>Армия · флот · налоги · Петербург</p>";
  return el;
}

export function person(identity, name, { cam = false, mic = true, speaking = false, screen = false, hue = 200 } = {}) {
  const pubs = new Map();
  const add = (source, extra) => pubs.set(source, { source, isMuted: false, ...extra });
  if (cam) add("camera", { track: { sid: `t${++sid}`, attach: () => fakeVideo(hue) } });
  add("microphone", { isMuted: !mic, track: mic ? { sid: `t${++sid}` } : null });
  if (screen) add("screen", { track: { sid: `t${++sid}`, attach: fakeScreen } });
  return {
    identity, name, isSpeaking: speaking,
    trackPublications: pubs,
    getTrackPublication: (source) => pubs.get(source) ?? null,
  };
}

export function div(cls, text) {
  const el = document.createElement("div");
  if (cls) el.className = cls;
  if (text != null) el.textContent = text;
  return el;
}

export function span(cls, text) {
  const el = document.createElement("span");
  if (cls) el.className = cls;
  if (text != null) el.textContent = text;
  return el;
}

export function svg(icon) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  el.setAttribute("class", "i");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#${icon}`);
  el.append(use);
  return el;
}

export function iconSpan(cls, icon) {
  const el = span(cls);
  el.append(svg(icon));
  return el;
}

export function button(cls, text, disabled = false, spin = false) {
  const el = document.createElement("button");
  el.className = cls;
  if (spin) el.append(span("spin", ""));
  el.append(text);
  el.disabled = disabled;
  return el;
}

export function initials(name) {
  return name.split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();
}

export function setIconButton(btn, on, icon) {
  btn.classList.toggle("off", !on);
  btn.querySelector("use").setAttribute("href", `#${icon}${on ? "" : "-off"}`);
}

export function lobby(kind, { title, avatar }) {
  $("lobby").hidden = false;
  $("previewAvatar").textContent = avatar;
  const actions = $("lobbyActions");
  const setText = (t, text = "") => {
    $("lobbyTitle").textContent = t;
    $("lobbyText").textContent = text;
    $("lobbyText").hidden = !text;
  };
  if (kind === "connecting") {
    setText("Подключаемся к уроку");
    actions.append(button("primary", "Подключаемся…", true, true));
  } else if (kind === "nomic") {
    setText("Нет доступа к микрофону");
    actions.append(button("primary", "Открыть настройки MAX"), button("secondary", "Попробовать снова"), button("link", "Войти без микрофона"));
    setIconButton($("pvMic"), false, "mic");
  } else if (kind === "over" || kind === "ended") {
    setText(kind === "ended" ? "Урок завершён" : "Урок уже закончился");
    actions.append(button("primary", "К классам"));
    $("preview").hidden = true;
  } else if (kind === "fail") {
    setText("Не удалось открыть урок", "Ссылка устарела. Откройте урок заново из чата с ботом");
    actions.append(button("primary", "Открыть из чата с ботом"));
    $("preview").hidden = true;
  } else {
    setText(title);
    actions.append(button("primary", "Войти в урок"));
  }
}
