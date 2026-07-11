---
interview-policy: always
type: decision
number: "0002"
title: "As-billed costing: cost each activity date at its effective pricing"

agency: "DecoupledLogic"
client: "Charles"
project: "traceyield"
product: ~
service: ~
workItem: ~

status: Drafted
supersededBy: ~
statusHistory:
  - status: Drafted
    date: 2026-07-11
    user: agent-charles

createdAt: 2026-07-11
updatedAt: 2026-07-11
removedAt: ~

author: agent-charles
owner: Charles
agent: agent-charles
strategicAlignment: 4
agencyValue: 2
clientValue: 4
userValue: 4
urgency: 2
viability: 4
feasibility: 5
deadlineWeight: 2
complexityPenalty: 3
sas: 3.20
---

# Decision 0002: As-billed costing: cost each activity date at its effective pricing

## Context

`report.py` computes **all** cost at the *current* `PRICING` table on every run.
Every costing path reads the live rates (`report.py:217-218` per-turn parse,
`:421` daily rollup, `:821` HTML recost, via `PRICING[tier]` / `cache_rates()`),
and Decision 0001's canonical-backed `aggregate()` deliberately preserves that
same "recompute at current rates" contract. The consequence is intentional and
documented in the UI ("Cost computed at current pricing", `report.py:1041`):
editing `PRICING` retroactively re-costs the entire history, giving an
apples-to-apples comparison across time.

What the tool **cannot** currently answer is the complementary question: *what
did a given day actually cost at the rates that were in effect on that day?*
The data to answer it already exists. `record_pricing()` (`report.py:488-495`)
stamps the `PRICING` table into the shared, committed `pricing_history.json` on
every run, and the report already renders it as a pricing-trend chart
(`report.py:1257-1260`) — but that store is **display-only**; nothing costs
against it.

This gap surfaced concretely on 2026-07-11 from a transient Opus pricing-drift
alarm (the scrape briefly reported `$6` input vs the table's `$5`; the live page
in fact still lists Opus 4.8 at `$5/$25`, so no edit was made). The episode is a
reminder that rates *do* move (Sonnet 5's intro pricing lapses 2026-08-31 by the
same table), and that "always recompute at today's rates" silently rewrites the
reported cost of history each time a rate changes. For trend analysis and for
reconciling against real invoices, an "as-billed" view — history pinned to the
rate that applied — is worth having alongside the comparability view.

## Decision

**Add a second, opt-in costing mode — "as-billed" — that costs each activity
date at the pricing effective on that date, drawn from `pricing_history.json`,
and expose a UI toggle between it and the existing "at-current-rates" default.**

Direction (details deferred to design, `/workflow-design`):

- Keep "at-current-rates" as the **default**; it stays the comparability view
  and the current behavior is unchanged.
- The as-billed path resolves each tier's base input/output rate for an activity
  date via an **as-of join**: the most-recent `pricing_history.json` snapshot
  dated **on or before** that activity date. Cache multipliers stay derived
  (`cache_rates()`: read 0.1x / w5m 1.25x / w1h 2x of the base input rate) — the
  lookup only needs base input/output per tier.
- The as-of join and the mode selection live at the **aggregation layer**
  (Decision 0001's `aggregate()` over `usage.db`), not bolted onto the legacy
  per-day parse. This is where cost is already assembled from raw tokens, and it
  keeps `analyze()` (the equivalence oracle / fallback) on its single
  current-rates contract.
- The toggle is a client-side switch in the emitted HTML where feasible; if the
  two modes cannot both be derived from one embedded payload, design decides
  whether the Python side emits both cost series or the toggle recosts in JS.

This is a governance decision that the capability is **worth building**; it does
not fix scope, schema, or UI beyond the direction above.

## Consequences

### Positive

- Answers "what did this day cost at the rates in effect then" — enables real
  invoice reconciliation and honest historical trend analysis, not only
  today's-rates comparability.
- Puts the already-collected `pricing_history.json` to work as a costing input
  instead of a display-only chart; increases the payoff of continuing to stamp
  it each run.
- Makes rate changes (e.g. Sonnet 5 intro pricing lapsing 2026-08-31)
  observable in reported cost rather than silently rewriting history.

### Negative

- Two costing modes to keep correct, test, and explain in the UI (risk of the
  two numbers confusing a reader if the toggle is not clearly labelled).
- `pricing_history.json` is keyed by **run date** and only holds snapshots for
  days the tool actually ran, so the as-of join is genuinely approximate — a
  rate change on a day with no run is attributed to the next run's stamp. This
  must be surfaced, not hidden.
- Adds a real coupling from the aggregation layer to `pricing_history.json`
  (previously only `record_pricing()` and the chart touched it).

### Neutral

- Does not change the default reported number; existing reports read the same
  unless a user opts into as-billed.
- `pricing_history.json` remains the shared, committed, non-personal store; no
  change to its per-machine/durability posture.

## Alternatives Considered

### Do nothing — keep only "at-current-rates"

Simplest; preserves one number and one contract. **Why rejected:** it
structurally cannot answer the as-billed question, and it means every future
rate change silently restates historical cost — the exact blind spot the
drift-capture episode exposed.

### Backfill `pricing_history.json` to a per-calendar-day series

Fill every date (not just run dates) so the lookup is an exact-date match.
**Why rejected (as the primary approach):** the snapshots only exist from rates
the tool observed on run days; manufacturing per-day rows invents precision that
was never captured, and the as-of join already yields the correct effective rate
without fabricating history. Backfill of *known* public rate-change dates could
be a later enhancement, not a prerequisite.

### Pin cost at write time (store a cost column per turn in `usage.db`)

Freeze each turn's cost when first ingested. **Why rejected:** it breaks
Decision 0001's "never read cost from a stored column; always recompute from raw
tokens" contract, and it forfeits the at-current-rates comparability view that
is currently the default and the whole point of recomputation.

## Implementation Notes

Two sub-problems design must resolve explicitly:

1. **As-of join semantics.** Most-recent snapshot on-or-before the activity
   date. Keying is by run date, so define the tie/gap behavior precisely.
2. **Pre-history fallback.** Activity dates earlier than the first snapshot have
   no effective rate. Candidate: fall back to the earliest snapshot (or to
   current `PRICING`) and mark those rows "unpinned" in the UI so the
   approximation is visible rather than silent.

Natural home is epic **E1** (canonical usage store); the work should sequence
**after** E1-F2's canonical-backed aggregation (`DependsOn` at plan time), since
that is the layer the as-of join plugs into.

## Traceability

| Stage | Document | Status |
|-------|----------|--------|
| Problem | N/A | - |
| Concept | N/A | - |
| Decision | This document | Drafted |
| Plan | Not started | - |

Intake: promoted from request **Q24** (tempo-portfolio intake).

## Related Decisions

- Decision 0001: Aggregate flip — derive day/session metrics from `usage.db`
  (the aggregation layer this decision extends).

---

**Decision makers:** Charles (owner), agent-charles (author)
