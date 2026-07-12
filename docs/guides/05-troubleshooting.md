# 5. Troubleshooting

Symptom, cause, fix. Find your symptom, apply the fix, rerun `python report.py`, and re-read the printed summary line and the **Data health** panel.

## The run itself

### "python: command not found" or nothing happens

**Cause.** Python 3 is not on your PATH under the name you used. On many macOS and Linux systems the name is `python3`, not `python`.

**Fix.** Try `python3 report.py`. Confirm with `python3 --version`. On Windows, if `python` opens the Microsoft Store instead of running, install Python from python.org or point at your interpreter directly, for example `C:\Users\you\anaconda3\python.exe report.py`.

### It ran but printed almost nothing / "0 active days"

**Cause.** No transcripts were found to parse. Either this machine has not used the agent yet, or the transcripts are not where the tool looks.

**Fix.** Confirm `~/.claude/projects` exists and contains `*.jsonl` files. If you keep transcripts elsewhere, the tool reads the standard location; there is no history to report until there is transcript data on **this** machine. Remember history is per-machine: a machine you have not used the agent on will have an empty report even if another machine is busy.

### It ran but the report file is not where I expected

**Cause.** The report is written under your per-machine folder, not the repository root.

**Fix.** Run `python report.py --machine-dir` to print the exact folder, then open `report.html` inside it. The name comes from your sanitized hostname unless you set `TRACEYIELD_MACHINE`.

## Wrong or missing data

### The numbers look too low, or old dates disappeared

**Cause.** Coding agents rotate (delete) old transcripts. traceyield can only reconstruct history from transcripts that still exist; once they are gone, the only copy is your local `daily_metrics.json` / `session_metrics.json`. If those were lost (fresh clone, disk wipe, stray delete) after the transcripts rotated, that history cannot be rebuilt.

**Fix.** Restore your `machines/<id>/` folder from a backup if you have one, and going forward back those files up out of band. See the "sharp edge" section in [guide 4](./04-updating.md). This is a data-durability issue, not a bug: a normal run preserves existing dates but can only *add* what transcripts still hold.

### I run on several machines and each shows only part of my usage

**Cause.** This is by design. Data is per-machine because each machine has its own transcripts. There is no cloud aggregation.

**Fix.** Run the report on each machine and open each machine's own report. To make a renamed machine reuse an existing folder's history, set `TRACEYIELD_MACHINE` to that folder's name. See [guide 8](./08-daily-run-automation.md).

## Reading the Data health panel

The **Data health** panel (top and bottom of the report, and warnings on stdout) is the tool checking its own data quality. Treat a warning there as "verify before trusting the numbers above."

### "SCHEMA DRIFT" warning

**Cause.** The vendor changed the shape of their logs: a new field, a new model id, or a new event type the parser has not been told about. The parser is resilient and will not crash, but an unrecognized model can mean usage rows are dropped and **under-counted** rather than mis-counted.

**Fix (users).** Note the warning and treat recent numbers with caution, especially if you also see a suspicious "$0 cost" note. Then update, in case a newer version already recognizes the change: `git pull`. If it persists, it is a signal for a maintainer to update the tool's baseline and pricing map; see [guide 6](./06-feature-reference.md) and the contributor docs.

### "tool activity but $0 cost" (suspicious)

**Cause.** There was activity on a date but no cost was attributed, usually because usage rows were dropped for an unrecognized model, that is, schema drift in disguise.

**Fix.** Same as schema drift above: `git pull`, and if it persists it needs a maintainer to map the new model. Do not trust the cost for affected dates until then.

### Coverage holes / "calendar gap day(s) with no usage"

**Cause.** There are dates inside your active range with no recorded usage. Often this is simply an idle day (you did not use the agent), which is normal. Sometimes it points to a real hole.

**Fix.** If you genuinely did not work those days, ignore it; the panel calls idle days normal. If you know you worked and there is a gap, the transcripts for those dates may have rotated away before a run captured them. Running the report more often (see [guide 8](./08-daily-run-automation.md)) closes this window.

## Pricing warnings on stdout

### "PRICING drift vs Anthropic pricing page"

**Cause.** The vendor's published rates no longer match the `PRICING` table in `report.py`. This can be a real price change, or an intro price lapsing.

**Fix.** First `git pull`, in case the rates are already corrected upstream. If not, edit the `PRICING` table to the correct current rates and rerun. Remember this recomputes all historical cost at the new rates, by design. See [guide 4](./04-updating.md).

### "pricing drift check skipped"

**Cause.** The tool could not fetch or parse the pricing page (offline, or the page layout changed). This is best-effort and deliberately fails safe.

**Fix.** Nothing required. Your report is unaffected; the tool simply did not verify prices this run. It will try again next run.

## Scheduled runs (daily task)

### The daily task does not produce a report or log

**Cause.** Usually the interpreter is not found in the task's environment, or the task points at the wrong path.

**Fix.** If `python` is not on the task's PATH, set the `PYTHON` environment variable to your interpreter before scheduling (for example `set PYTHON=C:\Users\you\anaconda3\python.exe`). Confirm the task points at `run.cmd`, which self-locates the repository. Check `machines/<id>/run.log` for the one-line summary each run appends. Full setup is in [guide 8](./08-daily-run-automation.md).

## When in doubt

The fastest general reset: `git pull`, then `python report.py`, then read the printed summary line and the Data health panel. Most issues are either "run it on the right machine," "the interpreter name," or "the vendor changed something and a newer version handles it."

## Next step

Go deeper on any panel in [6. Feature reference](./06-feature-reference.md), or start cutting spend in [7. Cost-optimization playbook](./07-cost-optimization-playbook.md).
