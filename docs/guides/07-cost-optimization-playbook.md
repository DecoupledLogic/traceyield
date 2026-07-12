# 7. Cost-optimization playbook

This is the payoff guide. It teaches you to run the TraceYield loop yourself: **diagnose** why your spend is shaped the way it is, **prescribe** the right lever, **remediate**, and then **confirm** the number actually moved. The whole thing is done from the report you already generate; no extra tooling.

Keep one principle in front of you the entire time:

> **Tokens are the meter, not the mission.** The goal is not fewer tokens. It is more value per token: the most useful work for the least cost. Every remedy below is judged by whether it cut cost *without* costing you capability.

## The one number that explains most of your bill

On any healthy agentic session, **cache read is your biggest token line by volume**, and that is fine. What matters is its size *per turn*. Because the model is stateless, every turn re-sends the entire conversation so far, and the cached prefix is re-read every time. So:

> **Cache-read-per-turn is a direct measurement of how big your context window is right now.**

A high cache-read-per-turn number is the tool telling you "you are carrying tens of thousands of tokens of context on every single turn." Cost is roughly **context size times number of turns**. That is the engine behind almost every expensive session, and it is why the two biggest levers are "carry less context" and "put routine turns on a cheaper model."

You do not compute this by hand. The **Token composition** panel shows the shape (mostly cache read, tiny fresh input = a big, slowly-changing context), and **Top sessions by cost** finds the worst offenders for you.

## The diagnostic pass (do this weekly)

Spend five minutes with the report on **Week** granularity:

1. **Scorecard.** Look at the KPI cards and their versus-previous deltas. Is cost up or down? Is tool error rate up or down? This tells you *whether* to dig, not *where*.
2. **Shape.** Open **Token composition**. Is it almost entirely cache read with little fresh input and modest output? That means large context carried across many turns. Healthy work has cache read dominant too, so judge by *trend*: is cache read per turn climbing?
3. **Concentration.** Open **Cost by model tier**. If one tier (usually Opus) is nearly all your spend, routing is your biggest lever.
4. **Outliers.** Open **Top sessions by cost**. Is one session far above the rest? That is a runaway conversation and the cheapest single fix.
5. **Waste.** Open **Tokens and cost per tool** and sort your attention by **Est. waste**, then read **Errors and fixes**. Recurring tool errors are wasted turns, and each wasted turn re-reads your whole context.

Those five reads map onto the four failure modes below. Match what you saw to the matching remedy.

## Failure mode 1: the runaway session (uncleared context)

**Diagnose.** One session sits far above the rest in **Top sessions by cost**. Its **Token composition** is overwhelmingly cache read. It spans many turns on one long conversation.

**Why it costs.** A long, uncleared conversation re-reads its entire accumulated context on every turn. The context only grows, so each turn is more expensive than the last, for work that may have moved on to a new task long ago.

**Prescribe and remediate.**

- **Clear between tasks.** Start a fresh context when you switch to an unrelated task, so each turn re-reads less. In Claude Code that is `/clear`; other agents have an equivalent. This is the single highest-return habit.
- **Compact long-but-related work.** When a task genuinely needs long context, compact or summarize rather than carrying every prior turn verbatim.
- **Split big tasks.** Break a sprawling session into scoped conversations so no single context balloons.

**Confirm.** Next week, the runaway session should not reappear at the top, and your average cost-per-turn (Cost card divided by Assistant turns card, or just the Cost trend) should fall.

## Failure mode 2: context bloat (across all your work)

**Diagnose.** Not one runaway, but a general upward drift in cache-read-per-turn: **Token composition** trending toward more cache read per turn, cost trending up while turns stay flat.

**Why it costs.** Habits that quietly inflate every context: keeping large files open, re-reading the same material each turn, long-lived sessions as a default, broad searches inline instead of delegated.

**Prescribe and remediate.**

- **Keep context small by default.** Clear early and often; do not let one session be your whole day.
- **Delegate wide reads.** Use subagents or scoped searches for "find where X is" so the raw file dumps do not land in and stay in your main context.
- **Reference, do not re-paste.** Point the agent at files rather than pasting their contents repeatedly.

