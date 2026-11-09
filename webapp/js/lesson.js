// Общий код экранов, вход, состав класса и события урока

// без ready клиент MAX может показать техническую заминку
window.WebApp?.ready?.();

const params = new URLSearchParams(location.search);

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

export const signedIn = () => Boolean(session);

function stored() {
  try {
    return sessionStorage.getItem("session");
  } catch {
    return null;
  }
}

function keep(token) {
  session = token;
  try {
    sessionStorage.setItem("session", token);
  } catch {
  }
}

async function post(path, body) {
  const res = await fetch(path, { method: "POST", headers: json, body: JSON.stringify(body) });
  const data = await res.json();
  if (!res.ok) throw Object.assign(new Error(data.detail), { status: res.status });
  return data;
}

function once(key, value) {
  try {
    if (sessionStorage.getItem(key) === value) return true;
    sessionStorage.setItem(key, value);
  } catch {
  }
  return false;
}

export async function signIn() {
  const ticket = params.get("ticket");
  if (ticket) {
    keep((await post("/api/auth/browser", { ticket })).token);
    // одноразовая ссылка не должна остаться в истории
    params.delete("ticket");
    history.replaceState(null, "", `${location.pathname}?${params}`);
    return null;
  }
  const initData = window.WebApp?.initData;
  if (initData) {
    const { token, start_param: startParam } = await post("/api/auth/max", { init_data: initData });
    keep(token);
    // MAX отдаёт тот же start_param при каждом переходе, кнопку бота выполняем один раз
    if (once("launch", initData)) return null;
    return startParam;
  }
  session = stored();
  return null;
}

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

export function failText(err) {
  if (err?.status === 401) return "Ссылка устарела. Откройте урок заново из чата с ботом";
  if (err instanceof TypeError) return "Нет связи с сервером. Проверьте интернет и попробуйте снова";
  return err?.message || "Попробуйте снова";
}

export async function botLink() {
  const health = await fetch("/api/health").then((r) => r.json()).catch(() => ({}));
  return health.bot ?? null;
}

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
  // 4403 значит не участник, ждать нечего
  ws.onclose = (e) => {
    if (e.code !== 4403) setTimeout(() => subscribe(onEvent), 2000);
  };
  return ws;
}

export async function browserLink() {
  const { ticket } = await api("/api/auth/ticket", { method: "POST" });
  const url = new URL(location.href);
  url.hash = "";
  url.searchParams.set("ticket", ticket);
  return url.toString();
}
