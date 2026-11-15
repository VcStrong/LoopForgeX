// ==== 公共 DOM 注入、工具函数（$ esc api post toast badge）
// 公共 DOM：对话框、toast、交给管家（按钮 + 面板）。页面上没有就自动注入（模板不用自己写）
(function ensureDom(){
  if(!document.body){console.error('core.js 要放在 <body> 末尾加载');return}
  const t=document.createElement('template');t.innerHTML=`<dialog id="dlg"><div class="hd"><b id="dlgT"></b><button onclick="dlg.close()">关闭</button></div><div class="bd" id="dlgB"></div></dialog>
<div class="toast" id="toast"></div>
<button id="askFab" title="交给管家：贴需求、日志、Bug 链接、账号，或拖文件进来；它归好档并接着推动">🤖 交给管家</button>
<section id="askPanel" hidden><div class="hd"><b>🤖 交给管家</b><span class="mut small" id="askSt"></span><a class="small" id="askNewP" title="手动建一期 / 事项">手动新建</a><a class="small" id="askClr" style="margin-left:0">清空</a><button id="askX" title="收起">✕</button></div>
  <div class="bd" id="askLog"></div>
  <div class="files small" id="askFiles"></div>
  <div class="ft"><label class="btn" title="附文件：日志、需求文档、设计图……" style="align-self:stretch;display:flex;align-items:center;cursor:pointer;padding:0 6px">📎<input type="file" id="askFile" multiple hidden></label>
    <textarea id="askIn" rows="3" placeholder="贴什么都行：需求链接或文字、ANR 日志、Bug 编号 / 链接、账号……也可以拖文件进来。我会归到对应的地方，接着推动；要动代码的会让你点一下确认。也可以问：我现在该处理什么？"></textarea><button class="pri" id="askGo">发送</button></div></section>`;
  [...t.content.children].forEach(el=>{if(!document.getElementById(el.id))document.body.appendChild(el)});
})();
const $=s=>document.querySelector(s), dlg=$('#dlg');
let app=$('#app');      // 页面渲染的目标容器：经典版是 #app，模板用 KB.setApp(el) 换
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const enc=encodeURIComponent;
const ORDER=['收集中','已批准开发','开发中','已完成','已暂停'];
function toast(m){const t=$('#toast');t.textContent=m;t.classList.add('show');clearTimeout(t._h);t._h=setTimeout(()=>t.classList.remove('show'),3500)}
// 站内确认 / 填写框：代替浏览器原生 confirm / prompt（跟随当前模板的 dialog 皮肤）。返回 Promise：确认 → true / 文字，取消 → false / null
function kbAsk(msg,o={}){return new Promise(done=>{
  let d=document.getElementById('kbAskDlg');
  if(!d){d=document.createElement('dialog');d.id='kbAskDlg';d.className='mini';document.body.appendChild(d)}
  if(d.open){d.onclose=null;d.close()}
  const inp=o.input, danger=o.danger??(!inp&&/^(删除|删掉|移除|驳回|停止|关闭|解除)/.test(msg));let val=inp?null:false;
  d.innerHTML=`<div class="hd"><b>${esc(o.title||(inp?'请填写':'请确认'))}</b><button data-x title="取消">✕</button></div><div class="bd" style="display:grid;gap:14px">
    <div style="white-space:pre-wrap;line-height:1.65">${esc(msg)}</div>${inp?`<input type="text" id="kbAskIn" value="${esc(o.def||'')}" placeholder="${esc(o.ph||'')}" style="width:100%">`:''}
    <div class="row" style="justify-content:flex-end;gap:8px"><button data-x>${esc(o.cancel||'取消')}</button><button class="${danger?'danger':'pri'}" id="kbAskOk">${esc(o.ok||'确定')}</button></div></div>`;
  const i=d.querySelector('#kbAskIn'),fin=()=>{val=inp?i.value:true;d.close()};
  d.querySelector('#kbAskOk').onclick=fin;d.querySelectorAll('[data-x]').forEach(b=>b.onclick=()=>d.close());
  if(i)i.onkeydown=e=>{if(e.key==='Enter'&&!e.isComposing){e.preventDefault();fin()}};
  d.onclose=()=>done(val);d.showModal();if(i){i.focus();i.select()}else d.querySelector('#kbAskOk').focus()})}
const kbConfirm=(msg,o)=>kbAsk(msg,o), kbPrompt=(msg,def,o)=>kbAsk(msg,{...o,input:true,def});
async function api(u,opt){const r=await fetch(u,opt);const j=await r.json();if(r.status===401&&j.login){location.href='/login';throw new Error('要先登录')}if(!r.ok){const e=new Error(j.error||r.status);e.need=j.need;throw e}return j}
const post=(u,b)=>api(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});
const badge=s=>`<span class="badge st-${esc(s)}">${esc(s)}</span>`;
const fileUrl=p=>'/file?path='+enc(p);

let entries=[],ovPeriods=[];
let ovStw=null,ovStats=null,ovRange='';try{ovRange=localStorage.getItem('kb.statRange')||''}catch(_){}
let ovFilter='全部';try{ovFilter=localStorage.getItem('kb.ovFilter')||'全部'}catch(_){}
const DOT={收集中:'var(--s0)',已批准开发:'var(--info)',开发中:'var(--s2)',已完成:'var(--s3)',已暂停:'var(--s1)'};