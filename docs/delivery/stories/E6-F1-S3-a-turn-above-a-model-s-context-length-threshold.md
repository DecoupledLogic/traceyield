---
id: 01M89J83X98WTEGYT2QAR2ZK56
type: story
key: E6-F1-S3
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

# A turn above a model's context-length threshold is priced at the higher rate

## Goal

A turn whose prompt is larger than its model's context-length threshold is priced at that model's higher rate.

## Definition of Done

_The conditions that make this shippable._

## Acceptance Criteria

- [ ] Scenario: below the threshold. Given a turn on a model with a context-length price step and a prompt at or below the threshold, when it is costed, then the lower rate applies.
- [ ] Scenario: above the threshold. Given the same model and a prompt above the threshold, when the turn is costed, then the higher rate applies to that turn.
- [ ] Scenario: per turn. Given a session with turns on both sides of the threshold, when it is costed, then each turn is priced from its own prompt size.
- [ ] Scenario: no step. Given a model with no context-length step, when its turns are costed, then prompt size has no effect on the rate.
- [ ] Scenario: prompt size defined. Given a turn, when its prompt size is computed, then the rule for which token counts make it up is the one the provider's page states, written down in the code and covered by a test at the boundary.

## Test Strategy

_How delivery is verified. Design fills this: coverage layers, key scenarios,
and the pre-release check. Retired testing-plan docs fold in here (Decision 0041)._

## Notes

Planned from Decision 0012. Q269 gives the thresholds (Haiku 5.5 at 100,000 prompt tokens, gpt-6.2-sol at 272,000 input tokens) but not which token counts make up a turn's prompt size or whether the higher rate covers the whole turn. The delivery re-read settles both.

## Assets

_Optional. Research, scripts, backfill, and artifact docs (estimate.csv,
presentation-deck) live in the work-item's asset directory: this file's path
with the `.md` extension removed, so the directory carries the same -slug as
the companion and reads as its contents in a listing (Decision 0048,
amended 2026-08-19). Rename both together if the title changes._

## Activity Log


## 2026-11-25 14:37 UTC -- created
Work-item E6-F1-S3 (story) created with status planned.
