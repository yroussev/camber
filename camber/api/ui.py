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

import json

from ..report.dashboard import _STYLE
from ..report.linking import LINK_STYLE, selection_bus_html

__all__ = ["live_dashboard_html", "role_units"]

# 0.96 (#78): the store holds IP units (camber.datasets._units converts at ingest), so each role's
# display unit follows from its physical kind. The first bucket of camber.mapping_assist.ROLE_UNIT
# that lists a role names its unit; a role no bucket lists is unitless here and gets its own panel.
_UNIT_LABEL = (
    ("degf", "°F"),
    ("percent", "%"),
    ("cfm", "cfm"),
    ("gpm", "gpm"),
    ("kw", "kW"),
    ("inh2o", "inH₂O"),
    ("ppm", "ppm"),
    ("rh", "%RH"),
    ("psig", "psig"),
)


def role_units() -> dict:
    """``{role slug: display unit}`` for the roles with a known unit in the store (IP units).

    Built from :data:`camber.mapping_assist.ROLE_UNIT`; any other CO₂ role reads ppm, any other
    ``*_temp`` role °F, and a setpoint (``*_sp``) its measurement's unit. A role not listed has no
    unit the viewer can state (a count, a status, a mode) and is drawn on its own panel.
    """
    from ..mapping_assist import ROLE_UNIT

    out: dict = {}
    for token, label in _UNIT_LABEL:
        for role in ROLE_UNIT.get(token, ()):
            out.setdefault(role.value, label)
    from ..model.roles import Role

    for role in Role:
        slug = role.value
        if "co2" in slug:
            out.setdefault(slug, "ppm")
        elif slug.endswith("_temp"):
            out.setdefault(slug, "°F")
    # a setpoint shares its measurement's unit, so the two overlay on one panel
    for role in Role:
        slug = role.value
        if slug.endswith("_sp"):
            base = "space_temp" if slug in ("cool_sp", "heat_sp") else slug[: -len("_sp")]
            if base in out:
                out.setdefault(slug, out[base])
    return dict(sorted(out.items()))


_UI_STYLE = (
    ".controls{margin:14px 0;display:flex;gap:10px 14px;align-items:center;flex-wrap:wrap}"
    ".controls label{font-size:14px}.muted{color:#666;font-size:13px}"
    ".roles{display:inline-flex;gap:10px;flex-wrap:wrap}.roles label{font-size:13px}"
    ".controls label{display:inline-flex;align-items:center;gap:6px;max-width:100%;min-width:0}"
    "select{max-width:100%;min-width:0;flex:1 1 auto}body{margin:16px}"
    ".chartbox{position:relative;border:1px solid #ddd;border-radius:6px;background:#fff;"
    "padding:4px 0}"
    "svg.trend{display:block;width:100%;touch-action:pan-y;cursor:crosshair}"
    "svg.trend text{font:11px system-ui,Arial,sans-serif;fill:#52514e}"
    "svg.trend .ptitle{font-weight:600;fill:#1d1d1b}"
    "svg.trend .grid{stroke:#e6e6e1;stroke-width:1}svg.trend .axis{stroke:#9a9a94}"
    "svg.trend .xhair{stroke:#52514e;stroke-width:1;stroke-dasharray:3 3}"
    ".legend{display:flex;flex-wrap:wrap;gap:6px 16px;margin:8px 0;font-size:13px}"
    ".legend .sw{display:inline-block;width:18px;height:3px;border-radius:2px;"
    "vertical-align:middle;margin-right:6px}"
    ".legend .u{color:#666}"
    ".tip{position:absolute;pointer-events:none;background:#fff;border:1px solid #ccc;"
    "border-radius:6px;padding:6px 8px;font-size:12px;box-shadow:0 2px 6px rgba(0,0,0,.12);"
    "white-space:nowrap;display:none;z-index:2}"
    ".tip .t{color:#666;margin-bottom:2px}"
    "select,button,input{font:inherit}"
)

_CONTROLS_HTML = (
    "<div class='controls'>"
    "<label>Facility <select id='facility'></select></label>"
    "<label>Equipment <select id='equip'></select></label>"
    "</div><div class='controls'>"
    "<span id='roles' class='roles'></span>"
    "</div><div class='controls'>"
    "<label title='Scale every series to 0-1 on one panel'><input type='checkbox' id='norm'> "
    "Normalised (0–1)</label>"
    "<label><input type='checkbox' id='live' checked> Live</label>"
    "<label>every <input type='number' id='interval' value='15' min='2' "
    "style='width:3.2em'> s</label>"
    "<button id='refresh'>Refresh</button>"
    "<span id='updated' class='muted'></span>"
    "</div>"
)

