# Canonical data model — a provider-neutral store for CLI-agent usage

*Design doc. Status: proposed (2026-07-10). This defines an abstract data model
that fits **both** Claude Code transcripts and OpenAI Codex rollout logs, and
captures as much of each as we can — not just the fields the current report
needs. Source inventories: [`claude-usage-data-research.md`](./claude-usage-data-research.md),
[`openai-usage-data-research.md`](./openai-usage-data-research.md).*

## Why this exists

Today's `daily_metrics.json` / `session_metrics.json` are **derived aggregates** —
per-day and per-session sums shaped for one report. They are lossy: once a turn
is folded into a day total, you can never ask a *new* question of it (latency of
`Edit` calls, retry-after-failure loops, error rate per git branch, prompt
length vs. cost). Transcripts also **rotate away**, so the raw grain is gone
unless we capture it.

This model is the **capture-now layer**: a normalized, provider-neutral,
turn-and-tool-grained store that is a *superset* of what the report consumes.
The report (and `daily_metrics.json`) become **projections** of it. We keep the
existing aggregate pipeline running unchanged and **dual-write** this store
alongside it (no risk to the working report), on the durable, only-grows
substrate the tool already relies on.

## Principles

1. **Faithful superset.** Capture every field either provider gives that has
   analytic value; never down-project at ingest below the finest grain we can
   preserve.
2. **Normalize the *semantics*, keep the *raw*.** Where the two providers mean
   different things by the same word (see §4), reconcile at ingest so the store
   is apples-to-apples — but always keep the raw model id, raw tool name, and raw
   error text alongside the normalized value.
3. **Cost is never stored.** It's a query-time projection of tokens × *current*
   `PRICING`, preserving the "recompute all history at today's rates" principle.
   Freezing a dollar figure would break trend comparability.
4. **One privacy switch.** `TOKENLENS_CAPTURE` governs whether verbatim text is
   stored. Structural mode still records that content existed, its length, and a
   hash — so counts/analysis work without holding sensitive text.
5. **Provider-agnostic core, provider-specific edges.** Common fields are
   first-class columns; provider-only fields live in typed extension columns; and
   an untyped raw-event escape hatch captures line types we don't model yet.

## 1. Field inventory — what each provider actually gives

### 1.1 Common to both (the shared core)

| Concept | Claude Code | Codex CLI |
|---|---|---|
| event timestamp (UTC) | `timestamp` (per line) | `timestamp` (per line) |
| session id | `sessionId` | `session_meta.payload.id` |
| working dir | `cwd` (per line) | `session_meta.cwd` / `turn_context.cwd` |
| exact model id | `message.model` | `turn_context.payload.model` |
| fresh input tokens | `usage.input_tokens` (already fresh) | `input_tokens − cached_input_tokens` (input is inclusive) |
| cache-read tokens | `usage.cache_read_input_tokens` | `cached_input_tokens` |
| output tokens | `usage.output_tokens` | `output_tokens` |
| tool call | `tool_use{id,name,input}` | `function_call{call_id,name,arguments}` |
| tool result | `tool_result{tool_use_id,content,is_error}` | `function_call_output{call_id,output}` |
| user prompt text | `message.role:"user"` content | `input_text` / `user_message` |
| assistant text | `text` content blocks | `output_text` content |
| CLI version | `version` | `session_meta.cli_version` |

### 1.2 Claude-only

- `gitBranch` — git branch in force (Codex has no equivalent).
- `uuid` / `parentUuid` — a real **message tree** (Codex is linear).
- `requestId` — API request id.
- `is_error` — **explicit boolean** on tool results (Codex has no flag).
- Cache-**write** tiers — `cache_creation.ephemeral_5m/1h_input_tokens` (Codex
  has no billed cache-write; caching is free to populate).
- `thinking` blocks — **full verbatim reasoning text** is persisted.

### 1.3 Codex-only

- `reasoning_output_tokens` — explicit reasoning-token **count** (subset of
  output). Claude bills thinking inside output but emits no separate count.
- `approval_policy` / `sandbox_policy` — per-turn safety posture.
- `model_provider` / `source` (`cli` vs `exec`).
- `total_token_usage` — **cumulative** counter (Claude reports per-message only).
- `compacted` items — history-compaction summaries (inflate raw totals).
- `world_state` / `inter_agent_communication` — multi-agent snapshots.

## 2. The abstract model — five entities

```
 machine 1───* session 1───* turn 1───* tool_call
                    │            │
                    └────────────┴───────* segment   (content: prompt/response/reasoning/tool io)
                                                      raw_event   (escape hatch, keyed to session)
```

