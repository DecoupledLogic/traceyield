# TraceYield: the framework and the tool

*Product / vision doc. Written 2026-07-11. This defines **what TraceYield is**, the
discipline, and how **traceyield**, the tool in this repo, implements it. It sits
above the how-it-works docs ([`architecture.md`](./architecture.md)) and the
why-the-numbers-mean-things doc ([`token-mechanics-and-insights.md`](./token-mechanics-and-insights.md)):
those describe the current single-machine tool; this one describes the framework
the tool is growing into. Living document: update it as the framework and the
tool converge.*

---

## 0. The one-paragraph version

**TraceYield** is a closed-loop discipline for managing the **cost and efficacy of
LLM interactions**, the same way FinOps is a discipline for cloud spend. The
**token** is its unit of account, but the object it actually manages is the
**interaction**: the prompt you send, the reasoning the model does, and the
response you get back. Tokens are the *meter*; the prompt/reasoning/response text
is the *material*. TraceYield runs a loop over that material, **describe → diagnose
→ predict → prescribe → remediate**, and keeps running it, so a team gets steadily
cheaper *and* more effective at using coding agents over time. **traceyield** (this
repo) is the reference tool that implements the framework.

---

## 1. Why this exists, and why "team"

For a solo developer, token cost is a curiosity: an interesting number at the end
of the day. For a **small-to-medium team**, it is something else entirely, an
**unowned, high-variance, low-visibility line item**:

- **Unowned.** Cloud spend has a FinOps owner; CI spend has a platform owner.
  Coding-agent spend usually has *nobody*. It's charged to a plan or an API key
  and nobody watches the shape of it.
- **High-variance.** One runaway session (a long, uncleared context re-read every
  turn) or one bad prompt *pattern* replicated across ten engineers can move the
  monthly number by a large multiple. The variance is driven by **habits and
  workflows**, not headcount.
- **Low-visibility.** The people who could fix it, the engineers, can't see
  their own footprint, and the person who feels the bill, the lead, can't see
  *why* it's shaped the way it is.

TraceYield is the practice that closes those three gaps for a team: shared
visibility, clear attribution, agreed norms, quantified levers, and a loop that
keeps the number honest.

**Who it's for.** Small-to-medium engineering teams adopting coding agents
(Claude Code, Codex, and successors) who have started to *feel* the spend but have
no shared, provider-neutral way to see, explain, and bend it.

---

## 2. The framework

### 2.1 What TraceYield manages

> **Tokens are the meter, not the mission.**

