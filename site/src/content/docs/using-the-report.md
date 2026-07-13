---
title: "Using the report"
order: 3
description: "Read the KPIs, trend chart, and cost breakdowns."
---

# 3. Using the report

This guide walks the dashboard top to bottom and tells you what you *do* with each part. For the exact formula behind any number, jump to [6. Feature reference](./06-feature-reference.md).

## Produce and open it

```bash
python report.py
```

Open the `report.html` under your machine's folder (see [guide 2](./02-install.md)). The page is titled **TraceYield LLM Usage & Health**. Everything below lives on that one page; scroll down to move through it, or use the controls at the top.

## The controls (top of the page)

Everything on the page reacts to these. Set them first.

- **Day / Week / Month.** The granularity. This changes how the trend is bucketed and what "the selected period" and "versus previous" mean.
- **Provider.** All, Claude, or Codex. Filters every panel to one provider or shows them combined.
- **Model.** All models, or a single model tier. Filters by the model that did the work.
- **Trend metric.** What the trend chart plots: Cost, Total tokens, Assistant turns, Tool error rate, Tool errors (count), or Sessions.
- **The stepper (`‹ name ›`).** Moves the selected period backward and forward. You can also use the left and right arrow keys. The name in the middle is the period you are looking at.

A good habit: switch to **Week** or **Month** and step back a few periods to see the shape of your usage, then drill into a spike at **Day**.

## KPI cards

Right under the controls are the headline cards for the **selected period**: Cost, Total tokens, Assistant turns, Sessions, and Tool error rate. Each card shows a **versus previous** delta at the same granularity, so on Week each card compares this week to last week.

Use these as your at-a-glance scorecard. When you make a change to how you work, this row is where you check next week whether it moved the needle.

## Trend

The chart plots your chosen trend metric across time at the chosen granularity. This is where you answer "is it getting better or worse?" rather than "what was today." Switch the metric to **Tool error rate** or **Cost** and step through weeks to see a real trend rather than daily noise.

## Selected-period breakdown

A cluster of panels that decompose the selected period.

- **Cost by project.** Where your spend went, per project. A single project dominating is normal; a surprising one is worth a look.
- **Cost by model tier.** How much went to Opus versus Sonnet versus Haiku. For most Claude Code users this is heavily Opus, which is the single biggest lever (see below and [guide 7](./07-cost-optimization-playbook.md)).
- **Cost by provider** and **Tokens by provider.** With more than one provider, dollars are a *projection* at each provider's rate card, and any provider without a rate card shows `$0` even though it used real tokens. This panel is also your live answer to "which providers have dollar pricing": a `$0` provider is one whose rate card is not defined. **Tokens by provider** is the pricing-independent, honest comparison; prefer it when comparing providers.
- **Model-routing savings estimate.** An interactive lever: it recomputes this period's **Opus** tokens at a cheaper tier's rates. Set what share of Opus is safely routable and pick Sonnet or Haiku, and it shows the estimated saving. It is an **upper bound**, so treat it as "no more than this," not a promise. Guide 7 shows how to act on it.
- **Token composition.** The five line-items every token splits into: fresh input, cache write 5m, cache write 1h, cache read, and output. This is where the money actually goes. Cache read is usually the biggest by volume; that is expected, and guide 6 explains why.
- **Tool usage (calls).** How often each tool was called in the period.

## Tokens and cost per tool

A table attributing each turn's full cost to its single tool. Because Claude Code runs one tool per turn, this is **exact** for tool turns. The important column is **Est. waste**: it puts a dollar figure on tool errors, using errors times average cost per call times a **retry factor** you can tune (how many extra turns you think each error costs). Sort your attention by this column to find where errors are actually costing you money.

## Errors and fixes

A table of your tool failures grouped into recurring **patterns** (for example "Command not found (shell mismatch)" or "Write/Edit before Read"), each with a **count**, a **share**, and a **suggested fix**. This is the most directly actionable panel in the whole report: each row is a habit you can change. Guide 7 turns this into a routine.

## Top sessions by cost

A table of your highest-cost individual conversations across **all history** (top 50), not just the selected period. It exists to catch a single **runaway conversation**: a long, uncleared context that gets re-read on every turn and quietly runs up a large bill. If one session sits far above the rest, that is usually the cheapest single thing you can fix. Guide 7 covers exactly how.

## Model pricing (tracked daily)

A chart and table of the model rates over time. Historical cost everywhere else in the report is computed at **current** rates so periods are comparable; this panel is the one place that shows how the rates themselves moved. If you ever need to correct the rates, see [guide 4](./04-updating.md).

## How to read this report

A built-in explainer section covering the five token line-items, how to use the numbers to improve, and a glossary. It is worth reading once inside the report itself; it is the short version of guides 6 and 7.

## Data health

The panel at the bottom (and mirrored at the very top) is traceyield checking its **own** data quality: whether the vendor changed their log format underneath it (schema drift), and whether there are dates with activity but nothing recorded (coverage holes). Green means the numbers above it can be trusted. If it warns, read [guide 5](./05-troubleshooting.md) before acting on the report.

## Next step

- To keep the tool current without losing history, go to [4. Keeping it updated](./04-updating.md).
- To go deep on any number, go to [6. Feature reference](./06-feature-reference.md).
- To actually cut spend, go to [7. Cost-optimization playbook](./07-cost-optimization-playbook.md).
