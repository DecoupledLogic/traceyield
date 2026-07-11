#!/usr/bin/env bash
# check-docs-index.sh
#
# Verify that an indexed docs directory's README.md index is in sync with the
# actual document files: every file is listed, no orphan rows, statuses match,
# and the statistics block is correct. Deterministic; reads files only.
#
# Built for tempo Decision 0017's lesson: status flips and index rows are easy to
# forget. This is the commit-time gate that catches the drift instead of relying
# on memory. Wired into scripts/hooks/pre-commit.
#
# Usage:
#   check-docs-index.sh [--dir <docs-subdir>] [--prefix <H1-prefix>]
#
# Defaults:
#   --dir:    docs/decisions   (the directory holding NNNN-*.md + README.md)
#   --prefix: Decision         (the word after "# " in each file's H1)
#
# Exit codes:
#   0 - index is in sync
#   1 - usage error
#   2 - drift found (missing rows, orphan rows, status mismatch, or bad stats)
#   3 - python3 not available

set -euo pipefail

if ! command -v python3 >/dev/null 2>&1; then
    echo "Error: python3 is required." >&2
    exit 3
fi

DIR="docs/decisions"
PREFIX="Decision"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dir)     DIR="$2"; shift 2 ;;
        --prefix)  PREFIX="$2"; shift 2 ;;
        -h|--help) sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *)         echo "Error: unknown argument: $1" >&2; exit 1 ;;
    esac
done

if [[ ! -d "$DIR" ]]; then
    echo "Error: directory not found: $DIR" >&2
    exit 1
fi
if [[ ! -f "$DIR/README.md" ]]; then
    echo "Error: index not found: $DIR/README.md" >&2
    exit 1
fi

DIR="$DIR" python3 - <<'PY'
import os, re, sys, glob

DIR = os.environ["DIR"]
readme = os.path.join(DIR, "README.md")

STATUS_WORDS = {"Drafted", "Proposed", "Accepted", "Published", "Superseded", "Rejected", "Deprecated", "Archived"}
_STATUS_LC = {w.lower(): w for w in STATUS_WORDS}  # case-insensitive lookup -> canonical form

def norm_status(text):
    """First recognised status word in a blob of text (case-insensitive, returned
    in canonical title-case), else the first bare word."""
    for w in re.findall(r"[A-Za-z]+", text):
        if w.lower() in _STATUS_LC:
            return _STATUS_LC[w.lower()]
    m = re.search(r"[A-Za-z]+", text)
    return m.group(0) if m else ""

def file_status(path):
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    # YAML front-matter form: status: Accepted
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                break
            m = re.match(r"\s*status:\s*(.+?)\s*$", lines[i])
            if m:
                return norm_status(m.group(1))
    # Inline bold-label form (older MADR-style header): **Status:** accepted
    for ln in lines:
        m = re.match(r"\s*\*\*status:?\*\*:?\s*(.+?)\s*$", ln, re.IGNORECASE)
        if m:
            return norm_status(m.group(1))
    # Prose form: "## Status" then the next non-empty line
    for i, ln in enumerate(lines):
        if re.match(r"#+\s*Status\s*$", ln.strip()):
            for j in range(i + 1, len(lines)):
                if lines[j].strip():
                    return norm_status(lines[j])
    return ""

# ── Collect files ──────────────────────────────────────────────────────────
files = {}  # number -> status
for path in sorted(glob.glob(os.path.join(DIR, "*.md"))):
    base = os.path.basename(path)
    if base.lower() == "readme.md":
        continue
    m = re.match(r"^(\d{3,4})-", base)
    if not m:
        continue
    files[m.group(1)] = file_status(path)

# ── Parse README index rows: | NNNN | [title](..) | Status | date | ──────────
with open(readme, encoding="utf-8") as f:
    readme_lines = f.read().splitlines()

index = {}   # number -> status
for ln in readme_lines:
    m = re.match(r"^\|\s*(\d{3,4})\s*\|.*?\|\s*([^|]+?)\s*\|", ln)
    if m:
        index[m.group(1)] = norm_status(m.group(2))

# ── Parse stats block: | Accepted | 13 |  and  | **Total** | **17** | ────────
stats = {}
total_claimed = None
for ln in readme_lines:
    mt = re.match(r"^\|\s*\*\*Total\*\*\s*\|\s*\*\*(\d+)\*\*\s*\|", ln)
    if mt:
        total_claimed = int(mt.group(1)); continue
    ms = re.match(r"^\|\s*([A-Za-z]+)\s*\|\s*(\d+)\s*\|", ln)
    if ms and ms.group(1) in STATUS_WORDS:
        stats[ms.group(1)] = int(ms.group(2))

# ── Checks ───────────────────────────────────────────────────────────────────
errors = []

missing = sorted(set(files) - set(index))
if missing:
    errors.append("Files not listed in README index: " + ", ".join(missing))

orphan = sorted(set(index) - set(files))
if orphan:
    errors.append("README index rows with no matching file: " + ", ".join(orphan))

for num in sorted(set(files) & set(index)):
    if files[num] and index[num] and files[num] != index[num]:
        errors.append(f"{num}: file says '{files[num]}' but index says '{index[num]}'")

# Stats: recompute from files
from collections import Counter
actual = Counter(s for s in files.values() if s)
for status, count in sorted(actual.items()):
    if status in stats and stats[status] != count:
        errors.append(f"Stats: '{status}' says {stats[status]} but {count} files have that status")
    elif status not in stats:
        errors.append(f"Stats: missing a '{status}' row (should be {count})")
for status in sorted(set(stats) - set(actual)):
    errors.append(f"Stats: '{status}' row says {stats[status]} but no files have that status")
if total_claimed is not None and total_claimed != len(files):
    errors.append(f"Stats: Total says {total_claimed} but {len(files)} files exist")

if errors:
    print(f"docs index out of sync in {DIR}:", file=sys.stderr)
    for e in errors:
        print(f"  - {e}", file=sys.stderr)
    print(f"\nFix {DIR}/README.md (or run /docs:sync from the tempo repo), then re-commit.", file=sys.stderr)
    sys.exit(2)

print(f"ok: {DIR}/README.md is in sync ({len(files)} docs)")
PY
