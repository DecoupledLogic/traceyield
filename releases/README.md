# Release pages

A **plain-language, user-facing announcement per release** — born when work merges
to main. Where a PR page (`../prs/`) explains a change technically, a release page
turns one or more merged PRs into a single benefit-shaped announcement. Published
at `https://decoupledlogic.github.io/traceyield/releases/`.

The `<slug>/release.md` records are the **source of truth**; `pages.py` renders
them into `site/public/releases/*.html` (git-ignored build output).

## Record schema

```yaml
slug: traceyield-docs-site               # folder name; kebab-case
title: TraceYield now has a real home on the web   # benefit-shaped headline
theme: One line supporting the headline.
status: announced                        # draft | ready | announced | held
released: 2026-07-13                       # ISO date it shipped (or omit)
prs:                                      # the PRs this release bundles
  - 36
  - 37
  - 38
userVisible: true                        # is there a user-facing benefit?
summary: One line for the index.
```

Below the frontmatter, the body is the announcement in **user language** — lead
with what a user can now do, no service names, no internal ids. The generated page
links back to each bundled PR's technical page under "The technical detail".

A PR listed in `prs` that has no `pr-<n>.md` record links out to GitHub instead of
a dead local link.

## Generating

```bash
python pages.py                # render every record -> site/public/
python pages.py --merge 37     # react to PR 37 merging (finalize/scaffold), then render
```

`--merge <n>`:

- If a release already lists PR `<n>`, it flips that release to `announced` and
  stamps `released` with the merge date.
- If **no** release lists it, it scaffolds a **draft** `release.md` for it (with
  `userVisible: false`) that you then rewrite in user language and bundle with any
  related PRs. Curate or delete drafts you don't want to announce.

## CI

The merge branch of **`../.github/workflows/pr-pages.yml`** runs `--merge <n>` when
a PR closes as merged, commits the release record to `main`, and the
`deploy-site.yml` deploy publishes it. See `../prs/README.md` for the one-time PAT
and Pages setup shared by both.
