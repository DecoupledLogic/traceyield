---
interview-policy: always
type: decision
number: "0003"
title: "Predict-rung economic indicators: run rate and runway"

agency: "DecoupledLogic"
client: "Charles"
project: "traceyield"
product: ~
service: ~
workItem: ~

status: Drafted
supersededBy: ~
statusHistory:
  - status: Drafted
    date: 2026-07-11
    user: agent-charles

createdAt: 2026-07-11
updatedAt: 2026-07-11
removedAt: ~

author: agent-charles
owner: Charles
agent: agent-charles
strategicAlignment: 5
agencyValue: 2
clientValue: 4
userValue: 4
urgency: 2
viability: 4
feasibility: 4
deadlineWeight: 2
complexityPenalty: 3
sas: 3.30
---

# Decision 0003: Predict-rung economic indicators: run rate and runway

## Context

TraceYield's framework (`docs/traceyield-framework.md` §2.2) defines a five-rung
maturity ladder: Describe -> Diagnose -> Predict -> Prescribe -> Remediate. The
tool today sits solidly at Describe and Diagnose, has a first Prescribe lever (the
routing-savings estimator), and has **nothing at Predict**. §3.2 names Predict as
"the gap," and §2.2's own examples for that rung are "burn-rate to end of month"
and "a team trending over budget."

Run rate and runway are exactly the two economic indicators that fill this rung:

- **Run rate** — the *rate of depletion*: extrapolate current burn to a period
  boundary (today's / this-week's spend projected to the month).
- **Runway** — the *remaining before exhaustion*: `remaining budget / burn rate =
  time left`.

Adding them is not scope creep; it is delivering the rung the framework already
promised. This decision is the governance statement that the capability is **worth
building**, promoted from request **Q25**.

The two indicators have very different requirements, which shapes the direction
below:

- **Run rate is nearly free.** Client-side aggregation already runs in the emitted
  HTML's JavaScript (`aggregate()` in `HTML_TMPL`) over the Python side's per-day
  cost buckets. A projected-period-spend / burn-trend metric is a derivation over
  data that already exists — a **JS-only change**: no new capture, no new config,
  no Python change.
- **Runway is meaningless without a cap**, and that cap is a *new first-class
  input* (a budget/quota config the tool does not have today). Worse, the correct
  denominator depends on the billing model, and TraceYield is provider-blind and
  multi-machine. A single runway number that silently mixes billing models would
  violate the framework's "upper bounds are labeled" honesty principle (§2.3 #6).

## Decision

**Deliver run rate and runway as the tool's first Predict-rung capability, as two
separately-scoped pieces of work: run rate first (no new inputs), runway second
(introducing a budget/quota config with per-billing-model semantics).**

Direction (details deferred to design/plan, `/workflow-plan`, `/workflow-design`):

- **Run rate** ships as a client-side metric in the emitted report: projected spend
  to the selected period boundary plus a burn trend, derived from the existing
  per-day buckets, framed explicitly as an **upper bound** (principle #6). No
  Python change, no new config.
- **Runway** introduces a **budget/quota denominator** as a new first-class input,
  and must be **explicit about which billing model it measures against** — never one
  blended number:
  - **API key / prepaid credits** -> real dollars depleting. Literal and honest.
  - **Subscription (Max / Pro)** -> the tool's computed cost is a *notional
    recompute at current rates* (Decision 0002 / principle #3), **not** billed
    dollars. Depletion here is of the **rate-limit windows** (the 5-hour and weekly
    quotas), a different meter. Because those windows **reset**, "runway"
    (startup-cash connotation: die at zero) is the wrong word — call it
    **"time-to-limit this window."**
  - **Self-imposed team budget** -> the most useful framing for the §1 "unowned,
    high-variance, low-visibility line item" problem; leans on the team-rollup
    direction in §3.3.

This is a governance decision that the capability is **worth building** and how it
should be scoped; it does not fix the schema, the config format, or the UI beyond
the direction above.

## Consequences

### Positive

- Fills the framework's named Predict-rung gap and moves the tool up the maturity
  ladder for the first time since the routing estimator.
- Directly serves the §1 team pain ("a team trending over budget") with a
  forward-looking number, not just a rear-view describe/diagnose view.
- Run rate lands cheaply (JS-only), so the rung shows value before the heavier
  budget-config work for runway is designed.

### Negative

- Runway forces a new config surface (budget/quota) the tool has never had, plus
  per-billing-model logic that must be kept honest and clearly labelled.
- Forecasts are inherently approximate; presenting a projected number risks being
  read as a promise unless the "upper bound" framing is enforced in the UI.
- "Time-to-limit" (subscription) vs "runway" (credits/budget) is a distinction the
  UI must carry explicitly, or the two meters will be conflated by readers.

### Neutral

- Run rate changes nothing about historical reported cost; it is a pure derivation
  over the same buckets.
- Runway's real-dollars flavor depends on billing data the tool may not see for
  subscription users; the rate-limit-window flavor is the fallback there.

## Alternatives Considered

### Do nothing — leave Predict empty

Keep the tool at Describe/Diagnose/first-Prescribe. **Why rejected:** the framework
explicitly names Predict as the gap and cites burn-rate as its canonical example;
leaving it empty stalls the ladder the product is built around.

### Ship run rate only; drop runway

Take the free JS win and skip the budget-config work. **Why rejected (as the end
state):** runway is the half of the rung that answers "how long do we have," which
is the question a lead actually asks. It is right to *sequence* run rate first, but
not to abandon runway.

### One blended "runway" number across all billing models

Present a single runway figure regardless of API vs subscription vs team budget.
**Why rejected:** it mixes incommensurable meters (real dollars vs a resetting
quota window) into one misleading number, violating principle #6. The billing model
must be explicit.

## Implementation Notes

- **Run rate** is a `HTML_TMPL` JavaScript change over the existing per-day payload;
  no Python or data-model change. This is the cheap first slice.
- **Runway** needs, at minimum: a budget/quota config input, a billing-model
  selector (credits / subscription-window / team-budget), and per-model math. The
  subscription-window flavor needs the 5-hour / weekly reset semantics and should be
  labelled "time-to-limit this window," not "runway."
- Team-budget runway is the natural bridge to the framework's machine-to-team
  rollup (§3.3); plan may sequence it after the per-machine flavors.
- Suggested shape at plan time: a Predict-rung epic with run rate and runway as
  separate features, run rate depended-on-by nothing and shippable immediately,
  runway carrying the config-design work.

## Traceability

| Stage | Document | Status |
|-------|----------|--------|
| Problem | N/A | - |
| Concept | N/A | - |
| Decision | This document | Drafted |
| Plan | Not started | - |

Intake: promoted from request **Q25** (tempo-portfolio intake).

## Related Decisions

- Decision 0001: Aggregate flip — derive day/session metrics from `usage.db`
  (the aggregation layer a budget/quota join would plug into).
- Decision 0002: As-billed costing — the other pricing-aware view; both reason
  about cost over time and share the "recompute vs pin" tension.

---

**Decision makers:** Charles (owner), agent-charles (author)
