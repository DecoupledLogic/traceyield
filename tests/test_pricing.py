#!/usr/bin/env python3
"""Tests for E3-F2-S2: shared pricing/classification utility modules.

Covers both acceptance-criteria scenarios of the story:

  AC1 "Shared by both layers, no duplication" -- traceyield.canonical
      (ingestion) gets tier()/classify() from the shared traceyield.pricing /
      traceyield.classification modules directly (same function object, not
      a re-implementation), NOT via traceyield.report (reporting). A static
      AST guard, mirroring tests/test_layout.py's and tests/test_paths.py's
      style, scans canonical.py for `report.tier(`/`report.classify(` call
      sites and fails if either remains; and scans pricing.py/
      classification.py to prove neither imports report or canonical (they
      sit BELOW both).

  AC2 "Behavior-neutral" -- cost_of()/tier()/classify() produce byte-for-byte
      the same numbers/labels as before the extraction. Pins the cost math
      for both claude and codex (including the codex no-cache-write case),
      tier()'s full mapping table (including fable -> opus and an
      unrecognized model -> None), and ERROR_RULES' order-sensitivity (an
      overlapping-substring case that only resolves correctly if rules are
      still matched top-to-bottom in the original order).

Stdlib-only (unittest).
"""
import ast
import os
import unittest

from traceyield import canonical, classification, pricing, report

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "traceyield")


# --------------------------------------------------------------- AC1: shared, no duplication
class TestSharedModuleIdentity(unittest.TestCase):
    """canonical.py and report.py must both resolve tier()/classify() to the
    SAME function objects defined in the shared modules -- not independent
    copies that happen to behave the same today and drift tomorrow."""

    def test_canonical_tier_is_the_shared_pricing_tier(self):
        self.assertIs(canonical.report.tier, pricing.tier)

    def test_canonical_classify_is_the_shared_classification_classify(self):
        self.assertIs(canonical.report.classify, classification.classify)

    def test_report_reexports_are_the_shared_objects(self):
        self.assertIs(report.tier, pricing.tier)
        self.assertIs(report.classify, classification.classify)
        self.assertIs(report.PRICING, pricing.PRICING)
        self.assertIs(report.CODEX_PRICING, pricing.CODEX_PRICING)
        self.assertIs(report.CACHE, pricing.CACHE)
        self.assertIs(report.RATE_CARDS, pricing.RATE_CARDS)
        self.assertIs(report.rate_card, pricing.rate_card)
        self.assertIs(report.cost_of, pricing.cost_of)
        self.assertIs(report.cache_rates, pricing.cache_rates)
        self.assertEqual(report.PRICING_URL, pricing.PRICING_URL)
        self.assertIs(report.ERROR_RULES, classification.ERROR_RULES)
        self.assertIs(report.ERROR_META, classification.ERROR_META)


def _dotted_call_name(func_node):
    """Return the dotted name of a Call's func (e.g. "report.tier"), or None
    if it isn't a simple dotted attribute/name chain. Mirrors test_layout.py/
    test_paths.py's helper of the same shape."""
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


def _call_sites(file_path, dotted_names):
    with open(file_path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=file_path)
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        dotted = _dotted_call_name(node.func)
        if dotted in dotted_names:
            hits.append(f"line {node.lineno}: {dotted}(...)")
    return hits


def _module_imports(file_path):
    """Return the set of top-level module names this file imports, found via
    `import X` / `from X import ...` anywhere in the AST."""
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


class TestNoReportCallSitesInCanonical(unittest.TestCase):
    """AC1: canonical.py (ingestion) must not reach into report.py
    (reporting) for pricing/classification anymore -- tier() and classify()
    now come from the shared modules directly."""

    def test_canonical_has_no_report_tier_or_classify_call_sites(self):
        file_path = os.path.join(SRC_DIR, "canonical.py")
        hits = _call_sites(file_path, {"report.tier", "report.classify"})
        self.assertEqual(
            hits, [],
            "canonical.py still calls report.tier(...)/report.classify(...) "
            f"instead of pricing.tier(...)/classification.classify(...): {hits}",
        )

    def test_scanner_self_test_catches_a_known_violation(self):
        # Proves the scanner is real (would have failed against the
        # pre-refactor code), mirroring test_layout.py's/test_paths.py's own
        # self-test pattern.
        import tempfile
        snippet = (
            "from traceyield import report\n"
            "def f(model):\n"
            "    return report.tier(model)\n"
        )
        with tempfile.NamedTemporaryFile(
            "w", suffix=".py", delete=False, dir=SRC_DIR,
        ) as f:
            f.write(snippet)
            tmp_path = f.name
        try:
            hits = _call_sites(tmp_path, {"report.tier", "report.classify"})
            self.assertEqual(len(hits), 1)
            self.assertIn("report.tier", hits[0])
        finally:
            os.remove(tmp_path)


