import { defineCollection } from 'astro:content';
import { glob } from 'astro/loaders';

// Single source of truth: the docs site renders the repo's canonical Markdown
// directly from docs/ -- there are no copies under the site. The id -> slug /
// title / group mapping lives in docs-nav.mjs, and the build-time link rewriting
// in remark-doc-links.mjs. The source files have no front-matter, so there is no
// schema to validate here.
const docs = defineCollection({
  loader: glob({
    base: '../docs',
    pattern: ['guides/*.md', 'traceyield-framework.md', 'token-mechanics-and-insights.md'],
  }),
});

export const collections = { docs };
