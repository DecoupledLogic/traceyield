# Learning from Claude Code's /insights

*Written 2026-07-13. This doc studies Claude Code's built-in `/insights` report,
separates what it does deterministically from what it generates with an LLM, and
maps the useful parts onto TraceYield's loop. It sits beside the framework doc
([`traceyield-framework.md`](./traceyield-framework.md), the describe / diagnose /
predict / prescribe / remediate loop) and the diagnostics doc
([`token-mechanics-and-insights.md`](./token-mechanics-and-insights.md), the D1-D13
economic catalog). Where those cover the economic half of the loop, this one argues
for a second facet (behavioral signals) and a concrete design for the loop's
unbuilt bottom rung (remediate). Grounded in the current code:
`classification.py` (`ERROR_RULES`), `report.py`, and `cli.py` — and, as of the
2026-07-13 revision, in a captured `/insights` run dissected line by line (see
"Grounding: the captured artifact" below).*

---

## The One Paragraph Version

`/insights` is a one-shot, harness-generated narrative about how a person uses
Claude Code. Under the prose it is powered by the same raw material TraceYield
already parses: session counts, message counts, tool-result errors, activity dates.
Two things in it are worth taking. First, it surfaces a facet TraceYield does not:
behavioral friction (shell mismatches, repeated rejections, rework) read as
workflow signal rather than as cost. Second, and more valuable, it ends on concrete
remediations: proposed CLAUDE.md edits, skills, and hooks. That is exactly the
remediate rung the TraceYield framework names but has not built. The parts worth
leaving are equally clear: the narrative is sycophantic, and in at least one
documented case it fabricated figures and mislabeled its own self-check as an
independent audit. The lesson is to take the deterministic substrate and the
artifact-generating idea, and to reject the ungrounded narrative.

---

## What Insights Actually Is

`/insights` is produced by harness code, not by a skill and not by the model in the
session. The evidence: no `insights` entry exists in the skill registry, the full
report arrives already computed with a finished HTML file on disk, and the model is
handed a fixed message to echo. The harness reads across many past session
transcripts, aggregates them, runs an LLM pass to write the narrative, and renders
a static HTML report.

Structurally it is the same input TraceYield consumes (`~/.claude/projects/**/*.jsonl`),
processed for a different output: a human-readable story about working style rather
than a spend-and-health dashboard. That overlap in input is why the good parts port
cleanly.

## The Split: Deterministic Versus Generated

The single most useful move is to separate what `/insights` counts from what it
writes. The counted layer is TraceYield's native territory. The written layer is
where the sycophancy and the one documented fabrication live.

| Insights element | Layer | TraceYield equivalent |
|---|---|---|
| Sessions, messages, hours, date range | Counted | Already aggregated in `report.py` |
| Commits | Counted | Not captured (git, not transcripts) |
| Tool-error rate and taxonomy | Counted | `ERROR_RULES` / `classify()` in `classification.py` |
| Friction categories (shell, git, rework) | Counted, then narrated | Partly the error taxonomy; see behavioral facet below |
| Project areas / what works | Counted (clustering), then narrated | `by_project` buckets |
| The narrative prose and flattery | Generated | None, by design |
| The fun anecdote | Generated | None |
| CLAUDE.md / skills / hooks suggestions | Generated from counted signals | Not built; the opportunity |

The friction categories deserve attention because they read as insight but reduce
to counting. The current run mapped to signals TraceYield already classifies:

| Insights friction category | Deterministic trigger in our taxonomy |
|---|---|
| Windows / PowerShell interop | `shell_syntax`, `shell_cmd_not_found` |
| Git branch and workflow assumptions | `user_rejected`, `git_error` |
| Premature action / fabricated info | Partly `user_rejected`; the rest is not error-shaped (a limit, see below) |

## Where Insights Maps Onto The TraceYield Loop

The framework doc defines the loop as describe, diagnose, predict, prescribe,
remediate. `/insights` exercises the whole loop in one pass, which is a useful
validation that the loop is the right shape. It is strongest exactly where
TraceYield is currently weakest.

| Loop rung | Insights does | TraceYield does today |
|---|---|---|
| Describe | KPI counts, working-style summary | KPI cards, trend chart |
| Diagnose | Friction categories | D1-D13 economic catalog, error taxonomy |
| Predict | Not really | Run-rate and runway (decision 0003) |
| Prescribe | CLAUDE.md / skills / hooks suggestions | Error-taxonomy fix strings, cost playbook |
| Remediate | Suggests the change (stops there) | Not built |

The takeaway: TraceYield leads on diagnose and predict for the economic facet.
`/insights` leads on prescribe, and it points directly at how to build remediate.
Neither tool closes the loop today; `/insights` stops at a suggestion the reader
must apply by hand.

## The New Facet: Behavioral Signals