The accounting atom is the **turn** (one assistant response with its usage). The
richest untapped signal is the **tool_call** (finer than a turn). Content
(prompts, responses, reasoning, tool i/o) is uniform **segments**. Anything we
don't model yet is preserved as a **raw_event**.

### 2.1 `session` — one conversation

| Field | Source (Claude / Codex) | Notes |
|---|---|---|
| `session_id` | `sessionId` / `session_meta.id` | natural key, scoped by provider |
| `provider` | derived (`claude`) / `session_meta.model_provider` | `claude` \| `codex` |
| `machine_id` | ours | already how artifacts are namespaced |
| `project` | encoded folder path / `cwd`-derived label | display label |
| `cwd` | `cwd` / `session_meta.cwd` | real path |
| `git_branch` | `gitBranch` / — | null for Codex |
| `cli_version` | `version` / `cli_version` | |
| `source` | — / `session_meta.source` | `cli` \| `exec` |
| `approval_policy` | — / `turn_context.approval_policy` | Codex safety posture |
| `sandbox_policy` | — / `turn_context.sandbox_policy` | Codex safety posture |
| `first_ts` / `last_ts` | min/max line timestamp | span |

### 2.2 `turn` — one assistant response (the accounting unit)

| Field | Source (Claude / Codex) | Notes |
|---|---|---|
| `turn_id` | `uuid` / synthesized `session_id:seq` | Codex token events aren't uuid'd |
| `session_id` | FK | |
| `parent_turn_id` | `parentUuid` / — | message tree (Claude only) |
| `ts` | `timestamp` | |
| `wall_ms` | derived: Δ from prior turn ts | throughput/latency analysis |
| `model` | `message.model` / `turn_context.model` | **raw id, never lost** |
| `tier` | normalized | `opus`/`sonnet`/`haiku`/`gpt-5*`… |
| `request_id` | `requestId` / — | |
| `stop_reason` | `stop_reason` if present / — | |
| `input_fresh` | `input_tokens` / `input−cached` | reconciled (see §4) |
| `cache_read` | `cache_read_input_tokens` / `cached_input_tokens` | |
| `cache_write_5m` | `ephemeral_5m` / `0` | Codex has no write tier |
| `cache_write_1h` | `ephemeral_1h` / `0` | |
| `output` | `output_tokens` / `output_tokens` | billed once |
| `reasoning_output` | null (count N/A) / `reasoning_output_tokens` | subset of output; **not** re-billed |
| `compacted` | `false` / from `compacted` items | Codex context compaction flag |
| `n_tool_calls` | count in turn | 0/1/many → drives pseudo-row attribution |

Note: **cost is not a column** — it's `tokens × current PRICING` at query time.

### 2.3 `tool_call` — one tool invocation

| Field | Source (Claude / Codex) | Notes |
|---|---|---|
| `call_id` | `tool_use.id` / `function_call.call_id` | natural key |
| `turn_id` | FK (via id→turn join) | |
| `session_id` | FK | |
| `ts` | line timestamp | |
| `name` | `tool_use.name` / `function_call.name` | **raw** (`Edit`, `shell`, `apply_patch`) |
| `kind` | normalized | `file_edit`/`shell`/`file_read`/`search`/`mcp`/… (§4.2) |
| `ok` | `!is_error` / heuristic | Codex: `success:false`, non-zero exit, stderr shape |
| `error_class` | `classify()` / `classify_codex()` | unified taxonomy (§4.3) |
| `exit_code` | — / shell exit | Codex shell only |
| `output_bytes` | len(content) / len(output) | |
| `latency_ms` | derived: result ts − call ts | |
| `args` (→ segment) | `tool_use.input` / `function_call.arguments` | see §3 |
| `output` (→ segment) | `tool_result.content` / `function_call_output.output` | see §3 |

### 2.4 `segment` — content (prompts, responses, reasoning, tool i/o)

One uniform table for **every piece of text**, so the verbatim switch governs one
place. `kind ∈ {prompt, response, reasoning, tool_args, tool_output}`.

| Field | Notes |
|---|---|
| `segment_id` | surrogate |
| `turn_id` / `tool_call_id` | whichever it belongs to |
| `kind` | prompt \| response \| reasoning \| tool_args \| tool_output |
| `role` | user \| assistant \| tool |
| `length` | char/byte length — **always** stored |
| `sha256` | content hash — **always** stored (dedupe, "same command again?") |
| `text` | **verbatim only when `TOKENLENS_CAPTURE=verbatim`**, else NULL |
| `text_available` | provider actually persisted the text (see reasoning caveat below) |

