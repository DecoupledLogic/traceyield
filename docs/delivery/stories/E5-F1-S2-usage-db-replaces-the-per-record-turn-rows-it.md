---
id: 01M89J41K832TPRB3FY0YJHDHX
type: story
key: E5-F1-S2
status: planned
stage: ~
stageSince: ~
needsDesign: yes
assigneeId: ~
queue: ~
externalSystem: ~
externalId: ~
externalUrl: ~
externalAssignee: ~
externalAssigneeId: ~
workType: bug
urgency: ~
impact: ~
appetiteMinutes: ~
estimateMinutes: 300
estimateDraftMinutes: 960
estimateP85Minutes: 1470
estimateBasis: estimate dev, size, n=6, THIN, seed 20261104
actualMinutes: ~
templateId: ~
service: ~
---

# usage.db replaces the per-record turn rows it already holds

## Goal

A usage.db built before the fix stops over-counting for every session whose transcript can still be re-read.

## Definition of Done

_The conditions that make this shippable._

## Acceptance Criteria

- [ ] Scenario: stale rows replaced. Given a usage.db holding one turn row per record for a session whose transcript still exists, when the report runs after the upgrade, then that session's tokens and cost equal the once-per-request totals.
- [ ] Scenario: rotated history left alone. Given turn rows for a session whose transcript no longer exists, when the report runs, then those rows are unchanged and no estimate is applied to them.
- [ ] Scenario: same answer either way. Given the same transcripts, when one store is upgraded in place and another is built fresh, then both give identical totals.
- [ ] Scenario: safe to repeat. Given an upgraded store, when the report runs again, then nothing changes.
- [ ] Scenario: old store opens. Given a usage.db from before this change, when it is opened, then it opens without error and keeps its tool-call and segment links to the surviving turns.

## Test Strategy

_How delivery is verified. Design fills this: coverage layers, key scenarios,
and the pre-release check. Retired testing-plan docs fold in here (Decision 0041)._

## Notes

Planned from Decision 0011. The decision's Consequences name the problem: `canonical.write()` inserts turns with `INSERT OR IGNORE` on `turn_id`, which is the record `uuid` today, so the parser change alone leaves the per-record rows in place and `aggregate()` keeps summing them. Design chooses between replacing the rows on ingest and deduplicating on `request_id` in `aggregate()`.

## Assets

_Optional. Research, scripts, backfill, and artifact docs (estimate.csv,
presentation-deck) live in the work-item's asset directory: this file's path
with the `.md` extension removed, so the directory carries the same -slug as
the companion and reads as its contents in a listing (Decision 0048,
amended 2026-08-19). Rename both together if the title changes._

## Activity Log


## 2026-11-25 14:35 UTC -- created
Work-item E5-F1-S2 (story) created with status planned.
