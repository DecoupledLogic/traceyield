---
id: 01M89J3GSXH5YD4PVS3C19A70Q
type: feature
key: E4-F1
status: in_progress
stage: ~
stageSince: ~
needsDesign: ~
assigneeId: ~
queue: ~
externalSystem: ~
externalId: ~
externalUrl: ~
externalAssignee: ~
externalAssigneeId: ~
workType: ~
urgency: ~
impact: ~
appetiteMinutes: ~
estimateMinutes: 300
estimateDraftMinutes: 960
estimateP85Minutes: 1470
estimateBasis: one leaf, estimate dev, size, n=6, THIN, seed 20261104
actualMinutes: ~
templateId: ~
service: ~
decisionRef: decision:traceyield/0010
---

# Extract one run from the provider logs

## Goal

Success is one command that turns a session id and a time window into every lane, model turn and tool call of that run, for Claude and Codex alike. It reads the logs and never touches usage.db; persisting any of it is outside this feature.

## Definition of Done

_The conditions that make this shippable._

## Acceptance Criteria

- [ ] _First criterion_

## Test Strategy

_How delivery is verified. Design fills this: coverage layers, key scenarios,
and the pre-release check. Retired testing-plan docs fold in here (Decision 0041)._

## Notes

## Assets

_Optional. Research, scripts, backfill, and artifact docs (estimate.csv,
presentation-deck) live in the work-item's asset directory: this file's path
with the `.md` extension removed, so the directory carries the same -slug as
the companion and reads as its contents in a listing (Decision 0048,
amended 2026-08-19). Rename both together if the title changes._

## Activity Log


## 2026-11-25 14:35 UTC -- created
Work-item E4-F1 (feature) created with status planned.

## 2026-11-25 14:40 UTC -- status-rollup
Rolled up to in_progress from child delivery. delivery of E4-F1-S1 started
