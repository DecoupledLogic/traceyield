#!/usr/bin/env python3
"""One-way mirror: TraceYield Tempo roadmap -> GitHub Issues.

The source of truth is ``roadmap.csv`` + the ``docs/delivery`` companions
(Decision 0025). This script *projects* the open epic/feature/story work into
GitHub Issues so contributors can follow and help, and closes issues whose
items have since been released. It never edits the roadmap's status; the only
thing it writes back is the GitHub issue mapping, into each companion's
``external*`` front-matter -- a field-scoped edit that preserves the body and
every other field, exactly like the lib's ``companionSetField`` (verified
clobber-safe).

It is idempotent: it dedups by the companion's ``externalId``, so re-running
only creates what is missing and reconciles the rest. Dry-run by default.

    python scripts/sync-issues.py              # print the plan, change nothing
    python scripts/sync-issues.py --apply      # create / update / close issues
    python scripts/sync-issues.py --repo owner/name --apply

Requires the authenticated ``gh`` CLI. Stdlib only.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ROADMAP = os.path.join(ROOT, "roadmap.csv")
DELIVERY = os.path.join(ROOT, "docs", "delivery")

# Only these work-item types become contributor-facing issues. Slices, requests,
# tech-debts and issues are Tempo-internal groupings and are deliberately left out.
SYNC_TYPES = {"epic", "feature", "story"}
TYPE_DIR = {"epic": "epics", "feature": "features", "story": "stories"}

# A Tempo status maps onto GitHub's binary open/closed. Everything that is not
# yet shipped stays open; shipped/among-review work is closed.
CLOSED_STATUSES = {"released", "in_review"}

EXTERNAL_SYSTEM = "github"

# Labels this tool OWNS (it will add/remove within these namespaces and leave
# any other labels -- e.g. "good first issue", "bug" -- untouched).
STATUS_COLOR = "fbca04"
TYPE_COLORS = {"epic": "5319e7", "feature": "1d76db", "story": "c5def5"}
HELP_LABEL = "help wanted"  # applied to open stories


# --------------------------------------------------------------------------- gh

def have_gh() -> bool:
    return shutil.which("gh") is not None


def gh(args, *, input_text=None, check=True):
    """Run a gh command and return stdout (text)."""
    proc = subprocess.run(
        ["gh", *args],
        input=input_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if check and proc.returncode != 0:
        raise RuntimeError(
            "gh %s failed (%d):\n%s" % (" ".join(args), proc.returncode, proc.stderr.strip())
        )
    return proc.stdout


def detect_repo() -> str:
    """owner/name from the git origin remote."""
    url = subprocess.run(
        ["git", "-C", ROOT, "config", "--get", "remote.origin.url"],
        capture_output=True, text=True,
    ).stdout.strip()
    m = re.search(r"[:/]([^/]+/[^/]+?)(?:\.git)?$", url)
    if not m:
        raise SystemExit("could not derive owner/name from remote: %r" % url)
    return m.group(1)


# ------------------------------------------------------------------- companions

def companion_path(item) -> str:
    return os.path.join(DELIVERY, TYPE_DIR[item["Type"]], item["Key"] + ".md")


def _split_front_matter(text):
    m = re.match(r"^(---\s*\n)(.*?)(\n---\s*\n)(.*)$", text, re.DOTALL)
    if not m:
        return None
    return m.group(1), m.group(2), m.group(3), m.group(4)


def read_companion(path):
    """Return (front_matter_dict, body) or (None, None) if absent/malformed."""
    if not os.path.isfile(path):
        return None, None
    with open(path, encoding="utf-8-sig") as f:
        text = f.read()
    parts = _split_front_matter(text)
    if not parts:
        return None, None
    _, fm, _, body = parts
    fields = {}
    for line in fm.splitlines():
        m = re.match(r"^([A-Za-z0-9_]+)\s*:\s*(.*)$", line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        fields[key] = "" if val in ("~", "null", "") else val
    return fields, body


def set_fm_field(path, field, value):
    """Field-scoped front-matter write; preserves body + every other field.

    Mirrors the Tempo lib's companionSetField so the two stay compatible.
    """
    with open(path, encoding="utf-8-sig") as f:
        text = f.read()
    parts = _split_front_matter(text)
    if not parts:
        raise RuntimeError("no front-matter block in %s" % path)
    head, fm, fence, body = parts
    line = "%s: %s" % (field, value)
    pat = re.compile(r"^%s\s*:.*$" % re.escape(field), re.MULTILINE)
    fm = pat.sub(line, fm, count=1) if pat.search(fm) else fm + "\n" + line
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(head + fm + fence + body)
    os.replace(tmp, path)


def extract_section(body, name):
    """Text of a '## <name>' section, trimmed; '' if absent."""
    if not body:
        return ""
    m = re.search(
        r"^##\s+%s\s*\n(.*?)(?=^##\s|\Z)" % re.escape(name),
        body, re.DOTALL | re.MULTILINE,
    )
    if not m:
        return ""
    text = m.group(1).strip()
    # Drop the template placeholder italic lines.
    text = re.sub(r"^_.*_$", "", text, flags=re.MULTILINE).strip()
    return text


# ------------------------------------------------------------------- roadmap

def load_items():
    """Rows of SYNC_TYPES, each enriched with companion fields + issue number."""
    with open(ROADMAP, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    items = [r for r in rows if r["Type"] in SYNC_TYPES]
    id_to_key = {r["Id"]: r["Key"] for r in rows}
    for it in items:
        fm, body = read_companion(companion_path(it))
        it["_fm"] = fm or {}
        it["_body"] = body or ""
        num = ""
        if (it["_fm"].get("externalSystem") == EXTERNAL_SYSTEM):
            num = it["_fm"].get("externalId", "")
        it["_issue"] = int(num) if str(num).isdigit() else None
    return items, id_to_key


def children_of(item, items):
    return [c for c in items if c["ParentId"] == item["Id"]]


def depends_numbers(item, items, id_to_key, num_by_key):
    out = []
    for dep_id in filter(None, (item["DependsOn"] or "").split(",")):
        key = id_to_key.get(dep_id.strip())
        if key and num_by_key.get(key):
            out.append((key, num_by_key[key]))
    return out


# ------------------------------------------------------------------- bodies

FOOTER_HELP = (
    "## How to help\n\n"
    "Comment to claim, then open a PR that references this issue "
    "(put `Closes #%d` in the PR description). See `CONTRIBUTING.md`. "
    "The maintainer owns `roadmap.csv`, so your PR needs no roadmap edits.\n"
)


def _hours(fm):
    mins = fm.get("estimateMinutes", "")
    if str(mins).isdigit():
        return " . estimate ~%dh" % round(int(mins) / 60)
    return ""


def _synced_header(item):
    rel = os.path.relpath(companion_path(item), ROOT).replace(os.sep, "/")
    return (
        "> **Synced from the TraceYield roadmap** (`%s`). The source of truth is "
        "`roadmap.csv` on `main`; this issue mirrors it and is maintained by "
        "`scripts/sync-issues.py`.\n" % rel
    )


def build_body(item, items, id_to_key, num_by_key):
    parents = [p for p in items if p["Id"] == item["ParentId"]]
    goal = extract_section(item["_body"], "Goal") or item["Title"]
    ac = extract_section(item["_body"], "Acceptance Criteria")
    lines = [_synced_header(item), ""]

    rel_bits = []
    if parents and num_by_key.get(parents[0]["Key"]):
        rel_bits.append("Part of #%d (%s **%s**)." % (
            num_by_key[parents[0]["Key"]], parents[0]["Key"], parents[0]["Title"]))
    for key, n in depends_numbers(item, items, id_to_key, num_by_key):
        rel_bits.append("Depends on #%d (%s)." % (n, key))
    if rel_bits:
        lines += [" ".join(rel_bits), ""]

    lines += ["## Goal", "", goal, ""]
    if ac:
        lines += ["## Acceptance Criteria", "", ac, ""]

    kids = [c for c in children_of(item, items) if num_by_key.get(c["Key"])]
    if kids:
        label = "Child stories" if item["Type"] == "feature" else "Children"
        lines += ["## %s" % label, ""]
        for c in kids:
            box = "x" if c["Status"] in CLOSED_STATUSES else " "
            lines.append("- [%s] #%d -- %s %s" % (
                box, num_by_key[c["Key"]], c["Key"], c["Title"]))
        lines.append("")

    if item["Type"] == "story":
        lines += [FOOTER_HELP % num_by_key.get(item["Key"], 0), ""]

    lines += [
        "---",
        "<sub>Tempo: `%s` . %s . status **%s**%s</sub>" % (
            item["Key"], item["Type"], item["Status"], _hours(item["_fm"])),
    ]
    return "\n".join(lines)


# ------------------------------------------------------------------- labels

def desired_labels(item):
    labels = ["type:%s" % item["Type"], "status:%s" % item["Status"]]
    if item["Type"] == "story" and item["Status"] not in CLOSED_STATUSES:
        labels.append(HELP_LABEL)
    return labels


def owned(label):
    return label.startswith("type:") or label.startswith("status:") or label == HELP_LABEL


def ensure_labels(repo, labels, ensured):
    for name in labels:
        if name in ensured:
            continue
        if name.startswith("type:"):
            color = TYPE_COLORS.get(name.split(":", 1)[1], "ededed")
        elif name.startswith("status:"):
            color = STATUS_COLOR
        else:
            color = "008672"
        gh(["label", "create", name, "--repo", repo, "--color", color, "--force"], check=False)
        ensured.add(name)


# ------------------------------------------------------------------- title / num

def issue_title(item):
    return "[%s] %s" % (item["Key"], item["Title"])


def _num_from_url(out):
    m = re.search(r"/(\d+)\s*$", out.strip())
    return int(m.group(1)) if m else None


# ------------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(description="Mirror the Tempo roadmap to GitHub Issues.")
    ap.add_argument("--repo", help="owner/name (default: git origin remote)")
    ap.add_argument("--apply", action="store_true", help="perform changes (default: dry run)")
    args = ap.parse_args(argv)

    if not have_gh():
        raise SystemExit("the `gh` CLI is required and was not found on PATH")
    repo = args.repo or detect_repo()
    apply = args.apply

    items, id_to_key = load_items()
    num_by_key = {it["Key"]: it["_issue"] for it in items if it["_issue"]}

    # Pass A -- ensure an issue exists for every OPEN, unmapped item.
    to_create = [it for it in items
                 if it["_issue"] is None and it["Status"] not in CLOSED_STATUSES]
    ensured = set()
    print("== plan for %s (%s) ==" % (repo, "APPLY" if apply else "dry run"))
    for it in to_create:
        print("  CREATE  %-10s %s" % (it["Key"], issue_title(it)))
        if apply:
            out = gh(["issue", "create", "--repo", repo,
                      "--title", issue_title(it),
                      "--body", "Syncing from the TraceYield roadmap..."])
            n = _num_from_url(out)
            if not n:
                raise RuntimeError("could not parse new issue number from: %r" % out)
            num_by_key[it["Key"]] = n
            it["_issue"] = n
            p = companion_path(it)
            set_fm_field(p, "externalSystem", EXTERNAL_SYSTEM)
            set_fm_field(p, "externalId", str(n))
            set_fm_field(p, "externalUrl", "https://github.com/%s/issues/%d" % (repo, n))
            print("          -> #%d, wrote external* to %s"
                  % (n, os.path.relpath(p, ROOT).replace(os.sep, "/")))

    # Pass B -- reconcile content, labels, and open/closed state for everything mapped.
    mapped = [it for it in items if num_by_key.get(it["Key"])]
    for it in mapped:
        n = num_by_key[it["Key"]]
        want_closed = it["Status"] in CLOSED_STATUSES
        verb = "CLOSE " if want_closed else "UPDATE"
        print("  %s  %-10s #%d" % (verb, it["Key"], n))
        if not apply:
            continue
        body = build_body(it, items, id_to_key, num_by_key)
        want = desired_labels(it)
        ensure_labels(repo, want, ensured)
        gh(["issue", "edit", str(n), "--repo", repo,
            "--title", issue_title(it), "--body-file", "-"], input_text=body)
        # Reconcile owned labels: add desired, remove owned-but-unwanted.
        meta = json.loads(gh(["issue", "view", str(n), "--repo", repo,
                              "--json", "labels,state"]))
        have = {l["name"] for l in meta["labels"]}
        add = [x for x in want if x not in have]
        remove = [x for x in have if owned(x) and x not in want]
        for x in add:
            gh(["issue", "edit", str(n), "--repo", repo, "--add-label", x], check=False)
        for x in remove:
            gh(["issue", "edit", str(n), "--repo", repo, "--remove-label", x], check=False)
        state = meta["state"].upper()
        if want_closed and state == "OPEN":
            gh(["issue", "close", str(n), "--repo", repo, "--comment",
                "Released in the TraceYield roadmap (status: %s)." % it["Status"]], check=False)
        elif not want_closed and state == "CLOSED":
            gh(["issue", "reopen", str(n), "--repo", repo], check=False)

    skipped = [it for it in items
               if it["_issue"] is None and it["Status"] in CLOSED_STATUSES]
    print("\nsummary: %d create, %d reconcile, %d skipped (released, never mirrored)"
          % (len(to_create), len(mapped), len(skipped)))
    if not apply:
        print("dry run -- re-run with --apply to make these changes.")


if __name__ == "__main__":
    main()
