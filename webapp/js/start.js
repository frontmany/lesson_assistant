// Стартовая страница мини-приложения (#24): кто открыл и что открыть.

import { api, signIn } from "./lesson.js";

const $ = (id) => document.getElementById(id);

function say(title, text = "") {
  $("title").textContent = title;
  $("text").textContent = text;
}

try {
  // start_param передаёт кнопка бота; ?start= — то же самое для разработки
  const start = (await signIn()) ?? new URLSearchParams(location.search).get("start");
  // данные запуска (#WebAppData=…) несём дальше: экран урока проверит их сам
  const go = (page, lesson) => location.replace(`${page}?lesson=${lesson}${location.hash}`);
  if (start?.startsWith("c")) {
    const { lesson_id: lesson } = await api(`/api/classes/${start.slice(1)}/lessons`, { method: "POST" });
    go("teacher.html", lesson);
  } else if (start?.startsWith("l")) {
    const lesson = start.slice(1);
    const me = await api(`/api/lessons/${lesson}/me`);
    go(me.role === "teacher" ? "teacher.html" : "student.html", lesson);
  } else if (window.WebApp?.initData) {
    say("Урок открывается из чата с ботом", "Нажмите «Начать урок» или «Войти в урок» в сообщении бота");
  } else {
    say("Урок открывается в MAX", "Откройте чат с ботом и нажмите «Войти в урок»");
    $("dev").hidden = false;
  }
} catch (err) {
  say("Не удалось открыть урок", err.message);
}
