# Cross-provider reporting: one dashboard, many providers

*Design doc. Status: proposed (2026-07-11). Governed by
[Decision 0007](./decisions/0007-cross-provider-reporting-per-provider-rate-cards.md).
This describes how TraceYield surfaces usage from more than one coding-assistant
provider (Claude Code + Codex today, others later) in a single report, and how
it prices each provider correctly. Read [`canonical-data-model.md`](./canonical-data-model.md)
first — this doc builds directly on the neutral store it defines.*

## The one-paragraph summary

The hard part is already done. The canonical store (`usage.db`) is
provider-blind: every provider ingests into one neutral record stream keyed by a
`provider` column, and the token vector is normalized so cross-provider sums are
apples-to-apples. What remains is entirely **above** the data layer: the
report's aggregation, pricing, and UI still assume Claude. This work generalizes
those three, so a provider that is already being *captured* also gets
*surfaced*.

## Where we are vs. where we're going

```
                       CAPTURED            PRICED           SURFACED
  Claude (usage.db)    ✅ yes              ✅ yes           ✅ yes
  Codex  (usage.db)    ✅ yes              ❌ no            ❌ no       ← this work
  Provider N (future)  (add a Provider)   (add a rate card) (free)
```

- **Captured** — `ClaudeProvider` + `CodexProvider` in `canonical.py` both write
  the neutral `Session`/`Turn`/`ToolCall`/`Segment`/`RawEvent` stream. Done in
  epic E1.
- **Priced** — a `(provider, tier)` needs a rate card to become dollars. Claude
  has one (`PRICING`); Codex does not.
- **Surfaced** — `aggregate()` and the HTML must stop filtering to
  `provider='claude'` and treat provider as a dimension.

## This supersedes the old "add OpenAI" plan

[`adding-openai-support.md`](./adding-openai-support.md) predates the aggregate
flip (roadmap E1-F2-S1). It proposed a *second parser* (`analyze_codex()`) and
*per-provider store files*. That is now obsolete:

- the Codex parser already exists (`CodexProvider`), and
- the report already derives from the unified `usage.db` via `aggregate()`.

So the remaining job is smaller and different: **generalize pricing, un-scope the
aggregation, add a provider facet.** No second parser, no store fork. That older
doc is kept for its Codex field research (§1.3–1.7), but its architecture section
is history.

## What "truly cross-provider" means here

Two principles, both from Decision 0007:

1. **Tokens are the neutral currency; dollars are a per-provider projection.**
   A combined token count is always honest — the store normalized it. A combined
   *dollar* total is only as good as the two rate cards behind it. So the UI
   leads with tokens for cross-provider comparison and treats a combined dollar
   figure as a projection, clearly labelled.

2. **Volume-always, dollars-when-priced.** A recognized model with no rate card
   still counts in token/volume aggregates at **$0**, so "we're using Codex but
   haven't priced it" is *visible*, not hidden. (An *unrecognized* model — `tier`
   is NULL — is still skipped entirely, unchanged from today.)

## The refactor, concretely

### 1. Pricing: from a flat table to per-provider rate cards

Today (`report.py:66-82`): a flat `PRICING = {opus, sonnet, haiku}` and a free
`cache_rates()` that hardcodes Anthropic's cache economics. That structure
cannot express a second provider, and it treats "cache writes are billable" as
universal when it is an Anthropic-specific fact.

The new shape nests by provider and moves cache economics *into* the per-provider
structure:

```python
# Base rates: (input_per_1M, output_per_1M), hand-verified per vendor.
PRICING = {
    "claude": {"opus": (5.00, 25.00), "sonnet": (2.00, 10.00), "haiku": (1.00, 5.00)},
    "codex":  {"gpt-5-codex": (1.25, 10.00), "gpt-5.5": (1.25, 10.00)},  # verify vs OpenAI
}

# Cache multipliers × the base input rate — a PROVIDER fact.
#   Claude bills cache writes; Codex does not.
CACHE = {
    "claude": dict(read=0.10, w5m=1.25, w1h=2.00),
    "codex":  dict(read=0.10, w5m=0.00, w1h=0.00),
}

def rate_card(provider, tier):
    """(input, output, {read,w5m,w1h $/tok}) or None if unpriced."""
    prov = PRICING.get(provider)
    if not prov or tier not in prov:
        return None
    ri, ro = prov[tier]
    c = CACHE.get(provider) or dict(read=0.0, w5m=0.0, w1h=0.0)
    return ri, ro, dict(read=ri*c["read"], w5m=ri*c["w5m"], w1h=ri*c["w1h"])

def cost_of(provider, tier, inp, cr, w5m, w1h, out):
    """Dollar cost of one turn's normalized token vector at current rates.
    Unpriced (provider, tier) → 0.0 (volume still counts; dollars do not)."""
    rc = rate_card(provider, tier)
    if rc is None:
        return 0.0
    ri, ro, cache = rc
    return (inp*ri + out*ro + cr*cache["read"] + w5m*cache["w5m"] + w1h*cache["w1h"]) / 1e6
```

