# Research: How Claude Code records usage data (for a usage/health report)

*Researched 2026-07-09. All external facts are sourced inline; treat pricing and
JSONL field names as verify-before-ship — the transcript format is an internal
detail of Claude Code and shifts between CLI releases (see "Format drift"
below).*

## TL;DR — what maps and what doesn't

We want a **per-day, per-session, per-tool cost-and-health report** of our own
Claude Code usage, built from data we already have on disk, with no server and
no dependency on a billing export. The question is which source carries enough
to do that. Ranked by fidelity:

| Source | Local? | Per-turn tokens? | Per-tool? | Per-session? | Cost derivable? | Verdict |
|---|---|---|---|---|---|---|
| **Claude Code transcripts** (`~/.claude/projects/**/*.jsonl`) | ✅ | ✅ (`usage` on every assistant message) | ✅ (`tool_use`/`tool_result` blocks) | ✅ (`sessionId`) | ✅ (rates × tokens) | **Primary target — everything the report needs is here** |
| Usage & Cost Admin API (org-level) | ❌ (API) | ❌ (time-bucketed aggregates) | ❌ | ❌ | ✅ (returned directly) | Supplement for authoritative billed dollars; no session/tool drill-down |
| Claude Code Analytics API (per-user) | ❌ (API) | ❌ (daily rollups) | ❌ | ❌ | ✅ (estimated) | Org/admin view of *estimated* per-user cost; not turn- or tool-level |
| Console Usage/Cost pages | ❌ (UI) | ❌ | ❌ | ❌ | ✅ (shown) | Human dashboard; not a programmatic feed |

**Bottom line:** build on the **local transcripts**. They are the only source
that is (a) already on disk, (b) offline, and (c) carries the full grain we need
— per-turn token composition, per-tool attribution, per-session totals, and the
tool-result errors that let us score *health*, not just spend. The Admin/Analytics
APIs are org-level aggregates: great for reconciling total dollars, useless for
"which conversation ran away" or "which tool wastes the most turns." Treat them
as an optional cross-check, not the engine.

---

## 1. Claude Code transcripts — the primary source

