/*
 * TraceYield Design System — app. Renders every section from the token + component data.
 * Add a SECTION entry and it appears in the nav automatically. The page's own design
 * follows the system it documents — it is the first consumer of TraceYield's tokens.
 */
(function (TDS) {
  "use strict";
  var t = TDS.tokens, c = TDS.components, esc = TDS.esc, icon = TDS.icon;

  /* ---------- render helpers ---------- */
  function swatch(item) {
    var sub = item.varName ? '<code>' + esc(item.varName) + '</code>' : "";
    return '<button class="swatch" data-copy="' + esc(item.hex) + '" title="Copy ' + esc(item.hex) + '">'
      + '<span class="chip" style="background:' + esc(item.hex) + '"></span>'
      + '<span class="meta"><strong>' + esc(item.name) + '</strong>'
      + '<span class="hex">' + esc(item.hex) + '</span>' + sub + '</span></button>';
  }
  function specimen(o) {
    var html = o.html;
    return '<figure class="specimen"><figcaption><strong>' + esc(o.title) + '</strong>'
      + (o.desc ? '<span>' + esc(o.desc) + '</span>' : "") + '</figcaption>'
      + '<div class="specimen-stage' + (o.block ? " block" : "") + '">' + html + '</div>'
      + '<details class="specimen-code"><summary>Markup <button class="copy-code" data-copy-text="'
      + esc(html) + '">Copy</button></summary><pre><code>' + esc(html) + '</code></pre></details></figure>';
  }
  var grid = function (items, fn) { return '<div class="grid">' + items.map(fn).join("") + '</div>'; };
  var intro = function (s) { return '<p class="section-intro">' + s + '</p>'; };
  function dsTable(head, rows) {
    return '<table class="ds-table"><thead><tr>' + head.map(function (h) { return '<th>' + esc(h) + '</th>'; }).join("")
      + '</tr></thead><tbody>' + rows.map(function (r) {
        return '<tr>' + r.map(function (cell) { return '<td>' + cell + '</td>'; }).join("") + '</tr>';
      }).join("") + '</tbody></table>';
  }

  /* ---------- sections ---------- */
  var SECTIONS = [
    {
      id: "overview", label: "Overview", ic: "grid", render: function () {
        return intro('<strong>TraceYield</strong> is a closed-loop discipline for managing the cost and efficacy of LLM interactions. Its interface should feel <strong>operational, data-first, and trustworthy</strong> — an instrument, not a flashy AI demo. This reference renders every token and component from a single source (<code>tokens.css</code> + <code>js/tokens.js</code>) shared with the report.')
          + '<div class="callout grad"><strong>Idea: tokens are the meter, not the mission.</strong> We measure in tokens because that is where cost and waste become visible, but we optimize for value per token. Deep-navy surfaces, one accent leading, a teal→violet gradient for identity and the leading data series.</div>'
          + '<h3>The closed loop — five rungs</h3>'
          + grid(t.rungs, function (r) {
            return '<div class="ty-card"><div style="color:var(--ty-accent);margin-bottom:8px">' + icon(r.icon) + '</div>'
              + '<div style="font-weight:600">' + esc(r.name) + '</div>'
              + '<div class="ty-muted" style="font-size:var(--ty-fs-sm);margin-top:3px">' + esc(r.q) + '</div></div>';
          })
          + '<h3>Voice &amp; principles</h3>'
          + '<div class="two"><div>' + dsTable(["Voice", ""], t.voice.map(function (v) {
            return ['<strong>' + esc(v.w) + '</strong>', esc(v.d)];
          })) + '</div><div>' + dsTable(["Principle", ""], t.principles.map(function (p) {
            return ['<strong>' + esc(p.w) + '</strong>', esc(p.d)];
          })) + '</div></div>';
      }
    },

    {
      id: "color", label: "Color", ic: "droplet", render: function () {
        return intro("Click any swatch to copy its hex. The gradient carries identity; brand accents are disciplined; status is the workhorse and always pairs with a label + icon.")
          + '<h3>Gradient — the logo mark</h3>'
          + '<div class="gradbar"></div><div class="gradstops"><span>#05B98A</span><span>#10B7D8</span><span>#258CF8</span><span>#7338FF</span></div>'
          + '<p class="copytip">Use for the closed-loop accent rail, hero fills, and the leading data series — never for body text.</p>'
          + '<h3>Brand accents</h3>' + grid(t.brand, swatch)
          + '<h3>Surfaces &amp; text (dark)</h3>' + grid(t.surfacesDark, swatch)
          + '<h3>Status semantics</h3>'
          + intro("Five-way status. Never rely on color alone — every state carries a label and a dot/icon.")
          + dsTable(["State", "Pill", "Means"], t.status.map(function (s) {
            return ['<strong>' + esc(s.name) + '</strong>', c.pill({ tone: s.key, label: s.name }), esc(s.means)];
          }));
      }
    },

    {
      id: "type", label: "Typography", ic: "type", render: function () {
        return intro("Inter for the interface; monospace for anything that reads like a ledger — costs, token counts, session IDs, timestamps.")
          + dsTable(["Use", "Family", "Notes"], t.type.map(function (r) {
            return ['<strong>' + esc(r.use) + '</strong>', '<code>' + esc(r.family) + '</code>', esc(r.note)];
          }))
          + '<h3>Type scale</h3>'
          + dsTable(["Token", "Size", "Use"], t.typeScale.map(function (r) {
            return ['<code>' + esc(r.token) + '</code>',
              '<span style="font-size:' + esc(r.size) + ';color:var(--ty-text);font-weight:600">' + esc(r.size) + '</span>',
              esc(r.use)];
          }))
          + '<div class="callout"><strong>Tabular numerals everywhere numbers align.</strong> Costs, token counts, and meters use <code>font-variant-numeric: tabular-nums</code> so columns don\'t jitter.</div>';
      }
    },

    {
      id: "shape", label: "Shape & Space", ic: "shape", render: function () {
        return intro("Rounded and calm, hairline borders over heavy shadows; elevation is reserved for things that truly float (menus, drawers).")
          + '<h3>Radius</h3>' + dsTable(["Token", "Value", "Use"], t.radius.map(function (r) {
            return ['<code>' + esc(r.varName) + '</code>', esc(r.value), esc(r.use)];
          }))
          + '<h3>Elevation</h3>' + dsTable(["Token", "Use"], t.shadows.map(function (r) {
            return ['<code>' + esc(r.varName) + '</code>', esc(r.use)];
          }))
          + '<h3>Spacing (4 / 8 grid)</h3>' + dsTable(["Token", "Value"], t.spacing.map(function (r) {
            return ['<code>' + esc(r.varName) + '</code>', esc(r.value)];
          }));
      }
    },

    {
      id: "icons", label: "Iconography", ic: "compass", render: function () {
        return intro("Clean, minimal line icons — consistent 2px stroke, rounded joins. The five primary icons are the rungs of the loop.")
          + '<h3>Primary — the loop</h3>'
          + '<div class="icongrid">' + t.rungs.map(function (r) {
            return '<div class="ig"><div class="box">' + icon(r.icon) + '</div><span>' + esc(r.name) + '</span></div>';
          }).join("") + '</div>'
          + '<h3>Utility</h3>'
          + '<div class="icongrid">' + t.utilIcons.map(function (n) {
            return '<div class="ig"><div class="box">' + icon(n) + '</div><span>' + esc(n) + '</span></div>';
          }).join("") + '</div>';
      }
    },

    {
      id: "components", label: "Components", ic: "layers", render: function () {
        return [
          intro("Every specimen is the real component — the same classes the report renders, off the same tokens. Expand any specimen to copy its markup."),
          "<h3>KPIs &amp; deltas</h3>",
          specimen({
            title: "KPI cards", desc: "cost rising = amber (attention); falling = green (good)", block: true,
            html: '<div class="ty-cards">' + [
              c.kpiCard({ label: "Total cost", value: "$1,240", delta: { dir: "up", text: "18% vs prev" } }),
              c.kpiCard({ label: "Active days", value: "21", delta: { dir: "flat", text: "unchanged" } }),
              c.kpiCard({ label: "Turns", value: "3,904", delta: { dir: "down", text: "6%" } }),
              c.kpiCard({ label: "Tool-error rate", value: "2.1%", delta: { dir: "down", text: "0.4pt" } }),
            ].join("") + '</div>'
          }),
          "<h3>Controls</h3>",
          specimen({ title: "Segmented control", html: c.seg(["Day", "Week", "Month"]) }),
          specimen({ title: "Buttons", html: c.button({ label: "Export", variant: "primary" }) + c.button({ label: "Reset" }) }),
          specimen({ title: "Status pills", desc: "label + dot, never color alone", html: t.status.map(function (s) { return c.pill({ tone: s.key, label: s.name }); }).join(" ") }),
          "<h3>Breakdown &amp; tables</h3>",
          specimen({
            title: "Horizontal bar rows", desc: "leading row uses the gradient", block: true,
            html: c.hbars([
              { label: "traceyield", pct: 82, val: "$612", grad: true },
              { label: "signal", pct: 54, val: "$402" },
              { label: "tempo", pct: 30, val: "$226" },
            ])
          }),
          specimen({
            title: "Table", desc: "mono IDs + tabular figures", block: true,
            html: c.table([{ t: "Session" }, { t: "Cost", num: true }, { t: "Turns", num: true }], [
              [{ t: "2e20…3fa", mono: true }, { t: "$84.10", num: true }, { t: "312", num: true }],
              [{ t: "a91c…0d2", mono: true }, { t: "$52.77", num: true }, { t: "198", num: true }],
            ])
          }),
          "<h3>Recommendation (the prescribe rung)</h3>",
          specimen({
            title: "Recommendation card", block: true,
            html: c.rec({ eyebrow: "Prescribe · routing", badge: "High impact", title: "Route long-context analysis sessions to a cheaper tier.", sub: "Est. monthly savings (upper bound)", money: "$1,240" })
          }),
        ].join("");
      }
    },

    {
      id: "dataviz", label: "Data viz", ic: "donut", render: function () {
        return intro("Use the categorical order below — accent family first — so charts across the report read as one system. Keep them clean, high-contrast, and always labelled.")
          + '<h3>Series palette</h3>' + grid(t.series, swatch)
          + '<h3>Token composition</h3>'
          + '<div class="ty-panel"><div class="donut-wrap">'
          + c.donut([
            { hex: "#12C99A", pct: 42 }, { hex: "#258CF8", pct: 23 },
            { hex: "#7338FF", pct: 21 }, { hex: "#0B1220", pct: 14 },
          ])
          + '<div>' + c.legend([
            { name: "Fresh input · 42%", hex: "#12C99A" }, { name: "Cache write · 23%", hex: "#258CF8" },
            { name: "Cache read · 21%", hex: "#7338FF" }, { name: "Output · 14%", hex: "#0B1220" },
          ]) + '</div></div></div>';
      }
    },

    {
      id: "report", label: "Report bridge", ic: "doc", render: function () {
        return intro("How this system restyles the emitted <code>report.html</code>. The report already renders from CSS variables, so applying the brand is a one-block swap.")
          + '<div class="callout"><strong>Step 1 — swap the <code>:root</code> block</strong> in <code>report.py</code> (line ~5). The legacy names map straight onto brand tokens:</div>'
          + dsTable(["report.py var", "→ brand token", "value (dark)"], [
            ["<code>--bg</code>", "<code>--ty-bg</code>", "<code>#0B1220</code>"],
            ["<code>--panel</code>", "<code>--ty-surface</code>", "<code>#141B2B</code>"],
            ["<code>--panel2</code>", "<code>--ty-surface-2</code>", "<code>#1B2436</code>"],
            ["<code>--ink</code>", "<code>--ty-text</code>", "<code>#E8ECF3</code>"],
            ["<code>--mut</code>", "<code>--ty-text-muted</code>", "<code>#94A0B4</code>"],
            ["<code>--line</code>", "<code>--ty-border</code>", "<code>#263149</code>"],
            ["<code>--accent</code>", "<code>--ty-accent</code>", "<code>#12C99A</code>"],
          ])
          + '<div class="callout"><strong>Step 2 — chart series.</strong> Point the SVG chart JS at <code>[\'#12C99A\',\'#258CF8\',\'#7338FF\',\'#10B7D8\',\'#F0A35E\',\'#E5709B\']</code> and use the gradient for the leading series.</div>'
          + '<div class="callout grad"><strong>Step 3 — brand the header.</strong> Add the mark + <em>Trace<span style="color:var(--ty-teal)">Yield</span></em> lockup above the <code>&lt;h1&gt;</code>. Full instructions in <code>design-system/README.md</code>.</div>';
      }
    },
  ];

  /* ---------- routing / nav ---------- */
  var stage = document.getElementById("stage");
  var title = document.getElementById("stageTitle");
  var navList = document.getElementById("navList");

  navList.innerHTML = SECTIONS.map(function (s) {
    return '<button class="nav-item" data-id="' + s.id + '"><span class="nav-ic">' + icon(s.ic) + '</span>' + esc(s.label) + '</button>';
  }).join("");

  function show(id) {
    var s = SECTIONS.filter(function (x) { return x.id === id; })[0] || SECTIONS[0];
    stage.innerHTML = s.render();
    title.textContent = s.label;
    [].forEach.call(navList.children, function (b) { b.classList.toggle("active", b.getAttribute("data-id") === s.id); });
    if (location.hash.slice(1) !== s.id) history.replaceState(null, "", "#" + s.id);
    stage.scrollTop = 0; window.scrollTo(0, 0);
  }
  navList.addEventListener("click", function (e) {
    var b = e.target.closest(".nav-item"); if (b) show(b.getAttribute("data-id"));
  });
  window.addEventListener("hashchange", function () { show(location.hash.slice(1)); });

  /* ---------- copy to clipboard ---------- */
  var toast = document.getElementById("toast"), toastT;
  function flash(msg) {
    toast.textContent = msg; toast.classList.add("show");
    clearTimeout(toastT); toastT = setTimeout(function () { toast.classList.remove("show"); }, 1400);
  }
  function copy(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () { flash("Copied " + text.slice(0, 40)); },
        function () { flash("Copy failed"); });
    } else {
      var ta = document.createElement("textarea"); ta.value = text; document.body.appendChild(ta);
      ta.select(); try { document.execCommand("copy"); flash("Copied " + text.slice(0, 40)); } catch (e) { flash("Copy failed"); }
      document.body.removeChild(ta);
    }
  }
  document.addEventListener("click", function (e) {
    var sw = e.target.closest("[data-copy]"); if (sw) { copy(sw.getAttribute("data-copy")); return; }
    var cc = e.target.closest("[data-copy-text]"); if (cc) { e.preventDefault(); copy(cc.getAttribute("data-copy-text")); }
  });

  /* ---------- theme toggle (dark → light → auto) ---------- */
  var order = ["dark", "light", "auto"], labels = { dark: "◐ Dark", light: "◐ Light", auto: "◐ Auto" };
  var tbtn = document.getElementById("themeToggle");
  function applyTheme(mode) {
    if (mode === "auto") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", mode);
    tbtn.textContent = labels[mode];
  }
  var cur = "dark"; applyTheme(cur);
  tbtn.addEventListener("click", function () { cur = order[(order.indexOf(cur) + 1) % order.length]; applyTheme(cur); });

  /* ---------- brand mark + boot ---------- */
  document.getElementById("brandMark").innerHTML = TDS.mark();
  show(location.hash.slice(1) || "overview");
})(window.TDS);
