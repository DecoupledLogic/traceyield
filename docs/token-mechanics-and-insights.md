# Token-serving mechanics & the insights our data can yield

*Written 2026-07-11. This is the "why" doc behind the numbers TraceYield collects.
The two source-inventory docs, [`claude-usage-data-research.md`](./claude-usage-data-research.md)
and [`openai-usage-data-research.md`](./openai-usage-data-research.md), cover
**where the data comes from**. This one covers **what the data means**: how tokens
are actually served and priced on each turn, why prompt-cache reads dominate the
volume, why the cache-vs-fresh ratio is a direct readout of context-window
occupancy, and how to turn all of that into **diagnostic** ("what's wrong / what's
happening") and **prescriptive** ("do this") recommendations. Covers both Claude
(Anthropic) and Codex (OpenAI). Pricing and cache multipliers are verify-before-ship;
they're grounded in `report.py`'s `PRICING` table and `cache_rates()`.*

---

## 0. The one-paragraph version

Every turn you send to a coding agent re-transmits the **entire conversation so
far**, system prompt, tool definitions, every prior message, every tool result,
because the model is **stateless between API calls** and its attention runs over
the *whole* context to produce the next token. Prompt caching means that giant,
mostly-unchanged prefix is served back to you at a deep discount (~0.1× input)
instead of full price, and only the *new* tail of the turn is billed as fresh
input. The direct consequence: on any healthy agentic session, **cache-read is
the dominant token line**, and its per-turn magnitude is essentially a
**measurement of how big your context window is right now**. That single fact is
where most of the diagnostic power hides. A high cache-read-per-turn number isn't
a billing curiosity: it's the tool telling you "you are carrying ~N thousand
tokens of context on every single turn," and that's what makes a long, uncleared
session quietly expensive.

---

## 1. How a turn is actually served

### 1.1 The API is stateless; the client re-sends everything

There is no server-side "conversation" that the model remembers. Each call to the
Messages API (Anthropic) or the Responses API (OpenAI) is **self-contained**: the
client library inside Claude Code / Codex packs up the *full* running transcript
and ships it every turn. Concretely, turn *N* sends:

```
[ system prompt ] + [ tool definitions ] + [ user₁, assistant₁, toolresult₁, … , userₙ ]
                     └────────────────── the "prefix" ──────────────────────┘  └ new ┘
```

The model then runs self-attention over that **entire** token sequence to emit the
next token. This is not an implementation quirk you can opt out of: a transformer's
output at position *k* is a function of *all* positions `< k`. There is no hidden
state carried between HTTP calls, so "remembering" the conversation can only mean
"re-sending the conversation." (See the transformer/attention mechanics and the
API-is-stateless framing in the research brief, §1.)

**Cost implication if there were no caching:** a conversation of *T* turns whose
context grows by *d* tokens per turn would re-bill the whole prefix every turn,
roughly `d·(1 + 2 + … + T) ≈ d·T²/2` input tokens over the session. Input cost
grows **quadratically** with conversation length. That is the problem prompt
caching exists to solve, and it's why understanding caching is understanding the
whole cost model.

### 1.2 Prompt caching: pay once to process a prefix, reuse it cheaply

When the model processes a prefix, the expensive part is computing the internal
key/value representations (the "KV cache") for every token. Prompt caching stores
that processed prefix for a short TTL. On the next turn, if the prefix is
**byte-identical up to a breakpoint**, the provider skips re-processing it and
serves it from cache. You pay:

- a small **one-time write** premium to *put* a span into the cache, and
- a deep **read discount** every time that span is reused,

instead of full input price on every turn. This turns the quadratic curve above
into something close to linear: the growing prefix is *read* (0.1×) not
*re-ingested* (1×), and only the freshly-appended tail is written.

### 1.3 What "fresh input" actually is

Because the prefix is served from cache, the `input_tokens` you're billed at full
rate each turn is only the **new** material the model hasn't seen yet: your latest
user message, plus any tool results that arrived since the last cache write. On a
coding agent that is typically *small* relative to the carried context: a few
hundred to a few thousand tokens against a prefix of tens or hundreds of thousands.
**This asymmetry is the entire reason cache-read dominates.**

> ⚠️ **Provider convention differs. Never copy the math across providers.**
> Anthropic's `usage.input_tokens` is **only the fresh** (uncached) input; cache
> reads are a *separate* field. OpenAI's `input_tokens` is **inclusive of cached**,
> so fresh = `input_tokens − cached_input_tokens`. Getting this backwards
> double-counts or under-counts context on one side. (Confirmed in both source
> docs; encoded in `report.py`'s Claude parser and the Codex mapping.)

---

## 2. The cache economics (why 5-minute vs 1-hour, and what a "write" costs)

`report.py` prices a Claude turn as **five line-items**, each a multiple of the
tier's base *input* rate (except output). This is `cache_rates(inp)` plus the
output rate:

| Line-item | Field | Multiplier | Meaning |
|---|---|---|---|
| fresh input | `usage.input_tokens` | **1.0×** | new tokens processed for the first time |
| cache write, 5 min TTL | `cache_creation.ephemeral_5m_input_tokens` | **1.25×** | put a span in cache, expires in 5 min |
| cache write, 1 h TTL | `cache_creation.ephemeral_1h_input_tokens` | **2.0×** | put a span in cache, expires in 1 h |
| cache read | `cache_read_input_tokens` | **0.1×** | reuse a cached span |
| output | `usage.output_tokens` | output rate | everything generated (text, thinking, tool args) |

Key facts baked into this:

- **Writes cost *more* than fresh input**, not less. A 5-minute write is 1.25×,
  a 1-hour write is 2×. You are pre-paying to make future reads cheap. So caching
  is only a win if the span is **reused enough times to amortize the write**.
- **Reads are 0.1×.** A cached token you reuse costs one-tenth of ingesting it
  fresh.
- **The multipliers are fixed by the API, not per-model.** Only the base
  input/output rates vary by tier (`PRICING`). This is why `cache_rates()` takes
  just the input rate and derives all three.

### 2.1 Break-even: when does a cache write pay for itself?

Anthropic's own numbers: a span reused across *R* requests costs
`write + (R−1)·read` under caching versus `R·1.0` without it.

- **5-minute (1.25× write):** `1.25 + 0.1·(R−1) ≤ R` solves at `R ≥ 1.28`, so
  caching wins by the **2nd request** (write on turn 1, read on turn 2). Anthropic
  states this as "2 requests break even" (1.25 + 0.1 = 1.35× vs 2× uncached).
- **1-hour (2.0× write):** `2.0 + 0.1·(R−1) ≤ R` solves at `R ≥ 2.1`, break-even
  at the **3rd request** (2.0 + 0.2 = 2.2× vs 3× uncached).

In an agentic loop the prefix is reused on *every* subsequent turn, so the write
is amortized almost immediately and every turn after rides the 0.1× read. Caching
is a massive net win. The only way to *lose* is to write a span and then **not
reuse it** (session ends, or the prefix is invalidated) before the TTL expires.
There's also a **minimum cacheable prefix** (~1,024 tokens on current Opus/Sonnet;
512 on the smallest models): shorter prefixes silently don't cache at all
(`cache_creation_input_tokens: 0`, no error).

### 2.2 The 5-minute vs 1-hour trade-off

The TTL is a bet on **how long until your next turn**:

- **5-minute (1.25×)**: the default; fine when you're actively working and turns
  come every few seconds/minutes. The prefix is re-written *and* re-read within
  the window, so the cache stays warm cheaply.
- **1-hour (2.0×)**: worth the higher write premium only when there will be
  **gaps longer than 5 minutes** between turns (you're reading, thinking, in a
  meeting) and you'd otherwise let the 5-minute cache expire and pay a *full
  fresh re-ingest* of the whole prefix on your next turn. The 2× write is cheaper
  than one full re-ingest of a large prefix.

Diagnostic corollary: seeing meaningful **1-hour** write volume in the token
composition tells you the harness judged your cadence gappy. Seeing lots of
**repeated 5-minute writes of the same large prefix** (high write:read) can mean
the opposite: the cache kept *expiring* between turns and getting rebuilt from
scratch. Both are visible in our five-line split.

### 2.3 Cache invalidation: the expensive footgun

A cached prefix is only reusable if it's **byte-identical up to the cache
breakpoint**. The render order is `tools → system → messages`, and the invariant is
brutal: **any byte change anywhere in the prefix invalidates everything after it.**
The usual silent culprits:

- **a mutating value near the top**, a `datetime.now()` / UUID in the system
  prompt, or non-deterministic JSON key order (`json.dumps` without `sort_keys`) in
  a tool definition, changes the prefix every turn and busts the whole cache,
- **a changing tool set or a model switch** mid-session: both bust *all* cache
  tiers (caches are model- and tool-definition-scoped),
- **letting the TTL lapse** between turns (gap > 5 min on the default tier),
- **the >20-block lookback overrun**: on a read, the system walks backward only
  ~20 content blocks from the breakpoint looking for a match; a single turn that
  appends more than ~20 tool_use/tool_result blocks can push the prior write out of
  that window and force a re-write.

When any of these happens you pay a fresh **cache-write** for the whole tail again:
a **cache-write spike** disproportionate to new content. That's why a high
cache-write:cache-read ratio is a red flag (see §4, D4). Anthropic's concrete test:
diff the rendered prompt bytes between two consecutive requests to find the
invalidator; if `cache_read_input_tokens` stays 0 across requests that *should*
share a prefix, an invalidator is confirmed.

**Not every change is equally catastrophic.** The cache has three tiers
(`tools`, `system`, `messages`, in render order), and a change only busts its own
tier *and everything after it*, so the cost of a change depends on how far
forward it sits. ✅ = that tier's cache survives:

| Change | Tools | System | Messages |
|---|:--:|:--:|:--:|
| Tool definitions (add / remove / reorder) | ❌ | ❌ | ❌ |
| Model switch | ❌ | ❌ | ❌ |
| `speed` / web-search / citations toggle, **system-prompt content** | ✅ | ❌ | ❌ |
| `tool_choice`, images, `thinking` on/off | ✅ | ✅ | ❌ |
| Message content (a normal new turn) | ✅ | ✅ | ❌ |

The two total-rebuilds are **changing the tool set** and **switching models**.
Both re-write from byte 0. Everything else keeps the (expensive) tools+system
prefix and only re-writes the message tail, which is the normal per-turn cost.
So a mid-session model swap or a churned tool list is the pathological case; a new
user turn is not. ([Anthropic: Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching))

### 2.4 Codex / OpenAI: simpler, three line-items

OpenAI's model is simpler: caching is **automatic** (no `cache_control`,
no breakpoints; the API caches the longest previously-seen prefix from ≥1,024
tokens in 128-token steps), and for the **GPT-5 / Codex generation** it's three
line-items:

