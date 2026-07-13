#!/usr/bin/env python3
"""Tests for E3-F2-S4: the provider protocol + split provider modules.

Covers both acceptance-criteria scenarios of the story:

  AC1 "Provider protocol" -- `traceyield.providers.base.Provider` is a
      `typing.Protocol` (`@runtime_checkable`) that matches exactly what
      `canonical.ingest()` calls on a provider (`name`, `roots()`,
      `parse_file(path)`); `ClaudeProvider` and `CodexProvider` (now living
      in their own `traceyield.providers.claude` / `traceyield.providers.codex`
      modules) each satisfy it structurally, with no shared base class. A
      static AST guard, mirroring tests/test_models.py's/test_pricing.py's
      style, proves each provider module imports only neutral traceyield
      submodules (models/paths/pricing/classification/transcripts) plus
      stdlib -- never `report`, never `canonical`. A self-test proves the
      guard is real: fed a snippet shaped like a provider module that
      imports `canonical`, it must flag the violation.

  AC2 "Adding a provider" -- a synthetic third provider (`FakeProvider`,
      defined only in this test file) that satisfies the `Provider` protocol
      structurally (no import of/subclassing anything from `traceyield`
      other than the neutral `models` dataclasses it yields) is registered
      and ingested by `canonical.ingest()` into a temp `usage.db`, and its
      records land there correctly -- with zero edits to report.py. This is
      the test that proves the seam is real rather than claimed: nothing
      about `FakeProvider` is special-cased anywhere in canonical.py.

Stdlib-only (unittest).
"""
import ast
import os
import tempfile
import unittest

from traceyield import canonical, models
from traceyield.providers import ClaudeProvider, CodexProvider, Provider
from traceyield.providers.base import Provider as BaseProvider

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "traceyield")
PROVIDERS_DIR = os.path.join(SRC_DIR, "providers")

# The neutral modules every provider module may depend on (mirrors
# test_models.py's NEUTRAL_MODULES) -- never `report`, never `canonical`.
NEUTRAL_SUBMODULES = {"models", "paths", "pricing", "classification", "transcripts"}


# --------------------------------------------------------------- AC1: the protocol itself
class TestProviderProtocolShape(unittest.TestCase):
    """Provider is the exact contract canonical.ingest() relies on: a `name`
    identifier plus the two methods ingest() actually calls, `roots()` and
    `parse_file(path)`. Nothing more, nothing invented."""

    def test_provider_is_the_same_object_reexported_from_the_package(self):
        self.assertIs(Provider, BaseProvider)

    def test_provider_is_a_runtime_checkable_protocol(self):
        import typing
        self.assertTrue(issubclass(type(Provider), type(typing.Protocol)) or hasattr(Provider, "_is_protocol"))
        self.assertTrue(getattr(Provider, "_is_protocol", False))
        self.assertTrue(getattr(Provider, "_is_runtime_protocol", False))

    def test_claude_provider_satisfies_the_protocol(self):
        self.assertIsInstance(ClaudeProvider(root="unused"), Provider)

    def test_codex_provider_satisfies_the_protocol(self):
        self.assertIsInstance(CodexProvider(root="unused"), Provider)

    def test_an_object_missing_parse_file_does_not_satisfy_the_protocol(self):
        class Incomplete:
            name = "incomplete"

            def roots(self):
                return []

        self.assertNotIsInstance(Incomplete(), Provider)

    def test_an_object_missing_name_does_not_satisfy_the_protocol(self):
        class NoName:
            def roots(self):
                return []

            def parse_file(self, path):
                return iter(())

        self.assertNotIsInstance(NoName(), Provider)

    def test_a_plain_duck_typed_object_with_no_traceyield_coupling_satisfies_it(self):
        # AC2's whole point: a NEW provider only needs to shape-match, not
        # import/subclass anything from traceyield.providers.
        class Duck:
            name = "duck"

            def roots(self):
                return ["/tmp/duck"]

            def parse_file(self, path):
                return iter(())

        self.assertIsInstance(Duck(), Provider)


