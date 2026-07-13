#!/usr/bin/env python3
"""Neutral, provider-agnostic record shapes for the canonical usage store
(E3-F2-S3).

Before this module existed, `Session`/`Turn`/`ToolCall`/`Segment`/`RawEvent`
were defined inside canonical.py (ingestion), which was otherwise fine --
canonical.py is their only *producer/consumer* today -- except that these
five dataclasses are exactly the kind of neutral, dependency-free shape that
belongs BELOW both report.py (reporting) and canonical.py (ingestion), same
rationale as pricing.py/classification.py (E3-F2-S2) and paths.py (E3-F2-S1):
a future reporting-side consumer of these shapes (or a second ingestion
module) should be able to import them without reaching into canonical.py's
internals, and this module makes that possible without anyone needing to.

Pure by design: five stdlib `@dataclass` definitions, nothing else. No file
I/O, no SQL, no knowledge of transcripts, report.py, or canonical.py. This is
a pure extraction -- every field, default, and comment is unchanged from
canonical.py; canonical.py now imports these five names directly (`from
traceyield.models import Session, Turn, ToolCall, Segment, RawEvent`) instead
of defining them itself, so `canonical.Turn is models.Turn` (same class
object, not a copy) for all five.
"""
from dataclasses import dataclass

# ---------------------------------------------------------------- neutral records
@dataclass
class Session:
    provider: str; session_id: str
    project: str = None; cwd: str = None; git_branch: str = None
    cli_version: str = None; source: str = None
    approval_policy: str = None; sandbox_policy: str = None
    first_ts: str = None; last_ts: str = None

@dataclass
class Turn:
    provider: str; session_id: str; turn_id: str; ts: str; model: str
    parent_turn_id: str = None; request_id: str = None; stop_reason: str = None
    input_fresh: int = 0; cache_read: int = 0; cache_write_5m: int = 0
    cache_write_1h: int = 0; output: int = 0; reasoning_output: int = None
    compacted: bool = False; n_tool_calls: int = 0; wall_ms: int = None
    tier: str = None
    project: str = None   # the project the SOURCE FILE lives in (per-turn, not
                           # per-session -- a session can span project dirs)

@dataclass
class ToolCall:
    provider: str; session_id: str; call_id: str; turn_id: str; ts: str
    name: str = None; kind: str = None; ok: bool = None; error_class: str = None
    exit_code: int = None; output_bytes: int = 0; latency_ms: int = None

@dataclass
class Segment:
    kind: str; role: str = None; turn_id: str = None; tool_call_id: str = None
    seq: int = 0; text: str = None; text_available: bool = True
    hash_src: str = None    # hashed for provenance when text is absent (e.g. redacted reasoning signature)

@dataclass
class RawEvent:
    provider: str; session_id: str; ts: str; type: str; raw: str = None
