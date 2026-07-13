#!/usr/bin/env python3
"""Tests for E3-F2-S3: neutral models module + severing canonical -> report.

Covers both acceptance-criteria scenarios of the story:

  AC1 "Reverse dependency removed" -- canonical.py (ingestion) no longer
      imports report.py (reporting) at all: no `import report`, no `from
      traceyield import report`, no `report.` attribute access anywhere in
      canonical.py. Static AST guards, mirroring tests/test_layout.py's and
      tests/test_pricing.py's style, prove this, and go further to assert
      the dependency direction for the WHOLE package: the neutral modules
      (models, paths, pricing, classification, transcripts) import neither
      report nor canonical, and canonical imports only neutral traceyield
      submodules -- plus, as of E3-F2-S4, the `providers` package (see the
      NOTE on TestPackageDependencyDirection below).

  AC2 "Both layers consume neutral models" -- canonical.Session/.Turn/
      .ToolCall/.Segment/.RawEvent are the SAME class objects as
      models.Session/.Turn/.ToolCall/.Segment/.RawEvent (not copies), and
      report.project_of/.result_text are the SAME function objects as
      transcripts.project_of/.result_text -- both layers share one neutral
      definition, not independent copies that happen to agree today.

  Round-trip safety (constraint 5) -- one instance of each of the five
  dataclasses is written through canonical.write() into a temp usage.db and
  read back column-for-column via raw SQL, proving the moved dataclasses'
  field order still lines up with SCHEMA/the INSERT tuples exactly (the
  highest-risk part of this refactor per the story's hard constraints).

Stdlib-only (unittest).
"""
import ast
import os
import unittest

from traceyield import canonical, models, paths, report, transcripts

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "traceyield")

# The neutral modules: must import neither report nor canonical (they sit
# BELOW both).
NEUTRAL_MODULES = {"models.py", "paths.py", "pricing.py", "classification.py", "transcripts.py"}


# --------------------------------------------------------------- AST scan helpers
def _module_imports(file_path):
    """Return the set of top-level module names this file imports, found via
    `import X` / `from X import ...` anywhere in the AST. Mirrors
    test_pricing.py's helper of the same name/shape."""
    with open(file_path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=file_path)
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                found.add(node.module.split(".")[0])
    return found


def _traceyield_submodule_names(file_path):
    """Return the set of traceyield SUBMODULE names this file imports, e.g.
    `from traceyield import paths, pricing` -> {"paths", "pricing"}, and
    `from traceyield.models import Turn` -> {"models"}. Used to prove
    canonical.py only ever reaches into neutral submodules."""
    with open(file_path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=file_path)
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or not node.module:
            continue
        parts = node.module.split(".")
        if parts[0] != "traceyield":
            continue
        if len(parts) > 1:
            found.add(parts[1])                      # from traceyield.models import X
        else:
            found.update(alias.name for alias in node.names)   # from traceyield import models, paths
    return found


def _attribute_accesses_on_name(file_path, name):
    """Return human-readable hits for every `<name>.<attr>` attribute access
    found anywhere in the AST (assignment target, call, bare expression --
    any Attribute node whose base is the bare Name `name`)."""
    with open(file_path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=file_path)
    hits = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == name):
            hits.append(f"line {node.lineno}: {name}.{node.attr}")
    return hits


