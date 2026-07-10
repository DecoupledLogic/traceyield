# Research: How OpenAI records usage data (for a Codex/ChatGPT report)

*Researched 2026-07-10. All external facts are sourced inline; treat pricing and
JSONL field names as verify-before-ship (OpenAI rev the rollout format across
CLI versions — see "Format drift" below).*

## TL;DR — what maps and what doesn't

This tool works because Claude Code writes a **local, per-turn, timestamped
transcript with token usage on every assistant message**. To reproduce the same
report for OpenAI users we need an equivalent source. Ranked by fidelity:

| Source | Local? | Per-turn tokens? | Per-tool? | Cost derivable? | Verdict |
|---|---|---|---|---|---|
| **Codex CLI rollout logs** (`~/.codex/sessions`) | ✅ | ✅ (cumulative + last) | ✅ (function calls) | ✅ (rates × tokens) | **Primary target — near 1:1 with Claude Code** |
| OpenAI Usage/Costs API (org-level) | ❌ (API) | ❌ (daily aggregates) | ❌ | ✅ (returned directly) | Supplement for org daily cost; no session/tool drill-down |
| ChatGPT data export (`conversations.json`) | ✅ (manual) | ❌ (no token counts) | ❌ (no tools) | ❌ | Degraded — turns/models only, never cost |

**Bottom line:** target the **Codex CLI**. It is the structural twin of Claude
Code and is the only source that carries everything `report.py` needs (per-day
buckets, per-session cost, per-tool attribution, token composition). ChatGPT
web usage cannot be costed and should be treated as a best-effort add-on, not a
peer.

---

## 1. Codex CLI — the primary source

OpenAI's Codex CLI is the direct analog to Claude Code: an agentic coding CLI
that persists every session to disk automatically, no flag required.

### 1.1 Where the logs live

```
$CODEX_HOME/sessions/YYYY/MM/DD/rollout-<timestamp>-<uuid>.jsonl
$CODEX_HOME/archived_sessions/…                 # same format, moved on archive
```

