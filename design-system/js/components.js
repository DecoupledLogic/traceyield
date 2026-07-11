/*
 * TraceYield component builders (functions → HTML).
 * Each returns the REAL product markup + classes from components.css, off the same tokens —
 * so a specimen in the DS page is the same thing the report renders. Drift is bounded to
 * markup, never values.
 */
(function (w) {
  "use strict";
  var TDS = w.TDS || (w.TDS = {});
  var esc = TDS.esc;

  TDS.components = {
    kpiCard: function (o) {
      var d = o.delta || {};
      var cls = d.dir === "up" ? "ty-delta-up" : d.dir === "down" ? "ty-delta-down" : "ty-delta-flat";
      var arrow = d.dir === "up" ? "▲ " : d.dir === "down" ? "▼ " : "— ";
      return '<div class="ty-card"><div class="k-label">' + esc(o.label) + '</div>'
        + '<div class="k-val">' + esc(o.value) + '</div>'
        + (d.text ? '<div class="k-delta ' + cls + '">' + arrow + esc(d.text) + '</div>' : '') + '</div>';
    },

    seg: function (items) {
      return '<div class="ty-seg">' + items.map(function (it, i) {
        return '<button class="' + (i === 0 ? "on" : "") + '">' + esc(it) + '</button>';
      }).join("") + '</div>';
    },

    button: function (o) {
      return '<button class="ty-btn' + (o.variant === "primary" ? " primary" : "") + '">' + esc(o.label) + '</button>';
    },

    pill: function (o) {
      return '<span class="ty-pill ' + esc(o.tone) + '"><i></i>' + esc(o.label) + '</span>';
    },

    hbar: function (o) {
      return '<div class="ty-hbar"><span class="ty-hlabel">' + esc(o.label) + '</span>'
        + '<span class="ty-htrack"><span class="ty-hfill' + (o.grad ? " grad" : "") + '" style="width:' + o.pct + '%"></span></span>'
        + '<span class="ty-hval">' + esc(o.val) + '</span></div>';
    },
    hbars: function (rows) {
      return '<div class="ty-hbars">' + rows.map(TDS.components.hbar).join("") + '</div>';
    },

    table: function (head, rows) {
      return '<table class="ty-table"><thead><tr>' + head.map(function (h) {
        return '<th' + (h.num ? ' class="num"' : "") + '>' + esc(h.t) + '</th>';
      }).join("") + '</tr></thead><tbody>' + rows.map(function (r) {
        return '<tr>' + r.map(function (cell) {
          return '<td' + (cell.num ? ' class="num ty-mono"' : (cell.mono ? ' class="ty-mono"' : "")) + '>' + esc(cell.t) + '</td>';
        }).join("") + '</tr>';
      }).join("") + '</tbody></table>';
    },

    legend: function (series) {
      return '<div class="ty-legend">' + series.map(function (s) {
        return '<span class="leg"><i style="background:' + esc(s.hex) + '"></i>' + esc(s.name) + '</span>';
      }).join("") + '</div>';
    },

    rec: function (o) {
      return '<div class="ty-rec"><div class="top"><span class="ty-eyebrow">' + esc(o.eyebrow) + '</span>'
        + TDS.components.pill({ tone: "ok", label: o.badge }) + '</div>'
        + '<p style="margin:0 0 10px;font-weight:600">' + esc(o.title) + '</p>'
        + '<div class="ty-muted" style="font-size:var(--ty-fs-sm)">' + esc(o.sub) + '</div>'
        + '<div class="money ty-grad-text">' + esc(o.money) + '</div></div>';
    },

    // A small SVG donut from series percentages (data-viz specimen).
    donut: function (parts) {
      var off = 0, ring = parts.map(function (p) {
        var seg = '<circle cx="21" cy="21" r="15.915" fill="none" stroke="' + p.hex
          + '" stroke-width="7" stroke-dasharray="' + p.pct + ' ' + (100 - p.pct)
          + '" stroke-dashoffset="' + (-off) + '"/>';
        off += p.pct; return seg;
      }).join("");
      return '<svg viewBox="0 0 42 42" width="132" height="132" aria-hidden="true">'
        + '<circle cx="21" cy="21" r="15.915" fill="none" stroke="var(--ty-surface-2)" stroke-width="7"/>'
        + ring + '</svg>';
    },
  };
})(window);
