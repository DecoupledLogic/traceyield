# 6. Feature reference

The deep dive. For each part of the report: what it is, exactly how the number is computed, and the caveats that keep you from over-reading it. Guide 3 is the tour; this is the manual.

## The costing model (read this first)

Every other number rests on this. Each request to the model re-sends the whole conversation so far, because the model is stateless between calls. To avoid re-billing all of it at full price, the API **caches** a stable prefix. Your tokens therefore split into **five line-items**, each a multiple of the model's base **input** rate:

| Line-item | Multiplier | What it is |
|-----------|-----------|-----------|
| Fresh input | 1x | Brand-new tokens read for the first time (a newly opened file, your latest message). |
| Cache write, 5 min | 1.25x | Storing a chunk in the cache the first time, reusable for 5 minutes. |
| Cache write, 1 hour | 2x | Same, kept for an hour. Claude Code uses 1h caching, so this line is large. |
| Cache read | 0.1x | Reading tokens already in the cache. The payoff of caching. |
| Output | output rate | Tokens the model generates (its reply plus tool calls), at the separate, higher output rate. |

Two consequences worth holding onto:

- **Cache read is usually your biggest line by volume**, and that is healthy, not a problem. Its per-turn size is essentially a *measurement of how big your context window is right now*. A high cache-read-per-turn number means "you are carrying N thousand tokens of context on every turn." That single fact drives most of the diagnosis in [guide 7](./07-cost-optimization-playbook.md).
- **A cached token you reuse costs about 8% of writing it fresh** at 1h TTL (0.1 versus 1.25), so caching pays off after roughly two reuses. The risk is **invalidation**: editing a file or changing tools near the front of the prompt forces an expensive re-write of everything after it.

Where the raw data allows only an aggregate cache-creation number with no 5m/1h split, the tool attributes it all to the 5m line. Model tiers map by name: anything containing `opus` (and `fable`) is the Opus tier, `sonnet` is Sonnet, `haiku` is Haiku; anything unrecognized is skipped entirely and gets no cost. That last rule is why an unrecognized new model shows up as under-counting, which the Data health panel is designed to catch.

## KPI cards

**What.** Cost, Total tokens, Assistant turns, Sessions, and Tool error rate for the selected period.

**How.** Summed over the buckets in the selected period at the current granularity. Each card's **versus previous** delta compares to the immediately preceding period at the same granularity (last week versus this week on Week, and so on).

**Caveats.** "Total tokens" is all five line-items combined, so it is dominated by cache read; do not read it as "new work done." Tool error rate is the share of tool results returned as errors.

## Trend chart

**What.** One metric across time: Cost, Total tokens, Assistant turns, Tool error rate, Tool errors (count), or Sessions.

**How.** The Python side only ever emits per-day buckets; day, week, and month aggregation happens client-side in the report. Stepping and the arrow keys move the selected period.

**Caveats.** Daily data is noisy. For trend judgments prefer Week or Month; use Day to inspect a specific spike.

## Cost by project

**What.** Spend attributed to each project in the selected period.

**How.** Claude Code encodes a project's absolute path as its directory name. The report computes the common leading path segments across all your projects and strips them, so labels read as just the repo name on any machine.

**Caveats.** Attribution follows the transcript's project directory. Work done outside a project directory groups accordingly.

## Cost by model tier

**What.** Spend split across Opus, Sonnet, and Haiku.

**How.** Cost per tier from the five-line model above, summed for the period.

**Caveats.** For most Claude Code users this is heavily Opus. That concentration is the single biggest lever, which is why the routing estimator exists.

## Cost by provider and Tokens by provider

**What.** With more than one provider (Claude, Codex), spend and tokens split by provider.

**How.** Cost is a **projection** at each provider's rate card. Any provider without a defined rate card counts its tokens at `$0` in the dollar view. **Tokens by provider** is pricing-independent. This panel doubles as the live source of truth for which providers currently have dollar pricing: a `$0` provider has no rate card yet.

**Caveats.** Do not compare providers by dollars while one lacks a rate card; the `$0` is a missing price, not free usage. **Tokens are the neutral, honest currency for provider comparison.**

## Model-routing savings estimate

**What.** An interactive estimate of what routing routine work off Opus would save.

**How.** It takes this period's **Opus** token usage and recomputes it at a cheaper tier's rates (Sonnet or Haiku, your choice), then scales by the **routable share** you enter (default 30%). The rates come from the embedded pricing block, so it recosts without hardcoding.

**Caveats.** It is explicitly an **upper bound**. It assumes the share you name is safely routable and that routing changes nothing else. Only you can judge which work tolerates a cheaper model. Treat the number as "the saving is no more than this."

## Token composition

**What.** The five line-items (fresh input, cache write 5m, cache write 1h, cache read, output) for the period.

**How.** Direct sum of each line across the period's buckets.

