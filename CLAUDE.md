# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-file Python tool that parses Claude Code's own transcript logs and produces a self-contained, interactive HTML usage/health dashboard. No dependencies, no build step, no framework — just `report.py` (stdlib only) plus the JSON data files it maintains and the `report.html` it emits.

## Commands

```bash
python report.py          # parse transcripts, merge data, regenerate report.html
```

- `run.cmd` is the scheduled-task wrapper (uses the Anaconda Python at `C:\Users\charl\anaconda3\python.exe`) and appends a one-line summary to `run.log` each run. It runs daily via Windows Task Scheduler.
- There are no tests, no linter, and no package manifest. The only "check" is running the script and confirming the printed summary line (active days, total cost, turns, tool-error rate) looks sane and `report.html` opens.

## Data flow (one `python report.py` run)

1. **Parse** — glob every `*.jsonl` under `~/.claude/projects` (`CLAUDE_PROJECTS`). Each transcript line is timestamped; metrics are bucketed by the UTC **activity date** (`timestamp[:10]`), not by run date. This is why a single run can reconstruct the entire history.
2. **Merge** — `merge_daily()` folds new day-buckets into `daily_metrics.json` with `dict.update` semantics: **the newest parse is authoritative per date, but dates whose transcripts have since been rotated away are preserved.** This file is the durable store; never regenerate it from scratch expecting old dates to survive if the source transcripts are gone.
3. **Record pricing** — `record_pricing()` stamps today's `PRICING` table into `pricing_history.json` (keyed by today's date), building a time series of the rates themselves.
4. **Emit** — `build_html()` inlines the full payload (`days`, error `meta`, `pricing_history`) into the `HTML_TMPL` string via `__PAYLOAD__` / `__PRICEROWS__` placeholders and writes `report.html`.

All aggregation into **day / week / month** views happens **client-side** in the emitted HTML's JavaScript (`aggregate()`), so the Python side only ever produces per-day buckets. Adding a new time granularity or trend metric is a JS change in `HTML_TMPL`, not a Python change.

## Key domain logic (all in report.py)

- **`PRICING` (per-1M-token base rates)** — edit this dict when Anthropic changes prices. Cache multipliers are fixed by the API and applied in `cache_rates()`: read = 0.1×, write-5m = 1.25×, write-1h = 2× the input rate. **All historical cost is (re)computed at the *current* rates** for apples-to-apples comparison; the pricing-history chart is the only place that shows how rates actually moved over time. So changing `PRICING` retroactively changes every day's reported cost — that's intentional.
- **`tier(model)`** — maps a raw model id to `opus` / `sonnet` / `haiku`. Note `fable` maps to the `opus` tier. Anything unrecognized returns `None` and that usage row is skipped entirely (no cost attributed).
- **Cost per token** — five line items: fresh input (1×), cache write-5m (1.25×), cache write-1h (2×), cache read (0.1×), output. When the transcript only gives an aggregate `cache_creation_input_tokens` with no 5m/1h breakdown, it's all attributed to 5m.
- **Per-tool cost attribution** — Claude Code serializes tool calls (~100% single-tool turns), so a turn's entire cost + output is attributed to its one tool. Multi-tool or no-tool turns land in the pseudo-rows `(multi-tool turn)` / `(final response)`. This is exact for single-tool turns and explicitly documented as such in the report UI.
- **`ERROR_RULES` — the error taxonomy.** An ordered list of `(name, substrings, title, fix)`. `classify()` lowercases tool-result text and returns the first rule whose substrings match, else `"other"`. To add/refine an error category, add a tuple here — `ERROR_META` and the report's "errors & fixes" table derive from it automatically. Rules are matched top-to-bottom, so order matters when substrings could overlap.

## Conventions & gotchas

- **Windows-first.** Paths, the `run.cmd` launcher, and the hardcoded Anaconda interpreter path all assume this specific Windows machine. The `clean()` JS helper strips the `C--Users-charl-source-repos-` project-dir prefix for display.
- **Everything is one file.** `report.py` holds config, parser, persistence, and the entire HTML/CSS/JS template (`HTML_TMPL`, a raw string). There is no templating engine — edits to the dashboard are string edits inside that literal. Keep the `__PAYLOAD__` / `__PRICEROWS__` placeholders intact.
- **Resilient parsing by design.** The parse loop swallows per-line and per-file exceptions (`except: continue`) so one malformed transcript can't abort a run. Be careful adding logic that depends on every line succeeding.
- `daily_metrics.json`, `pricing_history.json`, `report.html`, and `run.log` are **generated artifacts** committed alongside the code — regenerating them is expected, not a mistake.