| Line-item | Field | Multiplier |
|---|---|---|
| fresh input | `input_tokens − cached_input_tokens` | 1.0× |
| cached input read | `cached_input_tokens` | **~0.1×** (GPT-5-Codex $1.25 → $0.125 cached = 90% off) |
| output | `output_tokens` (includes `reasoning_output_tokens`) | output rate |

Two caveats worth stamping, because both **changed recently**:

- **The 0.1× read discount is generation-specific.** It holds for the GPT-5 / Codex
  family (~90% off). Older **GPT-4o-era** models cache at only **50% off** (0.5×).
  Since Codex runs the GPT-5 family, 0.1× is right for us, but don't copy it to a
  GPT-4o workload.
- **"No write premium" is now outdated for the newest models.** **GPT-5.6+** added a
  **1.25× cache-write fee** (matching Anthropic's 5-min tier); earlier GPT-5 / Codex
  models had none. So a Codex cost model must branch on model version.

The *context-occupancy* reading (below) works identically on Codex, using
`cached_input_tokens` where Claude uses `cache_read_input_tokens`. One extra Codex
signal: `reasoning_output_tokens` is a **subset** of `output_tokens` (not cached,
not additive), so you can measure what fraction of generation was "thinking" vs.
final answer: bill output once, report the reasoning share as its own diagnostic
(D13).

---

## 3. The centerpiece: cache-read ≈ a measurement of context size

This is the deduction the whole project quietly rests on, made explicit.

### 3.1 The near-identity

On turn *N*, the cached prefix that gets read back **is** the conversation context
carried from turn *N−1*: system prompt + tool defs + every prior message and tool
result. Therefore:

```
cache_read_tokens(turn N)  ≈  size of the context window occupied at turn N
```

This is not a loose correlation: it's close to an **identity**. Every token read
from cache is, by definition, a token of context the model is carrying this turn.
So averaged over a period:

```
average context occupancy  ≈  cache_read_tokens / assistant_turns  ( = tok.cache_read / msgs )
```

If that number is ~150,000, then on a typical turn the model is holding ~150k
tokens and re-reading all of it. Against the current flagship window that's ~15% of
a **1M-token** context (Opus 4.8 / Sonnet 5 default to 1M now; the old >200k
premium was removed), but against the **200k** window that Claude Code's status bar
historically still displays, it's 75% and near the compaction cliff. Either way,
the occupancy *number* is exact; only the *fraction-of-window* framing depends on
the model in force. This is *exactly* what "your very high cache usage indicates
high context usage" meant: it's the same quantity, read two ways.

For scale, published measurements of agentic coding sessions line up with this: by
~turn 30 a typical session carries 25–35k tokens of accumulated context on *every*
request, and a ~50-turn session runs on the order of **1M input tokens against
~40k output: roughly a 25:1 input:output ratio**, almost all of that input served
as cache reads.

There's a fixed **floor**: the system prompt + tool definitions (tens of
thousands of tokens) are cached and re-read every turn even in a brand-new
session. So `cache_read/turn` never goes to zero; the *growth above that floor* is
your conversation. A session that starts around the floor and climbs steadily is
context accreting turn over turn.