`tier()` needs **no** dispatch change: the `tier` column is already written by
the correct provider at ingest (`report.tier()` / `codex_tier()`,
`canonical.py:345,539`), so cost is a pure `(provider, tier)` lookup. The
5-line-item cost formula is unchanged; Codex's zeroed write tiers just drop out.

### 2. Aggregation: drop the `provider='claude'` filter

`aggregate()` (`report.py:280-465`) filters `provider='claude'` in six places.
Un-scope them and carry `provider` through. The cost loop becomes:

```python
for turn_id, sid, day, prov, tr, inp, cr, w5m, w1h, out, tproj in conn.execute("""
    SELECT turn_id, session_id, substr(ts,1,10), provider, tier,
           input_fresh, cache_read, cache_write_5m, cache_write_1h, output, project
    FROM turn WHERE tier IS NOT NULL AND ts IS NOT NULL"""):     # provider filter gone
    cost = cost_of(prov, tr, inp, cr, w5m, w1h, out)
    ...
    D["by_provider"][prov]["cost"] += cost   # new facet, mirrors by_model/by_project
```

Day and session **top-level shapes stay combined** — `provider` is a breakdown
*within* a day (like `by_model`/`by_project`), so `daily_metrics.json`,
`merge_daily()`, and `merge_sessions()` are untouched.

### 3. Preserve the equivalence oracle

`aggregate()` is proven equal to the Claude-only `analyze()` by
`TestAggregateEquivalence` (Decision 0001). To keep that guarantee while
un-scoping, add an optional scope:

```python
def aggregate(conn, provider=None):     # None = all providers (production)
    scope = "" if provider is None else f"AND provider='{provider}' "
    # every query gains `scope`; tests call aggregate(conn, provider='claude')
```

Production spans all providers; the test pins `provider='claude'` and the oracle
stays green turn-for-turn. This is why the change is a *refactor* (Claude numbers
identical) before it is a *feature* (Codex appears).

### 4. The remaining consumers

| Consumer | Change |
|---|---|
| `record_pricing()` (`report.py:492`) | Nest by provider: `hist[date] = {provider: {tier: {input, output}}}`; read old flat entries for back-compat. It writes `pricing_history.json`, the one committed shared store. |
| `check_pricing_drift()` (`report.py:529`) | Stay Claude-scoped — only Anthropic has a scrapeable pricing page. Codex pricing is hand-maintained with **no auto-drift alarm** (documented gap). |
| `build_html()` (`report.py:817-833`) | `price_rows` + payload `pricing` key become per-provider; the route estimator keeps reading `pricing.claude` (a within-Claude optimization). |

### 5. The UI facet

Add a **"Cost by provider"** bar (mirrors "Cost by model", `report.py:1170`) and
a provider filter/toggle in the client-side `aggregate()` (`report.py:1088`),
plus a token-normalized neutral-currency view for honest cross-provider
comparison. The `by_model` / `by_project` / session tables already render
whatever keys exist, so they need no change once `by_provider` is in the payload.

## Delivery shape (roadmap E2)

Planned off Decision 0007 via `/workflow-plan`:

| Key | Item |
|---|---|
| **E2** | Cross-provider reporting (epic) |
| E2-F1 | Per-provider pricing model (`PRICING`/`CACHE`/`rate_card`/`cost_of`; Claude refactor is behavior-neutral, then Codex rate card) |
| E2-F2 | Cross-provider aggregation (`aggregate(conn, provider=None)` + `by_provider`; `pricing_history.json` provider dimension) |
| E2-F3 | Cross-provider report UI ("Cost by provider" + provider filter + neutral-currency view) |
| E2-F4 | Multi-provider pricing drift & health (scope drift to Claude; extend `coverage()`/health to Codex) |

Sequencing: F1's pricing model is the keystone; F2/F3 depend on it. Codex rate
card values must be hand-verified against OpenAI's published pricing before
shipping (the sketch above used placeholders).

## Open items to confirm at design time

- **Exact Codex rate card values** — hand-verify against OpenAI pricing.
- **`pricing_history.json` migration** — read path for existing flat snapshots.
- **Neutral-currency view shape** — a token toggle on existing charts vs. a
  dedicated comparison panel.
- **Route estimator** — stays Claude-only (Opus→Sonnet/Haiku); no cross-provider
  analog yet.
</content>
