import { setupCall } from "./controls.js";
import { loadRoster, subscribe } from "./lesson.js";

const FLAG_TEXT = {
  long_silence: "давно не отвечал",
  quiet_but_precise: "тихий, но точный",
};

const roster = await loadRoster();
document.getElementById("title").textContent = `${roster.class_name}, ${roster.subject.toLowerCase()}`;

const container = document.getElementById("roster");
const rows = {};

for (const student of roster.students) {
  const row = document.createElement("div");
  row.className = "row";
  row.innerHTML = `
    <span>${student.name}${student.virtual ? " · запись" : ""}</span>
    <span class="bars">${'<span class="bar"></span>'.repeat(4)}</span>
    <span class="flags"></span>
    <span class="last"></span>`;
  container.append(row);
  rows[student.id] = row;
}

// TODO(Фёдор): учитель — из данных запуска MAX, а не жёстко
setupCall(roster.teacher.id, {
  onSpeakers: (ids) => {
    for (const [id, row] of Object.entries(rows)) row.classList.toggle("speaking", ids.includes(id));
  },
});

subscribe((event) => {
  const row = rows[event.student_id];
  if (!row) return;

  if (event.type === "scale") {
    row.querySelectorAll(".bar").forEach((bar, i) => bar.classList.toggle("on", i < event.level));
    row.querySelector(".flags").innerHTML = event.flags
      .map((f) => `<span class="flag ${f}">${FLAG_TEXT[f]}</span>`)
      .join("");
  }
  // TODO(Фёдор): лента реплик по макету Варвары; пока — последняя реплика в строке ученика
  if (event.type === "utterance") {
    row.querySelector(".last").textContent = event.text;
  }
  if (event.type === "analytics") {
    row.classList.toggle("off", !event.enabled);
  }
});