# --------------------------------------------------------------- AST guard helpers
def _module_imports(file_path):
    """Return the set of top-level module names this file imports, found via
    `import X` / `from X import ...` anywhere in the AST. Mirrors
    test_models.py's/test_pricing.py's helper of the same shape."""
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
    `from traceyield import paths` -> {"paths"}, `from traceyield.models
    import Turn` -> {"models"}. Mirrors test_models.py's helper exactly."""
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
            found.add(parts[1])
        else:
            found.update(alias.name for alias in node.names)
    return found


def _provider_module_files():
    return [
        os.path.join(PROVIDERS_DIR, name)
        for name in sorted(os.listdir(PROVIDERS_DIR))
        if name.endswith(".py")
    ]


class TestProviderModulesDependOnlyOnNeutralModules(unittest.TestCase):
    """AC1: each provider module (claude.py, codex.py, base.py, __init__.py
    under src/traceyield/providers/) depends only on neutral traceyield
    submodules -- never report, never canonical."""

    def test_no_provider_module_imports_report_or_canonical(self):
        violations = {}
        for file_path in _provider_module_files():
            bad = (_module_imports(file_path) | _traceyield_submodule_names(file_path)) & {"report", "canonical"}
            if bad:
                violations[os.path.basename(file_path)] = sorted(bad)
        self.assertEqual(
            violations, {},
            f"provider module(s) import report/canonical (must depend only "
            f"on neutral models/utilities): {violations}",
        )

    def test_no_provider_module_imports_non_neutral_traceyield_submodules(self):
        violations = {}
        allowed = NEUTRAL_SUBMODULES | {"providers"}  # providers/*.py may import each other (e.g. base's Provider)
        for file_path in _provider_module_files():
            submodules = _traceyield_submodule_names(file_path)
            bad = submodules - allowed
            if bad:
                violations[os.path.basename(file_path)] = sorted(bad)
        self.assertEqual(
            violations, {},
            f"provider module(s) import non-neutral traceyield submodule(s): {violations}",
        )

    def test_scanner_self_test_catches_a_provider_importing_canonical(self):
        # Proves the guard is real: a snippet shaped like a provider module
        # that reaches into canonical.py (the mistake constraint 2 warns
        # against -- "a provider importing the store is the same layering
        # mistake in a new coat") must be caught.
        import tempfile as tf
        snippet = (
            "from traceyield import canonical\n"
            "class RogueProvider:\n"
            "    name = 'rogue'\n"
            "    def roots(self):\n"
            "        return []\n"
            "    def parse_file(self, path):\n"
            "        return canonical.sha(path)\n"
        )
        with tf.NamedTemporaryFile(
            "w", suffix=".py", delete=False, dir=PROVIDERS_DIR,
        ) as f:
            f.write(snippet)
            tmp_path = f.name
        try:
            self.assertIn("canonical", _traceyield_submodule_names(tmp_path))
        finally:
            os.remove(tmp_path)

    def test_scanner_self_test_catches_a_provider_importing_report(self):
        import tempfile as tf
        snippet = "from traceyield import report\n"
        with tf.NamedTemporaryFile(
            "w", suffix=".py", delete=False, dir=PROVIDERS_DIR,
        ) as f:
            f.write(snippet)
            tmp_path = f.name
        try:
            self.assertIn("report", _traceyield_submodule_names(tmp_path))
        finally:
            os.remove(tmp_path)


