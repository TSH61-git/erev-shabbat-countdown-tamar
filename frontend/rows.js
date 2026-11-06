// Turns a timeline from the backend into the one sequential list the design shows.
// No DOM here, so Node can test the rules without a browser.

export const LAST_THINGS = "דברים אחרונים";
export const THURSDAY_NIGHT = "חמישי בלילה";

export function toMin(hhmm) {
  const [h, m] = hhmm.split(":").map(Number);
  return h * 60 + m;
}

export function hhmm(min) {
  const m = ((min % 1440) + 1440) % 1440;
  return `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
}

export function taskTitle(name) {
  // Saved names use "_" for spaces; English names also get a capital letter.
  const text = String(name || "").replace(/_/g, " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export function durationHe(minutes) {
  if (minutes < 60) return `${minutes} דק׳`;
  return `${Math.floor(minutes / 60)}:${String(minutes % 60).padStart(2, "0")} שעות`;
}

function taskRow(key, title, start, end, day, done) {
  const minutes = start === null ? null : end - start;
  return { kind: "task", key, title, start, end, day, minutes, done: done.has(key), next: false };
}

function sameDay(tl, nowIso) {
  return Boolean(nowIso) && nowIso.slice(0, 10) === tl.friday;
}

export function buildRows(tl, doneKeys = [], nowIso = tl.now) {
  const done = new Set(doneKeys);
  const rows = [];
  const inPlan = new Set();

  // Thursday night comes first: it happens before Friday, and its tasks are ticked like any other.
  const thursday = (tl.thursday_blocks || []).filter((b) => b.kind === "active");
  if (thursday.length) {
    rows.push({ kind: "section", title: THURSDAY_NIGHT });
    for (const b of thursday) {
      const start = toMin(b.start);
      let end = toMin(b.end);
      if (end < start) end += 1440;
      rows.push(taskRow(`task:${b.task}`, taskTitle(b.task), start, end, "thu", done));
      inPlan.add(b.task);
    }
  }

  const blocks = [...(tl.blocks || [])].sort((a, b) => toMin(a.start) - toMin(b.start));
  for (const b of blocks) {
    const start = toMin(b.start), end = toMin(b.end);
    if (b.kind === "active") {
      rows.push(taskRow(`task:${b.task}`, taskTitle(b.task), start, end, "fri", done));
      inPlan.add(b.task);
    } else if (b.kind === "free" || b.kind === "break") {
      // The scheduler's optional short break is free time too; back-to-back gaps read as one.
      const prev = rows.at(-1);
      if (prev?.kind === "free" && prev.end === start) {
        prev.end = end;
        prev.minutes = end - prev.start;
      } else {
        rows.push({ kind: "free", title: "זמן פנוי", start, end, minutes: end - start });
      }
    } else if (b.kind === "checklist") {
      rows.push({ kind: "section", title: LAST_THINGS });
      let t = start;
      for (const item of tl.checklist || []) {
        rows.push(taskRow(`checklist:${item.text}`, item.text, t, t + item.minutes, "fri", done));
        t += item.minutes;
      }
    }
    // Passive blocks are not rows: in this app every task is active, one at a time.
  }

  // A re-plan only lists what is left; tasks already ticked stay visible at the top,
  // so "X מתוך Y" never shrinks after a re-plan.
  const ticked = [...done].filter((k) => k.startsWith("task:") && !inPlan.has(k.slice(5)));
  rows.unshift(...ticked.map((k) => taskRow(k, taskTitle(k.slice(5)), null, null, "fri", done)));

  const today = sameDay(tl, nowIso);
  const now = today ? toMin(nowIso.slice(11, 16)) : null;
  const tasks = rows.filter((r) => r.kind === "task");
  const ahead = (r) => r.start !== null && (!today || (r.day === "fri" && r.end > now));
  const next = tasks.find((r) => !r.done && ahead(r));
  if (next) next.next = true;

  if (today && now < toMin(tl.ready)) {
    let at = rows.findIndex((r) => r.kind !== "section" && r.start !== null && r.day === "fri" && r.end > now);
    if (at < 0) at = rows.length;
    if (at > 0 && rows[at - 1].kind === "section") at -= 1;  // the pill goes above a section title
    rows.splice(at, 0, { kind: "now", time: hhmm(now) });
  }

  return { rows, total: tasks.length, doneCount: tasks.filter((r) => r.done).length };
}

export function notices(tl) {
  const out = [];
  if (tl.suggestion) {
    const names = tl.suggestion.tasks.map(taskTitle).join(", ");
    out.push(`שישי ארוך מדי ב־${durationHe(tl.overflow)}. המתכנן מציע להעביר את ${names} לחמישי בלילה – אשרי בצ׳אט.`);
  } else if (tl.fits === false) {
    out.push(`התוכנית חורגת ב־${durationHe(tl.overflow)}. כתבי למתכנן מה אפשר לשנות.`);
  }
  if ((tl.dropped || []).length) {
    out.push(`ירד מהתוכנית בגלל הזמן: ${tl.dropped.map(taskTitle).join(", ")}. אפשר לבקש מהמתכנן להחזיר.`);
  }
  return out;
}

export function countdown(minutesUntilReady, bufferMin) {
  // Text for the Shabbat card: [label, number, unit].
  const left = Math.ceil(minutesUntilReady);
  if (left > 0) {
    if (left >= 2880) return ["נשארו עד שבת", String(Math.round(left / 1440)), "ימים"];
    if (left >= 60) return ["נשארו עד שבת", `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`, "שעות"];
    return ["נשארו עד שבת", String(left), "דקות"];
  }
  const toCandles = left + bufferMin;
  if (toCandles > 0) return ["את מוכנה · הדלקת נרות בעוד", String(toCandles), "דקות"];
  return ["", "שבת שלום", ""];
}

export function dayProgress(minutesUntilReady, dayStart, ready) {
  const span = toMin(ready) - toMin(dayStart);
  if (span <= 0) return 0;
  return Math.min(1, Math.max(0, 1 - minutesUntilReady / span));
}
