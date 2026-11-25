---
interview-policy: always
type: decision
number: "0012"
title: "Price by model version: per-model rate cards with their own cache rates"

agency: "DecoupledLogic"
client: "Charles"
project: "traceyield"
product: ~
service: ~
workItem: ~

status: Drafted
framing: short-circuited
supersededBy: ~
statusHistory:
  - status: Drafted
    date: 2026-11-25
    user: agent-charles
    note: >-
      Shaped from request Q269, a rich source that also carries Q267 and Q268. The prices behind it came from one automated read of each pricing page on 2026-11-25 and are re-read at delivery. The change is not built.

createdAt: 2026-11-25
updatedAt: 2026-11-25
removedAt: ~

author: agent-charles
owner: Charles
agent: agent-charles
strategicAlignment: 5
agencyValue: 4
clientValue: 4
userValue: 5
urgency: 4
viability: 5
feasibility: 3
deadlineWeight: 2
complexityPenalty: 4
sas: 3.85
---

# Decision 0012: Price by Model Version

## Status

Drafted. `pricing.py` still prices by tier keyword, and the change waits on this decision moving to Accepted.

## Problem

The report misprices every model whose published rate differs from its tier's. `pricing.py` prices Claude usage by tier keyword: `tier()` maps a model name containing "opus" or "fable" to `opus` at (5.00, 25.00), "sonnet" to (2.00, 10.00) and "haiku" to (1.00, 5.00), and `CACHE["claude"]` prices every cache read at 0.1x the input rate. `cost_of()` takes a provider and a tier, so a turn's model version never reaches the price lookup.

