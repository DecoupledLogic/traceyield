#!/usr/bin/env python3
"""
Claude Code usage analytics — daily time-series edition.

Every transcript line is timestamped, so one run reconstructs the FULL history
bucketed by activity date. Aggregation into day / week / month views happens
client-side in report.html, so you can step through any period and see trends.

Each run:
  1. Ingests every transcript under CLAUDE_PROJECTS (and ~/.codex) into the
     canonical usage.db (canonical.py), then derives day/session metrics from
     it via aggregate() -- SQL GROUP BY over the provider-neutral turn/tool
     store is the source of truth. Falls back to the legacy direct-parse
     analyze() if the canonical path fails for any reason (never breaks a run).
     See docs/decisions/0001-aggregate-flip.md.
  2. Merges into daily_metrics.json (new data authoritative per date; older
     dates kept even if their transcripts get rotated away).
  3. Records today's model pricing in pricing_history.json.
  4. Regenerates report.html (self-contained interactive app; no dependencies).
  5. Checks PRICING against Anthropic's published rates and warns on drift
     (best-effort; never overwrites PRICING or fails the run).

Run daily:  python report.py       (wire to a scheduled task; see README note)
"""
import json, os, datetime, re, sys, urllib.request
from traceyield import classification, paths, pricing, transcripts
from traceyield import aggregation, persistence, health, html

# ---------------------------------------------------------------- config
# Every writable location and env-driven config value is now centralized in
# traceyield.paths (E3-F2-S1); this module just re-exports the names call
# sites (and existing tests) already depend on. See paths.py for the
# rationale (repo-root anchoring, per-machine namespacing, env overrides)
# and tests/test_paths.py for the guard that keeps it that way.
HERE = paths.HERE
CLAUDE_PROJECTS = paths.CLAUDE_PROJECTS
CODEX_SESSIONS = paths.CODEX_SESSIONS
machine_id = paths.machine_id
MACHINES_DIR = paths.MACHINES_DIR
MACHINE_DIR = paths.MACHINE_DIR
DAILY_FILE = paths.DAILY_FILE
SESSION_FILE = paths.SESSION_FILE
OUT_HTML = paths.OUT_HTML
HEALTH_FILE = paths.HEALTH_FILE
PRICING_FILE = paths.PRICING_FILE

# Rate cards, cost math, and tier() are centralized in traceyield.pricing
# (E3-F2-S2) -- both report.py (here) and canonical.py consume that shared,
# dependency-free module directly instead of one reaching into the other.
# This module just re-exports the names call sites (and existing tests)
# already depend on; see pricing.py for the values/formulas and the
# rationale for keeping the I/O-bound pricing functions (parse_pricing_page,
# check_pricing_drift, _fetch_pricing_page, record_pricing, below) here in
# report.py rather than in pricing.py.
PRICING = pricing.PRICING
PRICING_URL = pricing.PRICING_URL
CODEX_PRICING = pricing.CODEX_PRICING
CACHE = pricing.CACHE
RATE_CARDS = pricing.RATE_CARDS
rate_card = pricing.rate_card
cost_of = pricing.cost_of
cache_rates = pricing.cache_rates
tier = pricing.tier

# Tool-error taxonomy is centralized in traceyield.classification (E3-F2-S2)
# for the same reason as pricing above; re-exported here for backward compat.
ERROR_RULES = classification.ERROR_RULES
classify = classification.classify
ERROR_META = classification.ERROR_META