# --------------------------------------------------------------- AC1: reverse dependency removed
class TestCanonicalNoLongerImportsReport(unittest.TestCase):
    """AC1: canonical.py (ingestion) must not import report.py (reporting)
    in any form -- not `import report`, not `from traceyield import
    report`, and no `report.` attribute access left over from a partial
    removal."""

    def setUp(self):
        self.canonical_path = os.path.join(SRC_DIR, "canonical.py")

    def test_canonical_does_not_import_report(self):
        # Catches both `import report` (bare top-level import) and `from
        # traceyield import report` / `from traceyield.report import ...`
        # (the actual pre-refactor form) -- _module_imports() alone only
        # sees the "traceyield" part of the latter, so it's not enough on
        # its own; _traceyield_submodule_names() looks at the imported
        # NAMES too.
        imports = _module_imports(self.canonical_path)
        submodules = _traceyield_submodule_names(self.canonical_path)
        self.assertNotIn("report", imports,
                          "canonical.py still imports the report module")
        self.assertNotIn("report", submodules,
                          "canonical.py still imports traceyield.report")

    def test_canonical_has_no_report_attribute_access(self):
        hits = _attribute_accesses_on_name(self.canonical_path, "report")
        self.assertEqual(
            hits, [],
            f"canonical.py still accesses report.* attributes: {hits}",
        )

    def test_scanner_self_test_catches_a_known_violation(self):
        # Proves the scanner is real: fed a snippet shaped exactly like
        # canonical.py's PRE-refactor code (`from traceyield import report`
        # + a `report.machine_id()` call site), it must catch both the
        # import and the attribute access -- i.e. this guard WOULD have
        # failed against canonical.py before this story's changes.
        import tempfile
        snippet = (
            "from traceyield import report\n"
            "def f():\n"
            "    return report.machine_id()\n"
        )
        with tempfile.NamedTemporaryFile(
            "w", suffix=".py", delete=False, dir=SRC_DIR,
        ) as f:
            f.write(snippet)
            tmp_path = f.name
        try:
            self.assertIn("report", _traceyield_submodule_names(tmp_path))
            hits = _attribute_accesses_on_name(tmp_path, "report")
            self.assertEqual(len(hits), 1)
            self.assertIn("report.machine_id", hits[0])
        finally:
            os.remove(tmp_path)


class TestPackageDependencyDirection(unittest.TestCase):
    """AC1, taken further: prove the dependency direction for the whole
    package, not just canonical.py's report import. Neutral modules sit
    below both report.py and canonical.py; canonical.py may only reach into
    neutral traceyield submodules."""

    def test_neutral_modules_import_neither_report_nor_canonical(self):
        violations = {}
        for name in sorted(NEUTRAL_MODULES):
            file_path = os.path.join(SRC_DIR, name)
            bad = (_module_imports(file_path) | _traceyield_submodule_names(file_path)) & {"report", "canonical"}
            if bad:
                violations[name] = sorted(bad)
        self.assertEqual(
            violations, {},
            f"neutral module(s) import report/canonical (should sit below "
            f"both): {violations}",
        )

    def test_canonical_imports_only_neutral_traceyield_submodules(self):
        # NOTE (E3-F2-S4, deliberate change to a pre-existing assertion): this
        # test originally asserted canonical.py's traceyield submodule
        # imports were a subset of NEUTRAL_MODULES alone. E3-F2-S4 moved
        # ClaudeProvider/CodexProvider (the producers) out of canonical.py
        # into their own traceyield.providers package (see
        # traceyield.providers.base.Provider + tests/test_providers.py), and
        # canonical.py now imports that package to re-export
        # ClaudeProvider/CodexProvider/codex_tier/CODEX_TIER for backward
        # compatibility (canonical.ClaudeProvider is providers.ClaudeProvider,
        # not a copy). That is a NEW, intentional dependency edge --
        # canonical (the store/consumer + registry) depending on providers
        # (the producer layer) -- NOT a regression of the thing this suite
        # guards against: `providers` is not itself one of the neutral,
        # dependency-free bottom-layer modules (it depends ON them), so it
        # doesn't belong in NEUTRAL_MODULES, but canonical importing it does
        # not reintroduce a dependency on report.py, which remains the one
        # forbidden edge (asserted explicitly below, unchanged from before).
        file_path = os.path.join(SRC_DIR, "canonical.py")
        submodules = _traceyield_submodule_names(file_path)
        neutral_names = {name[:-3] for name in NEUTRAL_MODULES}   # strip ".py"
        allowed = neutral_names | {"providers"}
        self.assertTrue(
            submodules <= allowed,
            f"canonical.py imports traceyield submodule(s) outside the "
            f"allowed set (neutral modules + providers): "
            f"{submodules - allowed}",
        )
        self.assertNotIn("report", submodules)


