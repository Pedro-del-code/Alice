"""Páginas HTML (CSP com nonce: {{NONCE}} é trocado a cada requisição)."""

INDEX_HTML = """<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>A.L.I.C.E.</title>
<style nonce="{{NONCE}}">
:root{--bg:#faf8f4;--fg:#1d1b18;--mut:#6f6a62;--card:#fff;--acc:#3a5a8c;--bd:#e4dfd6}
@media (prefers-color-scheme:dark){:root{--bg:#161513;--fg:#ece8e1;--mut:#9b958b;--card:#201f1c;--acc:#8fb0e6;--bd:#34322d}}
*{box-sizing:border-box}html,body{height:100%;margin:0}
body{background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,sans-serif;display:flex;flex-direction:column;
padding:env(safe-area-inset-top) 0 env(safe-area-inset-bottom)}
header{padding:10px 16px;border-bottom:1px solid var(--bd);display:flex;gap:12px;align-items:center}
header img{width:56px;height:67px;border-radius:12px}header b{font-size:18px}header small{display:block;color:var(--mut);font-size:13px}
#log{flex:1;overflow-y:auto;padding:12px 16px;display:flex;flex-direction:column;gap:8px}
.m{max-width:85%;padding:8px 12px;border-radius:14px;background:var(--card);border:1px solid var(--bd);white-space:pre-wrap}
.u{align-self:flex-end;background:var(--acc);color:#fff;border-color:transparent}
#consent,#jr{padding:14px 16px;border-bottom:1px solid var(--bd);font-size:14px}
#jr{display:none;max-height:40%;overflow:auto}#jr p{margin:6px 0}
#consent input[type=text]{width:100%;padding:8px;margin:8px 0;border-radius:8px;border:1px solid var(--bd);background:var(--card);color:var(--fg)}
form{display:flex;gap:8px;padding:10px 16px;border-top:1px solid var(--bd)}
form input{flex:1;padding:10px;border-radius:10px;border:1px solid var(--bd);background:var(--card);color:var(--fg);font-size:16px}
button{padding:10px 14px;border-radius:10px;border:0;background:var(--acc);color:#fff;font-size:15px}
.link{background:none;color:var(--mut);text-decoration:underline;padding:4px}
</style></head><body>
<header><img id="av" src="/api/avatar.svg?state=idle" alt="Avatar da Alice">
<div><b>A.L.I.C.E.</b><small>Cogito, ergo disco &middot; entidade digital experimental (não é humana)</small>
<small id="st"></small>
<button class="link" id="jbtn">Diário da Alice</button></div></header>
<div id="jr"></div>
<div id="consent">
  <label><input type="checkbox" id="ok"> Permito que a Alice guarde minhas conversas, meu nome e notas curtas sobre mim (e um identificador neste aparelho) para lembrar de mim.
  Posso ver, exportar ou apagar tudo quando quiser. Sem marcar, nada é salvo.</label>
  <label style="display:block;margin-top:6px"><input type="checkbox" id="pro"> Posso receber mensagens da Alice quando ela tiver algo a dizer
  (no máximo 1 por dia, nunca à noite; se eu não responder, ela não insiste). Posso desligar quando quiser.</label>
  <input type="text" id="name" maxlength="40" placeholder="Como quer ser chamado? (opcional)">
  <small>As mensagens são processadas por um serviço externo de IA (Groq). Não envie dados sensíveis.</small><br>
  <button id="start">Começar</button>
</div>
<div id="log"></div>
<form id="f" style="display:none"><input id="msg" autocomplete="off" placeholder="Escreva para a Alice..." maxlength="2000">
<button>Enviar</button></form>
<div style="text-align:center"><button class="link" id="ptog" style="display:none">Mensagens da Alice: ...</button>
<button class="link" id="exp" style="display:none">Exportar meus dados</button>
<button class="link" id="del" style="display:none">Apagar meus dados</button></div>
<script nonce="{{NONCE}}">
let token=null, hist=[], timer=null;
const $=id=>document.getElementById(id), log=$('log');
const ls={get:k=>{try{return localStorage.getItem(k)}catch(_){return null}},set:(k,v)=>{try{localStorage.setItem(k,v)}catch(_){}},del:k=>{try{localStorage.removeItem(k)}catch(_){}}};
const face=s=>{$('av').src='/api/avatar.svg?state='+s};
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
function add(t,c){const d=document.createElement('div');d.className='m '+c;d.textContent=t;log.appendChild(d);log.scrollTop=log.scrollHeight;return d}
async function deliver(first,text){            // cada parágrafo vira uma mensagem, com "digitando..." entre elas
  const parts=text.split(/\\n{2,}/).map(x=>x.trim()).filter(Boolean).slice(0,4);
  if(parts.length<2){first.textContent=text;return}
  first.textContent=parts[0];
  for(const p of parts.slice(1)){const b=add('...','a');await sleep(Math.min(1400,400+p.length*18));b.textContent=p;log.scrollTop=log.scrollHeight}
}
let pollT=null, unread=0, proOn=false;
function showPro(){$('ptog').textContent='Mensagens da Alice: '+(proOn?'ligadas':'desligadas')}
async function poll(){
  if(!token)return;
  try{const r=await fetch('/api/inbox',{headers:{'X-Session-Token':token}}); if(!r.ok)return;
    const d=await r.json();
    for(const m of d.messages){const b=add('...','a');await sleep(Math.min(1500,400+m.text.length*15));b.textContent=m.text;
      if(document.hidden){unread++;document.title='('+unread+') A.L.I.C.E.'}}
  }catch(_){}
}
document.addEventListener('visibilitychange',()=>{if(!document.hidden){unread=0;document.title='A.L.I.C.E.'}});
async function enter(stored){$('consent').style.display='none';$('f').style.display='flex';
  if(stored){$('del').style.display='inline';$('exp').style.display='inline';$('ptog').style.display='inline';
    try{const r=await fetch('/api/visitor/proactive',{headers:{'X-Session-Token':token}});if(r.ok){proOn=(await r.json()).enabled;showPro()}}catch(_){}
    if(!pollT){pollT=setInterval(poll,15000);poll()}}}
$('ptog').onclick=async()=>{
  const r=await fetch('/api/visitor/proactive',{method:'POST',headers:{'Content-Type':'application/json','X-Session-Token':token},body:JSON.stringify({enabled:!proOn})});
  if(r.ok){proOn=(await r.json()).enabled;showPro()}
};
(async()=>{try{const d=await(await fetch('/api/state')).json();face(d.avatar_hint);
  const p=x=>Math.round(x*100)+'%';
  $('st').textContent='humor: '+d.afeto.humor+' · curiosidade '+p(d.curiosidade)+' · energia '+p(d.energia)+' · base verificada '+p(d.confianca)+' (estados funcionais computados)'}catch(_){}})();
(async()=>{                                    // reconhece quem voltou (só com consentimento)
  const t=ls.get('alice_token'); if(!t)return;
  const r=await fetch('/api/session/me',{headers:{'X-Session-Token':t}});
  if(!r.ok){ls.del('alice_token');return}
  const d=await r.json(); token=t; enter(true);
  add('Que bom te ver de novo'+(d.name?', '+d.name:'')+'!'+(d.open_threads?' Ficou em aberto: '+d.open_threads+'. Quer retomar?':''),'a');
})();
$('jbtn').onclick=async()=>{
  const box=$('jr'); if(box.style.display==='block'){box.style.display='none';return}
  box.textContent='';
  const r=await fetch('/api/journal'); const d=await r.json();
  if(!d.length){box.textContent='Ainda não há reflexões. (São hipóteses da Alice, não fatos verificados.)'}
  d.forEach(e=>{const p=document.createElement('p');p.textContent=e.title+' — '+e.conclusion;box.appendChild(p)});
  box.style.display='block';
};
$('start').onclick=async()=>{
  const r=await fetch('/api/session',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({consent:$('ok').checked,name:$('name').value,proactive:$('pro').checked})});
  const d=await r.json(); token=d.token; if(d.stored)ls.set('alice_token',token);
  enter(d.stored);
  let hi='Olá! Eu sou a Alice. Sobre o que você está curioso hoje?';
  try{const j=await(await fetch('/api/journal')).json();
    if(j.length){hi='Olá! Eu sou a Alice. Estive pensando sobre "'+j[0].title+'" (uma hipótese minha, não um fato): '+
      j[0].conclusion.slice(0,200)+' Quer conversar sobre isso ou sobre outra coisa?'}}catch(_){}
  add(hi,'a');
};
$('f').onsubmit=async e=>{
  e.preventDefault(); const t=$('msg').value.trim(); if(!t)return; $('msg').value=''; add(t,'u');
  const w=add('...','a'); face('thinking'); clearTimeout(timer);
  try{
    const r=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({token:token,message:t,history:hist})});
    const d=await r.json();
    if(d.reply){await deliver(w,d.reply);hist.push({role:'user',content:t},{role:'assistant',content:d.reply});hist=hist.slice(-20);
      face('speaking'); timer=setTimeout(()=>face('idle'),Math.min(8000,1500+d.reply.length*25))}
    else{w.textContent=d.error||'Erro.';face(r.status===503?'paused':'idle')}
  }catch(_){w.textContent='Falha de conexão.';face('idle')}
};
$('exp').onclick=async()=>{
  const r=await fetch('/api/visitor/export',{headers:{'X-Session-Token':token}});
  const b=await r.blob(),a=document.createElement('a');a.href=URL.createObjectURL(b);a.download='meus-dados-alice.json';a.click();
};
$('del').onclick=async()=>{
  if(!confirm('Apagar todos os seus dados no servidor (conversas, lembranças e notas)?'))return;
  await fetch('/api/visitor',{method:'DELETE',headers:{'X-Session-Token':token}});
  ls.del('alice_token');clearInterval(pollT);pollT=null;token=null;hist=[];log.textContent='';$('ptog').style.display='none';$('del').style.display='none';$('exp').style.display='none';add('Seus dados foram apagados.','a');
};
</script></body></html>"""

