# Architecture: how the tool is built

*Living document. Update it whenever the tool changes: a new data source, a new
panel, a schema change, a new provider. Last updated 2026-07-12 (data health
monitoring now runs per provider: schema-drift fingerprinting + coverage-hole
detection for Claude and Codex, the latter with its own rate card and $0-cost
suppression — epic E2).*

> Background: [`claude-usage-data-research.md`](./claude-usage-data-research.md)
> is why the tool is shaped this way (parse local transcripts, five-line cost
> model, durable derived store). [`openai-usage-data-research.md`](./openai-usage-data-research.md)
> and [`adding-openai-support.md`](./adding-openai-support.md) are the forward
> plan for a second provider. This doc describes what exists **today**.

## What it is

A tool that parses Claude Code's own transcript logs and emits a
**self-contained interactive HTML dashboard** (`report.html`) of usage and
health. It runs entirely on your machine — no server, no framework. One command
does everything:

```bash
python report.py                       # parse → merge → record pricing → emit report → drift check
python -m unittest discover -s tests   # tests (stdlib; also runs under pytest)
python report.py --machine-dir         # print this machine's artifact dir and exit (used by run.cmd)
```

## The whole system on one page

```
~/.claude/projects/**/*.jsonl                     PRICING (in report.py)
        │                                               │
        ▼  analyze()                                    ▼  record_pricing()
  per-day buckets  +  per-session buckets         pricing_history.json  (shared, repo root)
        │                    │
        ▼ merge_daily()      ▼ merge_sessions()
  daily_metrics.json   session_metrics.json        (durable, per-machine, git-ignored)
        │                    │
        └──────────┬─────────┘
                   ▼  build_html()  (inline payload into HTML_TMPL)
              report.html   (self-contained; day/week/month + all charts computed client-side)
                   │
                   ▼  check_pricing_drift()  (best-effort warn vs published rates; never mutates)
```

Everything, config, parser, persistence, and the entire HTML/CSS/JS template,
lives in `report.py`. There is no templating engine; the dashboard is a raw
string literal (`HTML_TMPL`) with two placeholders (`__PAYLOAD__`,
`__PRICEROWS__`). Edits to the dashboard are string edits inside that literal.

## Data flow (one `python report.py` run)

`main()` (`report.py`) runs these steps in order:

1. **Parse**: `analyze()` globs every `*.jsonl` under `CLAUDE_PROJECTS`
   (`~/.claude/projects`). Each transcript line is timestamped; metrics are
   bucketed by the UTC **activity date** (`timestamp[:10]`), *not* the run date.
   That's why one run reconstructs the whole history still on disk. It returns
   two dicts: per-day buckets and per-session buckets.
2. **Merge**: `merge_daily()` folds new day-buckets into `daily_metrics.json`
   with `dict.update` semantics: **newest parse is authoritative per date, but
   dates whose transcripts have since rotated away are preserved.**
   `merge_sessions()` does the same keyed by `sessionId` into
   `session_metrics.json`. Both are durable stores, never regenerated from
   scratch.
3. **Record pricing**: `record_pricing()` stamps today's `PRICING` table into
   `pricing_history.json` keyed by today's date, building a time series of the
   *rates themselves*.
4. **Emit**: `build_html()` inlines the full payload (`days`, top `sessions`,
   `pricing`, error `meta`, `pricing_history`) into `HTML_TMPL` and writes
   `report.html`.
5. **Health check**: `scan_claude()` / `scan_codex()` fingerprint the *shape*
   of each provider's logs, `build_health()` diffs that against `SCHEMA_EXPECT`
   and reconciles coverage, `write_health()` persists `health.json`, and
   `print_health()` warns to stdout. See **Data health monitoring** below.
6. **Check pricing drift**: `check_pricing_drift()` fetches Anthropic's
   published pricing page and warns to stdout if any tier in `PRICING` no longer
   matches. Runs *after* the report is written, is fully best-effort (never
   raises, never mutates `PRICING`), and degrades to a "skipped" note offline.

**All day / week / month aggregation happens client-side** in the emitted HTML's
JS (`aggregate()`). The Python side only ever produces per-day buckets, so
adding a granularity or trend metric is a JS change in `HTML_TMPL`, not a Python
change.

## Parser internals (`analyze()`)

For each `*.jsonl` file, for each line:

