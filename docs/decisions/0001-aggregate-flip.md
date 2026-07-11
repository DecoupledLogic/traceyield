# 0001 — Aggregate flip: derive day/session metrics from `usage.db`

**Status:** accepted · **Story:** E1-F2-S1

## Context

`report.py`'s `analyze()` has always been the single pass that both parses
Claude Code transcripts *and* accumulates `daily_metrics.json` /
`session_metrics.json`. Alongside it, `canonical.py` builds a provider-blind
SQLite store (`usage.db`) with `turn`/`tool_call`/`segment`/`session` tables,
originally as a best-effort dual-write (`ingest_canonical()`) that never fed
back into the report. With `ClaudeProvider` and `CodexProvider` both shipped
(E1-F1), the canonical store is now trusted enough to become the aggregates'
source of truth instead of a side channel.

## Decision

The live pipeline (`main()`) now derives `daily_metrics.json` and
`session_metrics.json` from `aggregate(conn)` — a new function that runs SQL
`GROUP BY` queries over `usage.db` and reproduces the exact `(days, sessions)`
dict shapes `analyze()` returns, so `merge_daily`/`merge_sessions`/`build_html`
consume it unchanged. Cost is still *recomputed* from raw tokens at the
current `PRICING`/`cache_rates()` on every run — never read from a stored
column — matching `analyze()`'s "recompute at current rates" contract.

`analyze()` is kept, unchanged, as:
1. the **equivalence oracle** — `TestAggregateEquivalence` in `test_report.py`
   asserts `aggregate(ingest(T)) == analyze(T)` for the same transcript `T`;
2. the **resilience fallback** — `main()` wraps `metrics_via_canonical()`
   (ingest + aggregate) in a `try/except`; any failure falls back to
   `analyze()` with a one-line stdout note, so a canonical-store bug can never
   stop a run from producing a report.

## Equivalence rules honored

- **Scope:** every query filters `provider='claude'` — Codex has no cost
  model yet.
- **Tier-not-null gate:** `cost`/`tok`/`msgs`/`by_model`/`by_project`/session
  cost accumulate only over `turn` rows with `tier IS NOT NULL`, mirroring
  `analyze()`'s `if tr is None: continue` (an unknown model contributes $0 but
  the day/session still exists).
- **NOT tier-filtered:** day `sessions` (distinct session id over the union of
  `turn` + `tool_call` rows), `tool_results`/`tool_errors`/`errors`, and
  `by_tool[*].calls`/`.err` — these come from `tool_call` rows independent of
  model tier, exactly as in `analyze()`.
- **`by_tool` cost/out attribution:** per tier-not-null turn, a `turn_id` →
  `tool_call` join determines the tool-use count; 0 → `"(final response)"`,
  1 → that tool's name, >1 → `"(multi-tool turn)"` — the whole turn's cost and
  output land on that one bucket.
- **Session span:** `start`/`end` are `MIN`/`MAX(ts)` over the union of that
  session's `turn` and `tool_call` rows, **not** `session.first_ts/last_ts`.
  A canonical `session` row is scoped to *the file*, and one file can hold
  multiple sessions (a rotated/resumed conversation) — relying on it would
  average two sessions' spans together. Deriving span from `turn`+`tool_call`
  rows matches `analyze()` turn-for-turn.

Two supporting fixes landed in `canonical.py` alongside `aggregate()`,
required for the span rule above to hold exactly:
- `ClaudeProvider.parse_file` now yields one `Session` record **per distinct
  `session_id`** seen in a file (each with its own first/last `ts`), instead
  of a single record for whichever session id was last seen in the file.
- `tool_call.ts` now tracks the **latest** known event for a call (call issue
  or result return) via the same `MAX`-with-`COALESCE` idiom already used for
  `session.last_ts`, instead of freezing at the call's own timestamp. A tool
  result is often the true last touch of a session.

## Known accepted divergence

`analyze()` counts a session into a day's `sessions` set on **any** line that
carries that `sessionId`, including a plain user-prompt line with no `usage`
and no `tool_result` (i.e. no billable turn, no tool call). `aggregate()`
derives day `sessions` from the union of `turn` and `tool_call` rows only —
canonical `segment` rows (which is where a bare prompt line lands) carry no
`session_id` column, so a session visible on a day *purely* through
non-billable prompt lines is invisible to `aggregate()`.

This is immaterial on real corpora: every real Claude Code session has at
least one billable turn or tool call on every day it's active. It's the one
theoretical gap between the two implementations, accepted as a trade-off
rather than adding a `session_id` column to `segment` for a case that doesn't
occur in practice.
