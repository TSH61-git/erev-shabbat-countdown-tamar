# PLAN — Erev Shabbat Countdown (API vs SDK)

> On approval, the first implementation step copies this file verbatim to `erev-shabbat-countdown-tamar/PLAN.md`. No code is written before that.

## 0. Context

Course assignment: build one agent twice. `API/` uses the raw Claude Messages API with a hand-written harness. `SDK/` uses the Claude Agent SDK, where Claude Code is the harness. Then measure both and report what the SDK saved and what it cost.

The agent plans Erev Shabbat for one person in Jerusalem. It fetches candle lighting from Hebcal, builds a deterministic backwards-derived timeline that ends 10 minutes before candle lighting, learns real durations week to week, stores one-time week overrides, suggests Thursday-night moves, and re-plans live when I fall behind. It does logistics only and refers halachic questions to a rav.

### Facts verified for this plan (2026-10-22)

| Item | Verified value | Source |
|---|---|---|
| Model ID | `claude-opus-5-5` | Claude API skill model table |
| Messages API effort | `output_config={"effort": "medium"}` (inside `output_config`, not top-level). Opus 5.5 default is already `medium`, set explicitly anyway | Claude API skill |
| Messages API thinking | `thinking={"type": "adaptive"}`. `disabled` and `budget_tokens` return 400 on Opus 5.5 | Claude API skill |
| Forced tool choice | `tool_choice` `any`/`tool` returns 400 on Opus 5.5. Use `auto` only | Claude API skill |
| Agent SDK model / effort | `ClaudeAgentOptions(model="claude-opus-5-5", effort="medium", thinking={"type": "adaptive"})`. `effort` and `thinking` are separate fields | code.claude.com/docs/en/agent-sdk/python |
| Agent SDK other fields | `system_prompt`, `tools`, `allowed_tools`, `mcp_servers`, `strict_mcp_config`, `setting_sources`, `resume`, `cwd`, `permission_mode` (`"dontAsk"` exists), `hooks`, `env`, `max_turns` | same page |
| Note | `allowed_tools` only auto-approves; it does NOT restrict. `tools` is what restricts built-ins | same page |
| CLAUDE.md | Loaded by `setting_sources=["project"]` from `cwd`, works with a custom system prompt, injected into the conversation (not the system prompt) | agent-sdk/modifying-system-prompts |
| ResultMessage | `usage` (top-level loop only), `model_usage` (per model, camelCase), `total_cost_usd` (client estimate), `num_turns`, `session_id`, `duration_ms`. In `ClaudeSDKClient` streaming mode `total_cost_usd` and `model_usage` are **cumulative** | agent-sdk/python |
| Session resume | `resume=session_id`; transcripts in `~/.claude/projects/<encoded-cwd>/<id>.jsonl` | agent-sdk/sessions |
| Caching minimum | 512 tokens for Opus 5.5 (bundled doc says to confirm on the live docs page; we verify with `count_tokens` in Phase 8) | prompt-caching.md |
| Cache pricing Opus 5.5 | input $4/MTok, output $20/MTok, cache read $0.20/MTok, 5-min cache write 1.25× = $5/MTok | Claude API skill |
| Preserved thinking | Opus 5.5 rejects edited history (accounts created after 2026-08-31). History must be append-only. Full-summary compaction (new conversation with no old thinking blocks) is safe; keep-tail compaction breaks | model-migration.md |
| Hebcal endpoint | `GET https://www.hebcal.com/shabbat?cfg=json&geonameid=281184&gy=YYYY&gm=M&gd=D&M=on&leyning=off` | hebcal.com docs + live test |
| Jerusalem | geonameid `281184`. Hebcal applies the 40-minute Jerusalem candle-lighting offset automatically; we do **not** send `b` | live response |
| Scenario candle times | 2026-10-30: **16:11** (ready 16:01). 2026-11-06: **16:05** (ready 15:55). Both `+02:00` | live response |
| Library versions (PyPI today) | `anthropic==2.1.0`, `claude-agent-sdk==0.2.172`, `requests==2.34.2`, `pytest==9.1.1`, `pytest-asyncio==1.4.0`; Python 3.13.3 installed | `pip index versions` |
| anthropic 2.x surface (Phase 0) | Sends API version `2026-10-21`. `messages.create` has `thinking`, `output_config` (`effort`: low/medium/high/xhigh/max), `tool_choice`, `cache_control`; `count_tokens` exists; `Usage` has `input_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`, `output_tokens` | installed package introspection |

---

## 1. Folder and file tree