# --------------------------------------------------------------- AC2: adding a provider
class FakeProvider:
    """A synthetic THIRD provider, defined only in this test. It satisfies
    Provider structurally (name/roots/parse_file) and yields nothing but the
    neutral traceyield.models dataclasses -- exactly what any real, external
    provider would need to do to be ingested. It imports nothing from
    traceyield.providers/canonical/report."""

    name = "fake"

    def __init__(self, session_id="fake-s1"):
        self.session_id = session_id

    def roots(self):
        return ["unused-by-parse_file-directly"]

    def parse_file(self, path):
        yield models.Turn(
            provider=self.name, session_id=self.session_id, turn_id="fake-t1",
            ts="2026-01-01T00:00:00Z", model="fake-model-1",
            input_fresh=10, output=5, tier=None,
        )
        yield models.Session(
            provider=self.name, session_id=self.session_id,
            project="fakeproj", first_ts="2026-01-01T00:00:00Z",
            last_ts="2026-01-01T00:00:00Z",
        )


class TestAddingAThirdProviderRequiresNoReportingLayerEdits(unittest.TestCase):
    """AC2: register FakeProvider directly with canonical.ingest() (bypassing
    default_providers() entirely -- exactly how a caller/test wires in a new
    provider) and prove its records land in a real usage.db schema, without
    canonical.ingest() knowing anything about FakeProvider specifically, and
    without report.py being touched at all (this test file never imports or
    exercises traceyield.report)."""

    def test_fake_provider_satisfies_the_protocol(self):
        self.assertIsInstance(FakeProvider(), Provider)

    def test_fake_provider_records_are_ingested_into_usage_db(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            # ingest() globs *.jsonl under prov.roots(); give FakeProvider a
            # root containing exactly one (empty-content, since parse_file
            # ignores `path` and yields fixed Recs) real file so the glob
            # finds something to call parse_file() on.
            root = os.path.join(tmp.name, "fakeroot")
            os.makedirs(root, exist_ok=True)
            with open(os.path.join(root, "conv.jsonl"), "w", encoding="utf-8") as f:
                f.write("")

            class RootedFakeProvider(FakeProvider):
                def roots(self):
                    return [root]

            conn = canonical.open_db(":memory:")
            files, recs = canonical.ingest(conn, [RootedFakeProvider()])
            self.assertEqual(files, 1)
            self.assertGreaterEqual(recs, 2)

            turn = conn.execute(
                "SELECT provider,session_id,model,input_fresh,output "
                "FROM turn WHERE turn_id='fake-t1'"
            ).fetchone()
            self.assertEqual(turn, ("fake", "fake-s1", "fake-model-1", 10, 5))

            sess = conn.execute(
                "SELECT provider,project FROM session WHERE session_id='fake-s1'"
            ).fetchone()
            self.assertEqual(sess, ("fake", "fakeproj"))
        finally:
            tmp.cleanup()

    def test_fake_provider_coexists_with_the_default_providers(self):
        # Registering a third provider alongside the two real ones (the
        # realistic "adding a provider" scenario) -- all three ingest into
        # the same db without interfering.
        tmp = tempfile.TemporaryDirectory()
        try:
            root = os.path.join(tmp.name, "fakeroot")
            os.makedirs(root, exist_ok=True)
            with open(os.path.join(root, "conv.jsonl"), "w", encoding="utf-8") as f:
                f.write("")

            class RootedFakeProvider(FakeProvider):
                def roots(self):
                    return [root]

            empty_claude_root = os.path.join(tmp.name, "claude-empty")
            empty_codex_root = os.path.join(tmp.name, "codex-empty")
            os.makedirs(empty_claude_root, exist_ok=True)
            os.makedirs(empty_codex_root, exist_ok=True)

            providers = [
                ClaudeProvider(root=empty_claude_root),
                CodexProvider(root=empty_codex_root),
                RootedFakeProvider(),
            ]
            conn = canonical.open_db(":memory:")
            files, recs = canonical.ingest(conn, providers)
            self.assertEqual(files, 1)   # only the fake provider's root has a file
            self.assertGreaterEqual(recs, 2)
            self.assertEqual(
                conn.execute("SELECT count(*) FROM turn WHERE provider='fake'").fetchone()[0], 1)
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
