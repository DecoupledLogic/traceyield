---
interview-policy: always
type: decision
number: "0007"
title: "Cross-provider reporting: provider as a first-class facet with per-provider rate cards"

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
strategicAlignment: 5
agencyValue: 3
clientValue: 3
userValue: 5
urgency: 3
viability: 5
feasibility: 5
deadlineWeight: 2
complexityPenalty: 2
sas: 3.85
---

# Decision 0007: Cross-provider reporting: provider as a first-class facet with per-provider rate cards

## Context

The canonical store (`usage.db`, epic E1) is already **provider-blind**: both
`ClaudeProvider` and `CodexProvider` ingest into one neutral
`Session`/`Turn`/`ToolCall`/`Segment`/`RawEvent` stream keyed by a `provider`
column (`canonical.py:281,438`), and the 6-line token vector
(`input_fresh, cache_read, cache_write_5m, cache_write_1h, output,
reasoning_output`) is normalized so cross-provider sums are already
apples-to-apples (`docs/canonical-data-model.md` §4.1). The `tier` column is
written **per-turn by the correct provider** at ingest (`report.tier()` for
Claude, `codex_tier()` for Codex, `canonical.py:345,539`). Codex data is
therefore being **captured** today.

It is not being **surfaced**. Everything downstream of the store still assumes
Claude:

1. `aggregate()` is hard-scoped to `provider='claude'` — every query filters
   Codex out (`report.py:323,330,346,368,378,389,402,418`).
2. `PRICING` is a flat, Claude-only table `{opus, sonnet, haiku}`
   (`report.py:66`), and `cache_rates()` (`report.py:75`) bakes in Anthropic's
   cache economics (read 0.1×, write-5m 1.25×, write-1h 2×) as if they were
   universal — OpenAI has **no billed cache-write tier**.
3. The report UI, `record_pricing()` (`report.py:492`), and
   `check_pricing_drift()` (`report.py:529`) all enumerate Claude tiers only.

The predecessor design note `docs/adding-openai-support.md` predates the
aggregate flip (E1-F2-S1) and proposes a *second parser* + per-provider store
files. That path is now obsolete: since the report derives from `usage.db` via
`aggregate()`, the Codex parser already exists and the store is already unified.
The remaining work is smaller and different — **generalize pricing, un-scope the
aggregation, and add a provider facet to the UI**.

The forces: keep the single-file, stdlib-only, offline character; do not
regress the working Claude report (its cost numbers must stay byte-identical);
and preserve the `analyze()` equivalence oracle that guards `aggregate()`
(Decision 0001).

## Decision

**Make `provider` a first-class dimension of the report, priced by per-provider
rate cards, so Codex (and any future provider) appears alongside Claude in one
unified dashboard.** The specific choices, each recorded here with its
rationale, are:

### D1 — Provider becomes a first-class facet; `aggregate()` is un-scoped

Drop the `provider='claude'` filter from every query in `aggregate()` and carry
`provider` through as a grouping dimension. A `by_provider` bucket joins the
existing `by_model` / `by_project` sub-aggregations inside each day and session
bucket. The day/session **top-level shape stays combined** (one total spanning
all providers) so `daily_metrics.json` and its `merge_daily()`/`merge_sessions()`
semantics are unchanged — provider is a breakdown *within* a day, not a new
top-level key. Rationale: mirrors how `by_model`/`by_project` already work, so
the merge/persistence layer and most of the JS need no change.

### D2 — Per-provider rate cards with per-provider cache economics

Replace flat `PRICING` and the free `cache_rates()` function with a nested
structure and a single cost function:

- `PRICING[provider][tier] = (input_per_1M, output_per_1M)`.
- `CACHE[provider] = {read, w5m, w1h}` multipliers on the base input rate —
  because "cache writes are billable" is a **vendor fact**, not a universal one
  (Claude bills them; Codex does not).
- `rate_card(provider, tier)` returns the resolved rates or `None`; `cost_of(
  provider, tier, inp, cr, w5m, w1h, out)` is the one place cost is computed.

The cost formula itself is unchanged (5 line items over the normalized vector);
Codex's zeroed write tiers simply drop out. `tier()` needs **no** dispatch
change — cost is a pure `(provider, tier)` lookup because tier is already stored
per-provider in the turn row.

### D3 — Volume-always, dollars-when-priced (see also Alternatives)

A **recognized-but-unpriced** tier (`tier` present, no `PRICING[provider][tier]`
row) counts toward token/message/volume aggregates at **$0 cost**, rather than
being excluded. `tier IS NULL` (an *unrecognized* model) is still skipped
entirely, exactly as today (`report.py:418` gate). Rationale: honestly surfaces
"you are using provider X that we have not priced yet" instead of hiding the
usage; keeps the tier-null contract identical to `analyze()`.

### D4 — Tokens are the neutral currency; combined dollars are a projection

The UI frames **tokens** as the ground-truth cross-provider comparison and
**dollars** as a per-provider projection. A combined dollar total is shown, but
it is only as trustworthy as the two rate cards; a token-normalized comparison
is always honest and is offered as the neutral view. Rationale: prevents a stale
or placeholder rate card from silently distorting a cross-provider *dollar*
comparison, which is the metric most likely to mislead.

### D5 — Codex pricing is hand-maintained; drift-check stays Claude-only

`PRICING["codex"]` is a hand-verified table (same posture as the Anthropic
table). `check_pricing_drift()` stays scoped to Claude, because Anthropic
publishes a scrapeable pricing page and OpenAI does not. The Codex table
therefore has **no automated drift alarm** — a documented, accepted gap.
Rationale: preserves the offline/no-dependency property and avoids a fragile
second scraper; consistent with the existing "hand-maintained + best-effort
drift check" design.