```
erev-shabbat-countdown-tamar/
  .gitignore                 .env, *.env, secrets*, data/, __pycache__/, .pytest_cache/, .venv/
  requirements.txt           exact pins (anthropic, claude-agent-sdk, requests, pytest, pytest-asyncio)
  PLAN.md                    this plan
  REPORT.md                  final comparison report (written last)
  scenario.json              the 8-turn, 2-session fixed-date scenario
  conftest.py                adds project root to sys.path for tests
  shared/
    __init__.py
    config.py                MODEL, EFFORT, CITY->geonameid map, defaults, DATA_DIR resolution
    pricing.py               per-MTok prices for claude-opus-5-5 (one table, used by both versions)
    prompts.py               SYSTEM_PROMPT text, shared by both versions (stable, no timestamps)
    store.py                 DataStore(data_dir): atomic JSON read/write, jsonl append, reset()
    domain.py                dataclasses: Profile, Task, Anchor, ChecklistItem, WeekOverride, Block, Timeline
    memory_files.py          load/save of the two memory layers (permanent + week-only), expiry rule
    hebcal.py                get_shabbat_times(): HTTP call, parse, cache; injectable fetcher for tests
    scheduler.py             build_timeline(): pure deterministic scheduler (no I/O)
    tool_specs.py            the 10 tool names, descriptions, JSON input schemas (single source of truth)
    tools_impl.py            the 10 tool functions; return compact strings; raise ToolError on bad input
    context_line.py          render_context(): the small per-turn context block
    scenario.py              load scenario.json, reset_state(data_dir), "now" injection
    checks.py                the 7 pass/fail checks on saved state + tool log; validate_timeline()
    results.py               results-file builder, LOC counter for both versions
    fixtures/
      hebcal_2026-10-30.json real Hebcal response captured for tests
      hebcal_2026-11-06.json real Hebcal response captured for tests
  API/
    main.py                  CLI: --scenario --out [--data-dir --compact-threshold]; runs scenario, checks, writes results
    agent.py                 ApiAgent: wires harness modules together; one instance per session
    harness/
      __init__.py
      loop.py                the while stop_reason == "tool_use" loop
      tools.py               tool schemas for the API, dispatch, is_error results
      history.py             append-only history.jsonl; rebuild active message list
      memory.py              load permanent + week memory from files for the agent
      context.py             builds each turn's user message (context block + text)
      compaction.py          threshold check + full-conversation summary
      caching.py             cache_control breakpoints on a per-request copy
      tokens.py              usage + cost per call and per turn
  SDK/
    main.py                  CLI: same flags; warns if ANTHROPIC_API_KEY set; runs scenario, checks, writes results
    agent.py                 ClaudeAgentOptions, ClaudeSDKClient, hook, CLAUDE.md render, usage extraction
    tools.py                 @tool wrappers over shared.tools_impl + create_sdk_mcp_server
  tests/
    test_scheduler.py        scheduler rules
    test_hebcal.py           parsing, caching, errors (mocked HTTP)
    test_memory_files.py     two memory layers, expiry, overrides never touch templates
    test_tools_impl.py       compact outputs, validation errors
    test_api_loop.py         fake Anthropic client: loop, parallel tool results, errors, refusal
    test_api_history.py      append-only, reload, compaction marker
    test_api_compaction.py   trigger, summary request shape, safe shape (no old thinking blocks)
    test_api_caching.py      breakpoint count/placement, stable system bytes, stored history untouched
    test_api_tokens.py       cost math, per-turn aggregation
    test_api_context.py      context block content, no full task list
    test_api_e2e.py          full scenario with fake client + fake Hebcal -> results file shape
    test_sdk_options.py      options builder fields, API-key warning, tool wrappers (no CLI launched)
    test_checks.py           each check passes on a good state and fails on a crafted bad state
    fakes.py                 FakeAnthropicClient, scripted responses, FakeHebcal
  results/
    api.json
    sdk.json
  data/                      runtime only, git-ignored; data/api/ and data/sdk/ wiped before each run
```

**Why `checks.py`, `scenario.py`, `results.py` live in `shared/`:** both runners must apply identical checks, reset logic and result fields, so the comparison is fair. They are runner utilities, not harness code, and are excluded from LOC counts.

---

## 2. Data model (files under `data/<api|sdk>/`)

Every JSON write uses `sort_keys=True` and a temp-file rename (deterministic bytes, no torn writes).

### Permanent memory

`profile.json`
```json
{"city": "Jerusalem", "geonameid": 281184, "ready_buffer_min": 10,
 "friday_start": "12:00", "thursday_night": {"start": "20:00", "end": "00:00"}}
```
`thursday_night.end` of `"00:00"` means midnight at the end of Thursday. The scheduler treats an end at or before the start as the next day.

`tasks.json` (templates; never modified by week overrides)
```json
{"tasks": [
  {"name": "cleaning", "minutes": 50, "kind": "active", "depends_on": [],
   "priority": "must", "anchor": null, "heavy": true, "tags": [], "thursday_ok": true},
  {"name": "set_table", "minutes": 15, "kind": "active", "depends_on": [],
   "priority": "must", "anchor": {"type": "end_by", "time": "15:00"}, "heavy": false,
   "tags": [], "thursday_ok": false},
  {"name": "shower", "minutes": 20, "kind": "active", "depends_on": [],
   "priority": "must", "anchor": {"type": "last"}, "heavy": false, "tags": [], "thursday_ok": false}
   ... (the full task list is the table in section 4)
]}
```
`anchor` is `null`, `{"type":"end_by","time":"HH:MM"}` or `{"type":"last"}` (last task before the checklist; at most one).

`checklist.json`
```json
{"items": [{"text": "Turn off phone", "minutes": 2}, {"text": "Set alarm", "minutes": 2},
           {"text": "Turn on hot plate", "minutes": 3}, {"text": "Set light timers", "minutes": 3}]}
```

`learned.json` (learned minutes = rounded mean of the last 3 samples; one sample gives that sample exactly)
```json
{"challah": {"minutes": 90, "samples": [{"friday": "2026-10-30", "minutes": 90}]},
 "cleaning": {"minutes": 40, "samples": [{"friday": "2026-10-30", "minutes": 40}]}}
```

### Week-only memory

`weeks/2026-11-06.json` (one file per Friday)
```json
{"friday": "2026-11-06",
 "overrides": [
   {"type": "skip_tags", "tags": ["dinner"], "note": "guests Friday night"},
   {"type": "move_to_thursday", "tasks": ["cleaning"], "note": "approved"}
 ]}
```
Override types: `skip_task{task}`, `skip_tags{tags}`, `set_minutes{task,minutes}`, `start_time{time}`, `move_to_thursday{tasks}`, `add_task{task-object}`.
**Expiry rule:** a week file is "active" only while `now < friday + 1 day 23:59` (through Shabbat). Expired files stay on disk for history but are never loaded into context or the scheduler.

