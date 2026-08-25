"""A live, framework-free web dashboard served by the read-only API (``GET /ui``).

The self-contained HTML dashboard (`camber.report.build_dashboard`) is a one-shot snapshot; this is
its **live** counterpart — a single vanilla-JS page (no framework, no CDN, CSP-safe) that fetches
the running store through the read-only JSON API (`/facilities`, `/points`, `/history`) and
**polls** so
the views refresh as new data lands. It reuses the shipped `window.CAMBER` cross-panel selection bus
(`camber.report.linking.selection_bus_html`) so a brush on the trend links to the readout like the
static dashboard's panels. Everything is inlined; the page ships no external asset.

Served by `camber.api.server.ReadAPIHandler` at ``GET /ui`` (the JSON endpoints are unchanged). The
page is static text — all data arrives client-side via same-origin `fetch`, so it is trivially
unit-testable and needs no store to build.
"""

from __future__ import annotations

from ..report.dashboard import _STYLE
from ..report.linking import LINK_STYLE, selection_bus_html

__all__ = ["live_dashboard_html"]

_UI_STYLE = (
    ".controls{margin:14px 0;display:flex;gap:14px;align-items:center;flex-wrap:wrap}"
    ".controls label{font-size:14px}.muted{color:#777;font-size:13px}"
    ".roles{display:inline-flex;gap:10px;flex-wrap:wrap}.roles label{font-size:13px}"
    "svg.trend{width:100%;height:240px;border:1px solid #ddd;border-radius:6px;background:#fff}"
    "select,button,input{font:inherit}"
)

_CONTROLS_HTML = (
    "<div class='controls'>"
    "<label>Facility <select id='facility'></select></label>"
    "<label>Equipment <select id='equip'></select></label>"
    "<span id='roles' class='roles'></span>"
    "<label><input type='checkbox' id='live' checked> Live</label>"
    "<label>every <input type='number' id='interval' value='15' min='2' "
    "style='width:3.2em'> s</label>"
    "<button id='refresh'>Refresh</button>"
    "<span id='updated' class='muted'></span>"
    "</div>"
)

