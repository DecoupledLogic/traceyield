#!/usr/bin/env python3
"""
Tests for traceyield.report's retained surface: the report-facade re-exports
(pure helpers, rate cards, machine identity) and the pricing-drift I/O
(parse_pricing_page/check_pricing_drift) that stayed in report.py per
E3-F3-S1 (these are reporting-side concerns -- drift-checking the dashboard's
rates -- not pure rate-card logic, so they don't belong in pricing.py).

As of E3-F3-S3, the aggregation/persistence/health/html-boundary test classes
that used to live in this file have moved to tests/test_aggregation.py,
tests/test_persistence.py, tests/test_health.py, and tests/test_html.py
respectively, mirroring the E3-F3-S1 module split; the shared synthetic-
transcript fixture builders moved to tests/helpers.py.

Stdlib-only (unittest) so they run with no extra dependencies:
    python -m unittest discover -s tests   (or: python -m pytest tests -q)

Imports the installed traceyield package (`pip install -e .`), not the repo
root or src/ tree directly, so the suite exercises the same import surface a
consumer of the package would.
"""
import contextlib, io, os, re, socket, unittest, warnings

from traceyield import report, canonical

# report.py favors a terse `json.load(open(...))` idiom that leaks file handles
# on CPython's GC schedule; that's a deliberate single-file style choice, not a
# bug the tests should fail on. Keep test output readable.
warnings.simplefilter("ignore", ResourceWarning)


# --------------------------------------------------------------- pure helpers
class TestPureHelpers(unittest.TestCase):
    def test_tier_mapping(self):
        self.assertEqual(report.tier("claude-opus-4-8"), "opus")
        self.assertEqual(report.tier("claude-fable-5"), "opus")      # fable → opus tier
        self.assertEqual(report.tier("claude-sonnet-5"), "sonnet")
        self.assertEqual(report.tier("claude-haiku-4-5"), "haiku")
        self.assertIsNone(report.tier("gpt-4o"))
        self.assertIsNone(report.tier(None))
        self.assertIsNone(report.tier(""))

    def test_cache_rates(self):
        r = report.cache_rates(5.0)
        self.assertAlmostEqual(r["read"], 0.5)
        self.assertAlmostEqual(r["w5m"], 6.25)
        self.assertAlmostEqual(r["w1h"], 10.0)

    def test_classify_matches_rules(self):
        self.assertEqual(report.classify("Error: file has not been read yet"), "read_before_write")
        self.assertEqual(report.classify("bash: foo: command not found"), "shell_cmd_not_found")
        self.assertEqual(report.classify("String to replace not found in file"), "edit_no_match")
        self.assertEqual(report.classify("no such file or directory"), "file_not_found")
        self.assertEqual(report.classify("InputValidationError: bad param"), "input_validation")

    def test_classify_unknown_is_other(self):
        self.assertEqual(report.classify("something totally unexpected"), "other")

    def test_error_meta_covers_every_rule_plus_other(self):
        for name, *_ in report.ERROR_RULES:
            self.assertIn(name, report.ERROR_META)
            self.assertIn("fix", report.ERROR_META[name])
        self.assertIn("other", report.ERROR_META)

    def test_top_sessions_sorts_caps_and_attaches_id(self):
        sessions = {f"s{i}": {"cost": float(i)} for i in range(5)}
        top = report.top_sessions(sessions, n=3)
        self.assertEqual([s["cost"] for s in top], [4.0, 3.0, 2.0])   # desc
        self.assertEqual(top[0]["id"], "s4")                          # id attached
        self.assertEqual(len(top), 3)                                 # capped