The goal is not "fewer tokens." It is **value per token**: the most useful
outcome for the least cost. A cheaper session that fails the task is not a win; a
more expensive session that avoids three failed reruns is. So TraceYield measures in
tokens (because that's where cost and inefficiency become *visible*) but reasons
about the **interaction** (because that's where value and waste actually live).

This is why the framework, and the tool, deliberately capture more than counts.
The **prompt, reasoning, and response text** are first-class material: they're how
you learn *why* a session was expensive (bloated context, thrashing tool loops,
over-long reasoning) rather than only *that* it was. Counts tell you where to look;
the material tells you what to do.

### 2.2 The maturity ladder: the "operations"

The operations in TraceYield are five rungs of a single closed loop. Each rung
answers a sharper question than the last, and the top rung feeds back into the
bottom:

| Rung | Question | Example for tokens |
|------|----------|--------------------|
| **1. Describe** | What did we spend, on what? | Cost by person, project, model, tool, day. Token composition (fresh / cache-write / cache-read / output). |
| **2. Diagnose** | Why: where's the waste? | A runaway session carrying 180k tokens of context every turn; a cache-hostile workflow; a tool-error hotspot; a prompt pattern that never clears context. |
| **3. Predict** | Where is this heading? | Burn-rate to end of month; a session on track to become the next runaway; a team trending over budget. |
| **4. Prescribe** | What should we change? | Route this class of turn to Sonnet; clear context at this boundary; adopt this prompt pattern; enable caching here. |
| **5. Remediate** | Do it, and confirm it worked | Apply the lever, then measure the delta and attribute the savings, closing the loop back to Describe. |

Most usage tools stop at rung 1–2. The `Ops` in TraceYield is the promise that the
loop **closes**. You don't just observe, you act and re-measure. That's the
difference between "analytics" and "operations."

### 2.3 Principles

1. **Value per token, not fewer tokens.** Optimize the ratio, not the numerator.
2. **The material matters.** Capture prompt/reasoning/response text, not just
   counts: that's where diagnosis and prescription come from.
3. **Recompute at current rates.** All historical cost is (re)computed at today's
   prices for apples-to-apples comparison; a separate pricing time-series is the
   only place rate changes show through. (See [`architecture.md`](./architecture.md).)
4. **Provider-blind.** TraceYield is about LLM interactions in general. Claude,
   Codex, and whatever comes next ingest into one neutral record stream, so norms
   and comparisons survive a provider switch. (See
   [`canonical-data-model.md`](./canonical-data-model.md).)
5. **Privacy by construction.** Interaction data is sensitive. It stays local to
   the machine/team that produced it; nothing personal is shared by default.
6. **Upper bounds are labeled.** Prescriptive estimates (e.g. routing savings) are
   framed as bounds, never as promises.

### 2.4 The levers

Prescriptions in TraceYield pull on a known, growing set of levers, each with a
quantifiable expected impact:

- **Routing**: send routable turns to a cheaper tier (Opus → Sonnet/Haiku).
- **Context hygiene**: clear/compact before context becomes the dominant cost;
  catch runaway sessions early.
- **Caching**: structure workflows so the cacheable prefix stays stable and
  cache-read stays cheap.
- **Prompt patterns**: replace token-hostile habits (re-reading whole files,
  never clearing) with efficient ones, then propagate the good pattern across the
  team.
- **Error reduction**: tool errors are paid-for tokens that produced nothing;
  cutting them is pure savings.

---

## 3. The tool: `traceyield`

`traceyield` is the reference implementation of the framework in this repo. It is a
self-contained, offline tool today, growing along the ladder.

### 3.1 What it is today

- **`report.py`**: parses a machine's own coding-agent transcripts and emits a
  self-contained interactive HTML dashboard: cost/token/session/error breakdowns,
  a runaway-session finder, an error taxonomy with fixes, and a routing-savings
  estimator.
- **`canonical.py`**: builds a **provider-blind** SQLite store (`usage.db`): one
  neutral record stream (`Session`/`Turn`/`ToolCall`/`Segment`/`RawEvent`) that
  every provider (`ClaudeProvider`, `CodexProvider`) ingests into. This is the
  substrate the framework needs: the place the *material* lives and the source of
  truth the aggregates are derived from.

### 3.2 Where it sits on the ladder

| Rung | Status in `traceyield` today |
|------|----------------------------|
| **Describe** | ✅ Shipped: the report's KPIs, breakdowns, per-session and per-tool cost. |
| **Diagnose** | ✅ Largely shipped: runaway-session finder, error taxonomy, data-health / schema-drift monitoring, and the "Context health" work. |
| **Predict** | ⬜ The gap: no burn-rate/forecast yet. |
| **Prescribe** | 🟡 First lever shipped: the routing-savings estimator (framed as an upper bound). |
| **Remediate** | ⬜ Frontier: no apply-and-confirm-the-delta loop yet. |

### 3.3 Where it's going

Two axes of growth, both implied by the framework:

1. **Up the ladder**: predict (burn-rate, forecast, budget alerts) and remediate
   (apply a lever, measure the saved delta, attribute it).
2. **From machine to team**: today every machine produces its own neutral records
   under `machines/<machine-id>/`. That per-machine, provider-blind store is
   deliberately the seed of a **team rollup**: the same records aggregate across a
   team to give shared visibility, per-person and per-workflow attribution, and
   team norms, without any provider- or machine-specific rework, because the
   record stream is already neutral.

---

## 4. Naming

- **TraceYield** (prose, capitalized): the framework / the discipline.
- **traceyield** (lowercase): the tool that implements it (this repo, its package,
  scripts, and identifiers).

Both supersede the earlier working names **TraceYield / Token Lens** and the
repo's original directory name **cc-usage-analytics**.

---

## 5. Related docs

- [`architecture.md`](./architecture.md): how the current tool is built.
- [`token-mechanics-and-insights.md`](./token-mechanics-and-insights.md): how
  tokens are served and priced, and how that becomes diagnostic/prescriptive
  insight (the engine behind rungs 2 and 4).
- [`canonical-data-model.md`](./canonical-data-model.md): the provider-blind
  record stream that makes TraceYield provider-blind.
