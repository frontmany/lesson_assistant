// Подтверждение необратимых действий: удалить класс, убрать ученика, выйти, завершить урок.
// Свой диалог, а не confirm(): во встроенном браузере MAX системный может не показаться.
// Возвращает Promise<boolean>: true — нажали действие, false — «Отмена», фон или Escape.

export function ask({ title, text = "", ok, danger = true }) {
  return new Promise((resolve) => {
    const back = Object.assign(document.createElement("div"), { className: "confirm-back" });
    const box = Object.assign(document.createElement("div"), { className: "confirm" });
    box.setAttribute("role", "alertdialog");
    box.setAttribute("aria-modal", "true");
    const h = Object.assign(document.createElement("h2"), { textContent: title, id: "confirmTitle" });
    box.setAttribute("aria-labelledby", h.id);
    box.append(h);
    if (text) box.append(Object.assign(document.createElement("p"), { textContent: text }));
    const row = Object.assign(document.createElement("div"), { className: "confirm-row" });
    const no = Object.assign(document.createElement("button"), { className: "confirm-no", textContent: "Отмена" });
    const yes = Object.assign(document.createElement("button"), {
      className: danger ? "confirm-yes danger" : "confirm-yes", textContent: ok,
    });
    row.append(no, yes);
    box.append(row);
    back.append(box);
    document.body.append(back);
    no.focus(); // по умолчанию — безопасный вариант

    const done = (value) => {
      document.removeEventListener("keydown", onKey);
      back.remove();
      resolve(value);
    };
    const onKey = (e) => { if (e.key === "Escape") done(false); };
    document.addEventListener("keydown", onKey);
    no.onclick = () => done(false);
    yes.onclick = () => done(true);
    back.onclick = (e) => { if (e.target === back) done(false); };
  });
}
