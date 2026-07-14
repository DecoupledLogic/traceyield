// Build-time remark plugin: drop the leading "N. " ordinal from a doc's H1.
//
// The canonical guides are titled "# 1. Overview...", "# 7. Cost-optimization
// playbook", etc. Those numbers imply a linear 1-8 reading order that the
// grouped site nav (Get started / Optimize / Operate / Concepts) no longer
// follows, so on the site we render just the title. The source Markdown under
// docs/ stays pristine -- the numbering still reads correctly on GitHub.

export default function remarkStripHeadingNumbers() {
  return (tree) => {
    for (const node of tree.children || []) {
      if (node.type !== 'heading' || node.depth !== 1) continue;
      const first = node.children && node.children[0];
      if (first && first.type === 'text') {
        first.value = first.value.replace(/^\s*\d+\.\s+/, '');
      }
    }
  };
}
