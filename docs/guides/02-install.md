# 2. Install and first run

## Prerequisites

- **Python 3.** That is the only requirement. The tool uses the standard library only, so there is nothing to `pip install`, no virtualenv to create, and no lockfile to resolve.
- **A coding agent that has produced transcripts.** traceyield reads the transcript logs your coding agents write locally (for example `~/.claude/projects` for Claude Code, `~/.codex` for Codex). If you have used a supported agent on this machine, those logs already exist. The report's **Provider** selector is the live list of what it ingested; see [guide 1](./01-overview.md).
- **Git**, to clone the repository and to pull updates later.

Check Python is available:

```bash
python --version      # or: python3 --version
```

If `python` is not found but `python3` is, use `python3` in every command below. On Windows, `python` is the usual name; on many macOS and Linux setups it is `python3`. See [guide 5](./05-troubleshooting.md) if neither works.

## Install

```bash
git clone https://github.com/DecoupledLogic/traceyield.git
cd traceyield
python report.py
```

That is the whole install. The first run parses your transcripts, writes your report, and prints a one-line summary.

## What the first run prints

You will see something like this:

```
Machine: dt-6cpyln3 -> .../machines/dt-6cpyln3
30 active days (2026-05-28..2026-07-10) | $5,409.92 | 36,622 turns | 662/18524 tool errors (3.6%)
117 sessions | priciest $353.90 (kinderos)
Report: machines/dt-6cpyln3/report.html
```

Line by line:

- **Machine** is your machine's folder. traceyield names it from your sanitized hostname (here `dt-6cpyln3`). All of this machine's output lives under that folder.
- **The summary line** is your headline numbers for the whole history it could reconstruct: active days and their date range, total cost, total assistant turns, and the tool-error count and rate.
- **Sessions** is how many distinct conversations it found, and the single priciest one (with its project).
- **Report** is the file to open.

If the summary looks sane and the report opens, the install worked. Treat that printed line as your smoke test on every run.

## Open the report

Open the `report.html` under your machine's folder in any browser. There is nothing to serve; it is a single self-contained file.

```bash
# macOS
open machines/<your-machine>/report.html
# Windows (PowerShell)
start machines\<your-machine>\report.html
# Linux
xdg-open machines/<your-machine>/report.html
```

Replace `<your-machine>` with the folder name from the "Machine:" line. If you are not sure what it is, ask the tool:

```bash
python report.py --machine-dir
```

That prints the resolved per-machine directory and exits without parsing anything.

## Where your data lives (per-machine)

This matters if you use more than one computer. The repository is meant to be shared across machines, but each machine has its own `~/.claude/projects`, so each machine's derived files are namespaced under `machines/<machine-id>/`:

| File | What it is |
|------|-----------|
| `machines/<id>/report.html` | Your dashboard. Open this. |
| `machines/<id>/daily_metrics.json` | The durable per-day metrics store. |
| `machines/<id>/session_metrics.json` | The durable per-session metrics store. |
| `machines/<id>/health.json` | The last data-health snapshot. |
| `machines/<id>/run.log` | The scheduled-runner log (see [guide 8](./08-daily-run-automation.md)). |

Everything under `machines/` is **git-ignored**, so it stays local to your machine and cloning the repository never carries anyone else's usage data. The one generated file that *is* committed is `pricing_history.json` at the repository root, because it is non-personal (dates and public rates) and shared so the pricing chart has history on a fresh clone.

### Overriding the machine folder

By default the folder name is your sanitized hostname. Set the `TRACEYIELD_MACHINE` environment variable to override it, for example to point a renamed machine at an existing folder so its history carries over:

```bash
# macOS / Linux
TRACEYIELD_MACHINE=my-laptop python report.py
# Windows (PowerShell)
$env:TRACEYIELD_MACHINE = "my-laptop"; python report.py
```

## Platform notes

- **Windows** is the primary target. The commands above work in PowerShell. There is also `run.cmd` for daily scheduling; see [guide 8](./08-daily-run-automation.md).
- **macOS and Linux** are fully supported: run `python report.py` and wire it into `cron` or `launchd` for daily runs.

## Verify (optional)

If you want to confirm the tool itself is healthy, run its test suite. It is standard-library only, so it needs no setup:

```bash
python -m unittest test_report test_canonical
```

Green means the cost math, merge logic, error taxonomy, and report generation all behave.

## Next step

Go to [3. Using the report](./03-using-the-report.md).
