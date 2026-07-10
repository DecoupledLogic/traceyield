#!/usr/bin/env python3
"""
Claude Code usage analytics — daily time-series edition.

Every transcript line is timestamped, so one run reconstructs the FULL history
bucketed by activity date. Aggregation into day / week / month views happens
client-side in report.html, so you can step through any period and see trends.

Each run:
  1. Parses every transcript under CLAUDE_PROJECTS, bucketing metrics by the
     UTC date of each message/tool-result.
  2. Merges into daily_metrics.json (new data authoritative per date; older
     dates kept even if their transcripts get rotated away).
  3. Records today's model pricing in pricing_history.json.
  4. Regenerates report.html (self-contained interactive app; no dependencies).

Run daily:  python report.py       (wire to a scheduled task; see README note)
"""
import json, os, glob, html, datetime
from collections import defaultdict, Counter

# ---------------------------------------------------------------- config
HERE = os.path.dirname(os.path.abspath(__file__))
CLAUDE_PROJECTS = os.path.expanduser(r"~/.claude/projects")
DAILY_FILE = os.path.join(HERE, "daily_metrics.json")
PRICING_FILE = os.path.join(HERE, "pricing_history.json")
OUT_HTML = os.path.join(HERE, "report.html")

# Base per-1M-token rates. Edit when Anthropic pricing changes.
# Cache multipliers fixed by the API: read=0.1x, write-5m=1.25x, write-1h=2x.
# Cost across all history is computed at THESE (current) rates for apples-to-
# apples comparison; the pricing trend chart shows how rates themselves moved.
PRICING = {
    "opus":   (5.00, 25.00),
    "sonnet": (2.00, 10.00),   # Sonnet 5 intro pricing thru 2026-08-31 (std 3/15)
    "haiku":  (1.00,  5.00),
}
def cache_rates(inp): return dict(read=inp*0.10, w5m=inp*1.25, w1h=inp*2.0)
def tier(model):
    if not model: return None
    m = model.lower()
    if "opus" in m or "fable" in m: return "opus"
    if "sonnet" in m: return "sonnet"
    if "haiku" in m: return "haiku"
    return None

# ---------------------------------------------------------------- error taxonomy
ERROR_RULES = [
    ("read_before_write", ["file has not been read yet"], "Write/Edit before Read",
     "Read a file before editing it — the harness tracks read state and rejects edits to unread files."),
    ("stale_edit", ["file has been modified since read"], "Edit on stale file",
     "Re-Read the file right before editing when a formatter/other edit/external process may have changed it."),
    ("edit_no_match", ["string to replace not found","old_string","not unique","no replacement was performed"],
     "Edit string didn't match",
     "old_string must match byte-for-byte and be unique. Add surrounding context, or use replace_all."),
    ("shell_cmd_not_found", ["command not found","exit code 127"], "Command not found (shell mismatch)",
     "Windows: ls/python/etc aren't on the Bash PATH. Use the PowerShell tool for Windows commands."),
    ("shell_syntax", ["unexpected eof","syntax error near","eval: line","unexpected token"],
     "Shell quoting / path-escaping error",
     "Windows backslash paths break Bash quoting. Prefer the PowerShell tool, or forward slashes / single quotes."),
    ("user_rejected", ["user doesn't want to proceed","was rejected","permission for this action was denied","haven't granted"],
     "User rejected / permission denied",
     "Recurrent denials suggest an allowlist entry (settings.json) or a different approach for that action."),
    ("is_directory", ["eisdir","illegal operation on a directory","is a directory","directory does not exist"],
     "Treated a directory as a file", "Confirm the path is a file (Glob/LS) before Read/Write."),
    ("file_not_found", ["no such file","does not exist","cannot access","cannot find","not found"],
     "File / path not found", "Verify paths (Glob first); often a wrong relative path or an assumed file."),
    ("blocked_dangerous", ["remove-item on system path","on system path '/'"],
     "Blocked dangerous operation", "A destructive command hit a guard. Scope paths explicitly; avoid roots."),
    ("input_validation", ["inputvalidationerror"], "Tool input validation error",
     "Often a deferred/MCP tool called before its schema loaded via ToolSearch, or a bad parameter."),
    ("json_field", ["unknown json field"], "Unknown JSON field", "Stale/misspelled payload field — check current schema."),
    ("git_error", ["fatal:","exit code 128"], "Git error", "Bad ref / not a repo / cannot change dir — check repo state first."),
]
def classify(text):
    low = text.lower()
    for name, subs, _, _ in ERROR_RULES:
        if any(s in low for s in subs): return name
    return "other"
ERROR_META = {n: {"title": t, "fix": f} for n, _, t, f in ERROR_RULES}
ERROR_META["other"] = {"title": "Other / uncategorized", "fix": "Review examples; add a rule to ERROR_RULES if a pattern recurs."}

