#!/usr/bin/env bash
# install-hooks.sh — point this clone's git hooks at the committed scripts/hooks.
#
# core.hooksPath is a per-clone local git setting (it lives in .git/config, which
# is never committed), so every new clone / machine must run this once to activate
# the pre-commit docs-index check. traceyield is multi-machine, so this is the
# reproducible install step.
#
# Usage:  bash scripts/install-hooks.sh
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

git config core.hooksPath scripts/hooks
echo "ok: core.hooksPath set to scripts/hooks (pre-commit docs-index check active)"
