#!/usr/bin/env python3
"""TraceYield PR & Release pages generator.

Turns two kinds of record into a static, on-brand section of the GitHub Pages
site, mirroring report.py's stdlib-only, zero-dependency idiom:

  prs/pr-<n>.md              A pull request, born when the PR is opened and kept
                             in sync as commits land and its lifecycle changes.
                             Renders the *technical* announcement -- what changed
                             and why, for the team. This is the "PR page".

  releases/<slug>/release.md A release, born when work merges to main. Bundles
                             one or more PRs and renders the *user-facing*
                             announcement in plain benefit language. This is the
                             "release page".

The records are the source of truth. The generated HTML under site/public/{prs,
releases}/ must never be hand-edited: Astro copies site/public/ verbatim into the
published site, so the pages appear at

  https://decoupledlogic.github.io/traceyield/prs/
  https://decoupledlogic.github.io/traceyield/releases/

and the existing deploy-site.yml redeploys them on any site/** change.

Usage:
  python pages.py            Regenerate every page from the records (no network).
  python pages.py --refresh  Also pull each open PR's live state + commit history
                             from `gh` (best-effort; falls back to the record when
                             gh is unavailable), so a page stays current with the
                             commits pushed since it was last built.
"""

import datetime
import glob
import html
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PR_RECORDS = os.path.join(ROOT, "prs")
REL_RECORDS = os.path.join(ROOT, "releases")
SITE_PUBLIC = os.path.join(ROOT, "site", "public")
PR_OUT = os.path.join(SITE_PUBLIC, "prs")
REL_OUT = os.path.join(SITE_PUBLIC, "releases")

BASE = "/traceyield"                     # GitHub project-page subpath (site/base.config.mjs)
BASE_SLASH = BASE.rstrip("/") + "/"      # Base.astro uses the trailing-slash form
REPO = "DecoupledLogic/traceyield"       # owner/repo for `gh` and PR links

SITE_STYLES = os.path.join(ROOT, "site", "src", "styles")
PAGES_CSS = os.path.join(SITE_PUBLIC, "pr-pages.css")  # generated: site chrome + page extras

# status value -> (semantic token, human label). The token maps onto the brand
# status colors inlined in STYLE below (ok / warn / bad / info / neutral / accent).
PR_STATUS = {
    "draft":              ("neutral", "Draft"),
    "needs-review":       ("info",    "Needs review"),
    "changes-requested":  ("warn",    "Changes requested"),
    "approved":           ("ok",      "Approved"),
    "blocked":            ("bad",     "Blocked"),
    "merged":             ("accent",  "Merged"),
    "closed":             ("neutral", "Closed"),
}

REL_STATUS = {
    "draft":     ("neutral", "Draft"),
    "ready":     ("info",    "Ready"),
    "announced": ("ok",      "Announced"),
    "held":      ("warn",    "Held"),
}


# --------------------------------------------------------------------------- #
# Record parsing (a small, dependency-free frontmatter + markdown subset)
# --------------------------------------------------------------------------- #

def parse_record(path):
    """Return (frontmatter_dict, body_str) for a `---` fenced markdown record.

    The frontmatter is a small YAML subset: `key: value` scalars plus block
    lists (a bare key followed by indented `- item` lines). No nesting beyond
    that -- deliberately, to stay stdlib-only and predictable.
    """
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.DOTALL)
    if not m:
        return {}, text.strip()
    front_raw, body = m.group(1), m.group(2)

    fm = {}
    key = None
    for line in front_raw.splitlines():
        if not line.strip():
            continue
        list_item = re.match(r"^\s+-\s+(.*)$", line)
        if list_item and key is not None:
            # promote the empty-value placeholder (`key:`) to a block list
            if not isinstance(fm.get(key), list):
                fm[key] = []
            fm[key].append(list_item.group(1).strip())
            continue
        kv = re.match(r"^([A-Za-z0-9_]+):\s*(.*)$", line)
        if kv:
            key, val = kv.group(1), kv.group(2).strip()
            if val == "":
                fm[key] = ""          # may become a block list on the next lines
            else:
                fm[key] = _scalar(val)
    return fm, body.strip()


