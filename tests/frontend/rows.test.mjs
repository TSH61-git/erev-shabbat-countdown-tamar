import assert from "node:assert/strict";
import { test } from "node:test";

import { buildRows, countdown, dayProgress, durationHe, notices, taskTitle } from "../../frontend/rows.js";

// The design's own example day, as the backend would send it.
const plan = {
  friday: "2026-11-06", ready: "15:55", candles: "16:05", day_start: "11:45", fits: true, overflow: 0,
  blocks: [
    { start: "11:45", end: "12:25", kind: "active", task: "להכין_חצילים" },
    { start: "12:25", end: "12:40", kind: "active", task: "לסדר_שולחן" },
    { start: "12:40", end: "13:30", kind: "active", task: "להכין_דג" },
    { start: "13:30", end: "14:20", kind: "active", task: "לגהץ" },
    { start: "14:20", end: "15:00", kind: "active", task: "לנקות_את_הבית" },
    { start: "15:00", end: "15:30", kind: "free", task: null },
    { start: "15:30", end: "15:45", kind: "active", task: "להתקלח", last: true },
    { start: "15:45", end: "15:55", kind: "checklist", task: null },
  ],
  checklist: [{ text: "להתקשר לדבורה", minutes: 5 }, { text: "לכבות שעונים מעוררים", minutes: 5 }],
  thursday_blocks: [],
};
const DONE = ["task:להכין_חצילים", "task:לסדר_שולחן", "task:להכין_דג"];
const kinds = (rows) => rows.map((r) => (r.kind === "task" ? r.title : r.kind === "now" ? `now ${r.time}` : r.kind === "section" ? `§${r.title}` : "free"));

test("the design's day: one list in order, now pill, next row, count", () => {
  const { rows, total, doneCount } = buildRows(plan, DONE, "2026-11-06T13:30");
  assert.deepEqual(kinds(rows), [
    "להכין חצילים", "לסדר שולחן", "להכין דג", "now 13:30", "לגהץ", "לנקות את הבית", "free",
    "להתקלח", "§דברים אחרונים", "להתקשר לדבורה", "לכבות שעונים מעוררים"]);
  assert.equal(total, 8);
  assert.equal(doneCount, 3);
  assert.deepEqual(rows.filter((r) => r.next).map((r) => r.title), ["לגהץ"]);
  assert.deepEqual(rows.filter((r) => r.done).map((r) => r.title), ["להכין חצילים", "לסדר שולחן", "להכין דג"]);
});

test("the scheduler's short break and the free time after it are one free row", () => {
  const blocks = plan.blocks.map((b) => (b.kind === "free" ? { ...b, end: "15:10", kind: "break" } : b));
  blocks.splice(6, 0, { start: "15:10", end: "15:30", kind: "free", task: null });
  const free = buildRows({ ...plan, blocks }, [], "2026-11-06T09:00").rows.filter((r) => r.kind === "free");
  assert.deepEqual(free.map((r) => [r.title, r.start, r.end, r.minutes]), [["זמן פנוי", 900, 930, 30]]);
});

test("checklist items get stacked times ending at ready", () => {
  const items = buildRows(plan, [], "2026-11-06T09:00").rows.filter((r) => r.key?.startsWith("checklist:"));
  assert.deepEqual(items.map((r) => [r.start, r.end, r.minutes]), [[945, 950, 5], [950, 955, 5]]);
});

test("never two rows at the same time", () => {
  const withPassive = { ...plan, blocks: [...plan.blocks, { start: "12:40", end: "14:10", kind: "passive", task: "דג_בתנור" }] };
  const timed = buildRows(withPassive, [], "2026-11-06T09:00").rows.filter((r) => r.start !== undefined && r.start !== null);
  for (let i = 1; i < timed.length; i++) assert.ok(timed[i].start >= timed[i - 1].end, `${timed[i].title} overlaps`);
  assert.ok(!timed.some((r) => r.title === "דג בתנור"));
});