### 3.2 Why fresh input stays small, and what it means when it doesn't

`input_tokens` (fresh) is just the newly-appended tail each turn. So a high
**cache_read : fresh_input** ratio is the normal, healthy signature of an agent
re-reading a large context. The interesting cases are the departures:

- **fresh input unusually *high* relative to cache_read** → you're injecting lots
  of *new* material each turn that wasn't cached: big fresh file reads, pasted
  logs, MCP tool payloads, or short conversations that never build a reusable
  prefix. Fresh input is the *expensive* line (1× vs 0.1×), so this is where a
  surprising bill often comes from.
- **cache_read enormous relative to everything** → long carried context (the
  runaway-session signature, §3.3).

### 3.3 Why long uncleared sessions are quadratically expensive

Within a session, each turn's prefix = the previous prefix **plus** what the last
turn appended. So cumulative cache-read over a session is roughly the running sum
of a *growing* prefix:

```
session cache_read  ≈  Σₙ (context at turn n)  ≈  turns × average_context
```

and because the context itself grows with turns, the total scales like
**turns × (turns × growth) → O(turns²)**. A 200-turn session that let its context
balloon to 180k pays cache-read on ~180k **every turn for the back half of the
conversation**. Even at 0.1×, 180k × 0.1 × (per-1M rate) × 100 turns is real money.
This is precisely the "single runaway conversation" the **Top sessions by cost**
table exists to catch, and now you can see *why* it's expensive: not many
messages, but a big context re-read on each of them.