### Caches and logs

- `times_cache.json`: `{"2026-10-30": {"candles": "16:11", "havdalah": "17:28", "parasha": "Vayera"}}`
- `last_timeline.json`: the structured last timeline (blocks with start, end, kind, task), read by checks.
- `tool_log.jsonl`: one line per tool invocation `{"turn": 4, "tool": "get_shabbat_times", "ok": true}`. Written by `shared/tools_impl.py`, so both versions log identically.
- API only: `history.jsonl` (section 5). SDK only: `session.json` `{"session_id": "..."}` and `workspace/CLAUDE.md`.

---

## 3. Tools (10 tools; specs in `shared/tool_specs.py`)

**Permanent delete vs week-only skip.** `remove_task` and `remove_checklist_item` delete from the saved templates forever. `set_week_override` only skips something for one Friday and never touches templates. For an ambiguous request such as "I don't need challah", the system prompt tells the agent to ask "permanently, or only this week?" before calling either tool.

**Design changes vs the original list, and why:**
- `upsert_task` becomes **`upsert_tasks(tasks:[...])`**. Turn 2 adds ~11 tasks; one call instead of 11 saves model calls and tokens.
- `set_checklist` + `add_checklist_item` merge into **`set_checklist(items, mode=replace|append)`**. Same effect, one schema.
- `log_actual` takes a list **`log_actuals(friday_date, entries:[{task,minutes}])`**. Turn 5 reports two tasks in one call.
- New **`update_profile`**. Check 1 needs city and buffer saved by a tool; there was no tool for it.

| Tool | Input schema (all `additionalProperties:false`, `strict:true` on API) | Example compact output |
|---|---|---|
| `get_shabbat_times` (EXTERNAL) | `{friday_date: "YYYY-MM-DD"}` | `2026-10-30 Jerusalem: candles 16:11, havdalah 17:28 (Vayera). ready 16:01` |
| `update_profile` | `{city?, ready_buffer_min? (int 0-60), friday_start? "HH:MM", thursday_night_start?, thursday_night_end?}` | `profile: Jerusalem, buffer 10, Fri start 12:00, Thu night 20:00-00:00` |
| `upsert_tasks` | `{tasks: [{name, minutes, kind: active\|passive, depends_on: [str], priority: must\|nice, anchor: null\|{type: end_by, time}\|{type: last}, heavy: bool, tags: [str], thursday_ok: bool}]}` | `saved 11 tasks (2 updated). 11 total` |
| `list_tasks` | `{}` | one line per task: `challah 90m* active must <-shopping thu_ok` (`*` = learned) |
| `set_checklist` | `{items: [{text, minutes}], mode: replace\|append}` | `checklist: 4 items, 10 min` |
| `log_actuals` | `{friday_date, entries: [{task, minutes}]}` | `learned: challah 60->90, cleaning 50->40` |
| `remove_task` | `{name}` | `removed template challah_bake; also removed it from depends_on of: none. 10 tasks` (unknown name → error listing names) |
| `remove_checklist_item` | `{text}` (case-insensitive exact match) | `removed checklist item "Set alarm". 3 items, 8 min` (no match → error listing items) |
| `set_week_override` | `{friday_date, change: {type, ...}, note}` | `override 2026-11-06: skip tag dinner -> skips cooking(must), slow_cook(must). CONFLICT with templates; templates unchanged` |
| `build_timeline` | `{friday_date, now?: "YYYY-MM-DDTHH:MM", delay_minutes?: int}` | see below |

`build_timeline` output example (one line per block, plus a header and footer):
```
Fri 2026-10-30 candles 16:11 ready 16:01 | start 12:00 | slack 6
12:00-12:50 Shopping (active)
12:50-13:05 Cooking (active)
13:05-14:05 Challah (active)
13:05-15:35 Slow cook (passive)
...  (one line per block, sorted by start)
15:25-15:31 free
15:31-15:51 Shower (active, last)
15:51-16:01 Checklist: phone off, alarm, hot plate, light timers
```
When Friday does not fit: `DOES NOT FIT by 20 min. SUGGEST Thursday night 20:00-20:40: cleaning (40). Ask user to approve.` and the Friday plan assuming the suggestion.
In re-plan mode: `RE-PLAN from 13:00 (40 behind): dropped flowers (nice)` followed by the remaining blocks.

**Errors:** every tool raises `ToolError("...")` for bad input (unknown task, cycle in dependencies, bad time, unsupported city). The API harness returns it as `tool_result` with `is_error: true`; the SDK wrapper returns `{"content": [...], "is_error": True}`.

`build_timeline` reads candle time from `times_cache.json` and calls `hebcal` itself if missing (so it never fails on a cold cache). Check 7 counts **model-initiated** `get_shabbat_times` calls in `tool_log.jsonl`; the system prompt tells the model to call it first.

---

## 4. Scheduler algorithm (`shared/scheduler.py`, pure function)

Approach: a **backward pass** computes each task's latest allowed finish from the ready time (this is the "backwards timeline"). A **forward pass** then places tasks as early as possible, in order of latest start. Pure backward placement (as-late-as-possible) would turn every minute of delay into an overrun; forward placement keeps the slack before the shower, where a live re-plan can use it.

