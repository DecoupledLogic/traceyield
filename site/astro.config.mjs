// @ts-check
import { defineConfig } from 'astro/config';
import remarkDocLinks from './remark-doc-links.mjs';
import remarkStripHeadingNumbers from './remark-strip-heading-numbers.mjs';
import { BASE } from './base.config.mjs';

// The site is a GitHub project page served under /traceyield/, so it builds with
// that base path (see base.config.mjs). Components link via
// import.meta.env.BASE_URL and the remark rewriter prefixes /docs/ routes with
// BASE, so everything resolves under the subpath. `npm run dev` / `preview` also
// serve under /traceyield/ locally, matching production.
export default defineConfig({
  site: 'https://decoupledlogic.github.io',
  base: BASE,
  markdown: {
    // Rewrite the canonical docs' repo-relative .md links to site routes /
    // GitHub source at build time (see remark-doc-links.mjs).
    remarkPlugins: [remarkDocLinks, remarkStripHeadingNumbers],
    shikiConfig: { theme: 'github-dark-default' },
  },
});
