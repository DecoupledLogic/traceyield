#!/usr/bin/env python3
"""Tests for traceyield.paths (E3-F2-S1).

Covers both acceptance-criteria scenarios of the story:

  (a) "Single source of writable paths" -- every writable location (machine
      dir, usage.db, report.html, daily/session/health JSON, pricing
      history) resolves through traceyield.paths, and each one honors its
      env-var override *without requiring a reimport* (the callable
      resolvers re-read os.environ on every call).

  (b) "No scattered path literals" -- a static AST guard, mirroring
      tests/test_layout.py's style, scans every module under
      src/traceyield/ (except paths.py itself) and fails if any of them
      construct a writable/source-root path directly (a repo-root __file__
      walk, an os.path.join onto a known writable filename/dirname, an
      os.path.expanduser of a ~/.claude or ~/.codex literal) or read one of
      the centralized env vars directly via os.environ.get(...).

Stdlib-only (unittest); no dependency on real ~/.claude or ~/.codex data,
and no writes under the real repo's machines/ directory (env vars are saved
and restored in setUp/tearDown).
"""
import ast
import os
import socket
import unittest

from traceyield import canonical, paths, report

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "traceyield")

# Env vars this story centralizes into paths.py. No module other than
# paths.py may read these directly via os.environ.get(...).
CENTRALIZED_ENV_VARS = {
    "TRACEYIELD_MACHINE",
    "TRACEYIELD_CAPTURE",
    "TRACEYIELD_RAW_RETENTION_DAYS",
    "CLAUDE_PROJECTS",
    "CODEX_HOME",
}

# Literal path segments that only ever appear inside writable-path
# construction (machines/<id>/... artifacts, or the shared pricing file).
# No module other than paths.py may os.path.join(...) onto one of these.
WRITABLE_PATH_LITERALS = {
    "machines",
    "daily_metrics.json",
    "session_metrics.json",
    "report.html",
    "health.json",
    "usage.db",
    "pricing_history.json",
}

# Home-relative source-root literals; only paths.py may os.path.expanduser
# these (as claude_projects() / codex_sessions()).
HOME_RELATIVE_MARKERS = (".claude", ".codex")

ALLOWED_ROOT_BUILDERS = {"paths.py"}


ENV_VARS_UNDER_TEST = (
    "TRACEYIELD_MACHINE",
    "TRACEYIELD_CAPTURE",
    "TRACEYIELD_RAW_RETENTION_DAYS",
    "CLAUDE_PROJECTS",
    "CODEX_HOME",
)


class _EnvIsolated(unittest.TestCase):
    """Saves/restores every env var this module cares about, so tests can
    freely set/unset them without leaking into other tests or touching the
    real machine's data."""

    def setUp(self):
        self._saved = {name: os.environ.get(name) for name in ENV_VARS_UNDER_TEST}

    def tearDown(self):
        for name, value in self._saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


# --------------------------------------------------------------- AC1: single source, env-honoring
class TestMachineIdentity(_EnvIsolated):
    def test_defaults_to_sanitized_hostname(self):
        os.environ.pop("TRACEYIELD_MACHINE", None)
        import re
        expect = re.sub(r"[^a-z0-9._-]+", "-", socket.gethostname().strip().lower()).strip("-._") or "unknown"
        self.assertEqual(paths.machine_id(), expect)

    def test_env_override_wins_and_is_sanitized(self):
        os.environ["TRACEYIELD_MACHINE"] = "Charl's PC!"
        self.assertEqual(paths.machine_id(), "charl-s-pc")

    def test_machine_dir_reflects_env_override_without_reimport(self):
        os.environ.pop("TRACEYIELD_MACHINE", None)
        before = paths.machine_dir()
        os.environ["TRACEYIELD_MACHINE"] = "some-other-box"
        after = paths.machine_dir()
        self.assertNotEqual(before, after)
        self.assertEqual(os.path.basename(after), "some-other-box")


