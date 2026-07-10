# TokenLens

Token-spend & health analytics for [Claude Code](https://claude.com/claude-code). One script parses your local Claude Code transcripts and produces a **self-contained, interactive HTML dashboard** — no server, no build step, no dependencies (Python stdlib only).

Because every transcript line is timestamped, a single run reconstructs your **entire history** bucketed by activity date. Day / week / month aggregation happens client-side in the report, so you can step through any period and watch trends move.

```
python report.py
# 30 active days (2026-05-28..2026-07-10) | $5,409.92 | 36,622 turns | 662/18524 tool errors (3.6%)
# 117 sessions | priciest $353.90 (kinderos)
# Report: report.html
```

Open `report.html` in any browser.

## What it shows

- **KPI cards** — cost, total tokens, assistant turns, sessions, and tool-error rate for the selected period, each with a delta vs. the previous period.
- **Trend chart** — step through day/week/month and switch the metric (cost, tokens, turns, error rate, sessions).
- **Cost breakdowns** — by project, by model tier, and the five-line **token composition** (fresh input, cache write 5m/1h, cache read, output) that shows where the money actually goes.
- **Per-session cost analysis** — a "Top sessions by cost" table across all history, to catch a single **runaway conversation** (usually a long, uncleared context that gets re-read every turn).
- **Model-routing savings estimator** — recomputes a period's **Opus** token usage at Sonnet/Haiku rates, scaled by a "how much is safely routable" input, to estimate what `/model` routing would save. Framed as an upper bound.
- **Tokens & cost per tool** — full turn cost attributed to its single tool (Claude Code serializes tool calls, so this is exact for tool turns), plus an **estimated-waste** column that puts a dollar figure on tool errors.
- **Error taxonomy & fixes** — tool failures classified into recurring patterns (Write/Edit-before-Read, Windows shell mismatches, stale edits, permission denials, …) each paired with a concrete remediation.
- **Model pricing over time** — rates are stamped daily so you can see how they moved; all historical cost is computed at *current* rates for apples-to-apples comparison.

The report has a built-in **"How to read this report"** section explaining the token economics and how to use the numbers to cut spend.

## Install & run

Requires Python 3 (stdlib only — nothing to `pip install`).

```bash
git clone https://github.com/DecoupledLogic/tokenlens.git
cd tokenlens
python report.py
```

It reads transcripts from `~/.claude/projects` and writes `report.html` next to the script.

### Run it daily (Windows)

`run.cmd` is a Task Scheduler wrapper that runs the report and appends a one-line summary to `run.log`. Point a daily scheduled task at it (edit the interpreter path and directory inside to match your machine):

```
schtasks /create /tn "TokenLens" /tr "C:\path\to\tokenlens\run.cmd" /sc daily /st 09:00
```

On macOS/Linux, wire `python report.py` into a `cron` job or `launchd` agent.

## How it works

Each run:

1. **Parses** every `*.jsonl` under `~/.claude/projects`, bucketing metrics by the UTC activity date of each message/tool-result. Malformed lines and files are skipped so one bad transcript can't abort a run.
2. **Merges** new day-buckets into `daily_metrics.json` and sessions into `session_metrics.json` — the newest parse is authoritative per date/session, and dates/sessions whose transcripts have since rotated away are preserved.
3. **Records** today's model pricing into `pricing_history.json`.
4. **Emits** `report.html` — the full dataset is inlined into a single self-contained file.

Everything lives in one file, `report.py`: config, parser, persistence, and the entire HTML/CSS/JS template.

### Pricing

Base per-1M-token rates live in the `PRICING` dict at the top of `report.py` — edit it when Anthropic changes prices. Cache multipliers are fixed by the API (read 0.1×, write-5m 1.25×, write-1h 2× the input rate). Changing `PRICING` recomputes *all* historical cost at the new rates, by design.

## Tests

Stdlib `unittest`, no dependencies:

```bash
python -m unittest test_report          # or: python -m pytest test_report.py -q
```

The suite builds fixture transcripts with hand-computable token counts and checks exact costs, session accumulation, the per-tier token breakdown the routing estimator depends on, merge semantics, the error taxonomy, and HTML generation.

## Files

| File | Role |
|------|------|
| `report.py` | The whole tool — parser, persistence, and HTML template |
| `test_report.py` | Test suite |
| `report.html` | Generated dashboard (open this) |
| `daily_metrics.json` | Durable per-day metrics store |
| `session_metrics.json` | Durable per-session metrics store |
| `pricing_history.json` | Daily snapshots of model pricing |
| `run.cmd` / `run.log` | Windows daily-runner wrapper and its log |

The JSON files and `report.html` are generated artifacts, committed alongside the code — regenerating them is expected.

---

Built by [DecoupledLogic](https://github.com/DecoupledLogic).
