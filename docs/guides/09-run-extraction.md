# 9. Extracting one run

The report answers "where does my spend go over weeks". Run extraction answers a narrower question: "what happened in this one piece of work". It reads one Claude Code session, the subagents it spawned and, if you ask, the Codex sessions that started in the same working directory, and writes one JSON document with every model turn and tool call.

Use it when you want to review a delivery: which agent ran on which model and reasoning effort, how long each tool call took, which calls failed or were refused, and what each one cost.

## Run it

```bash
traceyield run --session <session-id> \
  --start 2026-11-24T16:37:44Z --end 2026-11-24T19:14:30Z \
  --cwd "C:\Repos\shop\.worktrees\S5" \
  --out run.json
```

| Flag | Meaning |
|------|---------|
| `--session` | The Claude Code session id. It is the transcript's file name under `~/.claude/projects/<project>/`. |
| `--start`, `--end` | The window, in UTC. Turns and calls outside it are left out. |
| `--cwd` | Optional. Adds Codex sessions that started in this directory, or below it, inside the window. |
| `--no-text` | Leave out prompts, replies, tool inputs and tool outputs. Timings, tokens and costs stay. |
| `--clip` | Longest text kept per field. The default is 4,000 characters. |
| `--out` | Where to write. Without it the document goes to standard output. |

The command exits with status 2 and writes nothing when the session has no activity in the window.

## What is in the document

- **`lanes`**: one per agent. The main thread, each subagent, and each Codex session. Each has its models, reasoning efforts, turn and call counts, tokens and estimated cost.
- **`turns`**: one per model request, with tokens, cost and what the model said before it acted.
- **`calls`**: one per tool call, numbered in time order, with its duration, a one-line summary, input, output, and whether it failed (`error`) or was refused by a permission check (`denied`). A background command also carries `bgMs`, the time until it actually finished.
- **`prompts`**: what you typed on the main thread during the window.

## Things to know

- **It reads the logs directly.** It does not need `usage.db` and works on a machine that has never run the report. It also means the transcripts must still exist: coding agents delete old ones, so extract a run soon after it finishes and keep the file.
- **It contains your text.** The report's store keeps only lengths and hashes by default. This document keeps prompts, replies, and tool input and output, because that is what a reviewer needs. Values that look like credentials are replaced with `[redacted]`, but that is a pattern match and can miss. Read the file before you share it, or use `--no-text`.
- **Nothing is sent anywhere.** The file is written where you point `--out` and nowhere else.
- **Cost is an estimate** at the rate card in `pricing.py`. A model with no rate card, such as a new Codex model, is counted in tokens and shown as not priced.
- **One request is priced once.** Claude Code can log a single model request as several records that each repeat its token usage. Extraction merges them by request id.