# --------------------------------------------------------------- AC2: both layers share neutral models
class TestSharedDataclassIdentity(unittest.TestCase):
    """canonical.Session/.Turn/.ToolCall/.Segment/.RawEvent must be the SAME
    class objects as models.Session/.Turn/.ToolCall/.Segment/.RawEvent (not
    independent copies that happen to look the same today)."""

    def test_all_five_dataclasses_are_the_shared_models_classes(self):
        self.assertIs(canonical.Session, models.Session)
        self.assertIs(canonical.Turn, models.Turn)
        self.assertIs(canonical.ToolCall, models.ToolCall)
        self.assertIs(canonical.Segment, models.Segment)
        self.assertIs(canonical.RawEvent, models.RawEvent)


class TestSharedHelperIdentity(unittest.TestCase):
    """report.project_of/.result_text must be the SAME function objects as
    transcripts.project_of/.result_text -- both layers consume the one
    neutral definition."""

    def test_report_reexports_are_the_shared_transcripts_functions(self):
        self.assertIs(report.project_of, transcripts.project_of)
        self.assertIs(report.result_text, transcripts.result_text)

    def test_report_machine_id_is_still_the_shared_paths_function(self):
        # Unchanged by this story (already true since E3-F2-S1), reasserted
        # here because it's the third name canonical.py used to reach into
        # report.py for.
        self.assertIs(report.machine_id, paths.machine_id)


class TestProjectOfBehaviorUnchanged(unittest.TestCase):
    """project_of()'s DEFAULT root now resolves via paths.claude_projects()
    at call time instead of an import-time CLAUDE_PROJECTS snapshot, but
    what it resolves TO is unchanged, and explicit-root callers (report.py's
    analyze(), canonical.py's ClaudeProvider) are unaffected either way."""

    def test_explicit_root_behaves_identically(self):
        path = os.path.join("some", "root", "myproject", "conv.jsonl")
        root = os.path.join("some", "root")
        self.assertEqual(transcripts.project_of(path, root), "myproject")
        self.assertEqual(report.project_of(path, root), "myproject")

    def test_default_root_resolves_via_paths_claude_projects(self):
        root = paths.claude_projects()
        path = os.path.join(root, "myproject", "conv.jsonl")
        self.assertEqual(transcripts.project_of(path), "myproject")