**Confirm.** The cache-read line in **Token composition** should stop climbing per turn, and the Cost trend on **Week** should flatten or fall while your turns (work done) hold steady. That last part is the "without losing capability" check.

## Failure mode 3: model mis-routing

**Diagnose.** **Cost by model tier** shows one expensive tier (typically Opus) dominating. The **Model-routing savings estimate** panel puts a dollar figure on the opportunity.

**Why it costs.** The most capable model is also the most expensive on both input and output. Routine work (reading, simple edits, exploration, mechanical refactors) rarely needs it, but it is easy to leave everything on the top tier by default.

**Prescribe and remediate.**

- **Read the estimator honestly.** It recomputes this period's top-tier tokens at a cheaper tier's rates, scaled by the **routable share** you set. It is an **upper bound**: it assumes the share you name is truly safe to route. Start conservative (the default 30%) and only raise it for work you have confirmed a cheaper model handles well.
- **Route routine work.** Switch model per task (in Claude Code, `/model` to Sonnet or Haiku for the routine parts) and keep quality-sensitive work on the top tier.
- **Make it a norm, not a one-off.** The lever only pays if the routing habit sticks.

**Confirm.** **Cost by model tier** should show the cheaper tiers taking a bigger share next period, and total Cost should drop by *some fraction of* the estimator's upper bound. If quality suffers, dial the routable share back down; the estimator was always a ceiling, not a promise.

## Failure mode 4: tool-error waste

**Diagnose.** **Tokens and cost per tool** shows real dollars in the **Est. waste** column; **Errors and fixes** shows one or two patterns dominating the count and share.

**Why it costs.** Each tool error is roughly one extra turn, and every extra turn re-reads your whole context. Errors are pure waste: you pay full context cost for a turn that produced nothing.

**Prescribe and remediate.** Read the top pattern in **Errors and fixes** and apply its suggested fix. The most common, high-value ones:

- **Command not found / shell mismatch (Windows).** Use the right shell for the platform (on Windows, the PowerShell tool for Windows commands) instead of assuming a Unix PATH.
- **Write/Edit before Read.** Read a file before editing it; the harness rejects edits to unread files.
- **Edit string did not match / stale edit.** Match exactly and uniquely, or re-Read right before editing when something may have changed the file.
- **Permission denied (recurring).** A repeated denial suggests either an allowlist entry for that action or a different approach.

These are habit and configuration fixes, so they compound: fix the pattern once and the waste stops recurring.

**Confirm.** **Tool error rate** on the KPI cards and the **Errors and fixes** counts should drop next period, and the **Est. waste** dollar figure with them. Tune the **retry factor** input to match how many extra turns you believe an error really costs you, so the dollar figure reflects your reality.

## Putting it together: the loop

1. **Describe.** Once a week, open the report on Week granularity.
2. **Diagnose.** Run the five-read pass above; identify the one or two dominant failure modes.
3. **Prescribe.** Pick the matching lever. Prefer the cheapest, highest-return one first: usually clearing context and fixing the top error pattern.
4. **Remediate.** Change the habit or the setting. Change one thing at a time so you can attribute the result.
5. **Confirm.** Next week, check the specific card or panel the remedy targets. Kept the capability? Keep the habit. Did not move? Try the next lever.

Run that loop and the numbers get steadily cheaper *and* your work stays as capable, which is the entire point of TraceYield.

## Going deeper

- The mechanics behind every claim here (why caching works the way it does, why cache-read equals context occupancy) are in [`../token-mechanics-and-insights.md`](../token-mechanics-and-insights.md).
- The framework and its rungs are in [`../traceyield-framework.md`](../traceyield-framework.md).
- The exact computation and caveats of each panel are in [6. Feature reference](./06-feature-reference.md).

## Next step

To keep the report fresh so this loop always has current data, go to [8. Daily runs and automation](./08-daily-run-automation.md).
