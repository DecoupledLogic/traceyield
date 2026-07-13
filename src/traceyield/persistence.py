#!/usr/bin/env python3
"""
Durable JSON stores (file I/O): daily_metrics.json / session_metrics.json /
pricing_history.json / health.json. Kept apart from aggregation.py's pure
bucket math (E3-F3-S1) -- this module only ever reads/writes the on-disk
stores it's given a path for; it never derives a bucket itself.
"""
import datetime, json, os
from traceyield import paths, pricing

DAILY_FILE = paths.DAILY_FILE
SESSION_FILE = paths.SESSION_FILE
PRICING_FILE = paths.PRICING_FILE
HEALTH_FILE = paths.HEALTH_FILE
RATE_CARDS = pricing.RATE_CARDS


def merge_daily(newdays, path=DAILY_FILE):
    old = {}
    if os.path.exists(path):
        try: old = json.load(open(path, encoding="utf-8"))
        except: old = {}
    old.update(newdays)  # new authoritative per date; keeps rotated-out dates
    json.dump(old, open(path, "w", encoding="utf-8"), indent=0)
    return old
def merge_sessions(newsess, path=SESSION_FILE):
    # Same merge philosophy as days: a session lives in one transcript, so a
    # re-parse is fully authoritative for that id; sessions whose transcripts
    # have rotated away are preserved from the prior store.
    old = {}
    if os.path.exists(path):
        try: old = json.load(open(path, encoding="utf-8"))
        except: old = {}
    old.update(newsess)
    json.dump(old, open(path, "w", encoding="utf-8"), indent=0)
    return old
def _norm_pricing_entry(entry):
    """Upgrade a pricing_history.json date-entry to the provider-nested shape
    {provider: {tier: {input, output}}}, tolerating the legacy flat shape
    {tier: {input, output}} written before this snapshot carried a provider
    dimension (Decision 0007 D7). An entry is legacy-flat iff any of its
    values is itself a dict containing an "input" key -- a nested entry's
    values are provider->tier maps, which never have "input" at that level.
    Already-nested entries pass through unchanged (idempotent); numeric
    values are never altered, only re-nested."""
    if any(isinstance(v, dict) and "input" in v for v in entry.values()):
        return {"claude": entry}
    return entry

def record_pricing(path=PRICING_FILE):
    hist = {}
    if os.path.exists(path):
        try: hist = json.load(open(path, encoding="utf-8"))
        except: hist = {}
    hist = {d: _norm_pricing_entry(e) for d, e in hist.items()}
    hist[datetime.date.today().isoformat()] = {
        provider: {m: {"input": r[0], "output": r[1]} for m, r in card.items()}
        for provider, card in RATE_CARDS.items()
    }
    json.dump(hist, open(path, "w", encoding="utf-8"), indent=2)
    return hist

def write_health(health, path=HEALTH_FILE):
    json.dump(health, open(path, "w", encoding="utf-8"), indent=2)
    return health
