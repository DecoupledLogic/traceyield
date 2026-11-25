---
interview-policy: always
type: decision
number: "0011"
title: "Count each Claude model request once: merge records that repeat one request's usage"

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
      Shaped from request Q266, a rich source. The measurement behind it covers one session. The change is not built.

createdAt: 2026-11-25
updatedAt: 2026-11-25
removedAt: ~

author: agent-charles
owner: Charles
agent: agent-charles
strategicAlignment: 5
agencyValue: 4
clientValue: 4
userValue: 5
urgency: 5
viability: 5
feasibility: 4
deadlineWeight: 3
complexityPenalty: 3
sas: 4.20
---

# Decision 0011: Count Each Claude Model Request Once

## Status

Drafted. The report and the canonical store do not yet merge by request, and the change waits on this decision moving to Accepted.

## Problem

The report over-counts Claude cost. Claude Code 2.1.318 writes one assistant record per content block, and every record of one request repeats that request's full `usage`. `ClaudeProvider.parse_file()` yields a Turn for every record, and `aggregation.analyze()` dedupes on the record `uuid`, so one request is billed once per content block.

The over-count was found on 2026-11-25 while building run extraction (request Q265) and measured on session `ad425f62-2318-47b2-8a54-3f8c9b859997` (project `C--Repos-clientshield`), main thread, window 2026-11-24T16:37:44Z to 19:14:30Z:

- 231 assistant records for 110 distinct `message.id` / `requestId` values.
- 78 of the 110 requests were split over more than one record, and all 78 repeat identical usage on each record.
- Cost summed per record: $34.87. Cost with each request counted once: $16.29. Over-count 2.14x.

Only this one session was measured. How far back the per-block record shape goes is not known.

`traceyield.runs` already merges by request id. The report (`aggregation.py`) and `usage.db` (`canonical.py`, `providers/claude.py`) do not, so the same session costs differently depending on which path reads it.

The forces are that reported cost has to match what was billed, that history already merged into `daily_metrics.json` and `session_metrics.json` includes days whose transcripts no longer exist, and that a change to reported cost falls under the pricing and significant-change rules in CONTRIBUTING.

## Options Considered

### Option A (chosen): merge by request in the provider and in the report path

`ClaudeProvider.parse_file()` yields one Turn per model request, and `aggregation.analyze()` dedupes on the same request key instead of the record `uuid`.

- Pros: the canonical store and the report count a request the same way, so `analyze()` stays a valid equivalence oracle and fallback for `aggregate()` (Decision 0001). The rule matches `traceyield.runs`, which already merges by request.
- Cons: two code paths change together. Reported cost and turn counts change, so the CONTRIBUTING pricing and significant-change rules apply.

### Option B: fix only the canonical store and leave analyze()

Merge by request in `ClaudeProvider.parse_file()` and keep the `uuid` dedupe in `aggregation.analyze()`.

- Pros: a smaller change, and the live report path reads `usage.db`.
- Cons: `analyze()` keeps billing a request once per content block.
- Why not: `analyze()` is the fallback when the canonical path fails and the oracle that `aggregate()` is tested against. Left alone, a fallback run over-counts again and the two paths stop agreeing.

### Option C: divide stored history by an estimated factor

Restate the days whose transcripts have rotated away by dividing their stored figures by an estimate of the over-count.

- Pros: rotated days would sit on the same scale as corrected ones.
- Why not: the only measurement is one session, and how far back the per-block record shape goes is not known. A factor applied to days that cannot be reparsed replaces a known over-count with a figure nobody can check.

## Decision

A Claude turn is one model request.

Request key. A request is keyed on `requestId`, with `message.id` as the fallback and the record `uuid` as the last fallback. Records that share a key are one turn, and that request's usage is billed once.

Where the fix goes. It goes in the producer and in the report path: `ClaudeProvider.parse_file()` and `aggregation.analyze()`.

Turn content. Text and tool calls from every record of the request still attach to the one turn. `n_tool_calls` counts the request's calls.

History. Days whose transcripts still exist are corrected on the next run, because the report reparses them. Days whose transcripts have rotated away keep their stored figures. The report says from which date figures are request-deduplicated, so the two kinds of figure are never mixed silently. Restating rotated days by estimate is rejected.

## Consequences

### Positive

- Reported tokens and dollars count each request once, in the canonical store and in the report.
- The store, the report and the run document share one rule for what a turn is.
- Rotated history is labeled as it stands. No stored figure is replaced by an estimate.

### Negative

- Days whose transcripts have rotated away stay over-counted wherever the per-block record shape was in use, and the trend steps down at the date the report names.
- The size of the correction across history is unknown. One session was measured.
- `usage.db` keeps the turn rows it already ingested, one per record, and `canonical.write()` inserts turns with `INSERT OR IGNORE` on `turn_id`. The parser change alone does not remove those rows, so correcting the surviving days on the live `aggregate()` path depends on the design replacing them.

### Neutral

- Turn counts fall, because a turn is now a request. The measured session has 110 requests where it had 231 records.
- `n_tool_calls` on a turn can rise, because it now covers every record of the request.
- The change alters reported cost, so the pricing and significant-change rules in CONTRIBUTING apply.

## Traceability

- Intake request: `Q266` (tempo-portfolio, `docs/delivery/requests/Q266-traceyield-the-report-over-counts-claude-cost.md`).
- Found while building run extraction: request `Q265`, Decision 0010.
- Code in scope: `src/traceyield/providers/claude.py` (`ClaudeProvider.parse_file()`), `src/traceyield/aggregation.py` (`analyze()`).
- Evidence: the measurement of session `ad425f62-2318-47b2-8a54-3f8c9b859997` recorded in Q266.

## Related Decisions

- [0001](0001-aggregate-flip.md): `usage.db` is the aggregate source and `analyze()` stays as its oracle and fallback, which is why both paths take the fix.
- [0002](0002-as-billed-costing-per-activity-date-pricing.md): as-billed costing sets the rate applied to each activity date. This decision sets the quantity that rate is applied to.
- [0010](0010-run-extraction-json-from-provider-logs.md): `traceyield.runs` already merges by request. This decision brings the store and the report to the same rule.
