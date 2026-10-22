// Кнопки «Войти в урок» и микрофон — общие для учителя и ученика.

import { joinCall, toggleMic } from "./call.js";

const STATE_TEXT = {
  connecting: "Подключаемся…",
  connected: "В уроке",
  reconnecting: "Связь пропала, переподключаемся…",
  disconnected: "Вне урока",
};

export function setupCall(participantId, { onSpeakers } = {}) {
  const join = document.getElementById("join");
  const mic = document.getElementById("mic");
  const state = document.getElementById("state");
  let room;

  join.addEventListener("click", async () => {
    join.disabled = true;
    try {
      room = await joinCall(participantId, {
        onSpeakers,
        onState: (s) => (state.textContent = STATE_TEXT[s] ?? s),
      });
      join.hidden = true;
      mic.hidden = false;
    } catch (err) {
      // TODO(Фёдор): отдельный текст, если браузер не дал доступ к микрофону
      state.textContent = err.message;
      join.disabled = false;
    }
  });

  mic.addEventListener("click", async () => {
    const enabled = await toggleMic(room);
    mic.textContent = enabled ? "Выключить микрофон" : "Включить микрофон";
  });
}