- Skip lines with no `timestamp` or no dict `message` (guards, `except:
  continue` around per-line parse). The parse loop swallows per-line and
  per-file exceptions **by design**: one malformed transcript can't abort a run.
- **Bucket key** = `timestamp[:10]`. **Project** = the transcript's first
  path-segment under the root (`project_of()`), i.e. the `-`-encoded absolute
  path Claude Code uses as the folder name. **Session** = `sessionId`.
- **Content blocks** (`message.content[]`):
  - `tool_use` → increment `by_tool[name].calls`; remember `id → name` for the
    error join.
  - `tool_result` → increment `tool_results`; if `is_error`, increment
    `tool_errors`, `classify()` the result text into the error taxonomy, and
    attribute the error back to the originating tool via the `id → name` map.
- **Usage** (`message.usage`) → tier via `tier(message.model)`; unrecognized
  tier ⇒ the row is skipped (no cost attributed). Five token line-items are read
  and costed (below), then folded into the day, `by_model[tier]`,
  `by_project[proj]`, and the session accumulator.
- **Per-tool cost**: a turn's whole cost+output is attributed to its single
  `tool_use` (Claude Code serializes tool calls). Zero/multiple-tool turns land
  in pseudo-rows `(final response)` / `(multi-tool turn)`.

`new_day()`, `new_session()`, `new_model()`, `new_tool()` define the bucket
shapes; the serialize tail rounds costs and converts the session-id set to a
count. **Any new parser (e.g. a second provider) must emit this exact shape** so
the merge/HTML layers don't care where a bucket came from.

## Key domain logic (all in `report.py`)

- **`PRICING`**: hand-verified per-1M base rates (`opus`/`sonnet`/`haiku`), the
  source of truth. **All historical cost is recomputed at the *current* rates**
  for apples-to-apples comparison, so editing `PRICING` retroactively changes
  every day's reported cost, intentional. The pricing-history chart is the only
  place that shows how rates actually moved.
- **`cache_rates(inp)`**: cache multipliers fixed by the API: read `0.1×`,
  write-5m `1.25×`, write-1h `2×` the input rate.
- **Five token line-items**: fresh input (1×), cache write-5m (1.25×), cache
  write-1h (2×), cache read (0.1×), output. When the transcript gives only the
  aggregate `cache_creation_input_tokens` with no 5m/1h split, it's attributed to
  5m.
- **`tier(model)`**: maps a raw model id to `opus`/`sonnet`/`haiku`; `fable` →
  `opus`; anything unrecognized → `None` (row skipped).
- **`ERROR_RULES` / `classify()`**: the error taxonomy. An ordered list of
  `(name, substrings, title, fix)`; `classify()` lowercases the tool-result text
  and returns the first rule whose substrings match, else `"other"`. `ERROR_META`
  and the report's "errors & fixes" table derive from it automatically. Order
  matters (matched top-to-bottom).
- **`check_pricing_drift()` / `parse_pricing_page()`**: best-effort scrape of the
  published pricing page's **Model pricing** table (regex-scoped so the Batch and
  Fast-mode tables are ignored). A **drift alarm, not a live price source**: it
  warns on mismatch but never edits `PRICING`.

## Two analyses beyond a plain cost report

- **Per-session cost analysis**: sessions accumulate **globally** by `sessionId`
  (separate pass from day buckets) because one conversation's cost is split
  across the dates it touched. `build_html` embeds the top 50 by cost
  (`top_sessions()`); the full set persists to `session_metrics.json`. The report's
  "Top sessions by cost" table catches a single **runaway conversation** (usually
  a long, uncleared context re-read every turn).
- **Model-routing savings estimator**: this is why `by_model[tier]` carries the
  full five-part `tok` breakdown, not just cost. The client's `renderRoute()` /
  `costAtRates()` recompute the period's **Opus** tokens at Sonnet/Haiku rates
  (from the embedded `pricing` block) and scale by a user-set "routable share" to
  estimate `/model`-routing savings. It's an **upper bound**, framed as such.

## Data health monitoring (schema drift + coverage holes)

The parser is **resilient by design** (`except: continue`), which means a vendor
schema change never crashes: it *silently under-counts*: a renamed `usage`
field reads 0, a new model id maps to no tier and gets $0 attributed. So drift
can't be caught by watching for exceptions; it has to be **observed**. This layer
(all in `report.py`, structural twin of `check_pricing_drift()`: best-effort,
warns, never raises, never changes parsing) does that in two independent ways:

