"""Interactive linking — a brush-able inline-SVG scatter, no web framework.

The static dashboard embeds matplotlib figures as PNG. This adds *one interactive view*: an
inline-SVG scatter drawn by a small **vanilla-JS** module from an inline JSON payload, where
box-brushing a region highlights the selected points and writes their **timestamps** into a linked
readout — the pattern-D brush-back (region → when it happened) made live.

Everything is inlined and CSP-safe (no CDN, no framework, no external asset), so the dashboard stays
a single self-contained file. `interactive_scatter_html` returns an HTML fragment; the dashboard
embeds it when ``interactive=True``. The PNG panels remain static (extending the brush to them is a
later step); this delivers the linking principle end to end.
"""

from __future__ import annotations

import json

import pandas as pd

# CSS for the interactive scatter (merged into the dashboard <style>).
LINK_STYLE = (".camber-pt{fill:#3366cc;opacity:.55}"
              ".camber-pt.sel{fill:#d62728;opacity:.95}"
              ".camber-brush{fill:#3366cc;opacity:.12;stroke:#3366cc;stroke-dasharray:4 2}"
              "svg.camber-svg{border:1px solid #ddd;touch-action:none;cursor:crosshair}"
              ".camber-out{font-size:13px;margin-top:6px;color:#333}"
              # cross-panel linking: highlight for selected carpet cells + multitrend time bands
              ".camber-cell{stroke:#fff;stroke-width:.5}"
              ".camber-cell.sel{stroke:#d62728;stroke-width:1.5}"
              ".camber-band{fill:#d62728;opacity:.18}"
              "svg.camber-static{border:1px solid #ddd}")

# Vanilla-JS brush module; "__ID__" is replaced with the element id. No external dependencies.
_BRUSH_JS = """
(function(){
  var data = JSON.parse(document.getElementById("__ID__-data").textContent);
  var svg = document.getElementById("__ID__"), out = document.getElementById("__ID__-out");
  var W = +svg.getAttribute("width"), H = +svg.getAttribute("height"), pad = 44, NS = svg.namespaceURI;
  var xs = data.map(function(d){return d[1];}), ys = data.map(function(d){return d[2];});
  var xmin = Math.min.apply(null, xs), xmax = Math.max.apply(null, xs);
  var ymin = Math.min.apply(null, ys), ymax = Math.max.apply(null, ys);
  function sx(x){return pad + (x - xmin) / ((xmax - xmin) || 1) * (W - 2*pad);}
  function sy(y){return H - pad - (y - ymin) / ((ymax - ymin) || 1) * (H - 2*pad);}
  var circles = data.map(function(d){
    var c = document.createElementNS(NS, "circle");
    c.setAttribute("cx", sx(d[1])); c.setAttribute("cy", sy(d[2]));
    c.setAttribute("r", 3); c.setAttribute("class", "camber-pt"); svg.appendChild(c); return c;
  });
  var rect = document.createElementNS(NS, "rect");
  rect.setAttribute("class", "camber-brush"); rect.style.display = "none"; svg.appendChild(rect);
  var start = null;
  function at(e){var r = svg.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top];}
  svg.addEventListener("mousedown", function(e){start = at(e); rect.style.display = "";});
  svg.addEventListener("mousemove", function(e){
    if(!start) return; var p = at(e);
    rect.setAttribute("x", Math.min(start[0], p[0])); rect.setAttribute("y", Math.min(start[1], p[1]));
    rect.setAttribute("width", Math.abs(p[0]-start[0])); rect.setAttribute("height", Math.abs(p[1]-start[1]));
  });
  window.addEventListener("mouseup", function(e){
    if(!start) return; var p = at(e);
    var x0 = Math.min(start[0],p[0]), x1 = Math.max(start[0],p[0]);
    var y0 = Math.min(start[1],p[1]), y1 = Math.max(start[1],p[1]);
    var sel = [];
    circles.forEach(function(c, i){
      var cx = +c.getAttribute("cx"), cy = +c.getAttribute("cy");
      var hit = cx >= x0 && cx <= x1 && cy >= y0 && cy <= y1;
      c.classList.toggle("sel", hit); if(hit) sel.push(data[i][0]);
    });
    out.textContent = sel.length ? (sel.length + " selected: " + sel[0] + " … " + sel[sel.length-1])
                                 : "no points selected — drag a box over the cloud";
    if(window.CAMBER){window.CAMBER.set(sel);}   // publish to the cross-panel selection bus
    start = null; rect.style.display = "none";
  });
})();
"""


