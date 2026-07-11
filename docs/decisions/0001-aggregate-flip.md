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

`analyze()` is kept as:
1. the **equivalence oracle** — `TestAggregateEquivalence` in `test_report.py`
   asserts `aggregate(ingest(T)) == analyze(T)` for the same transcript `T`;
2. the **resilience fallback** — `main()` wraps `metrics_via_canonical()`
   (ingest + aggregate) in a `try/except`; any failure falls back to
   `analyze()` with a one-line stdout note, so a canonical-store bug can never
   stop a run from producing a report.

Unlike the first cut of this flip, `analyze()` was **not** left byte-for-byte
unchanged: real-corpus verification (300+ transcripts) surfaced a genuine
double-counting bug in the *legacy* `analyze()` path (see "Replay dedup"
below), so `analyze()` was fixed to match `aggregate()`'s (correct) behavior
rather than the other way around.

## Replay dedup — a correctness fix, not just a reconciliation

Claude Code replays the **same assistant turn** (identical `uuid`) — and its
tool result — into more than one transcript file across a session
resume/compaction. The canonical store already handled this correctly by
construction: `turn.turn_id` and `tool_call.call_id` are primary keys written
with `INSERT OR IGNORE` / `ON CONFLICT DO UPDATE`, so a replayed turn/call
collapses to one row regardless of how many files repeat it. The legacy
`analyze()`, walking transcripts line-by-line with no cross-file memory,
**summed every occurrence** — silently double- (or multi-) billing replayed
turns. On the real corpus this inflated reported cost by roughly **0.66%**
(~$30 across ~178 duplicated turns).

**Decision: dedup is correct** — each turn was billed once by the API, so it
should be counted once in the report. `analyze()` now tracks two run-scoped
sets across *all* files it walks (`seen_turn_ids` keyed by the assistant
message's `uuid`, `seen_result_ids` keyed by a `tool_result`'s
`tool_use_id`); a repeat is skipped in its entirety (no cost/tok/msgs/
by_model/by_project/by_tool/day-or-session-activity contribution at all),
mirroring the canonical store's dedup exactly. Only IDs that are present are
deduped — a line/block without a `uuid`/`tool_use_id` is never treated as a
duplicate. Both paths glob the same directory the same way, so first
occurrence wins identically in `analyze()`'s in-order walk and in
`canonical.ingest()`'s insert-or-ignore.

## Session span & day-activity — one shared definition, in both paths

The first cut of this flip defined `aggregate()`'s session `start`/`end` and
day `sessions` set over billable-turn + tool_result activity (via `turn` and
`tool_call` rows), while `analyze()` still updated them on *every* message
line, including plain prompt-only lines with no usage and no tool_result.
That mismatch is now closed by narrowing `analyze()` to match: it updates the
day-active-session set and a session's `start`/`end` **only** on a
(non-duplicate) billable turn or a (non-duplicate) `tool_result` block — never
on a prompt-only line, and never for a line whose replayed id was already
seen. `aggregate()` is unchanged (span = `MIN`/`MAX(ts)` over that session's
`turn` ∪ `tool_call` rows; day `sessions` = distinct session id over the same
union) — the two definitions were already equivalent, `analyze()` was simply
counting extra events `aggregate()` couldn't see.

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
- **`by_tool[*].calls` vs `.err` land on DIFFERENT days when a call and its
  result straddle UTC midnight:** `.calls` is bucketed by the linked turn's
  own day (`turn_id` → `turn.ts`), matching `analyze()`'s "a tool_use call
  belongs to its assistant turn's line/day". `.err` (and day `tool_results`/
  `tool_errors`/`errors`) stay bucketed by `tool_call.ts`'s own day, which
  tracks `MAX(call, result)` — i.e. the RESULT's day once a result has
  arrived — matching `analyze()`'s "a tool_result's error belongs to the
  result line's own day". A `tool_call` row's own `ts` is the right source for
  the latter but the wrong one for `.calls`, since it can drift to the next
  day (e.g. an `AskUserQuestion` answered just after midnight).
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

## Resolved: the earlier "known divergence" is now closed