- **Schema drift.** `scan_claude()` / `scan_codex()` do one cheap, cost-free pass
  that fingerprints only the **load-bearing shapes**: `usage` keys, cache-write
  keys, content-block types and model ids (Claude); `type` / `payload.type`
  variants, `token_count` info keys and models (Codex), plus which dates the
  transcripts cover. `schema_drift()` diffs that fingerprint against
  `SCHEMA_EXPECT`, a **baseline seeded from real data** (so already-normal fields
  like Claude's `inference_geo` / `server_tool_use` / `<synthetic>` don't
  false-alarm). It flags: a **new** value in a semantic category (possible new
  field/model/type to review), an **unmapped model** (usage silently dropped:
  add it to `tier()`), and a **required top-level key that vanished** (a likely
  rename that blinds the parser). We deliberately do **not** fingerprint the ~70
  churny top-level telemetry keys Claude Code writes: only the handful the
  parser depends on, and only for disappearance. **The update ritual:** drift
  fires → you look → either fix the parser or, if benign, add the new value to
  `SCHEMA_EXPECT` (a small, reviewable edit that doubles as schema documentation).
- **Coverage holes.** `coverage()` reconciles the durable day-series against the
  dates transcripts still cover, separating **benign idle days** (no usage, no
  alarm) from **suspicious holes**: a date whose transcripts carry lines but the
  store recorded nothing, or a stored day with tool activity yet **$0 cost**
  (usage rows dropped). Plus a **freshness watermark** (days since the store last
  advanced: the actionable daily alarm if a scheduled run or parse is failing).
  Scoped from the first active day to today; dates whose transcripts have rotated
  away aren't reconstructable, so they're out of the actionable window.
  Coverage now runs **per provider** (E2-F4-S2): `build_health()` scopes Claude
  coverage to the global day-series and Codex coverage to each day's
  `by_provider["codex"]` facet, and `print_health()` reports holes/gaps/staleness
  labelled by provider. The **$0-cost suspicious** heuristic is gated by
  `coverage(..., zero_cost_suspicious=)` — **on** for Claude, but **off** for
  Codex, where a recognized-but-unpriced tier legitimately costs $0
  (volume-always / dollars-when-priced, Decision 0007 D3), so it is never a hole.

**Codex now has a cost model and coverage** (epic E2): a hand-verified rate card
(`CODEX_PRICING` / `CACHE["codex"]`, E2-F1-S2) and per-provider coverage/health
(E2-F4-S2). It is no longer fingerprint-only. Fingerprinting still runs every
pass — scanning `~/.codex/sessions` **baselines the format** so vendor schema
drift is caught as it happens (see
[`adding-openai-support.md`](./adding-openai-support.md)). Pricing **drift-check**
stays Claude-only, because OpenAI publishes no scrapeable pricing contract to
diff against, so `CODEX_PRICING` is hand-maintained (Decision 0007 D5). (Early
fingerprint runs surfaced real divergences: a `model_context_window` key the doc
didn't list, plain `gpt-5`, extra `payload.type` variants, and sessions with no
`token_count` events.)

`build_health()` assembles the record; `write_health()` persists the full
`health.json`; `_slim_health()` trims it (drops the per-date map + churny key
list, caps the gap list) for embedding in the report payload. Surfaced three
ways: the **Data health panel** at the top of `report.html`, **stdout warnings**
via `print_health()` (captured in `run.log`), and the machine-readable
`health.json` for future alerting.

## The emitted report (`HTML_TMPL`)

One self-contained page. The Python payload (`build_html`) carries `generated`,
`days`, top `sessions`, `pricing`, error `meta`, and `pricing_history`. Client-side
JS renders:

- **KPI cards**: cost, tokens, turns, sessions, tool-error rate, each with a
  delta vs. the previous period.
- **Trend chart**: day/week/month stepper (`aggregate()`), switchable metric.
- **Breakdowns**: cost by project, cost by model tier, five-line **token
  composition**, tool usage.
- **Model-routing savings estimator**, **tokens & cost per tool** (with an
  est-waste column), **errors & fixes** table (from the taxonomy), **top sessions
  by cost**, and **pricing over time**.
