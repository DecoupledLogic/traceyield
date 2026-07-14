#!/usr/bin/env python3
"""Tests for pages.py, the PR / release static-page generator.

pages.py is a repo-root script (a sibling of report.py), not part of the
installed traceyield package, so this test imports it directly. test_layout.py
only forbids importing the `report` / `canonical` compat shims, so importing
`pages` here is allowed.

The tests are deterministic and network-free: the two functions that shell out
to `gh` (register_pr / on_merge) are exercised with pages.gh_json monkeypatched
to a fixed payload, and the record/output directories are redirected to a temp
dir so nothing touches the real prs/ or releases/ trees.
"""
import os
import tempfile
import unittest

import pages


class TestFrontmatterParsing(unittest.TestCase):
    def test_scalars_and_block_list_and_body(self):
        text = (
            "---\n"
            "pr: 7\n"
            "title: One Entitlement Per Subscription\n"
            "userVisible: true\n"
            "prs:\n"
            "  - subscriptions-service#3\n"
            "  - entitlements-service#2\n"
            "---\n"
            "\n"
            "Body line one.\n"
        )
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "r.md")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(text)
            fm, body = pages.parse_record(p)
        self.assertEqual(fm["pr"], "7")
        self.assertEqual(fm["title"], "One Entitlement Per Subscription")
        self.assertIs(fm["userVisible"], True)               # boolean coercion
        self.assertEqual(fm["prs"], ["subscriptions-service#3",
                                     "entitlements-service#2"])  # block list
        self.assertEqual(body, "Body line one.")

    def test_quoted_scalar_strips_quotes(self):
        text = '---\nsummary: "a: colon inside"\n---\nx\n'
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "r.md")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(text)
            fm, _ = pages.parse_record(p)
        self.assertEqual(fm["summary"], "a: colon inside")

    def test_missing_frontmatter_returns_body_only(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "r.md")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("just prose, no fence\n")
            fm, body = pages.parse_record(p)
        self.assertEqual(fm, {})
        self.assertEqual(body, "just prose, no fence")


class TestRecordRoundTrip(unittest.TestCase):
    def test_dump_then_parse_is_identity(self):
        fm = {"pr": 7, "title": "One Per Subscription", "status": "merged",
              "userVisible": True, "prs": ["36", "37"],
              "summary": "closes: the race with an index"}
        body = "## Heading\n\nA paragraph.\n"
        text = pages.dump_record(fm, body, pages.PR_FIELD_ORDER)
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "r.md")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(text)
            fm2, body2 = pages.parse_record(p)
        # scalars come back as strings; compare on stringified values
        self.assertEqual(fm2["title"], "One Per Subscription")
        self.assertEqual(fm2["status"], "merged")
        self.assertIs(fm2["userVisible"], True)
        self.assertEqual(fm2["prs"], ["36", "37"])
        self.assertEqual(fm2["summary"], "closes: the race with an index")
        self.assertEqual(body2, "## Heading\n\nA paragraph.")

    def test_colon_value_is_quoted_so_it_round_trips(self):
        # a raw "key: a: b" line would be misparsed; the dumper must quote it
        line = pages._dump_scalar("a: b")
        self.assertTrue(line.startswith('"') and line.endswith('"'))


class TestMarkdownSubset(unittest.TestCase):
    def test_headings_paragraph_and_inline(self):
        html = pages.render_md("## Title\n\nSome **bold** and `code` and a "
                               "[link](https://x.test).")
        self.assertIn("<h2>Title</h2>", html)
        self.assertIn("<strong>bold</strong>", html)
        self.assertIn("<code>code</code>", html)
        self.assertIn('<a href="https://x.test">link</a>', html)

    def test_task_list_renders_checkboxes(self):
        html = pages.render_md("- [x] done thing\n- [ ] todo thing\n")
        self.assertIn('<ul class="tasklist">', html)
        self.assertIn('<li class="done"><input type="checkbox" disabled checked> done thing</li>', html)
        self.assertIn('<li class="todo"><input type="checkbox" disabled> todo thing</li>', html)

    def test_plain_list_has_no_tasklist_class(self):
        html = pages.render_md("- one\n- two\n")
        self.assertIn("<ul><li>one</li><li>two</li></ul>", html)

    def test_code_fence_is_escaped(self):
        html = pages.render_md("```\n<script>x</script>\n```")
        self.assertIn("<pre><code>&lt;script&gt;x&lt;/script&gt;</code></pre>", html)

    def test_inline_escapes_html(self):
        self.assertIn("&lt;b&gt;", pages._inline("a <b> tag"))


class TestHelpers(unittest.TestCase):
    def test_clean_title_strips_conventional_commit_prefix(self):
        self.assertEqual(pages._clean_title("feat(site): deploy the docs site"),
                         "Deploy the docs site")
        self.assertEqual(pages._clean_title("fix: a thing"), "A thing")
        self.assertEqual(pages._clean_title("Already Clean"), "Already Clean")

    def test_slugify(self):
        self.assertEqual(pages._slugify("Deploy the Docs Site!"), "deploy-the-docs-site")