TraceYield's diagnostics are economic: tokens, cache efficiency, cost per turn.
`/insights` demonstrates a second, orthogonal facet, behavioral friction, that is
just as deterministic and that the current catalog does not cover. These are
countable from the same transcripts and would form a behavioral catalog paralleling
D1-D13.

| Proposed | Signal | Formula from what we parse | Reads as |
|---|---|---|---|
| B1 | Correction rate | `user_rejected` count / sessions | How often work gets redirected |
| B2 | Shell-mismatch rate | (`shell_syntax` + `shell_cmd_not_found`) / tool_results | Environment interop friction |
| B3 | Rework rate | (`stale_edit` + `read_before_write` + `edit_no_match`) / edits | Edit-discipline friction |
| B4 | Path-miss rate | (`file_not_found` + `is_directory`) / tool_results | Wrong-path guessing |
| B5 | Denial recurrence | Repeated `user_rejected` for the same tool or action | An allowlist or approach gap |

Each of these already has its raw counts in `daily_metrics.json` and
`session_metrics.json` via the error taxonomy. B1 through B5 are ratios of numbers
we hold, the same way D1 through D13 are. No new capture is required for this facet;
it is a reporting and rollup addition.

## The Honest Boundary

Not every `/insights` suggestion is deterministically groundable, and it is
important to say which are not rather than paper over it.

| Insights suggestion | Grounded in a countable signal? |
|---|---|
| Commit messages to a file with `-F` | Yes: recurring `shell_syntax` on commits |
| Default branch is master, confirm before new branches | Yes: `user_rejected` and git redos |
| Add an allowlist entry | Yes: repeated `user_rejected` (B5) |
| Never write to the production database | Partly: rejection on write attempts, plus a stated policy |
| Do not use em dashes | No: a stylistic correction, not an error |
| Follow the prod-support lifecycle | No: a workflow convention, not an error |

The friction-derived suggestions fall straight out of the taxonomy. The stylistic
and workflow ones come from an LLM reading the user's corrective messages, which a
pure error taxonomy cannot see. There are two honest ways to reach them, and they
have different risk profiles:

- A deterministic directive detector over user turns: match corrective phrasing in
  the human's messages ("don't", "stop", "never", "always", "I told you") and
  cluster the recurring ones. This stays countable and evidence-linked, and it
  would catch the em-dash case as "the user repeated a correction N times."
- An LLM summarization pass, which is what `/insights` does. This reaches the most
  suggestions but reintroduces the exact failure modes below.

TraceYield should prefer the deterministic detector and treat any LLM pass as
clearly-labeled, evidence-required, and never the source of a number.

## From Fix Strings To Artifacts

This is the highest-leverage idea to take from `/insights`. Today each rule in
`ERROR_RULES` pairs a pattern with a human-readable `fix` string. `/insights` shows
the next step: pair the pattern with a concrete, ready-to-apply artifact.

A rule that fires often enough could carry, instead of only prose:

- a CLAUDE.md paragraph to append,
- a `settings.json` permission or configuration line,
- a hook definition (JSON) for a lifecycle event,
- a `SKILL.md` scaffold for a recurring multi-step workflow.

Worked examples, each triggered by a signal we already count:

| Trigger (threshold) | Generated artifact |
|---|---|
| `shell_syntax` recurs | CLAUDE.md note: write commit messages to a file, use `-F` |
| `user_rejected` recurs for one tool (B5) | `settings.json` allowlist entry for that tool |
| `read_before_write` recurs | Hook on `PreToolUse` for Edit that warns if the file was not read |
| A workflow repeats across sessions | `SKILL.md` scaffold with the recurring steps |

The design keeps this deterministic: a rule fires only above a frequency threshold,
the artifact is templated (not model-written), and every artifact links back to the
count and the example transcripts that triggered it. That frequency gate is what
separates a grounded suggestion from `/insights`-style narrative.

## Closing The Loop: Approve And Apply

The goal the user stated is to go past `/insights` and actually create the approved
skills and hooks, not just suggest them. That is the remediate rung, and it forces
one architecture decision.

TraceYield's report is a self-contained HTML file with no server. A browser page
cannot write to `.claude/`. So remediate needs a write-back path. Three honest
options:

1. Two-phase CLI. The report renders suggestions with checkboxes and writes the
   approved set to a small JSON; `traceyield apply` reads that file and writes the
   approved artifacts into `.claude/`. Stays stdlib-only, deterministic, and
   testable, and it matches the existing `cli.py` subcommand shape (`report` today,
   `apply` alongside it). This is the recommended path.
2. Copy-paste blocks plus a generated `apply.ps1` / `apply.sh`. Zero new machinery,
   but approval is manual and nothing is written for the user.
3. An in-report action that writes files via a launched helper. Most seamless, but
   it breaks the one-file, no-server property the project values.

