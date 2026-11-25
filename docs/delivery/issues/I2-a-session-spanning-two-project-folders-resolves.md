---
id: 01M89KPGCAERRGBH51MKR664AT
type: issue
key: I2
status: in_review
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
workType: bug
urgency: ~
impact: ~
appetiteMinutes: ~
estimateMinutes: 195
estimateDraftMinutes: 120
estimateP85Minutes: 345
estimateBasis: estimate dev, size, n=10, THIN, seed 20261104
actualMinutes: ~
templateId: ~
service: ~
---

# A session spanning two project folders resolves to a project that depends on filesystem order

## Goal

A session with turns in two project folders resolves to the same project whatever order the filesystem lists the folders in.

## Definition of Done

The fix and its test are merged through a pull request with the `ci` check green on the Linux runner, and `ci` passes on `main` afterwards.

## Acceptance Criteria

- [ ] Scenario: reversed listing, report path. Given a session with turns under `projA` and `projB` and a directory listing returned in reverse order, when `analyze()` runs, then the session's project is `projA`.
- [ ] Scenario: reversed listing, store path. Given the same transcripts and a reversed listing, when `canonical.ingest()` runs into a fresh store and `aggregate()` reads it, then the session's project is `projA` and the result equals `analyze()`.
- [ ] Scenario: existing behaviour kept. Given the existing suite, when it runs, then every test passes, including `test_equivalence_session_project_first_wins_across_files` on the Linux `ci` runner.

## Test Strategy

- Unit: one new test in `tests/test_aggregation.py` that patches `glob.glob` to return its result reversed and asserts both paths still resolve `projA`. It fails before the fix on any platform.
- Full suite locally (`python -m unittest discover -s tests`).
- Pre-release check: the `ci` check on the pull request, which runs on Linux where the failure occurs.

## Notes

Planned from inline decision Q270 (fast-track). `ci` failed on PR #41 and on `main` at 20e49af with `'projB' != 'projA'`; neither run changed a source file the test exercises.

The rule this makes deterministic is "first in path order". Whether first should mean earliest timestamp is a separate question and is not changed here.

## Assets

_Optional. Research, scripts, backfill, and artifact docs (estimate.csv,
presentation-deck) live in the work-item's asset directory: this file's path
with the `.md` extension removed, so the directory carries the same -slug as
the companion and reads as its contents in a listing (Decision 0048,
amended 2026-08-19). Rename both together if the title changes._

## Activity Log


## 2026-11-25 15:03 UTC -- created
Work-item I2 (issue) created with status planned.

## 2026-11-25 15:03 UTC -- status-change
Status planned -> in_progress. delivery started

## 2026-11-25 15:16 UTC -- status-change
Status in_progress -> in_review. PR #42 opened

## 2026-11-25 15:16 UTC -- note
ci passed on PR #42 on the Linux runner (run 45643066899), which is the proof for the third acceptance criterion. Dev was done inline, not by dev-agent. The Codex pre-PR review had not finished when the PR opened. No automated PR review was requested.