```
def build_timeline(profile, templates, learned, checklist, week, candles, now=None, delay=0):
    ready = candles - profile.ready_buffer_min
    tasks = effective_tasks(templates, learned, week)      # learned minutes, skips, set_minutes, add_task
    validate: unique names, deps exist & not skipped (else drop dep with warning), no cycles, <=1 "last"
    day_start = week.start_time or profile.friday_start
    thursday = week.move_to_thursday tasks (+ their passive successors)

    if now: return replan(...)                               # see below

    C = sum(checklist.minutes)
    checklist_block = [ready - C, ready]
    active_limit = ready - C
    last = task with anchor "last"
    if last: last_block = [active_limit - last.d, active_limit]; active_limit = last_block.start

    # ---- backward pass: latest finish (LF) per task ----
    for t in reverse_topological(tasks without last, thursday):
        LF[t] = min(ready if t.passive else active_limit,
                    anchor_end_by(t),
                    min(LS[s] for s in successors(t)))         # successor latest start
        if t.active: enforce single resource backwards:
            # process actives in order of LF desc (tie: non-heavy first, so heavy lands earlier)
            LF[t] = min(LF[t], cursor); cursor = LF[t] - t.d
        LS[t] = LF[t] - t.d
    if min(LS) < day_start: overflow = day_start - min(LS) -> thursday_split()

    # ---- forward pass: place ASAP by LS ----
    t_active = day_start
    for t in tasks sorted by (LS, not heavy, name):           # deterministic order
        earliest = max(end[p] for p in deps(t)) or day_start
        if t.active: start = max(t_active, earliest); t_active = start + t.d
        else:        start = earliest                          # passive overlaps freely
        end = start + t.d
        assert end <= LF[t] else infeasible
    gap between last active end and last_block.start -> "free" block
    if free >= 15 and option enabled: insert "Break (optional)" 10 min mid-day
    return Timeline(blocks + last_block + checklist_block, slack, suggestions)
```

**Thursday split** (suggestion only; applied only after an approved `move_to_thursday` override):
```
candidates = tasks with thursday_ok, no anchor, not already moved
  each candidate brings its passive successors (dough rise/bake move with the dough)
  and requires its predecessors to be done earlier (moved too, or already Thursday)
pick the single candidate whose active minutes >= overflow, sorted by (heavy desc, minutes asc)
if none: greedy add largest candidates until overflow covered
check Thursday window: sum(active) fits in thursday_night window, else report "still does not fit"
```

**Live re-plan** (`now`, `delay`):
```
base = build_timeline(..., now=None)                    # same deterministic plan
effective = now - delay                                  # where I actually am in the plan
done      = blocks with end <= effective
partial   = block in progress at effective -> remaining = end - effective
remaining = partial + not-yet-started tasks (passive successors of unfinished tasks stay)
schedule remaining with day_start = now (backward pass + forward pass as above)
while infeasible and any nice task remains:
    drop the nice task with the latest LS (then largest minutes) ; reschedule
if still infeasible: report "MUST tasks over by N min" and list them (never silently drop must)
```

**Worked numbers for the scenario** (template minutes; chosen so each feature actually fires):

| Task | min | kind | deps | priority | other |
|---|---|---|---|---|---|
| shopping | 50 | active | – | must | |
| challah | 60 | active | – | must | dough first; flour is at home |
| challah_rise | 45 | passive | challah | must | |
| challah_bake | 30 | passive | challah_rise | must | |
| cooking | 15 | active | shopping | must | tag dinner, thu_ok |
| slow_cook | 150 | passive | cooking | must | tag dinner |
| cleaning | 50 | active | – | must | heavy, thu_ok |
| set_table | 15 | active | – | must | end_by 15:00 |
| flowers | 15 | active | – | nice | |
| shower | 20 | active | – | must | anchor last |
| checklist | 10 | – | – | – | 4 items |

Friday starts at 12:00. Thursday night is 20:00-00:00.

| Turn | Situation | Expected scheduler result |
|---|---|---|
| 4 | Week 1: ready 16:01, 241 min window, need 230 | **Fits, slack 6.** Shopping 12:00-12:50, cooking 12:50-13:05, challah 13:05-14:05, shower 15:31-15:51, checklist 15:51-16:01 |
| 6 | Week 2: ready 15:55, 235 min; learned challah 90, cleaning 40 | **Over by 20.** Suggests moving **cleaning** (the only heavy Thursday candidate, 40 ≥ 20) to Thursday 20:00-20:40. Friday then fits with slack 5 |
| 7 | Approval of cleaning move + guests | Week file gets two overrides: `move_to_thursday [cleaning]` and `skip_tags [dinner]`. Cooking and slow_cook are skipped for this week only |
| 8 | 13:00, 40 behind, so effective 12:20 | Challah started 12:00 and is 20 min in, 70 left. Remaining need 180 > 175 available, so **flowers (nice) is dropped**. Challah 13:00-14:10, set_table 14:10-14:25 (before the 15:00 anchor), shopping 14:25-15:15, free 15:15-15:25, shower 15:25-15:45, checklist 15:45-15:55. Rise and bake end 15:25 |

These numbers are also unit tests in `test_scheduler.py`, so a scheduler bug shows up offline before any paid run. If Phase 2 shows a different tie-break result, I'll adjust the minutes and report the change.

**Sensitivity note:** the turn-8 drop depends on the 4-item, 10-minute checklist. With a 2-item checklist, turn 8 fits with no drop.

---

## 5. API harness (`API/harness/`)

Every module prints through one helper per file: `log = make_logger("loop.py")` → `print(f"[loop.py] {msg}")`. Nothing is printed without the file name.