# Vanilla-JS app. No f-string: braces are literal. Talks only to the same-origin read-only API.
_APP_JS = r"""
(function(){
  var NS="http://www.w3.org/2000/svg";
  var PAL=["#3366cc","#dc3912","#109618","#ff9900","#990099","#0099c6","#dd4477"];
  var W=1000,H=240,M=28;
  var facSel=document.getElementById('facility'),eqSel=document.getElementById('equip');
  var rolesBox=document.getElementById('roles'),svg=document.getElementById('trend');
  var updated=document.getElementById('updated'),liveBox=document.getElementById('live');
  var intBox=document.getElementById('interval'),readout=document.getElementById('readout');
  var timer=null,xTs=[];

  function j(url){return fetch(url).then(function(r){return r.json();});}
  function opt(sel,v,t){var o=document.createElement('option');o.value=v;o.textContent=t;
    sel.appendChild(o);}
  function clear(el){while(el.firstChild)el.removeChild(el.firstChild);}
  function checkedRoles(){return Array.prototype.slice.call(
    rolesBox.querySelectorAll('input:checked')).map(function(i){return i.value;});}

  function loadFacilities(){
    return j('/facilities').then(function(d){
      clear(facSel);(d.facilities||[]).forEach(function(f){opt(facSel,f.facility_id,f.name||f.facility_id);});
      if(facSel.options.length)return loadPoints();
    });
  }
  function loadPoints(){
    return j('/points?facility_id='+encodeURIComponent(facSel.value)).then(function(d){
      var eqs=[],roles=[],seenE={},seenR={};
      (d.points||[]).forEach(function(p){
        if(!seenE[p.equip]){seenE[p.equip]=1;eqs.push(p.equip);}
        if(!seenR[p.role]){seenR[p.role]=1;roles.push(p.role);}
      });
      clear(eqSel);eqs.forEach(function(e){opt(eqSel,e,e);});
      clear(rolesBox);roles.forEach(function(r,i){
        var l=document.createElement('label'),c=document.createElement('input');
        c.type='checkbox';c.value=r;c.checked=i<3;c.addEventListener('change',draw);
        l.appendChild(c);l.appendChild(document.createTextNode(' '+r));rolesBox.appendChild(l);
      });
      return draw();
    });
  }
  function draw(){
    var fid=facSel.value,eq=eqSel.value;if(!fid||!eq)return Promise.resolve();
    var roles=checkedRoles();
    var url='/history?facility_id='+encodeURIComponent(fid)+'&equip='+encodeURIComponent(eq)
      +'&limit=3000';
    return j(url).then(function(d){
        var rows=(d.history||[]).filter(function(r){
          return roles.indexOf(r.role)>=0&&r.value!=null;});
        render(rows,roles);
        updated.textContent='updated '+new Date().toLocaleTimeString()+' · '+rows.length+' points';
      });
  }
  function render(rows,roles){
    clear(svg);svg.setAttribute('viewBox','0 0 '+W+' '+H);
    var tsSet={};rows.forEach(function(r){tsSet[r.ts]=1;});
    xTs=Object.keys(tsSet).sort();
    if(!xTs.length){var t=document.createElementNS(NS,'text');
      t.setAttribute('x',M);t.setAttribute('y',H/2);t.setAttribute('fill','#999');
      t.textContent='no data for this selection';svg.appendChild(t);return;}
    var xi={};xTs.forEach(function(t,i){xi[t]=i;});
    var vals=rows.map(function(r){return r.value;});
    var lo=Math.min.apply(null,vals),hi=Math.max.apply(null,vals),rng=(hi-lo)||1;
    function X(i){return M+(xTs.length<2?0:i*(W-2*M)/(xTs.length-1));}
    function Y(v){return H-M-((v-lo)/rng)*(H-2*M);}
    roles.forEach(function(role,ri){
      var pts=rows.filter(function(r){return r.role===role;})
        .sort(function(a,b){return a.ts<b.ts?-1:1;})
        .map(function(r){return X(xi[r.ts])+','+Y(r.value);}).join(' ');
      if(!pts)return;
      var pl=document.createElementNS(NS,'polyline');pl.setAttribute('points',pts);
      pl.setAttribute('fill','none');pl.setAttribute('stroke',PAL[ri%PAL.length]);
      pl.setAttribute('stroke-width','1.5');svg.appendChild(pl);
    });
    addBrush();
  }
  function addBrush(){
    var rect=null,x0=0;
    function px(e){var b=svg.getBoundingClientRect();return (e.clientX-b.left)*W/b.width;}
    svg.onmousedown=function(e){x0=px(e);rect=document.createElementNS(NS,'rect');
      rect.setAttribute('y',0);rect.setAttribute('height',H);rect.setAttribute('fill','rgba(51,102,204,.15)');
      svg.appendChild(rect);upd(x0);};
    svg.onmousemove=function(e){if(!rect)return;upd(px(e));};
    window.addEventListener('mouseup',function(e){
      if(!rect)return;var x1=px(e),a=Math.min(x0,x1),b=Math.max(x0,x1);
      var sel=new Set();xTs.forEach(function(t,i){var x=M+(xTs.length<2?0:i*(W-2*M)/(xTs.length-1));
        if(x>=a&&x<=b)sel.add(t);});
      if(window.CAMBER)window.CAMBER.set(sel);rect=null;});
    function upd(x1){var a=Math.min(x0,x1),b=Math.max(x0,x1);
      rect.setAttribute('x',a);rect.setAttribute('width',Math.max(b-a,1));}
  }
  if(window.CAMBER)window.CAMBER.onChange(function(sel){
    var a=Array.from(sel).sort();
    readout.textContent=a.length?(a.length+' selected: '+a[0]+' … '+a[a.length-1])
      :'brush the trend to select a time span';
  });
  function reschedule(){if(timer)clearInterval(timer);
    var s=Math.max(2,parseInt(intBox.value,10)||15);
    timer=setInterval(function(){if(liveBox.checked)draw();},s*1000);}
  facSel.addEventListener('change',loadPoints);
  eqSel.addEventListener('change',draw);
  intBox.addEventListener('change',reschedule);
  document.getElementById('refresh').addEventListener('click',draw);
  loadFacilities().then(reschedule);
})();
"""


def live_dashboard_html() -> str:
    """Return the self-contained live-dashboard HTML (inline JS/CSS, no external assets).

    The page fetches ``/facilities``, ``/points``, and ``/history`` same-origin and polls; it reuses
    the theme (`camber.report.dashboard._STYLE`) and the `window.CAMBER` cross-panel selection bus.
    Served at ``GET /ui`` by :class:`camber.api.server.ReadAPIHandler`.
    """
    style = _STYLE + LINK_STYLE + _UI_STYLE
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>CAMBER — live dashboard</title><style>{style}</style></head><body>"
        "<h1>CAMBER — live dashboard</h1>"
        "<p class='muted'>Live view of the read-only store. Brush the trend to select a "
        "time span.</p>"
        + _CONTROLS_HTML
        + "<svg id='trend' class='trend' viewBox='0 0 1000 240'></svg>"
        + "<div id='readout' class='camber-out'>brush the trend to select a time span</div>"
        + selection_bus_html()
        + "<script>"
        + _APP_JS
        + "</script></body></html>"
    )
