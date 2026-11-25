---
id: 01M89J3J8592XX0VENM4AQ9CT1
type: story
key: E4-F1-S1
status: in_progress
stage: ~
stageSince: ~
needsDesign: no
assigneeId: ~
queue: ~
externalSystem: ~
externalId: ~
externalUrl: ~
externalAssignee: ~
externalAssigneeId: ~
workType: feature
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

# traceyield run emits a session, its subagents and Codex sessions as one JSON document

## Goal

`traceyield run` writes one JSON document for a run: each agent's model and reasoning effort, every model turn and tool call with its duration, cost, input and output, and the failed or refused calls grouped for a retrospective.

## Definition of Done

The command, its guide (`docs/guides/09-run-extraction.md`) and the CLAUDE.md entry are merged through a pull request with the `ci` check green, and Decision 0010 is Accepted.

## Acceptance Criteria

- [ ] Scenario: one request, one charge. Given a transcript that logs one model request as several records, each repeating the request's usage, when the run is extracted, then the request is one turn and its tokens and cost are counted once.
- [ ] Scenario: lanes. Given a session that spawned subagents, when the run is extracted, then the main thread and each subagent are separate lanes, each with its models, reasoning efforts, turn and call counts, tokens and estimated cost, and no local file path appears in the document.
- [ ] Scenario: Codex beside Claude. Given `--cwd` and a Codex session that started in that directory or below it inside the window, when the run is extracted, then that session is a lane with its turns and its tool calls, including calls logged as `custom_tool_call`. A Codex session in another directory or outside the window is left out, and without `--cwd` no Codex lane appears.
- [ ] Scenario: unpriced model. Given a lane whose model has no rate card, when the run is extracted, then its tokens are counted, its cost is 0 and the lane is marked not priced.
- [ ] Scenario: tool calls. Given a tool call and its result, when the run is extracted, then the call carries its duration, a one-line summary, its input and output, and separate flags for a failed call and a call refused by a permission check. A background command also carries the time until it actually finished.
- [ ] Scenario: window. Given records before the start or after the end, when the run is extracted, then they are left out, and a record a fraction of a second inside the window is kept.
- [ ] Scenario: text. Given text containing values that look like credentials, when the run is extracted, then those values are replaced with `[redacted]`, any field longer than the clip length is cut and flagged, and with `--no-text` no prompt, reply, input or output text appears while timings, tokens and costs stay.
- [ ] Scenario: retrospective input. Given failed and refused calls, when the run is extracted, then they are grouped by error class, most frequent first, each with the taxonomy's title and suggested fix.
- [ ] Scenario: nothing found. Given a session id with no activity in the window, when the command runs, then it exits with status 2 and writes no file.
- [ ] Scenario: store untouched. Given any run, when the command runs, then usage.db is neither read nor written and the report's output is unchanged.

## Test Strategy

- Unit, synthetic fixtures only (`tests/test_runs.py`, builders in `tests/helpers.py`): one test or more per scenario above, with hand-computed costs.
- Provider additions (`Turn.effort`, `Segment.ts`, Codex `custom_tool_call`) are asserted on the provider output directly.
- Full suite: `python -m unittest discover -s tests` passes, which covers the store-untouched scenario through the existing report and canonical tests.
- Pre-release check: run the command once against a real session and confirm the lane, call and error counts look right. Done on 2026-11-25 against session ad425f62: 4 lanes, 278 calls, 264 turns.

## Notes

Planned from Decision 0010 (request Q265). The code already exists, uncommitted, on branch `feature/run-extract` in worktree `.worktrees/run-extract`, built before this story was planned. Delivery is review, gate and pull request, not a rebuild.

`needsDesign: no` by operator-directed override of the one-day rule: the design is the one Decision 0010 records and the code embodies.

Cost in the document uses the current tier table, which Decision 0012 replaces. Until then Opus 5.5 and Sonnet 5.5 lanes are overstated.

## Assets

_Optional. Research, scripts, backfill, and artifact docs (estimate.csv,
presentation-deck) live in the work-item's asset directory: this file's path
with the `.md` extension removed, so the directory carries the same -slug as
the companion and reads as its contents in a listing (Decision 0048,
amended 2026-08-19). Rename both together if the title changes._

## Activity Log


## 2026-11-25 14:35 UTC -- created
Work-item E4-F1-S1 (story) created with status planned.

## 2026-11-25 14:40 UTC -- status-change
Status planned -> in_progress. delivery started