def interactive_scatter_html(x, y, timestamps, *, xlabel: str = "x", ylabel: str = "y",
                             elem_id: str = "camber-link", width: int = 680,
                             height: int = 380) -> str:
    """Return a self-contained HTML fragment: an inline JSON payload, a brush-able SVG scatter, a
    linked readout, and the vanilla-JS module — no external dependencies."""
    pts = [[str(t), float(xi), float(yi)]
           for t, xi, yi in zip(timestamps, x, y) if pd.notna(xi) and pd.notna(yi)]
    payload = json.dumps(pts, separators=(",", ":"))
    js = _BRUSH_JS.replace("__ID__", elem_id)
    return (
        f"<p style='font-size:13px;color:#555'>{ylabel} vs {xlabel} — drag a box to select points; "
        f"the readout lists their timestamps.</p>"
        f"<script type='application/json' id='{elem_id}-data'>{payload}</script>"
        f"<svg class='camber-svg' id='{elem_id}' width='{width}' height='{height}'></svg>"
        f"<div class='camber-out' id='{elem_id}-out'>drag a box over the cloud to select</div>"
        f"<script>{js}</script>")


# --------------------------------------------------------------------------- cross-panel bus + SVG panels

# The shared selection bus (injected once when interactive). Holds a Set of selected timestamp strings;
# the scatter brush publishes to it, the SVG panels below subscribe. Idempotent.
_BUS_JS = """
(function(){
  if(window.CAMBER) return;
  window.CAMBER = {sel:new Set(), subs:[],
    set:function(ids){this.sel=new Set(ids); for(var i=0;i<this.subs.length;i++) this.subs[i](this.sel);},
    onChange:function(f){this.subs.push(f); f(this.sel);}};
})();
"""


def selection_bus_html() -> str:
    """The one-time cross-panel selection bus `<script>` (inject before the linked panels)."""
    return f"<script>{_BUS_JS}</script>"


def _blue(v: float) -> str:
    """Light→dark blue for a normalized value v in 0..1 (clamped)."""
    v = 0.0 if v != v else max(0.0, min(1.0, v))
    lo, hi = (247, 251, 255), (8, 48, 107)
    r, g, b = (int(lo[i] + (hi[i] - lo[i]) * v) for i in range(3))
    return f"#{r:02x}{g:02x}{b:02x}"


_CARPET_JS = """
(function(){
  if(!window.CAMBER) return;
  var cells = document.querySelectorAll("#__ID__ .camber-cell");
  window.CAMBER.onChange(function(sel){
    cells.forEach(function(c){ c.classList.toggle("sel", sel.has(c.getAttribute("data-ts"))); });
  });
})();
"""


def carpet_svg_html(series, *, title: str = "Load carpet", elem_id: str = "camber-carpet",
                    width: int = 680, height: int = 300) -> str:
    """An inline-SVG hour×date load carpet whose cells highlight on the shared selection.

    Each cell carries its ``data-ts`` (the sample's timestamp string, matching the scatter payload), so
    a brush selection in the scatter highlights the corresponding cells. Self-contained, CSP-safe.
    """
    import numpy as np

    s = pd.Series(series).dropna()
    if s.empty:
        return ""
    idx = pd.DatetimeIndex(s.index)
    dates = sorted({d.date() for d in idx})
    di = {d: i for i, d in enumerate(dates)}
    pad_l, pad_t = 30, 20
    cw = max((width - pad_l) / max(len(dates), 1), 1.0)
    ch = max((height - pad_t) / 24.0, 1.0)
    vmin, vmax = float(np.nanmin(s.values)), float(np.nanmax(s.values))
    rng = (vmax - vmin) or 1.0
    rects = []
    for t, v in zip(idx, s.values):
        x = pad_l + di[t.date()] * cw
        y = pad_t + t.hour * ch
        rects.append(f"<rect class='camber-cell' data-ts='{str(pd.Timestamp(t))}' "
                     f"x='{x:.1f}' y='{y:.1f}' width='{cw:.1f}' height='{ch:.1f}' "
                     f"fill='{_blue((v - vmin) / rng)}'></rect>")
    js = _CARPET_JS.replace("__ID__", elem_id)
    return (f"<p style='font-size:13px;color:#555'>{title} — cells highlight for the brushed selection "
            f"(hour of day × date).</p>"
            f"<svg class='camber-static' id='{elem_id}' width='{width}' height='{height}'>"
            f"{''.join(rects)}</svg><script>{js}</script>")


