// Information architecture for the docs site. SINGLE SOURCE of the mapping from
// a repo doc (its content-collection id, relative to /docs, no extension) to its
// site slug, sidebar title, and group. Imported by both the docs pages and the
// build-time link rewriter (remark-doc-links.mjs) so the two can never drift.
//
// To surface another doc: add it to the glob in content.config.ts and add a row
// here. To regroup or reorder: just move rows -- order here is the sidebar order.

export const DOCS_NAV = [
  {
    group: 'Get started',
    items: [
      { id: 'guides/01-overview', slug: 'overview', title: 'Overview' },
      { id: 'guides/02-install', slug: 'install', title: 'Install & first run' },
      { id: 'guides/03-using-the-report', slug: 'using-the-report', title: 'Using the report' },
    ],
  },
  {
    group: 'Optimize',
    items: [
      { id: 'guides/07-cost-optimization-playbook', slug: 'cost-optimization', title: 'Cost-optimization playbook' },
      { id: 'guides/06-feature-reference', slug: 'feature-reference', title: 'Feature reference' },
    ],
  },
  {
    group: 'Operate',
    items: [
      { id: 'guides/04-updating', slug: 'updating', title: 'Keeping it updated' },
      { id: 'guides/08-daily-run-automation', slug: 'daily-run-automation', title: 'Daily runs & automation' },
      { id: 'guides/05-troubleshooting', slug: 'troubleshooting', title: 'Troubleshooting' },
    ],
  },
  {
    group: 'Concepts',
    items: [
      { id: 'traceyield-framework', slug: 'framework', title: 'The TraceYield framework' },
      { id: 'token-mechanics-and-insights', slug: 'token-mechanics', title: 'Token mechanics & insights' },
    ],
  },
];

/** Flatten the grouped nav into a single ordered list of items. */
export function flatDocs() {
  return DOCS_NAV.flatMap((g) => g.items);
}
