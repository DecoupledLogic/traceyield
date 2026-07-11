/*
 * TraceYield tokens AS DATA.
 * Mirrors tokens.css so swatches/tables/specimens render from real values.
 * `varName` strings point back at the CSS variables that actually do the styling.
 */
(function (w) {
  "use strict";
  var TDS = w.TDS || (w.TDS = {});

  TDS.esc = function (s) {
    return String(s).replace(/[&<>"']/g, function (m) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[m];
    });
  };

  // Inline hexagon mark (mark.svg geometry) — recolored via the shared gradient.
  TDS.mark = function () {
    return '<svg viewBox="0 0 1254 1254" width="100%" height="100%" xmlns="http://www.w3.org/2000/svg">'
      + '<defs><linearGradient id="tdsmark" x1="300" y1="280" x2="900" y2="985" gradientUnits="userSpaceOnUse">'
      + '<stop offset="0" stop-color="#05b98a"/><stop offset="0.46" stop-color="#10b7d8"/>'
      + '<stop offset="0.72" stop-color="#258cf8"/><stop offset="1" stop-color="#7338ff"/></linearGradient></defs>'
      + '<path d="M627 228 L952 424 L952 806 L627 1002 L301 806 L301 424 Z" fill="none" stroke="url(#tdsmark)" stroke-width="70" stroke-linecap="round" stroke-linejoin="round"/>'
      + '<g fill="url(#tdsmark)"><rect x="432" y="603" width="77" height="165" rx="38.5"/>'
      + '<rect x="587" y="452" width="77" height="316" rx="38.5"/><rect x="741" y="571" width="77" height="197" rx="38.5"/></g></svg>';
  };

  // Line-icon set (2px stroke, rounded). Used in nav + iconography section.
  var I = {
    grid: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
    droplet: '<circle cx="6" cy="6" r="2.4"/><circle cx="6" cy="15.5" r="2.4"/><circle cx="15.5" cy="10.5" r="2.4"/><path d="M8 7l6 2.4M8 14.4l6-2.4"/>',
    type: '<path d="M4 7V5h16v2M9 19h6M12 5v14"/>',
    shape: '<rect x="3" y="3" width="18" height="18" rx="3"/><path d="M3 9h18"/>',
    bars: '<rect x="3" y="11" width="4" height="9" rx="1"/><rect x="10" y="4" width="4" height="16" rx="1"/><rect x="17" y="8" width="4" height="12" rx="1"/>',
    search: '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/>',
    trend: '<path d="M3 17l6-6 4 4 7-8"/><path d="M17 7h4v4"/>',
    compass: '<circle cx="12" cy="12" r="9"/><path d="M15.5 8.5l-2 5-5 2 2-5z"/>',
    loop: '<path d="M21 12a9 9 0 1 1-3-6.7"/><path d="M21 4v5h-5"/>',
    layers: '<path d="M12 3l8 4-8 4-8-4z"/><path d="M4 11l8 4 8-4M4 15l8 4 8-4"/>',
    donut: '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3"/>',
    doc: '<path d="M6 3h9l4 4v14H6z"/><path d="M14 3v5h5M9 13h6M9 17h6"/>',
  };
  TDS.icon = function (name, cls) {
    return '<svg class="' + (cls || "") + '" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
      + 'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' + (I[name] || "") + "</svg>";
  };

  TDS.tokens = {
    gradient: { css: "--ty-gradient", stops: ["#05B98A", "#10B7D8", "#258CF8", "#7338FF"] },

    brand: [
      { name: "Yield Teal", hex: "#12C99A", varName: "--ty-teal" },
      { name: "Sky Blue", hex: "#258CF8", varName: "--ty-sky" },
      { name: "Violet", hex: "#7338FF", varName: "--ty-violet" },
      { name: "Deep Navy", hex: "#0B1220", varName: "--ty-navy" },
      { name: "Ink", hex: "#0F172A", varName: "--ty-ink" },
      { name: "Slate", hex: "#64748B", varName: "--ty-slate" },
    ],

    surfacesDark: [
      { name: "Background", hex: "#0B1220", varName: "--ty-bg" },
      { name: "Surface", hex: "#141B2B", varName: "--ty-surface" },
      { name: "Surface 2", hex: "#1B2436", varName: "--ty-surface-2" },
      { name: "Border", hex: "#263149", varName: "--ty-border" },
      { name: "Text", hex: "#E8ECF3", varName: "--ty-text" },
      { name: "Text muted", hex: "#94A0B4", varName: "--ty-text-muted" },
    ],

    // Categorical series — accent family first (charts, legends).
    series: [
      { name: "Series 1", hex: "#12C99A", varName: "--ty-c1" },
      { name: "Series 2", hex: "#258CF8", varName: "--ty-c2" },
      { name: "Series 3", hex: "#7338FF", varName: "--ty-c3" },
      { name: "Series 4", hex: "#10B7D8", varName: "--ty-c4" },
      { name: "Overflow A", hex: "#F0A35E", varName: "--ty-c5" },
      { name: "Overflow B", hex: "#E5709B", varName: "--ty-c6" },
    ],

    status: [
      { key: "ok", name: "Healthy", means: "Green — nominal, passing, cost falling, strong signal" },
      { key: "warn", name: "Attention", means: "Amber — needs review, cost rising, staleness, retry pending" },
      { key: "bad", name: "Error", means: "Red — failed tool call, blocked, data hole" },
      { key: "info", name: "Active", means: "Blue — running, processing, neutral-operational" },
      { key: "neutral", name: "Idle", means: "Gray — inactive, skipped, no activity" },
    ],

    type: [
      { use: "Interface", family: "Inter", note: "UI text, headings, labels, body — clean, modern, highly readable" },
      { use: "Wordmark", family: "Poppins", note: "The logo lockup only (baked into the logo SVGs)" },
      { use: "Numeric / IDs", family: "monospace", note: "Costs, token counts, session IDs, timestamps — tabular figures" },
    ],
    typeScale: [
      { token: "--ty-fs-xs", size: "11px", use: "Eyebrows, table headers, pills" },
      { token: "--ty-fs-sm", size: "12px", use: "Metadata, helper text, legends" },
      { token: "--ty-fs-base", size: "13px", use: "Dense UI, table cells, controls" },
      { token: "--ty-fs-md", size: "14px", use: "Body copy (base)" },
      { token: "--ty-fs-lg", size: "16px", use: "Emphasis" },
      { token: "--ty-fs-xl", size: "18px", use: "Section headings (h2)" },
      { token: "--ty-fs-2xl", size: "22px", use: "Sub-hero" },
      { token: "--ty-fs-3xl", size: "26px", use: "KPI values, page title (h1)" },
    ],

    radius: [
      { varName: "--ty-radius-sm", value: "6px", use: "Inputs, chips, badges, buttons" },
      { varName: "--ty-radius-md", value: "10px", use: "Segmented control, callouts, swatches" },
      { varName: "--ty-radius-lg", value: "14px", use: "Cards, panels, recommendation" },
      { varName: "--ty-radius-pill", value: "999px", use: "Status pills" },
    ],
    shadows: [
      { varName: "--ty-shadow-sm", use: "Cards at rest" },
      { varName: "--ty-shadow-card", use: "Panels" },
      { varName: "--ty-shadow-pop", use: "Menus, drawers (things that truly float)" },
    ],
    spacing: [
      { varName: "--ty-space-1", value: "4px" }, { varName: "--ty-space-2", value: "8px" },
      { varName: "--ty-space-3", value: "12px" }, { varName: "--ty-space-4", value: "16px" },
      { varName: "--ty-space-6", value: "24px" }, { varName: "--ty-space-8", value: "32px" },
    ],

    // The closed loop — the five rungs (primary iconography).
    rungs: [
      { name: "Describe", icon: "bars", q: "What did we spend, on what?" },
      { name: "Diagnose", icon: "search", q: "Why was it expensive?" },
      { name: "Predict", icon: "trend", q: "Where is it heading?" },
      { name: "Prescribe", icon: "compass", q: "What is the next best change?" },
      { name: "Remediate", icon: "loop", q: "Apply it, measure the delta, repeat." },
    ],
    utilIcons: ["layers", "donut", "doc", "grid", "type", "shape"],

    voice: [
      { w: "Clear", d: "We explain the complex simply." },
      { w: "Confident", d: "We back recommendations with data." },
      { w: "Practical", d: "We focus on what teams can do." },
      { w: "Respectful", d: "We protect your data and your time." },
    ],
    principles: [
      { w: "Structured", d: "Systems and alignment" },
      { w: "Data-first", d: "Evidence over opinion" },
      { w: "Iterative", d: "Always improving" },
      { w: "Team-oriented", d: "Built for engineering teams" },
      { w: "Trustworthy", d: "Private, secure, reliable" },
    ],
  };
})(window);