def _scalar(val):
    """Coerce a frontmatter scalar: strip quotes, map booleans."""
    if len(val) >= 2 and val[0] in "\"'" and val[-1] == val[0]:
        val = val[1:-1]
    low = val.lower()
    if low in ("true", "false"):
        return low == "true"
    return val


_INLINE_CODE = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def _inline(text):
    """Render the inline markdown subset: `code`, **bold**, [text](url)."""
    out = html.escape(text, quote=False)
    # links first (before escaping would-be markup inside); operate on escaped text
    out = _LINK.sub(
        lambda m: '<a href="%s">%s</a>' % (html.escape(m.group(2), quote=True), m.group(1)),
        out,
    )
    out = _INLINE_CODE.sub(lambda m: "<code>%s</code>" % m.group(1), out)
    out = _BOLD.sub(lambda m: "<strong>%s</strong>" % m.group(1), out)
    return out


def render_md(text):
    """Render a small, safe markdown subset to HTML.

    Supports: ## / ### headings, paragraphs, `-` bullet lists, `- [ ]` / `- [x]`
    task lists (rendered as read-only checkboxes), and ``` fenced code blocks.
    Enough for the record bodies; not a general markdown engine.
    """
    lines = text.splitlines()
    out, i, n = [], 0, len(lines)
    while i < n:
        line = lines[i]
        if line.startswith("```"):
            i += 1
            buf = []
            while i < n and not lines[i].startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1  # skip closing fence
            out.append("<pre><code>%s</code></pre>" % html.escape("\n".join(buf)))
            continue
        if line.startswith("### "):
            out.append("<h3>%s</h3>" % _inline(line[4:].strip()))
            i += 1
            continue
        if line.startswith("## "):
            out.append("<h2>%s</h2>" % _inline(line[3:].strip()))
            i += 1
            continue
        if re.match(r"^\s*-\s+", line):
            items, has_task = [], False
            while i < n and re.match(r"^\s*-\s+", lines[i]):
                item = re.sub(r"^\s*-\s+", "", lines[i])
                task = re.match(r"^\[([ xX])\]\s+(.*)$", item)
                if task:
                    has_task = True
                    done = task.group(1).lower() == "x"
                    box = ('<input type="checkbox" disabled%s> '
                           % (" checked" if done else ""))
                    items.append('<li class="%s">%s%s</li>'
                                 % ("done" if done else "todo", box, _inline(task.group(2))))
                else:
                    items.append("<li>%s</li>" % _inline(item))
                i += 1
            cls = ' class="tasklist"' if has_task else ""
            out.append("<ul%s>%s</ul>" % (cls, "".join(items)))
            continue
        if not line.strip():
            i += 1
            continue
        # paragraph: gather until blank / block start
        buf = [line]
        i += 1
        while i < n and lines[i].strip() and not re.match(r"^\s*(-|#|```)", lines[i]):
            buf.append(lines[i])
            i += 1
        out.append("<p>%s</p>" % _inline(" ".join(s.strip() for s in buf)))
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# Live state (best-effort, --refresh only)
# --------------------------------------------------------------------------- #

def gh_json(args):
    """Run a `gh` command returning JSON, or None if gh is unavailable/fails."""
    try:
        res = subprocess.run(
            ["gh"] + args,
            capture_output=True, text=True, encoding="utf-8", timeout=30, check=True,
        )
        import json
        return json.loads(res.stdout)
    except Exception:
        return None