_TREND_JS = """
(function(){
  if(!window.CAMBER) return;
  var meta = JSON.parse(document.getElementById("__ID__-meta").textContent); // [[ts, x], ...] in order
  var hl = document.getElementById("__ID__-hl"), NS = hl.namespaceURI, H = meta.H, pad = meta.pad, xs = meta.xs;
  window.CAMBER.onChange(function(sel){
    while(hl.firstChild) hl.removeChild(hl.firstChild);
    var i = 0;
    while(i < xs.length){
      if(sel.has(xs[i][0])){
        var j = i; while(j+1 < xs.length && sel.has(xs[j+1][0])) j++;   // contiguous run
        var r = document.createElementNS(NS, "rect");
        r.setAttribute("class","camber-band"); r.setAttribute("x", xs[i][1]);
        r.setAttribute("y", pad); r.setAttribute("width", Math.max(xs[j][1]-xs[i][1], 1.5));
        r.setAttribute("height", H-2*pad); hl.appendChild(r); i = j+1;
      } else { i++; }
    }
  });
})();
"""


def multitrend_svg_html(df, cols, *, spans=None, elem_id: str = "camber-trend",
                        width: int = 680, height: int = 300) -> str:
    """An inline-SVG multitrend (a normalized polyline per column + fault-span shading) that shades the
    brushed time ranges on the shared selection. Self-contained, CSP-safe."""
    import json as _json

    import numpy as np

    frame = df[list(cols)] if cols else df
    frame = frame.dropna(how="all")
    if frame.empty or frame.shape[1] == 0:
        return ""
    idx = pd.DatetimeIndex(frame.index)
    n = len(idx)
    pad = 24

    def px(i):
        return pad + (i / max(n - 1, 1)) * (width - 2 * pad)

    lines = []
    palette = ["#3366cc", "#dc3912", "#109618", "#ff9900", "#990099"]
    for k, c in enumerate(frame.columns):
        v = frame[c].to_numpy(dtype=float)
        lo, hi = np.nanmin(v), np.nanmax(v)
        rng = (hi - lo) or 1.0
        pts = " ".join(f"{px(i):.1f},{(height - pad - (0 if vi != vi else (vi - lo) / rng) * (height - 2 * pad)):.1f}"
                       for i, vi in enumerate(v))
        lines.append(f"<polyline fill='none' stroke='{palette[k % len(palette)]}' "
                     f"stroke-width='1' points='{pts}'></polyline>")
    span_rects = []
    for start, end in (spans or []):
        i0 = int(idx.searchsorted(pd.Timestamp(start)))
        i1 = int(idx.searchsorted(pd.Timestamp(end)))
        span_rects.append(f"<rect class='camber-band' x='{px(i0):.1f}' y='{pad}' "
                          f"width='{max(px(i1) - px(i0), 1.5):.1f}' height='{height - 2 * pad}'></rect>")
    meta = {"H": height, "pad": pad, "xs": [[str(pd.Timestamp(t)), round(px(i), 1)]
                                            for i, t in enumerate(idx)]}
    js = _TREND_JS.replace("__ID__", elem_id)
    return (f"<p style='font-size:13px;color:#555'>Fault multitrend — brushed time ranges are shaded.</p>"
            f"<script type='application/json' id='{elem_id}-meta'>{_json.dumps(meta, separators=(',', ':'))}</script>"
            f"<svg class='camber-static' id='{elem_id}' width='{width}' height='{height}'>"
            f"<g id='{elem_id}-hl'></g>{''.join(span_rects)}{''.join(lines)}</svg><script>{js}</script>")