**Reasoning caveat — measured, not assumed.** Reasoning *text* is largely
**not recoverable** from either provider's local logs, so `reasoning` segments are
usually count/presence-only:

- **Claude Code redacts thinking.** In the transcripts on disk, `thinking` blocks
  carry `"thinking":""` plus an **encrypted `signature`** — the words are gone
  (verified: 7,494 thinking blocks totalled ~0 MB of text). A Claude `reasoning`
  segment therefore stores `length=0, text_available=false`, keeps the signature
  hash as provenance, and — because Claude's `usage` has no separate reasoning
  count — even `reasoning_output` on the turn is `NULL` (thinking is billed inside
  `output`).
- **Codex gives a count, sometimes a summary.** `reasoning_output_tokens` is
  always present on the turn; the full chain-of-thought usually isn't, but some
  versions persist a short **reasoning summary**. **Decision: treat that summary
  as the reasoning segment's text** (`text_available=true`) when no full trace
  exists — a partial trace beats none. When only the count is present, the segment
  is `length=0, text_available=false` while the turn still shows nonzero
  `reasoning_output`.

So the model must represent three states distinctly: *no reasoning*, *reasoning
happened but words unavailable* (Claude signature / Codex count-only), and
*reasoning text present* (Codex summary or, rarely, full trace).

### 2.5 `raw_event` — the escape hatch

For line types we don't model yet (Codex `world_state`,
`inter_agent_communication`, Claude `system`/`summary` lines, anything new a CLI
release introduces): store `{session_id, ts, provider, type, sha256, raw?}`.
`raw` (the full JSON) only when verbatim. This is the literal "capture as much as
we can now, structure it later" guarantee — a new field in a future CLI version
is retained even before we write a parser for it.

## 3. Capture modes (`TOKENLENS_CAPTURE`)

| Mode | segments.text / raw_event.raw | Everything else |
|---|---|---|
| **structural** (default) | NULL — only `length` + `sha256` | fully captured |
| **verbatim** | full text stored | fully captured |

Structural mode is shareable-in-principle and matches the project's demonstrated
privacy posture (history was purged of personal data once). Verbatim unlocks
semantic search, prompt-quality studies, and full replay — at the cost of holding
sensitive text locally. The switch is one env var read at ingest; the schema is
identical either way (only `text`/`raw` columns go null/populated).

## 4. Normalization rules (reconcile at ingest)

### 4.1 Tokens — one 6-line vector, semantics fixed

The dangerous mismatch: **Claude `input_tokens` excludes cache; Codex
`input_tokens` includes it.** Normalize both to `input_fresh` = tokens the model
read fresh this turn:

- Claude: `input_fresh = input_tokens` (already fresh); writes from
  `cache_creation`; when only the aggregate `cache_creation_input_tokens` exists,
  attribute to 5m.
- Codex: `input_fresh = input_tokens − cached_input_tokens`; `cache_read =
  cached_input_tokens`; write tiers = 0.
- `reasoning_output` is stored separately but is a **subset of `output`** —
  billed once, never added on top.

The 6-line vector (`input_fresh, cache_read, cache_write_5m, cache_write_1h,
output, reasoning_output`) is a superset of both providers, so one schema serves
both and cross-provider sums are already apples-to-apples.

### 4.2 Tools — raw name + normalized `kind`

Keep the raw name; add a normalized kind so cross-provider tool analysis is
possible at all:

| `kind` | Claude names | Codex names |
|---|---|---|
| `file_edit` | `Edit`, `Write`, `MultiEdit` | `apply_patch` |
| `file_read` | `Read` | `read_file` |
| `shell` | `Bash` | `shell`, `exec_command`, `local_shell_call` |
| `search` | `Grep`, `Glob` | (grep via shell) |
| `plan` | `TodoWrite` | `update_plan` |
| `mcp` | `mcp__*` | MCP tools |
| `other` | everything else | everything else |

### 4.3 Errors — one taxonomy over two harnesses

Claude gives an explicit `is_error` boolean; Codex needs a heuristic
(`success:false`, non-zero exit code, stderr-looking output). Both then map into
**one** canonical `error_class` vocabulary (unify the existing `ERROR_RULES` and
the planned `ERROR_RULES_CODEX` into shared classes: `file-not-found`,
`edit-no-match`, `command-not-found`, `permission-denied`, `timeout`, `other`).
Raw error text is kept as a `tool_output` segment (hash always, verbatim opt-in).