def refresh_pr(fm):
    """Merge live GitHub state + commit history into a PR frontmatter dict.

    Objective fields (url, branch, opened, author, terminal status, commits) are
    pulled from `gh`; curated fields (title, summary, statusNote, and a curated
    open status) are left untouched. No-op when gh is unavailable.
    """
    n = fm.get("pr")
    if not n:
        return fm
    data = gh_json(["pr", "view", str(n), "--repo", REPO,
                    "--json", "url,headRefName,createdAt,author,state,isDraft,title,mergedAt"])
    if data:
        fm["url"] = data.get("url", fm.get("url", ""))
        fm["branch"] = data.get("headRefName", fm.get("branch", ""))
        fm["opened"] = (data.get("createdAt") or "")[:10] or fm.get("opened", "")
        fm["author"] = (data.get("author") or {}).get("login", fm.get("author", ""))
        if data.get("mergedAt"):
            fm["mergedAt"] = data["mergedAt"][:10]
        # only auto-set terminal / draft statuses; never downgrade a curated one
        curated = {"blocked", "approved", "changes-requested"}
        state = (data.get("state") or "").upper()
        if state == "MERGED":
            fm["status"] = "merged"
        elif state == "CLOSED":
            fm["status"] = "closed"
        elif fm.get("status") not in curated:
            fm["status"] = "draft" if data.get("isDraft") else "needs-review"
    commits = gh_json(["pr", "view", str(n), "--repo", REPO, "--json", "commits"])
    if commits and commits.get("commits"):
        rows = []
        for c in commits["commits"]:
            msg = (c.get("messageHeadline") or "").strip()
            date = (c.get("committedDate") or "")[:10]
            sha = (c.get("oid") or "")[:7]
            author = ((c.get("authors") or [{}])[0]).get("login") or \
                     ((c.get("authors") or [{}])[0]).get("name") or ""
            rows.append((date, sha, msg, author))
        fm["_commits"] = rows
    return fm


# --------------------------------------------------------------------------- #
# Record writing (single-PR register + merge finalize, for CI automation)
# --------------------------------------------------------------------------- #

# Field order when serializing a PR record, so rewrites stay diff-stable.
PR_FIELD_ORDER = ["pr", "url", "branch", "title", "status", "author",
                  "opened", "mergedAt", "summary", "statusNote", "release"]
REL_FIELD_ORDER = ["slug", "title", "theme", "status", "released", "prs",
                   "userVisible", "summary"]


def _dump_scalar(val):
    if isinstance(val, bool):
        return "true" if val else "false"
    s = str(val)
    # quote when the value could be misread as YAML (starts special, or has a colon)
    if s == "" or s[0] in "#&*!|>%@`\"'[]{}," or ": " in s or s.endswith(":"):
        return '"%s"' % s.replace('"', '\\"')
    return s


def dump_record(fm, body, order):
    """Serialize a frontmatter dict + body back to a `---` fenced record.

    Keys are emitted in `order` (then any extras alphabetically), skipping the
    private `_commits` cache. List values are written as block lists.
    """
    keys = [k for k in order if k in fm and not k.startswith("_")]
    keys += sorted(k for k in fm if k not in keys and not k.startswith("_"))
    lines = ["---"]
    for k in keys:
        v = fm[k]
        if isinstance(v, list):
            lines.append("%s:" % k)
            lines.extend("  - %s" % item for item in v)
        else:
            lines.append("%s: %s" % (k, _dump_scalar(v)))
    lines.append("---")
    text = "\n".join(lines)
    if body.strip():
        text += "\n\n" + body.strip() + "\n"
    else:
        text += "\n"
    return text


def _clean_title(raw):
    """Turn a conventional-commit PR title into a human display title."""
    m = re.match(r"^[a-z]+(?:\([^)]*\))?!?:\s*(.*)$", raw.strip())
    title = m.group(1) if m else raw.strip()
    return title[:1].upper() + title[1:] if title else title


