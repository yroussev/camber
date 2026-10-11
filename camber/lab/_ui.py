"""The ``camber lab`` catalog page: one vanilla-JS HTML page, no framework, no CDN.

Everything is inline and fixed text, so the page's Content-Security-Policy can pin the script and
the stylesheet by their SHA-256 (:data:`LAB_CSP`) instead of allowing ``'unsafe-inline'``: the
browser runs exactly this script and nothing else. No markup carries a ``style`` attribute or an
inline event handler, and data from the catalog is only ever set as ``textContent`` / attribute
values (never parsed as HTML). The per-run CSRF token is the one dynamic value, in a ``<meta>``
tag outside the hashed script.

The page talks only to its own origin: ``GET /lab/catalog`` and ``/lab/jobs`` (polled), and
``POST /lab/jobs/fetch|ingest|from-dir|remove`` and ``/lab/jobs/<id>/cancel`` with the token
header and a JSON body. Before a fetch the page runs the server's own disk check (download, archive
extraction and store, with the fetch's headroom) and disables a row whose optional extra is not
installed (0.103, #123). Research-only (NC / ND) entries open a modal that states the licence
terms and asks the user to type the dataset id before a fetch is queued. Links go to the trend
viewer (``/ui?facility_id=``), the on-demand report (``/lab/reports/<fid>``), the workbook
exercise (0.97: the lab's ``/lab/docs/...`` copy or the docs site) and the publisher's page. A
row also has a "Remove…" button (a typed-id confirmation; optionally purging the store's
facilities) and, for a manual entry, "From a folder…" (0.103).
"""

from __future__ import annotations

import base64
import hashlib
import html

_CSS = r"""
:root{--bg:#f7f7f5;--fg:#1d1d1b;--muted:#6b6b66;--card:#fff;--line:#dcdcd6;--acc:#2f5fb3;
--ok:#1e7a3c;--warn:#9a5b00;--bad:#b3261e;--chip:#eef1f6}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--fg:#ececea;--muted:#a3a39c;--card:#20201e;
--line:#3a3a36;--acc:#7fa6ea;--ok:#6cc98a;--warn:#e0a84a;--bad:#f08a80;--chip:#2a2e36}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,-apple-system,
"Segoe UI",Roboto,sans-serif}
main{max-width:1180px;margin:0 auto;padding:16px}
h1{font-size:22px;margin:4px 0 2px}h2{font-size:17px;margin:22px 0 8px}
a{color:var(--acc)}.muted{color:var(--muted);font-size:13px}
.bar{display:flex;flex-wrap:wrap;gap:10px 14px;align-items:center;margin:10px 0}
.bar label{font-size:14px}
input,select,button{font:inherit;color:inherit}
input[type=search],select{background:var(--card);border:1px solid var(--line);border-radius:6px;
padding:5px 8px;max-width:100%}
button{background:var(--card);border:1px solid var(--line);border-radius:6px;padding:6px 12px;
cursor:pointer}
button.primary{background:var(--acc);border-color:var(--acc);color:#fff}
button:disabled{opacity:.5;cursor:default}
.wrap{overflow-x:auto;border:1px solid var(--line);border-radius:8px;background:var(--card)}
table{border-collapse:collapse;width:100%;min-width:640px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:13px;color:var(--muted);font-weight:600;white-space:nowrap}
tr:last-child td{border-bottom:0}
td.num{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
.title{font-weight:600}.id{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;
color:var(--muted)}
.teach{font-size:12px;color:var(--muted);margin-top:2px}
.teach summary{cursor:pointer;width:max-content;max-width:100%}
.teach ul{margin:4px 0 2px;padding-left:18px}
.badge{display:inline-block;border-radius:10px;padding:1px 8px;font-size:12px;white-space:nowrap;
background:var(--chip)}
.badge.open{color:var(--ok)}.badge.ro{color:var(--bad);font-weight:600}
.badge.manual{color:var(--warn)}.badge.missing{color:var(--bad);font-weight:600}
.badge.synthetic{color:var(--warn);font-weight:600}
.lic{white-space:nowrap}.hint{font-size:12px;color:var(--bad);overflow-wrap:anywhere}
td.links button{font-size:13px;padding:2px 8px;margin:2px 8px 2px 0}
.links a{margin-right:8px;white-space:nowrap}
.state{font-size:12px}.state.ok{color:var(--ok)}.state.warn{color:var(--warn)}
.sum{font-size:14px}.sum.over{color:var(--bad);font-weight:600}
.jobs{display:flex;flex-direction:column;gap:8px}
.job{border:1px solid var(--line);border-radius:8px;padding:8px 10px;background:var(--card)}
.job .head{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.job progress{width:100%;height:10px;margin-top:6px}
.job .msg{font-size:13px;color:var(--muted);overflow-wrap:anywhere}
.job .err{font-size:13px;color:var(--bad);overflow-wrap:anywhere}
.job.failed{border-color:var(--bad)}.job.done{border-color:var(--ok)}
dialog{border:1px solid var(--line);border-radius:10px;background:var(--card);color:var(--fg);
max-width:min(560px,calc(100vw - 32px));padding:18px}
dialog::backdrop{background:rgba(0,0,0,.45)}
dialog h3{margin:0 0 8px;font-size:17px}
dialog .terms{border-left:3px solid var(--bad);padding:6px 10px;margin:10px 0;font-size:14px}
dialog input[type=text]{width:100%;padding:6px 8px;border:1px solid var(--line);border-radius:6px;
background:var(--bg);font-family:ui-monospace,Menlo,Consolas,monospace}
dialog .row{display:flex;gap:10px;justify-content:flex-end;margin-top:14px;flex-wrap:wrap}
dialog .terms.info{border-left-color:var(--acc)}
dialog .blocked{color:var(--bad);font-size:14px}
.notice{border:1px solid var(--line);border-radius:8px;padding:8px 12px;background:var(--card);
font-size:14px}
.notice.err{border-color:var(--bad);color:var(--bad)}
.hidden{display:none}
#where{overflow-wrap:anywhere}
@media (max-width:720px){.hide-sm{display:none}main{padding:12px}h1{font-size:19px}
table,tbody,tr,td{display:block;min-width:0}thead{display:none}
tr{padding:8px 10px;border-bottom:1px solid var(--line)}tr:last-child{border-bottom:0}
td{border:0;padding:2px 0}td.num{text-align:left}td.pick{float:right;padding:2px 0 0 8px}
td[data-label]::before{content:attr(data-label) ": ";color:var(--muted);font-size:12px}
.links a{display:inline-block;margin:2px 12px 2px 0}}
"""