### 4.4 Identity & idempotency (why SQLite)

Natural keys make ingest **idempotent** — re-running never double-counts, and the
store only grows:

- `turn_id`: Claude `uuid`; Codex synthesized `session_id:seq` (token events lack
  uuids). `INSERT OR IGNORE`.
- `tool_call.call_id`: `tool_use.id` / `function_call.call_id`. `INSERT OR IGNORE`.
- `segment.sha256`: dedupe identical content.

This replaces the current `dict.update` merge with SQL upsert and gives the
only-grows / survives-rotation property for free.

## 5. Concrete SQLite schema (stdlib `sqlite3`)

```sql
CREATE TABLE session (
  session_id      TEXT NOT NULL,
  provider        TEXT NOT NULL,          -- claude | codex
  machine_id      TEXT NOT NULL,
  project         TEXT, cwd TEXT, git_branch TEXT,
  cli_version     TEXT, source TEXT,
  approval_policy TEXT, sandbox_policy TEXT,
  first_ts        TEXT, last_ts TEXT,
  PRIMARY KEY (provider, session_id)
);
CREATE TABLE turn (
  turn_id       TEXT PRIMARY KEY,         -- uuid | session:seq
  provider      TEXT NOT NULL,
  session_id    TEXT NOT NULL,
  parent_turn_id TEXT, ts TEXT NOT NULL, wall_ms INTEGER,
  model TEXT NOT NULL, tier TEXT,
  request_id TEXT, stop_reason TEXT,
  input_fresh INTEGER DEFAULT 0, cache_read INTEGER DEFAULT 0,
  cache_write_5m INTEGER DEFAULT 0, cache_write_1h INTEGER DEFAULT 0,
  output INTEGER DEFAULT 0, reasoning_output INTEGER,
  compacted INTEGER DEFAULT 0, n_tool_calls INTEGER DEFAULT 0
);
CREATE TABLE tool_call (
  call_id TEXT PRIMARY KEY,
  provider TEXT NOT NULL, session_id TEXT NOT NULL, turn_id TEXT,
  ts TEXT, name TEXT NOT NULL, kind TEXT,
  ok INTEGER, error_class TEXT, exit_code INTEGER,
  output_bytes INTEGER, latency_ms INTEGER
);
CREATE TABLE segment (
  segment_id INTEGER PRIMARY KEY,
  turn_id TEXT, tool_call_id TEXT,
  kind TEXT NOT NULL,                     -- prompt|response|reasoning|tool_args|tool_output
  role TEXT, length INTEGER, sha256 TEXT,
  text TEXT,                              -- verbatim only when TOKENLENS_CAPTURE=verbatim
  text_available INTEGER DEFAULT 1
);
CREATE TABLE raw_event (
  session_id TEXT, provider TEXT, ts TEXT, type TEXT,
  sha256 TEXT, raw TEXT                   -- raw only when verbatim
);
CREATE INDEX turn_day  ON turn(substr(ts,1,10));
CREATE INDEX turn_sess ON turn(provider, session_id);
CREATE INDEX tool_turn ON tool_call(turn_id);
```

`daily_metrics.json` / `session_metrics.json` are then a `GROUP BY substr(ts,1,10)`
and a `GROUP BY session_id` away — the report keeps consuming them unchanged.

## 6. Ingest — an abstract, provider-pluggable pass

The whole point of the abstraction: **a new provider is a new class, nothing
else changes.** Ingest is a producer/consumer split. Each provider *produces* a
stream of provider-neutral `Rec`s from its own log format; the core *consumes*
that stream and upserts it into the schema. The core never learns a provider's
field names, and a provider never touches SQL.

### 6.1 The neutral record types (the contract)

Every provider parser yields only these — the union of §2's entities:

