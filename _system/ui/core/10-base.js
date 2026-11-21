// ==== 公共 DOM 注入、工具函数（$ esc api post toast badge）
// 公共 DOM：对话框、toast、交给管家（按钮 + 面板）。页面上没有就自动注入（模板不用自己写）
(function ensureDom(){
  if(!document.body){console.error('core.js 要放在 <body> 末尾加载');return}
  const t=document.createElement('template');t.innerHTML=`<div id="kbBusy" class="kb-busy" hidden></div><div id="kbBanner" class="kb-banner" role="alert" hidden></div><dialog id="dlg"><div class="hd"><b id="dlgT"></b><button onclick="dlg.close()">关闭</button></div><div class="bd" id="dlgB"></div></dialog>
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
// 局部刷新：用 idiomorph（github.com/bigskysoftware/idiomorph，htmx / Turbo 8 的 morph 刷新）把新 HTML 合进现有 DOM，
// 只改变了的节点，焦点、光标、滚动、展开的 details 都保留（整块 innerHTML 重写会全部收起、页面变矮、滚动被拉回）。
// keepFilled：新 HTML 里标了 data-keep 的容器（devBox / stackBox 这类异步填的块，HTML 里只是「加载中…」占位）现在有内容就原样保留，等它自己刷新。
// 库没加载到就退回 innerHTML。事件都用 el.onclick= 赋值绑定，morph 后照常重新绑定，不会重复。
function morphHTML(el,html,o={}){
  if(!window.Idiomorph){el.innerHTML=html;return}
  // ignoreActiveValue 只护着正在输入的框：idiomorph 对有焦点的元素只改属性、不改里面的内容，焦点在按钮上（如刚点的「📦 归档」）时文字就不变了
  const typing=!!(document.activeElement&&document.activeElement.matches('input,textarea,select,[contenteditable]'));
  try{Idiomorph.morph(el,html,{morphStyle:'innerHTML',ignoreActiveValue:typing,restoreFocus:true,callbacks:{
    beforeAttributeUpdated:(a,n,t)=>!(a==='open'&&n.tagName==='DETAILS')&&!(t==='remove'&&a.startsWith('data-')),   // 展开状态、脚本记的 data-init / data-loaded 等标记不动
    beforeNodeMorphed:(a,b)=>!(o.keepFilled&&b.nodeType===1&&b.hasAttribute('data-keep')&&a.id===b.id&&a.hasChildNodes())}})}
  catch(e){console.error(e);el.innerHTML=html}
}
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
// 统一请求（REQ-031）：超过 300ms 顶部出加载条；401 跳登录；待改密码跳改密码；403 / 冲突 / 5xx / 断网在页面顶部提示 error 原文，不整页崩溃。
// 出错照旧抛 Error（e.status 状态码、e.need 要补的信息），调用方自己 toast 的不受影响
let kbBusyN=0,kbBusyT=null;
function kbBusy(on){kbBusyN=Math.max(0,kbBusyN+(on?1:-1));const b=document.getElementById('kbBusy');
  if(kbBusyN&&!kbBusyT)kbBusyT=setTimeout(()=>{kbBusyT=null;const x=document.getElementById('kbBusy');if(x&&kbBusyN)x.hidden=false},300);
  if(!kbBusyN){clearTimeout(kbBusyT);kbBusyT=null;if(b)b.hidden=true}}
function kbBanner(msg,o={}){let b=document.getElementById('kbBanner');if(!b)return;
  if(!msg){if(!o.kind||b.dataset.kind===o.kind){b.hidden=true;b.dataset.kind=''}return}
  if(b.dataset.kind==='offline'&&o.kind!=='offline')return;   // 断网提示优先，别被别的报错盖掉
  b.dataset.kind=o.kind||'err';b.className='kb-banner '+(o.kind||'err');
  b.innerHTML=`<span>${esc(msg)}</span>${o.kind==='offline'?'':'<button title="关闭">✕</button>'}`;b.hidden=false;
  const x=b.querySelector('button');if(x)x.onclick=()=>{b.hidden=true;b.dataset.kind=''};
  clearTimeout(b._t);if(o.kind!=='offline')b._t=setTimeout(()=>{if(b.dataset.kind!=='offline'){b.hidden=true;b.dataset.kind=''}},o.ms||10000)}
async function api(u,opt){
  let r,j;kbBusy(true);
  try{r=await fetch(u,opt)}catch(err){kbBusy(false);kbBanner('连不上知识库：检查网络或知识库是否还在运行');const e=new Error('连不上知识库');e.status=0;throw e}
  try{j=await r.json()}catch(_){j={error:r.ok?'':`服务返回 ${r.status}`}}finally{kbBusy(false)}
  if(r.status===401&&j.login){location.href='/login';throw new Error('要先登录')}
  if(r.status===403&&j.mustChangePassword){location.href='/login#change';throw new Error(j.error||'先修改初始密码')}
  if(!r.ok){const e=new Error(j.error||r.status);e.need=j.need;e.status=r.status;
    if(r.status===403)kbBanner('没有权限：'+(j.error||'这个操作'));
    else if(r.status===409&&!j.need)kbBanner(j.error||'有冲突，刷新后再试');
    else if(r.status>=500)kbBanner(j.error||'服务出错了（'+r.status+'）');
    throw e}
  if(j&&j.task&&j.task.id&&typeof platTaskNotify==='function')platTaskNotify(j.task);   // 开工 / 修复 / 运行到手机 返回了任务（REQ-013）
  return j}
const post=(u,b)=>api(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});
// 拖放进来的文件：文件夹展开成里面的文件（名字带相对路径，如 logs/anr/anr_1.txt），跳过 .DS_Store 和隐藏文件；
// 太多或太大返回 null 并提示压成 zip。直接用 dataTransfer.files 的话，文件夹是个读不了的 File，上传会报 Failed to fetch。
const DROP_MAX_N=200, DROP_MAX_BYTES=500<<20;
async function dropFiles(e,lim={}){const MAX_N=lim.n||DROP_MAX_N, MAX_B=lim.bytes||DROP_MAX_BYTES;
  const dt=e.dataTransfer;if(!dt)return [];
  const ents=[...(dt.items||[])].filter(it=>it.kind==='file').map(it=>it.webkitGetAsEntry&&it.webkitGetAsEntry());  // 要在事件里同步取，之后就清空了
  if(!ents.length||ents.some(x=>!x))return [...dt.files].filter(f=>!f.name.startsWith('.'));
  const out=[],hidden=n=>n.startsWith('.')||n==='__MACOSX';let size=0,dirs=0;
  const fileOf=en=>new Promise((ok,no)=>en.file(ok,no));
  const readAll=async dir=>{const r=dir.createReader(),all=[];for(;;){const b=await new Promise((ok,no)=>r.readEntries(ok,no));if(!b.length)return all;all.push(...b)}};
  const walk=async(en,pre)=>{
    if(hidden(en.name)||out.length>MAX_N||size>MAX_B)return;
    if(en.isDirectory){dirs++;for(const c of await readAll(en))await walk(c,pre+en.name+'/');return}
    const f=await fileOf(en);size+=f.size;
    out.push(pre?new File([f],pre+f.name,{type:f.type,lastModified:f.lastModified}):f)};
  try{for(const en of ents)await walk(en,'')}catch(err){toast('读取拖进来的文件失败：'+(err.message||err)+'，可以压成 zip 再拖');return null}
  if(out.length>MAX_N||size>MAX_B){toast(`文件太多（超过 ${MAX_N} 个或 ${MAX_B>=1<<30?(MAX_B/(1<<30))+'GB':(MAX_B>>20)+'MB'}），请压成 zip 再拖`);return null}
  if(!out.length&&dirs)toast('文件夹是空的（隐藏文件不传）');
  return out}
const badge=s=>`<span class="badge st-${esc(s)}">${esc(s)}</span>`;
const fileUrl=p=>'/file?path='+enc(p);

let entries=[],ovPeriods=[];
let ovStw=null,ovStats=null,ovRange='';try{ovRange=localStorage.getItem('kb.statRange')||''}catch(_){}
let ovFilter='全部';try{ovFilter=localStorage.getItem('kb.ovFilter')||'全部'}catch(_){}
const DOT={收集中:'var(--s0)',已批准开发:'var(--info)',开发中:'var(--s2)',已完成:'var(--s3)',已暂停:'var(--s1)'};
// 跳到板块里的某个元素（如「✓ 验收」按钮）：ids 用 | 分隔、按顺序取第一个有的；板块内容多是异步画的，等它渲染出来（最多约 8 秒）再滚，
// 滚到可视区中间并闪一下；找不到就什么都不做（调用方已滚到板块顶部）
function kbJumpTo(ids,root){
  const L=String(ids||'').split('|').filter(Boolean);if(!L.length)return;
  const find=()=>{for(const id of L){const e=(root||document).querySelector('#'+CSS.escape(id));if(e&&e.offsetParent!==null)return e}return null};
  let n=0;const tick=()=>{const e=find();
    if(!e){if(++n<40)setTimeout(tick,200);return}
    e.scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth',block:'center'});
    e.classList.remove('kb-flash');void e.offsetWidth;e.classList.add('kb-flash');setTimeout(()=>e.classList.remove('kb-flash'),2200);
    // 平滑滚动途中上面的异步块（开发进度、构建…）还可能长高，把它挤出可视区：滚完再看一眼，不在就补一次
    setTimeout(()=>{const f=find()||e,r=f.getBoundingClientRect();if(r.top<0||r.bottom>innerHeight)f.scrollIntoView({block:'center'})},1200)};
  setTimeout(tick,60);
}
