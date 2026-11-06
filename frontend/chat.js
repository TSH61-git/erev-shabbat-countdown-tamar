import { el, getJSON, icon, postJSON } from "./util.js";

// ---------- tiny Markdown: only what the agent writes (paragraphs, lists, tables, code) ----------

function inline(parent, text) {
  const re = /(\*\*[^*]+\*\*|`[^`]+`|\*[^*\s][^*]*\*)/g;
  let last = 0;
  for (const m of text.matchAll(re)) {
    if (m.index > last) parent.append(text.slice(last, m.index));
    const t = m[0];
    if (t.startsWith("**")) parent.append(el("strong", { text: t.slice(2, -2) }));
    else if (t.startsWith("`")) parent.append(el("code", { text: t.slice(1, -1) }));
    else parent.append(el("em", { text: t.slice(1, -1) }));
    last = m.index + t.length;
  }
  if (last < text.length) parent.append(text.slice(last));
  return parent;
}

function cells(row) {
  return row.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
}

export function markdown(container, text) {
  container.replaceChildren();
  const lines = text.replace(/\r/g, "").split("\n");
  let i = 0;
  const take = (test) => { const out = []; while (i < lines.length && test(lines[i])) out.push(lines[i++]); return out; };
  while (i < lines.length) {
    const line = lines[i];
    if (/^\s*```/.test(line)) {
      i++;
      const body = take((l) => !/^\s*```/.test(l));
      i++;
      container.append(el("pre", { text: body.join("\n") }));
    } else if (/^\s*\|.*\|\s*$/.test(line)) {
      const rows = take((l) => /^\s*\|.*\|\s*$/.test(l)).filter((r) => !/^\s*\|?[\s:|-]+\|?\s*$/.test(r));
      const [head, ...body] = rows;
      container.append(el("div", { class: "table-wrap" }, el("table", {},
        el("thead", {}, el("tr", {}, cells(head).map((c) => inline(el("th"), c)))),
        el("tbody", {}, body.map((r) => el("tr", {}, cells(r).map((c) => inline(el("td"), c))))))));
    } else if (/^\s*[-*•]\s+/.test(line)) {
      const items = take((l) => /^\s*[-*•]\s+/.test(l));
      container.append(el("ul", {}, items.map((l) => inline(el("li"), l.replace(/^\s*[-*•]\s+/, "")))));
    } else if (/^\s*\d+[.)]\s+/.test(line)) {
      const items = take((l) => /^\s*\d+[.)]\s+/.test(l));
      container.append(el("ol", {}, items.map((l) => inline(el("li"), l.replace(/^\s*\d+[.)]\s+/, "")))));
    } else if (/^#{1,6}\s+/.test(line)) {
      container.append(inline(el("p", { class: "md-h" }), line.replace(/^#{1,6}\s+/, "")));
      i++;
    } else if (!line.trim() || /^\s*(-{3,}|\*{3,})\s*$/.test(line)) {
      i++;
    } else {
      // The first line is always consumed, so a line that only looks like a table
      // (a lone "|") can never stall the loop.
      const para = [lines[i++], ...take((l) => l.trim() && !/^\s*([-*•]\s+|\d+[.)]\s+|\||```|#{1,6}\s)/.test(l))];
      const p = el("p");
      para.forEach((l, k) => { if (k) p.append(el("br")); inline(p, l); });
      container.append(p);
    }
  }
}

// ---------- messages ----------

const clock = () => icon(14, 14, { stroke: "#2B3A86", "stroke-width": "2", "stroke-linecap": "round" },
  ["circle", { cx: "12", cy: "12", r: "9" }], ["path", { d: "M12 7v5l3 2" }]);

function chip(label, running = false) {
  return el("div", { class: `chip${running ? " running" : ""}` }, clock(), label);
}

function botBubble(text, extra = "") {
  const b = el("div", { class: `bubble bot${extra ? ` ${extra}` : ""}` });
  markdown(b, text);
  return b;
}

const fridayFmt = new Intl.DateTimeFormat("he-IL", { day: "numeric", month: "long", timeZone: "UTC" });

function weekLabel(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  return `שבוע חדש · יום שישי ${fridayFmt.format(new Date(Date.UTC(y, m - 1, d)))}`;
}

function historyNodes(m) {
  if (m.type === "user") return [el("div", { class: "bubble me", text: m.text })];
  if (m.type === "assistant") {
    return [...(m.tool_labels || []).map((l) => chip(l)), botBubble(m.text || "", m.is_error ? "error" : "")];
  }
  if (m.type === "error") return [el("div", { class: "bubble bot error", text: "משהו השתבש ולא התקבלה תשובה. אפשר לנסות שוב." })];
  if (m.type === "week") return [el("div", { class: "divider", text: weekLabel(m.friday) })];
  if (m.type === "reset") return [el("div", { class: "divider", text: "שיחה חדשה" })];
  return [];
}

async function* events(response) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let cut;
    while ((cut = buffer.indexOf("\n\n")) >= 0) {
      const chunk = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      let name = "message", data = "";
      for (const line of chunk.split("\n")) {
        if (line.startsWith("event: ")) name = line.slice(7);
        else if (line.startsWith("data: ")) data += line.slice(6);
      }
      if (data) yield [name, JSON.parse(data)];
    }
  }
}

export function initChat({ onDone, userName }) {
  const msgs = document.getElementById("msgs");
  const form = document.getElementById("composer");
  const input = document.getElementById("msg");
  const sendBtn = document.getElementById("send");
  const status = document.getElementById("chatStatus");
  let busy = false;

  const scroll = () => { msgs.scrollTop = msgs.scrollHeight; };
  const setStatus = (text, isError = false) => { status.textContent = text; status.classList.toggle("error", isError); };
  const setBusy = (value) => { busy = value; sendBtn.disabled = value; };
  const greeting = () => botBubble(`שלום ${userName()}, ספרי לי על שישי שלך: באיזו עיר את גרה, כמה זמן לפני הדלקת נרות את רוצה להיות מוכנה, מה המשימות ובכמה זמן כל אחת, ומה הדברים האחרונים לפני שבת.`, "greeting");

  async function loadHistory() {
    const h = await getJSON("/api/history");
    const nodes = h.messages.flatMap(historyNodes);
    if (!h.messages.some((m) => m.type === "user")) nodes.push(greeting());
    msgs.replaceChildren(...nodes);
    scroll();
    if (h.busy) {
      // Another tab (or a closed one) is still finishing a reply; show it once it lands.
      setBusy(true);
      setStatus("המתכנן עדיין עונה…");
      setTimeout(() => loadHistory().then(() => { if (!busy) setStatus(""); }), 3000);
    } else {
      setBusy(false);
    }
  }

  async function send(text) {
    text = text.trim();
    if (!text || busy) return;
    msgs.querySelector(".greeting")?.remove();
    msgs.append(el("div", { class: "bubble me", text }));
    scroll();
    setBusy(true);
    setStatus("");
    let bubble = null, raw = "", pending = false, finished = false;
    const chips = [];
    const ensureBubble = () => {
      if (!bubble) { bubble = el("div", { class: "bubble bot typing" }); msgs.append(bubble); }
      return bubble;
    };
    const paint = () => { pending = false; markdown(ensureBubble(), raw); scroll(); };

    try {
      const r = await postJSON("/api/chat", { text });
      if (r.status === 409) throw new Error("המתכנן עדיין עונה על ההודעה הקודמת. נסי שוב בעוד רגע.");
      if (!r.ok) throw new Error("ההודעה לא נשלחה. נסי שוב.");
      for await (const [name, data] of events(r)) {
        if (name === "tool") {
          chips.forEach((c) => c.classList.remove("running"));
          if (chips.at(-1)?.textContent !== data.label) {
            const c = chip(data.label, true);
            chips.push(c);
            // Chips go above the reply, as in the design: what the planner did, then what it says.
            if (bubble) msgs.insertBefore(c, bubble); else msgs.append(c);
          } else {
            chips.at(-1).classList.add("running");
          }
          scroll();
        } else if (name === "text") {
          raw += data.delta;
          // One repaint per frame: deltas arrive faster than the eye can follow.
          if (!pending) { pending = true; requestAnimationFrame(paint); }
        } else if (name === "done") {
          finished = true;
          raw = data.reply || raw;
          paint();
          if (data.is_error) bubble.classList.add("error");
          onDone(data.changed || []);
        } else if (name === "error") {
          finished = true;
          ensureBubble().classList.add("error");
          bubble.textContent = "משהו השתבש ולא התקבלה תשובה. אפשר לנסות שוב.";
          console.warn("chat error:", data.message);
        }
      }
      if (!finished) {
        setStatus("החיבור נקטע; התשובה עדיין תישמר. טוענת מחדש…", true);
        setTimeout(() => loadHistory().then(() => onDone(["all"])), 3000);
      }
    } catch (e) {
      ensureBubble().classList.add("error");
      bubble.textContent = e.message;
    } finally {
      chips.forEach((c) => c.classList.remove("running"));
      bubble?.classList.remove("typing");
      setBusy(false);
      scroll();
    }
  }

  function fill(text, sendNow = false) {
    if (sendNow) { send(text); return; }
    input.value = text;
    input.focus();
    // Put the cursor on the blank the user has to fill, if any.
    const blank = text.indexOf("__");
    if (blank >= 0) input.setSelectionRange(blank, blank + 2);
    else input.setSelectionRange(text.length, text.length);
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = input.value;
    if (!text.trim() || busy) return;
    input.value = "";
    send(text);
  });
  document.getElementById("quick").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    if (b.dataset.send) fill(b.dataset.send, true);
    else fill(b.dataset.fill);
  });

  return { loadHistory, fill, isBusy: () => busy };
}
