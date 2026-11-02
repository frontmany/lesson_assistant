// Общий код экранов: кто вошёл, состав класса и поток событий урока.
//
// Вход (#24): в MAX мини-приложение отдаёт серверу данные запуска (initData),
// сервер проверяет подпись и выдаёт сессию; дальше запросы идут с ней.
// В браузере, куда урок открывают без микрофона в MAX, вместо данных запуска —
// одноразовая ссылка (?ticket=). Для разработки (DEV_AUTH=1 на сервере)
// участника можно назвать в адресе: student.html?id=ivan_i.

// Сказать MAX, что мини-приложение загрузилось: без этого клиент может показать «Техническую заминку»
window.WebApp?.ready?.();

const params = new URLSearchParams(location.search);

// Урок из адреса: его открывает стартовая страница мини-приложения.
// Без параметра — урок демо-класса из data/demo_lesson, для разработки
export const LESSON_ID = params.get("lesson") ?? "10a-history-05";
const DEV_ID = params.get("id");

export const LEVEL_LABELS = {
  1: "Пока тихо",
  2: "Есть ответ",
  3: "Участвуешь",
  4: "Активно на уроке",
};

const json = { "Content-Type": "application/json" };
let session = null;

function stored() {
  try {
    return sessionStorage.getItem("session");
  } catch {
    return null; // хранилище бывает недоступно: тогда сессия только в памяти страницы
  }
}

function keep(token) {
  session = token;
  try {
    sessionStorage.setItem("session", token);
  } catch {
    // см. stored()
  }
}

async function post(path, body) {
  const res = await fetch(path, { method: "POST", headers: json, body: JSON.stringify(body) });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail);
  return data;
}

// Узнать, кто открыл приложение. Возвращает start_param из MAX, если он есть:
// c<id> — учитель начинает урок класса, l<id> — открыть урок
export async function signIn() {
  const ticket = params.get("ticket");
  if (ticket) {
    keep((await post("/api/auth/browser", { ticket })).token);
    // одноразовая ссылка не должна остаться в истории и закладках
    params.delete("ticket");
    history.replaceState(null, "", `${location.pathname}?${params}`);
    return null;
  }
  const initData = window.WebApp?.initData;
  if (initData) {
    const { token, start_param: startParam } = await post("/api/auth/max", { init_data: initData });
    keep(token);
    return startParam;
  }
  session = stored();
  return null;
}

// Запрос к API урока от имени вошедшего. Ошибку сервера отдаёт текстом
export async function api(path, { method = "GET", body } = {}) {
  const url = new URL(path, location.origin);
  if (DEV_ID) url.searchParams.set("id", DEV_ID);
  const headers = { ...(body ? json : {}), ...(session ? { Authorization: `Bearer ${session}` } : {}) };
  const res = await fetch(url, { method, headers, body: body ? JSON.stringify(body) : undefined });
  if (!res.ok) {
    const detail = await res.json().then((b) => b.detail, () => res.statusText);
    throw Object.assign(new Error(detail), { status: res.status });
  }
  return res.json();
}

// Кто я в этом уроке: { participant_id, name, role }
export function whoAmI() {
  return api(`/api/lessons/${LESSON_ID}/me`);
}

export function loadRoster() {
  return api(`/api/lessons/${LESSON_ID}/roster`);
}

export function subscribe(onEvent) {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const url = new URL(`${proto}://${location.host}/ws/lessons/${LESSON_ID}`);
  if (session) url.searchParams.set("token", session);
  if (DEV_ID) url.searchParams.set("id", DEV_ID);
  const ws = new WebSocket(url);
  ws.onmessage = (msg) => onEvent(JSON.parse(msg.data));
  return ws;
}

// Одноразовая ссылка на этот же экран для браузера: её открывает «Открыть в браузере»
export async function browserLink() {
  const { ticket } = await api("/api/auth/ticket", { method: "POST" });
  const url = new URL(location.href);
  url.hash = "";
  url.searchParams.set("ticket", ticket);
  return url.toString();
}