Claude Code persists **every session to disk automatically**, no flag required.
Each conversation is a JSONL file that grows one line per event. This is the
same local record other community tools (ccusage, claude-code-log) parse.
([ccusage](https://ccusage.com/guide/cost-modes), [claude-code-log](https://github.com/daaain/claude-code-log))

### 1.1 Where the logs live

```
~/.claude/projects/<encoded-project-path>/<sessionId>.jsonl
```

- Root is `~/.claude/projects`. Each **project** (a working directory you ran
  Claude Code in) becomes one subfolder; each **session** becomes one `.jsonl`
  file named by its `sessionId`. ([claude-dev.tools: JSONL format](https://claude-dev.tools/docs/jsonl-format), [ClaudeWorld: Session Storage](https://claude-world.com/tutorials/s16-session-storage/))
- The subfolder name is the project's **absolute path with the separators
  replaced by `-`** — e.g. `C--Users-you-source-repos-myapp`. This means the
  directory name *is* the project identity (and encodes the full machine path),
  which we can decode back to a readable label. ([databunny: Session File Format](https://databunny.medium.com/inside-claude-code-the-session-file-format-and-how-to-inspect-it-b9998e66d56b))
- Writing is **always-on and append-only**; closing the terminal doesn't delete
  the file. ([Manage sessions — Claude Code Docs](https://code.claude.com/docs/en/sessions))

Consequence for us: the parse strategy is dead simple — **glob `**/*.jsonl`
under the root, read each line, bucket by the line's own timestamp.** Because
every line is independently timestamped, a *single* run reconstructs the entire
history we still have on disk, not just "today."

### 1.2 Line schema — one JSON object per line

Every line is a self-contained JSON event. Top-level fields that matter to us:
([claude-dev.tools: JSONL format](https://claude-dev.tools/docs/jsonl-format), [simonw/claude-code-transcripts](https://github.com/simonw/claude-code-transcripts))

| Field | Purpose | What we pull from it |
|---|---|---|
| `timestamp` | UTC ISO-8601 time of the event | **activity date** (`timestamp[:10]`) — the bucket key |
| `sessionId` | the conversation this line belongs to | session key for per-session totals |
| `type` | `user` / `assistant` / `system` / summary | which lines carry billable usage |
| `message` | the model message object (below) | content blocks + `usage` |
| `cwd`, `gitBranch`, `version`, `uuid`, `parentUuid`, `requestId` | provenance / threading | not required for v1; `uuid`/`parentUuid` form the message tree if we ever need it |

The **`message`** object is where the substance is:

- `message.role` — `user` or `assistant`.
- `message.model` — the model id in force for an assistant message (e.g.
  `claude-opus-4-…`, `claude-sonnet-…`). This is where we read the **tier**.
- `message.content[]` — an array of typed **content blocks** (below).
- `message.usage` — token accounting, present on assistant messages (below).

### 1.3 The content blocks we parse

`message.content` is a list; each block has a `type`:
([claude-dev.tools: transcripts](https://claude-dev.tools/docs/transcripts), [databunny](https://databunny.medium.com/inside-claude-code-the-session-file-format-and-how-to-inspect-it-b9998e66d56b))

- `text` — assistant prose. (Counted as output via `usage`; no special handling.)
- `thinking` — extended-thinking blocks. (Billed inside output; no separate line.)
- `tool_use` — a tool call: `{ "type":"tool_use", "id":"toolu_…",
  "name":"Edit", "input":{…} }`. The `name` is the tool; the `id` links it to
  its result.
- `tool_result` — the result of a tool call, delivered on the *next* (user-role)
  message: `{ "type":"tool_result", "tool_use_id":"toolu_…",
  "content":"…", "is_error": true|false }`. `tool_use_id` joins back to the
  originating `tool_use.id`, and **`is_error` is an explicit boolean** — no
  heuristic needed to know a tool failed.

That `is_error` flag is a gift: it lets us build a real **error taxonomy** by
classifying the `content` text of failed results (file-not-found, edit-didn't-
match, shell-command-not-found, permission-denied, …), and attribute each
failure back to the tool that produced it via the `id → name` join.

### 1.4 Token usage — the `usage` object

On assistant messages, `message.usage` reports the turn's token accounting.
The fields we need: ([claude-dev.tools: JSONL format](https://claude-dev.tools/docs/jsonl-format), [ccusage](https://ccusage.com/guide/cost-modes), [claude-code-usage-analyzer](https://github.com/aarora79/claude-code-usage-analyzer))

```json
"usage": {
  "input_tokens": 312,
  "output_tokens": 1875,
  "cache_read_input_tokens": 148032,
  "cache_creation_input_tokens": 5120,
  "cache_creation": {
    "ephemeral_5m_input_tokens": 1024,
    "ephemeral_1h_input_tokens": 4096
  }
}
```

Critical semantics:

- **`input_tokens` is only the *fresh* (uncached) input** — the tokens the model
  read for the first time this turn. It does **not** include cache reads. (This
  is the opposite of Codex's convention, where input is inclusive of cache —
  worth flagging so we never copy the wrong math between providers.)
- **`cache_read_input_tokens`** — tokens served from the prompt cache, billed at
  a deep discount. On Claude Code this is the **dominant volume line**: the whole
  conversation prefix is re-read from cache on every turn.
- **`cache_creation_input_tokens`** — tokens written *into* the cache this turn,
  billed at a *premium* over fresh input. When present, `cache_creation` breaks
  it into **5-minute** and **1-hour** TTL buckets
  (`ephemeral_5m_input_tokens` / `ephemeral_1h_input_tokens`), each with a
  different write multiplier. If only the aggregate `cache_creation_input_tokens`
  is present with no breakdown, attribute it to the 5-minute tier (ccusage takes
  the same fallback — price the aggregate at the standard cache-creation rate).
  ([ccusage](https://ccusage.com/guide/cost-modes))
- **`output_tokens`** — everything the model generated (reply text, thinking,
  tool-call arguments), billed at the separate, higher output rate.

### 1.5 Cost model — five token line-items

Anthropic bills prompt-cache writes at a premium and reads at a discount, so a
turn's cost is **five** line-items, each a multiple of the model's base **input**
rate (except output, which has its own rate): ([Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching), [Pricing](https://platform.claude.com/docs/en/about-claude/pricing.md), [ccusage cost model](https://ccusage.com/guide/cost-modes))

| Line-item | Source field | Multiplier |
|---|---|---|
| fresh input | `input_tokens` | **1×** input rate |
| cache write — 5m TTL | `cache_creation.ephemeral_5m_input_tokens` | **1.25×** input rate |
| cache write — 1h TTL | `cache_creation.ephemeral_1h_input_tokens` | **2×** input rate |
| cache read | `cache_read_input_tokens` | **0.1×** input rate |
| output | `output_tokens` | output rate |

The cache multipliers (0.1× read, 1.25× write-5m, 2× write-1h) are **fixed by
the API**, not per-model — only the base input/output rates vary by tier.
([Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)) A cached token you reuse costs ~8% of writing it fresh at
1h TTL, so caching pays for itself after ~2 reuses; the risk is **invalidation**
(editing near the front of the prompt forces an expensive re-write of everything
after it). This five-line split is exactly what makes a *token-composition* panel
worth building — it shows, per period, that cache-read volume (not fresh input)
is where the money goes.

**Design decision — recompute all history at current rates.** Because we hold the
raw token counts, we can (re)cost every past day at *today's* `PRICING` table
rather than storing a frozen dollar figure. That keeps the trend apples-to-apples
when Anthropic changes prices; the only place to show how rates *actually* moved
is a separate pricing-over-time series (stamp the rate table daily).

### 1.6 Model tiers & pricing (per 1M tokens)

Map each raw `message.model` id to a tier by substring, and price the tier.
Rates found 2026-07-09 ([Pricing](https://platform.claude.com/docs/en/about-claude/pricing.md)):

| Tier | Matches | Input | Output |
|---|---|---|---|
| `opus` | `opus`, and **`fable`** (Fable prices at the Opus tier) | $5.00 | $25.00 |
| `sonnet` | `sonnet` | $2.00 (intro, std $3.00) | $10.00 |
| `haiku` | `haiku` | $1.00 | $5.00 |

Notes:

- An **unrecognized** model id should map to no tier and have its usage **skipped
  entirely** (no cost attributed) rather than guessed — safer than mispricing.
- Anthropic exposes **no pricing API** — the Models API returns capabilities, not
  rates. The authoritative live source is the **published pricing page**
  (Markdown at `platform.claude.com/docs/en/docs/about-claude/pricing.md`). Since
  all historical cost is recomputed at the current table, a hand-maintained
  `PRICING` dict is the source of truth; a best-effort scrape of that page can
  *warn on drift* but must never silently rewrite the table (a bad scrape would
  retroactively distort every day's reported cost). ([Pricing](https://platform.claude.com/docs/en/about-claude/pricing.md))

### 1.7 Field mapping → the per-day / per-session shape we'll build

| Report field | Transcript source |
|---|---|
| activity date | `timestamp[:10]` |
| session id | `sessionId` |
| project label | subfolder name (decode the `-`-encoded absolute path) |
| model tier | `tier(message.model)` |
| fresh input tokens | `usage.input_tokens` |
| cache-read tokens | `usage.cache_read_input_tokens` |
| cache-write 5m / 1h | `usage.cache_creation.ephemeral_5m/1h_input_tokens` (fallback: aggregate → 5m) |
| output tokens | `usage.output_tokens` |
| assistant turns (`msgs`) | count of assistant messages with `usage` |
| tool calls (`by_tool[name]`) | `tool_use.name` |
| tool results / errors | `tool_result` count; `is_error==true` → error + `classify(content)` |
| per-tool cost | attribute the whole turn's cost+output to its single `tool_use` (see §1.8) |

All **day / week / month** rollups can be done downstream from per-day buckets —
the parser only ever needs to emit per-day (and, separately, per-session) totals.

### 1.8 Per-tool cost attribution — the one modeling choice

Token usage is reported **per turn**, not per tool. But Claude Code **serializes
tool calls** — the overwhelming majority of assistant turns contain exactly one
`tool_use`. So we can attribute a turn's *entire* cost and output to its single
tool, which is **exact** for single-tool turns. Turns with zero or multiple tool
calls go to pseudo-rows (`(final response)` / `(multi-tool turn)`) rather than
being force-split. This is the honest ceiling of per-tool precision until a
custom harness logs per-tool usage — and it's worth stating that caveat in the
UI.

### 1.9 Format drift & gotchas (read before implementing)

- **The transcript format is internal and versioned.** Anthropic can change field
  names or nesting between CLI releases; direct parsers can break on any update.
  ([claude-dev.tools](https://claude-dev.tools/docs/jsonl-format)) Parse **defensively**: wrap per-line and per-file reads in
  try/except and skip on failure so one malformed transcript can't abort a run.
- **Not every line has `usage` or a `message`.** System lines, summaries, and
  user messages won't carry token usage — guard for it and skip.
- **Cache-creation breakdown may be absent.** Older/simpler turns give only the
  aggregate `cache_creation_input_tokens`; handle both (aggregate → 5m fallback).
- **Transcripts rotate.** Claude Code prunes old sessions, so a re-parse only sees
  what's still on disk. If we want history to survive rotation, the *derived*
  store has to be durable on its own — a later parse can only ever **add** newly
  seen dates/sessions, never resurrect ones whose transcripts are gone.
- **Sessions span days.** A single conversation's cost is split across every date
  it touched, so per-session totals must be accumulated **globally by
  `sessionId`**, separately from the per-day buckets — otherwise a runaway
  conversation is invisible (its cost is smeared thin across many days).
- **Project label is a path, encoded.** Every project folder on a machine shares a
  long machine-specific prefix (the `-`-encoded home path). To get readable
  labels we'll strip the common leading path segments shared across all projects
  rather than hardcoding a prefix.

---

## 2. Usage & Cost Admin API — org-level supplement

For **authoritative billed dollars** (as opposed to our rate-card estimate),
Anthropic exposes a programmatic Admin API:
([Usage and Cost API](https://platform.claude.com/docs/en/build-with-claude/usage-cost-api))

- **`/v1/organizations/usage_report/messages`** — token consumption in fixed
  time buckets (`1m` / `1h` / `1d`), broken out into **uncached input, cached
  input, cache creation, and output** tokens, filterable/groupable by model,
  workspace, API key, and service tier.
- **`/v1/organizations/cost_report`** — service-level **cost in USD**, daily
  granularity, grouped by workspace or description.

Fit: this can reconcile a *total* — day, model, workspace dollars straight from
billing — but it returns **aggregates only**: no `sessionId`, no per-tool
breakdown, no error signal. It also requires an **Admin API key**
(`sk-ant-admin01-…`, distinct from a normal key) and is a **network pull**, which
breaks the "offline, local-only, no-secrets" property we want for the primary
tool. Treat it as an optional cross-check for finance, not the engine.

*(Related: the **Claude Code Analytics API** returns *estimated* per-user cost and
productivity metrics for an org — again a daily rollup, admin-scoped, not
turn/tool grain. ([Usage and Cost API — per-user Claude Code costs](https://platform.claude.com/docs/en/build-with-claude/usage-cost-api)) Same verdict: supplement, not source.)*

---

## 3. Console Usage / Cost pages — human dashboard

The Claude Console **Usage** and **Cost** pages show the same org-level data in a
UI. ([Usage and Cost API](https://platform.claude.com/docs/en/build-with-claude/usage-cost-api)) No programmatic feed beyond the APIs above, no session/tool
drill-down. Useful as a sanity check against our totals; not a data source.

---

## Design implications (what this research tells us to build)

1. **Parse local transcripts, bucket by activity timestamp.** Glob
   `~/.claude/projects/**/*.jsonl`, one pass, defensive per-line parsing. A single
   run reconstructs all history still on disk.
2. **Two accumulators, not one:** per-day buckets *and* a global per-session
   accumulator (keyed by `sessionId`), because sessions span days.
3. **Five-line cost model**, recomputed at current rates; stamp the rate table
   daily so pricing movement is its own series.
4. **Health, not just spend:** exploit `is_error` to build an error taxonomy and
   a per-tool waste estimate — this is the differentiator over a pure cost tool.
5. **Durable derived store.** Since transcripts rotate, the derived per-day /
   per-session data must persist and only ever grow (merge new dates/sessions
   over old; never regenerate-from-scratch and lose rotated history).
6. **Emit a self-contained report.** Everything above reduces to per-day and
   per-session buckets; day/week/month aggregation and all charts can live
   client-side so the output is one dependency-free file.
7. **Hand-maintained pricing + best-effort drift alarm** against the published
   pricing page — never let a scrape mutate the rate table.

---

## Sources

- [claude-dev.tools — Claude Code JSONL transcript format](https://claude-dev.tools/docs/jsonl-format) · [Reading session transcripts](https://claude-dev.tools/docs/transcripts) · [Log locations](https://claude-dev.tools/docs/log-locations)
- [Manage sessions — Claude Code Docs](https://code.claude.com/docs/en/sessions)
- [databunny — Inside Claude Code: the session file format](https://databunny.medium.com/inside-claude-code-the-session-file-format-and-how-to-inspect-it-b9998e66d56b)
- [ClaudeWorld — Session Storage](https://claude-world.com/tutorials/s16-session-storage/)
- [daaain/claude-code-log](https://github.com/daaain/claude-code-log) · [simonw/claude-code-transcripts](https://github.com/simonw/claude-code-transcripts)
- [ccusage — cost modes & cache handling](https://ccusage.com/guide/cost-modes) · [aarora79/claude-code-usage-analyzer](https://github.com/aarora79/claude-code-usage-analyzer)
- [Anthropic — Usage and Cost Admin API](https://platform.claude.com/docs/en/build-with-claude/usage-cost-api) · [Cost Report reference](https://docs.anthropic.com/en/api/admin-api/usage-cost/get-cost-report)
- [Anthropic — Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching) · [Pricing](https://platform.claude.com/docs/en/about-claude/pricing.md)
