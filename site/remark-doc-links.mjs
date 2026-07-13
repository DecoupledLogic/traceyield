// Build-time remark plugin: rewrite the repo-relative Markdown links inside the
// canonical docs so they work as a website. A link whose target is a doc we
// publish becomes its on-site route (/docs/<slug>); any other repo doc becomes a
// link to its source on GitHub. External links, mailto:, and #anchors are left
// untouched.
//
// This is the ONLY place doc links are rewritten. The Markdown under docs/ stays
// pristine -- its ./NN-name.md links keep working when read on GitHub -- and the
// site derives correct routes from it at build time. See docs-nav.mjs for the
// id -> slug map that this and the docs pages share.

import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { flatDocs } from './src/docs-nav.mjs';

const SITE_DIR = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(SITE_DIR, '..');
const DOCS_DIR = path.join(REPO_ROOT, 'docs');
const GH_BLOB = 'https://github.com/DecoupledLogic/traceyield/blob/main';

// content-collection id (path under docs/, no .md) -> site slug
const ID_TO_SLUG = new Map(flatDocs().map((d) => [d.id, d.slug]));

const idOf = (abs) =>
  path.relative(DOCS_DIR, abs).replace(/\.md$/i, '').split(path.sep).join('/');
const repoRelOf = (abs) => path.relative(REPO_ROOT, abs).split(path.sep).join('/');

function eachLink(node, fn) {
  if (!node || typeof node !== 'object') return;
  if (node.type === 'link') fn(node);
  if (Array.isArray(node.children)) for (const child of node.children) eachLink(child, fn);
}

export default function remarkDocLinks() {
  return (tree, file) => {
    const fileAbs = (file.history && file.history[0]) || file.path;
    if (!fileAbs) return;
    const dir = path.dirname(fileAbs);
    eachLink(tree, (node) => {
      const url = node.url || '';
      // Leave external (scheme:), in-page (#anchor), and already-absolute (/path) links.
      if (/^([a-z][a-z0-9+.-]*:|#|\/)/i.test(url)) return;
      const [rel, hash] = url.split('#');
      if (!/\.md$/i.test(rel)) return;
      const targetAbs = path.resolve(dir, rel);
      const suffix = hash ? `#${hash}` : '';
      const id = idOf(targetAbs);
      node.url = ID_TO_SLUG.has(id)
        ? `/docs/${ID_TO_SLUG.get(id)}${suffix}`
        : `${GH_BLOB}/${repoRelOf(targetAbs)}${suffix}`;
    });
  };
}
