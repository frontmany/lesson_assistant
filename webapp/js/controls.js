// Кнопки «Войти в урок» и микрофон — общие для учителя и ученика.

import { joinCall, toggleMic } from "./call.js";
import { inMax, micAccess, openInBrowser, openMaxSettings, openSystemSettings } from "./mic.js";

const STATE_TEXT = {
  connecting: "Подключаемся…",
  connected: "В уроке",
  reconnecting: "Связь пропала, переподключаемся…",
  disconnected: "Вне урока",
};

// Подсказка, когда микрофона нет. Кнопка «Открыть в браузере» есть всегда:
// если настройки не помогли, урок можно провести там
const MIC_HELP = {
  "max-settings": {
    text: "Разреши микрофон для урока в настройках MAX, потом снова нажми «Войти в урок».",
    action: ["Открыть настройки MAX", openMaxSettings],
  },
  "system-settings": {
    text: "У MAX нет доступа к микрофону. Включи его в настройках телефона, потом снова нажми «Войти в урок».",
    action: ["Открыть настройки телефона", openSystemSettings],
  },
  browser: {
    text: "В этой версии MAX микрофон для уроков пока не работает. Открой урок в браузере.",
  },
};

export function setupCall(participantId, { onSpeakers } = {}) {
  const join = document.getElementById("join");
  const mic = document.getElementById("mic");
  const state = document.getElementById("state");
  const help = micHelp(join.closest(".toolbar"));
  let room;

  join.addEventListener("click", async () => {
    join.disabled = true;
    help.hide();
    try {
      const access = await micAccess();
      if (access !== "ok") {
        help.show(access);
        return;
      }
      room = await joinCall(participantId, {
        onSpeakers,
        onState: (s) => (state.textContent = STATE_TEXT[s] ?? s),
      });
      join.hidden = true;
      mic.hidden = false;
    } catch (err) {
      // В MAX без доступа к микрофону выход один — браузер
      if (err.name === "NotAllowedError" && inMax()) {
        state.textContent = "";
        help.show("browser");
        return;
      }
      state.textContent = err.name === "NotAllowedError" ? "Браузер не дал доступ к микрофону" : err.message;
    } finally {
      join.disabled = false;
    }
  });

  mic.addEventListener("click", async () => {
    const enabled = await toggleMic(room);
    mic.textContent = enabled ? "Выключить микрофон" : "Включить микрофон";
  });
}

function micHelp(toolbar) {
  const box = document.createElement("div");
  box.className = "mic-help";
  box.hidden = true;
  const text = document.createElement("p");
  const action = document.createElement("button");
  const browser = document.createElement("button");
  browser.textContent = "Открыть в браузере";
  browser.className = "secondary";
  browser.addEventListener("click", openInBrowser);
  box.append(text, action, browser);
  toolbar.after(box);

  let onAction;
  action.addEventListener("click", () => onAction?.());

  return {
    show(kind) {
      const { text: message, action: act } = MIC_HELP[kind];
      text.textContent = message;
      action.hidden = !act;
      if (act) [action.textContent, onAction] = act;
      box.hidden = false;
    },
    hide() {
      box.hidden = true;
    },
  };
}
