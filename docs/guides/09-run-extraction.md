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
| `--start`, `--end` | The window, as ISO 8601 timestamps. A value with no offset is read as UTC. A model request belongs to the window when it starts inside it. |
| `--cwd` | Optional. Adds Codex sessions that started in this directory, or below it, inside the window. |
| `--no-text` | Leave out everything taken from transcript text: prompts, replies, tool inputs and outputs, call summaries and subagent descriptions. Tool names, timings, tokens and costs stay. |
| `--clip` | Longest text kept per field. The default is 4,000 characters. Replies are also held to 1,200 and prompts to 600. A field that was cut carries a flag (`inputCut`, `outputCut`, `textCut`). |
| `--out` | Where to write. Without it the document goes to standard output. |

The command exits with status 2 and writes nothing when the session has no activity in the window, or when `--start` or `--end` is not a timestamp.

## What is in the document

- **`lanes`**: one per agent. The main thread, each subagent, and each Codex session. Each has its models, reasoning efforts, turn and call counts, tokens and estimated cost.
- **`turns`**: one per model request, with tokens, cost and what the model said before it acted. A request that starts inside the window brings its tool calls with it, including one that finishes after the window ends. A call whose request started before the window is left out.
- **`calls`**: one per tool call, numbered in time order, with its duration, a one-line summary, input, output, and whether it failed (`error`) or was refused by a permission check (`denied`). A background command also carries `bgMs`, the time until it actually finished.
- **`prompts`**: what you typed on the main thread during the window, including slash commands and `!` shell lines. Lines the tool wrote itself are left out.

## Things to know

- **It reads the logs directly.** It does not need `usage.db` and works on a machine that has never run the report. It also means the transcripts must still exist: coding agents delete old ones, so extract a run soon after it finishes and keep the file.
- **It contains your text.** The report's store keeps only lengths and hashes by default. This document keeps prompts, replies, and tool input and output, because that is what a reviewer needs. That text includes whatever file paths and names your commands printed. The document's own metadata holds no local path.
- **Redaction is a pattern match.** A value under a name containing password, secret, token, api key, client id, authorization or credential is replaced with `[redacted]`, in structured tool input and in free text, and so are bearer and basic tokens, passwords inside URLs, and a few well-known token shapes such as GitHub and AWS keys. It redacts some ordinary code that uses those words, and it misses a secret stored under any other name or printed with no name at all. Read the file before you share it, or use `--no-text`.
- **Nothing is sent anywhere.** The file is written where you point `--out` and nowhere else.
- **Cost is an estimate** at the rate card in `pricing.py`. A model with no rate card, such as a new Codex model, is counted in tokens and shown as not priced.
- **One request is priced once.** Claude Code can log a single model request as several records that each repeat its token usage. Extraction merges them by request id, then message id.
- **Codex tool calls are read here and not by the report.** Newer Codex versions log `exec` calls in a form the scheduled report still stores as raw events. Run extraction reads them as tool calls. Codex logs a request's token count after its tool calls, so each call is attributed to the next count logged.
- **Codex sessions are matched by folder.** `--cwd` is compared with the folder each Codex session started in, using Windows rules for a Windows path and case-sensitive rules for any other.