```python
from dataclasses import dataclass, field

@dataclass
class Session:  # one per conversation
    provider:str; session_id:str; project:str=None; cwd:str=None
    git_branch:str=None; cli_version:str=None; source:str=None
    approval_policy:str=None; sandbox_policy:str=None; ts:str=None

@dataclass
class Turn:      # one assistant response (the accounting unit)
    provider:str; session_id:str; turn_id:str; ts:str; model:str
    parent_turn_id:str=None; request_id:str=None; stop_reason:str=None
    input_fresh:int=0; cache_read:int=0; cache_write_5m:int=0
    cache_write_1h:int=0; output:int=0; reasoning_output:int=None
    compacted:bool=False; n_tool_calls:int=0

@dataclass
class ToolCall:  # one tool invocation
    provider:str; session_id:str; call_id:str; turn_id:str; ts:str
    name:str; ok:bool=None; error_class:str=None; exit_code:int=None
    output_bytes:int=0; latency_ms:int=None

@dataclass
class Segment:   # one piece of content (prompt/response/reasoning/tool io)
    kind:str; role:str=None; turn_id:str=None; tool_call_id:str=None
    text:str=None; text_available:bool=True

@dataclass
class RawEvent:  # anything unmodeled — the escape hatch
    provider:str; session_id:str; ts:str; type:str; raw:str=None
```

### 6.2 The `Provider` interface

```python
class Provider:
    name: str
    def roots(self) -> list[str]: ...          # glob roots to scan
    def parse_file(self, path) -> Iterator[Rec]: ...   # yield Session/Turn/ToolCall/Segment/RawEvent

PROVIDERS = [ClaudeProvider(), CodexProvider()]    # add a class → new provider supported
```

A provider owns exactly its own quirks: token-semantics reconciliation (§4.1),
the id→turn join, its error heuristic, and mapping raw tool names to `kind`
(§4.2) — via the **shared** helpers `tool_kind()`, `error_class()`, `sha()` so the
taxonomy stays unified across providers.

### 6.3 The core is provider-blind

```python
def ingest(db, providers=PROVIDERS, capture=os.environ.get("TOKENLENS_CAPTURE","structural")):
    verbatim = capture == "verbatim"
    for prov in providers:
        for root in prov.roots():
            for f in glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True):
                try:
                    with db:                       # one tx per file; rollback on error
                        for rec in prov.parse_file(f):
                            write(db, rec, verbatim)   # dispatch on type(rec)
                except Exception:
                    continue                        # resilient-by-design, like analyze()

def write(db, rec, verbatim):
    if isinstance(rec, Turn):
        db.execute("INSERT OR IGNORE INTO turn(...) VALUES(...)", astuple(rec))
    elif isinstance(rec, ToolCall):
        db.execute("INSERT INTO tool_call(...) VALUES(...) "
                   "ON CONFLICT(call_id) DO UPDATE SET ok=?, error_class=?, ...", ...)
    elif isinstance(rec, Segment):
        text = rec.text if verbatim else None
        db.execute("INSERT INTO segment(kind,role,turn_id,tool_call_id,length,sha256,text,text_available)"
                   " VALUES(?,?,?,?,?,?,?,?)",
                   (rec.kind, rec.role, rec.turn_id, rec.tool_call_id,
                    len(rec.text or ""), sha(rec.text), text, rec.text_available))
    elif isinstance(rec, RawEvent):
        raw = clamp(rec.raw, RAW_CAP) if verbatim else None   # §7 growth cap
        db.execute("INSERT INTO raw_event(provider,session_id,ts,type,sha256,raw) VALUES(?,?,?,?,?,?)",
                   (rec.provider, rec.session_id, rec.ts, rec.type, sha(rec.raw), raw))
    elif isinstance(rec, Session):
        db.execute("INSERT INTO session(...) VALUES(...) ON CONFLICT(provider,session_id) "
                   "DO UPDATE SET last_ts=max(last_ts,excluded.last_ts), ...", ...)
```

`INSERT OR IGNORE` on `turn_id` / `call_id` makes re-ingest **idempotent** — the
store survives transcript rotation and only ever grows, exactly like the current
`merge_daily()` semantics but at row grain and without a rewrite.

### 6.4 A provider parser, sketched (`ClaudeProvider.parse_file`)