**Caveats.** This is the single most diagnostic panel for *shape*. A composition that is almost entirely cache read with tiny fresh input is a large, slowly-changing context re-read every turn. See [guide 7](./07-cost-optimization-playbook.md).

## Tool usage (calls)

**What.** How many times each tool was called in the period. A volume view, not a cost view.

## Tokens and cost per tool

**What.** A table attributing each turn's full cost and output to its single tool, plus calls, error count, error rate, and an estimated-waste dollar figure.

**How.** Claude Code serializes tool calls (about 100% of turns use a single tool), so a turn's entire cost and output are attributed to that one tool. This is **exact** for single-tool turns. Turns with multiple tools or none land in the pseudo-rows `(multi-tool turn)` and `(final response)`. **Est. waste** = errors times average cost per call times a **retry factor** you set (assumed extra turns per error, default 1). The cost-per-call part is exact; the retry factor is your tunable assumption.

**Caveats.** The exactness claim holds only because turns are single-tool; the pseudo-rows exist precisely so nothing is silently misattributed. Est. waste is a model, not a measured figure, because the tool cannot know how many extra turns an error actually triggered.

## Errors and fixes

**What.** Tool failures grouped into recurring patterns, each with count, share, and a suggested fix.

**How.** Each tool-result error text is lowercased and matched, in order, against an ordered taxonomy; the first matching rule wins, else it is "other." The current patterns:

| Pattern | Typical fix |
|---------|-------------|
| Write/Edit before Read | Read a file before editing it; the harness rejects edits to unread files. |
| Edit on stale file | Re-Read right before editing when something may have changed the file. |
| Edit string did not match | `old_string` must match byte-for-byte and be unique; add context or use replace-all. |
| Command not found (shell mismatch) | On Windows, `ls`/`python`/etc are not on the Bash PATH; use the PowerShell tool. |
| Shell quoting / path-escaping error | Windows backslash paths break Bash quoting; prefer PowerShell or forward slashes. |
| User rejected / permission denied | Recurrent denials suggest an allowlist entry or a different approach. |
| Treated a directory as a file | Confirm the path is a file before Read/Write. |
| File / path not found | Verify paths first; often a wrong relative path or an assumed file. |
| Blocked dangerous operation | A destructive command hit a guard; scope paths explicitly. |
| Tool input validation error | Often a deferred/MCP tool called before its schema loaded, or a bad parameter. |
| Unknown JSON field | Stale or misspelled payload field; check the current schema. |
| Git error | Bad ref, not a repo, or cannot change directory; check repo state first. |

**Caveats.** Rules match top to bottom, so order matters when substrings overlap. Anything unmatched is "other"; a large "other" share is a hint that a new recurring pattern deserves its own rule (a maintainer change).

## Top sessions by cost

**What.** Your highest-cost individual conversations across **all history** (top 50), regardless of the selected period.

**How.** Sessions are accumulated globally, keyed by session id, across every date they touched, because one conversation's cost is split across the dates it spanned. The report embeds the top 50 by cost; the full set persists to `session_metrics.json`.

**Caveats.** This is all-time by design; it is not filtered by the period controls. A single session far above the rest is almost always a long, uncleared context re-read every turn, the cheapest single thing to fix.

## Model pricing (tracked daily)

**What.** A chart and table of model rates over time.

**How.** Each run stamps the current `PRICING` table into `pricing_history.json`, keyed by date, building a time series of the rates themselves. All cost elsewhere in the report is computed at **current** rates for comparability; this is the only panel that shows how rates moved.

**Caveats.** Editing `PRICING` retroactively changes every day's reported cost everywhere else. That is intentional. See [guide 4](./04-updating.md).

## Data health

**What.** traceyield checking its own data quality, shown top and bottom of the report and as stdout warnings.

**How.** A cost-free extra pass fingerprints the shape of each provider's logs and diffs it against a known baseline (schema drift), and reconciles the day-series against transcript coverage (coverage holes, staleness, suspicious zero-cost days). A provider can be fingerprinted for health even before it has a full cost model, so a provider may appear in health checks while still reading `$0` in the cost panels (the Cost by provider panel is the live indicator of which providers are priced). Old raw event payloads are nulled after a retention window (90 days by default) as storage hygiene.

**Caveats.** This panel catches **silent** failures the resilient parser would otherwise hide: an unrecognized model that under-counts, or dates with activity but nothing recorded. Green here is what lets you trust everything above it. Warnings are triaged in [guide 5](./05-troubleshooting.md).

## Underlying architecture (pointer)

The report is emitted by `report.py`, which holds the parser, the persistence, and the entire HTML/CSS/JS template. A companion module, `canonical.py`, builds a provider-blind SQLite store (`usage.db`) that each provider ingests into as one neutral record stream. For the full data model and design decisions, read [`../architecture.md`](../architecture.md), [`../canonical-data-model.md`](../canonical-data-model.md), and the decision records under [`../decisions/`](../decisions/).

## Next step

Put it to work in [7. Cost-optimization playbook](./07-cost-optimization-playbook.md).
