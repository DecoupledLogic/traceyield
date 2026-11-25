---
id: 01M89J821050DKFR9Y6CPEH9KB
type: story
key: E6-F1-S1
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

# Claude turns are priced from a per-model rate card, with the tier as a flagged fallback

## Goal

A Claude turn is priced from the rate card entry for its own model version, and a version with no entry is priced by tier and reported as an estimate.

## Definition of Done

_The conditions that make this shippable._

## Acceptance Criteria

- [ ] Scenario: model entry. Given a turn on a model version that has a rate card entry, when it is costed, then input, output and cache-read tokens use that entry's rates, so an Opus 5.5 turn and an Opus 5 turn with the same tokens cost different amounts.
- [ ] Scenario: cache writes. Given any priced Claude turn, when it is costed, then 5-minute cache writes are 1.25x and 1-hour cache writes are 2x of that model's own input rate.
- [ ] Scenario: flagged fallback. Given a turn on a Claude version with no entry, when it is costed, then it is priced by its tier keyword and the report marks that cost as an estimate.
- [ ] Scenario: Fable. Given a Fable turn, when it is costed, then it uses its own entry and is never priced at the opus rate.
- [ ] Scenario: unchanged models. Given turns on models whose published rate equals today's tier rate, when they are costed, then their cost is the same as before this change.
- [ ] Scenario: one lookup. Given the same turn, when the report, the canonical aggregate and run extraction each cost it, then all three give the same figure.
- [ ] Scenario: verified rates. Given the entries shipped, when they are compared with Anthropic's pricing page as re-read at delivery, then every entry matches and the pull request records the date and URL of that read.
- [ ] Scenario: Sonnet comment. Given `pricing.py`, when it is read, then the Sonnet 5 rate is unchanged and its comment no longer calls it an introductory price.

## Test Strategy

_How delivery is verified. Design fills this: coverage layers, key scenarios,
and the pre-release check. Retired testing-plan docs fold in here (Decision 0041)._

## Notes

Planned from Decision 0012 (request Q269). Also closes request Q267: the Sonnet rate was verified correct on 2026-11-25 and only its comment is stale.

The rates in Q269 came from one automated read of the pricing page. They are inputs to check, not values to copy.

## Assets

_Optional. Research, scripts, backfill, and artifact docs (estimate.csv,
presentation-deck) live in the work-item's asset directory: this file's path
with the `.md` extension removed, so the directory carries the same -slug as
the companion and reads as its contents in a listing (Decision 0048,
amended 2026-08-19). Rename both together if the title changes._

## Activity Log


## 2026-11-25 14:37 UTC -- created
Work-item E6-F1-S1 (story) created with status planned.
