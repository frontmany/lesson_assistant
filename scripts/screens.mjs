// Экспорт экранов в design/screens/*.png: Edge или Chrome без окна через протокол отладки (CDP).
//
// Два источника, оба — настоящий интерфейс:
//  - макеты звонка design/teacher-call.html и student-call.html: настоящие стили и сетка
//    приложения (webapp/css, webapp/js/stage.js), участники подставные;
//  - стартовая страница webapp/index.html на запущенном приложении: настоящие классы
//    демо-учителя (вход по одноразовой ссылке, её выдаёт контейнер приложения).
//
//   python -m http.server 8090                 # в корне репозитория — для макетов
//   docker compose run -d --rm --name la-dev -e DEV_AUTH=1 -e MAX_BOT_TOKEN= -e SESSION_SECRET=screens -p 8001:8000 app
//   docker exec -i la-dev python - < scripts/screens-seed.py   # демо-учитель с классами
//   node scripts/screens.mjs [фильтр]          # фильтр — часть имени файла
//
// BROWSER — путь к Edge или Chrome, MOCKS — где открыт репозиторий, APP — приложение,
// APP_CONTAINER — контейнер приложения для одноразовых ссылок.
import { execFileSync, spawn } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const BROWSER = process.env.BROWSER ?? "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const MOCKS = process.env.MOCKS ?? "http://127.0.0.1:8090/design";
const APP = process.env.APP ?? "http://127.0.0.1:8001";
const CONTAINER = process.env.APP_CONTAINER ?? "la-dev";
const OUT = new URL("../design/screens/", import.meta.url);
const filter = process.argv[2] ?? "";

// демо-люди на копии приложения (их заводит scripts/screens-seed.py); SESSION_SECRET нужен,
// чтобы одноразовую ссылку из docker exec принял сервер: без токена бота ключ свой у каждого процесса
const TEACHER = [990001, "Марина Сергеевна"];
const STUDENT = [990101, "Пётр Новиков"];
const NEWCOMER = [990201, "Алина Гришина"];

const D = { w: 1280, h: 800, scale: 1, mobile: false }; // компьютер
const M = { w: 375, h: 812, scale: 2, mobile: true };   // телефон @2x

const shots = [];
const add = (file, url, size, opts = {}) => shots.push({ file, url, ...size, ...opts });
const mock = (page) => `${MOCKS}/${page}`;

// ---------- звонок учителя ----------
for (const dark of [false, true]) {
  const s = dark ? "-dark" : "";
  add(`teacher-call-d-n6${s}`, mock("teacher-call.html?n=6"), D, { dark });
  add(`teacher-call-m-n6${s}`, mock("teacher-call.html?n=6"), M, { dark });
}
for (const [name, q] of [["n1", "n=1"], ["n2", "n=2"], ["n30", "n=30"], ["share", "n=6&share=1"], ["start", "n=6&mode=start"]]) {
  add(`teacher-call-d-${name}`, mock(`teacher-call.html?${q}`), D);
  add(`teacher-call-m-${name}`, mock(`teacher-call.html?${q}`), M);
}
add("teacher-call-d-n30-panel-closed", mock("teacher-call.html?n=30&panel=0"), D);
add("teacher-call-d-menu", mock("teacher-call.html?n=6&menu=s1"), D);
add("teacher-call-d-n30-dark", mock("teacher-call.html?n=30"), D, { dark: true });
add("teacher-call-m-menu", mock("teacher-call.html?n=6&menu=s1&panel=1"), M);
add("teacher-call-m-n6-sheet", mock("teacher-call.html?n=6&panel=1"), M);
add("teacher-call-m-n6-sheet-dark", mock("teacher-call.html?n=6&panel=1"), M, { dark: true });
add("teacher-call-m-n30-sheet", mock("teacher-call.html?n=30&panel=1"), M);

// ---------- состояния звонка ----------
for (const st of ["lobby", "connecting", "muted", "reconnecting", "nomic", "ended", "over", "fail"]) {
  add(`call-state-d-${st}`, mock(`teacher-call.html?n=6&state=${st}`), D);
  add(`call-state-m-${st}`, mock(`teacher-call.html?n=6&state=${st}`), M);
}
for (const st of ["lobby", "nomic", "reconnecting", "ended"]) {
  add(`call-state-d-${st}-dark`, mock(`teacher-call.html?n=6&state=${st}`), D, { dark: true });
  add(`call-state-m-${st}-dark`, mock(`teacher-call.html?n=6&state=${st}`), M, { dark: true });
}