- **`clean()` project labels**: machine-agnostic: `PROJ_STRIP` derives the common
  leading path-segments across all project ids in the payload and strips them, so
  "Cost by project" reads as just the repo name on any machine (no hardcoded
  prefix).

## Persistence & files

| File | Scope | Tracked? | Written by |
|---|---|---|---|
| `machines/<id>/daily_metrics.json` | per-machine, durable | git-ignored | `merge_daily()` |
| `machines/<id>/session_metrics.json` | per-machine, durable | git-ignored | `merge_sessions()` |
| `machines/<id>/report.html` | per-machine output | git-ignored | `build_html()` |
| `machines/<id>/health.json` | per-machine (schema drift + coverage) | git-ignored | `write_health()` |
| `machines/<id>/run.log` | per-machine | git-ignored | `run.cmd` |
| `machines/<id>/usage.db` | per-machine, durable (canonical store) | git-ignored | `canonical.ingest()` |
| `pricing_history.json` | **shared, repo root** | **tracked** | `record_pricing()` |

**Per-machine namespacing.** The repo is shared across machines, but every machine
has its own `~/.claude/projects`, so each machine's *derived* artifacts live under
`machines/<machine_id>/`, never at the repo root, so one machine's run can't
clobber another's. `machine_id()` returns the sanitized hostname by default;
`TRACEYIELD_MACHINE` overrides it. `pricing_history.json` is the one durable store
that stays **shared at the repo root**, because it's stamped from the `PRICING`
table (code), not derived from any machine's transcripts, so it's identical
everywhere and non-personal (dates + public rates).

**The sharp edge: the durable store has no git backup.** `machines/` is
git-ignored, so each machine's `daily_metrics.json` / `session_metrics.json` is the
*only* copy of dates whose transcripts have since rotated away. `analyze()` only
reconstructs what transcripts still hold; the merge functions only *preserve* what's
already on disk. Lose that local store after transcripts rotate and the pre-rotation
history is **gone**. Back it up out-of-band.

**Known gap: installed-package data directory (tracked by E3-F4-S1).**
`src/traceyield/report.py`'s `HERE` anchor is `os.path.dirname` applied three
times to `os.path.abspath(__file__)`, which correctly walks up from
`src/traceyield/report.py` to the repo root in a source checkout (where
`machines/` and `pricing_history.json` live). Once the package is installed
from a wheel, `report.py` instead lives at
`.../site-packages/traceyield/report.py`, and three levels up from there
lands somewhere inside the Python install/venv — **not** a meaningful data
directory. As of E3-F1-S5 (CI now builds and installs the wheel and runs the
suite + `traceyield --help` / `--machine-dir` / `python -m traceyield` smoke
checks against that install), `traceyield --machine-dir` under a wheel
install exits 0 and prints *a* path, but that path is inside the install
location, not a sensible per-machine data directory — and nothing in this
package should ever write there. CI is deliberately restricted to the three
smoke commands above and never runs the full `traceyield report` pipeline,
specifically to avoid creating directories or files inside site-packages.
Defining the real installed-application data-directory policy (env var
override, XDG/platform-appropriate default, pipx-safe, never writing inside
the install dir) is explicitly out of scope here and is **E3-F4-S1**'s job
(see Decision 0008, Phase 4).

## Scheduling (`run.cmd`)

`run.cmd` is the Windows Task Scheduler wrapper. It's machine-agnostic: resolves
the repo dir from its own location (`%~dp0`), asks `report.py --machine-dir` where
this machine's folder is, and appends a one-line summary to
`machines\<machine_id>\run.log`. Interpreter is `python` on PATH unless `PYTHON`
is set. On macOS/Linux, wire `python report.py` into cron/launchd.

## Tests (`tests/test_report.py`)

The suite (also runs under pytest) builds fixture
transcripts in a temp dir with **hand-computable token counts** and asserts exact
costs. The parametrized `analyze(root=…)`, `merge_daily(…, path=…)`,
`merge_sessions(…, path=…)`, and `record_pricing(path=…)` seams exist so tests
never touch the real `~/.claude` or a machine's real data under `machines/`. If
you change the cost formula, cache multipliers, error taxonomy, or the
session/`by_model` shapes, update the fixtures' expected numbers.

## Conventions & gotchas

- **Keep `__PAYLOAD__` / `__PRICEROWS__` intact** in the HTML template.
- **Resilient parsing by design.** The parse loop swallows per-line/per-file
  exceptions. Don't add logic that assumes every line succeeds.
