// generated from page.html by mkpage.py
#pragma once
const char PAGE_HTML[] PROGMEM = R"ledctl_raw(<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover,user-scalable=no">
<meta name="theme-color" content="#0b0b0f">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="ledctl">
<link rel="manifest" href="/manifest.json">
<link rel="icon" href="/icon.svg">
<link rel="apple-touch-icon" href="/icon.svg">
<title>ledctl</title>
<style>
:root{--bg:#0b0b0f;--card:#16161d;--line:#24242e;--txt:#f2f2f5;--dim:#8a8a99;--c:#ff9a3c}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{margin:0;background:var(--bg);color:var(--txt);font:15px/1.4 -apple-system,system-ui,"Segoe UI",Roboto,sans-serif}
body{padding:calc(env(safe-area-inset-top) + 14px) 16px calc(env(safe-area-inset-bottom) + 24px);max-width:480px;margin:0 auto;user-select:none;-webkit-user-select:none}
header{display:flex;align-items:center;justify-content:space-between;margin-bottom:6px}
h1{font-size:20px;margin:0;letter-spacing:.5px}
.status{font-size:12px;color:var(--dim);display:flex;align-items:center;gap:8px}
.dot{width:9px;height:9px;border-radius:50%;background:#3a3a46;transition:background .08s,box-shadow .08s}
.dot.on{background:var(--c);box-shadow:0 0 10px var(--c)}
.orb-wrap{display:flex;justify-content:center;padding:22px 0 18px}
.orb{width:150px;height:150px;border-radius:50%;background:var(--c);box-shadow:0 0 60px 6px var(--c);transition:background .15s,box-shadow .15s,transform .09s,opacity .2s}
.orb.off{background:#1d1d25;box-shadow:none}
.orb.kick{transform:scale(1.08)}
.modes{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:16px}
.modes button{background:var(--card);border:1px solid var(--line);color:var(--txt);border-radius:14px;padding:14px 4px;font:600 14px/1 inherit;font-family:inherit;display:flex;flex-direction:column;align-items:center;gap:7px}
.modes button span{font-size:20px;line-height:1}
.modes button.wide{grid-column:span 3;flex-direction:row;justify-content:center;gap:10px;font-size:16px;padding:16px 4px;background:linear-gradient(90deg,#1d1426,#14202a)}
.modes button.sel{border-color:var(--c);box-shadow:inset 0 0 0 1px var(--c);background:#1d1d27}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:14px 14px 6px;margin-bottom:12px}
.row{margin-bottom:12px}
.lab{display:flex;justify-content:space-between;font-size:13px;color:var(--dim);margin-bottom:8px}
.lab b{color:var(--txt);font-weight:600}
input[type=range]{-webkit-appearance:none;appearance:none;width:100%;height:30px;border-radius:15px;background:#2a2a35;outline:none;margin:0}
input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;width:30px;height:30px;border-radius:50%;background:#fff;border:3px solid rgba(0,0,0,.35);box-shadow:0 1px 4px rgba(0,0,0,.5)}
input[type=range]::-moz-range-thumb{width:24px;height:24px;border-radius:50%;background:#fff;border:3px solid rgba(0,0,0,.35)}
#hue{background:linear-gradient(90deg,#f00,#ff0,#0f0,#0ff,#00f,#f0f,#f00)}
.swatches{display:flex;gap:9px;flex-wrap:wrap;margin-bottom:12px}
.swatches button{width:36px;height:36px;border-radius:50%;border:2px solid rgba(255,255,255,.12)}
.hide{display:none}
.meter{height:10px;border-radius:5px;background:#2a2a35;overflow:hidden;margin-bottom:4px}
#mbar{height:100%;width:0;background:var(--c);transition:width .08s}
.modes button.offb{grid-column:span 3;flex-direction:row;justify-content:center;padding:11px 4px}
.modes button.offb2{grid-column:span 2;flex-direction:row;justify-content:center;gap:8px}
.sub{text-align:center;color:var(--dim);font-size:14px;margin:-6px 0 14px;min-height:20px;letter-spacing:.3px}
.seg{display:grid;grid-template-columns:1fr 1fr;background:#2a2a35;border-radius:12px;padding:3px;gap:3px}
.seg button{border:0;border-radius:9px;padding:10px 6px;background:transparent;color:var(--dim);font:600 13px/1.2 inherit;font-family:inherit}
.seg button.on{background:var(--card);color:var(--txt);box-shadow:0 0 0 1px var(--c)}
.spec{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:10px}
.spec div{height:56px;border-radius:10px;background:#2a2a35;position:relative;overflow:hidden}
.spec i{position:absolute;left:0;right:0;bottom:0;height:0;background:var(--c);opacity:.9;transition:height .08s}
.spec span{position:absolute;left:8px;top:6px;font-size:11px;color:#fff;opacity:.8}
.note{font-size:11px;color:var(--dim);margin-top:6px}
footer{text-align:center;color:var(--dim);font-size:12px;margin-top:8px}
.err{color:#ff6b6b}
</style>
</head>
<body>
<header>
  <h1>ledctl</h1>
  <div class="status"><span id="st">connecting…</span><div class="dot" id="dot"></div></div>
</header>

<div class="orb-wrap"><div class="orb" id="orb"></div></div>
<div class="sub" id="sub"></div>

<div class="modes" id="modes">
  <button data-m="music" class="wide"><span>♪</span>Music</button>
  <button data-m="pulse"><span>◉</span>Pulse</button>
  <button data-m="party"><span>✦</span>Party</button>
  <button data-m="chill"><span>≈</span>Chill</button>
  <button data-m="solid"><span>●</span>Solid</button>
  <button data-m="breathe"><span>◐</span>Breathe</button>
  <button data-m="study"><span>✎</span>Study</button>
  <button data-m="makeout"><span>♥</span>Make out</button>
  <button data-m="off" class="offb2"><span>⏻</span>Off</button>
</div>

<div class="card" id="colourCard">
  <div class="swatches" id="sw"></div>
  <div class="row"><div class="lab">Colour <b id="hv"></b></div><input type="range" id="hue" min="0" max="359"></div>
  <div class="row"><div class="lab">Saturation <b id="sv"></b></div><input type="range" id="sat" min="0" max="100"></div>
</div>

<div class="card">
  <div class="row"><div class="lab">Brightness <b id="vv"></b></div><input type="range" id="bri" min="5" max="100"></div>
  <div class="row hide" id="srcRow"><div class="lab">Listen to</div>
    <div class="seg"><button id="srcMic" class="on">Board mic</button><button id="srcPhone">This phone's music</button></div>
    <div class="note" id="phoneNote"></div></div>
  <div class="row hide" id="micRow"><div class="lab">Sound <b id="mv"></b></div>
    <div class="spec"><div><i id="bb"></i><span>Bass</span></div><div><i id="bm"></i><span>Mid</span></div><div><i id="bh"></i><span>Treble</span></div></div>
    <div class="meter"><div id="mbar"></div></div><div class="note" id="nf"></div></div>
  <div class="row hide" id="sensRow"><div class="lab">Beat sensitivity <b id="nv"></b></div><input type="range" id="sens" min="1" max="10"></div>
</div>

<footer id="ft"></footer>

<script>
const $=id=>document.getElementById(id);
const S={mode:'solid',h:30,s:70,v:60,sens:5,beats:0,ma:-1,cap:100,ph:0};
const SW=[[30,70],[0,100],[25,100],[45,100],[120,100],[180,100],[220,100],[275,100],[320,90],[0,0]];
let lastBeats=-1,pending={},timer=null,inflight=false,dragging=false;

function hsl(h,s,v){ // display colour, keep it vivid even at low brightness
  const l=100-s/2; return `hsl(${h} ${s}% ${Math.min(l,62)}%)`;
}
function paint(){
  const off=S.mode==='off', mk=S.mode==='makeout', sd=S.mode==='study', h=S.mode==='party'?S.ph:(mk?(330+Math.round(25*Math.sin(Date.now()/3000)))%360:S.h), s=(S.mode==='party'||mk)?100:S.s;
  const c=sd?((S.study||'').startsWith('Break')?'hsl(30 90% 60%)':'hsl(212 45% 86%)'):hsl(h,s,S.v);
  document.documentElement.style.setProperty('--c',c);
  const o=$('orb'); o.classList.toggle('off',off); o.style.opacity=off?1:(.35+.65*S.v/100);
  document.querySelectorAll('#modes button').forEach(b=>b.classList.toggle('sel',b.dataset.m===S.mode));
  const music=['music','pulse','party'].includes(S.mode);
  $('sensRow').classList.toggle('hide',!music);
  $('micRow').classList.toggle('hide',!['music','pulse','party','chill'].includes(S.mode));
  $('srcRow').classList.toggle('hide',!(APP||window.LAPTOP)||!['music','pulse','party','chill'].includes(S.mode));
  $('bb').style.height=(S.sb||0)+'%'; $('bm').style.height=(S.sm||0)+'%'; $('bh').style.height=(S.sh||0)+'%';
  $('nf').textContent=S.nf?'Background noise (fan, aircon) is learned and filtered out automatically':'';
  $('mbar').style.width=(S.lv||0)+'%';
  $('mv').textContent=(S.mic===undefined||S.mic<0)?'no data':(S.mic===0?'mic silent':(S.lk?`♪ ${S.bpm} BPM`:''));
  $('colourCard').classList.toggle('hide',['party','music','study','makeout','off'].includes(S.mode));
  const subs={music:'Colours follow the song',pulse:'Your colour, pulsing to the beat',party:'New colour every beat',chill:'Slow aurora around your colour',breathe:'Breathe with it: in 4 s, out 6 s',study:S.study||'Focus 25 min / break 5 min',makeout:'Candlelight reds and violets',solid:'',off:''};
  $('sub').textContent=subs[S.mode]||'';
  if(!dragging){ $('hue').value=S.h; $('sat').value=S.s; $('bri').value=S.v; $('sens').value=S.sens; }
  $('hv').textContent=S.h+'°'; $('sv').textContent=S.s+'%'; $('vv').textContent=S.v+'%'; $('nv').textContent=S.sens;
  $('sat').style.background=`linear-gradient(90deg,hsl(${S.h} 0% 60%),hsl(${S.h} 100% 50%))`;
  let f=S.ma>=0?`strip ${(S.ma/1000).toFixed(2)} A`:'';
  if(S.cap<100) f+=` · limited to ${S.cap}%`;
  if(S.fpga && S.fpga!=='ok') f+=` · FPGA: ${S.fpga}`;
  $('ft').textContent=f;
}
function send(p){
  Object.assign(pending,p); Object.assign(S,p.m?{mode:p.m}:{},p); paint();
  if(timer) return;
  timer=setTimeout(flush,60);
}
async function flush(){
  timer=null;
  if(inflight){ timer=setTimeout(flush,40); return; }
  const q=new URLSearchParams(pending).toString(); pending={};
  await api(q);
}
const APP=!!window.Android;
function gotState(j){
  const keep=dragging?{h:S.h,s:S.s,v:S.v,sens:S.sens}:{};
  Object.assign(S,j,keep);
  if(lastBeats>=0 && j.beats!==lastBeats) kick();
  lastBeats=j.beats;
  paint();
}
window.onState=function(t){ try{ gotState(JSON.parse(t)); }catch(e){} };
let phoneSrc=false;
try{ phoneSrc=localStorage.getItem('src')==='phone'; }catch(e){}
function setSrc(p){ phoneSrc=p; try{localStorage.setItem('src',p?'phone':'mic');}catch(e){}
  $('srcMic').classList.toggle('on',!p); $('srcPhone').classList.toggle('on',p);
  if(APP) Android.phoneAudio(p); else if(window.LAPTOP) fetch('/sync?on='+(p?1:0)).then(r=>r.text()).then(t=>$('phoneNote').textContent=t).catch(()=>{});
  if(!p) $('phoneNote').textContent=''; }
window.onPhone=function(t,ok){ $('phoneNote').textContent=t; $('phoneNote').style.color=ok?'':'#ff8a8a'; };
window.onConn=function(t,ok){ $('st').textContent=t; $('st').classList.toggle('err',!ok); };
async function api(q){
  if(APP){ if(q) Android.send(q); return; }
  inflight=true;
  try{
    const r=await fetch('/api'+(q?'?'+q:''),{cache:'no-store'});
    const j=await r.json();
    $('st').textContent='connected'; $('st').classList.remove('err');
    gotState(j);
  }catch(e){ $('st').textContent='not connected'; $('st').classList.add('err'); }
  inflight=false;
}
function kick(){
  $('dot').classList.add('on');
  if(['music','party'].includes(S.mode)) $('orb').classList.add('kick');
  setTimeout(()=>{$('dot').classList.remove('on');$('orb').classList.remove('kick')},110);
}
document.querySelectorAll('#modes button').forEach(b=>b.onclick=()=>send({m:b.dataset.m}));
SW.forEach(([h,s])=>{const b=document.createElement('button');b.style.background=s?`hsl(${h} ${s}% 55%)`:'#fff4e0';
  b.onclick=()=>{ if(S.mode==='off'||S.mode==='party') send({m:'solid',h,s}); else send({h,s}); }; $('sw').appendChild(b);});
[['hue','h'],['sat','s'],['bri','v'],['sens','sens']].forEach(([id,k])=>{
  const el=$(id);
  el.addEventListener('input',()=>{dragging=true; send({[k]:+el.value});});
  el.addEventListener('change',()=>{dragging=false;});
  el.addEventListener('pointerup',()=>{dragging=false;});
  el.addEventListener('touchend',()=>{dragging=false;});
});
if(!APP){ setInterval(()=>{ if(!inflight && !timer) api(''); },300); api(''); }
else { $('st').textContent='searching…'; setInterval(paint,200); }
$('srcMic').onclick=()=>setSrc(false); $('srcPhone').onclick=()=>setSrc(true);
if(window.LAPTOP){ $('srcPhone').textContent="This laptop's music"; setSrc(true); }
else if(APP && phoneSrc) setTimeout(()=>setSrc(true),500); else { $('srcMic').classList.add('on'); }
paint();
</script>
</body>
</html>
)ledctl_raw";
const char MANIFEST_JSON[] PROGMEM = R"ledctl_raw({"name":"ledctl","short_name":"ledctl","start_url":"/","display":"standalone","background_color":"#0b0b0f","theme_color":"#0b0b0f","icons":[{"src":"/icon.svg","sizes":"any","type":"image/svg+xml"}]})ledctl_raw";
const char ICON_SVG[] PROGMEM = R"ledctl_raw(<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><rect width="100" height="100" rx="22" fill="#0b0b0f"/><circle cx="50" cy="50" r="24" fill="#ff9a3c"/><circle cx="50" cy="50" r="34" fill="none" stroke="#ff9a3c" stroke-opacity=".35" stroke-width="5"/></svg>)ledctl_raw";