# --------------------------------------------------------------- per-provider rate cards
class TestRateCards(unittest.TestCase):
    """rate_card()/cost_of() are the per-provider layer wrapped around the
    existing flat PRICING map. Claude costs must come out byte-identical to
    the pre-refactor formula (this is the behavior-neutral oracle)."""

    def test_rate_card_returns_claude_tier_rates(self):
        self.assertEqual(report.rate_card("claude", "opus"), (5.00, 25.00))
        self.assertEqual(report.rate_card("claude", "sonnet"), report.PRICING["sonnet"])

    def test_rate_card_none_for_unpriced_tier_or_provider(self):
        self.assertIsNone(report.rate_card("claude", "nonesuch"))
        self.assertIsNone(report.rate_card("nonesuch", "opus"))

    def test_cost_of_matches_hand_computed_dollars(self):
        # opus: input=5.00, output=25.00 per 1M; cache multipliers 0.10/1.25/2.0
        inp, out, cr, w5m, w1h = 1000, 500, 200, 100, 50
        ri, ro = 5.00, 25.00
        expected = (inp*ri + out*ro + cr*(ri*0.10) + w5m*(ri*1.25) + w1h*(ri*2.0)) / 1e6
        got = report.cost_of("claude", "opus", inp, out, cr, w5m, w1h)
        self.assertAlmostEqual(got, expected)

    def test_cost_of_unpriced_provider_or_tier_is_zero(self):
        self.assertEqual(report.cost_of("claude", "nonesuch", 100, 100, 0, 0, 0), 0.0)
        self.assertEqual(report.cost_of("nonesuch", "opus", 100, 100, 0, 0, 0), 0.0)

    def test_cache_write_tiers_priced_at_1_25x_and_2x_base_input(self):
        # AC2: cache economics come from CACHE -- w5m priced at 1.25x, w1h at 2x
        # the base input rate. Isolate each write tier's dollar contribution.
        ri, ro = report.rate_card("claude", "sonnet")
        base = report.cost_of("claude", "sonnet", 0, 0, 0, 0, 0)
        self.assertEqual(base, 0.0)
        w5m_only = report.cost_of("claude", "sonnet", 0, 0, 0, 1_000_000, 0)
        w1h_only = report.cost_of("claude", "sonnet", 0, 0, 0, 0, 1_000_000)
        self.assertAlmostEqual(w5m_only, ri * 1.25)
        self.assertAlmostEqual(w1h_only, ri * 2.0)

    # ----------------------------------------------------- Codex rate card (E2-F1-S2)
    def test_codex_rate_card_priced_tier_matches_hand_computed_dollars(self):
        # AC1: gpt-5.3-codex is priced at (1.75, 14.00) per 1M with a 0.10x
        # cache-read multiplier and NO cache-write premium (w5m/w1h -> $0).
        self.assertEqual(report.rate_card("codex", "gpt-5.3-codex"), (1.75, 14.00))
        ri, ro = 1.75, 14.00
        inp = out = cr = w5m = w1h = 1_000_000
        expected = (inp*ri + out*ro + cr*(ri*0.10) + w5m*0.0 + w1h*0.0) / 1e6
        got = report.cost_of("codex", "gpt-5.3-codex", inp, out, cr, w5m, w1h)
        self.assertAlmostEqual(got, expected)
        # cache-write tokens contribute nothing regardless of magnitude.
        write_only = report.cost_of("codex", "gpt-5.3-codex", 0, 0, 0, 1_000_000, 1_000_000)
        self.assertEqual(write_only, 0.0)

    def test_codex_rate_card_second_priced_tier_locks_the_card(self):
        # Lock in the second priced tier so a future edit to CODEX_PRICING
        # can't silently drift without a failing test.
        self.assertEqual(report.rate_card("codex", "gpt-5.5"), (5.00, 30.00))
        ri, ro = 5.00, 30.00
        inp, out, cr = 1_000_000, 1_000_000, 1_000_000
        expected = (inp*ri + out*ro + cr*(ri*0.10)) / 1e6
        got = report.cost_of("codex", "gpt-5.5", inp, out, cr, 0, 0)
        self.assertAlmostEqual(got, expected)

    def test_codex_unpriced_tier_counts_volume_at_zero_dollars(self):
        # AC2: gpt-5 and gpt-5-codex are recognized (codex_tier() gives a
        # non-null label) but intentionally omitted from CODEX_PRICING, so
        # they still count tokens/msgs while costing $0 (Decision 0007 D3,
        # "volume-always / dollars-when-priced").
        for unpriced_tier in ("gpt-5", "gpt-5-codex"):
            self.assertIsNone(report.rate_card("codex", unpriced_tier))
            self.assertEqual(
                report.cost_of("codex", unpriced_tier, 1000, 500, 200, 100, 50), 0.0)

    def test_codex_unrecognized_model_still_untiered(self):
        # AC3: a model that isn't in the gpt-5* family stays untiered
        # (codex_tier() -> None), so aggregate()'s `tier IS NOT NULL` filter
        # excludes it entirely -- unchanged, direct-level assertion since no
        # new aggregate fixture is needed just to prove codex_tier()'s
        # boundary (see tests/test_aggregation.py's TestAggregateByProvider
        # ._mixed_conn for the existing aggregate()-level codex fixture,
        # which already covers a recognized tier).
        self.assertIsNone(canonical.codex_tier("some-unrelated-model"))
        self.assertIsNone(canonical.codex_tier("o3-mini"))