def register_pr(n):
    """Create or update prs/pr-<n>.md from live GitHub state, then return its path.

    New records are seeded from `gh` (title, summary, body). Existing records keep
    their curated fields (title, summary, statusNote, release, curated status) and
    only refresh objective ones. Writes only when the content actually changes, so
    a no-op event produces no commit. Returns (path, changed).
    """
    path = os.path.join(PR_RECORDS, "pr-%s.md" % n)
    fm, body = (parse_record(path) if os.path.exists(path) else ({}, ""))
    is_new = not os.path.exists(path)
    fm.setdefault("pr", int(n) if str(n).isdigit() else n)

    data = gh_json(["pr", "view", str(n), "--repo", REPO, "--json",
                    "url,headRefName,createdAt,author,state,isDraft,title,body,mergedAt"])
    if data:
        fm["url"] = data.get("url", fm.get("url", ""))
        fm["branch"] = data.get("headRefName", fm.get("branch", ""))
        fm["opened"] = (data.get("createdAt") or "")[:10] or fm.get("opened", "")
        fm["author"] = (data.get("author") or {}).get("login", fm.get("author", ""))
        if data.get("mergedAt"):
            fm["mergedAt"] = data["mergedAt"][:10]
        curated = {"blocked", "approved", "changes-requested"}
        state = (data.get("state") or "").upper()
        if state == "MERGED":
            fm["status"] = "merged"
        elif state == "CLOSED":
            fm["status"] = "closed"
        elif fm.get("status") not in curated:
            fm["status"] = "draft" if data.get("isDraft") else "needs-review"
        # curated fields: seed on create, preserve on update
        if not fm.get("title"):
            fm["title"] = _clean_title(data.get("title", "") or "PR #%s" % n)
        pr_body = (data.get("body") or "").strip()
        if not fm.get("summary"):
            # first meaningful prose line: skip blanks, markdown headings, and HTML comments
            first = next((ln.strip() for ln in pr_body.splitlines()
                          if ln.strip() and not ln.lstrip().startswith(("#", "<!--"))), "")
            fm["summary"] = first or fm["title"]
        if is_new and not body.strip():
            body = pr_body or "_No description provided._"
    else:
        fm.setdefault("status", "needs-review")
        fm.setdefault("title", "PR #%s" % n)

    new_text = dump_record(fm, body, PR_FIELD_ORDER)
    old_text = ""
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            old_text = fh.read()
    changed = new_text != old_text
    if changed:
        write(path, new_text)
    return path, changed


def _slugify(text):
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:60] or "release"


def on_merge(n):
    """React to PR #<n> merging: finalize any release that bundles it, or scaffold
    a draft release for it if none does. Returns a list of (path, changed)."""
    data = gh_json(["pr", "view", str(n), "--repo", REPO, "--json",
                    "state,mergedAt,title,url"])
    if not data or (data.get("state") or "").upper() != "MERGED":
        return []
    merged_on = (data.get("mergedAt") or "")[:10]
    results = []

    referencing = []
    for path in sorted(glob.glob(os.path.join(REL_RECORDS, "*", "release.md"))):
        fm, body = parse_record(path)
        prs = [re.sub(r"\D", "", str(x)) for x in (fm.get("prs") or [])]
        if str(n) in prs:
            referencing.append((path, fm, body))

    if referencing:
        for path, fm, body in referencing:
            if fm.get("status") != "announced":
                fm["status"] = "announced"
                fm.setdefault("released", merged_on)
                fm["slug"] = fm.get("slug") or os.path.basename(os.path.dirname(path))
                new_text = dump_record(fm, body, REL_FIELD_ORDER)
                write(path, new_text)
                results.append((path, True))
    else:
        # no release bundles this PR yet -> scaffold a draft the maintainer can curate
        title = _clean_title(data.get("title", "") or "Release")
        slug = _slugify(title)
        path = os.path.join(REL_RECORDS, slug, "release.md")
        if not os.path.exists(path):
            fm = {"slug": slug, "title": title, "theme": "",
                  "status": "draft", "released": merged_on,
                  "prs": [str(n)], "userVisible": False,
                  "summary": "Draft release for PR #%s — edit or bundle before announcing." % n}
            body = ("_Draft release scaffolded on merge of PR #%s. Rewrite this in "
                    "plain, user-facing language, set `userVisible` and `status`, and "
                    "bundle any related PRs into `prs`._" % n)
            write(path, dump_record(fm, body, REL_FIELD_ORDER))
            results.append((path, True))
    return results


# --------------------------------------------------------------------------- #
# HTML rendering
# --------------------------------------------------------------------------- #

