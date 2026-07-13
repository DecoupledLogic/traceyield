---
title: "Overview"
order: 1
description: "What TraceYield is and how the dashboard is organized."
---

# 1. Overview: what traceyield is

## In one paragraph

**traceyield** reads the transcript logs that your coding agents already write on your machine, and produces a single, self-contained, interactive HTML dashboard of your usage: what you spent, on what, and where the waste is. It has no dependencies beyond Python's standard library, no server, no build step, and no network calls except an optional best-effort check of a provider's public pricing page. Everything runs locally and your data never leaves your machine.

> **Which providers and levers are active for you?** traceyield is provider-neutral by design, so this guide set stays general and uses specific tools (Claude Code, Codex) only as examples. The **live source of truth for what is supported on your machine is the report itself**: the **Provider** selector lists every provider it ingested, and the **Cost by provider** panel shows which ones have dollar pricing (any provider without a rate card reads `$0`). When you want to know what is supported today, look there rather than at a hardcoded list in the docs.

## The brand and the tool

Two names, on purpose:

- **TraceYield** (capitalized) is the *discipline*: a way of managing the cost and effectiveness of your interactions with coding agents. Think of it as FinOps for coding-agent spend.
- **traceyield** (lowercase, this repository) is the *reference tool* that implements the discipline.

The unit of account is the **token**, but the thing the discipline actually manages is the **interaction**: the prompt you send, the reasoning the model does, and the response you get back. Tokens are the meter. Value per token is the goal. The point is never "use fewer tokens"; it is "get more useful work out of each token you pay for."

## The loop it supports

TraceYield runs a five-rung loop over your usage, and traceyield is built to help you climb it:

1. **Describe.** What happened? Cost, tokens, turns, sessions, per project and per model.
2. **Diagnose.** Why is the number shaped this way? Runaway sessions, context bloat, mis-routing, tool-error waste.
3. **Predict.** Where is this heading? Run rate and runway.
4. **Prescribe.** What is the lever, and what is it worth? The routing-savings estimator is one example.
5. **Remediate.** Do it, then confirm the number moved. You do this; the report shows whether it worked.

Which rungs the tool covers most today is something the report shows you directly (the panels that exist are the rungs it supports). For the full framework and where it is going, read [`../traceyield-framework.md`](../traceyield-framework.md). For the mechanics of *why* the numbers mean what they mean, read [`../token-mechanics-and-insights.md`](../token-mechanics-and-insights.md). This guide set is the practical layer on top of both.

## What the report shows

A single run produces a dashboard with:

- **KPI cards**: cost, total tokens, assistant turns, sessions, and tool-error rate for the selected period, each with a change versus the previous period.
- **A trend chart** you can step through by day, week, or month, switching the metric.
- **Cost breakdowns**: by project, by model tier, by provider, and the five-line token composition that shows where the money actually goes.
- **Top sessions by cost** across all history, to catch a single runaway conversation.
- **A model-routing savings estimator** that shows what routing routine work to a cheaper model would save.
- **Tokens and cost per tool**, including a dollar figure on tool errors.
- **An error taxonomy** that classifies tool failures into recurring patterns, each with a concrete fix.
- **Model pricing over time**, and a **data-health panel** that watches for silent data problems.

Guide 3 walks all of this in order; guide 6 is the deep reference.

## How it works, briefly

Because every line in a transcript is timestamped, a single run can reconstruct your **entire history**, bucketed by the activity date of each event rather than by the date you ran it. The Python side only ever produces per-day buckets; the day, week, and month aggregation happens inside the report in your browser. That is why you can open the report and step through any period.

For the full data flow, see guide 6 and [`../architecture.md`](../architecture.md).

## What it does not do

Being clear about the boundaries saves confusion later:

- **It is local and per-machine.** It reads the local transcript logs your coding agents write (for example `~/.claude/projects` for Claude Code) on the machine you run it on. There is no cloud aggregation. If you use several machines, each keeps its own data under `machines/<machine-id>/`. See [guide 8](./08-daily-run-automation.md).
- **It is read-only on your usage.** It never changes your transcripts, your agent, or your bill. It only reads and reports.
- **It is not a live billing feed.** It computes cost from a hand-maintained pricing table, and it recomputes *all* historical cost at *current* rates so periods are comparable. It also checks the vendor's published pricing page each run and warns you if the table looks stale, but it never silently rewrites prices. See [guide 4](./04-updating.md).
- **It is not a quality judge.** It measures cost and mechanical health (errors, context size), not whether the model gave you a good answer. The routing estimator is deliberately framed as an *upper bound* precisely because only you can judge which work is safe to route.

## Next step

Go to [2. Install and first run](./02-install.md).
