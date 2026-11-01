import { setupCall } from "./controls.js";
import { LEVEL_LABELS, signIn, subscribe, whoAmI } from "./lesson.js";

// Ученик — тот, кого сервер узнал по данным запуска MAX (#24)
await signIn();
const me = await whoAmI().catch((err) => {
  document.getElementById("state").textContent = err.message;
  document.getElementById("join").hidden = true;
  throw err;
});
if (me.role === "teacher") location.replace(`teacher.html${location.search}`);
const studentId = me.participant_id;

setupCall(studentId);

const scale = document.getElementById("scale");
const steps = Object.entries(LEVEL_LABELS).map(([level, label]) => {
  const step = document.createElement("div");
  step.className = "step";
  step.textContent = label;
  step.dataset.level = level;
  scale.append(step);
  return step;
});

let reached = 1;
steps[0].classList.add("reached");

subscribe((event) => {
  if (event.type !== "scale" || event.student_id !== studentId) return;
  // Шкала только растёт
  reached = Math.max(reached, event.level);
  steps.forEach((step, i) => step.classList.toggle("reached", i < reached));
});
