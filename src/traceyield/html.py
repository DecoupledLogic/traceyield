#!/usr/bin/env python3
"""
Presentation: the HTML_TMPL template and build_html(), which inlines the run
payload into it via the __PAYLOAD__ / __PRICEROWS__ markers. No file I/O of
its own -- report.main() writes the returned string to OUT_HTML.

E3-F3-S2: HTML_TMPL is no longer an inline raw-string literal. It is a
packaged resource (traceyield/resources/report.html), loaded once at import
time via importlib.resources so it works identically from an editable
install and a built wheel.
"""
import datetime, importlib.resources, json
from traceyield import classification, pricing
from traceyield.aggregation import top_sessions
from traceyield.health import _slim_health

PRICING = pricing.PRICING
cache_rates = pricing.cache_rates
ERROR_META = classification.ERROR_META


def _load_template():
    """Load the HTML dashboard template from the packaged resource
    (traceyield/resources/report.html). Works from both an editable
    install and a built wheel via importlib.resources."""
    return importlib.resources.files("traceyield.resources").joinpath("report.html").read_text(encoding="utf-8")

def build_html(days, sessions, pricing_hist, health=None):
    price_rows = []
    for mdl in ("opus","sonnet","haiku"):
        i,o = PRICING[mdl]; cr = cache_rates(i)
        price_rows.append(f"<tr><td>{mdl}</td><td class='num'>${i:.2f}</td><td class='num'>${o:.2f}</td>"
                          f"<td class='num'>${cr['read']:.2f}</td><td class='num'>${cr['w5m']:.2f}</td><td class='num'>${cr['w1h']:.2f}</td></tr>")
    payload = json.dumps({
        "generated": datetime.datetime.now().isoformat(timespec="seconds"),
        "days": days,
        "sessions": top_sessions(sessions),
        "pricing": {m:{"input":r[0],"output":r[1]} for m,r in PRICING.items()},
        "meta": ERROR_META,
        "pricing_history": pricing_hist,
        "health": _slim_health(health),
    })
    tmpl = HTML_TMPL
    tmpl = tmpl.replace("__PAYLOAD__", payload)
    tmpl = tmpl.replace("__PRICEROWS__", "".join(price_rows))
    return tmpl

# ---------------------------------------------------------------- template
# Loaded once at import time from the packaged resource (see _load_template
# above); not re-read per build_html() call.
HTML_TMPL = _load_template()