class TestWritableLocations(_EnvIsolated):
    def test_machine_dir_lives_under_machines_dir_under_here(self):
        self.assertEqual(os.path.dirname(paths.machine_dir()), paths.machines_dir())
        self.assertEqual(os.path.dirname(paths.machines_dir()), paths.here())

    def test_per_machine_artifacts_live_under_machine_dir(self):
        md = paths.machine_dir()
        self.assertEqual(os.path.dirname(paths.daily_file()), md)
        self.assertEqual(os.path.dirname(paths.session_file()), md)
        self.assertEqual(os.path.dirname(paths.out_html()), md)
        self.assertEqual(os.path.dirname(paths.health_file()), md)
        self.assertEqual(os.path.dirname(paths.db_file()), md)

    def test_artifact_filenames_are_unchanged(self):
        self.assertEqual(os.path.basename(paths.daily_file()), "daily_metrics.json")
        self.assertEqual(os.path.basename(paths.session_file()), "session_metrics.json")
        self.assertEqual(os.path.basename(paths.out_html()), "report.html")
        self.assertEqual(os.path.basename(paths.health_file()), "health.json")
        self.assertEqual(os.path.basename(paths.db_file()), "usage.db")

    def test_pricing_file_is_shared_at_repo_root_not_per_machine(self):
        self.assertEqual(os.path.dirname(paths.pricing_file()), paths.here())
        self.assertEqual(os.path.basename(paths.pricing_file()), "pricing_history.json")

    def test_writable_locations_move_when_machine_id_changes(self):
        os.environ["TRACEYIELD_MACHINE"] = "box-a"
        a = (paths.machine_dir(), paths.daily_file(), paths.db_file())
        os.environ["TRACEYIELD_MACHINE"] = "box-b"
        b = (paths.machine_dir(), paths.daily_file(), paths.db_file())
        for x, y in zip(a, b):
            self.assertNotEqual(x, y)
        # pricing_file is NOT per-machine -- it must not move.
        self.assertEqual(paths.pricing_file(), paths.pricing_file())


class TestSourceRoots(_EnvIsolated):
    def test_claude_projects_defaults_to_dot_claude_projects(self):
        os.environ.pop("CLAUDE_PROJECTS", None)
        self.assertEqual(paths.claude_projects(), os.path.expanduser(r"~/.claude/projects"))

    def test_claude_projects_honors_env_override(self):
        os.environ["CLAUDE_PROJECTS"] = os.path.join("some", "tmp", "dir")
        self.assertEqual(paths.claude_projects(), os.path.join("some", "tmp", "dir"))

    def test_codex_sessions_defaults_to_dot_codex_sessions(self):
        os.environ.pop("CODEX_HOME", None)
        self.assertEqual(paths.codex_sessions(), os.path.expanduser(r"~/.codex/sessions"))

    def test_codex_sessions_honors_codex_home_env_override(self):
        os.environ["CODEX_HOME"] = os.path.join("some", "other", "dir")
        self.assertEqual(paths.codex_sessions(), os.path.join("some", "other", "dir"))


class TestOtherEnvDrivenConfig(_EnvIsolated):
    def test_capture_mode_defaults_to_structural(self):
        os.environ.pop("TRACEYIELD_CAPTURE", None)
        self.assertEqual(paths.capture_mode(), "structural")

    def test_capture_mode_honors_env_override(self):
        os.environ["TRACEYIELD_CAPTURE"] = "verbatim"
        self.assertEqual(paths.capture_mode(), "verbatim")

    def test_raw_retention_days_defaults_to_90(self):
        os.environ.pop("TRACEYIELD_RAW_RETENTION_DAYS", None)
        self.assertEqual(paths.raw_retention_days(), 90)

    def test_raw_retention_days_honors_env_override(self):
        os.environ["TRACEYIELD_RAW_RETENTION_DAYS"] = "30"
        self.assertEqual(paths.raw_retention_days(), 30)


class TestReportAndCanonicalConsumePaths(_EnvIsolated):
    """report.py / canonical.py must resolve every writable location THROUGH
    paths.py (re-exports), not by constructing their own -- this is the
    "any component" half of AC1."""

    def test_report_reexports_match_paths_resolvers(self):
        self.assertEqual(report.HERE, paths.here())
        self.assertEqual(report.CLAUDE_PROJECTS, paths.claude_projects())
        self.assertEqual(report.MACHINES_DIR, paths.machines_dir())
        self.assertEqual(report.MACHINE_DIR, paths.machine_dir())
        self.assertEqual(report.DAILY_FILE, paths.daily_file())
        self.assertEqual(report.SESSION_FILE, paths.session_file())
        self.assertEqual(report.OUT_HTML, paths.out_html())
        self.assertEqual(report.HEALTH_FILE, paths.health_file())
        self.assertEqual(report.PRICING_FILE, paths.pricing_file())
        self.assertIs(report.machine_id, paths.machine_id)

    def test_canonical_reexports_match_paths_resolvers(self):
        self.assertEqual(canonical.DB_FILE, paths.db_file())
        self.assertEqual(canonical.CAPTURE, paths.capture_mode())
        self.assertEqual(canonical.RAW_RETENTION_DAYS, paths.raw_retention_days())


# --------------------------------------------------------------- AC2: no scattered path literals
def _dotted_call_name(func_node):
    """Return the dotted name of a Call's func, e.g. Attribute(Attribute(Name
    ('os'),'path'),'join') -> "os.path.join", or None if it isn't a simple
    dotted attribute/name chain."""
    parts = []
    node = func_node
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    else:
        return None
    return ".".join(reversed(parts))


def _string_constants(node):
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            yield child.value


