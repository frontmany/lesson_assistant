// Доступ к микрофону в MAX, без него урок открывается в браузере

import { browserLink } from "./lesson.js";

const REASON = "Ассистенту урока нужно слышать ваш голос";

const MAX_SETTINGS = ["app_permission_denied", "mic_app_permission_denied"];
const SYSTEM_SETTINGS = ["system_permission_denied", "mic_system_permission_denied"];
const REINIT = ["not_inited", "not_found"];
const SUPPORTED = ["android", "web"];
// Мост не ответил за это время, считаем, что микрофона нет
const ASK_TIMEOUT_MS = 6000;

export function inMax() {
  return Boolean(window.WebApp?.initData);
}

export async function micAccess() {
  if (!inMax()) return "ok";
  const supported = SUPPORTED.includes(window.WebApp.platform);
  const timeout = new Promise((resolve) => setTimeout(() => resolve("browser"), ASK_TIMEOUT_MS));
  const access = supported ? await Promise.race([ask(window.WebApp.PermissionsManager), timeout]) : "browser";
  // Ссылку готовим заранее, MAX откроет её только сразу по нажатию
  if (access !== "ok") link = await browserLink().catch(() => location.href);
  return access;
}

async function ask(pm) {
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

let link = null;

function browserUrl() {
  return link ?? location.href;
}

export function openInBrowser() {
  if (window.WebApp.platform === "web") window.open(browserUrl(), "_blank", "noopener");
  else window.WebApp.openLink(browserUrl());
}

async function request(pm) {
  try {
    return await pm.requestAccess(["mic"], REASON);
  } catch (err) {
    if (!REINIT.includes(code(err))) throw err;
    await pm.init();
    return await pm.requestAccess(["mic"], REASON);
  }
}

function verdict(info) {
  const mic = info?.mic;
  if (!mic) return "ok";
  if (mic.available === false) return "browser";
  if (mic.appAccessGranted === false) return "max-settings";
  if (mic.systemAccessGranted === false) return "system-settings";
  return "ok";
}

function fromError(err) {
  // Старый MAX на Android отказывает уже в init
  if (err?.error?.code === "client.permissions_get_info.refused" && window.WebApp.platform === "android") return "update";
  const c = code(err);
  if (MAX_SETTINGS.includes(c)) return "max-settings";
  if (SYSTEM_SETTINGS.includes(c)) return "system-settings";
  return "browser";
}

function code(err) {
  return String(err?.error?.code ?? "").split(".").pop();
}

async function flag(obj, name) {
  const v = obj[name];
  return typeof v === "function" ? await v.call(obj) : v;
}