The tell in our data: a session with **high `cache_read / msgs`** (big average
context) and a **high total cost** despite a modest `msgs` count. Contrast a
cheap-but-chatty session: many `msgs`, low `cache_read / msgs` (context kept
small), low cost.

---

## 4. Diagnostic catalog: metrics we can derive from what we already store

Everything below is computable from the fields already in `daily_metrics.json` /
`session_metrics.json` (`tok.{input,output,cache_read,cache_write_5m,cache_write_1h}`,
`msgs`, `tool_results`, `tool_errors`, `cost`, per `by_model` / `by_tool` /
per-session). None of these need new capture: they're ratios of existing numbers.

| # | Signal | Formula (our fields) | Reads as | Healthy / watch |
|---|---|---|---|---|
| D1 | **Context occupancy** | `cache_read / msgs` | avg tokens carried per turn | rising toward the window limit = bloat |
| D2 | **Cache-read share** | `cache_read / (cache_read + input + cache_write_5m + cache_write_1h)` | how input-side volume splits | high is *normal* for agents; very low = short/fresh-heavy work |
| D3 | **Cache reuse ratio** | `cache_read / (cache_write_5m + cache_write_1h)` | how many reads per token written | want **high**; ~1 or below = writing cache you barely reuse |
| D4 | **Write-premium burn** | `cost(writes) / cost(reads)` (writes = 1.25×/2× volume, reads = 0.1×) | are you paying to cache more than you save | high = churn/invalidation (§2.3) |
| D5 | **1h-vs-5m write mix** | `cache_write_1h / (cache_write_1h + cache_write_5m)` | how gappy your cadence looks | lots of 1h = long between-turn gaps |
| D6 | **Fresh-input intensity** | `input / msgs` | new material injected per turn | spikes = big fresh file reads / pasted payloads |
| D7 | **Output intensity** | `output / msgs` | generation per turn | spikes = large writes / verbose thinking |
| D8 | **Cost per turn** | `cost / msgs` | efficiency of a turn | trending up within a session = context bloat |
| D9 | **Runaway session** | session `cost` high **and** `cache_read/msgs` high | one convo carrying a big context | the Top-sessions table |
| D10 | **Tool-error rate** | `tool_errors / tool_results` | harness friction | already surfaced; pairs with the error taxonomy |
| D11 | **Error waste $** | `Σ per-tool (err × cost/call × retry)` | dollars lost to failing tools | already surfaced |
| D12 | **Opus routable share** | `by_model.opus` tokens recosted at Sonnet/Haiku | over-use of the premium tier | the routing estimator |
| D13 | **Reasoning share** (Codex) | `reasoning_output / output` | how much generation was thinking | high = expensive deliberation |