# --------------------------------------------------------------- machine identity
class TestMachineId(unittest.TestCase):
    """machine_id() picks the per-machine data directory. Hostname by default,
    TRACEYIELD_MACHINE override, always sanitized to a filesystem-safe slug."""

    def setUp(self):
        self._saved = os.environ.pop("TRACEYIELD_MACHINE", None)

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("TRACEYIELD_MACHINE", None)
        else:
            os.environ["TRACEYIELD_MACHINE"] = self._saved

    def _expect(self, raw):
        return re.sub(r"[^a-z0-9._-]+", "-", raw.strip().lower()).strip("-._") or "unknown"

    def test_defaults_to_sanitized_hostname(self):
        self.assertEqual(report.machine_id(), self._expect(socket.gethostname()))

    def test_env_override_wins_and_is_sanitized(self):
        os.environ["TRACEYIELD_MACHINE"] = "Charl's PC!"
        self.assertEqual(report.machine_id(), "charl-s-pc")

    def test_blank_override_falls_back_to_hostname(self):
        os.environ["TRACEYIELD_MACHINE"] = "   "
        self.assertEqual(report.machine_id(), self._expect(socket.gethostname()))

    def test_data_files_are_namespaced_under_machine_dir(self):
        # Derived artifacts live under machines/<id>/; pricing_history is shared
        # at the repo root because it comes from PRICING, not from transcripts.
        self.assertEqual(os.path.dirname(report.DAILY_FILE), report.MACHINE_DIR)
        self.assertEqual(os.path.dirname(report.SESSION_FILE), report.MACHINE_DIR)
        self.assertEqual(os.path.dirname(report.OUT_HTML), report.MACHINE_DIR)
        self.assertEqual(os.path.dirname(report.MACHINE_DIR), report.MACHINES_DIR)
        self.assertEqual(os.path.dirname(report.PRICING_FILE), report.HERE)


# --------------------------------------------------------------- pricing drift
# A trimmed fixture mirroring the real Anthropic pricing page: the Model pricing
# table (with a deprecated and a retired row to skip), plus a Batch table below
# it that lists the same tiers at HALF price — the parser must not read that one.
PRICING_PAGE = """\
# Pricing

## Model pricing

| Model | Base Input Tokens | 5m Cache Writes | 1h Cache Writes | Cache Hits & Refreshes | Output Tokens |
| ----- | ----------------- | --------------- | --------------- | ---------------------- | ------------- |
| Claude Opus 4.8 | $5 / MTok | $6.25 / MTok | $10 / MTok | $0.50 / MTok | $25 / MTok |
| Claude Opus 4.1 ([deprecated](/x)) | $15 / MTok | $18.75 / MTok | $30 / MTok | $1.50 / MTok | $75 / MTok |
| Claude Sonnet 5 [through August 31, 2026](/y) | $2 / MTok | $2.50 / MTok | $4 / MTok | $0.20 / MTok | $10 / MTok |
| Claude Sonnet 5 starting September 1, 2026 | $3 / MTok | $3.75 / MTok | $6 / MTok | $0.30 / MTok | $15 / MTok |
| Claude Haiku 4.5 | $1 / MTok | $1.25 / MTok | $2 / MTok | $0.10 / MTok | $5 / MTok |
| Claude Haiku 3.5 ([retired](/z)) | $0.80 / MTok | $1 / MTok | $1.60 / MTok | $0.08 / MTok | $4 / MTok |

## Batch processing

| Model | Batch input | Batch output |
| ----- | ----------- | ------------ |
| Claude Opus 4.8 | $2.50 / MTok | $12.50 / MTok |
| Claude Haiku 4.5 | $0.50 / MTok | $2.50 / MTok |
"""