# Page-specific CSS, appended to the site's own tokens.css + global.css so these
# pages reuse the site chrome (.site-header/.nav/.hero/.card/.prose/.btn/...) and
# only add what the PR/release views need. All colors resolve through --ty-* so a
# design-token edit restyles these pages too.
PAGE_CSS = """
/* ---- PR / release page extras (generated by pages.py) ---- */
.meta { display:flex; flex-wrap:wrap; gap:8px 16px; align-items:center; margin:var(--ty-space-4) 0 0;
  font-size:var(--ty-fs-base); color:var(--ty-text-muted); }
.meta .mono { font-family:var(--ty-font-mono); font-size:var(--ty-fs-sm); }
.meta .btn { margin-top:0; }
.pill { display:inline-flex; align-items:center; gap:6px; padding:3px 11px;
  border-radius:var(--ty-radius-pill); font-size:var(--ty-fs-sm); font-weight:var(--ty-w-semibold);
  border:1px solid transparent; }
.pill.ok { color:var(--ty-ok-fg); background:var(--ty-ok-bg); border-color:var(--ty-ok); }
.pill.warn { color:var(--ty-warn-fg); background:var(--ty-warn-bg); border-color:var(--ty-warn); }
.pill.bad { color:var(--ty-bad-fg); background:var(--ty-bad-bg); border-color:var(--ty-bad); }
.pill.info { color:var(--ty-info-fg); background:var(--ty-info-bg); border-color:var(--ty-info); }
.pill.neutral { color:var(--ty-neutral-fg); background:var(--ty-neutral-bg); border-color:var(--ty-border); }
.pill.accent { color:var(--ty-accent); background:var(--ty-surface-2); border-color:var(--ty-accent); }
.index-row { display:flex; gap:16px; align-items:baseline; padding:var(--ty-space-4) 0;
  border-bottom:1px solid var(--ty-border); }
.index-row a.title { font-weight:var(--ty-w-semibold); font-size:var(--ty-fs-lg); }
.index-row .sum { color:var(--ty-text-muted); font-size:var(--ty-fs-md); margin:var(--ty-space-1) 0 0; }
.index-row .n { font-family:var(--ty-font-mono); color:var(--ty-text-faint);
  font-size:var(--ty-fs-base); min-width:52px; }
.badges { display:flex; gap:6px; flex-wrap:wrap; margin:var(--ty-space-2) 0 0; }
table.commits { width:100%; border-collapse:collapse; font-size:var(--ty-fs-base); margin:var(--ty-space-2) 0; }
table.commits th, table.commits td { text-align:left; padding:8px 10px;
  border-bottom:1px solid var(--ty-border); vertical-align:top; }
table.commits th { color:var(--ty-text-muted); font-weight:var(--ty-w-semibold);
  font-size:var(--ty-fs-sm); text-transform:uppercase; letter-spacing:.04em; }
table.commits td.mono { font-family:var(--ty-font-mono); font-size:var(--ty-fs-sm); white-space:nowrap; }
.prose ul.tasklist { list-style:none; padding-left:0; }
.prose ul.tasklist li { margin:5px 0; }
.prose ul.tasklist li.done { color:var(--ty-text-muted); }
.prose ul.tasklist input { margin-right:8px; }
.empty { color:var(--ty-text-muted); padding:var(--ty-space-10) 0; }
"""


def build_css():
    """Emit site/public/pr-pages.css = the site's real tokens + chrome + PAGE_CSS.

    Reads the site's own tokens.css and global.css so the generated pages share
    the exact header/footer/hero/card/prose styling (and never drift from a
    hand-kept copy). Resolves global.css's `@import "./tokens.css"` by inlining
    tokens first. Falls back to just PAGE_CSS if the sources are missing.
    """
    chunks = []
    for name in ("tokens.css", "global.css"):
        p = os.path.join(SITE_STYLES, name)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                css = fh.read()
            # drop @import lines (tokens is inlined above global; no external fetch)
            css = re.sub(r'^\s*@import\s+[^;]+;\s*$', '', css, flags=re.MULTILINE)
            chunks.append(css)
    chunks.append(PAGE_CSS)
    write(PAGES_CSS, "\n".join(chunks))


def page_shell(title, body_html, description="Change notes and releases for TraceYield."):
    """Wrap page content in the exact Base.astro chrome (header, footer, theme,
    search), linking the generated pr-pages.css. Mirrors site/src/layouts/Base.astro
    so these pages are indistinguishable from the rest of the site."""
    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>%(title)s</title>
<meta name="description" content="%(desc)s" />
<link rel="icon" type="image/svg+xml" href="%(base)sbrand/mark.svg" />
<link rel="preconnect" href="https://fonts.googleapis.com" />
<script>
  (function () {
    try {
      var t = localStorage.getItem('ty-theme') || 'dark';
      document.documentElement.setAttribute('data-theme', t);
    } catch (e) {}
  })();
