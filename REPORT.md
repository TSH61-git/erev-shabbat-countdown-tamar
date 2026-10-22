# Report: Messages API vs Claude Agent SDK

The same agent was built twice. Both versions use `claude-opus-5-5` at effort `medium`, the same system prompt, the same 10 tools and the same 8-turn scenario. Every run started from wiped data. The final runs were made on 2026-10-23.

## 1. Results

| | API | SDK |
|---|---|---|
| Checks passed | **7/7** | **7/7** |
| Model calls | 21 | 19 |
| Input / cache read / cache write / output tokens | 3,336 / 110,991 / 11,885 / 5,190 | 38 / 165,692 / 8,355 / 4,718 |
| Prompt tokens per call | 6,010 | 9,162 |
| Cache read % | 87.9% | 95.2% |
| Cost | $0.199 | $0.195 (≈ $0.234 from a cold cache) |
| Time | 68 s | 85 s |
| Compaction | Once, at turn 6 | Never triggered |

Both costs use one price table. The SDK figure matches the SDK's own `total_cost_usd` exactly. The SDK run started 15 minutes after an earlier one and found its 1-hour cache still warm: turn 1 wrote 380 tokens instead of about 5,370. A cold start would add about $0.04.

## 2. Lines of code per harness part

| Part | API | SDK | SDK replacement |
|---|---|---|---|
| loop | 39 | 5 | `ClaudeSDKClient`, `query()`, `receive_response()` |
| tools | 27 | 11 | `@tool` + `create_sdk_mcp_server` |
| history | 48 | 2 | `resume=session_id` |
| memory | 33 | 3 | CLAUDE.md + `setting_sources=["project"]` |
| context | 12 | 4 | `UserPromptSubmit` hook |
| compaction | 48 | 0 | automatic |
| caching | 20 | 0 | automatic |
| tokens | 46 | 9 | `ResultMessage.usage` / `model_usage` |
| **Total** | **273** | **34** | |

## 3. Where the SDK used more tokens and where fewer

- **More: about 52% more prompt tokens per call.** Claude Code adds its own context to every request: working folder, OS, model name, today's date, the account's email address, and a token-budget reminder on every call. It also never compacted in 8 turns, so its conversation kept growing. The API version compacted at turn 6 and shrank its later prompts.
- **Fewer: uncached input and model calls.** The API's compaction cost it one extra summary call, and the following request missed the cache (3,288 uncached tokens on turn 6). The SDK had almost no uncached input, made 2 fewer calls, and produced slightly fewer output tokens.
- **Net cost: about equal.** The SDK's higher cache hit rate is offset by its 1-hour cache writes, which cost $8 per million tokens against $5. From a cold start the SDK is about 18% more expensive. This is one run each, so a few cents of difference is noise.

## 4. Control I had in the API and lost in the SDK

- **The exact prompt.** Claude Code inserted the real date, which conflicted with the scenario's fixed dates, so I had to add a rule to the system prompt. It also put the account's email address into the context, which this agent never needs.
- **Caching.** I can't choose where the breakpoints go or how long entries live. Claude Code always writes 1-hour entries.
- **Compaction.** I can't choose the threshold or the summary. It only happens near the context limit, so it couldn't be demonstrated here.
- **History.** The transcript lives under `~/.claude/projects/`, and I reach it only through `resume`.
- **Accounting.** The streamed per-message usage is a snapshot with output cut short, and the cost totals are running totals that continue across `resume`. My first SDK cost was wrong in both directions until I read `ResultMessage.usage` instead.
- **Which models run.** Claude Code called `claude-haiku-5-5` to write a session title. That cost $0.0002, but I never asked for it.

What I gained: 34 lines instead of 273, sessions and resume for free, automatic compaction, and hooks.

## 5. My choice: the API version

This agent has a small, fixed set of my own tools, and its real logic is a deterministic Python scheduler. It needs exact dates, measurable cost per turn, and a context that holds only what I put in. The API gives all three; the SDK made each one harder. The 239 extra lines are simple, and the 136 offline tests cover them. I would pick the SDK for an agent that needs built-in tools (files, shell, web), subagents or long open-ended sessions, or for a quick first prototype.

## Method and limits

- One run of each version.
- Checks run on saved files, with the expected values in `scenario.json`.
- SDK model calls are counted as distinct message ids. That gives 19, matching the Claude Code transcript. The SDK's `num_turns` adds up to 21 and counts something else.
- Prices are list prices checked on 2026-10-22.
- Logs: `results/api_run.log` and `results/sdk_run.log`.