# ---------------------------------------------------------------- parse
def project_of(path):
    return os.path.relpath(path, CLAUDE_PROJECTS).split(os.sep)[0]
def result_text(b):
    c = b.get("content")
    if isinstance(c, str): return c
    if isinstance(c, list): return " ".join(x.get("text","") for x in c if isinstance(x, dict))
    return ""
def new_tool(): return {"calls":0,"out":0,"cost":0.0,"err":0}
def new_day():
    return {"cost":0.0,
            "tok":{"input":0,"output":0,"cache_read":0,"cache_write_5m":0,"cache_write_1h":0},
            "msgs":0,"tool_results":0,"tool_errors":0,
            "sids":set(), "by_model":defaultdict(lambda:{"cost":0.0,"cache_read":0,"output":0}),
            "by_project":defaultdict(lambda:{"cost":0.0,"msgs":0}),
            "by_tool":defaultdict(new_tool), "errors":Counter()}

def analyze():
    files = glob.glob(os.path.join(CLAUDE_PROJECTS, "**", "*.jsonl"), recursive=True)
    days = defaultdict(new_day)
    for f in files:
        proj = project_of(f)
        idname = {}
        try:
            for line in open(f, encoding="utf-8"):
                line = line.strip()
                if not line: continue
                try: o = json.loads(line)
                except: continue
                ts = o.get("timestamp")
                if not ts: continue
                d = ts[:10]
                m = o.get("message")
                if not isinstance(m, dict): continue
                D = days[d]
                sid = o.get("sessionId")
                if sid: D["sids"].add(sid)
                turn_tools = []
                content = m.get("content")
                if isinstance(content, list):
                    for b in content:
                        if not isinstance(b, dict): continue
                        t = b.get("type")
                        if t == "tool_use":
                            nm = b.get("name","?")
                            turn_tools.append(nm)
                            D["by_tool"][nm]["calls"] += 1
                            idname[b.get("id")] = nm
                        elif t == "tool_result":
                            D["tool_results"] += 1
                            if b.get("is_error"):
                                D["tool_errors"] += 1
                                D["errors"][classify(result_text(b))] += 1
                                enm = idname.get(b.get("tool_use_id"))
                                if enm: D["by_tool"][enm]["err"] += 1
                u = m.get("usage")
                if not isinstance(u, dict): continue
                tr = tier(m.get("model"))
                if tr is None: continue
                inp=u.get("input_tokens",0) or 0; out=u.get("output_tokens",0) or 0
                cr=u.get("cache_read_input_tokens",0) or 0; cc=u.get("cache_creation_input_tokens",0) or 0
                det=u.get("cache_creation") or {}
                w1h=det.get("ephemeral_1h_input_tokens",0) or 0; w5m=det.get("ephemeral_5m_input_tokens",0) or 0
                if w1h+w5m==0 and cc>0: w5m=cc
                ri,ro=PRICING[tr]; crate=cache_rates(ri)
                cost=(inp*ri+out*ro+cr*crate["read"]+w5m*crate["w5m"]+w1h*crate["w1h"])/1e6
                D["cost"]+=cost; D["msgs"]+=1
                tk=D["tok"]; tk["input"]+=inp; tk["output"]+=out; tk["cache_read"]+=cr
                tk["cache_write_5m"]+=w5m; tk["cache_write_1h"]+=w1h
                bm=D["by_model"][tr]; bm["cost"]+=cost; bm["cache_read"]+=cr; bm["output"]+=out
                bp=D["by_project"][proj]; bp["cost"]+=cost; bp["msgs"]+=1
                # tool calls serialize (~100% single-tool turns) → attribute the
                # whole turn's cost+output to its one tool; else a pseudo-row.
                tkey = turn_tools[0] if len(turn_tools)==1 else ("(final response)" if not turn_tools else "(multi-tool turn)")
                bt=D["by_tool"][tkey]; bt["cost"]+=cost; bt["out"]+=out
        except: continue
    # serialize (sets->counts kept as list length; round cost)
    out = {}
    for d, D in days.items():
        out[d] = {
            "cost": round(D["cost"], 4),
            "tok": D["tok"],
            "msgs": D["msgs"], "tool_results": D["tool_results"], "tool_errors": D["tool_errors"],
            "sessions": len(D["sids"]),
            "by_model": {m:{"cost":round(v["cost"],4),"cache_read":v["cache_read"],"output":v["output"]} for m,v in D["by_model"].items()},
            "by_project": {p:{"cost":round(v["cost"],4),"msgs":v["msgs"]} for p,v in D["by_project"].items()},
            "by_tool": {t:{"calls":v["calls"],"out":v["out"],"cost":round(v["cost"],4),"err":v["err"]} for t,v in D["by_tool"].items()},
            "errors": dict(D["errors"]),
        }
    return out

