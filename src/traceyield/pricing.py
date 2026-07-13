#!/usr/bin/env python3
"""Shared rate cards and cost math (E3-F2-S2).

Before this module existed, PRICING/CODEX_PRICING/CACHE/RATE_CARDS/tier()/
cost_of() all lived in report.py (reporting), yet canonical.py (ingestion)
needed tier() too and reached across the dependency boundary with
`from traceyield import report` to get it -- an ingestion module depending on
a reporting module purely to price/classify a token count is a reversed
dependency (see docs/decisions/0008-installable-src-layout-package.md's
Phase 2, "extract shared pricing/classification"). This module is the fix:
it sits BELOW both report.py and canonical.py, has no knowledge of either,
and both import it directly.

Pure by design: everything here is deterministic rate-table lookups and
arithmetic over caller-supplied numbers. No file I/O, no network, no
knowledge of transcripts, the filesystem, or traceyield.paths. That's a
deliberate scope boundary, not an oversight -- the network/file-I/O pricing
functions (parse_pricing_page, check_pricing_drift, _fetch_pricing_page,
record_pricing) stay in report.py because they are reporting-side concerns
(they read/write pricing_history.json via traceyield.paths and scrape a
public pricing page to power the dashboard's drift warning); ingestion has
no need for any of them, so keeping them out of this module keeps it
free of I/O and of a paths.py dependency. report.py re-exports every name
below (report.PRICING, report.tier, report.cost_of, ...) for backward
compatibility with existing call sites and tests -- see report.py's
"pricing/classification" section.

This is a pure extraction: every value and formula here is byte-for-byte
unchanged from what report.py computed before this module existed (same
PRICING/CODEX_PRICING rates, same CACHE multipliers, same tier keywords
including fable -> opus, same cost_of() math). Cost numbers are identical
before and after.
"""

# ---------------------------------------------------------------- rate cards
# Base per-1M-token rates. Edit when Anthropic pricing changes.
# Cache multipliers fixed by the API: read=0.1x, write-5m=1.25x, write-1h=2x.
# Cost across all history is computed at THESE (current) rates for apples-to-
# apples comparison; the pricing trend chart shows how rates themselves moved.
# These are the authoritative source of truth (hand-verified against the
# Anthropic pricing page); report.check_pricing_drift() re-verifies them each
# run and warns on mismatch — it never overwrites, since a bad scrape would
# retroactively distort every day's reported cost.
PRICING = {
    "opus":   (5.00, 25.00),
    "sonnet": (2.00, 10.00),   # Sonnet 5 intro pricing thru 2026-08-31 (std 3/15)
    "haiku":  (1.00,  5.00),
}
# Anthropic's published pricing page (Markdown). Anthropic exposes no pricing
# API — the Models API returns capabilities but no rates — so this doc page is
# the authoritative live source for report.check_pricing_drift().
PRICING_URL = "https://platform.claude.com/docs/en/docs/about-claude/pricing.md"
# Codex (OpenAI) per-1M-token rates for the tiers OpenAI currently prices.
# Hand-verified 2026-07-12 (checked twice) against OpenAI's published pricing
# page: https://developers.openai.com/api/docs/pricing -- same "authoritative,
# re-verified, never auto-overwritten" posture as PRICING above (Decision 0007
# D5). gpt-5 and gpt-5-codex are INTENTIONALLY OMITTED: they're no longer
# listed on OpenAI's pricing page, so they stay unpriced and count volume
# (tokens/msgs) at $0 rather than guessing a rate (Decision 0007 D3,
# "volume-always / dollars-when-priced").
CODEX_PRICING = {
    "gpt-5.3-codex": (1.75, 14.00),
    "gpt-5.5":       (5.00, 30.00),
    "gpt-5.5-pro":   (30.00, 180.00),
}
# Per-provider cache multipliers (fractions of that provider's input rate).
# Claude: read=0.1x, write-5m=1.25x, write-1h=2x (see PRICING comment above).
# Codex: read=0.1x only (verified 0.175/1.75 == 0.50/5.00 == 0.10 on OpenAI's
# pricing page, same date/source as CODEX_PRICING) -- OpenAI has no cache-write
# premium, so there are deliberately no w5m/w1h keys here; cost_of()'s
# `.get(..., 0.0)` defaults make Codex cache-write tokens contribute $0.
CACHE = {"claude": {"read": 0.10, "w5m": 1.25, "w1h": 2.0},
         "codex":  {"read": 0.10}}
# Per-provider rate card: provider -> tier -> (input, output) per 1M tokens.
# Claude's card references the existing flat PRICING dict so there's one
# source of truth -- this is NOT a replacement for PRICING, just a lookup
# layer around it so other providers can register their own tier maps.
RATE_CARDS = {"claude": PRICING, "codex": CODEX_PRICING}

def rate_card(provider, tier):
    """(input, output) per-1M rate for provider+tier, or None if either is unpriced."""
    return RATE_CARDS.get(provider, {}).get(tier)

def cost_of(provider, tier, inp, out, cr, w5m, w1h):
    """Cost in dollars for one turn's token counts, priced off RATE_CARDS/CACHE.
    Returns 0.0 for an unpriced (provider, tier) pair -- never raises."""
    card = rate_card(provider, tier)
    if card is None: return 0.0
    ri, ro = card
    mult = CACHE.get(provider, {})
    read = ri * mult.get("read", 0.0); w5 = ri * mult.get("w5m", 0.0); w1 = ri * mult.get("w1h", 0.0)
    return (inp*ri + out*ro + cr*read + w5m*w5 + w1h*w1) / 1e6

def cache_rates(inp, provider="claude"): return {k: inp*v for k, v in CACHE[provider].items()}

def tier(model):
    if not model: return None
    m = model.lower()
    if "opus" in m or "fable" in m: return "opus"
    if "sonnet" in m: return "sonnet"
    if "haiku" in m: return "haiku"
    return None
