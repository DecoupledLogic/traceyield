// @ts-check
import { defineConfig } from 'astro/config';

// Prototype config. `base` is left at root so the local `npm run preview`
// serves at http://localhost:4321/ . When we deploy to the project Pages path
// (decoupledlogic.github.io/traceyield) we add `base: '/traceyield'` and switch
// links to import.meta.env.BASE_URL -- or point a custom domain at root and
// leave this as-is.
export default defineConfig({
  site: 'https://decoupledlogic.github.io',
  markdown: {
    shikiConfig: { theme: 'github-dark-default' },
  },
});