</script>
<link rel="stylesheet" href="%(base)spr-pages.css" />
</head>
<body>
<header class="site-header">
  <div class="wrap">
    <a class="brand" href="%(base)s">
      <img class="logo-dark" src="%(base)sbrand/logo-mark-dark.svg" alt="TraceYield" />
      <img class="logo-light" src="%(base)sbrand/logo-mark-light.svg" alt="TraceYield" />
    </a>
    <nav class="nav">
      <a href="%(base)s">Home</a>
      <a href="%(base)sprs/index.html">PRs</a>
      <a href="%(base)sreleases/index.html">Releases</a>
      <a href="%(base)sdocs/overview">Docs</a>
      <a class="hide-sm" href="https://github.com/DecoupledLogic/traceyield">GitHub</a>
      <button class="icon-btn" id="search-open" aria-label="Search" title="Search">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/></svg>
      </button>
      <button class="icon-btn" id="theme-toggle" aria-label="Toggle theme" title="Toggle light / dark">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>
      </button>
    </nav>
  </div>
</header>
<main>
%(body)s
</main>
<footer class="site-footer">
  <div class="wrap">
    <span>TraceYield &middot; Tokens are the meter, not the mission.</span>
    <span>&copy; 2026 DecoupledLogic &middot; Apache-2.0 (name &amp; marks: <a href="https://github.com/DecoupledLogic/traceyield/blob/main/TRADEMARK.md">trademark policy</a>)</span>
  </div>
</footer>
<dialog id="searchbox"><div class="pad"><div id="search"></div></div></dialog>
<link rel="stylesheet" href="%(base)spagefind/pagefind-ui.css" />
<script>
  document.getElementById('theme-toggle')?.addEventListener('click', function () {
    var cur = document.documentElement.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
    document.documentElement.setAttribute('data-theme', cur);
    try { localStorage.setItem('ty-theme', cur); } catch (e) {}
  });
  var base = '%(base)s';
  var dlg = document.getElementById('searchbox');
  var built = false;
  document.getElementById('search-open')?.addEventListener('click', function () {
    if (!built) {
      built = true;
      var s = document.createElement('script');
      s.src = base + 'pagefind/pagefind-ui.js';
      s.onload = function () {
        new window.PagefindUI({ element: '#search', showSubResults: true, showImages: false,
          processResult: function (result) {
            var prefix = base.replace(/\\/$/, '');
            var fix = function (u) { return u && u.charAt(0) === '/' && u.indexOf(prefix + '/') !== 0 ? prefix + u : u; };
            result.url = fix(result.url);
            if (result.sub_results) { result.sub_results.forEach(function (x) { x.url = fix(x.url); }); }
            return result;
          } });
      };
      document.head.appendChild(s);
    }
    dlg.showModal();
    setTimeout(function () { dlg.querySelector('input')?.focus(); }, 60);
  });
  dlg?.addEventListener('click', function (e) { if (e.target === dlg) dlg.close(); });
</script>
</body>
</html>""" % {
        "title": html.escape(title), "desc": html.escape(description),
        "base": BASE_SLASH, "body": body_html,
    }


def pill(mapping, status):
    tok, label = mapping.get(status, ("neutral", status or "unknown"))
    return '<span class="pill %s">%s</span>' % (tok, html.escape(label))


def _hero(eyebrow, heading_html, lede_html="", extra_html=""):
    """A masthead matching the site's .hero (inside a .wrap)."""
    parts = ['<section class="hero"><div class="wrap">',
             '<p class="eyebrow">%s</p>' % eyebrow,
             "<h1>%s</h1>" % heading_html]
    if lede_html:
        parts.append('<p class="lede">%s</p>' % lede_html)
    if extra_html:
        parts.append(extra_html)
    parts.append("</div></section>")
    return "".join(parts)


def _section(inner_html):
    return '<section class="section"><div class="wrap">%s</div></section>' % inner_html