# ---------------------------------------------------------------- persist
def merge_daily(newdays):
    old = {}
    if os.path.exists(DAILY_FILE):
        try: old = json.load(open(DAILY_FILE, encoding="utf-8"))
        except: old = {}
    old.update(newdays)  # new authoritative per date; keeps rotated-out dates
    json.dump(old, open(DAILY_FILE, "w", encoding="utf-8"), indent=0)
    return old
def record_pricing():
    hist = {}
    if os.path.exists(PRICING_FILE):
        try: hist = json.load(open(PRICING_FILE, encoding="utf-8"))
        except: hist = {}
    hist[datetime.date.today().isoformat()] = {m:{"input":r[0],"output":r[1]} for m,r in PRICING.items()}
    json.dump(hist, open(PRICING_FILE, "w", encoding="utf-8"), indent=2)
    return hist

# ---------------------------------------------------------------- html
def build_html(days, pricing_hist):
    price_rows = []
    for mdl in ("opus","sonnet","haiku"):
        i,o = PRICING[mdl]; cr = cache_rates(i)
        price_rows.append(f"<tr><td>{mdl}</td><td class='num'>${i:.2f}</td><td class='num'>${o:.2f}</td>"
                          f"<td class='num'>${cr['read']:.2f}</td><td class='num'>${cr['w5m']:.2f}</td><td class='num'>${cr['w1h']:.2f}</td></tr>")
    payload = json.dumps({
        "generated": datetime.datetime.now().isoformat(timespec="seconds"),
        "days": days,
        "meta": ERROR_META,
        "pricing_history": pricing_hist,
    })
    tmpl = HTML_TMPL
    tmpl = tmpl.replace("__PAYLOAD__", payload)
    tmpl = tmpl.replace("__PRICEROWS__", "".join(price_rows))
    return tmpl

def main():
    newdays = analyze()
    days = merge_daily(newdays)
    pricing_hist = record_pricing()
    open(OUT_HTML, "w", encoding="utf-8").write(build_html(days, pricing_hist))
    tc = sum(d["cost"] for d in days.values()); tm = sum(d["msgs"] for d in days.values())
    te = sum(d["tool_errors"] for d in days.values()); tr = sum(d["tool_results"] for d in days.values())
    ds = sorted(days)
    print(f"{len(days)} active days ({ds[0]}..{ds[-1]}) | ${tc:,.2f} | {tm:,} turns | "
          f"{te}/{tr} tool errors ({te/max(tr,1)*100:.1f}%)")
    print(f"Report: {OUT_HTML}")