| File | Responsibility | Main functions | Prints (examples) |
|---|---|---|---|
| `loop.py` | Agent loop for one user turn | `run_turn(client, request_builder, dispatcher, history, meter) -> str` | `[loop.py] turn 4 call 1: stop_reason=tool_use (2 tool_use blocks)` / `[loop.py] turn 4 call 3: stop_reason=end_turn` / `[loop.py] refusal: category=...` |
| `tools.py` | API tool list from `shared.tool_specs` (sorted, deterministic), dispatch, error wrapping | `api_tool_defs()`, `dispatch(block, turn) -> tool_result dict` | `[tools.py] dispatch build_timeline {friday_date: 2026-10-30}` / `[tools.py] build_timeline ok (9 lines, 412 chars)` / `[tools.py] upsert_tasks ERROR: unknown dependency 'shoping' -> is_error` |
| `history.py` | Append-only `history.jsonl`; rebuild active messages (after the last compaction marker) | `append_message(msg)`, `append_compaction(summary)`, `load_active() -> list`, `append_session_start(n)` | `[history.py] append assistant (3 blocks) seq=17` / `[history.py] loaded 22 messages from disk (session 2 resume)` |
| `memory.py` | Read the two memory layers through `shared.memory_files`; expire week files | `load(data_dir, now) -> MemoryView` | `[memory.py] permanent: profile, 11 tasks, 4 checklist items, 2 learned` / `[memory.py] week 2026-11-06: 1 override` / `[memory.py] expired week 2026-10-30 skipped` |
| `context.py` | Build the user message: context block + user text. Static system prompt stays untouched | `build_user_message(memory_view, now, text) -> dict` | `[context.py] context 212 chars: city, buffer, 1 override, summary line` |
| `compaction.py` | Before each user turn: if last request's total input > threshold, summarize the whole active conversation | `should_compact(last_usage, threshold)`, `compact(client, active_msgs) -> summary` | `[compaction.py] input 9,842 > 8,000 -> compacting 26 messages` / `[compaction.py] summary 640 chars; new conversation starts from summary` |
| `caching.py` | Put `cache_control` on a **copy** of the request: end of system block (covers tools+system), and last block of the newest user message (rolling). Max 2 of 4 breakpoints | `apply(request) -> request` | `[caching.py] breakpoints: system, msg[21].content[-1]` |
| `tokens.py` | Read `response.usage`, compute cost from `shared.pricing`, aggregate per turn | `record(turn, usage)`, `turn_summary(turn)` | `[tokens.py] call: in=812 cache_r=3,104 cache_w=640 out=402 $0.0181` / `[tokens.py] turn 4 total: 3 calls $0.051` |

**Request shape** (built in `agent.py`):
```
client.messages.create(model="claude-opus-5-5", max_tokens=16000,
    thinking={"type": "adaptive"}, output_config={"effort": "medium"},
    system=[{"type": "text", "text": SYSTEM_PROMPT}],  tools=api_tool_defs(),
    tool_choice={"type": "auto"}, messages=active_messages)
```
Non-streaming is fine at 16k `max_tokens`. No server-side `fallbacks`: a fallback would switch models and break the same-model rule. Refusals are handled by stopping the turn and recording it.

**Loop rules:**
- Append `response.content` exactly as returned, thinking blocks included and unchanged. Never edit earlier messages.
- All `tool_result` blocks of one response go back in ONE user message, in the same order as the `tool_use` blocks.
- Max 10 calls per turn as a safety stop.
- Stop reasons: `tool_use` loops; `end_turn` returns the text; `max_tokens`/`refusal`/`pause_turn` are logged and end the turn.

**`history.jsonl` lines:**
```
{"seq": 1, "type": "session_start", "session": 1}
{"seq": 2, "type": "message", "message": {"role": "user", "content": [...]}}
{"seq": 9, "type": "compaction", "summary": "...", "covers_seq": [2, 8]}
```
`load_active()` returns `[user: "<summary>...</summary>"]` plus everything after the latest compaction. Old thinking blocks are never replayed after compaction. Under preserved thinking that is the safe shape.

**Session 2:** `API/main.py` destroys the first `ApiAgent` and builds a new one. The new agent reloads memory from files and the conversation from `history.jsonl` (parallel to SDK `resume`). The threshold is set so compaction fires during session 2.

**Compaction threshold:** default 8,000 input tokens, CLI `--compact-threshold`. Phase 8 calibrates it from the first live run's per-call input sizes so it triggers once, around turn 6 or 7. The summary call uses the same model and effort and counts as a model call of that turn.

**System prompt** (`shared/prompts.py`, identical bytes every request) covers: role, scope (logistics and timing only), the halachic-referral sentence, tool-use rules ("call get_shabbat_times before build_timeline; never compute times yourself; show the tool's timeline lines as returned"), conflict handling ("week-only changes go to set_week_override; never edit templates for one week"), Thursday moves need approval, compact replies, and the note that context blocks and system reminders come from the application. It also includes the rule to ask "permanently, or only this week?" when a removal request is ambiguous. Combined with 10 tool schemas this is well above 512 tokens. Phase 8 confirms with `client.messages.count_tokens`.

---

## 6. SDK version

### Exact options (`SDK/agent.py`)
```
server = create_sdk_mcp_server(name="shabbat", version="1.0.0", tools=ALL_TOOLS)   # replaces: tools.py
options = ClaudeAgentOptions(
    model="claude-opus-5-5",
    effort="medium",
    thinking={"type": "adaptive"},
    system_prompt=SYSTEM_PROMPT,                       # same text as API
    tools=[],                                          # no built-in tools
    mcp_servers={"shabbat": server},                   # replaces: tools.py
    allowed_tools=[f"mcp__shabbat__{n}" for n in TOOL_NAMES],   # replaces: tools.py
    strict_mcp_config=True,
    permission_mode="dontAsk",                         # headless: anything not allowed is denied
    setting_sources=["project"],                       # replaces: memory.py  (loads workspace/CLAUDE.md)
    cwd=DATA_DIR / "workspace",                        # replaces: memory.py
    resume=saved_session_id_or_None,                   # replaces: history.py
    hooks={"UserPromptSubmit": [HookMatcher(hooks=[inject_context])]},  # replaces: context.py
    max_turns=15,                                      # replaces: loop.py (safety stop)
)
```