# Vanilla-JS app. No f-string: braces are literal; __UNITS__ is replaced with a JSON object.
_APP_JS = r"""
(function(){
  var NS="http://www.w3.org/2000/svg";
  var UNITS=__UNITS__;
  // categorical slots in fixed order; a role keeps its colour whatever else is ticked
  var PAL=["#2a78d6","#eb6834","#1baf7a","#eda100","#e87ba4","#008300","#4a3aa7","#e34948"];
  var facSel=document.getElementById('facility'),eqSel=document.getElementById('equip');
  var rolesBox=document.getElementById('roles'),svg=document.getElementById('trend');
  var legend=document.getElementById('legend'),tip=document.getElementById('tip');
  var updated=document.getElementById('updated'),liveBox=document.getElementById('live');
  var normBox=document.getElementById('norm');
  var intBox=document.getElementById('interval'),readout=document.getElementById('readout');
  var timer=null,allRoles=[],last=null,geo=null;

  function j(url){return fetch(url).then(function(r){return r.json();});}
  function opt(sel,v,t){var o=document.createElement('option');o.value=v;o.textContent=t;
    sel.appendChild(o);}
  function clear(el){while(el.firstChild)el.removeChild(el.firstChild);}
  function mk(tag,attrs,text){var e=document.createElementNS(NS,tag);
    for(var k in attrs)e.setAttribute(k,attrs[k]);if(text!=null)e.textContent=text;return e;}
  function checkedRoles(){return Array.prototype.slice.call(
    rolesBox.querySelectorAll('input:checked')).map(function(i){return i.value;});}
  function unitOf(r){return UNITS[r]||'';}
  function colorOf(r){var i=allRoles.indexOf(r);return PAL[(i<0?0:i)%PAL.length];}
  function dashOf(r){var i=allRoles.indexOf(r);return i>=PAL.length?'6 3':'';}
  // store timestamps: an offset-less ISO string is the stored wall clock, read as UTC
  function tms(ts){return Date.parse(/(Z|[+-]\d\d:?\d\d)$/.test(ts)?ts:ts+'Z');}
  function pad(n){return (n<10?'0':'')+n;}
  // a time label at the tick step's resolution: hours under a day, days under a year
  function fmtT(ms,step){var d=new Date(ms);
    var md=pad(d.getUTCMonth()+1)+'-'+pad(d.getUTCDate());
    var hm=pad(d.getUTCHours())+':'+pad(d.getUTCMinutes());
    if(step==null)return d.getUTCFullYear()+'-'+md+' '+hm;
    if(step<864e5)return (d.getUTCHours()===0?md+' ':'')+hm;
    if(step<28*864e5)return md;
    return d.getUTCFullYear()+'-'+pad(d.getUTCMonth()+1);}
  function fmtV(v){var a=Math.abs(v);if(v===Math.round(v))return String(v);
    return a>=100?v.toFixed(0):a>=10?v.toFixed(1):v.toFixed(2);}
  function fmtTick(v,st){var dec=Math.max(0,-Math.floor(Math.log(st)/Math.LN10+1e-9));
    return v.toFixed(Math.min(dec,4));}
  function niceTicks(lo,hi,n){if(hi===lo){hi=lo+1;lo=lo-1;}
    var raw=(hi-lo)/n,p=Math.pow(10,Math.floor(Math.log(raw)/Math.LN10)),f=raw/p;
    var st=(f<1.5?1:f<3?2:f<7?5:10)*p,a=Math.floor(lo/st)*st,out=[];
    for(var v=a;v<=hi+st*0.5;v+=st)out.push(+v.toPrecision(12));
    out.step=st;return out;}
  function timeTicks(t0,t1,n){var H=36e5,D=864e5;
    var steps=[H,3*H,6*H,12*H,D,2*D,7*D,14*D,30*D,91*D,182*D,365*D];
    var st=steps[steps.length-1];
    for(var i=0;i<steps.length;i++)if((t1-t0)/steps[i]<=n){st=steps[i];break;}
    var out=[],a=Math.ceil(t0/st)*st;for(var t=a;t<=t1;t+=st)out.push(t);out.step=st;
    return out;}

  function loadFacilities(){
    return j('/facilities').then(function(d){
      clear(facSel);(d.facilities||[]).forEach(function(f){
        var nm=f.display_name||f.name||f.facility_id;
        opt(facSel,f.facility_id,nm+(f.state&&f.state!=='active'?' ['+f.state+']':''));});
      // deep link: /ui?facility_id=<fid> preselects that facility (camber lab links here)
      var want=new URLSearchParams(location.search).get('facility_id');
      if(want)Array.prototype.forEach.call(facSel.options,function(o){
        if(o.value===want)facSel.value=want;});
      if(facSel.options.length)return loadPoints();
    });
  }
  function loadPoints(){
    return j('/points?facility_id='+encodeURIComponent(facSel.value)).then(function(d){
      var eqs=[],seenE={},seenR={};allRoles=[];
      (d.points||[]).forEach(function(p){
        if(!seenE[p.equip]){seenE[p.equip]=1;eqs.push(p.equip);}
        if(!seenR[p.role]){seenR[p.role]=1;allRoles.push(p.role);}
      });
      clear(eqSel);eqs.forEach(function(e){opt(eqSel,e,e);});
      clear(rolesBox);allRoles.forEach(function(r,i){
        var l=document.createElement('label'),c=document.createElement('input');
        c.type='checkbox';c.value=r;c.checked=i<3;c.addEventListener('change',draw);
        l.appendChild(c);
        l.appendChild(document.createTextNode(' '+r+(unitOf(r)?' ('+unitOf(r)+')':'')));
        rolesBox.appendChild(l);
      });
      return draw();
    });
  }
  function draw(){
    var fid=facSel.value,eq=eqSel.value;if(!fid||!eq)return Promise.resolve();
    var roles=checkedRoles();
    // one request per role, so every ticked series gets its own row budget
    return Promise.all(roles.map(function(r){
      return j('/history?facility_id='+encodeURIComponent(fid)+'&equip='+encodeURIComponent(eq)
        +'&role='+encodeURIComponent(r)+'&limit=5000');
    })).then(function(parts){
      var series=[],n=0;
      roles.forEach(function(r,i){
        var pts=(parts[i].history||[]).filter(function(h){return h.value!=null;})
          .map(function(h){return {ts:h.ts,t:tms(h.ts),v:h.value};})
          .sort(function(a,b){return a.t-b.t;});
        n+=pts.length;if(pts.length)series.push({role:r,unit:unitOf(r),pts:pts});
      });
      last=series;render();
      updated.textContent='updated '+new Date().toLocaleTimeString()+' · '+n+' points';
    });
  }
  function groups(series){
    if(normBox.checked)return [{key:'norm',label:'normalised (0–1 per series)',items:series}];
    var out=[],by={};
    series.forEach(function(s){var k=s.unit?'u:'+s.unit:'r:'+s.role;
      if(!by[k]){by[k]={key:k,label:s.unit?s.unit:s.role+' (no unit)',items:[]};out.push(by[k]);}
      by[k].items.push(s);});
    return out;
  }
  function renderLegend(series){
    clear(legend);
    series.forEach(function(s){
      var lo=Infinity,hi=-Infinity;s.pts.forEach(function(p){if(p.v<lo)lo=p.v;if(p.v>hi)hi=p.v;});
      var it=document.createElement('span'),sw=document.createElement('span');
      sw.className='sw';sw.style.background=colorOf(s.role);
      if(dashOf(s.role))sw.style.backgroundImage=
        'repeating-linear-gradient(90deg,transparent 0 6px,#fff 6px 9px)';
      var u=document.createElement('span');u.className='u';
      u.textContent=' '+(s.unit||'no unit')+' · '+fmtV(lo)+'–'+fmtV(hi);
      it.appendChild(sw);it.appendChild(document.createTextNode(s.role));it.appendChild(u);
      legend.appendChild(it);});
  }
  function render(){
    var series=last||[];clear(svg);renderLegend(series);tip.style.display='none';
    var W=Math.max(300,Math.round(svg.getBoundingClientRect().width||1000));
    var small=W<560,L=small?46:60,R=12,PH=small?110:140,GAP=24,XA=30;
    var gs=groups(series);
    var H=Math.max(1,gs.length)*(PH+GAP)+XA;
    svg.setAttribute('viewBox','0 0 '+W+' '+H);svg.setAttribute('height',H);
    if(!series.length){svg.appendChild(mk('text',{x:L,y:40},'no data for this selection'));
      geo=null;return;}
    var t0=Infinity,t1=-Infinity;series.forEach(function(s){
      if(s.pts[0].t<t0)t0=s.pts[0].t;if(s.pts[s.pts.length-1].t>t1)t1=s.pts[s.pts.length-1].t;});
    if(t1===t0)t1=t0+1;
    function X(t){return L+(t-t0)*(W-L-R)/(t1-t0);}
    var span=t1-t0,xt=timeTicks(t0,t1,small?4:8);
    gs.forEach(function(g,gi){
      var top=gi*(PH+GAP)+GAP,bot=top+PH;
      var lo=Infinity,hi=-Infinity;
      if(g.key==='norm'){lo=0;hi=1;}else g.items.forEach(function(s){s.pts.forEach(function(p){
        if(p.v<lo)lo=p.v;if(p.v>hi)hi=p.v;});});
      var yt=niceTicks(lo,hi,small?3:4),ylo=Math.min(lo,yt[0]),yhi=Math.max(hi,yt[yt.length-1]);
      if(yhi===ylo)yhi=ylo+1;
      function Y(v){return bot-(v-ylo)*PH/(yhi-ylo);}
      var names=g.items.map(function(s){return s.role;}).join(', ');
      svg.appendChild(mk('text',{x:L,y:top-8,'class':'ptitle'},
        g.label+(g.key==='norm'?'':'  —  '+names)));
      yt.forEach(function(v){var y=Y(v);
        svg.appendChild(mk('line',{x1:L,x2:W-R,y1:y,y2:y,'class':'grid'}));
        svg.appendChild(mk('text',{x:L-6,y:y+4,'text-anchor':'end'},fmtTick(v,yt.step)));});
      xt.forEach(function(t){svg.appendChild(mk('line',{x1:X(t),x2:X(t),y1:top,y2:bot,'class':'grid'}));});
      svg.appendChild(mk('line',{x1:L,x2:W-R,y1:bot,y2:bot,'class':'axis'}));
      g.items.forEach(function(s){
        var lo2=Infinity,hi2=-Infinity;
        if(g.key==='norm')s.pts.forEach(function(p){if(p.v<lo2)lo2=p.v;if(p.v>hi2)hi2=p.v;});
        var rng=(hi2-lo2)||1;
        function V(v){return g.key==='norm'?(v-lo2)/rng:v;}
        // break the line across gaps well over the typical sample step
        var st=[];for(var i=1;i<s.pts.length;i++)st.push(s.pts[i].t-s.pts[i-1].t);
        st.sort(function(a,b){return a-b;});var gapMax=(st.length?st[st.length>>1]:0)*6||Infinity;
        s.gap=gapMax;
        var d='',prev=null;
        s.pts.forEach(function(p){d+=(prev===null||p.t-prev>gapMax?'M':'L')+X(p.t).toFixed(1)+','
          +Y(V(p.v)).toFixed(1);prev=p.t;});
        var path=mk('path',{d:d,fill:'none',stroke:colorOf(s.role),'stroke-width':'2',
          'stroke-linejoin':'round','stroke-linecap':'round'});
        if(dashOf(s.role))path.setAttribute('stroke-dasharray',dashOf(s.role));
        svg.appendChild(path);
      });
    });
    var yb=gs.length*(PH+GAP)+GAP-GAP;
    xt.forEach(function(t){svg.appendChild(mk('text',{x:X(t),y:yb+16,'text-anchor':'middle'},
      fmtT(t,xt.step)));});
    svg.appendChild(mk('text',{x:W-R,y:yb+28,'text-anchor':'end'},'time (UTC)'));
    geo={W:W,H:H,L:L,R:R,t0:t0,t1:t1,X:X,top:GAP,bot:yb,span:span};
  }
  function pxOf(e){var b=svg.getBoundingClientRect();return (e.clientX-b.left)*geo.W/b.width;}
  function tOf(x){return geo.t0+(x-geo.L)*(geo.t1-geo.t0)/(geo.W-geo.L-geo.R);}
  function nearest(pts,t){var a=0,b=pts.length-1;while(b-a>1){var m=(a+b)>>1;
    if(pts[m].t<t)a=m;else b=m;}return Math.abs(pts[a].t-t)<=Math.abs(pts[b].t-t)?pts[a]:pts[b];}
  var brush=null,x0=0,hair=null;
  function showTip(e){
    if(!geo||!last||!last.length)return;
    var x=pxOf(e);if(x<geo.L||x>geo.W-geo.R){hideTip();return;}
    var t=tOf(x);
    if(!hair){hair=mk('line',{'class':'xhair'});}
    hair.setAttribute('x1',x);hair.setAttribute('x2',x);hair.setAttribute('y1',geo.top);
    hair.setAttribute('y2',geo.bot);svg.appendChild(hair);
    clear(tip);var head=document.createElement('div');head.className='t';
    head.textContent=fmtT(t)+' UTC';tip.appendChild(head);
    last.forEach(function(s){var p=nearest(s.pts,t),row=document.createElement('div');
      var sw=document.createElement('span');sw.className='sw';sw.style.cssText=
        'display:inline-block;width:10px;height:3px;margin-right:6px;vertical-align:middle;background:'
        +colorOf(s.role);
      // no reading inside a gap: the nearest sample may be hours away
      var val=Math.abs(p.t-t)>(s.gap||Infinity)/2?'—':fmtV(p.v)+(s.unit?' '+s.unit:'');
      row.appendChild(sw);row.appendChild(document.createTextNode(s.role+': '+val));
      tip.appendChild(row);});
    var box=svg.parentNode.getBoundingClientRect(),cx=e.clientX-box.left,cy=e.clientY-box.top;
    tip.style.display='block';
    var tw=tip.offsetWidth;tip.style.left=Math.max(4,Math.min(cx+14,box.width-tw-4))+'px';
    tip.style.top=Math.max(4,cy-10)+'px';
  }
  function hideTip(){tip.style.display='none';
    if(hair&&hair.parentNode)hair.parentNode.removeChild(hair);}
  svg.addEventListener('mousedown',function(e){if(!geo)return;x0=pxOf(e);
    brush=mk('rect',{y:geo.top,height:geo.bot-geo.top,fill:'rgba(42,120,214,.15)'});
    svg.appendChild(brush);upd(x0);});
  svg.addEventListener('mousemove',function(e){if(!geo)return;if(brush)upd(pxOf(e));showTip(e);});
  svg.addEventListener('mouseleave',hideTip);
  window.addEventListener('mouseup',function(e){
    if(!brush)return;var x1=pxOf(e),ta=tOf(Math.min(x0,x1)),tb=tOf(Math.max(x0,x1));
    var sel=new Set();(last||[]).forEach(function(s){s.pts.forEach(function(p){
      if(p.t>=ta&&p.t<=tb)sel.add(p.ts);});});
    if(window.CAMBER)window.CAMBER.set(sel);brush.parentNode.removeChild(brush);brush=null;});
  function upd(x1){var a=Math.min(x0,x1),b=Math.max(x0,x1);
    brush.setAttribute('x',a);brush.setAttribute('width',Math.max(b-a,1));}
  if(window.CAMBER)window.CAMBER.onChange(function(sel){
    var a=Array.from(sel).sort();
    readout.textContent=a.length?(a.length+' selected: '+a[0]+' … '+a[a.length-1])
      :'brush the trend to select a time span';
  });
  function reschedule(){if(timer)clearInterval(timer);
    var s=Math.max(2,parseInt(intBox.value,10)||15);
    timer=setInterval(function(){if(liveBox.checked)draw();},s*1000);}
  var rz=null;
  window.addEventListener('resize',function(){clearTimeout(rz);rz=setTimeout(render,120);});
  facSel.addEventListener('change',loadPoints);
  eqSel.addEventListener('change',draw);
  normBox.addEventListener('change',render);
  intBox.addEventListener('change',reschedule);
  document.getElementById('refresh').addEventListener('click',draw);
  loadFacilities().then(reschedule);
})();
"""