class TestResultTextUnchanged(unittest.TestCase):
    def test_string_content_returned_as_is(self):
        self.assertEqual(transcripts.result_text({"content": "hello"}), "hello")

    def test_list_content_joins_text_blocks(self):
        b = {"content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}
        self.assertEqual(transcripts.result_text(b), "a b")

    def test_missing_content_returns_empty_string(self):
        self.assertEqual(transcripts.result_text({}), "")

    def test_report_result_text_agrees(self):
        b = {"content": [{"type": "text", "text": "x"}]}
        self.assertEqual(report.result_text(b), transcripts.result_text(b))


# --------------------------------------------------------------- round-trip safety
class TestRoundTripInsertTuplesLineUp(unittest.TestCase):
    """Constraint 5: write one instance of every dataclass through
    canonical.write() into a temp usage.db, read every column back via raw
    SQL, and assert field-for-field equality -- proves the moved dataclasses'
    field order still lines up with SCHEMA / the INSERT tuples exactly."""

    def test_all_five_record_types_round_trip_field_for_field(self):
        conn = canonical.open_db(":memory:")

        turn = models.Turn(
            provider="claude", session_id="s1", turn_id="t1",
            ts="2026-01-01T00:00:00Z", model="claude-opus-4-8",
            parent_turn_id="t0", request_id="req1", stop_reason="end_turn",
            input_fresh=100, cache_read=50, cache_write_5m=10,
            cache_write_1h=5, output=20, reasoning_output=None,
            compacted=True, n_tool_calls=1, wall_ms=1234,
            tier="opus", project="projX",
        )
        # ToolCall is written TWICE in production (the call itself, then its
        # result), upserted onto one row by call_id -- mirror that here so
        # every ToolCall column gets exercised, same as canonical.write()'s
        # real two-phase call/result split (see write()'s ToolCall branch).
        call = models.ToolCall(
            provider="claude", session_id="s1", call_id="c1", turn_id="t1",
            ts="2026-01-01T00:00:00Z", name="Read", kind="file_read",
        )
        result = models.ToolCall(
            provider="claude", session_id="s1", call_id="c1", turn_id="t1",
            ts="2026-01-01T00:00:01Z", name=None,
            ok=True, error_class=None, exit_code=0, output_bytes=42,
            latency_ms=100,
        )
        seg = models.Segment(
            kind="response", role="assistant", turn_id="t1", tool_call_id=None,
            seq=0, text="hello", text_available=True, hash_src=None,
        )
        raw = models.RawEvent(
            provider="claude", session_id="s1", ts="2026-01-01T00:00:00Z",
            type="system", raw="{}",
        )
        sess = models.Session(
            provider="claude", session_id="s1", project="projX",
            cwd="/home/u/proj", git_branch="main", cli_version="1.2.3",
            source="cli", approval_policy="on-request",
            sandbox_policy="read-only", first_ts="2026-01-01T00:00:00Z",
            last_ts="2026-01-01T01:00:00Z",
        )

        for rec in (turn, call, result, seg, raw, sess):
            canonical.write(conn, rec, verbatim=True)
        conn.commit()

        r = conn.execute(
            "SELECT turn_id,provider,session_id,parent_turn_id,ts,wall_ms,model,tier,"
            "request_id,stop_reason,input_fresh,cache_read,cache_write_5m,cache_write_1h,"
            "output,reasoning_output,compacted,n_tool_calls,project "
            "FROM turn WHERE turn_id='t1'"
        ).fetchone()
        self.assertEqual(r, (
            "t1", "claude", "s1", "t0", "2026-01-01T00:00:00Z", 1234,
            "claude-opus-4-8", "opus", "req1", "end_turn", 100, 50, 10, 5,
            20, None, 1, 1, "projX",
        ))

        r = conn.execute(
            "SELECT call_id,provider,session_id,turn_id,ts,name,kind,ok,error_class,"
            "exit_code,output_bytes,latency_ms FROM tool_call WHERE call_id='c1'"
        ).fetchone()
        self.assertEqual(r, (
            "c1", "claude", "s1", "t1", "2026-01-01T00:00:01Z", "Read",
            "file_read", 1, None, 0, 42, 100,
        ))

        r = conn.execute(
            "SELECT turn_id,tool_call_id,kind,seq,role,length,sha256,text,text_available "
            "FROM segment WHERE turn_id='t1'"
        ).fetchone()
        self.assertEqual(r, (
            "t1", "", "response", 0, "assistant", len("hello"),
            canonical.sha("hello"), "hello", 1,
        ))

        r = conn.execute(
            "SELECT provider,session_id,ts,type,sha256,raw FROM raw_event"
        ).fetchone()
        self.assertEqual(r, (
            "claude", "s1", "2026-01-01T00:00:00Z", "system", canonical.sha("{}"), "{}",
        ))

        r = conn.execute(
            "SELECT provider,session_id,machine_id,project,cwd,git_branch,cli_version,"
            "source,approval_policy,sandbox_policy,first_ts,last_ts "
            "FROM session WHERE session_id='s1'"
        ).fetchone()
        self.assertEqual(r, (
            "claude", "s1", paths.machine_id(), "projX", "/home/u/proj", "main",
            "1.2.3", "cli", "on-request", "read-only",
            "2026-01-01T00:00:00Z", "2026-01-01T01:00:00Z",
        ))


if __name__ == "__main__":
    unittest.main()
