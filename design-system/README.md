# TraceYield Design System

The shared visual contract for TraceYield — the brand-aligned tokens and components the
report (and any future UI) renders from. This is the *why-it-looks-this-way* companion to
[`../docs/brand/brand-guidelines.html`](../docs/brand/brand-guidelines.html) (the poster/spec).

```
design-system/
├── index.html        # shell: sidebar nav + workspace stage + theme toggle
├── styles.css        # @imports tokens.css + components.css; adds this page's chrome
├── tokens.css        # SHARED SOURCE OF TRUTH — CSS custom properties (+ report bridge)
├── components.css    # reusable product components, all built on the tokens
├── js/
│   ├── tokens.js      # tokens AS DATA → window.TDS.tokens (swatches/tables render from it)
│   ├── components.js  # component builders (functions → HTML) → window.TDS.components
│   └── app.js         # section renderers, sidebar routing, theme toggle, copy-to-clipboard
└── README.md          # this file
```

Open `index.html` and use the left nav to browse each section (Overview, Color, Typography,
Shape, Iconography, Components, Data viz, Report bridge). Click a swatch to copy its hex; expand
a specimen to copy its markup; the top-right toggle cycles dark → light → auto.

Following the *tokens-as-data, components-as-functions* pattern: add a `SECTION` entry in
`js/app.js` and it appears in the nav automatically — the page is the first consumer of the
tokens it documents.

## Brand basis

- **Idea:** *"Tokens are the meter, not the mission."* Closed-loop, data-first, trustworthy.
- **Gradient (the logo mark):** `#05B98A → #10B7D8 → #258CF8 → #7338FF` (teal→cyan→blue→violet).
- **Accent:** Yield Teal `#12C99A` leads; Sky Blue and Violet support.
- **Type:** Inter for UI; monospace for tokens / IDs / timestamps. Wordmark is Poppins (baked into the logo SVGs).
- **Logo assets:** `../docs/brand/logo-mark-{light,dark}.svg` (suffix = the background it sits on), `mark.svg` (icon only).

## Applying it to `report.py`

The report already uses CSS variables at the top of `HTML_TMPL` (`--bg`, `--panel`, `--panel2`,
`--ink`, `--mut`, `--line`, `--accent`). `tokens.css` ends with a **REPORT BRIDGE** block that
remaps exactly those names onto brand tokens, so restyling is a one-block swap.

**Step 1 — swap the `:root` block.** In `report.py`, replace the current line 5:

```css
:root{--bg:#0f1117;--panel:#171a23;--panel2:#1e222e;--ink:#e7e9ee;--mut:#8b90a0;--line:#2a2f3d;--accent:#6b8afd;}
```

with the brand values (dark theme). The report stays self-contained (no external file), so inline
the resolved hexes:

```css
:root{
  --bg:#0B1220;      /* Deep Navy   (--ty-bg)      */
  --panel:#141B2B;   /* Surface     (--ty-surface) */
  --panel2:#1B2436;  /* Surface 2   (--ty-surface-2) */
  --ink:#E8ECF3;     /* Text        (--ty-text)    */
  --mut:#94A0B4;     /* Muted       (--ty-text-muted) */
  --line:#263149;    /* Border      (--ty-border)  */
  --accent:#12C99A;  /* Yield Teal  (--ty-accent)  */
}
```

That alone brings the panels, cards, segmented control, tables, and accent onto the brand.

**Step 2 — chart series colors.** The SVG charts pick series colors in JS. Point them at the
brand categorical order (accent family first) instead of ad-hoc blues:

```js
const SERIES = ['#12C99A','#258CF8','#7338FF','#10B7D8','#F0A35E','#E5709B']; // --ty-c1..--ty-c6
```

Use `SERIES[i % SERIES.length]` wherever a per-series/per-slice color is assigned (donut slices,
line strokes, legend swatches, `.hfill` fills). For the *leading* series or a hero bar, the
gradient `linear-gradient(120deg,#05B98A,#10B7D8 42%,#258CF8 70%,#7338FF)` reads as the brand.

**Step 3 — delta semantics (optional but on-brand).** Cost *rising* is the thing to notice, so
`.delta.up` uses warn amber `#F0A35E` and `.delta.down` uses ok green `#34D399` — already the
report's convention; keep it.

**Step 4 — brand the header (optional).** Add the mark + wordmark lockup above the `<h1>` using the
`.ty-brandbar` pattern (inline the `mark.svg` geometry as the report is a single self-contained file).

Because every value traces back to a token, changing a brand color later is a one-line edit here,
then a re-paste into `report.py` — or, longer-term, have `report.py` read these values so the two
never drift.

## Rules of thumb

- One accent leads per view; the others support. Don't rainbow a layout.
- Status color is **never** the only signal — always pair with a label or icon.
- The gradient is for identity and the leading series, not for text or large fills behind copy.
- Monospace every number that is an ID, token count, cost, or timestamp (tabular figures).