# ---------------------------------------------------------------- parse
# project_of()/result_text() are shared by both layers -- this module's own
# analyze()/aggregate() below, AND canonical.py's ClaudeProvider (ingestion)
# -- so they live in traceyield.transcripts (E3-F2-S3), the neutral module
# BELOW both, rather than being defined here and reached into from
# canonical.py (the last of that reverse ingestion->reporting coupling; see
# traceyield.transcripts' module docstring). Re-exported here for backward
# compatibility with existing call sites/tests.
project_of = transcripts.project_of
result_text = transcripts.result_text
# ---------------------------------------------------------------- re-exports
# The bucket math / analyze() / aggregate() (E3-F3-S1) now live in
# traceyield.aggregation; durable JSON stores in traceyield.persistence;
# schema-drift + coverage monitoring in traceyield.health; the HTML template +
# build_html() in traceyield.html. Every name below is re-exported AS THE SAME
# OBJECT (not a copy) so existing call sites/tests referencing report.X keep
# working unchanged.
new_tool = aggregation.new_tool
new_model = aggregation.new_model
new_day = aggregation.new_day
new_session = aggregation.new_session
analyze = aggregation.analyze
aggregate = aggregation.aggregate
_empty_bucket = aggregation._empty_bucket
top_sessions = aggregation.top_sessions

merge_daily = persistence.merge_daily
merge_sessions = persistence.merge_sessions
_norm_pricing_entry = persistence._norm_pricing_entry
record_pricing = persistence.record_pricing
write_health = persistence.write_health

SCHEMA_EXPECT = health.SCHEMA_EXPECT
DRIFT_CATS = health.DRIFT_CATS
_fp = health._fp
scan_claude = health.scan_claude
scan_codex = health.scan_codex
schema_drift = health.schema_drift
coverage = health.coverage
build_health = health.build_health
_slim_health = health._slim_health
print_health = health.print_health

HTML_TMPL = html.HTML_TMPL
build_html = html.build_html

