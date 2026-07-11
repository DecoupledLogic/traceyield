---
interview-policy: always
type: decision
number: "0004"
title: "raw_event retention: archive-before-null over destructive age-out"

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
strategicAlignment: 3
agencyValue: 2
clientValue: 3
userValue: 3
urgency: 2
viability: 4
feasibility: 5
deadlineWeight: 1
complexityPenalty: 2
sas: 2.75
---

# Decision 0004: raw_event retention: archive-before-null over destructive age-out

## Context

Story **E1-F3-S1** shipped `canonical.age_out()` as a **destructive** retention
pass: `UPDATE raw_event SET raw=NULL WHERE ts < now-90d`. The verbatim payload is
gone once nulled; only the `sha256` survives. That destructive default was an
implementation choice, not a deliberated policy — the story's job was to bound the
one unbounded surface, and null-out is the cheapest way to do it. Before the pass
ever runs in production, the retention **policy** deserves a decision. This is the
governance statement promoted from request **Q26**.

Three facts scope the question narrowly:

- **The modeled store is never at risk.** Turns, tool calls, segments, and all
  derived cost are kept forever regardless of retention; age-out never touches
  them.
- **`structural` mode (the default) stores no `raw` at all** — only the `sha256`.
  Age-out only bites in `verbatim` mode, so the surface at issue is *exclusively*
  the verbatim JSON of **unmodeled** log line types (the escape hatch).
- **The growth is trivial.** `docs/canonical-data-model.md` §7 estimates
  ~0.2–0.6 GB/year for that surface — nothing for a local, per-machine file.

So disk pressure is **not** the deciding factor. The one factor that is: **the
raw escape-hatch payloads are reprocessable input, not exhaust.** If we later model
a new line type (a new Codex event, a new provider), we can only backfill it from
**retained raw**. `SET raw=NULL` forecloses that permanently; keeping the payload
(hot or cold) preserves the reprocessing seam. That option-value is the whole
argument, and it points away from destructive null-out.

A second, orthogonal issue surfaced during E1-F3-S1 delivery: `age_out()` is
**not wired into the scheduled production path**. `report.py`'s
`metrics_via_canonical()` calls `canonical.ingest(db)` but never `age_out(db)`, and
the scheduled task (`run.cmd`) runs `report.py`, not `canonical.py`. The pass is
therefore **dormant in production** today — only the manual `python canonical.py`
entrypoint runs it. Whatever policy wins, this wiring must be resolved.

## Decision

**Adopt archive-before-null as the retention policy, superseding E1-F3-S1's
destructive-null assumption**, and resolve the production-wiring gap as part of the
same work. Details are deferred to design/plan (`/workflow-plan`,
`/workflow-design`); this decision fixes the direction, not the schema or file
format.

Direction:

- **Archive, then null.** Before nulling an aged `raw_event.raw`, append the row's
  verbatim payload (plus its structural keys and `sha256`) to a compressed,
  per-machine archive — e.g. `machines/<id>/raw_archive/YYYY-MM.jsonl.gz` — using
  **stdlib `gzip` only**. This bounds the hot `usage.db` while preserving the
  reprocessing seam. It stays inside the tool's non-negotiable ethos: single-file,
  stdlib-only, no build step, per-machine data namespacing, `machines/`
  git-ignored.
- **Retention window stays configurable** (`RAW_RETENTION_DAYS`, default 90;
  `TRACEYIELD_RAW_RETENTION_DAYS`), and the pass stays idempotent.
- **Resolve the wiring** by running the retention pass on the scheduled path:
  call it **best-effort** from `report.py`'s `metrics_via_canonical()` (after
  `ingest`, guarded so a retention failure can never abort a report run and never
  triggers the `analyze()` fallback). The `python canonical.py` entrypoint keeps
  its own call.
- **Keep a seam for the future.** Structure the pass so the archive sink is one
  implementation behind a thin boundary; a real cold-storage / data-lake sink
  (deferred, see Alternatives) can replace it later without touching the age-out
  logic.