### `# replaces:` map

| Harness file | SDK lines marked `# replaces: <file>` |
|---|---|
| `loop.py` | `async with ClaudeSDKClient(options)`, `await client.query(...)`, `async for msg in client.receive_response()`, `max_turns` |
| `tools.py` | each `@tool(...)` wrapper in `SDK/tools.py`, `create_sdk_mcp_server`, `mcp_servers`, `allowed_tools`, `is_error` return |
| `history.py` | save `ResultMessage.session_id` to `session.json`, `resume=` |
| `memory.py` | render `workspace/CLAUDE.md` from shared memory files at session start, `setting_sources`, `cwd` |
| `context.py` | the `UserPromptSubmit` hook returning `additionalContext` = `render_context(...)` |
| `compaction.py` | none: automatic. One comment line documents it; LOC counted as 0 |
| `caching.py` | none: automatic. LOC 0 |
| `tokens.py` | read `ResultMessage.usage`, `model_usage`, `total_cost_usd`, `num_turns`; compute per-turn deltas because cost and model_usage are cumulative |

LOC rule: API counts non-blank, non-comment lines per harness file. SDK counts the lines that carry each tag. Both are computed by `shared/results.py`.

### Memory in the SDK
- **CLAUDE.md** (`data/sdk/workspace/CLAUDE.md`) holds permanent memory as text: city, buffer, Friday start, Thursday window, and the one-line task summary. It is rendered from the shared JSON files at the start of each session. It is loaded through `setting_sources=["project"]`.
- **Same shared files** are read and written through the MCP tools, so the scheduler sees exactly what the API version sees.
- **Per-turn context** (now, this Friday, week overrides) comes from the hook, identical text to API `context.py`.

### Session 2 resume
Session 1 runs turns 1–5 in one `ClaudeSDKClient`. Its `session_id` goes to `data/sdk/session.json`. Session 2 creates a **new** client with `resume=session_id`, re-renders CLAUDE.md first, then runs turns 6–8.

### Auth
`SDK/main.py` prints a loud warning and exits unless `--allow-api-key` is passed when `ANTHROPIC_API_KEY` is set. It runs on the Claude Code login. `API/main.py` exits with a clear message if the key is missing. Keys are never read from files.

### Per-turn SDK metrics
- **tokens:** `ResultMessage.usage` (verify in Phase 9 whether it is per query or cumulative; compute deltas if cumulative).
- **model calls:** `num_turns`, cross-checked with the count of distinct assistant message IDs.
- **cost:** two figures. Delta of `total_cost_usd`, and our own cost from usage × `shared.pricing`, so both versions are priced the same way.
- `model_usage` keys also reveal any extra helper-model calls Claude Code makes. That is a report finding.

---

## 7. Offline tests (`pytest`, no key, no network)

**FakeAnthropicClient** (`tests/fakes.py`): `client.messages.create(**kwargs)` records every request (deep copy) and returns the next scripted response. Responses are real SDK model objects (`anthropic.types.Message`) built from dicts, so the harness exercises real attribute access. Usage numbers are scripted so cost math is checkable. `count_tokens` is faked too.

**FakeHebcal:** `shared.hebcal` takes an injectable `fetch(url) -> dict`. Tests pass a function that serves the two captured fixture files, or raises for error cases. A pytest `autouse` fixture blocks real `requests.get`.

| Test file | Tests |
|---|---|
| test_scheduler | ready = candles − buffer; checklist is last block ending at ready; `last` anchor directly before checklist; no two active blocks overlap; passive overlaps active; no task starts before its deps end; `end_by` anchor respected; week 1 (ready 16:01) fits with **slack 6**; week 2 with learned durations (ready 15:55) is **over by 20** → suggests cleaning (40) on Thursday 20:00-20:40, and Friday then fits with slack 5; turn-7 state (cleaning moved + dinner skipped) fits; turn-8 re-plan (13:00, 40 behind) drops flowers only, keeps all must tasks, and matches the section 4 block times; must-only infeasible → reported, never dropped; cycle → ToolError; determinism (same input → same output) |
| test_hebcal | parses 16:11 / 16:05 from fixtures; no `b` parameter sent; geonameid 281184 in URL; cache hit avoids a second fetch; HTTP error → ToolError; unsupported city → ToolError |
| test_memory_files | profile/tasks/checklist/learned round-trip; learned mean of last 3; week override written only to its own file; templates byte-identical after override; expired week not loaded |
| test_tools_impl | every tool returns ≤ N lines / chars; `list_tasks` one line per task; errors raise ToolError; `tool_log.jsonl` written |
| test_tools_impl (remove) | `remove_task` deletes the template and strips it from other tasks' `depends_on`; unknown name raises ToolError; `remove_checklist_item` deletes a case-insensitive match; no match raises ToolError; **a week-only `skip_task`/`skip_tags` never removes or changes any template** (tasks.json byte-identical before and after, and the next week's timeline still contains the task) |
| test_api_loop | single end_turn; tool_use → dispatch → one user message with ALL tool_results in order; tool error → `is_error: true` and loop continues; max-calls guard; refusal stops turn; request has `output_config.effort == "medium"` and model id |
| test_api_history | file only grows (byte prefix unchanged after every append); reload after new agent equals previous active list; compaction marker → active list starts with summary |
| test_api_compaction | below threshold no call; above threshold one summary call with no tools; new active list contains no old thinking blocks; history.jsonl still has old lines |
| test_api_caching | ≤ 4 breakpoints; system breakpoint present; system text bytes identical across all requests in a run; stored history objects carry no cache_control (copy only) |
| test_api_tokens | cost formula per component; per-turn sum of several calls; cache % |
| test_api_context | context contains city, buffer, week overrides, summary line; does NOT contain task lines or history |
| test_api_e2e | 8 scripted turns with FakeClient + FakeHebcal → results file has every required field, checks run, LOC dict has 8 keys |
| test_sdk_options | `build_options()` → model, effort, strict_mcp_config True, `tools == []`, allowed list = 10 mcp names, system prompt == shared; wrappers call shared functions (await handler directly) and map ToolError to `is_error`; API-key warning |
| test_checks | each of 7 checks: pass on a good fixture state, fail on a crafted bad state |

