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
# Counts and the few measured roles no ROLE_UNIT bucket lists (their unit is fixed by the role's
# definition in camber.model.roles) are named here; ROLE_UNIT itself, which drives the mapping
# suggester, is unchanged.
_ROLE_LABEL = {
    "occupancy": "persons",  # a head count; an occupied flag reads as 0 / 1 persons
    "sat_reset_requests": "requests",
    "static_pressure_requests": "requests",
    "compressor_stage": "stage",
    "heat_stage": "stage",
    "supply_air_humidity": "%RH",
    "return_air_humidity": "%RH",
    "filter_diff_press": "inH₂O",
    "pump_head": "psi",
    "source_loop_diff_press": "psi",
    "source_loop_pump_speed": "%",
}
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
    ``*_temp`` role °F, and a setpoint (``*_sp``) its measurement's unit. Counts carry their count
    unit (``occupancy`` reads persons, the G36 request roles requests, stage roles stage). A role
    not listed has no unit the viewer can state (a status, a mode, a command) and is drawn on its
    own panel.
    """
    from ..mapping_assist import ROLE_UNIT

    out: dict = dict(_ROLE_LABEL)
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
    "[hidden]{display:none!important}"  # a display rule below would otherwise un-hide a label
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
    "<button id='lists' title='Re-read the facility and equipment lists from the store'>"
    "Reload lists</button>"
    "</div><div class='controls'>"
    "<span id='roles' class='roles'></span>"
    "</div><div class='controls' id='range'>"
    "<label>From <input type='date' id='from'></label>"
    "<label>to <input type='date' id='to'></label>"
    "<button data-days='7' class='preset' title='The last 7 days of data'>Last 7 days</button>"
    "<button data-days='30' class='preset' title='The last 30 days of data'>Last 30 days</button>"
    "<button data-days='0' class='preset' title='Every sample, thinned to fit'>All</button>"
    "<button id='zoomout' title='Back to the previous span' disabled>Zoom out</button>"
    "</div><div class='controls'>"
    "<label title='Scale every series to 0-1 on one panel'><input type='checkbox' id='norm'> "
    "Normalised (0–1)</label>"
    "<label id='utcbox' hidden title='Show the time axis in UTC instead of site time'>"
    "<input type='checkbox' id='utc'> UTC</label>"
    "<label><input type='checkbox' id='live' checked> Live</label>"
    "<label>every <input type='number' id='interval' value='15' min='2' "
    "style='width:3.2em'> s</label>"
    "<button id='refresh' title='Re-read the store now, lists included'>Refresh</button>"
    "<span id='updated' class='muted'></span>"
    "</div>"
    "<div id='window' class='muted' aria-live='polite'></div>"
)

# per-series sample budgets: the whole span is thinned to _OVERVIEW_POINTS, a brushed or picked
# window is read at full resolution up to _WINDOW_POINTS (thinned past that, and the page says so)
_OVERVIEW_POINTS = 2000
_WINDOW_POINTS = 20000

# Vanilla-JS app. No f-string: braces are literal; __UNITS__ is replaced with a JSON object and
# __OVERVIEW__ / __WINDOW__ with the sample budgets.
_APP_JS = r"""
(function(){
  var NS="http://www.w3.org/2000/svg";
  var UNITS=__UNITS__,OVERVIEW=__OVERVIEW__,WINDOW=__WINDOW__,DAY=864e5;
  // categorical slots in fixed order; a role keeps its colour whatever else is ticked
  var PAL=["#2a78d6","#eb6834","#1baf7a","#eda100","#e87ba4","#008300","#4a3aa7","#e34948"];
  function $(id){return document.getElementById(id);}
  var facSel=$('facility'),eqSel=$('equip'),rolesBox=$('roles'),svg=$('trend');
  var legend=$('legend'),tip=$('tip'),updated=$('updated'),liveBox=$('live');
  var normBox=$('norm'),utcBox=$('utc'),utcLbl=$('utcbox'),intBox=$('interval');
  var readout=$('readout'),winLine=$('window'),fromIn=$('from'),toIn=$('to');
  var zoomBtn=$('zoomout');
  var timer=null,allRoles=[],last=null,geo=null,FTZ={},TZ=null,fmtZ=null,offC={};
  // AXIS: how /points says to label this facility's time axis (no zone: never claim UTC)
  var AXIS={timezone:null,local_label:'local time (no time zone recorded)',utc_label:null};
  // view: the window drawn, in stored wall-clock ms ({} = the whole span, thinned);
  // views: the spans to go back to; extent: first/last stored sample of the whole span
  var view={},views=[],extent=null,pendingSel=null,meta=null,busy=0;

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
  // store timestamps: an offset-less ISO string is the stored wall clock, held as UTC ms
  function tms(ts){return Date.parse(/(Z|[+-]\d\d:?\d\d)$/.test(ts)?ts:ts+'Z');}
  // wall-clock ms back to the store's naive ISO form (for start= / end=)
  function iso(w){return new Date(w).toISOString().slice(0,19);}
  function pad(n){return (n<10?'0':'')+n;}
  function num(n){return String(n).replace(/\B(?=(\d{3})+(?!\d))/g,',');}
  // The store holds the site's naive wall clock. With a recorded zone the axis shows it as local
  // time in that zone and the UTC box converts; without one there is no box and no UTC claim.
  function setZone(){TZ=AXIS.timezone||FTZ[facSel.value]||null;fmtZ=null;offC={};
    if(TZ){try{fmtZ=new Intl.DateTimeFormat('en-US',{timeZone:TZ,hourCycle:'h23',
      year:'numeric',month:'numeric',day:'numeric',hour:'numeric',minute:'numeric',
      second:'numeric'});}catch(e){TZ=null;}}
    utcLbl.hidden=!TZ;if(!TZ)utcBox.checked=false;}
  function utcOn(){return !!(TZ&&utcBox.checked);}
  function axisLabel(){return utcOn()?(AXIS.utc_label||'time (UTC)')
    :(AXIS.local_label||'local time (no time zone recorded)');}
  function zoneTag(){return utcOn()?'UTC':(TZ||'local, no time zone recorded');}
  // the zone's UTC offset (ms) at instant u, cached per hour
  function offMs(u){var k=Math.floor(u/36e5);if(offC[k]!=null)return offC[k];var o={};
    fmtZ.formatToParts(new Date(u)).forEach(function(q){o[q.type]=q.value;});
    var s=Math.floor(u/1000)*1000;
    return offC[k]=Date.UTC(+o.year,+o.month-1,+o.day,+o.hour%24,+o.minute,+o.second)-s;}
  function wallToUtc(w){return w-offMs(w-offMs(w));}
  function disp(w){return utcOn()?wallToUtc(w):w;}
  function toWall(t){return utcOn()?t+offMs(t):t;}
  // a time label at the tick step's resolution: hours under a day, days under a year
  function fmtT(ms,step){var d=new Date(ms);
    var md=pad(d.getUTCMonth()+1)+'-'+pad(d.getUTCDate());
    var hm=pad(d.getUTCHours())+':'+pad(d.getUTCMinutes());
    if(step==null)return d.getUTCFullYear()+'-'+md+' '+hm;
    if(step<DAY)return (d.getUTCHours()===0?md+' ':'')+hm;
    if(step<28*DAY)return md;
    return d.getUTCFullYear()+'-'+pad(d.getUTCMonth()+1);}
  function dateOf(ms){return new Date(ms).toISOString().slice(0,10);}
  function fmtV(v){var a=Math.abs(v);if(v===Math.round(v))return String(v);
    return a>=100?v.toFixed(0):a>=10?v.toFixed(1):v.toFixed(2);}
  function fmtTick(v,st){var dec=Math.max(0,-Math.floor(Math.log(st)/Math.LN10+1e-9));
    return v.toFixed(Math.min(dec,4));}
  function niceTicks(lo,hi,n){if(hi===lo){hi=lo+1;lo=lo-1;}
    var raw=(hi-lo)/n,p=Math.pow(10,Math.floor(Math.log(raw)/Math.LN10)),f=raw/p;
    var st=(f<1.5?1:f<3?2:f<7?5:10)*p,a=Math.floor(lo/st)*st,out=[];
    for(var v=a;v<=hi+st*0.5;v+=st)out.push(+v.toPrecision(12));
    out.step=st;return out;}
  function timeTicks(t0,t1,n){var H=36e5,D=DAY;
    var steps=[60e3,5*60e3,15*60e3,H,3*H,6*H,12*H,D,2*D,7*D,14*D,30*D,91*D,182*D,365*D];
    var st=steps[steps.length-1];
    for(var i=0;i<steps.length;i++)if((t1-t0)/steps[i]<=n){st=steps[i];break;}
    var out=[],a=Math.ceil(t0/st)*st;for(var t=a;t<=t1;t+=st)out.push(t);out.step=st;
    return out;}

  // ---- facility and equipment lists: re-read on demand, the DOM touched only on a change
  function sameList(sel,vals){if(sel.options.length!==vals.length)return false;
    for(var i=0;i<vals.length;i++)if(sel.options[i].value!==vals[i][0]||
      sel.options[i].textContent!==vals[i][1])return false;return true;}
  function fillList(sel,vals,keep){if(sameList(sel,vals))return false;clear(sel);
    vals.forEach(function(v){opt(sel,v[0],v[1]);});
    if(keep!=null)Array.prototype.forEach.call(sel.options,function(o){
      if(o.value===keep)sel.value=keep;});
    return true;}
  function loadFacilities(first){
    var keep=facSel.value;
    return j('/facilities').then(function(d){
      var vals=(d.facilities||[]).map(function(f){
        var nm=f.display_name||f.name||f.facility_id;FTZ[f.facility_id]=f.timezone||null;
        return [f.facility_id,nm+(f.state&&f.state!=='active'?' ['+f.state+']':'')];});
      fillList(facSel,vals,keep||null);
      // deep link: /ui?facility_id=<fid> preselects that facility (camber lab links here)
      if(first){var want=new URLSearchParams(location.search).get('facility_id');
        if(want)Array.prototype.forEach.call(facSel.options,function(o){
          if(o.value===want)facSel.value=want;});}
      if(!facSel.options.length)return;
      if(first||facSel.value!==keep)return loadPoints(true);
      return loadPoints(false);
    });
  }
  // fresh: a new facility was picked (reset the span); otherwise keep the choices made
  function loadPoints(fresh){
    var fid=facSel.value;
    return j('/points?facility_id='+encodeURIComponent(fid)).then(function(d){
      if(fid!==facSel.value)return;
      AXIS=d.time_axis||AXIS;setZone();
      var eqs=[],seenE={},seenR={},roles=[];
      (d.points||[]).forEach(function(p){
        if(!seenE[p.equip]){seenE[p.equip]=1;eqs.push([p.equip,p.equip]);}
        if(!seenR[p.role]){seenR[p.role]=1;roles.push(p.role);}
      });
      var keepEq=fresh?null:eqSel.value;
      var eqChanged=fillList(eqSel,eqs,keepEq)&&!fresh&&eqSel.value!==keepEq;
      var was=checkedRoles();
      if(fresh||roles.join('\n')!==allRoles.join('\n')){
        allRoles=roles;clear(rolesBox);roles.forEach(function(r,i){
          var l=document.createElement('label'),c=document.createElement('input');
          c.type='checkbox';c.value=r;c.checked=fresh?i<3:was.indexOf(r)>=0;
          c.addEventListener('change',function(){draw();});
          l.appendChild(c);
          l.appendChild(document.createTextNode(' '+r+(unitOf(r)?' ('+unitOf(r)+')':'')));
          rolesBox.appendChild(l);});
      }
      if(fresh||eqChanged)resetView();
      return draw();
    });
  }
  function refreshAll(){return loadFacilities(false);}

  // ---- the window drawn
  function unselect(){pendingSel=null;if(window.CAMBER)window.CAMBER.set(new Set());}
  function resetView(){view={};views=[];extent=null;zoomBtn.disabled=true;}
  // a new window: a brushed span is selected once read (pendingSel), any other clears the selection
  function go(v){if(!pendingSel)unselect();views.push(view);view=v;zoomBtn.disabled=false;
    return draw();}
  function draw(){
    var fid=facSel.value,eq=eqSel.value;if(!fid||!eq)return Promise.resolve();
    var roles=checkedRoles(),win=view.a!=null,q='';
    if(win)q='&start='+encodeURIComponent(iso(view.a))+'&end='+encodeURIComponent(iso(view.b));
    q+='&max_points='+(win?WINDOW:OVERVIEW);
    busy++;
    // one request per role, so every ticked series gets its own sample budget
    return Promise.all(roles.map(function(r){
      return j('/history?facility_id='+encodeURIComponent(fid)+'&equip='+encodeURIComponent(eq)
        +'&role='+encodeURIComponent(r)+q);
    })).then(function(parts){
      busy--;
      var series=[],n=0,src=0,thin=false,lo=Infinity,hi=-Infinity;
      roles.forEach(function(r,i){var h=parts[i]||{};
        src+=h.source_count||0;if(h.downsampled)thin=true;
        if(h.first){lo=Math.min(lo,tms(h.first));hi=Math.max(hi,tms(h.last));}
        var pts=(h.history||[]).filter(function(x){return x.value!=null;})
          .map(function(x){var w=tms(x.ts);return {ts:x.ts,w:w,t:disp(w),v:x.value};})
          .sort(function(a,b){return a.t-b.t;});
        n+=pts.length;if(pts.length)series.push({role:r,unit:unitOf(r),pts:pts});
      });
      if(!win&&lo<=hi)extent={a:lo,b:hi};
      meta={n:n,src:src,thin:thin,a:win?view.a:lo,b:win?view.b:hi,win:win};
      last=series;render();showWindow();
      if(pendingSel){var ps=pendingSel,sel=new Set();pendingSel=null;
        series.forEach(function(s){s.pts.forEach(function(p){
          if(p.w>=ps.a&&p.w<=ps.b)sel.add(p.ts);});});
        if(window.CAMBER)window.CAMBER.set(sel);}
      updated.textContent='updated '+new Date().toLocaleTimeString()+' · '+num(n)+' points drawn';
    },function(){busy--;});
  }
  function showWindow(){
    if(!meta||!(meta.a<=meta.b)){winLine.textContent='no samples in this span';return;}
    var a=disp(meta.a),b=disp(meta.b);
    fromIn.value=dateOf(a);toIn.value=dateOf(b);
    winLine.textContent=(meta.win?'Showing ':'Showing all data, ')+fmtT(a)+' to '+fmtT(b)
      +' ('+axisLabel()+') · '+num(meta.n)+(meta.thin?' of '+num(meta.src)
      +' samples drawn (min and max of each time bucket kept)':' samples, every one drawn');
  }
  // a span of days in the display clock, from the date inputs or a preset
  function dayStart(s){return Date.parse(s+'T00:00:00Z');}
  function pickDates(){if(!fromIn.value||!toIn.value)return;
    var da=dayStart(fromIn.value),db=dayStart(toIn.value);if(isNaN(da)||isNaN(db))return;
    go({a:toWall(Math.min(da,db)),b:toWall(Math.max(da,db)+DAY-1000)});}
  function preset(days){
    if(!days){unselect();views=[];view={};zoomBtn.disabled=true;return draw();}
    var end=extent?extent.b:(meta&&meta.b);if(end==null)return;
    return go({a:end-days*DAY,b:end});}

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
    if(!series.length){svg.appendChild(mk('text',{x:L,y:40},
      view.a!=null?'no data in this span: zoom out or pick other dates'
      :'no data for this selection'));geo=null;return;}
    var t0=Infinity,t1=-Infinity;
    // a window spans exactly what was asked for; the whole span, the data's own extent
    if(view.a!=null){t0=disp(view.a);t1=disp(view.b);}
    else series.forEach(function(s){
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
    svg.appendChild(mk('text',{x:W-R,y:yb+28,'text-anchor':'end','class':'xlabel'},axisLabel()));
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
    head.textContent=fmtT(t)+' '+zoneTag();tip.appendChild(head);
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
  function clampX(x){return Math.max(geo.L,Math.min(geo.W-geo.R,x));}
  svg.addEventListener('mousedown',function(e){if(!geo)return;e.preventDefault();
    x0=clampX(pxOf(e));
    brush=mk('rect',{y:geo.top,height:geo.bot-geo.top,fill:'rgba(42,120,214,.15)'});
    svg.appendChild(brush);upd(x0);});
  svg.addEventListener('mousemove',function(e){if(!geo)return;if(brush)upd(clampX(pxOf(e)));
    showTip(e);});
  svg.addEventListener('mouseleave',hideTip);
  // brush to zoom: the span is re-read at full resolution and its samples are selected
  window.addEventListener('mouseup',function(e){
    if(!brush)return;var x1=clampX(pxOf(e));brush.parentNode.removeChild(brush);brush=null;
    if(Math.abs(x1-x0)<5){unselect();return;}
    var a=toWall(tOf(Math.min(x0,x1))),b=toWall(tOf(Math.max(x0,x1)));
    pendingSel={a:a,b:b};go({a:Math.floor(a),b:Math.ceil(b)});});
  function upd(x1){var a=Math.min(x0,x1),b=Math.max(x0,x1);
    brush.setAttribute('x',a);brush.setAttribute('width',Math.max(b-a,1));}
  if(window.CAMBER)window.CAMBER.onChange(function(sel){
    var a=Array.from(sel).sort();
    readout.textContent=a.length?(a.length+' selected: '+a[0]+' … '+a[a.length-1])
      :'brush the trend to zoom to a time span and select it';
  });
  function reschedule(){if(timer)clearInterval(timer);
    var s=Math.max(2,parseInt(intBox.value,10)||15);
    timer=setInterval(function(){if(liveBox.checked&&!busy)refreshAll();},s*1000);}
  var rz=null;
  window.addEventListener('resize',function(){clearTimeout(rz);rz=setTimeout(render,120);});
  facSel.addEventListener('change',function(){AXIS={timezone:null,
    local_label:'local time (no time zone recorded)',utc_label:null};unselect();
    loadPoints(true);});
  eqSel.addEventListener('change',function(){unselect();resetView();draw();});
  // a facility or equipment ingested after the page opened appears when its list is opened
  var lastList=0;
  function onOpen(){var now=Date.now();if(now-lastList<2000)return;lastList=now;refreshAll();}
  facSel.addEventListener('mousedown',onOpen);facSel.addEventListener('focus',onOpen);
  eqSel.addEventListener('mousedown',onOpen);eqSel.addEventListener('focus',onOpen);
  $('lists').addEventListener('click',refreshAll);
  normBox.addEventListener('change',render);
  utcBox.addEventListener('change',function(){(last||[]).forEach(function(s){
    s.pts.forEach(function(p){p.t=disp(p.w);});
    s.pts.sort(function(a,b){return a.t-b.t;});});render();showWindow();});
  fromIn.addEventListener('change',pickDates);toIn.addEventListener('change',pickDates);
  Array.prototype.forEach.call(document.querySelectorAll('button.preset'),function(btn){
    btn.addEventListener('click',function(){preset(+btn.getAttribute('data-days'));});});
  zoomBtn.addEventListener('click',function(){if(!views.length)return;unselect();
    view=views.pop();zoomBtn.disabled=!views.length;draw();});
  intBox.addEventListener('change',reschedule);
  $('refresh').addEventListener('click',refreshAll);
  loadFacilities(true).then(reschedule);
})();
"""


def live_dashboard_html() -> str:
    """Return the self-contained live-dashboard HTML (inline JS/CSS, no external assets).

    The page fetches ``/facilities``, ``/points``, and ``/history`` same-origin and polls; it reuses
    the theme (`camber.report.dashboard._STYLE`) and the `window.CAMBER` cross-panel selection bus.
    Served at ``GET /ui`` by :class:`camber.api.server.ReadAPIHandler`. 0.96 (#78): the ticked
    series are drawn in one panel per unit (:func:`role_units`; each with a labelled y axis), on a
    shared, labelled time axis, with a legend, a hover readout, and a normalised (0-1) toggle. The
    time axis and the hover readout show site time, labelled with the facility's zone, when
    ``/facilities`` reports a ``timezone`` (a UTC box converts).

    0.103 (#122): the page opens on each series' whole span, thinned server-side to a min/max
    envelope (``/history?max_points=``), and says which span it shows and how many samples it
    drew. Date inputs and presets (last 7 / 30 days of data, all) pick a window; brushing a panel
    zooms to that span, re-read at full resolution (up to a cap), and selects its samples; **Zoom
    out** steps back. The facility and equipment lists are re-read on **Reload lists**,
    **Refresh**, the live poll, or when a list is opened. The time-axis label comes from
    ``/points``' ``time_axis``: with no recorded zone it reads ``local time (no time zone
    recorded)`` and the page never claims UTC.
    """
    style = _STYLE + LINK_STYLE + _UI_STYLE
    units = json.dumps(role_units(), ensure_ascii=False).replace("<", "\\u003c")
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>CAMBER — live dashboard</title><style>{style}</style></head><body>"
        "<h1>CAMBER — live dashboard</h1>"
        "<p class='muted'>Live view of the read-only store. One panel per unit; hover for "
        "values, brush a panel to zoom to a time span.</p>"
        + _CONTROLS_HTML
        + "<div id='legend' class='legend' aria-label='legend'></div>"
        + "<div class='chartbox'><svg id='trend' class='trend' viewBox='0 0 1000 240' "
        "role='img' aria-label='trend chart'></svg><div id='tip' class='tip'></div></div>"
        + "<div id='readout' class='camber-out'>brush the trend to zoom to a time span and select "
        "it</div>"
        + selection_bus_html()
        + "<script>"
        + _APP_JS.replace("__UNITS__", units)
        .replace("__OVERVIEW__", str(_OVERVIEW_POINTS))
        .replace("__WINDOW__", str(_WINDOW_POINTS))
        + "</script></body></html>"
    )
