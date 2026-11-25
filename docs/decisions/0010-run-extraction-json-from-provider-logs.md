---
interview-policy: always
type: decision
number: "0010"
title: "Run extraction: one session, its subagents and Codex sessions as a JSON document read from the provider logs"

agency: "DecoupledLogic"
client: "Charles"
project: "traceyield"
product: ~
service: ~
workItem: ~

status: Drafted
framing: short-circuited
supersededBy: ~
statusHistory:
  - status: Drafted
    date: 2026-11-25
    user: agent-charles
    note: >-
      Shaped from request Q265, a rich source. The work is already built on branch feature/run-extract. This record states the decision it embodies.

createdAt: 2026-11-25
updatedAt: 2026-11-25
removedAt: ~

author: agent-charles
owner: Charles
agent: agent-charles
strategicAlignment: 4
agencyValue: 4
clientValue: 3
userValue: 4
urgency: 3
viability: 5
feasibility: 5
deadlineWeight: 2
complexityPenalty: 2
sas: 3.65
---

# Decision 0010: Run Extraction as a JSON Document Read from the Provider Logs

## Status

Drafted. The implementation exists on branch feature/run-extract and waits on this decision moving to Accepted before the pull request merges.

## Problem

A reviewer of a delivery run has no single artifact that shows what the agents did. The change-record prototype session on 2026-11-25 (Tempo `docs/guides/change-record/`) needs, for one run, each agent's model and reasoning effort, every model turn and tool call with its duration, cost, input and output, and the failed or refused calls grouped for a retrospective. It also needs Codex sessions beside the Claude ones.

TraceYield cannot supply that today. The default structural capture in `usage.db` does not hold per-call detail, and it has no column for reasoning effort. Reading the raw logs by hand does not scale, and the Claude main thread, its subagent files and the Codex rollouts live in different places and formats.

The forces are the privacy of prompt and tool text, the existing rule that `usage.db` is the aggregate source (Decision 0001), and the stdlib-only runtime.

## Options Considered

### Option A (chosen): a read-only run command over the provider logs

A new `traceyield run` command assembles one JSON document for a single run directly from the providers' `parse_file()` stream, without reading or writing `usage.db`. A run is a Claude session id with a time window, plus any Codex rollouts whose working directory falls under a given path inside that window.

- Pros: no schema change and no migration. It reuses the provider parsers, so Claude and Codex share one neutral record shape. Per-call detail and effort are available without widening what the scheduled capture stores.
- Cons: it re-reads the logs on every run instead of querying stored rows. Effort is available only while the source logs exist.

### Option B: persist per-call detail and effort into usage.db

Widen the capture so a run can be rebuilt from stored rows.

- Pros: fast queries, and the data survives log retention.
- Cons: a schema change, a larger database, and prompt and tool text stored by default, which Decision 0004 treats as the data to age out.
- Why not: the privacy and retention cost lands on every user for a need that arises per run. Persisting `effort` stays a separate schema change, outside this decision.

### Option C: status quo

Reviewers read the raw logs or go without per-call detail.

- Why not: the change-record page cannot be built, and Codex runs stay invisible beside Claude ones.

## Decision

TraceYield adds `traceyield run --session <id> --start <iso> --end <iso> [--cwd <dir>] [--no-text] [--clip N] [--out <file>]`, backed by the new module `src/traceyield/runs.py`.

Lanes. Each provider exposes `run_lanes()`. For Claude the lanes are the main thread plus `subagents/agent-*.jsonl`. For Codex they are the rollouts whose `session_meta.cwd` is under `--cwd` and that fall inside the window.

Neutral records. `Turn.effort` and `Segment.ts` are added to the neutral records and are not `usage.db` columns. The Codex provider now models `custom_tool_call` and `custom_tool_call_output`, which newer Codex CLIs use for `exec`.

Failures. Failed or refused calls are grouped by `classification.ERROR_META` with the suggested fix, for use by `/retro`.

Privacy. The document carries prompt, reply and tool text, clipped to 4,000 characters per field by default, with values that look like credentials replaced by a pattern match. It is an explicit command and is never part of the scheduled report. `--no-text` drops all text.

The command is documented in `docs/guides/09-run-extraction.md`. Because it is a new command, the significant-change list in CONTRIBUTING applies.

Out of scope: persisting `effort` to `usage.db`, which is a separate schema change.

## Consequences

### Positive

- One document answers what a run did, per agent and per call, for Claude and Codex alike.
- No schema change, so existing databases and reports are untouched.
- The failure grouping feeds the retrospective directly from the existing error taxonomy.

### Negative

- Credential redaction is a pattern match, so an unusual secret can pass through. The document must be treated as sensitive and shared deliberately.
- Output depends on the provider logs still existing, and on the new Codex tool-call shapes staying stable.

### Neutral

- The run document and `usage.db` can disagree on cost for the same session, because they are built by different paths. The run document is a per-call view, not an aggregate.

## Traceability

- Intake request: `Q265` (tempo-portfolio, `docs/delivery/requests/Q265-traceyield-extract-one-run-session-subagents.md`).
- Implementation: branch `feature/run-extract`, worktree `.worktrees/run-extract` (323 tests pass, 33 of them new).
- Guide: `docs/guides/09-run-extraction.md`.
- Origin: the Tempo change-record prototype, `docs/guides/change-record/`.

## Related Decisions

- [0001](0001-aggregate-flip.md): `usage.db` stays the aggregate source, which this command does not touch.
- [0004](0004-raw-event-retention-archive-before-null.md): why per-call text is not persisted by default.
- [0007](0007-cross-provider-reporting-per-provider-rate-cards.md): the provider facet and per-provider costing the Codex lanes rely on.