D1, D3, and D4 are the genuinely new lenses this doc argues for: they're the ones
that read *context health*, not just spend, and they fall straight out of the
five-line split we already persist.

---

## 5. Prescriptive playbook: what each signal should make you do

Map the diagnostics above to concrete actions. These are the "so what."

| If you see… | It means… | Do this |
|---|---|---|
| **D1 rising** (context/turn climbing toward the window) | context is accreting; every turn re-reads more | `/compact` to summarize, or `/clear` and restart the task with only what's needed; split the task across fresh sessions |
| **D9** (runaway session in Top-sessions) | one long conversation re-reading a huge context | don't keep piling onto it. Start a new session per sub-task; use **subagents** to do bounded work in isolated context and return only a summary |
| **D6 spikes** (fresh input per turn high) | you're re-injecting big *new* payloads each turn (1× price) | avoid re-reading whole large files; read the slice you need; pull big references once and let them cache instead of re-pasting |
| **D3 low / D4 high** (writing cache you don't reuse; write-premium burn) | cache is being built and thrown away: invalidation or short sessions | keep the front of the prompt **stable** (don't churn system prompt/tool list/memory mid-session); avoid model-switching mid-session; batch turns so the cache stays warm |
| **D5 high** (lots of 1h writes) | long gaps between turns | fine *if* intentional (you stepped away); if not, work in tighter bursts so the cheaper 5-min cache suffices |
| **D8 climbing within a session** | per-turn cost inflating from context growth | same as D1: compact or restart before it compounds quadratically (§3.3) |
| **D12 material** (Opus on routable work) | premium tier doing work a cheaper tier could | route the safely-routable share to Sonnet/Haiku (`/model`); the estimator upper-bounds the savings |
| **D10 / D11 elevated** (tool errors / waste) | harness friction burning turns | apply the paired fix from the error taxonomy (e.g. Read-before-Edit, PowerShell for Windows shells); recurrent denials → allowlist |
| **D13 high** (Codex reasoning share) | lots of tokens spent thinking | lower the reasoning effort for routine work; reserve high effort for genuinely hard turns |

