# PR pages

A **technical change note per pull request** — born when the PR opens, kept in
sync as commits land and its lifecycle changes. Published as its own section of
the docs site at `https://decoupledlogic.github.io/traceyield/prs/`.

The `pr-<n>.md` records here are the **source of truth**. `pages.py` renders them
into `site/public/prs/*.html`, which is **build output** (git-ignored, regenerated
in CI) — never hand-edit the HTML.

## Record schema

Each `pr-<n>.md` is YAML frontmatter plus a free-form markdown body (the technical
note). Curated fields are written once and preserved; objective fields are
refreshed from GitHub.

```yaml
pr: 37                                   # PR number (also the file name)
url: https://github.com/DecoupledLogic/traceyield/pull/37
branch: feat/site-deploy                 # head branch
title: Deploy the Astro docs site        # human display title (curated)
status: merged                           # see values below
author: charleslbryant                   # GitHub handle
opened: 2026-07-13                        # ISO date opened
mergedAt: 2026-07-13                      # ISO date merged (only when merged)
summary: One line for the index and the page lede.
statusNote: Optional one-line reviewer-facing note (curated).   # optional
```

Status values: `draft`, `needs-review`, `changes-requested`, `approved`,
`blocked`, `merged`, `closed`. A curated open status (`blocked` / `approved` /
`changes-requested`) is never silently downgraded by a refresh.

The body renders as the "technical change note": headings, paragraphs, lists,
`- [ ]` task lists, fenced code, and inline `code` / **bold** / [links](#).

## Generating

```bash
python pages.py                # render every record -> site/public/ (no network)
python pages.py --refresh      # also pull live PR state + commit history from gh
python pages.py --pr 37        # create/update one record from gh, then render
```

`--pr <n>` seeds a new record from `gh` (title, summary, body) or refreshes an
existing one's objective fields. It is idempotent and only writes on a real change.

## How it stays in sync (CI)

- **`.github/workflows/pr-pages.yml`** runs on every `pull_request` event: it runs
  `python pages.py --pr <n>` and commits the changed record to `main`. On merge it
  also runs `--merge <n>` to finalize/scaffold the release (see `../releases/`).
- The commit to `main` triggers **`deploy-site.yml`**, which regenerates the pages
  (with live commit history) and publishes them.

### One-time setup

GitHub Pages only publishes from `main`, so `pr-pages.yml` commits records to the
branch-protected `main`. That needs an admin token (this repo keeps
`enforce_admins` OFF for exactly this, like the Tempo `roadmap.csv` writes):

1. Create a **fine-grained PAT** with **Contents: write** on this repo, owned by an
   admin.
2. Add it as the repo secret **`PAGES_PUSH_TOKEN`**.

Pages must already be on the **GitHub Actions** build type (set for the docs site).