- **Windows-first, multi-machine.** `run.cmd` is portable; artifacts are namespaced
  per machine; `clean()` derives the project-label prefix from data.
- **History was purged.** Earlier commits contained personal usage data; it was
  removed from the entire history with a `git filter-branch` rewrite. If the repo
  is re-shared, that rewrite must be force-pushed.

## Extending it

- **New trend metric / granularity / panel** → JS change in `HTML_TMPL` (the
  Python side only emits per-day buckets).
- **New error category** → add a tuple to `ERROR_RULES`; the table updates itself.
- **Pricing change** → edit `PRICING`; the drift check re-verifies each run.
- **New provider (e.g. OpenAI Codex)** → write a second parser that emits the same
  per-day/per-session dict shape, then generalize the few Anthropic-specific
  assumptions. Full plan in [`adding-openai-support.md`](./adding-openai-support.md);
  source research in [`openai-usage-data-research.md`](./openai-usage-data-research.md).
  On the **canonical** side this is already proven: `CodexProvider` in
  `canonical.py` is one new class emitting the shared `Rec` stream, see
  [`codexprovider.md`](./codexprovider.md).

## Change log

- **2026-07-12**: CI now builds and installs the wheel (`python -m build
  --wheel` + `pip install dist/*.whl`) and runs the suite plus `traceyield
  --help` / `--machine-dir` / `python -m traceyield` smoke checks against
  that install, instead of only an editable install; a CI step asserts
  `traceyield.__file__` resolves under `site-packages`, not the checkout, so
  an editable-only run can't masquerade as a wheel run. Documented the
  installed-package data-directory gap above (tracked by **E3-F4-S1**).
- **2026-07-10**: Canonical store: `CodexProvider` added to `canonical.py`
  (parallel to `ClaudeProvider`, registered in `default_providers()`), parsing
  Codex CLI rollout logs (`~/.codex/sessions/**/*.jsonl`) into the same neutral
  `Rec` stream keyed `provider='codex'`: one new class, no schema / `write()` /
  test-harness change, validating the provider abstraction. Codex-specific math:
  fresh input = `input_tokens − cached_input_tokens`, cache-read =
  `cached_input_tokens`, no cache-write tier; `reasoning_output` recorded as a
  subset of output; both `token_count` shapes (nested `info.last_token_usage`
  delta + old flat/cumulative diff); `codex_tier()` for the gpt-5* family; a
  failure heuristic on `function_call_output` (`exit_code`/`success:false`) reusing
  `report.classify()`. Real-corpus smoke: 37 Codex files fold into one `usage.db`
  alongside Claude, idempotent on re-ingest. Reference: [`codexprovider.md`](./codexprovider.md).

- **2026-07-10**: Canonical store (first slice): `canonical.py`: a
  provider-neutral, turn/tool/segment-grained SQLite db (`machines/<id>/usage.db`)
  that captures far more than the daily/session aggregates (per-tool latency, error
  class, git branch, prompts/responses, redacted-reasoning provenance). Producer/
  consumer split (a `Provider` yields neutral `Rec`s; `write()` upserts them
  provider-blind), so a new provider is one class. `ClaudeProvider` implemented;
  `CodexProvider` is the planned twin. Dual-written best-effort from `main()`
  (`ingest_canonical()`), never touching the existing JSON/report. Opt-in verbatim
  via `TRACEYIELD_CAPTURE`. Design + measured size model:
  [`canonical-data-model.md`](./canonical-data-model.md). Tests: `test_canonical.py`.
- **2026-07-10**: Data health monitoring: `scan_claude()`/`scan_codex()` schema
  fingerprinting vs. a `SCHEMA_EXPECT` baseline, `coverage()` idle-vs-hole
  reconciliation + freshness watermark, `health.json`, a Data health report panel,
  and `run.log` warnings. Codex logs fingerprinted (drift-baselined) ahead of a
  full parser.
- **2026-07-10**: Per-machine artifact namespacing (`machines/<id>/`);
  machine-agnostic `run.cmd` and `clean()` project labels; `machines/` git-ignored,
  `pricing_history.json` kept shared. Pricing drift check added.
- **2026-07-10**: Initial tool: transcript parser, five-line cost model, daily
  metrics, per-session analysis, model-routing estimator, error taxonomy,
  self-contained HTML report.