The published table no longer fits that shape. Read from `PRICING_URL` (https://platform.claude.com/docs/en/docs/about-claude/pricing.md) on 2026-11-25, as base input / output per million tokens and cache-read multiplier:

| Model | Published | TraceYield today |
|---|---|---|
| Opus 5.5 | $4 / $20, cache read 0.05x | $5 / $25, 0.1x |
| Opus 5, 4.8, 4.7, 4.6, 4.5 | $5 / $25, 0.1x | $5 / $25, 0.1x |
| Sonnet 5.5 | $2 / $10, cache read 0.05x | $2 / $10, 0.1x |
| Sonnet 5 | $2 / $10, 0.1x | $2 / $10, 0.1x |
| Sonnet 4.6, 4.5 | $3 / $15, 0.1x | $2 / $10, 0.1x |
| Haiku 5.5 | $0.10 / $0.50 up to 100,000 prompt tokens, $0.50 / $2.50 above | $1 / $5 |
| Haiku 4.5 | $1 / $5 | $1 / $5 |
| Fable 5.2 | $8 / $40, cache read 0.025x | $5 / $25, 0.1x (mapped to opus) |
| Fable 5.1 | $10 / $50, cache read 0.025x | $5 / $25, 0.1x |
| Fable 5 | $10 / $50, 0.1x | $5 / $25, 0.1x |

Cache writes are 1.25x (5 minutes) and 2x (1 hour) of base input for every model, as `CACHE["claude"]` has them today. Q269 records no cache-read multiplier for Haiku 5.5 or Haiku 4.5.

Codex has the same shape problem. OpenAI's pricing page lists `gpt-6.2-sol` at $2.00 input, $10.00 output and $0.10 cached input (0.05x) up to 272,000 input tokens, and $4.00 / $15.00 / $0.20 above. `CODEX_PRICING` has no entry for the model and `codex_tier()` returns None for it, so the 19 turns a `codex exec` review ran on it on 2026-11-24 cost $0 (request Q268). `CACHE["codex"]` is one 0.1x multiplier for every model, and neither rate card has a context-length tier.

These figures came from one automated read of each pricing page on 2026-11-25. Delivery has to re-read both pages and reconcile them with the pricing drift check before any rate changes.

The effect was measured on one run, session `ad425f62` (2026-11-24). Its main thread on Opus 5.5 read 23.8M tokens from cache, which is $11.91 at today's table and $4.76 at the published rate. Q269 does not say whether that token count is request-deduplicated (Decision 0011). The ratio between the two figures holds either way.

Request Q267 took the Sonnet rate for an intro price that had lapsed. Its verification on 2026-11-25 found the opposite: Anthropic's pricing page states that Sonnet 5's $2 / $10 "is now the standard price" and that the increase to $3 / $15 scheduled for 2026-09-01 "will not occur". The `PRICING` value is right, and its comment, "Sonnet 5 intro pricing thru 2026-08-31 (std 3/15)", is stale.

The forces are that report and run-extraction dollars have to match the provider's price list, that the rate cards stay hand-maintained and are never overwritten by a scrape (Decision 0007 D5), that `pricing_history.json` and the drift check are both built around tiers, and that a change to reported cost falls under the pricing and significant-change rules in CONTRIBUTING.

## Options Considered

### Option A (chosen): per-model entries with a tier fallback

A rate card entry is keyed by model version and carries input, output and cache-read rates. Lookup tries the model version first and falls back to the tier keyword, and a fallback price is reported as an estimate.

- Pros: every row of the published table can be recorded as published, including Fable and the 5.5 cache rates. One shape covers Claude and Codex. A version released before its entry exists still gets a figure, and the report says that figure is an estimate.
- Cons: the cards grow to one entry per priced version, all hand-maintained. `pricing_history.json` and the drift check are built on tiers and need design work. Reported cost changes, so the CONTRIBUTING pricing and significant-change rules apply.

### Option B: keep tiers and add special cases for the 5.5 models

Keep `PRICING` keyed by tier and branch in `tier()` and `cost_of()` for Opus 5.5, Sonnet 5.5 and Haiku 5.5.

- Pros: the smallest change. The stored `tier` column, `pricing_history.json` and the drift check keep their shape.
- Cons: `CACHE` holds one read multiplier per provider, so each special case needs a cache exception as well as a rate.
- Why not: the mismatch is not confined to the 5.5 models. Sonnet 4.6 and 4.5 are $3 / $15 against a $2 / $10 tier, the three Fable versions carry two base rates and two cache multipliers and none of them is the opus rate, and `gpt-6.2-sol` needs its own cached-input rate. Each is another branch, and so is every later version. The exceptions add up to a per-model table spread across code paths.

### Option C: fetch live prices at run time

Read each provider's pricing page on every run and price from what it returns.

- Pros: no rate card to maintain, and a new version is priced as soon as its rate is published.
- Why not: Decision 0007 rejected a live price map because it adds a network dependency and breaks offline use, and `pricing.py` keeps the hand-verified table authoritative because a bad scrape would retroactively distort every day's reported cost. The 2026-07-11 drift alarm recorded in Decision 0002 was such a scrape: it briefly reported $6 input against the table's $5. The automated read stays a warning, not a price source.

## Decision

Each turn is priced at the published rate for its own model version.

Entry shape. A rate card entry is keyed by model version and carries input, output and its own cache-read rate. Cache-write multipliers are unchanged.

Lookup. Lookup goes to the model version first. A version with no entry falls back to the existing tier keyword, and the report states that cost as an estimate. The fallback is never used silently.

Fable. Fable is no longer priced as opus. Each Fable version is priced from its own entry.

Codex. The same entry shape serves Codex. `gpt-6.2-sol` gets an entry with its cached-input rate.

Context-length tiers. Where a model's price steps up with context length (Haiku 5.5 above 100,000 prompt tokens, `gpt-6.2-sol` above 272,000 input tokens), each turn is priced from that turn's own prompt size.

Sonnet. The Sonnet 5 rate stays as it is. The stale comment on it in `pricing.py` is corrected.

Rates. The entries take the rates Q269 records, once delivery has re-read both pricing pages and reconciled them with the pricing drift check. No rate changes before that re-read.

Left to design. How per-model rates interact with as-billed history in `pricing_history.json` (Decision 0002), whose snapshots hold input and output per tier, and with the drift check, which compares one published row per tier keyword and covers Claude only. What a Fable version with no entry falls back to, once Fable is out of the opus tier, goes with it.

## Consequences

### Positive

- Each turn is priced at its own model's published input, output and cache-read rate, so report and run-extraction dollars match the provider's price list.
- `gpt-6.2-sol` usage gets a dollar figure where it shows $0 today.
- A new model version is a new entry, with no change to the lookup. Until the entry exists, its cost is shown and labeled as an estimate.
- One entry shape covers both providers, so a per-model cache rate needs no provider-specific exception.

### Negative

- The rate cards grow from three Claude tiers and three Codex models to one entry per priced version, all maintained by hand. Codex entries have no automated drift check (Decision 0007 D5).
- The rates rest on one automated read of each pricing page on 2026-11-25. They are not confirmed until delivery re-reads both pages.
- `pricing_history.json` snapshots hold input and output per tier, and the drift check compares one published row per tier keyword. As built, neither can record or verify a per-model cache rate or a context-length tier.
- Q269 gives the context-length thresholds but not which token counts make up a turn's prompt size, or whether the higher rate covers the whole turn. The re-read at delivery has to settle both.

### Neutral

- Reported cost moves in both directions. Opus 5.5 and Haiku 5.5 rates fall, Sonnet 4.6, Sonnet 4.5 and Fable base rates rise, and the Fable 5.2 and 5.1 cache-read multipliers fall. The pricing and significant-change rules in CONTRIBUTING apply.
- The tier keywords stay in place as the fallback. A model with neither an entry nor a tier stays unpriced, as Decision 0007 D3 has it.
- `tests/test_pricing.py` and `tests/test_report.py` pin the Fable-to-opus mapping, so they change with it.
- `build_html()` lists prices and feeds the route estimator by tier. What those show once rates are per model is part of design.
- The Sonnet 5 rate does not change. Only its comment does.

## Traceability

- Intake request: `Q269` (tempo-portfolio, `docs/delivery/requests/Q269-traceyield-prices-now-differ-by-model-version-the.md`).
- Carried requests, in the same folder: `Q267` (the Sonnet rate is correct and only its comment is stale) and `Q268` (`gpt-6.2-sol` has no rate card).
- Found on 2026-11-25 while verifying Q267 and Q268 against the providers' pricing pages.
- Code in scope: `src/traceyield/pricing.py` (`PRICING`, `CODEX_PRICING`, `CACHE`, `RATE_CARDS`, `rate_card()`, `cost_of()`, `tier()`), `src/traceyield/providers/codex.py` (`codex_tier()`).
- Left to design: `src/traceyield/persistence.py` (`record_pricing()`, which stamps `pricing_history.json`), `src/traceyield/report.py` (`parse_pricing_page()`, `check_pricing_drift()`).
- Evidence: the price read of 2026-11-25 and the session `ad425f62` measurement, both recorded in Q269.

## Related Decisions

- [0002](0002-as-billed-costing-per-activity-date-pricing.md): as-billed costing prices each activity date from `pricing_history.json`. Per-model rates change what a snapshot has to hold, and that is left to design.
- [0007](0007-cross-provider-reporting-per-provider-rate-cards.md): per-provider rate cards keyed by tier, with cache multipliers per provider. This decision keys the cards by model version and moves the cache-read rate onto the entry. D3 and D5 stand.
- [0010](0010-run-extraction-json-from-provider-logs.md): run extraction reports a cost per turn, so it takes the same per-model rates.
- [0011](0011-count-each-claude-model-request-once.md): sets the quantity a rate is applied to. This decision sets the rate.