_JS = r"""
(function(){
"use strict";
var TOKEN=document.querySelector('meta[name="camber-lab-token"]').getAttribute('content');
var $=function(id){return document.getElementById(id);};
var data=null,selected={},subset='default',jobsSeen={},pollTimer=null;

function el(tag,attrs,text){var e=document.createElement(tag);
  if(attrs)Object.keys(attrs).forEach(function(k){
    if(k==='class')e.className=attrs[k];else e.setAttribute(k,attrs[k]);});
  if(text!=null)e.textContent=String(text);return e;}
function clear(n){while(n.firstChild)n.removeChild(n.firstChild);}
function bytes(n){if(n==null)return '?';var u=['B','KB','MB','GB','TB'],i=0;n=Number(n);
  while(n>=1000&&i<u.length-1){n/=1000;i++;}return (i?n.toFixed(n<10?1:0):n)+' '+u[i];}
function getJSON(url){return fetch(url,{credentials:'same-origin',cache:'no-store'})
  .then(function(r){return r.json().then(function(j){if(!r.ok)throw new Error(j.error||r.status);
    return j;});});}
function post(url,body){return fetch(url,{method:'POST',credentials:'same-origin',
  headers:{'Content-Type':'application/json','X-Camber-Lab-Token':TOKEN},
  body:JSON.stringify(body||{})}).then(function(r){return r.json().then(function(j){
    if(!r.ok)throw new Error(j.error||('HTTP '+r.status));return j;});});}
function notice(msg,isErr){var n=$('notice');n.textContent=msg||'';
  n.className='notice'+(isErr?' err':'')+(msg?'':' hidden');}

function sub(d){return (d.subsets&&d.subsets[subset])||{};}
function fetched(d){return !!(d.fetched&&d.fetched[subset]);}
function visible(d){
  var q=$('q').value.trim().toLowerCase(),lic=$('lic').value,kind=$('kind').value;
  if(q&&(d.id+' '+d.title+' '+d.summary+' '+(d.teaches||[]).join(' ')).toLowerCase()
    .indexOf(q)<0)return false;
  if(lic==='open'&&d.research_only)return false;
  if(lic==='research'&&!d.research_only)return false;
  if(kind&&d.kind!==kind)return false;
  if($('labeled').checked&&!d.labeled_faults)return false;
  if($('have').checked&&!(d.facilities||[]).length)return false;
  return true;}

function render(){
  var body=$('rows');clear(body);var shown=0;
  data.datasets.forEach(function(d){
    if(!visible(d))return;shown++;
    var tr=el('tr');
    var c0=el('td',{'class':'pick'}),cb=el('input',{type:'checkbox','aria-label':'select '+d.id});
    var miss=d.missing_extras||[];
    if(d.manual||miss.length)delete selected[d.id];
    cb.checked=!!selected[d.id];cb.disabled=d.manual||miss.length>0;
    cb.addEventListener('change',function(){if(cb.checked)selected[d.id]=1;
      else delete selected[d.id];summary();});
    c0.appendChild(cb);tr.appendChild(c0);
    var c1=el('td');c1.appendChild(el('div',{'class':'title'},d.title));
    c1.appendChild(el('div',{'class':'id'},d.id+(d.equipment?' · '+d.equipment:'')));
    if(d.teaches&&d.teaches.length){
      // collapsed by default (the list is long); opened when the search matches its text
      var det=el('details',{'class':'teach'});
      det.appendChild(el('summary',null,'what it teaches ('+d.teaches.length+')'));
      var ul=el('ul');d.teaches.forEach(function(t){ul.appendChild(el('li',null,t));});
      det.appendChild(ul);
      var q=$('q').value.trim().toLowerCase();
      if(q&&d.teaches.join(' ').toLowerCase().indexOf(q)>=0)det.open=true;
      c1.appendChild(det);}
    tr.appendChild(c1);
    tr.appendChild(el('td',{'class':'hide-sm'},d.kind+(d.labeled_faults?' · labelled':'')));
    var c3=el('td',{'data-label':'Licence'});
    c3.appendChild(el('span',{'class':'badge '+(d.research_only?'ro':'open'),
      title:d.research_only?(d.access_reason||'non-commercial / no-derivatives licence'):
      'open licence'},d.research_only?'research-only':'open'));
    c3.appendChild(document.createTextNode(' '));
    c3.appendChild(el('span',{'class':'id lic'},d.licence));
    if(d.manual){c3.appendChild(document.createTextNode(' '));
      c3.appendChild(el('span',{'class':'badge manual',title:'download it yourself, then '+
        'ingest it from a folder'},'manual'));}
    // 0.103 (#133): generated on this computer by CAMBER; no real building behind it
    if(d.synthetic){c3.appendChild(document.createTextNode(' '));
      c3.appendChild(el('span',{'class':'badge synthetic',title:'generated locally by CAMBER '+
        '(nothing is downloaded); it describes no real building'},'synthetic'));}
    // 0.103 (#123): the optional extras an entry needs; a missing one blocks the fetch
    (d.requires_extras||[]).forEach(function(x){
      var m=miss.filter(function(k){return k.extra===x;})[0];
      c3.appendChild(document.createTextNode(' '));
      c3.appendChild(el('span',{'class':'badge'+(m?' missing':''),title:m?('not installed: '+
        (m.hint||x)):'optional extra '+x+' (installed)'},'needs '+x));
      if(m)c3.appendChild(el('div',{'class':'hint'},'install first: '+(m.hint||x)));});
    tr.appendChild(c3);
    tr.appendChild(el('td',{'class':'num','data-label':'Download'},
      d.synthetic?'generated':bytes(sub(d).download_bytes)));
    tr.appendChild(el('td',{'class':'num hide-sm'},sub(d).store_bytes==null?'?':
      bytes(sub(d).store_bytes)));
    var c6=el('td',{'data-label':'Status'});
    var fac=d.facilities||[];
    if(fac.length){fac.forEach(function(f){c6.appendChild(el('div',{'class':'state ok'},
      f.facility_id+(f.state&&f.state!=='active'?' ['+f.state+']':'')+' · '+
      (f.rows!=null?Number(f.rows).toLocaleString()+' rows':'')));});}
    else c6.appendChild(el('div',{'class':'state'+(fetched(d)?' warn':'')},
      fetched(d)?(d.synthetic?'generated, not ingested':'fetched, not ingested'):
      (d.synthetic?'not generated':'not fetched')));
    tr.appendChild(c6);
    var c7=el('td',{'class':'links'});
    fac.forEach(function(f){
      c7.appendChild(el('a',{href:'/ui?facility_id='+encodeURIComponent(f.facility_id)},
        'trends'));
      if(d.report)c7.appendChild(el('a',{href:'/lab/reports/'+
        encodeURIComponent(f.facility_id),target:'_blank',rel:'noopener'},'report'));});
    if(/^(https:\/\/|\/lab\/docs\/workbook\/)/.test(d.exercise||''))c7.appendChild(el('a',
      {href:d.exercise,target:'_blank',rel:'noopener noreferrer'},'exercise'));
    if(/^https:\/\//.test(d.landing_url||''))c7.appendChild(el('a',{href:d.landing_url,
      target:'_blank',rel:'noopener noreferrer'},d.synthetic?'how it is made':'publisher'));
    if(d.manual){var bf=el('button',{type:'button'},'From a folder…');bf.disabled=miss.length>0;
      bf.addEventListener('click',function(){fromFolder(d);});c7.appendChild(bf);}
    var have=fac.length||d.bytes_on_disk>0||Object.keys(d.fetched||{}).some(function(k){
      return d.fetched[k];});
    if(have){var br=el('button',{type:'button'},'Remove…');
      br.addEventListener('click',function(){removeDialog(d);});c7.appendChild(br);}
    tr.appendChild(c7);
    body.appendChild(tr);
  });
  $('shown').textContent=shown+' of '+data.datasets.length+' datasets shown';
  summary();}

function chosen(){return data.datasets.filter(function(d){return selected[d.id];});}
// 0.103 (#123): the same disk check the server runs before it queues a job (and the fetch's own
// headroom): download + archive extraction in the cache, the ingest's estimate in the store,
// added together when both are on one filesystem
function withMargin(n){return n>0?Math.ceil(n*(1+(data.disk_margin||0))):0;}
function shortfall(cache,store){
  var free=data.free_bytes||{},out=[];
  function chk(label,need,fr){if(need&&fr!=null&&need>fr)out.push(label+' needs '+bytes(need)+
    ', '+bytes(fr)+' free');}
  if(data.same_disk)chk('cache + store',withMargin(cache+store),free.data_dir);
  else{chk('cache',withMargin(cache),free.data_dir);chk('store',withMargin(store),free.store);}
  return out;}
function summary(){
  var ch=chosen(),dl=0,ex=0,st=0;
  ch.forEach(function(d){var n=sub(d).needs||{};dl+=Number(n.download||0);
    ex+=Number(n.extract||0);st+=Number(n.store||0);});
  var free=data.free_bytes||{},fd=free.data_dir,fs=free.store;
  var pf=shortfall(dl+ex,st),pi=shortfall(ex,st),s=$('sum');
  var txt=ch.length?(ch.length+' selected · download '+bytes(dl)+(ex?' · extract ≈'+bytes(ex):'')+
    ' · store ≈'+bytes(st)+' · free: cache '+bytes(fd)+(data.same_disk?' (same disk as the store)':
    ', store '+bytes(fs))):'nothing selected';
  if(ch.length&&pf.length)txt+=' — not enough disk to fetch & ingest: '+pf.join('; ');
  if(ch.length&&!pf.length&&pi.length)txt+=' — not enough disk to ingest: '+pi.join('; ');
  s.textContent=txt;
  s.className='sum'+(ch.length&&pf.length?' over':'');
  $('go').disabled=!ch.length||pf.length>0;$('ing').disabled=!ch.length||pi.length>0;}

// the research-only modal: one entry at a time, the dataset id must be typed exactly
function acknowledge(list){
  var out={},i=0;
  return new Promise(function(resolve,reject){
    function next(){
      if(i>=list.length){resolve(out);return;}
      var d=list[i],dlg=$('ack');
      $('ack-title').textContent=d.title;
      $('ack-licence').textContent=d.licence+(d.access_reason?' — '+d.access_reason:'');
      $('ack-id').textContent=d.id;
      $('ack-cite').textContent=d.citation||'';
      var box=$('ack-check'),typed=$('ack-typed'),ok=$('ack-ok');
      box.checked=false;typed.value='';ok.disabled=true;
      function upd(){ok.disabled=!(box.checked&&typed.value===d.id);}
      box.onchange=upd;typed.oninput=upd;
      ok.onclick=function(){out[d.id]=typed.value;dlg.close('ok');};
      $('ack-cancel').onclick=function(){dlg.close('cancel');};
      dlg.onclose=function(){dlg.onclose=null;
        if(dlg.returnValue==='ok'){i++;next();}else reject(new Error('cancelled'));};
      dlg.returnValue='';dlg.showModal();typed.focus();
    }
    next();});}

function start(kind){
  var ch=chosen();if(!ch.length)return;
  var needs=ch.filter(function(d){return d.research_only&&(kind==='fetch'||!d.acknowledged);});
  acknowledge(needs).then(function(ack){
    var body={ids:ch.map(function(d){return d.id;}),subset:subset,acknowledge:ack};
    var url=kind==='fetch'?'/lab/jobs/fetch':'/lab/jobs/ingest';
    if(kind==='fetch')body.ingest=true;else body.force=$('force').checked;
    return post(url,body).then(function(r){notice('queued job '+r.job.id);selected={};
      render();pollJobs();});
  }).catch(function(e){notice(e.message==='cancelled'?'':e.message,e.message!=='cancelled');});}

// 0.103 (#123): remove a dataset's local files (and, optionally, its facilities in the store);
// the dataset id must be typed, and a workspace's lifecycle can forbid the purge
function removeDialog(d){
  var dlg=$('rm'),box=$('rm-purge'),typed=$('rm-typed'),ok=$('rm-ok'),blk=$('rm-blocked');
  var fac=(d.facilities||[]).map(function(f){return f.facility_id;});
  var blocked=d.purge_blocked||[];
  $('rm-title').textContent=d.title;$('rm-id').textContent=d.id;
  $('rm-what').textContent='Deletes its downloaded files and extractions from the cache ('+
    bytes(d.bytes_on_disk)+' on disk). The acknowledgements ledger is kept.';
  $('rm-fac').textContent=fac.length?fac.join(', '):'none ingested';
  box.checked=false;box.disabled=!fac.length||blocked.length>0;
  blk.textContent=blocked.length?('Cannot purge here: '+blocked.join('; ')):'';
  typed.value='';ok.disabled=true;
  function upd(){ok.disabled=typed.value!==d.id;}
  typed.oninput=upd;
  ok.onclick=function(){dlg.close('ok');};
  $('rm-cancel').onclick=function(){dlg.close('cancel');};
  dlg.onclose=function(){dlg.onclose=null;if(dlg.returnValue!=='ok')return;
    post('/lab/jobs/remove',{id:d.id,confirm:typed.value,purge_store:box.checked})
      .then(function(r){notice('queued job '+r.job.id);pollJobs();})
      .catch(function(e){notice(e.message,true);});};
  dlg.returnValue='';dlg.showModal();typed.focus();}

// 0.103 (#123): a manual entry's files, downloaded by hand, ingested from a local folder
// (camber datasets ingest --from-dir); the server checks the path and only reads it
function fromFolder(d){
  var dlg=$('fd'),path=$('fd-path'),ok=$('fd-ok');
  $('fd-title').textContent=d.title;
  $('fd-instr').textContent=d.manual_instructions||'Download the files from the publisher first.';
  ok.disabled=!path.value.trim();
  path.oninput=function(){ok.disabled=!path.value.trim();};
  ok.onclick=function(){dlg.close('ok');};
  $('fd-cancel').onclick=function(){dlg.close('cancel');};
  dlg.onclose=function(){dlg.onclose=null;if(dlg.returnValue!=='ok')return;
    var dir=path.value.trim();
    acknowledge(d.research_only&&!d.acknowledged?[d]:[]).then(function(ack){
      return post('/lab/jobs/from-dir',{id:d.id,dir:dir,subset:subset,
        force:$('force').checked,acknowledge:ack});
    }).then(function(r){notice('queued job '+r.job.id);pollJobs();})
    .catch(function(e){notice(e.message==='cancelled'?'':e.message,e.message!=='cancelled');});};
  dlg.returnValue='';dlg.showModal();path.focus();}

function renderJobs(list){
  var box=$('jobs');clear(box);
  if(!list.length){box.appendChild(el('div',{'class':'muted'},'no jobs yet'));return;}
  list.forEach(function(j){
    var d=el('div',{'class':'job '+j.state});
    var h=el('div',{'class':'head'});
    h.appendChild(el('strong',null,j.kind));
    h.appendChild(el('span',{'class':'id'},(j.params.ids||[]).join(', ')+' · '+
      (j.params.subset||'default')));
    h.appendChild(el('span',{'class':'badge'},j.state));
    if(j.state==='queued'||j.state==='running'){
      var b=el('button',null,j.cancel_requested?'cancelling…':'Cancel');
      b.disabled=j.cancel_requested;
      b.addEventListener('click',function(){b.disabled=true;
        post('/lab/jobs/'+j.id+'/cancel',{}).then(pollJobs).catch(function(e){
          notice(e.message,true);});});
      h.appendChild(b);}
    d.appendChild(h);
    if(j.state==='running'){var p=el('progress');
      if(j.total){p.max=j.total;p.value=j.done||0;}d.appendChild(p);}
    d.appendChild(el('div',{'class':'msg'},j.message+(j.total?' ('+bytes(j.done)+' of '+
      bytes(j.total)+')':'')));
    if(j.error)d.appendChild(el('div',{'class':'err'},j.error));
    (j.result||[]).forEach(function(r){
      if(r.fetch&&r.fetch.citation)d.appendChild(el('div',{'class':'msg'},
        r.id+' — please cite: '+r.fetch.citation));
      if(r.remove)d.appendChild(el('div',{'class':'msg'},r.id+': freed '+
        bytes(r.remove.freed_bytes)+((r.remove.facilities_dropped||[]).length?
        '; dropped from the store: '+r.remove.facilities_dropped.join(', '):'')));
      if(r.ingest)d.appendChild(el('div',{'class':'msg'},r.id+': '+(r.ingest.skipped?
        'already up to date':('ingested '+Number(r.ingest.rows).toLocaleString()+' rows into '+
        (r.ingest.facilities||[]).join(', ')))));});
    box.appendChild(d);});}

function pollJobs(){
  if(pollTimer)clearTimeout(pollTimer);
  return getJSON('/lab/jobs').then(function(r){
    var active=false,changed=false;
    r.jobs.forEach(function(j){var fin=j.state!=='queued'&&j.state!=='running';
      if(!fin)active=true;if(jobsSeen[j.id]!==j.state){if(fin&&jobsSeen[j.id])changed=true;
        jobsSeen[j.id]=j.state;}});
    renderJobs(r.jobs);
    if(changed)loadCatalog();
    pollTimer=setTimeout(pollJobs,active?1000:8000);
  }).catch(function(e){notice('lost contact with the lab server: '+e.message,true);
    pollTimer=setTimeout(pollJobs,8000);});}

function loadCatalog(){
  return getJSON('/lab/catalog').then(function(d){data=d;
    var dp=d.display||{};
    $('where').textContent=(d.mode==='workspace'?'workspace '+dp.workspace:'store '+dp.store)+
      ' · cache '+dp.data_dir;
    var subs={};d.datasets.forEach(function(x){Object.keys(x.subsets||{}).forEach(
      function(s){subs[s]=1;});});
    var sel=$('subset');if(!sel.options.length)Object.keys(subs).sort().forEach(function(s){
      var o=el('option',{value:s},s);if(s===subset)o.selected=true;sel.appendChild(o);});
    render();}).catch(function(e){notice('could not load the catalog: '+e.message,true);});}

['q','lic','kind','labeled','have'].forEach(function(id){
  $(id).addEventListener(id==='q'?'input':'change',function(){if(data)render();});});
$('subset').addEventListener('change',function(){subset=$('subset').value;if(data)render();});
$('go').addEventListener('click',function(){start('fetch');});
$('ing').addEventListener('click',function(){start('ingest');});
loadCatalog().then(pollJobs);
})();
"""