def render_pr_page(fm, body):
    n = fm.get("pr", "?")
    title = fm.get("title") or "PR #%s" % n
    url = fm.get("url") or "https://github.com/%s/pull/%s" % (REPO, n)
    meta_bits = [pill(PR_STATUS, fm.get("status", ""))]
    if fm.get("branch"):
        meta_bits.append('<span class="mono">%s</span>' % html.escape(str(fm["branch"])))
    if fm.get("author"):
        meta_bits.append("<span>@%s</span>" % html.escape(str(fm["author"])))
    if fm.get("opened"):
        meta_bits.append("<span>opened %s</span>" % html.escape(str(fm["opened"])))
    if fm.get("mergedAt"):
        meta_bits.append("<span>merged %s</span>" % html.escape(str(fm["mergedAt"])))

    extra = ('<div class="meta">%s</div>'
             '<div class="cta-row" style="margin-top:var(--ty-space-4)">'
             '<a class="btn btn-primary" href="%s">View PR #%s on GitHub &rarr;</a></div>'
             % ("".join(meta_bits), html.escape(url, quote=True), html.escape(str(n))))
    lede = _inline(str(fm["summary"])) if fm.get("summary") else ""
    hero = _hero("PR #%s &middot; Technical change note" % html.escape(str(n)),
                 _inline(str(title)), lede, extra)

    blocks = []
    if fm.get("statusNote"):
        blocks.append('<div class="card"><h3>Status</h3><p>%s</p></div>'
                      % _inline(str(fm["statusNote"])))
    blocks.append('<div class="prose">%s</div>' % render_md(body))

    commits = fm.get("_commits")
    if commits:
        rows = "".join(
            "<tr><td class='mono'>%s</td><td class='mono'>%s</td><td>%s</td><td>%s</td></tr>"
            % (html.escape(d), html.escape(s), _inline(m), html.escape(a))
            for d, s, m, a in commits
        )
        blocks.append('<h2 style="margin-top:var(--ty-space-8)">Commit history</h2>'
                      '<table class="commits"><thead><tr><th>Date</th><th>Commit</th>'
                      '<th>Message</th><th>Author</th></tr></thead><tbody>%s</tbody></table>' % rows)

    return page_shell("PR #%s — %s" % (n, title), hero + _section("".join(blocks)),
                      description=str(fm.get("summary") or title))


def render_release_page(fm, body, pr_index):
    title = fm.get("title") or fm.get("slug", "Release")
    extra = ""
    meta_bits = [pill(REL_STATUS, fm.get("status", ""))]
    if fm.get("released"):
        meta_bits.append("<span>released %s</span>" % html.escape(str(fm["released"])))
    extra = '<div class="meta">%s</div>' % "".join(meta_bits)
    lede = _inline(str(fm["theme"])) if fm.get("theme") else ""
    hero = _hero("Release &middot; What&rsquo;s new", _inline(str(title)), lede, extra)

    blocks = ['<div class="prose">%s</div>' % render_md(body)]

    prs = fm.get("prs") or []
    if prs:
        links = []
        for ref in prs:
            num = re.sub(r"\D", "", str(ref))
            if num in pr_index:
                # a local PR page exists -> link to it
                href, label, note = ("%sprs/pr-%s.html" % (BASE_SLASH, num), pr_index[num], "")
            else:
                # no record for this PR -> link out to GitHub rather than a 404
                href = "https://github.com/%s/pull/%s" % (REPO, num)
                label = "PR #%s" % num
                note = (' <span class="mono" style="color:var(--ty-text-faint)">'
                        '(on GitHub)</span>')
            links.append('<div class="index-row"><span class="n">#%s</span>'
                         '<div><a class="title" href="%s">%s</a>%s</div></div>'
                         % (html.escape(num), html.escape(href, quote=True),
                            _inline(str(label)), note))
        blocks.append('<div class="card" style="margin-top:var(--ty-space-8)">'
                      '<h3>The technical detail</h3>'
                      '<p style="color:var(--ty-text-muted)">The engineering change notes behind '
                      'this release.</p>' + "".join(links) + "</div>")

    return page_shell("Release — %s" % title, hero + _section("".join(blocks)),
                      description=str(fm.get("summary") or title))