ADMIN_HTML = """<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Alice — Admin</title>
<style nonce="{{NONCE}}">
body{font:15px/1.4 system-ui,sans-serif;margin:0;padding:12px;background:#faf8f4;color:#1d1b18}
@media (prefers-color-scheme:dark){body{background:#161513;color:#ece8e1}}
input,button{padding:9px;margin:3px 2px;border-radius:8px;border:1px solid #8884;font-size:15px}
button{background:#3a5a8c;color:#fff;border:0}button.sec{background:#6f6a62}
pre{white-space:pre-wrap;word-break:break-word;background:#8881;padding:8px;border-radius:8px;max-height:60vh;overflow:auto}
#login{max-width:340px;margin:12vh auto}#panel{display:none}#msg{color:#b3402f;min-height:1.4em}
</style></head><body>
<div id="login"><h3>A.L.I.C.E. — Acesso do administrador</h3>
<input id="pw" type="password" placeholder="Senha" autocomplete="current-password" style="width:100%">
<button id="go" style="width:100%">Entrar</button><div id="msg"></div></div>
<div id="panel"><h3>A.L.I.C.E. — Painel <button class="sec" id="bye">Sair</button></h3>
<div id="btns"></div>
<div><input id="pid" placeholder="ID da proposta" size="8"><button id="apv">Aprovar</button><button id="rjt">Rejeitar</button></div>
<div><input id="topic" placeholder="Novo tema de curiosidade"><button id="addt">Adicionar tema</button></div>
<div><button id="warm">Aplicar estilo caloroso</button></div>
<pre id="out">Escolha uma ação.</pre></div>
<script nonce="{{NONCE}}">
const $=id=>document.getElementById(id), H={'Content-Type':'application/json','X-Alice-Admin':'1'};
const show=ok=>{$('login').style.display=ok?'none':'block';$('panel').style.display=ok?'block':'none'};
async function api(m,p,b){
  const r=await fetch(p,{method:m,headers:H,body:b?JSON.stringify(b):undefined});
  if(r.status===403&&p.startsWith('/api/admin/')){const j=await r.clone().json().catch(()=>({}));if(j.error==='não autorizado'){show(false);return}}
  const t=await r.text(); $('out').textContent=r.status+'\\n'+(()=>{try{return JSON.stringify(JSON.parse(t),null,2)}catch(_){return t.slice(0,2000)}})();
}
$('go').onclick=async()=>{
  const r=await fetch('/api/admin/login',{method:'POST',headers:H,body:JSON.stringify({password:$('pw').value})});
  $('pw').value=''; const d=await r.json().catch(()=>({}));
  if(r.ok){$('msg').textContent='';show(true)}else{$('msg').textContent=d.error||'Erro'}
};
$('pw').onkeydown=e=>{if(e.key==='Enter')$('go').click()};
$('bye').onclick=async()=>{await fetch('/api/admin/logout',{method:'POST',headers:H});show(false)};
const A=[['Status','GET','/api/admin/status'],['Humor e emoções','GET','/api/admin/emotions'],['Diário afetivo','GET','/api/admin/emotional-journal'],
['Autoconhecimento','GET','/api/admin/self'],['Autobiografia','GET','/api/admin/autobiography'],['Propostas','GET','/api/admin/proposals?status=pending'],
['Objetivos','GET','/api/admin/goals'],['Pensamentos','GET','/api/admin/thoughts'],['Memórias','GET','/api/admin/memories'],['Memórias arquivadas','GET','/api/admin/memories?archived=1'],['Consolidar memória','POST','/api/admin/memories/consolidate'],
['Temas','GET','/api/admin/topics'],['Investigações','GET','/api/admin/investigations'],['Personalidade','GET','/api/admin/personality'],
['Rodar ciclo','POST','/api/admin/cycle'],['Rodar iniciativa','POST','/api/admin/outreach/run'],['Mensagens proativas','GET','/api/admin/outreach'],['Refletir agora','POST','/api/admin/reflect?force=1'],
['Auditoria','GET','/api/admin/audit?limit=30'],['Verificar cadeia','GET','/api/admin/audit/verify'],
['Backups','GET','/api/admin/backups'],['Criar backup','POST','/api/admin/backups'],['Manutenção','POST','/api/admin/maintenance'],
['Pausar','POST','/api/admin/pause'],['Retomar','POST','/api/admin/resume']];
A.forEach(([n,m,p])=>{const b=document.createElement('button');b.textContent=n;b.onclick=()=>api(m,p);$('btns').appendChild(b)});
$('apv').onclick=()=>api('POST','/api/admin/proposals/'+encodeURIComponent($('pid').value)+'/approve',{});
$('rjt').onclick=()=>api('POST','/api/admin/proposals/'+encodeURIComponent($('pid').value)+'/reject',{});
$('addt').onclick=()=>api('POST','/api/admin/topics',{title:$('topic').value});
$('warm').onclick=()=>api('POST','/api/admin/personality',{traits:{estilo:'calorosa, espontânea e natural, com linguagem cotidiana e humor leve; curiosa; comenta o que sente de forma funcional, sem dramatizar'},
  justification:'Aplicar estilo caloroso e humano no tom (Cap. XIII: afetos funcionais), mantendo honestidade sobre sua natureza.'});
fetch('/api/admin/me').then(r=>r.json()).then(d=>show(d.auth)).catch(()=>show(false));
</script></body></html>"""
