# 4. Keeping it updated

Updating traceyield is just `git pull`, but there are two things worth understanding so an update never costs you data: how your **durable stores** behave, and how **pricing** works.

## Update the tool

```bash
cd traceyield
git pull
python report.py
```

Because the tool is a single Python file plus its data, there is nothing to rebuild and nothing to reinstall. Pull, run, done. If the test suite matters to you, run it after pulling:

```bash
python -m unittest discover -s tests
```

## What happens to your data on update

Your usage data is **not** in the repository, so pulling never touches it. It lives under `machines/<your-machine>/`, which is git-ignored and local to your machine. An update changes `report.py` and the tests; it leaves your `daily_metrics.json` and `session_metrics.json` alone.

### How the durable stores merge

Each run parses whatever transcripts still exist and folds the results into the on-disk stores:

- `daily_metrics.json` merges **per date**: the newest parse wins for a date, and dates whose transcripts have since been rotated away are **preserved**, not dropped.
- `session_metrics.json` merges the same way, **per session id**.

So a normal run only ever *adds to* or *refreshes* your history. It never regenerates from scratch and never deletes old dates just because their source transcripts are gone.

## The sharp edge: your history has no backup

This is the one thing to internalize. Coding agents rotate (delete) old transcripts over time. traceyield can only reconstruct history from transcripts that still exist. Once a date's transcripts are gone, the **only** remaining copy of that date is in your local `daily_metrics.json` (and `session_metrics.json`). Those files are git-ignored, so there is no copy in the repository either.

The consequences:

- A fresh clone on a new machine starts with only the history its transcripts still hold.
- If you delete `machines/<id>/`, wipe the disk, or lose those two JSON files **after** the underlying transcripts have rotated, that pre-rotation history is **gone**.

**Recommendation: back up your durable stores out of band.** Copy `machines/<your-machine>/daily_metrics.json` and `session_metrics.json` somewhere safe (a synced folder, a backup drive) on a schedule. If you are moving to a new machine or a fresh clone and want to keep your history, copy your old `machines/<id>/` folder into place **before** the first run, so the merge folds live data onto your preserved history instead of starting empty.

## Pricing: how rates work and when to edit them

All cost in the report is computed from a hand-maintained `PRICING` table inside `report.py`. Two things follow from that:

1. **All historical cost is computed at the current rates.** Change a rate and every past day's reported cost changes with it. This is intentional: it keeps every period comparable at one set of prices. The only panel that shows how rates actually moved over time is **Model pricing (tracked daily)**, which reads the separate, committed `pricing_history.json`.
2. **The tool checks itself but never silently rewrites prices.** After writing the report, traceyield fetches the vendor's public pricing page and warns you on stdout if any tier in `PRICING` no longer matches. This is a *drift alarm*, not a live price feed. A bad or offline scrape degrades to a "skipped" note rather than a false alarm, and it never edits `PRICING`, because silently rewriting prices would silently rewrite all of your history.

### When you should edit PRICING

Edit the `PRICING` dict at the top of `report.py` when the vendor actually changes prices and the drift check warns you. It is a small dict of per-1M-token base rates by model tier. Cache multipliers are fixed by the API (read 0.1x, write-5m 1.25x, write-1h 2x the input rate) and are applied automatically, so you only maintain the base rates. After editing, rerun `python report.py`; the new rates apply to all history and today's rates are stamped into `pricing_history.json`.

If you maintain a local edit to `PRICING`, note that `git pull` may bring an upstream change to the same table. Resolve it toward the correct current rates.

## Next step

If an update or a run surprised you, go to [5. Troubleshooting](./05-troubleshooting.md).