def render_pr_index(records):
    rows = []
    for fm, _ in sorted(records, key=lambda r: str(r[0].get("opened", "")), reverse=True):
        n = fm.get("pr", "?")
        rows.append(
            '<div class="index-row"><span class="n">#%s</span><div>'
            '<a class="title" href="%sprs/pr-%s.html">%s</a>'
            '<div class="badges">%s</div>'
            '<p class="sum">%s</p></div></div>'
            % (html.escape(str(n)), BASE_SLASH, html.escape(str(n)),
               _inline(str(fm.get("title") or ("PR #%s" % n))),
               pill(PR_STATUS, fm.get("status", "")),
               _inline(str(fm.get("summary", "")))))
    hero = _hero("Change notes", 'Pull <span class="grad">requests</span>',
                 "A technical note per PR, born when the PR opens and kept in sync as "
                 "commits land and its status changes.")
    inner = "".join(rows) if rows else '<p class="empty">No PRs tracked yet.</p>'
    return page_shell("TraceYield — Pull requests", hero + _section(inner))


def render_release_index(records):
    rows = []
    for fm, _ in sorted(records, key=lambda r: str(r[0].get("released", "")), reverse=True):
        slug = fm.get("slug", "")
        rows.append(
            '<div class="index-row"><div>'
            '<a class="title" href="%sreleases/%s.html">%s</a>'
            '<div class="badges">%s</div>'
            '<p class="sum">%s</p></div></div>'
            % (BASE_SLASH, html.escape(str(slug)),
               _inline(str(fm.get("title") or slug)),
               pill(REL_STATUS, fm.get("status", "")),
               _inline(str(fm.get("summary", "")))))
    hero = _hero("What&rsquo;s new", '<span class="grad">Releases</span>',
                 "A plain-language announcement per release, born when work merges to main.")
    inner = "".join(rows) if rows else '<p class="empty">No releases announced yet.</p>'
    return page_shell("TraceYield — Releases", hero + _section(inner))


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #

def load_prs(refresh=False):
    records = []
    for path in sorted(glob.glob(os.path.join(PR_RECORDS, "pr-*.md"))):
        fm, body = parse_record(path)
        if refresh:
            fm = refresh_pr(fm)  # refresh objective fields + commit history from gh
        records.append((fm, body))
    return records


def load_releases():
    records = []
    for path in sorted(glob.glob(os.path.join(REL_RECORDS, "*", "release.md"))):
        fm, body = parse_record(path)
        if "slug" not in fm:
            fm["slug"] = os.path.basename(os.path.dirname(path))
        records.append((fm, body))
    return records


def write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


def _flag_value(argv, name):
    """Return the token following `--name`, or None."""
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            return argv[i + 1]
    return None


def render_all(refresh=False):
    """Regenerate the whole PR/release section (CSS + every page) from records."""
    build_css()
    prs = load_prs(refresh=refresh)
    releases = load_releases()
    pr_index = {str(fm.get("pr")): (fm.get("title") or "PR #%s" % fm.get("pr"))
                for fm, _ in prs}
    for fm, body in prs:
        write(os.path.join(PR_OUT, "pr-%s.html" % fm.get("pr")), render_pr_page(fm, body))
    write(os.path.join(PR_OUT, "index.html"), render_pr_index(prs))
    for fm, body in releases:
        write(os.path.join(REL_OUT, "%s.html" % fm["slug"]), render_release_page(fm, body, pr_index))
    write(os.path.join(REL_OUT, "index.html"), render_release_index(releases))
    return len(prs), len(releases)


def main(argv):
    refresh = "--refresh" in argv

    # --pr N: create/update a single PR record from live GitHub state (CI: PR events)
    pr_n = _flag_value(argv, "--pr")
    if pr_n:
        path, changed = register_pr(pr_n)
        print("pr %s: %s %s" % (pr_n, "wrote" if changed else "unchanged",
                                os.path.relpath(path, ROOT)))

    # --merge N: react to a merge (finalize/scaffold the release). CI: closed+merged
    merge_n = _flag_value(argv, "--merge")
    if merge_n:
        for path, _ in on_merge(merge_n):
            print("merge %s: wrote %s" % (merge_n, os.path.relpath(path, ROOT)))

    n_prs, n_rel = render_all(refresh=refresh)
    print("pages: %d PR page(s), %d release(s)%s -> %s"
          % (n_prs, n_rel, " (refreshed)" if refresh else "",
             os.path.relpath(SITE_PUBLIC, ROOT)))


if __name__ == "__main__":
    main(sys.argv[1:])