Option 1 preserves every design value the project already holds (single tool,
stdlib only, per-machine data, test coverage) while genuinely delivering the "it
creates them" behavior. Each applied artifact should be idempotent (safe to run
twice), reversible (write to a clearly marked block or a backup), and logged, so an
approve-and-apply run is auditable the same way a report run is.

## What To Steal, What To Leave

Steal:

- The behavioral facet (B1-B5), read from the error taxonomy we already run.
- The pattern-to-artifact upgrade of `ERROR_RULES`, gated on a frequency threshold.
- The approve-and-apply CLI path that closes the loop to remediate.

Leave:

- The narrative prose and the flattery. TraceYield's voice is numbers and
  evidence, not a working-style story.
- Any figure produced by an LLM. In this report's own run, `/insights` fabricated
  time-spent estimates and described its self-check as an independent audit, both
  of which the user had to correct. A remediation engine that writes real files to
  `.claude/` cannot afford that failure mode.

## Guardrails Carried From The Failure

Because remediate writes real config, the honesty bar is higher than for a report.
Three rules, each a direct response to a documented `/insights` miss:

- Every suggestion cites its count and at least one example transcript. No artifact
  is generated from an uncounted claim.
- No number is ever LLM-produced. Counts come from the parser; if a value is
  unknown, the report says so rather than inventing it.
- No self-labeling as an audit or a verification the tool did not perform. The
  report describes what it counted, nothing more.

## Grounding: the captured artifact (2026-07-13)

The analysis above predated seeing a real report; a captured `/insights` run
(625 messages, 47 sessions) was then dissected line by line. It confirmed the
split and sharpened the take/leave calls. Two structural facts settle the design.

**Where `/insights` data actually lives.** The narrative is rendered from a
separate, `/insights`-only store at `~/.claude/usage-data/facets/<session>.json`,
not from the raw transcripts TraceYield parses. Each facet is a bag of *LLM
judgments* about one session:

```
outcome: "mostly_achieved"
user_satisfaction_counts: { "likely_satisfied": 1 }
claude_helpfulness, friction_counts, friction_detail, brief_summary, ...
```

Every field there is model-derived. TraceYield reads the transcripts underneath,
which carry the deterministic substrate and none of these judgments — so nothing
in `usage-data/facets` is a source TraceYield should trust as a count.

**The report's sections, sorted by what TraceYield should do with each:**

| `/insights` element | Nature | TraceYield verdict |
|---|---|---|
| Response-time distribution (user think-time) | Deterministic (timestamps) | **Take** — novel, honest describe-facet |
| Messages by time-of-day; parallel-session overlap | Deterministic (timestamps) | **Take** |
| Tool / language / session-type mix | Deterministic (counts) | **Take** — substrate already parsed |
| Lines +/-, files touched | Deterministic (Edit/Write payloads) | **Take** |
| Tool errors; friction clusters | Deterministic (taxonomy) | **Already have** (`ERROR_RULES`, B1-B5) |
| "What You Work On" (semantic clusters) | LLM-named | **Relabel** — `by_project` (by cwd) is the countable version |
| Outcomes (Fully/Mostly Achieved counts) | LLM-inferred | **Reject as counts** |
| Inferred Satisfaction counts | LLM-inferred | **Reject** — see below |
| "On the Horizon" | Speculation | **Reject** — run-rate/runway is the honest predict |
| CLAUDE.md / Skill / Hook / MCP suggestions | Artifact + evidence + "why" | **Take the template** — the prescribe→remediate prize |

**The satisfaction finding (a settled question).** The report's "Inferred
Satisfaction" is labeled *model-estimated*, and it is: it comes from the
`user_satisfaction_counts` bucket a model picked, not from the 1-5 the CLI's
periodic "How am I doing?" prompt collects. That explicit rating is not persisted
anywhere TraceYield can read — not in the transcripts, `history.jsonl`, or
`sessions/`; it appears to be telemetry only. Consequence: **TraceYield shows no
satisfaction number.** An explicit user rating would be the one honest, *measured*
satisfaction signal, but the data does not contain it. If Anthropic ever persists
the real rating into the transcripts, it becomes a legitimate describe-facet;
until then, any satisfaction figure is inference the doctrine rejects. (Field
note: even an honestly-given score would not help today — TraceYield never sees
it, and `/insights` infers rather than reads it.)

## Suggested Next Steps

This doc is analysis, not a decision. If the direction holds, it seeds two decision
records and their delivery work:

- A behavioral-signals facet (B1-B5) as a reporting addition over the existing
  taxonomy counts, paralleling the D-catalog panels.
- A prescribe-to-remediate engine: pattern-to-artifact rules with a frequency gate,
  plus a `traceyield apply` subcommand and the approve-and-apply flow, under the
  guardrails above.

Both are groundable in code that already exists (`classification.py`, `cli.py`,
the persisted metrics), which is the point: `/insights` did not reveal a missing
capability so much as show that the substrate is already here and only the bottom
of the loop is unbuilt.
