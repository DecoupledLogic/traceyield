---
id: 01M89J84WQBEWVFZYY47PF782W
type: story
key: E6-F1-S4
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

# The drift check and pricing history work per model

## Goal

The pricing drift check and the stored pricing history both work per model version, so a wrong or changed rate is noticed for the model it belongs to.

## Definition of Done

_The conditions that make this shippable._

## Acceptance Criteria

- [ ] Scenario: drift per model. Given the rate card and a fetched pricing page, when the drift check runs, then each model entry is compared with its own published row and a mismatch names the model.
- [ ] Scenario: Fable checked as Fable. Given a Fable entry, when the drift check runs, then it is compared with the Fable row and not with the opus row.
- [ ] Scenario: history snapshot. Given a report run, when pricing is recorded, then the snapshot in `pricing_history.json` holds each model's input, output and cache-read rates.
- [ ] Scenario: old snapshots load. Given a `pricing_history.json` written before this change, when it is read, then it loads without error and its tier-shaped entries are still understood.
- [ ] Scenario: price table. Given a generated report, when its price table is read, then it lists the model versions actually priced with their rates.
- [ ] Scenario: offline. Given no network, when the report runs, then the drift check is skipped with the same message as today and the report is still produced.

## Test Strategy

_How delivery is verified. Design fills this: coverage layers, key scenarios,
and the pre-release check. Retired testing-plan docs fold in here (Decision 0041)._

## Notes

Planned from Decision 0012, which leaves the interaction with as-billed history (Decision 0002) and the drift check to design. Known tier-shaped code: `parse_pricing_page()` takes one published row per tier keyword, `pricing_history.json` snapshots hold input and output per tier, `build_html()` builds the price table and route estimator by tier, and `tests/test_pricing.py` and `tests/test_report.py` pin the Fable-to-opus mapping.

## Assets

_Optional. Research, scripts, backfill, and artifact docs (estimate.csv,
presentation-deck) live in the work-item's asset directory: this file's path
with the `.md` extension removed, so the directory carries the same -slug as
the companion and reads as its contents in a listing (Decision 0048,
amended 2026-08-19). Rename both together if the title changes._

## Activity Log


## 2026-11-25 14:37 UTC -- created
Work-item E6-F1-S4 (story) created with status planned.