# ---------------------------------------------------------------- template
HTML_TMPL = r"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Claude Code Usage Report</title>
<style>
:root{--bg:#0f1117;--panel:#171a23;--panel2:#1e222e;--ink:#e7e9ee;--mut:#8b90a0;--line:#2a2f3d;--accent:#6b8afd;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:960px;margin:0 auto;padding:28px 20px 90px}
h1{font-size:25px;margin:0 0 4px} h2{font-size:17px;margin:30px 0 10px;border-bottom:1px solid var(--line);padding-bottom:8px}
.sub{color:var(--mut);font-size:13px;margin-bottom:20px}
.controls{display:flex;flex-wrap:wrap;gap:10px;align-items:center;background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px 14px;position:sticky;top:0;z-index:5}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:9px;overflow:hidden}
.seg button{background:transparent;color:var(--mut);border:0;padding:7px 14px;font-size:13px;cursor:pointer}
.seg button.on{background:var(--accent);color:#fff}
select{background:var(--panel2);color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:7px 10px;font-size:13px}
.stepper{display:inline-flex;align-items:center;gap:8px;margin-left:auto}
.stepper button{background:var(--panel2);color:var(--ink);border:1px solid var(--line);border-radius:8px;width:34px;height:34px;font-size:16px;cursor:pointer}
.stepper button:disabled{opacity:.35;cursor:default} .pname{font-weight:600;min-width:150px;text-align:center}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-top:14px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:15px}
.klabel{color:var(--mut);font-size:11px;text-transform:uppercase;letter-spacing:.04em}
.kval{font-size:24px;font-weight:650;margin-top:5px} .delta{font-size:12px;margin-top:5px}
.delta.up{color:#f0a35e} .delta.down{color:#5bbf8a} .delta.flat{color:var(--mut)}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px;margin-top:12px;overflow-x:auto}
.chart{width:100%;height:auto} .grid{stroke:var(--line);stroke-width:1}
.ytick{fill:var(--mut);font-size:10px;text-anchor:end} .xtick{fill:var(--mut);font-size:10px;text-anchor:middle}
.dot{cursor:pointer} .legend{margin-top:8px;display:flex;flex-wrap:wrap;gap:14px}
.leg{font-size:12px;color:var(--mut);display:flex;align-items:center;gap:6px} .leg i{width:10px;height:10px;border-radius:2px;display:inline-block}
.hbars{display:flex;flex-direction:column;gap:7px}
.hbar{display:grid;grid-template-columns:160px 1fr 78px;align-items:center;gap:10px;font-size:13px}
.hlabel{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.htrack{background:var(--panel2);border-radius:5px;height:15px;overflow:hidden}
.hfill{display:block;height:100%;border-radius:5px} .hval{color:var(--mut);text-align:right;font-variant-numeric:tabular-nums}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--mut);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.03em}
td.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.muted{color:var(--mut)} .mono{font-family:ui-monospace,Consolas,monospace;font-size:11px}
.two{display:grid;grid-template-columns:1fr 1fr;gap:12px} @media(max-width:680px){.two{grid-template-columns:1fr}.hbar{grid-template-columns:110px 1fr 64px}}
.foot{color:var(--mut);font-size:12px;margin-top:36px;border-top:1px solid var(--line);padding-top:16px}
.hint{color:var(--mut);font-size:12px}
input[type=number]{background:var(--panel2);color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:3px 6px;width:56px;font-size:12px}
.doc h3{font-size:15px;margin:18px 0 8px;color:var(--ink)} .doc h3:first-child{margin-top:0}
.doc dl{margin:0} .doc dt{font-weight:600;margin-top:11px} .doc dd{margin:2px 0 0;color:var(--mut)}
.doc dt .mono{color:var(--accent);margin-left:4px} .doc ul{margin:6px 0 0;padding-left:20px} .doc li{margin:7px 0}
.doc p{margin:8px 0}
</style></head><body><div class="wrap">
<h1>Claude Code — Usage &amp; Health</h1>
<div class="sub" id="sub"></div>

<div class="controls">
  <div class="seg" id="gran">
    <button data-g="day" class="on">Day</button><button data-g="week">Week</button><button data-g="month">Month</button>
  </div>
  <label class="hint">Trend metric&nbsp;
    <select id="metric">
      <option value="cost">Cost ($)</option>
      <option value="tokens">Total tokens</option>
      <option value="turns">Assistant turns</option>
      <option value="errrate">Tool error rate (%)</option>
      <option value="errors">Tool errors (count)</option>
      <option value="sessions">Sessions</option>
    </select>
  </label>
  <div class="stepper">
    <button id="prev" title="Previous (←)">‹</button>
    <span class="pname" id="pname"></span>
    <button id="next" title="Next (→)">›</button>
  </div>
</div>

<div class="cards" id="cards"></div>

<h2>Trend <span class="hint" id="trendhint"></span></h2>
<div class="panel" id="trend"></div>

<h2>Selected period — breakdown</h2>
<div class="two">
  <div class="panel"><div class="muted" style="margin-bottom:10px">Cost by project</div><div id="byproj"></div></div>
  <div class="panel"><div class="muted" style="margin-bottom:10px">Cost by model tier</div><div id="bymodel"></div></div>
</div>
<div class="panel"><div class="muted" style="margin-bottom:10px">Token composition</div><div id="comp"></div></div>
<div class="panel"><div class="muted" style="margin-bottom:10px">Tool usage (calls)</div><div id="tools"></div></div>