### 5.1 The general context-hygiene principle

The through-line of the whole cost model is: **context is a standing tax you pay
on every turn.** Caching makes the tax cheap (0.1×) but not free, and it compounds
with conversation length. So the highest-leverage habit is **keeping context
small and stable**:

- **Small**: `/compact` (LLM-summarize history, e.g. ~70k → ~4k tokens, keeps
  intent) or `/clear` (wipe entirely, loses accumulated understanding) when a task
  is done; don't let one session serve five unrelated tasks. **Compact
  proactively**: around ~60% context utilization, ahead of the ~80% auto-compact
  cliff; a manual compact while the model still has full recall produces a better
  summary than the emergency auto-compact. Note `/compact` *isn't free*: it's an
  LLM call billed against your **full current context**, so it's a spend, just a
  cheaper one than continuing to drag the context. Use **subagents** as a
  context-management tool, not just delegation: a subagent runs in its own fresh
  context and returns only its conclusion, keeping exploratory reads out of the
  main thread.
- **Stable**: don't churn the front of the prompt mid-session (timestamps, UUIDs,
  unsorted JSON, a changing tool list all invalidate the cache and force re-writes,
  §2.3); avoid mid-session model swaps.
- **Warm**: work in bursts so the 5-minute cache stays hot rather than expiring
  and forcing full re-ingests.

On the **Codex** side the levers are analogous, auto-compaction as the window
fills, plus a fresh-session command, but confirm the exact command names against
OpenAI's current Codex CLI docs before relying on them; they weren't pinned to a
primary source at time of writing.

None of this requires the user to think in tokens. TraceYield's job is to translate
these mechanics into the three or four numbers (D1, D3, D8, D9) that make the right
habit obvious.

---

## 6. What the data *can't* tell us (honest limits)

- **Per-tool token attribution is turn-level, not tool-level.** Claude Code
  serializes tool calls so single-tool turns attribute exactly, but usage is
  reported per *turn*: a turn with a fresh file read charges that read to whatever
  tool ran, and multi-tool/no-tool turns go to pseudo-rows. So D6/D7 are precise
  per turn, approximate per tool.
- **Cache-read floor isn't separated from conversation.** `cache_read` bundles the
  static system+tools prefix with the growing conversation. D1 is an *occupancy*
  proxy, not a clean "conversation size": the floor inflates it, especially for
  short sessions. (Separating them would need the turn-grained canonical store,
  §7.)
- **We infer, we don't observe, invalidation.** D3/D4 *suggest* churn but can't
  point at the exact line that broke the cache: the transcript doesn't record
  cache-breakpoint decisions.
- **Codex tool-error detection is heuristic.** No `is_error` boolean, so D10/D11
  on the Codex side lean on output-shape heuristics (non-zero exit, `success:false`),
  not a clean flag.