def live_dashboard_html() -> str:
    """Return the self-contained live-dashboard HTML (inline JS/CSS, no external assets).

    The page fetches ``/facilities``, ``/points``, and ``/history`` same-origin and polls; it reuses
    the theme (`camber.report.dashboard._STYLE`) and the `window.CAMBER` cross-panel selection bus.
    Served at ``GET /ui`` by :class:`camber.api.server.ReadAPIHandler`. 0.96 (#78): the ticked
    series are drawn in one panel per unit (:func:`role_units`; each with a labelled y axis), on a
    shared, labelled time axis, with a legend, a hover readout, and a normalised (0-1) toggle.
    """
    style = _STYLE + LINK_STYLE + _UI_STYLE
    units = json.dumps(role_units(), ensure_ascii=False).replace("<", "\\u003c")
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>CAMBER — live dashboard</title><style>{style}</style></head><body>"
        "<h1>CAMBER — live dashboard</h1>"
        "<p class='muted'>Live view of the read-only store. One panel per unit; hover for "
        "values, brush a panel to select a time span.</p>"
        + _CONTROLS_HTML
        + "<div id='legend' class='legend' aria-label='legend'></div>"
        + "<div class='chartbox'><svg id='trend' class='trend' viewBox='0 0 1000 240' "
        "role='img' aria-label='trend chart'></svg><div id='tip' class='tip'></div></div>"
        + "<div id='readout' class='camber-out'>brush the trend to select a time span</div>"
        + selection_bus_html()
        + "<script>"
        + _APP_JS.replace("__UNITS__", units)
        + "</script></body></html>"
    )
