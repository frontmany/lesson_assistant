// Общий код экранов: состав класса и поток событий урока.

export const LESSON_ID = "10a-history-05";

export const LEVEL_LABELS = {
  1: "Пока тихо",
  2: "Есть ответ",
  3: "Участвуешь",
  4: "Активно на уроке",
};

export async function loadRoster() {
  const res = await fetch(`/api/lessons/${LESSON_ID}/roster`);
  return res.json();
}

export function subscribe(onEvent) {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/lessons/${LESSON_ID}`);
  ws.onmessage = (msg) => onEvent(JSON.parse(msg.data));
  return ws;
}