test("now inside a task puts the pill above it and marks it next", () => {
  const { rows } = buildRows(plan, DONE, "2026-11-06T13:40");
  const i = rows.findIndex((r) => r.kind === "now");
  assert.equal(rows[i].time, "13:40");
  assert.equal(rows[i + 1].title, "לגהץ");
  assert.ok(rows[i + 1].next);
});

test("an unticked past task is not next; the current one is", () => {
  const { rows } = buildRows(plan, [], "2026-11-06T13:40");
  assert.deepEqual(rows.filter((r) => r.next).map((r) => r.title), ["לגהץ"]);
});

test("the pill goes above the last-things title, not inside it", () => {
  const { rows } = buildRows(plan, [], "2026-11-06T15:46");
  const i = rows.findIndex((r) => r.kind === "now");
  assert.equal(rows[i + 1].kind, "section");
  assert.ok(rows.find((r) => r.next).title === "להתקשר לדבורה");
});

test("no pill on other days or after ready; next is the first open task", () => {
  for (const now of ["2026-11-05T20:00", "2026-11-06T15:58"]) {
    assert.ok(!buildRows(plan, [], now).rows.some((r) => r.kind === "now"), now);
  }
  assert.equal(buildRows(plan, ["task:להכין_חצילים"], "2026-11-05T20:00").rows.find((r) => r.next).title, "לסדר שולחן");
});

test("tasks ticked before a re-plan stay at the top", () => {
  const replan = { ...plan, blocks: plan.blocks.slice(3), replan_from: "13:30", delay: 20 };
  const { rows, total, doneCount } = buildRows(replan, DONE, "2026-11-06T13:30");
  assert.deepEqual(kinds(rows).slice(0, 4), ["להכין חצילים", "לסדר שולחן", "להכין דג", "now 13:30"]);
  assert.ok(rows.slice(0, 3).every((r) => r.done && r.start === null));
  assert.equal(total, 8);
  assert.equal(doneCount, 3);
});

test("Thursday night is its own section before Friday", () => {
  const moved = { ...plan, thursday_blocks: [{ start: "20:00", end: "20:40", kind: "active", task: "לנקות_את_הבית" }] };
  const { rows } = buildRows(moved, [], "2026-11-05T19:00");
  assert.deepEqual(kinds(rows).slice(0, 2), ["§חמישי בלילה", "לנקות את הבית"]);
  assert.ok(rows[1].next, "on Thursday the Thursday task is next");
  assert.ok(!buildRows(moved, [], "2026-11-06T09:00").rows[1].next, "on Friday it is past");
});

test("notices point to the chat when the plan has a problem", () => {
  assert.deepEqual(notices(plan), []);
  assert.match(notices({ ...plan, fits: false, overflow: 20 })[0], /חורגת ב־20 דק׳/);
  const sug = notices({ ...plan, fits: false, overflow: 40, suggestion: { tasks: ["לנקות_את_הבית"] } });
  assert.match(sug[0], /לנקות את הבית לחמישי בלילה/);
  assert.match(notices({ ...plan, dropped: ["פרחים"] })[0], /פרחים/);
});

test("Hebrew and English names, durations", () => {
  assert.equal(taskTitle("להכין_חצילים"), "להכין חצילים");
  assert.equal(taskTitle("set_table"), "Set table");
  assert.equal(durationHe(40), "40 דק׳");
  assert.equal(durationHe(70), "1:10 שעות");
});

test("countdown and day progress match the design at 13:30", () => {
  assert.deepEqual(countdown(145, 10), ["נשארו עד שבת", "2:25", "שעות"]);
  assert.deepEqual(countdown(25, 10), ["נשארו עד שבת", "25", "דקות"]);
  assert.deepEqual(countdown(-3, 10), ["את מוכנה · הדלקת נרות בעוד", "7", "דקות"]);
  assert.deepEqual(countdown(-12, 10), ["", "שבת שלום", ""]);
  assert.deepEqual(countdown(5 * 1440, 10), ["נשארו עד שבת", "5", "ימים"]);
  assert.equal(Math.round(dayProgress(145, "09:00", "15:55") * 100), 65);
  assert.equal(dayProgress(1000, "09:00", "15:55"), 0);
});
