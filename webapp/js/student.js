import { setupCall } from "./controls.js";
import { LEVEL_LABELS, subscribe } from "./lesson.js";

// TODO(Фёдор): в MAX брать ученика из window.WebApp.initData, а не из адреса
const studentId = new URLSearchParams(location.search).get("id") ?? "artem_k";

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
