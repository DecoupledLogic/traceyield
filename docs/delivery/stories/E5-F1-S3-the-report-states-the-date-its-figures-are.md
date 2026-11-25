---
id: 01M89J42WB04NK7FYHSY0YPPYX
type: story
key: E5-F1-S3
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
estimateDraftMinutes: 480
estimateP85Minutes: 990
estimateBasis: estimate dev, size, n=9, THIN, seed 20261104
actualMinutes: ~
templateId: ~
service: ~
---

# The report states the date its figures are request-deduplicated from

## Goal

A reader of the report can tell which days count each request once and which still hold older per-record figures.

## Definition of Done

_The conditions that make this shippable._

## Acceptance Criteria

- [ ] Scenario: mixed history. Given stored days of both kinds, when the report is generated, then it states the date from which figures are request-deduplicated.
- [ ] Scenario: clean history. Given a store where every day is request-deduplicated, when the report is generated, then no caveat appears.
- [ ] Scenario: stored day corrected. Given a day in `daily_metrics.json` and `session_metrics.json` whose transcripts still exist, when the report runs, then that day's stored figures are replaced with the corrected ones.
- [ ] Scenario: stored day kept. Given a stored day whose transcripts have rotated away, when the report runs, then its figures are unchanged and it falls before the stated date.

## Test Strategy

_How delivery is verified. Design fills this: coverage layers, key scenarios,
and the pre-release check. Retired testing-plan docs fold in here (Decision 0041)._

## Notes

Planned from Decision 0011, which rejects restating rotated days by estimate. How the report determines the date is left to design.

## Assets

_Optional. Research, scripts, backfill, and artifact docs (estimate.csv,
presentation-deck) live in the work-item's asset directory: this file's path
with the `.md` extension removed, so the directory carries the same -slug as
the companion and reads as its contents in a listing (Decision 0048,
amended 2026-08-19). Rename both together if the title changes._

## Activity Log


## 2026-11-25 14:35 UTC -- created
Work-item E5-F1-S3 (story) created with status planned.