---

## 8. `scenario.json` and the runners

```json
{
  "city": "Jerusalem",
  "sessions": [
    {"session": 1, "turns": [
      {"turn": 1, "now": "2026-10-29T09:00", "friday": "2026-10-30",
       "text": "I live in Jerusalem, I want to be ready 10 minutes before candle lighting. On Fridays I start at 12:00. Thursday night I'm free 20:00-00:00."},
      {"turn": 2, "now": "2026-10-29T09:05", "friday": "2026-10-30",
       "text": "Add my tasks. 1) shopping: 50 min, active, must, no dependencies, not heavy, cannot move to Thursday. 2) challah: 60 min, active, must, no dependencies, not heavy, cannot move to Thursday. 3) challah_rise: 45 min, passive, must, depends on challah, cannot move to Thursday. 4) challah_bake: 30 min, passive, must, depends on challah_rise, cannot move to Thursday. 5) cooking: 15 min, active, must, depends on shopping, tag dinner, not heavy, can move to Thursday. 6) slow_cook: 150 min, passive, must, depends on cooking, tag dinner, cannot move to Thursday. 7) cleaning: 50 min, active, must, no dependencies, heavy, can move to Thursday. 8) set_table: 15 min, active, must, no dependencies, must end by 15:00, cannot move to Thursday. 9) flowers: 15 min, active, nice-to-have, no dependencies, cannot move to Thursday. 10) shower: 20 min, active, must, no dependencies, always the last task before the checklist, cannot move to Thursday."},
      {"turn": 3, "now": "2026-10-29T09:10", "friday": "2026-10-30",
       "text": "My last-minute checklist: turn off the phone 2 min, set the alarm 2 min, turn on the hot plate 3 min, set the light timers 3 min."},
      {"turn": 4, "now": "2026-10-29T09:15", "friday": "2026-10-30", "text": "Build my Friday plan."},
      {"turn": 5, "now": "2026-10-31T19:00", "friday": "2026-10-30",
       "text": "Weekly review: challah took 90 minutes, cleaning only 40."}
    ]},
    {"session": 2, "turns": [
      {"turn": 6, "now": "2026-11-05T09:00", "friday": "2026-11-06", "text": "Plan this Friday."},
      {"turn": 7, "now": "2026-11-05T09:10", "friday": "2026-11-06",
       "text": "Yes, move cleaning to Thursday night. And this week we're guests Friday night, no dinner cooking."},
      {"turn": 8, "now": "2026-11-06T13:00", "friday": "2026-11-06",
       "text": "It's 13:00 and I'm 40 minutes behind."}
    ]}
  ],
  "checks": [1, 2, 3, 4, 5, 6, 7]
}
```
`now` goes only into the per-turn context block, never into the system prompt.

**`main.py` flow (both versions):**
1. Parse `--scenario --out` (plus `--data-dir`, `--compact-threshold`). Auth check.
2. `reset_state(data_dir)`: delete and recreate `data/<api|sdk>/` (memory, history, caches, logs, workspace). SDK also deletes `session.json`. Each run starts clean.
3. For each session, build a new agent. For each turn, set the tool context (turn number, now), run the turn, time it, collect the metrics.
4. Run `shared.checks` on the saved state + `tool_log.jsonl`:
   1. profile city == Jerusalem and buffer == 10.
   2. checklist has 4 items and the last block of `last_timeline.json` is the checklist.
   3. `learned.challah.minutes == 90`.
   4. session-2 timeline uses 90 for challah (recorded per build in the timeline JSON).
   5. `weeks/` has exactly one file with overrides, `2026-11-06.json`. It holds both a `move_to_thursday` override that includes cleaning and a dinner skip (`skip_tags [dinner]`, or `skip_task` entries that cover cooking and slow_cook). In `tasks.json`, cleaning, cooking and slow_cook still exist with their template minutes (50, 15, 150) unchanged.
   6. `validate_timeline(final)`: ends ≤ candles − 10, no overlapping active blocks, deps respected.
   7. `get_shabbat_times` appears in the tool log.
5. Write `results/<api|sdk>.json`:
```json
{"version": "api", "model": "claude-opus-5-5", "effort": "medium",
 "libraries": {"anthropic": "2.1.0"}, "run_started": "...",
 "turns": [{"turn": 4, "session": 1, "tokens": {"input": 0, "cache_read": 0, "cache_write": 0, "output": 0},
            "model_calls": 3, "tools": ["get_shabbat_times", "build_timeline"],
            "cost_usd": 0.0, "seconds": 0.0, "compacted": false, "reply_preview": "..."}],
 "totals": {"tokens": {...}, "cache_read_pct": 0.0, "model_calls": 0, "cost_usd": 0.0, "seconds": 0.0},
 "checks": [{"id": 1, "name": "profile saved", "passed": true, "detail": "Jerusalem/10"}],
 "checks_passed": "7/7",
 "loc": {"loop.py": 0, "tools.py": 0, "history.py": 0, "memory.py": 0, "context.py": 0,
         "compaction.py": 0, "caching.py": 0, "tokens.py": 0},
 "sdk_extra": {"reported_total_cost_usd": 0.0, "model_usage": {}}}
```
`cache_read_pct = cache_read / (input + cache_read + cache_write)`.

