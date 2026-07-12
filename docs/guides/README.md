# TraceYield user guides

These guides are for **people who use traceyield**, not people who work on its code. If you want to install the tool, read your report, keep it running, fix a problem, or spend less on coding agents without giving up capability, you are in the right place. No knowledge of the codebase is assumed.

If you are here to contribute to the tool itself, see [`../architecture.md`](../architecture.md), [`../canonical-data-model.md`](../canonical-data-model.md), and [`../../CONTRIBUTING.md`](../../CONTRIBUTING.md) instead.

## Start here

Read these in order the first time. After that, jump to whatever you need.

| Guide | Read this when you want to |
|-------|---------------------------|
| [1. Overview](./01-overview.md) | Understand what traceyield is, what it measures, and what it does and does not do. |
| [2. Install and first run](./02-install.md) | Get it running and produce your first report. |
| [3. Using the report](./03-using-the-report.md) | Open the dashboard and know what every control, card, and table is telling you. |
| [4. Keeping it updated](./04-updating.md) | Pull new versions safely and protect your usage history. |
| [5. Troubleshooting](./05-troubleshooting.md) | Fix a run that produced no report, wrong numbers, or a health warning. |
| [6. Feature reference](./06-feature-reference.md) | Go deep on every panel: exactly how each number is computed and its caveats. |
| [7. Cost-optimization playbook](./07-cost-optimization-playbook.md) | Diagnose why spend is shaped the way it is and remediate it. This is the payoff. |
| [8. Daily runs and automation](./08-daily-run-automation.md) | Schedule it to run every day and operate it across several machines. |

## The one-minute version

traceyield reads the transcript logs your coding agent already writes on your machine and turns them into a single self-contained HTML dashboard. There is no server, no account, no build step, and no data leaves your machine. You run one command, you open one file, and you can see where your token spend goes and how to bend it down.

```bash
python report.py
# then open the report.html it names under machines/<your-machine>/
```

The tool implements a discipline called **TraceYield**: a loop of *describe, diagnose, predict, prescribe, remediate* over your coding-agent usage, so you get steadily cheaper and more effective over time. Guide 7 is where you learn to run that loop yourself.