class TestRendering(unittest.TestCase):
    def test_pr_page_has_status_pill_and_github_button(self):
        fm = {"pr": 37, "title": "Deploy the site", "status": "merged",
              "url": "https://github.com/o/r/pull/37", "summary": "s"}
        html = pages.render_pr_page(fm, "body")
        self.assertIn('class="pill accent"', html)      # merged -> accent pill
        self.assertIn(">Merged</span>", html)
        self.assertIn('href="https://github.com/o/r/pull/37"', html)
        self.assertIn("site-header", html)              # site chrome present

    def test_pr_page_commit_table_when_present(self):
        fm = {"pr": 1, "title": "t", "status": "merged",
              "_commits": [("2026-07-13", "abc1234", "msg", "alice")]}
        html = pages.render_pr_page(fm, "b")
        self.assertIn("Commit history", html)
        self.assertIn("abc1234", html)

    def test_release_links_local_page_when_record_exists_else_github(self):
        fm = {"slug": "rel", "title": "Rel", "status": "announced",
              "prs": ["37", "99"]}
        pr_index = {"37": "Deploy the site"}      # 99 has no record
        html = pages.render_release_page(fm, "body", pr_index)
        self.assertIn('href="%sprs/pr-37.html"' % pages.BASE_SLASH, html)  # local
        self.assertIn('href="https://github.com/%s/pull/99"' % pages.REPO, html)  # fallback
        self.assertIn("(on GitHub)", html)


class TestRegisterAndMerge(unittest.TestCase):
    """register_pr / on_merge with gh monkeypatched and paths redirected."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        d = self._tmp.name
        self._saved = {k: getattr(pages, k) for k in
                       ("PR_RECORDS", "REL_RECORDS", "SITE_PUBLIC",
                        "PR_OUT", "REL_OUT", "PAGES_CSS", "gh_json")}
        pages.PR_RECORDS = os.path.join(d, "prs")
        pages.REL_RECORDS = os.path.join(d, "releases")
        os.makedirs(pages.PR_RECORDS)
        os.makedirs(pages.REL_RECORDS)

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(pages, k, v)
        self._tmp.cleanup()

    def _fake_gh(self, payload):
        def _gh(args):
            return payload
        pages.gh_json = _gh

    def test_register_creates_record_from_gh(self):
        self._fake_gh({
            "url": "https://github.com/o/r/pull/5", "headRefName": "feat/x",
            "createdAt": "2026-07-10T00:00:00Z", "author": {"login": "bob"},
            "state": "OPEN", "isDraft": False, "title": "feat(x): do the thing",
            "body": "## Summary\n\nDoes the thing well.", "mergedAt": None,
        })
        path, changed = pages.register_pr(5)
        self.assertTrue(changed)
        fm, body = pages.parse_record(path)
        self.assertEqual(fm["branch"], "feat/x")
        self.assertEqual(fm["author"], "bob")
        self.assertEqual(fm["status"], "needs-review")           # open, not draft
        self.assertEqual(fm["title"], "Do the thing")            # cleaned
        self.assertEqual(fm["summary"], "Does the thing well.")  # skips heading
        self.assertIn("Does the thing well.", body)

    def test_register_is_idempotent_and_preserves_curated_status(self):
        self._fake_gh({
            "url": "u", "headRefName": "b", "createdAt": "2026-07-10T00:00:00Z",
            "author": {"login": "bob"}, "state": "OPEN", "isDraft": False,
            "title": "t", "body": "", "mergedAt": None,
        })
        path, _ = pages.register_pr(5)
        # curate: a human sets status to approved
        fm, body = pages.parse_record(path)
        fm["status"] = "approved"
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(pages.dump_record(fm, body, pages.PR_FIELD_ORDER))
        # re-register: objective refresh must NOT clobber the curated status
        _, changed = pages.register_pr(5)
        fm2, _ = pages.parse_record(path)
        self.assertEqual(fm2["status"], "approved")
        self.assertFalse(changed)

    def test_on_merge_scaffolds_draft_release_when_none_references_pr(self):
        self._fake_gh({"state": "MERGED", "mergedAt": "2026-07-13T00:00:00Z",
                       "title": "feat(site): ship it", "url": "u"})
        results = pages.on_merge(9)
        self.assertEqual(len(results), 1)
        path, changed = results[0]
        self.assertTrue(changed)
        fm, _ = pages.parse_record(path)
        self.assertEqual(fm["status"], "draft")
        self.assertEqual(fm["prs"], ["9"])
        self.assertEqual(fm["released"], "2026-07-13")

    def test_on_merge_finalizes_existing_release(self):
        # a curated release bundling PR 9, still held
        rel_dir = os.path.join(pages.REL_RECORDS, "the-release")
        os.makedirs(rel_dir)
        with open(os.path.join(rel_dir, "release.md"), "w", encoding="utf-8") as fh:
            fh.write(pages.dump_record(
                {"slug": "the-release", "title": "The Release", "status": "held",
                 "prs": ["9", "10"]}, "body", pages.REL_FIELD_ORDER))
        self._fake_gh({"state": "MERGED", "mergedAt": "2026-07-13T00:00:00Z",
                       "title": "t", "url": "u"})
        results = pages.on_merge(9)
        self.assertEqual(len(results), 1)
        fm, _ = pages.parse_record(results[0][0])
        self.assertEqual(fm["status"], "announced")
        self.assertEqual(fm["released"], "2026-07-13")

    def test_on_merge_noop_when_pr_not_merged(self):
        self._fake_gh({"state": "OPEN", "mergedAt": None, "title": "t", "url": "u"})
        self.assertEqual(pages.on_merge(9), [])


if __name__ == "__main__":
    unittest.main()
