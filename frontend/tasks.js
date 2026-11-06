import { buildRows, durationHe, hhmm, notices } from "./rows.js";
import { el, icon } from "./util.js";

const checkIcon = () => icon(16, 16, { stroke: "#fff", "stroke-width": "3", "stroke-linecap": "round", "stroke-linejoin": "round" },
  ["path", { d: "M5 12l5 5 9-10" }]);

function range(r) {
  return r.start === null ? "" : `${hhmm(r.start)}–${hhmm(r.end)}`;
}

function taskNode(r, onToggle) {
  const classes = ["row", r.done && "done", r.next && !r.done && "next"].filter(Boolean).join(" ");
  const check = el("button", {
    class: "check", type: "button", "aria-pressed": String(r.done), "aria-label": `סימון: ${r.title}`,
    onclick: () => onToggle(r.key, !r.done, check),
  }, checkIcon());
  return el("div", { class: classes },
    el("span", { class: "time", text: range(r) }),
    el("div", { class: "body" }, el("span", { class: "title", text: r.title })),
    el("span", { class: "dur", text: r.minutes === null ? "" : durationHe(r.minutes) }),
    check);
}

function rowNode(r, onToggle) {
  if (r.kind === "task") return taskNode(r, onToggle);
  if (r.kind === "free") {
    return el("div", { class: "free" }, el("span", { class: "time", text: range(r) }),
      el("span", { class: "title", text: r.title }), el("span", { class: "dur", text: durationHe(r.minutes) }));
  }
  if (r.kind === "section") return el("div", { class: "section", text: r.title });
  return el("div", { class: "now", "aria-label": `השעה עכשיו ${r.time}` }, el("span", { text: `עכשיו ${r.time}` }), el("div"));
}

export function renderTasks(tl, doneKeys, nowIso, onToggle) {
  const list = document.getElementById("rows");
  const count = document.getElementById("doneCount");
  const box = document.getElementById("notices");
  box.replaceChildren();
  if (!tl || tl.state === "empty") {
    count.textContent = "";
    list.replaceChildren(el("div", { class: "empty", text:
      "עוד אין תוכנית לשישי. ספרי למתכנן באיזו עיר את גרה, כמה זמן לפני הדלקת נרות את רוצה להיות מוכנה, ומה המשימות שלך ובכמה זמן כל אחת." }));
    return;
  }
  if (tl.state === "unavailable") {
    count.textContent = "";
    list.replaceChildren(el("div", { class: "empty", text: "לא הצלחתי להביא כרגע את זמני השבת. ננסה שוב בעוד כמה דקות." }));
    return;
  }
  const { rows, total, doneCount } = buildRows(tl, doneKeys, nowIso);
  count.textContent = total ? `${doneCount} מתוך ${total} הושלמו` : "";
  box.append(...notices(tl).map((text) => el("p", { class: "notice", text })));
  list.replaceChildren(...rows.map((r) => rowNode(r, onToggle)));
}