- **Context-window *limit* is a moving target.** Current flagships (Opus 4.8,
  Sonnet 5) default to a **1M-token** window at standard rates, while older models
  and Claude Code's status bar still frame 200k, so what "D1 is high" *means* shifts
  by model. The occupancy *number* is exact; the *fraction-of-window* framing
  depends on the model in force that turn.
- **Provider pricing keeps moving.** The OpenAI cache-read discount is
  generation-specific (0.1× for GPT-5/Codex, 0.5× for GPT-4o) and GPT-5.6+ added a
  1.25× cache-write fee, so a Codex cost model has to branch on model version, and
  today's `PRICING`-style flat assumptions will drift.

---

## 7. Where these metrics should live (forward pointer)

Today's aggregates (`daily_metrics.json` / `session_metrics.json`) already carry
everything D1–D13 need, so the diagnostic panels are a **client-side / JS**
addition to `HTML_TMPL` (mirroring how `aggregate()` and `renderRoute()` already
work), no new capture required. The richer, *turn-grained* questions (separating
the cache floor from conversation growth, per-turn cost slope within a session,
latency-of-tool, retry-loop detection) want the **canonical store** (`usage.db`,
epic **E1**), which preserves per-turn/per-tool grain the day/session sums discard.
See [`canonical-data-model.md`](./canonical-data-model.md). The natural next step
once the aggregate flip (E1-F2-S1) lands is a **"Context health" panel**: D1 as a
gauge against the model's window, D3/D4 as a cache-efficiency readout, and D8's
per-turn cost slope as the early-warning line for a session about to run away.

---

## Sources

Mechanics and pricing here build on the two source-inventory docs (which carry the
primary Anthropic/OpenAI citations) plus the mechanics research brief compiled for
this doc:

- [`claude-usage-data-research.md`](./claude-usage-data-research.md): Claude Code
  transcript schema, five-line cost model, cache-creation TTL fields, pricing.
- [`openai-usage-data-research.md`](./openai-usage-data-research.md): Codex rollout
  schema, `cached_input_tokens` inclusive-of-cache convention, three-line cost model,
  `reasoning_output_tokens`.
- [Anthropic: Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
  · [Pricing](https://platform.claude.com/docs/en/about-claude/pricing.md)
  · [Context windows](https://platform.claude.com/docs/en/build-with-claude/context-windows)
- [OpenAI: API pricing](https://developers.openai.com/api/docs/pricing)
  · [Prompt caching guide](https://developers.openai.com/api/docs/guides/prompt-caching)
  · [API prompt caching announcement](https://openai.com/index/api-prompt-caching/)
  · [Codex rate card](https://help.openai.com/en/articles/20001106-codex-rate-card)
- Cache-write fee change for GPT-5.6+: [Elser AI: GPT-5.6 pricing](https://www.elser.ai/blog/gpt-5-6-pricing-explained-sol-terra-luna-and-prompt-caching)
- KV-cache / why the prefix must be re-processed: [KV Cache Internals (Towards AI)](https://pub.towardsai.net/kv-cache-internals-how-transformers-avoid-recomputing-attention-27672f3382e0)
- Real-world agentic session cost measurements (25:1 input:output, ~1M tokens/50 turns):
  [Vantage: The hidden cost driver in agentic coding](https://www.vantage.sh/blog/agentic-coding-costs)
  · [Waxell: AI agent context-window cost](https://waxell.ai/blog/ai-agent-context-window-cost)
- Context management (`/compact` vs `/clear`, ~60%/80% thresholds, subagents):
  [SitePoint](https://www.sitepoint.com/claude-code-context-management/)
  · [Zenva](https://academy.zenva.com/claude-code-context-window-compact-vs-clear/)
- [ccusage: cost modes & cache handling](https://ccusage.com/guide/cost-modes)
  · [Codex guide](https://ccusage.com/guide/codex/)
- Ground truth for the multipliers and field names: `report.py` (`PRICING`,
  `cache_rates()`, `analyze()`).
