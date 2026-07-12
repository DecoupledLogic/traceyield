# 8. Daily runs and automation

The report is only as fresh as your last run, and there is a real reason to run it often: coding agents rotate (delete) old transcripts, so running daily captures history before it disappears (see the sharp edge in [guide 4](./04-updating.md)). This guide covers scheduling a daily run and operating across several machines.

## Run it manually any time

```bash
python report.py
```

Every run reparses whatever transcripts still exist and merges the results into your durable stores, so running more than once a day is harmless: it only refreshes and adds.

## Windows: scheduled task with run.cmd

The repository ships `run.cmd`, a Task Scheduler wrapper. It is machine-agnostic: it resolves the repository directory from its own location, asks the tool where this machine's folder is, and appends a one-line summary to `machines\<machine-id>\run.log`. You do not edit it.

Point a daily scheduled task at it:

```
schtasks /create /tn "TraceYield Usage Report" /tr "C:\path\to\traceyield\run.cmd" /sc daily /st 09:00
```

Replace `C:\path\to\traceyield` with your clone's path and pick a time.

### If python is not on PATH

`run.cmd` uses `python` from PATH by default. If that is not your interpreter, set the `PYTHON` environment variable to the one you want before scheduling:

```
set PYTHON=C:\Users\you\anaconda3\python.exe
```

Set it in an environment the scheduled task inherits (for example a system or user environment variable), because a value you `set` in one interactive shell does not persist to the task.

## macOS and Linux: cron or launchd

There is no `run.cmd` equivalent needed; just schedule `python report.py` from the clone directory.

**cron** (runs daily at 09:00):

```cron
0 9 * * * cd /path/to/traceyield && /usr/bin/python3 report.py >> machines/$(hostname)/run.log 2>&1
```

Use the absolute path to your Python 3 interpreter (find it with `which python3`).

**launchd** (macOS): create a `LaunchAgent` plist that runs `python3 report.py` with `WorkingDirectory` set to your clone and a `StartCalendarInterval` for the time you want.

## Reading run.log

Each scheduled run appends one summary line to `machines/<machine-id>/run.log`, the same headline you see on a manual run: active days and date range, total cost, turns, and tool-error rate. Skim it to confirm the task is running and the numbers look sane. If a day's line is missing, the task did not run that day; see the scheduled-run section of [guide 5](./05-troubleshooting.md).

## Operating across several machines

Data is per-machine by design, because each machine has its own transcripts and there is no cloud aggregation. To operate a fleet:

- **Run the report on each machine** (schedule it on each, as above). Each writes its own `machines/<machine-id>/` folder and its own report.
- **Open each machine's own report.** There is no combined cross-machine view; you compare by opening each.
- **Keep the shared repository in sync** with `git pull` on each machine so they all run the same version. Only `pricing_history.json` is shared and committed; everyone's usage data stays local and git-ignored.

### Naming a machine's folder

The folder name defaults to the machine's sanitized hostname. To override it, set `TRACEYIELD_MACHINE`. Two common reasons:

- **A renamed machine** whose hostname no longer matches its existing folder: set `TRACEYIELD_MACHINE` to the old folder name so its history carries on.
- **A fresh clone** where you want to reuse a folder you copied in: set the variable to that folder's name before the first run.

```bash
# macOS / Linux
TRACEYIELD_MACHINE=my-laptop python report.py
# Windows (PowerShell)
$env:TRACEYIELD_MACHINE = "my-laptop"; python report.py
```

Find the resolved folder any time without parsing:

```bash
python report.py --machine-dir
```

## Do not forget the backup

Scheduling keeps history fresh, but it does not back it up. Your `machines/<id>/daily_metrics.json` and `session_metrics.json` are the only copy of dates whose transcripts have rotated away, and they are git-ignored. Copy them somewhere safe on a schedule. The full reasoning is in [guide 4](./04-updating.md).

## Next step

That completes the guide set. Return to the [index](./README.md), or go straight to the [cost-optimization playbook](./07-cost-optimization-playbook.md) to start getting value back.
