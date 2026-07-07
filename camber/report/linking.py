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
              ".camber-out{font-size:13px;margin-top:6px;color:#333}")

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