<h2>Tokens &amp; cost per tool <span class="hint" id="waste"></span></h2>
<div class="panel">
<div class="hint" style="margin-bottom:8px">Full turn cost attributed to its single tool — Claude Code serializes tool calls, so this is <b>exact</b> for tool turns (input/cache can't be split further until a custom harness logs per-tool usage). <b>Est. waste</b> = errors &times; avg cost/call &times; retry factor <input id="retry" type="number" value="1" min="0" step="0.5"> (assumed extra turns per error).</div>
<table><thead><tr><th>Tool</th><th class="num">Calls</th><th class="num">Output tok</th><th class="num">Cost</th><th class="num">Errors</th><th class="num">Err rate</th><th class="num">Est. waste</th></tr></thead><tbody id="tooltbody"></tbody></table>
</div>

<h2>Selected period — errors &amp; fixes</h2>
<div class="panel"><table><thead><tr><th>Pattern</th><th class="num">Count</th><th class="num">Share</th><th>Suggested fix</th></tr></thead><tbody id="errtbody"></tbody></table></div>

<h2>Model pricing (tracked daily)</h2>
<div class="panel"><div class="muted" style="margin-bottom:6px">Input price $/1M over time</div><div id="pricechart"></div></div>
<div class="panel"><table>
<thead><tr><th>Model tier</th><th class="num">Input</th><th class="num">Output</th><th class="num">Cache read</th><th class="num">Write 5m</th><th class="num">Write 1h</th></tr></thead>
<tbody>__PRICEROWS__</tbody></table>
<div class="muted" style="margin-top:8px">Per 1M tokens. Cache read = 0.1&times; input, write-5m = 1.25&times;, write-1h = 2&times;. All-history cost is computed at current rates for comparability; this chart shows how the rates themselves moved. Edit <span class="mono">PRICING</span> in report.py when Anthropic changes prices.</div></div>

<h2>How to read this report</h2>
<div class="panel doc">
<h3>Where the money goes — the five token line-items</h3>
<p>Each request re-sends the whole conversation so far. To avoid re-billing all of it at full price, the API <b>caches</b> a stable prefix of the prompt. Your tokens split into five lines, each a multiple of the model's base <b>input</b> price:</p>
<dl>
<dt>Fresh input <span class="mono">1&times;</span></dt><dd>Brand-new tokens the model reads for the first time (a newly opened file, your latest message). Full input price.</dd>
<dt>Cache write &mdash; 5&nbsp;min <span class="mono">1.25&times;</span></dt><dd>Storing a chunk in the cache the first time costs a 25% premium. It stays reusable for 5 minutes.</dd>
<dt>Cache write &mdash; 1&nbsp;hour <span class="mono">2&times;</span></dt><dd>Same, kept for an hour &mdash; double the input price to write. Claude Code uses 1h caching, which is why this line is large.</dd>
<dt>Cache read <span class="mono">0.1&times;</span></dt><dd>Reading tokens already in the cache costs a tenth of input price. This is the payoff of caching &mdash; but because your ~154K-token context is re-read on <em>every</em> turn, it&rsquo;s still your single biggest cost line by volume.</dd>
<dt>Output <span class="mono">output rate</span></dt><dd>Tokens the model generates (its reply + tool calls), billed at the separate, higher output price (Opus $25/1M).</dd>
</dl>
<p class="muted">A cached token you reuse costs ~8% of writing it fresh at 1h TTL (0.1 vs 1.25), so caching pays off after ~2 reuses. The risk is <b>invalidation</b>: editing a file or changing tools near the front of the prompt forces an expensive re-write of everything after it.</p>
<h3>How to use it to improve</h3>
<ul>
<li><b>Right-size the model.</b> ~98% of spend is Opus. Route routine work (reading, simple edits, exploration) to Sonnet with <span class="mono">/model</span> &mdash; cheaper input and 0.1&times; cache reads at $0.20 vs $0.50.</li>
<li><b>Keep context small.</b> Cost &asymp; context size &times; turns; the ~154K/turn re-read is the engine. <span class="mono">/clear</span> between tasks so each turn re-reads less.</li>
<li><b>Cut errors = cut wasted turns.</b> Each tool error &asymp; one extra turn that re-reads the full context. The per-tool panel&rsquo;s <b>Est. waste</b> column puts a dollar figure on it. Top offenders: Windows shell errors (use PowerShell for Windows commands) and Write/Edit-before-Read (Read first).</li>
<li><b>Watch the trend, not the day.</b> Switch to Week/Month and step with &larr;/&rarr; to see whether cost-per-turn and error rate improve after a change.</li>
</ul>
<h3>Glossary</h3>
<dl>
<dt>Turn</dt><dd>One assistant response (usually one tool call). &ldquo;Assistant turns&rdquo; counts these.</dd>
<dt>Session</dt><dd>One Claude Code conversation (a distinct sessionId).</dd>
<dt>Tool error rate</dt><dd>Share of tool results returned as errors. Lower is better.</dd>
<dt>Est. waste</dt><dd>Modeled error cost = errors &times; avg cost/call &times; retry factor. Cost/call is exact; the retry factor (how many extra turns an error triggers) is your tunable assumption.</dd>
<dt>vs prev</dt><dd>Change from the previous period at the current granularity.</dd>
</dl>
</div>

<div class="foot">Rerun daily: <span class="mono">python report.py</span>. Data bucketed by activity timestamp &rarr; <span class="mono">daily_metrics.json</span>; prices &rarr; <span class="mono">pricing_history.json</span>. Cost computed at current pricing. Use ← / → to step periods.</div>
</div>

<script>
const DATA = __PAYLOAD__;
const META = DATA.meta;
const $ = s => document.querySelector(s);
const fmtUSD = v => "$"+(v>=1000? v.toLocaleString(undefined,{maximumFractionDigits:0}) : v.toFixed(2));
const fmtTok = v => v>=1e9?(v/1e9).toFixed(2)+"B":v>=1e6?(v/1e6).toFixed(1)+"M":v>=1e3?(v/1e3).toFixed(0)+"K":Math.round(v);
const fmtInt = v => Math.round(v).toLocaleString();
const esc = s => String(s).replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
const clean = p => p.replace("C--Users-charl-source-repos-","").replace("C--Users-charl-source-repos","(root)")||"(root)";
const MONTHS=["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];

const dayKeys = Object.keys(DATA.days).sort();
function mondayOf(ds){const d=new Date(ds+"T00:00:00Z");let wd=(d.getUTCDay()+6)%7;d.setUTCDate(d.getUTCDate()-wd);return d.toISOString().slice(0,10);}
function tokTotal(t){return t.input+t.output+t.cache_read+t.cache_write_5m+t.cache_write_1h;}

function blankPeriod(key,label){return{key,label,cost:0,
  tok:{input:0,output:0,cache_read:0,cache_write_5m:0,cache_write_1h:0},
  msgs:0,tool_results:0,tool_errors:0,sessions:0,
  by_model:{},by_project:{},by_tool:{},errors:{},dates:[]};}
function addNum(dst,src,keys){keys.forEach(k=>dst[k]+=src[k]||0);}
function addCounter(dst,src){for(const k in src)dst[k]=(dst[k]||0)+src[k];}
function addModel(dst,src){for(const t in src){dst[t]=dst[t]||{cost:0,cache_read:0,output:0};for(const k in src[t])dst[t][k]=(dst[t][k]||0)+src[t][k];}}
function addProj(dst,src){for(const p in src){dst[p]=dst[p]||{cost:0,msgs:0};for(const k in src[p])dst[p][k]=(dst[p][k]||0)+src[p][k];}}
function addTool(dst,src){for(const t in src){dst[t]=dst[t]||{calls:0,out:0,cost:0,err:0};for(const k in src[t])dst[t][k]=(dst[t][k]||0)+src[t][k];}}

function aggregate(gran){
  const groups=new Map();
  for(const dk of dayKeys){
    let key,label;
    if(gran==="day"){key=dk;label=dk;}
    else if(gran==="week"){key=mondayOf(dk);label="wk of "+key;}
    else{key=dk.slice(0,7);const[y,m]=key.split("-");label=MONTHS[+m-1]+" "+y;}
    if(!groups.has(key))groups.set(key,blankPeriod(key,label));
    const P=groups.get(key),D=DATA.days[dk];
    P.cost+=D.cost; P.msgs+=D.msgs; P.tool_results+=D.tool_results; P.tool_errors+=D.tool_errors;
    P.sessions+=D.sessions; P.dates.push(dk);
    addNum(P.tok,D.tok,Object.keys(P.tok));
    addModel(P.by_model,D.by_model); addProj(P.by_project,D.by_project);
    addTool(P.by_tool,D.by_tool); addCounter(P.errors,D.errors);
  }
  return [...groups.values()].sort((a,b)=>a.key<b.key?-1:1);
}

const METRICS={
  cost:{f:p=>p.cost,fmt:fmtUSD,label:"Cost"},
  tokens:{f:p=>tokTotal(p.tok),fmt:fmtTok,label:"Total tokens"},
  turns:{f:p=>p.msgs,fmt:fmtInt,label:"Assistant turns"},
  errrate:{f:p=>p.tool_results?p.tool_errors/p.tool_results*100:0,fmt:v=>v.toFixed(1)+"%",label:"Tool error rate"},
  errors:{f:p=>p.tool_errors,fmt:fmtInt,label:"Tool errors"},
  sessions:{f:p=>p.sessions,fmt:fmtInt,label:"Sessions"},
};

let state={gran:"day",metric:"cost",idx:0,periods:[]};

function svgLine(xs,vals,fmt,sel,color){
  const w=880,h=230,pad=46,n=xs.length;
  if(!n)return"<p class='muted'>No data.</p>";
  const mx=Math.max(...vals,0)*1.1||1;
  const X=i=>pad+i*(w-2*pad)/Math.max(n-1,1), Y=v=>h-pad-(v/mx)*(h-2*pad);
  let s=`<svg viewBox='0 0 ${w} ${h}' class='chart'>`;
  for(let g=0;g<5;g++){const gy=pad+g*(h-2*pad)/4,val=mx*(1-g/4);
    s+=`<line x1='${pad}' y1='${gy}' x2='${w-pad}' y2='${gy}' class='grid'/><text x='${pad-6}' y='${gy+3}' class='ytick'>${fmt(val)}</text>`;}
  s+=`<polyline points='${vals.map((v,i)=>X(i)+","+Y(v)).join(" ")}' fill='none' stroke='${color}' stroke-width='2'/>`;
  vals.forEach((v,i)=>{const r=i===sel?5:2.6,c=i===sel?"#fff":color;
    s+=`<circle class='dot' cx='${X(i)}' cy='${Y(v)}' r='${r}' fill='${c}' stroke='${color}' stroke-width='${i===sel?2:0}' onclick='pick(${i})'><title>${esc(xs[i])}: ${fmt(v)}</title></circle>`;});
  const step=Math.max(1,Math.ceil(n/10));
  for(let i=0;i<n;i+=step)s+=`<text x='${X(i)}' y='${h-pad+16}' class='xtick'>${esc(xs[i].length>7?xs[i].slice(5):xs[i])}</text>`;
  return s+"</svg>";
}
function hbars(rows,fmt,color){
  if(!rows.length)return"<p class='muted'>None.</p>";
  const mx=Math.max(...rows.map(r=>r[1]))||1;
  return"<div class='hbars'>"+rows.map(([l,v])=>
    `<div class='hbar'><span class='hlabel'>${esc(l)}</span><span class='htrack'><span class='hfill' style='width:${v/mx*100}%;background:${color}'></span></span><span class='hval'>${fmt(v)}</span></div>`).join("")+"</div>";
}

function render(){
  const P=state.periods, i=state.idx, cur=P[i], prev=P[i-1];
  const M=METRICS[state.metric];
  // stepper
  $("#pname").textContent=cur.label; $("#prev").disabled=i<=0; $("#next").disabled=i>=P.length-1;
  const range=cur.dates.length>1?` · ${cur.dates[0]} → ${cur.dates[cur.dates.length-1]}`:"";
  // KPI cards
  const kd=(label,val,d,dfmt)=>{let dh="";if(d!==null){const c=d>0?"up":d<0?"down":"flat",sg=d>0?"+":"";dh=`<div class='delta ${c}'>${sg}${dfmt(d)} vs prev</div>`;}
    return `<div class='card'><div class='klabel'>${label}</div><div class='kval'>${val}</div>${dh}</div>`;};
  const errRate=cur.tool_results?cur.tool_errors/cur.tool_results*100:0;
  const prevErr=prev&&prev.tool_results?prev.tool_errors/prev.tool_results*100:null;
  $("#cards").innerHTML=[
    kd("Cost",fmtUSD(cur.cost),prev?cur.cost-prev.cost:null,v=>fmtUSD(Math.abs(v))),
    kd("Total tokens",fmtTok(tokTotal(cur.tok)),prev?tokTotal(cur.tok)-tokTotal(prev.tok):null,v=>fmtTok(Math.abs(v))),
    kd("Assistant turns",fmtInt(cur.msgs),prev?cur.msgs-prev.msgs:null,v=>fmtInt(Math.abs(v))),
    kd("Sessions",fmtInt(cur.sessions),prev?cur.sessions-prev.sessions:null,v=>fmtInt(Math.abs(v))),
    kd("Tool error rate",errRate.toFixed(1)+"%",prevErr!==null?errRate-prevErr:null,v=>Math.abs(v).toFixed(1)+"pp"),
  ].join("");
  $("#sub").innerHTML=`Generated ${esc(DATA.generated)} · ${dayKeys.length} active days (${dayKeys[0]} → ${dayKeys[dayKeys.length-1]}) · viewing <b>${cur.label}</b>${range}`;
  // trend
  $("#trendhint").textContent="— "+M.label+" by "+state.gran+" · click a point or use ← →";
  $("#trend").innerHTML=svgLine(P.map(p=>p.label),P.map(M.f),M.fmt,i,"#6b8afd");
  // breakdowns
  $("#byproj").innerHTML=hbars(Object.entries(cur.by_project).map(([p,d])=>[clean(p),d.cost]).sort((a,b)=>b[1]-a[1]),v=>fmtUSD(v),"#5bbf8a");
  $("#bymodel").innerHTML=hbars(Object.entries(cur.by_model).map(([m,d])=>[m,d.cost]).sort((a,b)=>b[1]-a[1]),v=>fmtUSD(v),"#f0a35e");
  const t=cur.tok;
  $("#comp").innerHTML=hbars([["Cache read",t.cache_read],["Cache write 1h",t.cache_write_1h],["Cache write 5m",t.cache_write_5m],["Output",t.output],["Fresh input",t.input]],fmtTok,"#8b7bfd");
  $("#tools").innerHTML=hbars(Object.entries(cur.by_tool).filter(([t,v])=>v.calls>0).map(([t,v])=>[t,v.calls]).sort((a,b)=>b[1]-a[1]).slice(0,15),fmtInt,"#6b8afd");
  // tokens & cost per tool
  const rf=parseFloat($("#retry").value); const rfac=isNaN(rf)?1:rf;
  const trows=Object.entries(cur.by_tool).sort((a,b)=>b[1].cost-a[1].cost);
  let waste=0;
  $("#tooltbody").innerHTML=trows.map(([t,v])=>{
    const per=v.calls?v.cost/v.calls:0, w=v.err*per*rfac; waste+=w;
    const er=v.calls?v.err/v.calls*100:0;
    return `<tr><td>${esc(t)}</td><td class='num'>${v.calls?fmtInt(v.calls):"—"}</td><td class='num'>${fmtTok(v.out)}</td><td class='num'>${fmtUSD(v.cost)}</td><td class='num'>${v.err||""}</td><td class='num'>${v.calls&&v.err?er.toFixed(1)+"%":""}</td><td class='num'>${w?fmtUSD(w):""}</td></tr>`;
  }).join("");
  $("#waste").textContent=waste?`— ≈ ${fmtUSD(waste)} wasted on errors this period (retry ×${rfac})`:"";
  // errors
  const te=Object.values(cur.errors).reduce((a,b)=>a+b,0)||1;
  $("#errtbody").innerHTML=Object.entries(cur.errors).sort((a,b)=>b[1]-a[1]).map(([k,v])=>{
    const m=META[k]||{title:k,fix:""};
    return `<tr><td><b>${esc(m.title)}</b><br><span class='muted mono'>${esc(k)}</span></td><td class='num'>${v}</td><td class='num'>${Math.round(v/te*100)}%</td><td>${esc(m.fix)}</td></tr>`;
  }).join("")||"<tr><td colspan=4 class='muted'>No tool errors in this period. 🎉</td></tr>";
}
window.pick=i=>{state.idx=i;render();};

function rebuild(keepEnd){
  state.periods=aggregate(state.gran);
  state.idx=keepEnd?state.periods.length-1:Math.min(state.idx,state.periods.length-1);
  render();
}

// pricing chart (static)
(function(){
  const pd=Object.keys(DATA.pricing_history).sort();
  const colors={opus:"#f0a35e",sonnet:"#5bbf8a",haiku:"#6b8afd"};
  const w=880,h=200,pad=46,n=pd.length;
  const series=["opus","sonnet","haiku"].map(m=>({m,vals:pd.map(d=>(DATA.pricing_history[d][m]||{}).input||0)}));
  const mx=Math.max(1,...series.flatMap(s=>s.vals))*1.15;
  const X=i=>pad+i*(w-2*pad)/Math.max(n-1,1),Y=v=>h-pad-(v/mx)*(h-2*pad);
  let s=`<svg viewBox='0 0 ${w} ${h}' class='chart'>`;
  for(let g=0;g<4;g++){const gy=pad+g*(h-2*pad)/3,val=mx*(1-g/3);s+=`<line x1='${pad}' y1='${gy}' x2='${w-pad}' y2='${gy}' class='grid'/><text x='${pad-6}' y='${gy+3}' class='ytick'>$${val.toFixed(1)}</text>`;}
  series.forEach(se=>{s+=`<polyline points='${se.vals.map((v,i)=>X(i)+","+Y(v)).join(" ")}' fill='none' stroke='${colors[se.m]}' stroke-width='2'/>`;
    se.vals.forEach((v,i)=>s+=`<circle cx='${X(i)}' cy='${Y(v)}' r='2.6' fill='${colors[se.m]}'/>`);});
  const step=Math.max(1,Math.ceil(n/8));for(let i=0;i<n;i+=step)s+=`<text x='${X(i)}' y='${h-pad+16}' class='xtick'>${esc(pd[i].slice(5))}</text>`;
  s+="</svg><div class='legend'>"+series.map(se=>`<span class='leg'><i style='background:${colors[se.m]}'></i>${se.m}</span>`).join("")+"</div>";
  $("#pricechart").innerHTML=s;
})();

// wire controls
document.querySelectorAll("#gran button").forEach(b=>b.onclick=()=>{
  document.querySelectorAll("#gran button").forEach(x=>x.classList.remove("on"));
  b.classList.add("on"); state.gran=b.dataset.g; rebuild(true);
});
$("#metric").onchange=e=>{state.metric=e.target.value;render();};
$("#retry").oninput=()=>render();
$("#prev").onclick=()=>{if(state.idx>0){state.idx--;render();}};
$("#next").onclick=()=>{if(state.idx<state.periods.length-1){state.idx++;render();}};
document.addEventListener("keydown",e=>{if(e.key==="ArrowLeft")$("#prev").click();if(e.key==="ArrowRight")$("#next").click();});

rebuild(true);
</script>
</body></html>"""

if __name__ == "__main__":
    main()