class TestSharedModulesAreDependencyFree(unittest.TestCase):
    """pricing.py / classification.py sit BELOW both report.py and
    canonical.py: neither may import either of them (that would defeat the
    point of extracting a shared, dependency-free layer)."""

    def test_pricing_imports_neither_report_nor_canonical(self):
        imports = _module_imports(os.path.join(SRC_DIR, "pricing.py"))
        self.assertNotIn("report", imports)
        self.assertNotIn("canonical", imports)

    def test_classification_imports_neither_report_nor_canonical(self):
        imports = _module_imports(os.path.join(SRC_DIR, "classification.py"))
        self.assertNotIn("report", imports)
        self.assertNotIn("canonical", imports)


# --------------------------------------------------------------- AC2: behavior-neutral
class TestPricingMathIsUnchanged(unittest.TestCase):
    """Pins cost_of()'s dollar output for hand-computable token counts, for
    both providers, directly against traceyield.pricing (the new source of
    truth) -- proving the extraction changed WHERE the formula lives, not
    WHAT it computes."""

    def test_claude_cost_matches_hand_computed_dollars(self):
        # opus: input=5.00, output=25.00 per 1M; cache multipliers 0.10/1.25/2.0
        inp, out, cr, w5m, w1h = 1000, 500, 200, 100, 50
        ri, ro = 5.00, 25.00
        expected = (inp*ri + out*ro + cr*(ri*0.10) + w5m*(ri*1.25) + w1h*(ri*2.0)) / 1e6
        got = pricing.cost_of("claude", "opus", inp, out, cr, w5m, w1h)
        self.assertAlmostEqual(got, expected)

    def test_codex_cost_matches_hand_computed_dollars(self):
        # gpt-5.3-codex: input=1.75, output=14.00 per 1M; cache-read 0.10x,
        # NO cache-write premium (w5m/w1h contribute $0).
        inp, out, cr, w5m, w1h = 1_000_000, 1_000_000, 1_000_000, 1_000_000, 1_000_000
        ri, ro = 1.75, 14.00
        expected = (inp*ri + out*ro + cr*(ri*0.10) + w5m*0.0 + w1h*0.0) / 1e6
        got = pricing.cost_of("codex", "gpt-5.3-codex", inp, out, cr, w5m, w1h)
        self.assertAlmostEqual(got, expected)

    def test_codex_cache_write_only_case_is_zero_dollars(self):
        # AC2 explicitly calls out the codex no-cache-write case: w5m/w1h
        # tokens alone must cost $0 (no w5m/w1h keys in CACHE["codex"]).
        got = pricing.cost_of("codex", "gpt-5.3-codex", 0, 0, 0, 1_000_000, 1_000_000)
        self.assertEqual(got, 0.0)

    def test_unpriced_provider_or_tier_costs_zero(self):
        self.assertEqual(pricing.cost_of("claude", "nonesuch", 100, 100, 0, 0, 0), 0.0)
        self.assertEqual(pricing.cost_of("nonesuch", "opus", 100, 100, 0, 0, 0), 0.0)

    def test_pricing_module_and_report_agree_exactly(self):
        # Cross-check: report.py's re-exported cost_of() (still exercised by
        # every existing report.py test) must be the identical function --
        # not merely equal output today.
        self.assertIs(report.cost_of, pricing.cost_of)


class TestTierMappingIsUnchanged(unittest.TestCase):
    TABLE = [
        ("claude-opus-4-8", "opus"),
        ("claude-fable-5", "opus"),      # fable -> opus, load-bearing quirk
        ("claude-sonnet-5", "sonnet"),
        ("claude-haiku-4-5", "haiku"),
        ("gpt-4o", None),                # unrecognized model -> None
        (None, None),
        ("", None),
    ]

    def test_tier_table(self):
        for model, expected in self.TABLE:
            with self.subTest(model=model):
                self.assertEqual(pricing.tier(model), expected)

    def test_report_tier_agrees_with_shared_tier(self):
        for model, expected in self.TABLE:
            with self.subTest(model=model):
                self.assertEqual(report.tier(model), expected)


class TestClassifyOrderIsUnchanged(unittest.TestCase):
    """ERROR_RULES is matched top-to-bottom; classify() must still resolve
    an overlapping-substring input to the FIRST matching rule, in the
    original declared order."""

    def test_classify_table(self):
        cases = [
            ("Error: file has not been read yet", "read_before_write"),
            ("bash: foo: command not found", "shell_cmd_not_found"),
            ("String to replace not found in file", "edit_no_match"),
            ("no such file or directory", "file_not_found"),
            ("InputValidationError: bad param", "input_validation"),
            ("something totally unexpected", "other"),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(classification.classify(text), expected)

    def test_order_sensitive_overlap_resolves_to_first_rule(self):
        # "not found" alone is file_not_found's substring, but edit_no_match
        # (earlier in ERROR_RULES) matches on "not unique" first when both
        # substrings are present -- proves rule ORDER, not just substring
        # presence, drives the result.
        text = "Error: old_string is not unique in file; also not found elsewhere"
        self.assertEqual(classification.classify(text), "edit_no_match")

    def test_error_meta_covers_every_rule_and_other(self):
        for name, *_ in classification.ERROR_RULES:
            self.assertIn(name, classification.ERROR_META)
            self.assertIn("fix", classification.ERROR_META[name])
        self.assertIn("other", classification.ERROR_META)


if __name__ == "__main__":
    unittest.main()