### D6 — Preserve the `analyze()` equivalence oracle

`aggregate(conn, provider=None)` gains an optional provider scope: `None` (the
production call) spans all providers; tests call `aggregate(conn,
provider='claude')` so `TestAggregateEquivalence` against the Claude-only
`analyze()` stays green turn-for-turn. Rationale: the oracle is the guarantee
that the Claude report did not regress; un-scoping must not cost us that.

### D7 — `pricing_history.json` gains a provider dimension

`record_pricing()` nests the stamp by provider
(`hist[date] = {provider: {tier: {input, output}}}`), with a backward-compatible
read of the existing flat entries. Rationale: it is the one committed, shared
store; its shape change must not orphan historical snapshots.

Scope of *this* decision is governance — that the capability is **worth
building** and the direction above is the chosen shape. Exact schema, UI
layout, and migration mechanics are elaborated at `/workflow-design` time per
work item.

## Consequences

### Positive

- Delivers the product's core thesis — a genuinely cross-provider usage
  dashboard — on data already being captured, at low marginal cost.
- One unified report and one durable store; no per-provider file forking.
- The pricing model becomes extensible: a third provider is a new rate card +
  cache entry, nothing structural.
- Claude behavior is provably unchanged (oracle preserved), so the working
  report carries no regression risk.

### Negative

- Two hand-maintained rate cards to keep current, with automated drift coverage
  for only one of them (Codex rates can silently go stale).
- A combined dollar total can mislead if a rate card is placeholder/stale —
  mitigated but not eliminated by the neutral-currency framing (D4).
- Touches the one committed shared store (`pricing_history.json`), requiring a
  careful backward-compatible read.

### Neutral

- The default combined Claude number is unchanged for Claude-only machines
  (Codex simply does not appear when there is no `~/.codex` data).
- No change to the per-machine namespacing or durability posture of the stores.

## Alternatives Considered

### Per-provider store files + separate/parallel report (the old plan)

`docs/adding-openai-support.md`'s recommendation: a second `analyze_codex()`
parser writing `machines/<id>/codex/daily_metrics.json`, and a per-provider
`report.html`. **Why rejected:** superseded by the aggregate flip (E1-F2-S1).
The Codex parser already exists in `canonical.py` and the report already derives
from the unified `usage.db`; re-introducing a second parser and split stores
would duplicate work and forfeit the single-page cross-provider view.

### Exclude unpriced providers until a rate card exists (reject D3)

Keep the current "skip if not priced" behavior for whole providers. **Why
rejected:** it hides real usage — a machine actively using Codex would show
nothing until pricing is hand-entered, which reads as "no Codex activity" rather
than "unpriced Codex activity." Volume-always makes the gap visible.

### Store cost as a per-turn column, priced per provider at ingest

Freeze each turn's dollar cost when ingested. **Why rejected:** breaks Decision
0001's "never read cost from a stored column; always recompute from raw tokens
at current rates" contract, and forfeits recomputability across rate changes.

### Pull live rates from LiteLLM's price map (instead of hand-maintained)

Use `model_prices_and_context_window.json` as the rate source. **Why rejected
(for now):** adds a network/data dependency and breaks the offline,
no-dependency property. Hand-maintained tables + a best-effort drift check match
the existing design; LiteLLM could be revisited later as a drift *source*, not a
runtime dependency.

## Implementation Notes

Coupling points to change (current line references):

- `PRICING` / `cache_rates()` → nested `PRICING` + `CACHE` + `rate_card()` +
  `cost_of()` (`report.py:66-82`).
- `aggregate()` cost loop and its five sibling queries → drop `provider='claude'`,
  select `provider`, add the `provider=None` scope param, accumulate
  `by_provider` (`report.py:280-465`).
- `record_pricing()` → provider-nested with backward-compatible read
  (`report.py:487-494`).
- `check_pricing_drift()` / `parse_pricing_page()` → iterate `PRICING["claude"]`
  only; document the Codex gap (`report.py:503-556`).
- `build_html()` `price_rows` + payload `pricing` key → per-provider; route
  estimator keeps reading `pricing.claude` (`report.py:817-833`).
- Codex rate card values must be **hand-verified** against OpenAI's published
  pricing before shipping (the sketch used placeholders).
- Tests: extend `TestAggregateEquivalence` to pin `provider='claude'`; add
  Codex costing fixtures with hand-computed dollars; assert unpriced-tier → $0
  with nonzero tokens (D3).

Natural home is a new epic (**E2**), planned off this decision via
`/workflow-plan`. Sequencing: the pricing model (D2) is the keystone — the
aggregation un-scope (D1/D6) and UI facet (D4) depend on it.

## Traceability

| Stage | Document | Status |
|-------|----------|--------|
| Problem | N/A | - |
| Concept | [Cross-provider reporting](../cross-provider-reporting.md) | Drafted |
| Decision | This document | Drafted |
| Plan | Roadmap slice R0 / epic E2 (`roadmap.csv`) | Planned |

## Related Decisions

- Decision 0001: Aggregate flip — derive day/session metrics from `usage.db`
  (the aggregation layer this decision un-scopes; its `analyze()` oracle is
  preserved per D6).
- Decision 0002: As-billed costing (also plugs into the `aggregate()` costing
  layer; the per-provider rate cards here and the as-of pricing join there both
  live at that layer and must stay compatible).

---

**Decision makers:** Charles (owner), agent-charles (author)
</content>
</invoke>