_BODY = """
<main>
<h1>CAMBER lab</h1>
<div class="muted">Open building datasets: fetch from the publisher, ingest, then view trends and
reports. Loopback only. <span id="where"></span></div>
<div id="notice" class="notice hidden" role="status"></div>
<h2>Catalog</h2>
<div class="bar">
<input type="search" id="q" placeholder="search datasets" aria-label="search datasets">
<label>Licence <select id="lic"><option value="">all</option><option value="open">open</option>
<option value="research">research-only</option></select></label>
<label>Kind <select id="kind"><option value="">all</option><option value="simulated">simulated
</option><option value="real">real</option><option value="lab">lab</option>
<option value="synthetic">synthetic</option></select></label>
<label><input type="checkbox" id="labeled"> labelled faults</label>
<label><input type="checkbox" id="have"> ingested</label>
<span id="shown" class="muted"></span>
</div>
<div class="wrap"><table>
<thead><tr><th></th><th>Dataset</th><th class="hide-sm">Kind</th><th>Licence</th>
<th>Download</th><th class="hide-sm">Store</th><th>Status</th><th>Open</th></tr></thead>
<tbody id="rows"></tbody></table></div>
<div class="bar">
<label>Subset <select id="subset"></select></label>
<button id="go" class="primary" disabled>Fetch &amp; ingest</button>
<button id="ing" disabled>Ingest (already fetched)</button>
<label title="ingest again even when the data and the mapping are unchanged"><input
type="checkbox" id="force"> force re-ingest</label>
<span id="sum" class="sum"></span>
</div>
<p class="muted">Research-only datasets (non-commercial or no-derivatives licences) ask you to
acknowledge their terms and type the dataset id; the acknowledgement is recorded in the cache's
acknowledgements ledger, and every report built from the data carries a do-not-redistribute
banner. CAMBER redistributes no data.</p>
<h2>Jobs</h2>
<div id="jobs" class="jobs"></div>
</main>
<dialog id="ack" aria-labelledby="ack-h">
<h3 id="ack-h">Research-only dataset</h3>
<div class="title" id="ack-title"></div>
<div class="terms">Licence <strong id="ack-licence"></strong>: research and non-commercial use
only. You may not sell it or redistribute it, or anything derived from it. CAMBER downloads it
from the publisher for you; the acknowledgement is recorded in the acknowledgements ledger.</div>
<div class="muted" id="ack-cite"></div>
<p><label><input type="checkbox" id="ack-check"> I accept these terms for this dataset.</label></p>
<p><label for="ack-typed">Type the dataset id <code id="ack-id"></code> to confirm:</label>
<input type="text" id="ack-typed" autocomplete="off" spellcheck="false"></p>
<div class="row"><button id="ack-cancel">Cancel</button>
<button id="ack-ok" class="primary" disabled>Acknowledge</button></div>
</dialog>
<dialog id="rm" aria-labelledby="rm-h">
<h3 id="rm-h">Remove a dataset</h3>
<div class="title" id="rm-title"></div>
<div class="terms" id="rm-what"></div>
<p><label><input type="checkbox" id="rm-purge"> Also drop its facilities from the store
(<span id="rm-fac"></span>). Its trends and reports go with them; the ids stay reserved.</label></p>
<div class="blocked" id="rm-blocked"></div>
<p><label for="rm-typed">Type the dataset id <code id="rm-id"></code> to confirm:</label>
<input type="text" id="rm-typed" autocomplete="off" spellcheck="false"></p>
<div class="row"><button id="rm-cancel">Cancel</button>
<button id="rm-ok" class="primary" disabled>Remove</button></div>
</dialog>
<dialog id="fd" aria-labelledby="fd-h">
<h3 id="fd-h">Ingest from a folder</h3>
<div class="title" id="fd-title"></div>
<div class="terms info" id="fd-instr"></div>
<p><label for="fd-path">The folder that holds the downloaded files (an absolute path; CAMBER
only reads it, verifies each file and copies it into the cache):</label>
<input type="text" id="fd-path" autocomplete="off" spellcheck="false"
placeholder="/path/to/the/downloaded/files"></p>
<div class="row"><button id="fd-cancel">Cancel</button>
<button id="fd-ok" class="primary" disabled>Ingest</button></div>
</dialog>
"""


def _sha256_source(text: str) -> str:
    return "'sha256-" + base64.b64encode(hashlib.sha256(text.encode()).digest()).decode() + "'"


# The page's policy: nothing loads but this exact script and stylesheet (pinned by hash) and
# same-origin fetches; no frames, no forms, no base-uri games.
LAB_CSP = (
    "default-src 'none'; "
    f"script-src {_sha256_source(_JS)}; "
    f"style-src {_sha256_source(_CSS)}; "
    "img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none'; "
    "frame-ancestors 'none'; object-src 'none'"
)


def lab_page_html(token: str) -> str:
    """The catalog page, with the per-run CSRF ``token`` in a ``<meta>`` tag."""
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<meta name='referrer' content='no-referrer'>"
        f"<meta name='camber-lab-token' content='{html.escape(token, quote=True)}'>"
        f"<title>CAMBER lab</title><style>{_CSS}</style></head><body>"
        + _BODY
        + f"<script>{_JS}</script></body></html>"
    )
