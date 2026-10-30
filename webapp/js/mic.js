// Доступ к микрофону в MAX. Код: Фёдор.
// По документации MAX Bridge (dev.max.ru/docs/webapps/bridge) доступ двухуровневый:
// мини-приложению внутри MAX и самому MAX в системе телефона, нужны оба.
// PermissionsManager пока есть только на Android и в веб-клиенте MAX, поэтому
// на iOS и десктопе, а также при любом непонятном отказе урок открывается в браузере.

const REASON = "Ассистенту урока нужно слышать ваш голос";

const MAX_SETTINGS = ["app_permission_denied", "mic_app_permission_denied"];
const SYSTEM_SETTINGS = ["system_permission_denied", "mic_system_permission_denied"];
const REINIT = ["not_inited", "not_found"];

// Внутри MAX у страницы есть данные запуска; в обычном браузере их нет
export function inMax() {
  return Boolean(window.WebApp?.initData);
}

// Что нужно, чтобы был микрофон:
// "ok" — можно звать getUserMedia; "max-settings" — разрешить мини-приложению в MAX;
// "system-settings" — разрешить MAX в настройках телефона; "browser" — открыть урок в браузере
export async function micAccess() {
  if (!inMax()) return "ok";
  const pm = window.WebApp.PermissionsManager;
  if (!pm) return "browser";
  try {
    if (!(await flag(pm, "isInited"))) await pm.init();
    return verdict(await request(pm));
  } catch (err) {
    return fromError(err);
  }
}

export function openMaxSettings() {
  return window.WebApp.PermissionsManager.openMaxSettings();
}

export function openSystemSettings() {
  return window.WebApp.PermissionsManager.openSystemSettings();
}

// Тот же урок во внешнем браузере. Только по нажатию: без клика MAX ссылку не откроет.
// TODO(Фёдор, #24): в браузере нет initData — передать в ссылке одноразовый токен входа
export function openInBrowser() {
  window.WebApp.openLink(location.href);
}

async function request(pm) {
  try {
    return await pm.requestAccess(["mic"], REASON);
  } catch (err) {
    // Сессия моста сбросилась: init() и ещё одна попытка
    if (!REINIT.includes(code(err))) throw err;
    await pm.init();
    return await pm.requestAccess(["mic"], REASON);
  }
}

// Отказ не всегда приходит ошибкой: requestAccess может вернуть флаги доступа
function verdict(info) {
  const mic = info?.mic;
  if (!mic) return "ok";
  if (mic.available === false) return "browser";
  if (mic.appAccessGranted === false) return "max-settings";
  if (mic.systemAccessGranted === false) return "system-settings";
  return "ok";
}

function fromError(err) {
  const c = code(err);
  if (MAX_SETTINGS.includes(c)) return "max-settings";
  if (SYSTEM_SETTINGS.includes(c)) return "system-settings";
  // mic_not_supported, client.unsupported_method (десктоп) и всё неизвестное
  return "browser";
}

// «client.permissions_request_access.mic_app_permission_denied» → «mic_app_permission_denied»
function code(err) {
  return String(err?.error?.code ?? "").split(".").pop();
}

async function flag(obj, name) {
  const v = obj[name];
  return typeof v === "function" ? await v.call(obj) : v;
}