# ------------------------------------------------------- pricing drift check
def _fetch_pricing_page(url=PRICING_URL, timeout=15):
    """Fetch the Anthropic pricing page as text. Raises on any network error."""
    req = urllib.request.Request(url, headers={"User-Agent": "traceyield-pricing-check"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")

def parse_pricing_page(md):
    """Scrape {tier: (input, output)} from the page's 'Model pricing' table.

    Columns are: Model | Base Input | 5m write | 1h write | cache hit | Output,
    so input is cell 1 and output is cell 5. Scoped to the Model pricing section
    only — the Batch and Fast-mode tables below it list the same tiers at other
    rates. For each tier we take the first non-deprecated/-retired row whose
    model name contains the tier keyword (the current flagship for that tier).
    """
    m = re.search(r"##\s*Model pricing(.*?)(?:\n##\s|\Z)", md, re.S)
    if not m: return {}
    out = {}
    for ln in m.group(1).splitlines():
        ln = ln.strip()
        if not ln.startswith("|"): continue
        cells = [c.strip() for c in ln.strip("|").split("|")]
        if len(cells) < 6: continue
        name = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", cells[0]).lower()  # drop md links
        if "deprecated" in name or "retired" in name: continue
        ip, op = re.search(r"\$([0-9.]+)", cells[1]), re.search(r"\$([0-9.]+)", cells[5])
        if not (ip and op): continue          # skips header / separator rows (no $)
        for t in ("opus", "sonnet", "haiku"):
            if t in name and t not in out:
                out[t] = (float(ip.group(1)), float(op.group(1)))
    return out

def check_pricing_drift(url=PRICING_URL):
    """Warn (to stdout) if PRICING has drifted from Anthropic's published rates.

    Best-effort and non-authoritative: it never raises and never mutates
    PRICING. On any failure (offline, page moved, layout changed) it prints a
    'skipped' note and returns []. Returns a list of human-readable drift lines.

    Claude-only by design (Decision 0007 D5): this only ever compares
    RATE_CARDS["claude"] (== PRICING) against the scraped Anthropic page.
    Codex/OpenAI pricing (CODEX_PRICING) is hand-maintained and has NO
    automated drift alarm -- Anthropic publishes a scrapeable pricing doc we
    diff against here, OpenAI does not, so Codex rates are verified by hand
    (see the CODEX_PRICING comment) each time that dict is edited.
    """
    try:
        published = parse_pricing_page(_fetch_pricing_page(url))
    except Exception as e:
        print(f"  pricing drift check skipped ({type(e).__name__}: {e})")
        return []
    if not published:
        print("  pricing drift check skipped (could not parse pricing page)")
        return []
    drift = []
    for tier, (ci, co) in RATE_CARDS["claude"].items():   # Claude only -- see docstring
        pub = published.get(tier)
        if pub is None:
            drift.append(f"{tier}: not found on pricing page")
        elif (ci, co) != pub:
            drift.append(f"{tier}: PRICING={ci}/{co} vs Anthropic={pub[0]}/{pub[1]} per 1M")
    if drift:
        print("  WARNING: PRICING drift vs Anthropic pricing page -- update PRICING in report.py:")
        for d in drift: print(f"      {d}")
    else:
        print(f"  pricing verified against Anthropic pricing page ({len(published)} tiers match)")
    return drift

def metrics_via_canonical():
    """Live production path: ingest the canonical turn/tool/segment store
    (canonical.py) and derive (days, sessions) from it via aggregate() -- SQLite
    is the source of truth for the aggregates, not a parallel transcript walk.

    Imported lazily to avoid an import cycle (canonical imports report). Raises
    on any failure (ingest or aggregate) so main() can fall back to analyze();
    it never partially commits -- merge_daily/merge_sessions only run on the
    result this returns. See docs/canonical-data-model.md and
    docs/decisions/0001-aggregate-flip.md."""
    from traceyield import canonical
    db = canonical.open_db()
    files, recs = canonical.ingest(db)
    print(f"Canonical store: {canonical.DB_FILE} (capture={canonical.CAPTURE}) "
          f"| {files} files -> {recs} records")
    try:
        newdays, newsess = aggregate(db)
    finally:
        db.close()
    return newdays, newsess

def main():
    os.makedirs(MACHINE_DIR, exist_ok=True)   # machines/<machine_id>/
    try:
        newdays, newsess = metrics_via_canonical()
    except Exception as e:
        print(f"Aggregate-from-db failed ({type(e).__name__}: {e}); fell back to analyze()")
        newdays, newsess = analyze()
    days = merge_daily(newdays)
    sessions = merge_sessions(newsess)
    pricing_hist = record_pricing()
    # Fingerprint both providers' data shapes and reconcile coverage, then embed
    # the health record in the report and persist it (best-effort; never fatal).
    health = build_health(days, scan_claude(), scan_codex())
    write_health(health)
    open(OUT_HTML, "w", encoding="utf-8").write(build_html(days, sessions, pricing_hist, health))
    tc = sum(d["cost"] for d in days.values()); tm = sum(d["msgs"] for d in days.values())
    te = sum(d["tool_errors"] for d in days.values()); tr = sum(d["tool_results"] for d in days.values())
    ds = sorted(days)
    span = f"({ds[0]}..{ds[-1]})" if ds else "(none)"
    print(f"Machine: {machine_id()} -> {MACHINE_DIR}")
    print(f"{len(days)} active days {span} | ${tc:,.2f} | {tm:,} turns | "
          f"{te}/{tr} tool errors ({te/max(tr,1)*100:.1f}%)")
    if sessions:
        top = max(sessions.values(), key=lambda s: s["cost"])
        print(f"{len(sessions):,} sessions | priciest ${top['cost']:,.2f} ({top.get('project','?')})")
    print(f"Report: {OUT_HTML}")
    print_health(health)    # schema drift + data holes (into run.log)
    check_pricing_drift()   # best-effort; report is already written above


if __name__ == "__main__":
    # `--machine-dir` prints the resolved per-machine directory and exits, so the
    # run.cmd wrapper can target its run.log there without re-implementing the
    # hostname sanitization in batch.
    if "--machine-dir" in sys.argv:
        print(MACHINE_DIR)
    else:
        main()