```python
def parse_file(self, path):
    sid = self._sid(path); idturn = {}; prev_ts = {}
    for o in self._lines(path):                     # json per line, skip junk
        ts = o.get("timestamp"); m = o.get("message")
        if not ts or not isinstance(m, dict): 
            yield RawEvent("claude", sid, ts, o.get("type","?"), json.dumps(o)); continue
        if is_first_line: yield Session("claude", sid, cwd=o.get("cwd"),
                                        git_branch=o.get("gitBranch"), cli_version=o.get("version"), ts=ts)
        u = m.get("usage")
        if isinstance(u, dict):                     # an assistant turn
            tid = o.get("uuid")
            yield Turn("claude", sid, tid, ts, m.get("model"),
                       parent_turn_id=o.get("parentUuid"), request_id=o.get("requestId"),
                       input_fresh=u.get("input_tokens",0), cache_read=u.get("cache_read_input_tokens",0),
                       cache_write_5m=_w5m(u), cache_write_1h=_w1h(u), output=u.get("output_tokens",0),
                       wall_ms=_delta(prev_ts, sid, ts))
            for b in m.get("content") or []:
                if b.get("type")=="text":     yield Segment("response","assistant",turn_id=tid,text=b.get("text"))
                elif b.get("type")=="thinking":                       # redacted → count-only (§2.4)
                    yield Segment("reasoning","assistant",turn_id=tid,text=None,text_available=bool(b.get("thinking")))
                elif b.get("type")=="tool_use":
                    idturn[b["id"]] = tid
                    yield ToolCall("claude", sid, b["id"], tid, ts, b["name"], name_kind=tool_kind(b["name"]))
                    yield Segment("tool_args","tool",tool_call_id=b["id"],text=json.dumps(b.get("input")))
        else:                                        # user line: tool_results
            for b in m.get("content") or []:
                if isinstance(b,dict) and b.get("type")=="tool_result":
                    yield ToolCall("claude", sid, b["tool_use_id"], idturn.get(b["tool_use_id"]), ts, name=None,
                                   ok=not b.get("is_error"), error_class=error_class(result_text(b)) if b.get("is_error") else None)
                    yield Segment("tool_output","tool",tool_call_id=b["tool_use_id"],text=result_text(b))
```

`CodexProvider.parse_file` is the twin: `session_meta`→`Session`,
`turn_context`→remember model, `event_msg/token_count`→`Turn` (with
`input_fresh = input−cached`, `reasoning_output` set, writes 0),
`function_call(_output)`→`ToolCall`+`Segment`, reasoning summary→`Segment("reasoning", text=summary)`
per §2.4. Same `Rec` stream out; the core doesn't know which ran.

## 7. Size growth (measured on a real corpus)

Profiled against this machine's transcripts — **30 active days**
(2026-05-28 → 2026-07-10), **192 MB / 68.3 k lines**, **32,943 turns**,
**16,552 tool_calls**, **~49,750 segments**, throughput **~750 turns/day**. Of the
raw bytes only **27% (51 MB) is text**: tool output 30 MB, tool args 13 MB,
responses 7 MB, prompts 1 MB, **reasoning ≈ 0** (redacted).

Estimated store size (SQLite, ~0.8 KB/turn structural incl. indexes; verbatim
adds deduped text):

| Mode | Per corpus (30 active days) | % of raw | Per active-day | **Annualized (~250 active days)** |
|---|---|---|---|---|
| **structural** (default) | ~26 MB | ~14% | ~0.85 MB | **~0.2 GB/yr** |
| **verbatim** (full text) | ~77 MB | ~40% | ~2.5 MB | **~0.6 GB/yr** |

**Verdict: let modeled data run — no cap.** Even verbatim at ~0.6 GB/yr is fine
for a local, per-machine file, and it's *smaller than the raw transcripts it
replaces* (which rotate away anyway) because we store text once, not re-wrapped in
a cache envelope every turn. `sha256` dedupe on repeated tool outputs / file reads
shrinks it further.

**The one cap goes on `raw_event.raw` only** — the sole unbounded, unpredictable
surface (unmodeled line types, especially Codex `world_state` /
`inter_agent_communication` snapshots, which can be large). Bound it two cheap
ways, both leaving the modeled tables untouched:

- **Per-event clamp** — store at most `RAW_CAP` (e.g. 32 KB) of `raw`; the
  `sha256` is always kept, so nothing is *undetectable*, just not fully retained.
- **Age-out** — a periodic `UPDATE raw_event SET raw=NULL WHERE ts < now-90d`
  (structural columns and hashes survive; only the bulky verbatim JSON drops).

That keeps the growth of the modeled store fully predictable (linear in turns)
and quarantines the only variable-size risk.

## 8. Deferred / open

- **Backfill vs. forward-only.** First ingest reconstructs everything still on
  disk (same property as today); rotated-away history can't be recovered.
- **Cross-provider session identity** — a user "conversation" never spans
  providers, so no merge needed; `(provider, session_id)` is globally unique.
- **DB migrations** — a `schema_version` pragma + additive-only column policy
  keeps old dbs readable as the model grows.
- **Aggregate flip.** Once the db is trusted, regenerate `daily_metrics.json` /
  `session_metrics.json` *from* it (a `GROUP BY`) instead of dual-writing — the
  report keeps consuming the same JSON, unaware the source changed.
```