- `CODEX_HOME` defaults to `~/.codex` (override via env var). ([Inventive HQ](https://inventivehq.com/knowledge-base/openai/where-configuration-files-are-stored), [ccusage](https://ccusage.com/guide/codex/))
- Files are **date-partitioned** and named `rollout-YYYY-MM-DDTHH-MM-SS-<uuid>.jsonl`. ([openai/codex #24948](https://github.com/openai/codex/issues/24948))
- Saving is always-on; closing the terminal does not delete the file. Archiving is a **move**, not a transform — archived files keep the full transcript. ([Verdent](https://www.verdent.ai/guides/codex-cli-resume-continue-save-chat), [Daniel Vaughan KB](https://codex.danielvaughan.com/2026/06/02/codex-cli-session-archiving-lifecycle-management-v0136/))

This is exactly the shape our parser already assumes: glob `**/*.jsonl`, bucket
by the per-line timestamp. The only change is the root (`~/.codex/sessions`
instead of `~/.claude/projects`) and the line schema below.

### 1.2 Line schema — `RolloutLine` wrapping a `RolloutItem`

Every line is one JSON object: a UTC timestamp plus a typed payload. ([DeepWiki: Rollout Persistence](https://deepwiki.com/openai/codex/3.5.2-rollout-persistence-and-replay), [DEV: Reverse-engineering rollout traces](https://dev.to/milkoor/reverse-engineering-codex-cli-rollout-traces-3b9b))

```json
{ "timestamp": "2026-07-09T14:03:11.482Z", "type": "<item type>", "payload": { … } }
```

`type` (the `RolloutItem` variant) is one of:

| `type` | Purpose | What we pull from it |
|---|---|---|
| `session_meta` | Session-level metadata | `id`, `cwd`, `model_provider`, `cli_version`, `source` → session id + project |
| `turn_context` | Snapshot of turn settings | **`model`**, approval/sandbox policy, `cwd` → active model tier |
| `response_item` | Raw model responses & tool calls | assistant/user messages, `function_call`, `function_call_output` |
| `event_msg` | Protocol events | **`token_count`** (usage), `user_message`, `agent_message` |
| `compacted` | History-compaction summary | (skip for metrics; inflates token totals — see gotchas) |
| `inter_agent_communication`, `world_state` | Multi-agent / world snapshots | ignore for now |

Sources for the variant list: [DeepWiki](https://deepwiki.com/openai/codex/3.5.2-rollout-persistence-and-replay), [PR #3380 "Introduce rollout items"](https://github.com/openai/codex/pull/3380).

### 1.3 The records we actually parse

**Session metadata** (first line of the file):

```json
{ "timestamp": "…", "type": "session_meta",
  "payload": { "id": "c0ffee…-uuid", "cwd": "/home/u/proj",
               "model_provider": "openai", "cli_version": "0.44.1", "source": "cli" } }
```

**Turn context** — where the model name lives (NOT in `session_meta`):

```json
{ "timestamp": "…", "type": "turn_context",
  "payload": { "model": "gpt-5-codex", "approval_policy": "on-request",
               "sandbox_policy": "workspace-write", "cwd": "/home/u/proj" } }
```

**Token usage** — `event_msg` with `payload.type === "token_count"`. Reports
**cumulative** totals plus the last turn's delta. ([ccusage](https://ccusage.com/guide/codex/), [openai/codex #9660](https://github.com/openai/codex/issues/9660))

```json
{ "timestamp": "…", "type": "event_msg",
  "payload": { "type": "token_count",
    "info": {
      "total_token_usage": { "input_tokens": 48210, "cached_input_tokens": 44032,
                             "output_tokens": 1875, "reasoning_output_tokens": 1216,
                             "total_tokens": 50085 },
      "last_token_usage":  { "input_tokens": 6142,  "cached_input_tokens": 5888,
                             "output_tokens": 402,  "reasoning_output_tokens": 256,
                             "total_tokens": 6544 } } } }
```

The five `TokenUsage` fields are defined in `codex-rs/protocol/src/protocol.rs`:
`input_tokens`, `cached_input_tokens`, `output_tokens`, `reasoning_output_tokens`,
`total_tokens`. ([search: TokenUsage struct](https://github.com/openai/codex/blob/main/codex-rs/protocol/src/protocol.rs))

Critical semantics:
- **`input_tokens` is inclusive of cached.** Fresh (full-price) input =
  `input_tokens − cached_input_tokens`. `cached_input_tokens` is billed at the
  cache-read discount.
- **`reasoning_output_tokens` is a subset of `output_tokens`**, already billed
  in output. Bill `output_tokens` once at the output rate; do **not** add
  reasoning on top.
- Use `last_token_usage` for per-turn attribution, or diff successive
  `total_token_usage` values (ccusage subtracts the previous cumulative). Prefer
  `last_token_usage` when present; fall back to cumulative-diff for old files.

**Assistant / user message**:

```json
{ "timestamp": "…", "type": "response_item",
  "payload": { "type": "message", "role": "assistant",
               "content": [ { "type": "output_text", "text": "Done." } ] } }
```
(User turns use `"role": "user"` and `"type": "input_text"`.)

**Tool call + result** — Codex calls tools as OpenAI "function calls":

```json
{ "timestamp": "…", "type": "response_item",
  "payload": { "type": "function_call", "name": "shell",
               "arguments": "{\"command\":[\"ls\"]}", "call_id": "call_abc" } }
{ "timestamp": "…", "type": "response_item",
  "payload": { "type": "function_call_output", "call_id": "call_abc",
               "output": "…stdout/stderr or {content, success}…" } }
```

Codex tool names differ from Claude Code's: `shell` / `exec_command`,
`apply_patch`, `read_file`, `update_plan`, plus MCP tools. There is also a
dedicated `local_shell_call` item type in some versions. The `call_id` links a
call to its output (our parser already does this id→name join for Claude).

### 1.4 Field mapping → `report.py`'s `new_day()` / `new_session()` shape

| Our field | Codex source |
|---|---|
| activity date | `RolloutLine.timestamp[:10]` |
| session id (`sids`, session key) | `session_meta.payload.id` (or the filename UUID) |
| project (`by_project`) | `session_meta.payload.cwd` — a real path; derive a label (basename, or sanitize like Claude's path-encoding) |
| model tier (`by_model`) | `turn_context.payload.model` → tier map (see §1.5) |
| `tok.input` (fresh) | `last.input_tokens − last.cached_input_tokens` |
| `tok.cache_read` | `last.cached_input_tokens` |
| `tok.output` | `last.output_tokens` |
| `tok.cache_write_5m` / `_1h` | **0** — OpenAI has no billed cache-write tier (caching is automatic/free to write) |
| `msgs` (assistant turns) | count `response_item` messages with `role=="assistant"` (or `token_count` events) |
| `tool_results` | count `function_call_output` items |
| `tool_errors` | `function_call_output` whose output signals failure (heuristic; see gotchas) |
| `by_tool[name]` | `function_call.payload.name` |
| `errors` (taxonomy) | needs a **Codex-specific** rule set (our `ERROR_RULES` strings are Claude-harness-specific) |

The important structural point: **Codex spreads what Claude packs into one
message across multiple lines.** Claude Code puts `usage` on the assistant
message itself; Codex emits a separate `token_count` event near (but not
deterministically adjacent to) the turn. So per-tool cost attribution must pair
each turn's `last_token_usage` with the `function_call` in that turn, rather
than reading usage straight off the tool-call object.

### 1.5 Cost model — simpler than Anthropic's

Anthropic bills five line-items (fresh input, cache write-5m ×1.25, write-1h ×2,
cache read ×0.1, output). **OpenAI collapses to three:**

- **fresh input** — `(input_tokens − cached_input_tokens)` × input rate
- **cached input read** — `cached_input_tokens` × input rate × **0.1**
- **output** — `output_tokens` × output rate

There is **no cache-write premium** — prompt caching on OpenAI is automatic and
free to populate; you only save on reads. The `0.1×` cache-read multiplier
matches Anthropic's (confirmed by the published cached-input rates being exactly
1/10 of input: gpt-5.5 $5.00 → cached $0.50; gpt-5.3-codex $1.75 → $0.175).
([OpenAI API pricing](https://developers.openai.com/api/docs/pricing))

### 1.6 Model tiers & pricing (per 1M tokens)

Codex defaults to the `gpt-5*-codex` family; users can switch models mid-session
(each `turn_context` records the model in force). Rates found 2026-07-10:

| Model | Input | Cached input | Output | Source |
|---|---|---|---|---|
| `gpt-5-codex` | $1.25 | $0.125 | $10.00 | [pricepertoken](https://pricepertoken.com/pricing-page/model/openai-gpt-5-codex), search |
| `gpt-5.3-codex` | $1.75 | $0.175 | $14.00 | [OpenAI pricing](https://developers.openai.com/api/docs/pricing) |
| `gpt-5.5` | $5.00 | $0.50 | $30.00 | [OpenAI pricing](https://developers.openai.com/api/docs/pricing), [Intro GPT-5.5](https://openai.com/index/introducing-gpt-5-5/) |
| `gpt-5.5-pro` | $30.00 | — | $180.00 | [OpenAI pricing](https://developers.openai.com/api/docs/pricing) |

Notes:
- OpenAI **has no clean pricing API** (same problem as Anthropic — the Models API
  returns capabilities, not rates). The two live sources are the [OpenAI pricing
  page](https://developers.openai.com/api/docs/pricing) (scrape, mirroring our
  `check_pricing_drift()`) and **LiteLLM's community pricing dataset**, which is
  what ccusage uses (`model_prices_and_context_window.json`). ([ccusage](https://ccusage.com/guide/codex/))
- The model line-up churns fast (gpt-5.5, 5.6-sol/terra/luna already appear on
  the page). A tier map should degrade gracefully: unknown model → skip the row
  (as our `tier()` already does by returning `None`).

### 1.7 Format drift & gotchas (read before implementing)

- **Multiple format versions.** Codex has shipped at least three session-metadata
  layouts — "new (≥0.44)", "mid", and "oldest (2025/08)". A robust parser must
  tolerate all three. ([codex-trace](https://github.com/PixelPaw-Labs/codex-trace)) In particular older `token_count`
  events were **flat** (`payload.input_tokens`) before the `payload.info.{last,total}_token_usage`
  nesting existed — handle both.
- **`codex exec` (non-interactive) historically omitted `token_count` events**
  from the session file (issue [#9660](https://github.com/openai/codex/issues/9660)). Sessions run head-less may have
  messages/tools but no usage → cost 0. Detect and flag rather than silently
  under-report.
- **Compaction inflates raw token totals.** Long sessions rewrite history via
  `compacted` items; naive summing double-counts. Use `last_token_usage` deltas
  (or dedupe on compaction) rather than trusting the largest cumulative. ([openai/codex #24948](https://github.com/openai/codex/issues/24948), [#27131](https://github.com/openai/codex/issues/27131))
- **Tool-error detection is fuzzier than Claude's `is_error` flag.** Codex
  `function_call_output` may carry a structured `{content, success}` or just raw
  text; there is no universal boolean. Expect a heuristic (non-zero exit code in
  shell output, `"success": false`, error-looking stderr) and a Codex-specific
  `ERROR_RULES` table.
- **World-readable files.** Rollout files were created mode `0644` on Unix
  (issue [#21660](https://github.com/openai/codex/issues/21660)) — irrelevant to parsing, but note it if we ever copy them.
- **Session id vs filename.** The internal `session_id` and the filename UUID are
  auto-generated together; either can key a session, but prefer `session_meta.id`
  for stability. ([Discussion #3827](https://github.com/openai/codex/discussions/3827))

### 1.8 Prior art to lean on

- **ccusage** already parses Codex logs end-to-end (token deltas, per-model
  breakdown, LiteLLM pricing, cached-token handling, `isFallback` for legacy
  gpt-5 pricing). Best reference implementation for the delta math and pricing.
  ([ccusage Codex guide](https://ccusage.com/guide/codex/))
- **codex-trace** — a viewer that decodes all three format versions; useful for
  seeing real field layouts across versions. ([repo](https://github.com/PixelPaw-Labs/codex-trace))

---

## 2. OpenAI Usage & Costs API — org-level supplement

For **API-key** (not CLI) OpenAI usage, the platform exposes:

- The dashboard at `platform.openai.com/usage`, and the programmatic
  **Usage API** (`/v1/organization/usage/*`) and **Costs API**
  (`/v1/organization/costs`), which return **token counts and USD grouped by
  day, model, and project** — but **not** per-session or per-tool.

Fit: this can populate the `daily_metrics.json` top line (daily cost, tokens,
by-model) with authoritative billed dollars, but it **cannot** feed the session
table, per-tool attribution, error taxonomy, or token-composition panels. Treat
it as an optional cross-check / a path for teams who use the API directly rather
than the CLI. Requires an admin API key; it's a network pull, not a local file,
so it breaks the "no-dependency, offline, local-only" property of the current
tool.

---

## 3. ChatGPT web app — degraded, cost-blind

For people using **ChatGPT** (the consumer/Team web app) rather than Codex:

- The only self-serve data source is **Settings → Data controls → Export**,
  which emails a ZIP containing **`conversations.json`** (full history as a
  parent/child **message tree**) plus `chat.html`. ([OpenAI community](https://community.openai.com/t/decoding-exported-data-by-parsing-conversations-json-and-or-chat-html/403144), [export guide](https://www.ai-toolbox.co/ai-toolbox-chatgpt-features/export-chatgpt-to-json-complete-guide))
- Each message node has `create_time`, `author.role`, and a **`model_slug`**, so
  we can reconstruct **turns, dates, and model mix**.
- **It contains no token counts and no cost**, and there are **no tool-call
  records**. ChatGPT subscriptions are flat-fee, so there is no per-message
  billing to recover.

Fit: at best this fills `msgs`, `sessions` (one per conversation), `by_model`
(counts, not cost), and dates. Every dollar figure, the token-composition panel,
the per-tool table, the routing estimator, and the error taxonomy would be
blank. It is also a **manual, on-demand export** (a tree to flatten), not a
continuously-updated log — so it can't drive a daily scheduled run the way Codex
logs can. Recommend supporting it only as an explicitly-labeled "counts only"
mode, if at all.

---

## Sources

- [ccusage — Codex CLI usage analysis](https://ccusage.com/guide/codex/)
- [DeepWiki: openai/codex — Rollout Persistence and Replay](https://deepwiki.com/openai/codex/3.5.2-rollout-persistence-and-replay)
- [DEV: Reverse engineering Codex CLI rollout traces](https://dev.to/milkoor/reverse-engineering-codex-cli-rollout-traces-3b9b)
- [openai/codex Discussion #3827 — Session/Rollout Files](https://github.com/openai/codex/discussions/3827)
- [openai/codex #9660 — token_count events in non-interactive files](https://github.com/openai/codex/issues/9660)
- [openai/codex #24948 — session logs grow from compaction](https://github.com/openai/codex/issues/24948)
- [openai/codex #27131 — self-ingesting session logs / token growth](https://github.com/openai/codex/issues/27131)
- [openai/codex #21660 — rollout files world-readable](https://github.com/openai/codex/issues/21660)
- [openai/codex PR #3380 — Introduce rollout items](https://github.com/openai/codex/pull/3380)
- [PixelPaw-Labs/codex-trace — session log viewer](https://github.com/PixelPaw-Labs/codex-trace)
- [Inventive HQ — where Codex config is stored](https://inventivehq.com/knowledge-base/openai/where-configuration-files-are-stored)
- [OpenAI API pricing](https://developers.openai.com/api/docs/pricing) · [Introducing GPT-5.5](https://openai.com/index/introducing-gpt-5-5/) · [pricepertoken: gpt-5-codex](https://pricepertoken.com/pricing-page/model/openai-gpt-5-codex)
- [Decoding ChatGPT's exported conversations.json](https://community.openai.com/t/decoding-exported-data-by-parsing-conversations-json-and-or-chat-html/403144)
