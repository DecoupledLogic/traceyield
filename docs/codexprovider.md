# CodexProvider: parsing Codex CLI rollout logs into the canonical store

*Reference for the `CodexProvider` class in `traceyield.providers.codex`
(moved out of `canonical.py` in E3-F2-S4; `canonical.CodexProvider` still
resolves, re-exported, same class object). Read
[`canonical-data-model.md`](./canonical-data-model.md) (esp. §4.1, §6) and
[`openai-usage-data-research.md`](./openai-usage-data-research.md) (§1.2–1.7)
first. This doc assumes both. Shipped in work item E1-F1-S1.*

## What it is

`CodexProvider` is the second provider in the canonical store, parallel to
`ClaudeProvider` (`traceyield.providers.claude`). It parses OpenAI Codex CLI
rollout logs (`~/.codex/sessions/**/*.jsonl`) and yields the **same neutral
`Rec` stream** (`Session`/`Turn`/`ToolCall`/`Segment`/`RawEvent`) that
`canonical.write()` upserts provider-blind, keyed by `provider='codex'`. It
is the proof the abstraction holds: **one new class, no schema / `write()` /
test-harness change.** Claude and Codex records coexist in one `usage.db`,
distinguished only by the `provider` column.

The seam mirrors `ClaudeProvider` exactly, `__init__(self, root=None)` (defaults
to `$CODEX_HOME` or `~/.codex/sessions`, so tests pass a temp dir and never touch
the real corpus), `roots()`, and `parse_file(path)` yielding `Rec`s, so
`ingest()` calls it identically -- formalized as the `traceyield.providers.base.Provider`
protocol as of E3-F2-S4. It's registered in `canonical.default_providers()`.
Both provider classes depend only on the neutral layer (`traceyield.models`,
`traceyield.paths`, `traceyield.classification`, `traceyield.transcripts` --
`ClaudeProvider` additionally uses `traceyield.pricing`; `CodexProvider`
deliberately does not, since Codex tiers via its own `codex_tier()`, not
`pricing.tier()`), never on `canonical.py` or `report.py`.

## Record-emission mapping (Codex line → neutral Rec)

Every rollout line is one JSON object: `{ "timestamp", "type", "payload" }`.

| Codex line (`type` / `payload.type`) | Emit |
|---|---|
| `session_meta` | accumulate `id`→sid, `cwd`, `cli_version`, `originator`(→`source`); yield one `Session` at end of file. Tolerates old/new naming (`originator` in real data; `source`/`model_provider` in other versions). |
| `turn_context` | remember `payload.model` (active tier, **can change mid-file**), `approval_policy`, `sandbox_policy` (dict or string) → onto the `Session` and subsequent `Turn`s |
| `event_msg` + `payload.type=="token_count"` | yield `Turn`: **synthesize `turn_id = f"{sid}:{seq}"`** (Codex token events carry no uuid). Tokens per §"Token math" below |
| `response_item` + `payload.type=="function_call"` | yield `ToolCall(name, kind=tool_kind(name))` keyed by `call_id`; remember `call_id→(turn_id, ts_ms)`; yield `Segment("tool_args", text=arguments)` |
| `response_item` + `payload.type=="function_call_output"` | yield `ToolCall(name=None, ok, error_class, exit_code)` (upsert on `call_id`) + `Segment("tool_output", text)` |
| `response_item` + `payload.type=="message"` role assistant/user | `Segment("response"/"prompt", text=<joined content-part text>)` |
| `response_item` + `payload.type=="reasoning"` | `Segment("reasoning", text=<summary text or None>, text_available=bool(summary))`: the summary counts as text; `encrypted_content` is hashed for provenance when there's no summary |
| `compacted` | set a pending flag; the next synthesized `Turn` gets `compacted=True` |
| everything else (`agent_reasoning`/`user_message`/`agent_message` echoes, `world_state`, `inter_agent_communication`, `turn_aborted`, review-mode markers, unknown) | `RawEvent` (escape hatch), so nothing is double-counted |

## Token math (Codex-specific, do **not** reuse Claude's)

