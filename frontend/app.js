import { initChat } from "./chat.js";
import { countdown, dayProgress, hhmm, toMin } from "./rows.js";
import { renderTasks } from "./tasks.js";
import { getJSON, postJSON } from "./util.js";

const $ = (id) => document.getElementById(id);
const phone = window.matchMedia("(max-width: 759px)");
const REFRESH_MS = 2 * 60 * 1000;

const state = {
  user: { name: "תמר", initial: "ת" },
  candles: null, timeline: null, done: [],
  // Server minute counts plus the time they arrived: the page never trusts the device clock.
  readyAt: null, serverNow: null, fetchedAt: 0,
};

// ---------- time, from the server's own clock ----------

function nowIso() {
  if (!state.serverNow) return null;
  const elapsed = Math.floor((Date.now() - state.fetchedAt) / 60000);
  return `${state.serverNow.slice(0, 11)}${hhmm(toMin(state.serverNow.slice(11, 16)) + elapsed)}`;
}

// ---------- Shabbat card ----------

const hebrewCalendar = new Intl.DateTimeFormat("he-IL-u-ca-hebrew", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });

function fallbackDate(iso) {
  // Only when Hebcal can't be reached: the browser's Hebrew calendar (digits, not letters).
  const [y, m, d] = iso.split("-").map(Number);
  return hebrewCalendar.format(new Date(Date.UTC(y, m - 1, d)));
}

function renderShabbat() {
  const c = state.candles;
  if (!c) return;
  $("hebDate").textContent = `יום שישי · ${c.date_he || fallbackDate(c.friday)}`;
  $("parasha").textContent = c.parasha_he || c.parasha || "";
  $("candles").textContent = c.candles || "—";
  $("ready").textContent = c.ready || "—";
  $("bannerReady").textContent = c.ready || "—";
  tick();
}

function tick() {
  const c = state.candles;
  const num = $("countValue").parentElement;
  if (!c || !c.available) {
    $("countLabel").textContent = "נשארו עד שבת";
    $("countValue").textContent = "—";
    $("countUnit").textContent = "";
    setBar(0);
    return;
  }
  const left = (state.readyAt - Date.now()) / 60000;
  const [label, value, unit] = countdown(left, c.ready_buffer_min);
  $("countLabel").textContent = label;
  $("countValue").textContent = value;
  $("countUnit").textContent = unit;
  num.classList.toggle("is-text", !unit);
  setBar(dayProgress(Math.max(left, 0), c.day_start, c.ready));
}

function setBar(fraction) {
  const pct = Math.round(fraction * 100);
  $("barFill").style.width = `${pct}%`;
  $("bar").setAttribute("aria-label", `${pct} אחוז מהיום עברו`);
}

// ---------- tasks ----------

function renderList() {
  renderTasks(state.timeline, state.done, nowIso(), toggle);
}

async function toggle(key, done, button) {
  // Shown at once; put back if the server refuses, so the screen never lies about what is saved.
  const before = state.done;
  state.done = done ? [...before, key] : before.filter((k) => k !== key);
  renderList();
  try {
    const r = await postJSON("/api/done", { key, done });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    state.done = (await r.json()).done;
  } catch (e) {
    console.warn("done:", e);
    state.done = before;
  }
  renderList();
  button?.focus?.();
}

// ---------- profile popover ----------

const pop = $("profilePop"), popBtn = $("profileBtn");

function setPopover(open) {
  pop.hidden = !open;
  popBtn.setAttribute("aria-expanded", String(open));
  if (!open && pop.contains(document.activeElement)) popBtn.focus();
}

popBtn.addEventListener("click", (e) => { e.stopPropagation(); setPopover(pop.hidden); });
$("profileClose").addEventListener("click", () => setPopover(false));
document.addEventListener("click", (e) => { if (!pop.hidden && !pop.contains(e.target)) setPopover(false); });

function renderFacts(profile, tasks) {
  $("factCity").textContent = profile ? profile.city_he : "—";
  $("factBuffer").textContent = profile ? `${profile.ready_buffer_min} דקות` : "—";
  $("factStart").textContent = profile ? profile.friday_start : "—";
  $("factThursday").textContent = profile ? `${profile.thursday_night.start}–${profile.thursday_night.end}` : "—";
  const n = tasks.tasks.length, learned = tasks.tasks.filter((t) => t.learned_minutes !== null).length;
  $("factTasks").textContent = n ? `${n}, מתוכן ${learned} נלמדו` : "אין עדיין";
}

$("newConversation").addEventListener("click", async () => {
  if (chat.isBusy()) return;
  if (!confirm("להתחיל שיחה חדשה? הפרטים, המשימות והזמנים שנלמדו נשמרים.")) return;
  const r = await postJSON("/api/session/new");
  if (r.ok) { setPopover(false); await chat.loadHistory(); }
});

// ---------- phone: chat as a bottom sheet ----------

const fab = $("chatFab"), backdrop = $("backdrop");
const behind = [document.querySelector(".top"), document.querySelector("main")];

function setSheet(open) {
  document.body.classList.toggle("sheet-open", open);
  backdrop.hidden = !open;
  fab.setAttribute("aria-expanded", String(open));
  // The page behind a sheet must not take focus or clicks.
  for (const node of behind) node.inert = open;
  if (open) { $("fabDot").hidden = true; setTimeout(() => $("msg").focus(), 50); } else { fab.focus(); }
}
const sheetOpen = () => document.body.classList.contains("sheet-open");

fab.addEventListener("click", () => setSheet(true));
$("chatClose").addEventListener("click", () => setSheet(false));
backdrop.addEventListener("click", () => setSheet(false));
phone.addEventListener("change", () => { if (!phone.matches && sheetOpen()) setSheet(false); });
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (!pop.hidden) setPopover(false);
  else if (sheetOpen()) setSheet(false);
});

// ---------- loading ----------

async function loadCandles() {
  const c = await getJSON("/api/candles");
  state.candles = c;
  if (c.available) state.readyAt = Date.now() + c.minutes_until_ready * 60000;
  renderShabbat();
}

async function loadTimeline() {
  const tl = await getJSON("/api/timeline");
  state.timeline = tl;
  state.done = tl.done || [];
  if (tl.now) { state.serverNow = tl.now; state.fetchedAt = Date.now(); }
  renderList();
}

async function loadFacts() {
  const [h, p, t] = await Promise.all([getJSON("/api/health"), getJSON("/api/profile"), getJSON("/api/tasks")]);
  state.user = h.user;
  $("userName").textContent = h.user.name;
  $("avatar").textContent = h.user.initial;
  if (!state.serverNow) { state.serverNow = h.now; state.fetchedAt = Date.now(); }
  renderFacts(p, t);
}

async function refresh() {
  const results = await Promise.allSettled([loadFacts(), loadCandles(), loadTimeline()]);
  results.forEach((r) => { if (r.status === "rejected") console.warn("refresh:", r.reason); });
}

const chat = initChat({
  userName: () => state.user.name,
  onDone: () => {
    if (phone.matches && !sheetOpen()) $("fabDot").hidden = false;
    refresh();
  },
});

refresh().then(() => chat.loadHistory()).catch((e) => console.warn("start:", e));
setInterval(tick, 15000);
setInterval(renderList, 60000);  // moves the "now" pill and the next row with the clock
setInterval(() => { if (!document.hidden) refresh(); }, REFRESH_MS);
document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