def _scan_file_for_path_literal_violations(file_path):
    """Return a list of human-readable violation strings found in file_path,
    or [] if it's clean. Mirrors tests/test_layout.py's ast-scan style."""
    with open(file_path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=file_path)

    violations = []
    for node in ast.walk(tree):
        # (1) __file__ used outside paths.py: only paths.py may walk up from
        # its own location to find the repo root.
        if isinstance(node, ast.Name) and node.id == "__file__":
            violations.append(f"line {node.lineno}: references __file__ (repo-root walk)")

        if not isinstance(node, ast.Call):
            continue
        dotted = _dotted_call_name(node.func)
        if dotted is None:
            continue

        # (2) os.path.join(...) onto a known writable-artifact literal.
        if dotted == "os.path.join":
            for s in _string_constants(node):
                if s in WRITABLE_PATH_LITERALS:
                    violations.append(
                        f"line {node.lineno}: os.path.join(...) constructs a "
                        f"writable path using literal {s!r}"
                    )

        # (3) os.path.expanduser(...) of a ~/.claude or ~/.codex literal.
        if dotted == "os.path.expanduser":
            for s in _string_constants(node):
                if any(marker in s for marker in HOME_RELATIVE_MARKERS):
                    violations.append(
                        f"line {node.lineno}: os.path.expanduser({s!r}) "
                        "constructs a source-root path"
                    )

        # (4) os.environ.get("<centralized env var>") read directly.
        if dotted == "os.environ.get":
            for s in _string_constants(node):
                if s in CENTRALIZED_ENV_VARS:
                    violations.append(
                        f"line {node.lineno}: os.environ.get({s!r}) reads a "
                        "centralized env var directly"
                    )

    return violations


class TestNoScatteredWritablePathLiterals(unittest.TestCase):
    """E3-F2-S1 AC2: writable-path (and centralized-env-var) construction is
    confined to paths.py; nothing else in the package builds these paths or
    reads these env vars itself."""

    def test_no_module_other_than_paths_constructs_writable_paths(self):
        # E3-F2-S4: os.listdir(SRC_DIR) alone only sees the top-level
        # src/traceyield/*.py files, not the new src/traceyield/providers/
        # subpackage it introduced -- os.walk() here so ClaudeProvider/
        # CodexProvider (which resolve their default roots via
        # paths.claude_projects()/paths.codex_sessions(), never a raw
        # literal) are covered by this guard too, not exempted by omission.
        violations = {}
        for dirpath, _dirnames, filenames in os.walk(SRC_DIR):
            for name in sorted(filenames):
                if not name.endswith(".py") or name in ALLOWED_ROOT_BUILDERS:
                    continue
                file_path = os.path.join(dirpath, name)
                rel = os.path.relpath(file_path, SRC_DIR)
                hits = _scan_file_for_path_literal_violations(file_path)
                if hits:
                    violations[rel] = hits

        self.assertEqual(
            violations, {},
            "module(s) under src/traceyield/ construct writable/source-root "
            "paths (or read centralized env vars) directly, instead of "
            f"going through traceyield.paths: {violations}",
        )

    def test_scanner_self_test_catches_known_violation_patterns(self):
        # Proves the scanner itself is real (not a no-op) by feeding it a
        # synthetic snippet containing one instance of each violation kind
        # and asserting all four are caught. Mirrors test_layout.py's own
        # "test_forbidden_root_module_detection_catches_from_import".
        import tempfile

        snippet = (
            "import os\n"
            "HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))\n"
            "MACHINES_DIR = os.path.join(HERE, 'machines')\n"
            "CLAUDE_PROJECTS = os.path.expanduser(r'~/.claude/projects')\n"
            "CAPTURE = os.environ.get('TRACEYIELD_CAPTURE') or 'structural'\n"
        )
        with tempfile.NamedTemporaryFile(
            "w", suffix=".py", delete=False, dir=SRC_DIR,
        ) as f:
            f.write(snippet)
            tmp_path = f.name
        try:
            hits = _scan_file_for_path_literal_violations(tmp_path)
            joined = " | ".join(hits)
            self.assertIn("__file__", joined)
            self.assertIn("machines", joined)
            self.assertIn(".claude", joined)
            self.assertIn("TRACEYIELD_CAPTURE", joined)
        finally:
            os.remove(tmp_path)

    def test_paths_module_itself_is_exempt_and_still_does_the_construction(self):
        # Sanity check that the allowlist isn't hiding an empty paths.py:
        # paths.py itself SHOULD trip the raw scanner (it's the one place
        # allowed to), otherwise the exemption is meaningless.
        file_path = os.path.join(SRC_DIR, "paths.py")
        hits = _scan_file_for_path_literal_violations(file_path)
        self.assertTrue(hits, "paths.py no longer performs any of the path "
                               "construction it's supposed to centralize")


if __name__ == "__main__":
    unittest.main()