class TestPricingDrift(unittest.TestCase):
    def test_parse_reads_input_and_output_columns(self):
        p = report.parse_pricing_page(PRICING_PAGE)
        self.assertEqual(p["opus"], (5.0, 25.0))
        self.assertEqual(p["haiku"], (1.0, 5.0))      # not the $0.50 batch row

    def test_parse_takes_first_nondeprecated_row_per_tier(self):
        p = report.parse_pricing_page(PRICING_PAGE)
        self.assertEqual(p["opus"], (5.0, 25.0))      # 4.8, not deprecated 4.1 ($15)
        self.assertEqual(p["sonnet"], (2.0, 10.0))    # intro row, not the Sept-1 $3 row

    def test_parse_ignores_tables_outside_model_pricing(self):
        # The Batch table lists Opus at $2.50/$12.50; the Model pricing value wins.
        self.assertEqual(report.parse_pricing_page(PRICING_PAGE)["opus"], (5.0, 25.0))

    def test_parse_returns_empty_when_section_absent(self):
        self.assertEqual(report.parse_pricing_page("# Pricing\n\nno table here"), {})

    def _drift(self, page):
        # check_pricing_drift() prints its findings to stdout; that's intended in a
        # real run, but here the "drift" is synthetic fixture data (e.g. opus 5.0+1
        # = 6.0), so swallow the output to keep the suite's stdout clean and avoid a
        # scary-looking "Anthropic=6.0" line that isn't a real rate.
        orig = report._fetch_pricing_page
        report._fetch_pricing_page = lambda url=None, timeout=15: page
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                return report.check_pricing_drift()
        finally:
            report._fetch_pricing_page = orig

    def _page_from(self, rates):
        """Build a minimal Model pricing table from a {tier: (in, out)} dict."""
        rows = "\n".join(f"| Claude {t.title()} X | ${i} / MTok | ${i*1.25} / MTok "
                         f"| ${i*2} / MTok | ${i*0.1} / MTok | ${o} / MTok |"
                         for t, (i, o) in rates.items())
        return ("## Model pricing\n\n| Model | Base Input | 5m | 1h | hit | Output |\n"
                "| - | - | - | - | - | - |\n" + rows + "\n\n## Batch\n")

    def test_drift_empty_when_pricing_matches_page(self):
        # Generated from the live PRICING dict, so this stays green across
        # legitimate rate edits (e.g. Sonnet intro pricing lapsing).
        self.assertEqual(self._drift(self._page_from(report.PRICING)), [])

    def test_drift_reports_changed_tier(self):
        bumped = dict(report.PRICING)
        oi, oo = bumped["opus"]
        bumped["opus"] = (oi + 1, oo)                       # page says opus costs $1 more
        drift = self._drift(self._page_from(bumped))
        self.assertTrue(any(d.startswith("opus:") for d in drift))
        self.assertFalse(any(d.startswith("haiku:") for d in drift))

    def test_drift_never_raises_on_fetch_failure(self):
        orig = report._fetch_pricing_page
        def boom(url=None, timeout=15): raise OSError("offline")
        report._fetch_pricing_page = boom
        try:
            self.assertEqual(report.check_pricing_drift(), [])   # swallowed, not raised
        finally:
            report._fetch_pricing_page = orig

    def test_drift_reads_through_rate_cards_claude_not_a_stale_pricing_ref(self):
        # Swap RATE_CARDS["claude"] for a distinct dict (not PRICING itself) and
        # confirm check_pricing_drift() picks up the swap. This proves the loop
        # reads RATE_CARDS["claude"] live rather than closing over the PRICING
        # name directly -- the page below still reflects the real PRICING rates,
        # so a swapped opus rate must surface as drift.
        orig_cards = report.RATE_CARDS
        swapped_claude = {"opus": (99.0, 199.0), "sonnet": (2.00, 10.00), "haiku": (1.00, 5.00)}
        report.RATE_CARDS = dict(orig_cards, claude=swapped_claude)
        try:
            drift = self._drift(self._page_from(report.PRICING))
        finally:
            report.RATE_CARDS = orig_cards
        self.assertTrue(any(d.startswith("opus:") for d in drift))

    def test_drift_never_looks_at_codex_tiers(self):
        # Give the codex card a colliding "opus" key priced wildly differently,
        # and salt the fixture page with a codex-looking row too. If the check
        # ever iterated RATE_CARDS as a whole (rather than RATE_CARDS["claude"]
        # specifically), this bogus codex "opus" entry -- or the codex row on
        # the page -- would leak into the drift comparison.
        orig_cards = report.RATE_CARDS
        report.RATE_CARDS = dict(orig_cards,
                                  codex={"opus": (999.0, 999.0), "gpt-5.3-codex": (1.75, 14.00)})
        page = self._page_from(report.PRICING) + \
            "| gpt-5.3-codex | $1.75 / MTok | $0 | $0 | $0.175 / MTok | $14 / MTok |\n"
        try:
            drift = self._drift(page)
        finally:
            report.RATE_CARDS = orig_cards
        self.assertEqual(drift, [])
        for d in drift:
            self.assertNotIn("gpt-5", d.lower())
            self.assertNotIn("codex", d.lower())

    def test_codex_no_drift_gap_is_documented_in_source(self):
        # Scenario 2 (E2-F4-S1 AC): the Codex no-drift gap must be documented as
        # a comment/docstring near check_pricing_drift(), not just implied by
        # behavior. Isolate the "pricing drift check" section of report.py and
        # grep it for the key phrases.
        #
        # As of E3-F3-S1 (module split), the schema & coverage monitoring
        # section that used to immediately follow the pricing-drift section
        # moved to traceyield/health.py, so the section's end boundary here is
        # the next top-level def (metrics_via_canonical) rather than that
        # (now relocated) header comment.
        src_path = os.path.join(os.path.dirname(os.path.abspath(report.__file__)), "report.py")
        src = open(src_path, encoding="utf-8").read()
        m = re.search(
            r"# -+ pricing drift check(.*?)\ndef metrics_via_canonical",
            src, re.S)
        self.assertIsNotNone(m, "could not locate the pricing drift check section in report.py")
        region = re.sub(r"\s+", " ", m.group(1).lower())
        self.assertIn("hand-maintained", region)
        self.assertIn("codex", region)
        self.assertIn("no automated drift alarm", region)


if __name__ == "__main__":
    unittest.main()