The `token_count` event reports a five-field `TokenUsage`
(`input_tokens`, `cached_input_tokens`, `output_tokens`,
`reasoning_output_tokens`, `total_tokens`). The per-turn delta is taken as:

1. **Fresh input is exclusive-of-cache, computed:** `input_fresh =
   input_tokens − cached_input_tokens`; `cache_read = cached_input_tokens`;
   `cache_write_5m = cache_write_1h = 0` (OpenAI has no billed cache-write tier).
   Claude's `input_tokens` is *already* fresh. That path is not reused.
2. **`reasoning_output` is real for Codex**: set from `reasoning_output_tokens`.
   It is a **subset of `output`**, so cost projections must not add it on top.
3. **Delta source, three shapes handled:** prefer `info.last_token_usage` (the
   per-turn delta). Fall back to diffing successive `info.total_token_usage`
   (cumulative) for files that omit `last_token_usage`. Also handle the old
   **flat** shape (`payload.input_tokens` with no `info` nesting). The first
   observation with no prior baseline is taken as the delta itself.
4. **Tier map:** `codex_tier()` maps the gpt-5* family; it's forward-tolerant:
   an unseen `gpt-5*` point release tiers by its own id rather than dropping to
   `None`. An unrelated model → `tier=None`, but the turn is **still recorded**
   (raw model never lost, same policy as `ClaudeProvider`).

## Tool failure heuristic

Codex `function_call_output` has no `is_error` boolean. The `output` field is
usually a JSON string of the shape
`{"output": "<stdout/stderr>", "metadata": {"exit_code": N, "duration_seconds": …}}`;
sometimes a structured `{"content", "success": bool}`; sometimes plain text.
`_codex_tool_output()` parses all three and returns `(text, exit_code, success)`.
A call is **failed** when `exit_code` is non-zero or `success is False`; then
`ok=0` and `error_class = report.classify(text)`. The existing unified taxonomy
already catches the real cases (e.g. `fatal: not a git repository` → `git_error`,
shell quoting → `shell_syntax`). `exit_code` is captured on the `ToolCall` (Claude
leaves it NULL). Plain-text output is treated as success with a NULL `exit_code`.

## Format-drift notes (real corpus)

- **Headless `codex exec` sessions omit `token_count` events**: such files yield
  tools/segments/session with **0 turns** and must not crash (validated: 15 of 37
  real files have no `token_count`).
- **Compaction** rewrites history via `compacted` items; using `last_token_usage`
  deltas (not the largest cumulative) avoids double-counting.
- Models seen in the real corpus: `gpt-5`, `gpt-5-codex`. Timestamps end in `Z`.
- `sandbox_policy` may be a string (older) or a `{"mode": …}` dict (real data).
  Both are stored (dict serialized to JSON).

## Test coverage

`tests/test_canonical.py` gains a `codex_rollout()` fixture next to the Claude
helpers and asserts via SQL, using the `CodexProvider(root=tmp)` / `open_db(":memory:")`
seam: fresh-input math; `reasoning_output` not folded into `output`; both
`token_count` shapes (asserting the delta path); a failing `function_call_output`
→ `ok=0` + right `error_class` + `exit_code`; a `codex exec` file with no
`token_count` → 0 turns / no crash; a mid-session model switch picking up the new
tier; a reasoning summary → `Segment("reasoning")` with text vs. count-only → not;
and idempotent re-ingest. Run: `python -m unittest tests.test_canonical`.
`tests/test_providers.py` (E3-F2-S4) separately covers the protocol itself --
`CodexProvider` (and `ClaudeProvider`) structurally satisfying
`traceyield.providers.base.Provider`, and an `ast` guard proving
`traceyield/providers/codex.py` imports only the neutral layer.

## Roadmap context

Two sibling work items were **blocked on trusting the canonical store**, so they
follow CodexProvider (see [`canonical-data-model.md`](./canonical-data-model.md)
§8):

- **E1-F2-S1, aggregate flip:** regenerate `daily_metrics.json` /
  `session_metrics.json` from a `GROUP BY` over `usage.db` instead of dual-writing.
- **E1-F3-S1, raw_event age-out:** periodic `UPDATE raw_event SET raw=NULL WHERE
  ts < now−90d` (the per-event 32 KB clamp already ships).
