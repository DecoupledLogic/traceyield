// Single source for the site's base path. TraceYield's site is a GitHub project
// page served under /<repo>/, so it deploys under this subpath. astro.config
// sets `base` from here (which drives import.meta.env.BASE_URL in components),
// and remark-doc-links.mjs prefixes on-site /docs/ routes with it. Change it in
// this one place -- or set it to '/' if a custom domain serves the site at root.
export const BASE = '/traceyield';