An earlier revision of this decision recorded a single accepted gap: a
session visible on a day *purely* through non-billable prompt lines (no
`usage`, no `tool_result`) would be counted into that day's `sessions` set by
`analyze()` but be invisible to `aggregate()` (canonical `segment` rows carry
no `session_id`). Narrowing `analyze()`'s day/session-activity definition to
billable-turn + tool_result touches only (above) resolves this exactly, not
just "immaterially" — a prompt-only line now contributes nothing in *either*
path, so there is no longer an asymmetry to reason about, whether or not real
data happens to exercise it. `TestAggregateEquivalence` locks this with a
dedicated leading-prompt-only-line fixture asserting deep equality on both
`days` and `sessions`, alongside a cross-file turn-replay fixture for the
dedup fix above.

With both fixes in place, `analyze(root) == aggregate(ingest(root))` (full
deep equality on `days` and `sessions`) is expected to hold exactly across
the real corpus — not just "within cost rounding" as originally scoped, but
as an exact match, since every difference has a controlled cause.

## Round 2 — two cosmetic residuals from real-corpus verification

A first real-corpus comparison (300+ transcripts, static snapshot) matched to
the cent on total cost and matched exactly on cost/token/msgs/`by_model`.
Two smaller residuals remained, both fixed here:

- **`by_tool[*].calls` day attribution.** Fixed as described above (`.calls`
  now joins to the linked turn's day instead of using `tool_call.ts`'s day)
  — a straddling-midnight `tool_use`/`tool_result` pair is locked by
  `test_equivalence_tool_call_and_result_straddle_midnight`.
- **Session `project` is first-cwd-wins, not last-wins.** `canonical.py`'s
  `Session` upsert flipped `project`/`cwd`/`git_branch`/`cli_version` from
  `COALESCE(excluded.x, session.x)` (last writer wins) to
  `COALESCE(session.x, excluded.x)` (first writer wins), matching
  `analyze()`'s first-seen-wins semantics for a session's project/meta. This
  matters when one `session_id` spans two project directories across files
  (e.g. a worktree switch mid-session) — locked by
  `test_equivalence_session_project_first_wins_across_files`.

## Round 3 — `by_project` closed with a per-turn `project` column (schema v2)

Round 2 left one flagged, deliberately-deferred caveat: when a session spans
two project directories, `by_project` (the day-level "cost by project"
breakdown) didn't agree between the two paths, because `aggregate()` joined
it via `turn.session_id → session.project` (session-level, one value per
session) while `analyze()` attributes each turn's cost to the FILE it
actually appeared in (per-turn granularity). The real-corpus comparison
confirmed this was the **last** residual — sessions and total cost already
matched exactly, and `by_project` differed on exactly one day for exactly the
split-project session flagged in Round 2.

**Fix: give `turn` its own `project` column.** `canonical.py`'s `turn` table
gained `project TEXT`, populated per-turn by `ClaudeProvider.parse_file` from
the same `proj = report.project_of(path, self.root)` value it already
computes for every line (the FILE's own project — independent of the
session's single resolved `project`). `CodexProvider` sets it to `None`
(unused: `aggregate()` scopes to `provider='claude'`). `aggregate()`'s
`by_project` accumulation now keys on `turn.project` directly (falling back
to the session's `project` on the should-never-happen case a Claude turn's
own `project` is `NULL`), instead of joining through `session.project`.
`sessions[*].project` is untouched — it's still `session.project`
(first-wins, Round 2), which was already correct.

**Migration:** `usage.db` is a durable, gitignored, per-machine store (not
regenerated from scratch), so existing files needed an additive, in-place
upgrade rather than a fresh schema. `SCHEMA_VERSION` bumped to 2;
`open_db()` now runs `_migrate()` on every open, which checks
`PRAGMA table_info(turn)` and issues `ALTER TABLE turn ADD COLUMN project
TEXT` only when the column is missing (guarded against a duplicate-column
`OperationalError` too, belt-and-suspenders). `CREATE TABLE IF NOT EXISTS`
alone can't add a column to an already-existing table, which is exactly why
this explicit migration step exists — the additive-only column policy
`docs/canonical-data-model.md` §8 anticipated. `TestSchemaMigration` in
`test_canonical.py` builds a pre-v2 `turn` table by hand, confirms `open_db()`
upgrades it, that ingest then succeeds and populates the new column, and that
re-opening an already-migrated db is a silent no-op.

With `turn.project` in place, `test_equivalence_session_project_first_wins_
across_files` now asserts full `days == days` deep-equality (previously it
deliberately stopped short of that). Real-corpus equivalence is now exact —
`analyze(root) == aggregate(ingest(root))` on every day and every session,
with no known remaining divergence.