---

## 9. Implementation phases

Each phase ends with offline tests green and a list of suggested commits for you. I never commit.

| # | Phase | What to check after it |
|---|---|---|
| 0 | Skeleton: folders, `.gitignore`, `requirements.txt` with exact pins, venv, copy PLAN.md. Confirm the `anthropic` 2.x client surface (`messages.create`, `output_config`, `count_tokens`) against its changelog, since the bundled docs describe 1.x | `pip install -r requirements.txt`; `pytest` runs (0 tests); `git status` shows no secrets or data |
| 1 | `shared/store.py`, `domain.py`, `memory_files.py`, `config.py` | test_memory_files green; inspect a written JSON by hand |
| 2 | `shared/scheduler.py` (largest logic) | test_scheduler green, incl. the worked numbers; read 3 printed timelines |
| 3 | `shared/hebcal.py` + fixtures | test_hebcal green; optional one live smoke call prints 16:11 |
| 4 | `tool_specs.py`, `tools_impl.py`, `prompts.py`, `context_line.py` | test_tools_impl green; eyeball compact outputs |
| 5 | `scenario.json`, `scenario.py`, `checks.py`, `results.py` (LOC counter) | test_checks green |
| 6 | API harness A: `tokens.py`, `caching.py`, `history.py`, `tools.py`, `loop.py` + `fakes.py` | test_api_loop/history/caching/tokens green; every print has a file tag |
| 7 | API harness B: `memory.py`, `context.py`, `compaction.py`, `API/agent.py`, `API/main.py` | test_api_e2e green; full offline run with fake client writes a results file |
| 8 | **First paid API run** (est. ≈ $1, my estimate). Verify `count_tokens` ≥ 512 for the prefix, `cache_read_input_tokens` > 0 from the 2nd call, no preserved-thinking 400s; calibrate compaction threshold; rerun from clean | `results/api.json` with 7/7 or a clear failing check to fix |
| 9 | SDK: `SDK/tools.py`, `SDK/agent.py`, `SDK/main.py` + test_sdk_options; then a paid run on the Claude Code login (key unset) | `results/sdk.json`; confirm the hook context arrives, CLAUDE.md loaded, usage semantics |
| 10 | Final clean runs of both, `REPORT.md` (tables from the results files, analysis) | REPORT has every required section; numbers match JSON |

**Suggested commit pattern** (you run them): one commit per phase, for example `chore: project skeleton, pinned deps, gitignore`, `feat(shared): deterministic scheduler with tests`, `feat(api): agent loop, tools dispatch, history`. The exact files per commit are listed at the end of each phase.

---

## 10. Risks

1. **Model data entry.** Checks depend on the model saving exactly the stated numbers in turn 2. Mitigation: turn-2 text spells out every field; tools validate strictly; `list_tasks` lets the model self-check.
2. **Preserved thinking (400 on edited history).** Mitigation: append-only history, content stored and replayed byte-for-byte, full-summary compaction only. Open point: whether moving the rolling `cache_control` marker off an earlier block counts as an edit. A 3-request live test in Phase 8 settles it. Fallback: top-level automatic `cache_control` or a static breakpoint only.
3. **Serializing thinking blocks to JSONL** must round-trip exactly (`signature`, empty `thinking`). A test round-trips a scripted thinking block; Phase 8 confirms live.
4. **Compaction may not trigger** with a small conversation. The threshold is calibrated from real numbers in Phase 8 and is a CLI flag.
5. **SDK usage semantics** (`usage` per query vs cumulative; extra helper-model calls; tokens Claude Code adds, such as CLAUDE.md and reminders) are only observable live. Handled by delta logic and reported as findings.
6. **SDK hooks in Python.** If `UserPromptSubmit` `additionalContext` does not behave as documented, fall back to prepending the context to the query text, still marked `# replaces: context.py`.
7. **Hebcal availability.** Live calls are cached per run. Tests never hit the network.
8. **Comparison fairness.** Same model, effort, system prompt text, tool descriptions and scenario. The SDK adds its own context, which is the point of the comparison.
9. **`anthropic` 2.x** is newer than the bundled docs (1.x). Phase 0 checks the client surface before writing harness code.

## Verification (end to end)
- `pytest -q` passes with `ANTHROPIC_API_KEY` unset and the network blocked.
- `python API/main.py --scenario scenario.json --out results/api.json` prints file-tagged logs and writes 7/7 checks.
- `python SDK/main.py --scenario scenario.json --out results/sdk.json` (key unset) writes 7/7 checks.
- Re-running either command reproduces the same saved state (clean reset, fixed dates).

---

## 11. Decisions (confirmed 2026-10-22) and remaining open points

Decisions:
1. Friday starts at 12:00. Thursday night is 20:00-00:00. Turn 1 states both. Section 4 numbers are tuned for them.
2. API session 2: a new agent instance reloads both memory files and the saved conversation.
3. SDK built-in tools: none (`tools=[]`).
4. Exactly 8 turns. Turn 7 carries both the approval and the guests change. Check 5 expects both overrides in the 2026-11-06 week file, with templates unchanged.
5. Server-side refusal fallbacks stay off.
6. Git: the user runs `git init`, commits and pushes. I only create or switch branches. Every phase ends with a list of suggested commits, messages and files.
7. Added `remove_task` and `remove_checklist_item` for permanent deletes. They are separate from the week-only `set_week_override`, and the agent asks when a request is ambiguous.

8. Checklist keeps 4 items, 10 minutes in total: turn off the phone 2, set the alarm 2, turn on the hot plate 3, set the light timers 3.
9. Turn 2 text in `scenario.json` spells out every field of the section 4 table.
