---
id: 01M89J40ACV7RVHB0N6XCDJCMF
type: story
key: E5-F1-S1
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

# The Claude provider and analyze() yield one turn per model request

## Goal

The Claude provider and `analyze()` treat one model request as one turn, so its usage is billed once however many records the log wrote for it.

## Definition of Done

_The conditions that make this shippable._

## Acceptance Criteria

- [ ] Scenario: split request. Given a transcript where one request is logged as several records that each repeat the same usage, when `ClaudeProvider.parse_file()` reads it, then it yields exactly one Turn for that request carrying the usage once.
- [ ] Scenario: request key. Given records, when they are grouped, then the key is `requestId`, else `message.id`, else the record `uuid`, so a record with neither id is still one turn as it is today.
- [ ] Scenario: content kept. Given a request split over a text record and a tool-use record, when it is parsed, then the reply text and every tool call attach to the one turn and `n_tool_calls` counts all of the request's calls.
- [ ] Scenario: both paths agree. Given the same transcript, when `analyze()` and `aggregate()` over a freshly built store each total it, then tokens, cost and turn counts are identical.
- [ ] Scenario: replay. Given a resumed transcript that repeats a request already seen, when it is parsed, then the request is not billed a second time.
- [ ] Scenario: measured case. Given a fixture shaped like the measured session (several requests, most split over two or three records), when it is totalled, then the cost equals the hand-computed once-per-request figure and not the per-record sum.

## Test Strategy

_How delivery is verified. Design fills this: coverage layers, key scenarios,
and the pre-release check. Retired testing-plan docs fold in here (Decision 0041)._

## Notes

Planned from Decision 0011 (request Q266). `traceyield.runs` already merges by request id; once this lands it can drop its own merge.

## Assets

_Optional. Research, scripts, backfill, and artifact docs (estimate.csv,
presentation-deck) live in the work-item's asset directory: this file's path
with the `.md` extension removed, so the directory carries the same -slug as
the companion and reads as its contents in a listing (Decision 0048,
amended 2026-08-19). Rename both together if the title changes._

## Activity Log


## 2026-11-25 14:35 UTC -- created
Work-item E5-F1-S1 (story) created with status planned.
