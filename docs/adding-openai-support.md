# Adding OpenAI (Codex CLI) support to the report

*How to extend `report.py` to parse OpenAI Codex CLI logs and emit the same
dashboard, alongside (or instead of) Claude Code. Read
[`openai-usage-data-research.md`](./openai-usage-data-research.md) first — this
doc assumes the schema and cost model established there.*

> **Status update (2026-07-10).** The **canonical store** already has a Codex
> parser: `CodexProvider` in `canonical.py` ingests `~/.codex/sessions` into the
> shared `Session`/`Turn`/`ToolCall`/`Segment`/`RawEvent` stream keyed
> `provider='codex'` (see [`codexprovider.md`](./codexprovider.md) and the
> canonical [change log](./architecture.md#change-log)). This document covers the
> **other** integration path — teaching the `report.py` aggregate/HTML pipeline
> to *emit a Codex dashboard*. The two are complementary: the canonical path
> captures the raw neutral records; the aggregate flip (roadmap E1-F2-S1) will
> derive the report from that store rather than re-parsing.

## Goal & scope

Produce the **same `report.html`** — same day/week/month stepper, KPI cards,
per-project/per-model/per-tool breakdowns, session table, routing estimator —
driven by **Codex CLI rollout logs** (`~/.codex/sessions/**/*.jsonl`) instead of
(or in addition to) Claude Code transcripts.

In scope: the Codex CLI path (high fidelity). Out of scope for v1: the OpenAI
Costs API and ChatGPT `conversations.json` (both are cost-blind or org-level —
see the research doc §2–3). Design the seam so they can slot in later.

## Why this is mostly a parser change

The Python side already reduces everything to **provider-neutral per-day and
per-session buckets** (`new_day()` / `new_session()` in `report.py`), and the
HTML template just renders those buckets. Nothing in the aggregation, merge,
persistence, or most of the JS cares where the numbers came from. The coupling
to "Claude" lives in a small, enumerable set of places (below). The job is:
**write a second parser that emits the existing dict shape, then generalize the
few Anthropic-specific assumptions.**

## The Claude-specific coupling points in `report.py`

Everything that must change or generalize, with current line references:

| Location | Today (Claude-only) | Needs |
|---|---|---|
| `CLAUDE_PROJECTS` (`report.py:26`) | globs `~/.claude/projects` | a second root `~/.codex/sessions` |
| `PRICING` (`:58`) | `opus/sonnet/haiku` | OpenAI model rates; provider-scoped |
| `tier(model)` (`:68`) | matches opus/sonnet/haiku substrings | map `gpt-5*-codex`, `gpt-5.5`, … |
| `cache_rates()` (`:67`) | 5m ×1.25, 1h ×2 write tiers | OpenAI has **no cache-write tier** (read ×0.1 only) |
| `analyze()` usage block (`:183–205`) | reads `message.usage` Anthropic field names | Codex `token_count` event, `input−cached` fresh-input math |
| `analyze()` message/tool loop (`:163–182`) | Claude `content[]` blocks, `is_error` flag | Codex `response_item` / `function_call(_output)` lines |
| `ERROR_RULES` / `classify()` (`:77–108`) | Claude-harness error strings | Codex-specific error rules |
| HTML `__PRICEROWS__` + pricing chart (`:335`, JS `:709`) | hardcodes `["opus","sonnet","haiku"]` | derive tier list from data/pricing |
| `renderRoute()` JS (`:666`) | assumes `opus → sonnet/haiku` | generalize "expensive tier → cheaper tier" |
| `check_pricing_drift()` (`:297`) | scrapes Anthropic page | second scraper for OpenAI rates (optional) |

Note the `by_model` / `by_project` / session rendering is **already generic** —
it renders whatever tier and project keys exist. That's the leverage.

## Recommended architecture: a provider seam

Keep the single-file, stdlib-only, offline character. Introduce a light notion
of **provider** rather than forking the script.

### 1. Provider-scoped config

```python
PROVIDERS = {
    "claude": {
        "root": os.path.expanduser("~/.claude/projects"),
        "pricing": {  # (input, output) per 1M
            "opus": (5.00, 25.00), "sonnet": (2.00, 10.00), "haiku": (1.00, 5.00),
        },
        "cache_write": True,   # 5m ×1.25, 1h ×2
    },
    "codex": {
        "root": os.path.expanduser("~/.codex/sessions"),
        "pricing": {           # verify at ship time — see research §1.6
            "gpt-5-codex":   (1.25, 10.00),
            "gpt-5.3-codex": (1.75, 14.00),
            "gpt-5.5":       (5.00, 30.00),
        },
        "cache_write": False,  # cached read ×0.1 only; no write premium
    },
}
```

Cost model stays "fresh-input ×1 + cache-read ×0.1 + writes + output". For Codex,
`cache_write` is off so the two write line-items are just zero — the existing
5-field `tok` dict is a **superset** and needs no shape change. That's deliberate:
`daily_metrics.json` keeps one schema across providers.

### 2. `tier()` becomes provider-aware

```python
def tier(model, provider="claude"):
    if not model: return None
    m = model.lower()
    if provider == "codex":
        # exact-ish match against the provider's pricing keys; unknown → skip row
        for key in PROVIDERS["codex"]["pricing"]:
            if key in m: return key
        if "gpt-5" in m: return "gpt-5.5"   # sane fallback for unpriced 5.x
        return None
    # claude
    if "opus" in m or "fable" in m: return "opus"
    if "sonnet" in m: return "sonnet"
    if "haiku" in m: return "haiku"
    return None
```

Unknown/unpriced model → `None` → row skipped (matches today's behavior).

### 3. A second parser: `analyze_codex(root)`

Emits the **exact same `(days, sessions)` dict shape** as `analyze()`. Key
differences from the Claude parser (all grounded in research §1.3–1.5):

- **One session per file.** `session_meta.payload.id` is the session key;
  `payload.cwd` is the project. Read `turn_context.payload.model` to know the
  active tier (it can change mid-session).
- **Usage comes from `event_msg`/`token_count`, not the message.** Prefer
  `payload.info.last_token_usage` for per-turn deltas; fall back to diffing
  successive `total_token_usage` for old flat-format files.
- **Fresh input = `input_tokens − cached_input_tokens`** (input is inclusive of
  cached). `cache_read = cached_input_tokens`. `output = output_tokens`. Do
  **not** add `reasoning_output_tokens` (already in output). Write tiers = 0.
- **Tools = `function_call` / `function_call_output`** joined by `call_id`
  (same id→name trick the Claude parser already uses). Tool names are Codex's
  (`shell`, `apply_patch`, `read_file`, …).
- **Per-tool cost attribution** pairs a turn's `last_token_usage` with the
  `function_call` in that turn — because usage is a *separate line*, not attached
  to the tool call. Single-tool-turn assumption still mostly holds; keep the
  `(multi-tool turn)` / `(final response)` pseudo-rows.

Skeleton (illustrative — mirror `analyze()`'s bucketing exactly):

```python
def analyze_codex(root=PROVIDERS["codex"]["root"]):
    files = glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True)
    days, sessions = defaultdict(new_day), defaultdict(new_session)
    price = PROVIDERS["codex"]["pricing"]
    for f in files:
        sid, cwd, model = None, None, None
        idname, prev_total = {}, None
        try:
            for line in open(f, encoding="utf-8"):
                line = line.strip()
                if not line: continue
                try: o = json.loads(line)
                except: continue
                ts, typ, p = o.get("timestamp"), o.get("type"), o.get("payload") or {}
                if not ts: continue
                d = ts[:10]; D = days[d]

                if typ == "session_meta":
                    sid = p.get("id") or sid
                    cwd = p.get("cwd") or cwd
                elif typ == "turn_context":
                    model = p.get("model") or model

                elif typ == "response_item":
                    pt = p.get("type")
                    if pt == "function_call":
                        nm = p.get("name", "?"); idname[p.get("call_id")] = nm
                        D["by_tool"][nm]["calls"] += 1
                    elif pt == "function_call_output":
                        D["tool_results"] += 1
                        if codex_is_error(p.get("output")):
                            D["tool_errors"] += 1
                            D["errors"][classify_codex(p.get("output"))] += 1
                            nm = idname.get(p.get("call_id"))
                            if nm: D["by_tool"][nm]["err"] += 1
                    # message role=assistant → count a turn (see note below)

                elif typ == "event_msg" and p.get("type") == "token_count":
                    info = p.get("info") or {}
                    u = info.get("last_token_usage")
                    if u is None:                      # old flat format / cumulative-only
                        tot = info.get("total_token_usage") or {
                            k: p.get(k) for k in ("input_tokens","output_tokens","cached_input_tokens")}
                        u = _delta(tot, prev_total); prev_total = tot
                    tr = tier(model, "codex")
                    if tr is None or not u: continue
                    inp = (u.get("input_tokens",0) or 0) - (u.get("cached_input_tokens",0) or 0)
                    cr  = u.get("cached_input_tokens",0) or 0
                    out = u.get("output_tokens",0) or 0
                    ri, ro = price[tr]
                    cost = (max(inp,0)*ri + cr*ri*0.1 + out*ro) / 1e6
                    # …fold cost/tokens into D, D["by_model"][tr], D["by_project"][cwd],
                    #   and sessions[sid] exactly like analyze() does…
        except: continue
    return _serialize(days), _serialize(sessions)   # same tail as analyze()
```

Reuse `new_day()`, `new_session()`, `new_tool()`, `new_model()`, and the
serialize tail verbatim — the output must be byte-compatible with the Claude
path so `merge_daily()` / `merge_sessions()` / `build_html()` don't care which
parser produced a bucket.

Helpers to add: `codex_is_error(output)` (heuristic: `success == False`,
non-zero exit, stderr-looking text — research §1.7) and `classify_codex()` with
a Codex-specific `ERROR_RULES_CODEX`.

### 4. Storage: keep providers side-by-side, don't merge blindly

Sessions and days from different providers must not collide. Two clean options:

- **(Recommended) Per-provider files under the machine dir:**
  `machines/<id>/codex/daily_metrics.json`, `…/codex/session_metrics.json`, and
  a combined `report.html` (or a per-provider one). Cleanest isolation; each
  provider merges independently with the existing `merge_*` functions (they
  already take a `path=`).
- **Single store with a `provider` field** on every bucket/session and a
  provider filter in the UI. More UI work; only worth it if you want a unified
  cross-provider view in one page.

Given the tool is already per-machine namespaced, **per-provider subfolders** are
the smallest, safest change and keep the durable-store semantics intact. A
`--provider codex|claude|all` CLI flag (default `all`) picks which roots to
parse and which stores to write.

### 5. HTML generalizations (small, JS-only)

- **Pricing table / chart:** derive the tier list from `DATA.pricing` keys
  instead of the literal `["opus","sonnet","haiku"]` (`report.py:335`, JS
  `:709`, `:713`). Assign colors from a palette by index.
- **Routing estimator (`renderRoute`, JS `:666`):** today it hardcodes
  "Opus → Sonnet/Haiku". Generalize to "most-expensive tier present → any
  cheaper tier", picking the default source tier as the priciest one with
  usage. The `costAtRates()` math is already rate-agnostic.
- **Copy:** the "How to read this report" section names Anthropic's cache tiers
  and `/model`. Gate the cache-write explanation on `cache_write`, and swap
  `/model` guidance for Codex's `/model` equivalent when rendering the Codex
  report. Keep it provider-aware via a small `DATA.provider` field in the
  payload.

Everything else in the template (KPI cards, trend, by-project, by-model,
by-tool, sessions, token composition) already renders whatever keys the payload
carries — no change.

### 6. Pricing drift check (optional, best-effort)

Mirror `check_pricing_drift()` with a second scraper against
`developers.openai.com/api/docs/pricing`, or ingest **LiteLLM's**
`model_prices_and_context_window.json` (what ccusage uses) as the live source.
Same rules as today: never mutate `PRICING`, never fail the run, print a
"skipped" note when offline or the layout changed. OpenAI has no pricing API, so
scrape-or-LiteLLM is the only option (research §1.6).

## Tests

Extend `test_report.py` the same way it tests the Claude path: build **fixture
Codex rollout `.jsonl`** in a temp dir with hand-computable token counts and
assert exact costs through `analyze_codex(root=…)`. Cover specifically:

- fresh-input math: `input_tokens=1000, cached_input_tokens=800` → 200 fresh
  ×input + 800 ×input×0.1 + output ×output-rate.
- `reasoning_output_tokens` is **not** double-billed.
- **both** token_count formats: new nested `info.last_token_usage` **and** old
  flat/cumulative (assert the delta path).
- a `function_call` + failing `function_call_output` increments `tool_errors`
  and the right `by_tool[...]["err"]`.
- a `codex exec` file with **no** token_count events → cost 0, flagged, not a
  crash (research §1.7).
- model switch mid-session (two `turn_context` lines) attributes later turns to
  the new tier.

Keep the parametrized `path=`/`root=` seams so tests never touch a real
`~/.codex`.

## Suggested delivery order

1. **Parser + fixtures first** (`analyze_codex` + tests) — pure Python, no UI,
   fully verifiable against hand-computed costs. Highest-risk, isolate it.
2. **Provider config + storage seam** (`PROVIDERS`, per-provider paths,
   `--provider` flag, provider-aware `tier()`).
3. **Wire into `main()`** — parse each selected provider, merge into its own
   store, emit report(s). Smoke-test against a real `~/.codex` if available.
4. **HTML generalizations** — tier-list-from-data, generic routing, provider-
   aware copy.
5. **Optional:** OpenAI pricing drift check; then revisit ChatGPT-export /
   Costs-API as clearly-labeled low-fidelity add-ons.

## Open decisions to confirm before building

- **Project labels from real paths.** Codex `cwd` is a real filesystem path, not
  Claude's `-`-encoded dir name. Decide whether to reuse the JS `clean()`
  common-prefix stripping (encode cwd the same way) or just show the basename.
- **Unified vs per-provider report.** One combined `report.html` with a provider
  toggle, or separate `report.html` per provider. Per-provider is simpler and
  matches the storage recommendation.
- **Pricing source of truth.** Hand-maintained `PROVIDERS["codex"]["pricing"]`
  (like the Anthropic table) vs pulling LiteLLM. Hand-maintained keeps the
  offline/no-dependency property; LiteLLM reduces drift-maintenance. Recommend
  hand-maintained + drift check, consistent with the existing design.