// ---------- звонок ученика ----------
for (const l of [1, 2, 3, 4]) add(`student-call-m-l${l}`, mock(`student-call.html?level=${l}`), M);
for (const l of [1, 2, 4]) add(`student-call-m-l${l}-dark`, mock(`student-call.html?level=${l}`), M, { dark: true });
add("student-call-m-l3-dark", mock("student-call.html?level=3"), M, { dark: true });
add("student-call-m-muted", mock("student-call.html?level=2&mic=0"), M);
add("student-call-m-ana-off", mock("student-call.html?ana=off"), M);
add("student-call-m-ana-off-dark", mock("student-call.html?ana=off"), M, { dark: true });
add("student-call-m-alone", mock("student-call.html?teacher=0&n=0&level=1"), M);
add("student-call-m-share", mock("student-call.html?share=1"), M);
add("student-call-d-l3", mock("student-call.html?level=3&n=6"), D);

// ---------- стартовая страница (настоящее приложение) ----------
const home = (file, who, opts = {}) => add(file, null, M, { who, ...opts });
home("start-welcome", NEWCOMER);
home("start-teacher", TEACHER);
home("start-teacher-dark", TEACHER, { dark: true });
home("start-class", TEACHER, { steps: ["document.querySelector('#classes button.row').click()"] });
home("start-class-dark", TEACHER, { dark: true, steps: ["document.querySelector('#classes button.row').click()"] });
home("start-invite", TEACHER, { steps: ["document.querySelector('#classes button.row').click()", "document.getElementById('inviteBtn').click()"] });
home("start-create", TEACHER, { steps: ["document.getElementById('create').click()"] });
home("start-student", STUDENT);

// ---------- CDP ----------
const port = 9333;
const profile = mkdtempSync(join(tmpdir(), "screens-"));
const browser = spawn(BROWSER, ["--headless=new", "--disable-gpu", "--hide-scrollbars", "--mute-audio",
  `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`, "about:blank"], { stdio: "ignore" });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

let info;
for (let i = 0; i < 50 && !info; i++) {
  info = await fetch(`http://127.0.0.1:${port}/json/version`).then((r) => r.json()).catch(() => null);
  if (!info) await sleep(200);
}
const ws = new WebSocket(info.webSocketDebuggerUrl);
await new Promise((r) => ws.addEventListener("open", r, { once: true }));
let seq = 0;
const pending = new Map();
const events = [];
ws.addEventListener("message", (m) => {
  const msg = JSON.parse(m.data);
  if (msg.id && pending.has(msg.id)) {
    const { resolve, reject } = pending.get(msg.id);
    pending.delete(msg.id);
    msg.error ? reject(new Error(`${msg.error.message}`)) : resolve(msg.result);
  } else if (msg.method) events.push(msg);
});
const send = (method, params = {}, sessionId) => new Promise((resolve, reject) => {
  const id = ++seq;
  pending.set(id, { resolve, reject });
  ws.send(JSON.stringify({ id, method, params, sessionId }));
});

function ticket([uid, name]) {
  const code = `from app.auth import User, ticket_for; print(ticket_for(User(${uid}, ${JSON.stringify(name)})))`;
  return execFileSync("docker", ["exec", CONTAINER, "python", "-c", code], { encoding: "utf8" }).trim();
}

for (const s of shots.filter((x) => x.file.includes(filter))) {
  const url = s.url ?? `${APP}/index.html?ticket=${ticket(s.who)}`;
  // каждый снимок — в новой вкладке: вход и запомненное в sessionStorage не переходят дальше
  const { targetId } = await send("Target.createTarget", { url: "about:blank" });
  const { sessionId } = await send("Target.attachToTarget", { targetId, flatten: true });
  await send("Page.enable", {}, sessionId);
  await send("Emulation.setDeviceMetricsOverride", { width: s.w, height: s.h, deviceScaleFactor: s.scale, mobile: s.mobile }, sessionId);
  await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: s.dark ? "dark" : "light" }] }, sessionId);
  await send("Page.navigate", { url }, sessionId);
  await sleep(1800);
  for (const step of s.steps ?? []) {
    await send("Runtime.evaluate", { expression: step, awaitPromise: true }, sessionId);
    await sleep(900);
  }
  const { data } = await send("Page.captureScreenshot", { format: "png" }, sessionId);
  writeFileSync(new URL(`${s.file}.png`, OUT), Buffer.from(data, "base64"));
  await send("Target.closeTarget", { targetId });
  console.log(`design/screens/${s.file}.png`);
}
ws.close();
browser.kill();
