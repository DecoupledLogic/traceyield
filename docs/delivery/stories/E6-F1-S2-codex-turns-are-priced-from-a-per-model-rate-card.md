---
id: 01M89J82YZGDM16538735RZ4N0
type: story
key: E6-F1-S2
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

# Codex turns are priced from a per-model rate card, including gpt-6.2-sol

## Goal

A Codex turn is priced from the rate card entry for its own model, so usage on gpt-6.2-sol shows a dollar figure.

## Definition of Done

_The conditions that make this shippable._

## Acceptance Criteria

- [ ] Scenario: new model. Given a turn on `gpt-6.2-sol`, when it is costed, then fresh input, cached input and output each use that model's published rate, and cached input is not priced at the provider-wide 0.1x.
- [ ] Scenario: existing models. Given turns on `gpt-5.3-codex`, `gpt-5.5` and `gpt-5.5-pro`, when they are costed, then their cost is the same as before this change.
- [ ] Scenario: unlisted model. Given a turn on a Codex model with no entry, when it is costed, then its tokens are counted and its cost is 0, as Decision 0007 D3 requires.
- [ ] Scenario: run extraction. Given a run with a `gpt-6.2-sol` Codex lane, when it is extracted, then the lane is marked priced and carries a cost.
- [ ] Scenario: verified rates. Given the entries shipped, when they are compared with OpenAI's pricing page as re-read at delivery, then every entry matches and the pull request records the date and URL of that read.

## Test Strategy

_How delivery is verified. Design fills this: coverage layers, key scenarios,
and the pre-release check. Retired testing-plan docs fold in here (Decision 0041)._

## Notes

Planned from Decision 0012. Also closes request Q268 (gpt-6.2-sol has no rate card).

## Assets

_Optional. Research, scripts, backfill, and artifact docs (estimate.csv,
presentation-deck) live in the work-item's asset directory: this file's path
with the `.md` extension removed, so the directory carries the same -slug as
the companion and reads as its contents in a listing (Decision 0048,
amended 2026-08-19). Rename both together if the title changes._

## Activity Log


## 2026-11-25 14:37 UTC -- created
Work-item E6-F1-S2 (story) created with status planned.
