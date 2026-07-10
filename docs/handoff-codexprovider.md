# Handoff — build the CodexProvider slice

*Session handoff, 2026-07-10. Picks up after the canonical-store first slice
(commit `9a7fe2c` on branch `canonical-store`). Read
[`canonical-data-model.md`](./canonical-data-model.md) (esp. §4.1, §6) and
[`openai-usage-data-research.md`](./openai-usage-data-research.md) (§1.2–1.7)
first — this doc assumes both.*

## Where we are

The canonical store exists and works: `canonical.py` holds the SQLite schema,
five neutral `Rec` dataclasses (`Session`/`Turn`/`ToolCall`/`Segment`/`RawEvent`),
shared helpers (`sha`, `tool_kind`, `_ms`), a provider-blind `write()`, and
`ingest()`. **`ClaudeProvider` is done and tested**; it's the reference to mirror.
Verified on the real 192 MB corpus (32.8k turns, 16.5k tool_calls, 50.4k
segments, 30 MB structural db, idempotent re-ingest). 81/81 tests green.

The whole point of the abstraction: **CodexProvider is one new class emitting the
same `Rec` stream. No schema, `write()`, or test-harness change.**

## The task

Add `class CodexProvider` to `canonical.py` (parallel to `ClaudeProvider`) that
parses Codex rollout logs (`~/.codex/sessions/**/*.jsonl`) and yields the same
neutral records. Then register it in `default_providers()` and add
`test_canonical.py` fixtures. Codex data is present on this machine
(`~/.codex/sessions`, 37 files, 6.6 MB) for a real smoke test.

## Record-emission mapping (Codex line → neutral Rec)

| Codex line (`type` / `payload.type`) | Emit |
|---|---|
| `session_meta` | accumulate `id`→sid, `cwd`, `model_provider`, `cli_version`, `source`; yield `Session` at end of file (like Claude) |
| `turn_context` | remember `payload.model` (active tier; can change mid-file), `approval_policy`, `sandbox_policy` → onto `Session` |
| `event_msg` + `payload.type=="token_count"` | yield `Turn` — **synthesize `turn_id = f"{sid}:{seq}"`** (Codex token events have no uuid). Tokens per §4.1 below |
| `response_item` + `payload.type=="function_call"` | yield `ToolCall(name=..., kind=tool_kind(name))` keyed by `call_id`; remember `call_id→(turn_id, ts_ms)`; yield `Segment("tool_args", text=payload.arguments)` |
| `response_item` + `payload.type=="function_call_output"` | yield `ToolCall(name=None, ok=..., error_class=...)` (upsert on `call_id`) + `Segment("tool_output", text=output)` |
| `response_item` + `payload.type=="message"` role assistant/user | `Segment("response"/"prompt", text=output_text/input_text)` |
| `response_item` + `payload.type=="reasoning"` (some versions) | `Segment("reasoning", text=summary or None, text_available=bool(summary))` — **summary counts as text** (already the decided rule) |
| `compacted` | set `Turn.compacted=True` context flag (or mark the next turn) |
| anything else (`world_state`, `inter_agent_communication`, unknown) | `RawEvent` (escape hatch) |

## The Codex-specific reconciliations (don't copy Claude's math)

1. **Fresh input is exclusive-of-cache, computed:** `input_fresh =
   input_tokens − cached_input_tokens`; `cache_read = cached_input_tokens`;
   `cache_write_5m = cache_write_1h = 0` (OpenAI has no cache-write tier).
   Claude's `input_tokens` is *already* fresh — do NOT reuse that path.
2. **`reasoning_output` is real for Codex** — set it from
   `reasoning_output_tokens`; it's a **subset of `output`**, so do not add it on
   top when anyone later computes cost.
3. **Prefer `info.last_token_usage`** for per-turn deltas; fall back to diffing
   successive `info.total_token_usage` (cumulative) for old flat-format files —
   see research §1.7. Handle the old **flat** `payload.input_tokens` shape too.
4. **Tier map:** reuse `report.tier()`? No — `report.tier()` is Claude-only
   (opus/sonnet/haiku). Codex needs its own map (`gpt-5-codex`, `gpt-5.3-codex`,
   `gpt-5.5`, …). Either add a `provider` arg to `tier()` (see
   `adding-openai-support.md` §2) or a small `codex_tier()` in `canonical.py`.
   Unknown model → `tier=None` but **still record the turn** (raw model never
   lost — same policy as ClaudeProvider).
5. **Error signal is a heuristic, not a boolean.** Codex `function_call_output`
   has no `is_error`. Detect failure from `success:false`, a non-zero shell
   `exit_code`, or stderr-shaped output. Then map to the unified `error_class`.
   `report.classify()`'s substrings are Claude-harness-specific — a
   Codex-tuned classifier (or an extended rule set) is likely needed. Capture
   `exit_code` on the `ToolCall` when the shell output carries one (Claude leaves
   it NULL).
6. **Model provider label:** `session_meta.model_provider` may be `openai` or a
   third-party (Codex can proxy). Store it; still tier by the model id.

## Test plan (mirror the Claude tests in `test_canonical.py`)

Build fixture rollout `.jsonl` with hand-computable tokens and assert via SQL:
- fresh-input math: `input_tokens=1000, cached_input_tokens=800` → `input_fresh=200`,
  `cache_read=800`, writes `0`.
- `reasoning_output` recorded and **not** folded into `output`.
- both `token_count` shapes: nested `info.last_token_usage` **and** old
  flat/cumulative (assert the delta path).
- `function_call` + failing `function_call_output` → `ok=0`, right `error_class`,
  `exit_code` captured.
- a `codex exec` file with **no** `token_count` events → tools/segments recorded,
  0 turns, no crash (research §1.7).
- model switch mid-session (two `turn_context`) → later turns get the new tier.
- reasoning summary present → `Segment("reasoning")` with text; count-only → not.

Keep the `ClaudeProvider(root=tmp)` / `open_db(":memory:")` seam pattern; add a
`codex_rollout()` fixture helper next to the existing `assistant()`/`tool_result()`.

## Definition of done

- `CodexProvider` in `canonical.py`, registered in `default_providers()`.
- Codex fixtures + assertions in `test_canonical.py`; full suite green.
- Smoke: `python canonical.py` ingests real `~/.codex/sessions` and prints
  sane counts; re-run idempotent; both providers land in one `usage.db`
  distinguished by the `provider` column.
- `docs/architecture.md` change-log + `docs/adding-openai-support.md` updated to
  note the canonical path (not just the aggregate path) now has a Codex parser.

## Roadmap context (the other two deferred items)

Sibling work items, both **blocked on trusting the canonical store** (so they come
after CodexProvider): **(a) aggregate flip** — regenerate `daily_metrics.json` /
`session_metrics.json` from a `GROUP BY` over `usage.db` instead of dual-writing;
**(b) raw_event age-out** — periodic `UPDATE raw_event SET raw=NULL WHERE ts <
now−90d` (the per-event 32 KB clamp already ships). See
`canonical-data-model.md` §8.