This is a governance decision that archive-before-null is **worth doing** and how
it should be shaped. It does not create work-items — `/workflow-plan` builds those
off this decision.

## Consequences

### Positive

- Preserves the reprocessing/backfill seam: newly-modeled line types can be
  recovered from the cold archive instead of being lost forever.
- Still bounds the hot `usage.db`, so the modeled-store growth stays predictable
  (linear in turns) and the variable-size risk is quarantined.
- Stays fully inside the stdlib-only, single-file, per-machine ethos — no new
  dependency, no service, no build step.
- Wiring the pass into `report.py` makes retention actually run on the scheduled
  path instead of being dormant.

### Negative

- Adds a new on-disk artifact class (`raw_archive/*.jsonl.gz`) whose lifecycle
  (rotation, its own eventual cap) must itself be considered, or we have merely
  moved the unbounded surface rather than bounding it.
- The archive shares the durable store's sharp edge: `machines/` is git-ignored,
  so the cold archive has no git backup and is lost on a disk wipe / fresh clone.
- Slightly more code than a bare `UPDATE ... SET raw=NULL`, and the archive write
  must be crash-safe so a half-written archive cannot precede the null.

### Neutral

- No effect on any historical reported cost or on the modeled tables; retention
  only ever touched the `raw` escape-hatch column.
- In `structural` mode there is nothing to archive (no `raw` is stored), so the
  policy is a no-op there — same as the destructive pass is today.

## Alternatives Considered

### Keep destructive age-out (null, no archive)

Ship E1-F3-S1 as-is; null the aged `raw` with no archive. **Why rejected:** it
permanently forecloses reprocessing/backfill, which is the *only* reason retention
of this surface matters at all. Destroying the payload to reclaim ~0.5 GB/yr on a
local file trades the one valuable property (option-value) for a saving we do not
need.

### Do not age out at all

Delete/dormant the pass; let `raw` grow. **Why rejected (as the end state):** at
verbatim capture the escape-hatch surface is genuinely unbounded (large
`world_state` / `inter_agent_communication` snapshots), and leaving it uncapped
re-opens exactly the risk E1-F3-S1 was meant to quarantine. Archive-before-null
keeps the payload *and* bounds the hot db, which is strictly better than keeping it
hot forever. (This remains the correct fallback if the archive work is judged not
worth it at plan time — it is cheap and honest.)

### Real cold storage / data lake behind a RetentionSink abstraction

Ship aged payloads to S3 / a lake behind a pluggable sink. **Why rejected (now):**
it breaks the stdlib-only, single-file, per-machine ethos and is heavy machinery
for a personal telemetry tool. Deferred, not dismissed: the archive sink should be
built behind a thin seam so a lake sink can replace it if the store ever becomes
multi-machine or shared.

## Implementation Notes

- The archive sink is a small `gzip`-append writer keyed by machine and month;
  write-then-null must be ordered so a crash cannot null a row whose payload was
  not durably archived.
- Wiring into `metrics_via_canonical()` must be best-effort: wrap the call so an
  exception is swallowed (logged to stdout), never re-raised — otherwise a
  retention error would trip `main()`'s fallback to `analyze()` and silently drop
  the canonical aggregate path.
- Consider whether the archive itself needs a cap / rotation policy, or the
  unbounded surface has merely moved from the db to the filesystem.
- Suggested shape at plan time: a small tech-debt/story under **E1-F3** that
  (a) adds the archive-before-null sink, (b) wires the pass into the scheduled
  `report.py` path, and (c) covers both with tests straddling the window — plus a
  note updating `docs/canonical-data-model.md` §7 to describe the archive.

## Traceability

| Stage | Document | Status |
|-------|----------|--------|
| Problem | N/A | - |
| Concept | N/A | - |
| Decision | This document | Drafted |
| Plan | Not started | - |

Intake: promoted from request **Q26** (tempo-portfolio intake).

## Related Decisions

- Decision 0001: Aggregate flip — derives day/session metrics from `usage.db`;
  `metrics_via_canonical()` (the wiring site for the retention pass) is its
  production path.

---

**Decision makers:** Charles (owner), agent-charles (author)
