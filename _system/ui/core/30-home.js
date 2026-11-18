// ==== 首页：界面模板分发、经典总览
// 界面模板：总览按设置里选的模板画（数据都来自 /api/brief，模板代码在 /ui-templates.js）；「经典」就是下面的 overview
let uiTpl=null,uiSaved='经典',uiList=[],briefSig='',briefTimer=null;
async function uiTemplate(){if(uiTpl===null){try{const r=await api('/api/ui'),u=new URLSearchParams(location.search).get('ui');uiSaved=r['模板'];uiList=r['列表']||[];
  uiTpl=u?((uiList.find(x=>x['目录']===u||x['名称']===u)||{})['名称']||uiSaved):uiSaved}catch(_){uiTpl='经典'}}return uiTpl}
// 换界面模板：存进设置后重新加载整站（服务器按模板给页面）
async function setTemplate(name){const r=await post('/api/ui/template',{name});toast(r.msg);uiTpl=uiSaved=name;
  setTimeout(()=>{if(location.search)location.replace('/'+location.hash);else location.reload()},400);return r}
async function home(){const t=await uiTemplate();if(t==='经典'||!window.KBUI||!KBUI.has(t))return overview();return briefPage(t)}
const briefOpts={act:async(id,action,x)=>{try{toast((await post('/api/brief/act',{id,action,...x})).msg.split('\n')[0])}catch(e){toast(e.message)}briefSig='';render()},
  ask:async text=>{try{await post('/api/chat/ask',{text,page:'/'});askOpen(true)}catch(e){toast(e.message)}},
  preview:text=>api('/api/intake/preview?text='+enc(text)),openAsk:()=>askOpen(true)};
async function briefPage(t,quiet){
  const b=await api('/api/brief'),sig=JSON.stringify(b).replace(/"时间":"[^"]*"/,'');
  if(quiet&&sig===briefSig)return;briefSig=sig;
  app.innerHTML=`<section id="ovUpd"></section><div class="row" style="justify-content:space-between;margin-bottom:12px;gap:8px;flex-wrap:wrap"><span class="mut small">界面：${esc(t)} · ${esc((b['时间']||'').slice(11))} 更新</span>
    <span class="row small" style="gap:12px"><a href="#/classic">经典总览</a><a href="#/settings">换界面</a></span></div><div id="briefView">${KBUI.render(t,b,briefOpts)}</div>`;
  KBUI.bind($('#briefView'),b,briefOpts);loadUpd();
  clearTimeout(briefTimer);briefTimer=setTimeout(()=>{const h=decodeURIComponent(location.hash.slice(1)||'/');if(h==='/'&&canRender())briefPage(t,true).catch(()=>{});else if(h==='/')briefTimer=setTimeout(()=>briefPage(t,true).catch(()=>{}),6000)},6000);   // AI 在做什么：6 秒看一次，有变化才重画
}
async function overview(){
  const d=await api('/api/overview');
  try{d.adds=(await api('/api/adds')).items}catch(_){d.adds=[]}
  sideNav(d);
  const tasks=d.periods.filter(p=>p.kind==='事项');d.periods=d.periods.filter(p=>p.kind!=='事项');
  const cnt=s=>d.periods.filter(p=>s==='全部'?p.status!=='已完成':p.status===s).length;
  const sum=k=>d.periods.concat(tasks).reduce((a,p)=>a+(p[k]||0),0);
  const list=d.periods.filter(p=>ovFilter==='全部'?p.status!=='已完成':p.status===ovFilter);   // 「全部」= 在推进的；做完的折叠在后面
  const doneList=ovFilter==='全部'?d.periods.filter(p=>p.status==='已完成'):[];
  const tag=(cls,t,tip)=>`<span class="badge ${cls}"${tip?` title="${esc(tip)}"`:''}>${t}</span>`;
  const card=p=>`<a class="pcard2" href="#/p/${enc(p.name)}">
    <div class="cover">${p.cover?`<img loading="lazy" src="${fileUrl(p.cover)}" alt="">`:`<div class="ph">${esc((p.name.split('-').slice(1).join('-')||p.name).slice(0,8))}</div>`}
      <div class="ctags">${badge(p.status)}${p.running?'<span class="badge st-开发中">● 后台开发中</span>':''}</div></div>
    <div class="pb"><h3>${esc(p.name)}</h3><div class="sum">${esc(p.summary||'（没有简介）')}</div>
      <div class="meta">${d.cats.map(c=>`<span class="${p.counts[c]?'':'zero'}">${c} ${p.counts[c]}</span>`).join('')}<span class="${p.designImages?'':'zero'}">设计稿 ${p.designImages}</span></div>
      ${p.branches&&p.branches.length?`<div class="meta mono" title="开发分支">🌿 ${esc(p.branches.join('；'))}</div>`:''}</div>
    <div class="pf">${p.pendingQ?tag('st-已批准开发',`🔔 ${p.pendingQ} 个待确认`):''}${p.issues?tag('st-已暂停2',`⚠ ${p.issues} 个问题`,'不阻断开发、但要你处理的问题（手机不够、缺账号等）'):''}
      ${p.openBugs?`<span class="badge st-开发中" title="归到这一期、还没解决的 Bug（平台上激活、知识库也没标已修复）">🐞 ${p.openBugs} 个没解决</span>`:''}${p.retest?tag('st-收集中',`🔁 ${p.autoRetest?'等条件自动复测':'可复测'}`):''}${p.dnd?tag('st-收集中','🌙 免打扰'):''}
      ${p.sources?tag(p.pendingSources?'st-已暂停2':'st-收集中',`来源 ${p.sources}${p.pendingSources?' · '+p.pendingSources+' 待核对':' · 已核对'}`):''}
      ${p.staleSources?tag('st-已暂停2',`⚠ ${p.staleSources} 条原文有变化`):''}${p.imagesWithoutNote?tag('st-已暂停2',`${p.imagesWithoutNote} 张图无说明`):''}
      <span class="lt">${p.lastLog?esc(p.lastLog.time):''}</span>${sideOn()?`<span class="small navtg" data-pnav="${esc(p.name)}" data-show="${p.navHidden?'1':''}">${p.navHidden?'＋ 加到侧栏':'移出侧栏'}</span>`:''}</div></a>`;
  const ph=d.phones;
  app.innerHTML=`
  <div class="pagehead"><div><h1>总览</h1><p class="mut">按「期」管理需求、设计稿、接口文档和零散资料；资料齐了就在期里一键开始开发。</p></div>
    <div class="mut small">${d.zentao&&d.zentao.open?`<a href="#/bugs">🐞 Bug ${d.zentao.open}${d.zentao.pending?` · <b style="color:var(--s1)">${d.zentao.pending} 待归纳</b>`:''}</a> · `:''}已索引资料 ${d.docs} · 代码仓库 ${d.repos.length}${d.inbox.length?` · <b style="color:var(--s1)">收件箱 ${d.inbox.length} 个待整理</b>`:''}</div></div>
  <section id="ovUpd"></section>
  <section id="ovOnb"></section>
  <section class="kpis">
    <div class="kpi" style="--c:var(--s2)"><div><div class="k">开发中</div><div class="v">${cnt('开发中')}</div></div><span class="mut small">已批准待开工 ${cnt('已批准开发')}</span></div>
    <div class="kpi" style="--c:var(--s1)"><div><div class="k">待你确认</div><div class="v">${sum('pendingQ')}</div></div></div>
    <div class="kpi" style="--c:var(--s4)"><div><div class="k">需要你出面的问题</div><div class="v">${sum('issues')}</div></div><span class="mut small">手机、账号、环境等</span></div>
    <a class="kpi" href="#/phones" style="--c:var(--s3)"><div><div class="k">空闲手机</div><div class="v">${ph?ph.free+' / '+ph.total:'—'}</div></div><span class="mut small">${ph?'多个期共用':'本机找不到 adb'}</span></a>
  </section>
  <section class="stw" id="ovStw">${stwHtml(ovStw)}</section>
  <section class="wstat" id="ovStats">${statsHtml(ovStats)}</section>
  <div class="fchips">${['全部',...ORDER].map(s=>`<button class="fchip ${s===ovFilter?'on':''}" data-f="${s}">${s==='全部'?'在推进':s}<b>${cnt(s)}</b></button>`).join('')}</div>
  <section class="pgrid">${list.map(card).join('')||'<p class="mut">'+(ovFilter==='全部'?'没有在推进的期。':'这个状态下还没有期。')+'</p>'}</section>
  ${doneList.length||d.archived?`<details style="margin-top:10px"><summary class="small mut">最近完成 ${doneList.length} 期${d.archived?`　·　<a href="#/archive">📦 已归档 ${d.archived} 个 →</a>`:''}</summary><section class="pgrid" style="margin-top:8px">${doneList.map(card).join('')}</section></details>`:''}
  <div class="row" style="justify-content:space-between;margin-top:22px"><h2 style="margin:0">事项 <span class="mut small" style="font-weight:400">不是产品需求、但要推动的工作：用户上报的 ANR、加埋点、线上问题、优化、调研、其他</span></h2><button id="ovTask">＋ 新建事项</button></div>
  ${taskTable(tasks)}
  ${d.adds&&d.adds.length?`<h2>模糊添加 <a class="small" id="ovAdd" style="font-weight:normal">查看全部</a></h2>${d.adds.slice(0,3).map(addRec).join('')}`:''}
  ${d.inbox.length?`<h2>收件箱（${d.inbox.length}）</h2>
  <div class="cat"><div class="bd"><ul class="files">${d.inbox.map(f=>`<li><span>${esc(f.name)}${f.isDir?'/':''}</span><span class="mut small">${size(f.size)}</span></li>`).join('')}</ul>
  <div class="row" style="margin-top:8px">归档到 <select id="ibP">${d.periods.map(p=>`<option>${esc(p.name)}</option>`).join('')}</select><button class="pri" id="ibGo">自动分类归档</button></div></div></div>`:''}
  <h2>代码仓库</h2>
  ${d.repos.length?`<div class="tw"><table><tr><th>名称</th><th>路径</th><th>已索引文件</th><th>登记时间</th></tr>${d.repos.map(r=>`<tr><td>${esc(r.name)}</td><td class="small mono">${esc(r.path)}</td><td>${r.files}</td><td class="small">${esc(r.added)}</td></tr>`).join('')}</table></div>`:'<p class="mut">还没有登记。终端运行 <code>kb repo &lt;代码路径&gt;</code>，或让 Claude 登记。</p>'}
  <p class="mut small">知识库目录：${esc(d.root)}　·　<a href="#/d/使用说明">使用说明</a></p>`;
  app.querySelectorAll('[data-f]').forEach(b=>b.onclick=()=>{ovFilter=b.dataset.f;try{localStorage.setItem('kb.ovFilter',ovFilter)}catch(_){}overview()});
  loadStats();loadStw();loadOnb();loadUpd();
  if($('#ovAdd'))$('#ovAdd').onclick=()=>addDlg();
  $('#ovTask').onclick=()=>addDlg('task');
  app.querySelectorAll('[data-tnav]').forEach(a=>a.onclick=()=>taskNav(a.dataset.tnav,!!a.dataset.show));
  app.querySelectorAll('[data-pnav]').forEach(a=>a.onclick=e=>{e.preventDefault();e.stopPropagation();taskNav(a.dataset.pnav,!!a.dataset.show)});
  const g=$('#ibGo');if(g)g.onclick=async()=>{try{const r=await post('/api/inbox',{period:$('#ibP').value});toast(r.msg);render()}catch(e){toast(e.message)}};
}

// ==== 程序更新：总览顶部「⬆ 有 N 项更新」（版本号就是提交号；检查在管家巡检里，满 1 小时 fetch 一次）
function updHtml(v){
  if(!v||!v['落后']||v['设置']['模式']==='关')return '';
  const cs=v['提交']||[],main=cs.filter(c=>c['类型']!=='其他'),other=cs.length-main.length,blk=v['不能更新']||[];
  const li=c=>`<li><span class="badge ${c['类型']==='新功能'?'st-开发中':c['类型']==='修复'?'st-已批准开发':''}">${esc(c['类型'])}</span> ${esc(c['标题'])} <span class="mut mono small">${esc(c['提交'])} · ${esc(c['时间'])}</span></li>`;
  return `<div class="bcard" style="margin-bottom:12px;border-color:var(--s2)"><div class="row" style="justify-content:space-between;gap:8px;flex-wrap:wrap">
    <b>⬆ 知识库有 ${v['落后']} 项更新</b><span class="mut small mono">${esc(v['当前'])} → ${esc(v['远端'])}${v['检查时间']?' · '+esc(v['检查时间'].slice(5))+' 检查':''}</span>
    <span class="row" style="gap:8px">${blk.length?'':v['排队']?'<span class="small mut">已排队：等后台会话结束后自动更新</span>':`<button class="pri small" id="updGo">⬆ 更新并重启</button>`}<a class="small" href="#/settings">更新设置</a></span></div>
    <details style="margin-top:6px"><summary class="small">更新说明（${main.length} 项新功能 / 修复${other?`，另有 ${other} 个其他提交`:''}）</summary><ul class="small">${(main.length?main:cs).map(li).join('')}</ul></details>
    ${blk.length?`<div class="small" style="color:var(--warn);margin-top:4px">只提示不更新：${esc(blk.join('；'))}</div>`
      :v['在跑'].length?`<div class="small mut" style="margin-top:4px">有 ${v['在跑'].length} 个后台会话在跑（${esc(v['在跑'].slice(0,3).join('、'))}）：点了会排队，等它们结束再更新。</div>`:''}
    <div class="small mut" style="margin-top:4px">只动程序文件，不动期、基础信息、设置；重启后自检，失败自动退回原来的版本。${esc(v['mcp提示'])}</div></div>`;
}
async function loadUpd(){
  const el=$('#ovUpd');if(!el)return;let v;try{v=await api('/api/update')}catch(_){return}
  el.innerHTML=updHtml(v);const b=$('#updGo');if(b)b.onclick=()=>updRun(b);
}
// 一键更新：网页进程拉代码、装依赖后原地重启；这里等新版本起来再刷新页面
async function updRun(btn){
  if(!await kbConfirm('更新知识库程序并重启网页？只动程序文件，不动资料；重启要十几秒，自检失败会自动退回。'))return;
  if(btn){btn.disabled=true;btn.textContent='更新中…'}
  let old='';try{old=(await api('/api/update'))['当前完整']}catch(_){}
  let r;try{r=await post('/api/update/apply',{})}catch(e){toast(e.message);if(btn){btn.disabled=false;btn.textContent='⬆ 更新并重启'}return}
  toast(r.msg);if(!/^正在更新/.test(r.msg)){render();return}
  const t0=Date.now();await new Promise(ok=>setTimeout(ok,4000));
  while(Date.now()-t0<180000){
    try{const v=await api('/api/update'),lu=v['上次更新']||{};
      if(lu['结果']==='已更新'||lu['结果']==='已退回'||(v['当前完整']&&v['当前完整']!==old&&lu['结果']!=='重启中')){
        await kbConfirm(lu['结果']==='已退回'?'更新自检没通过，已退回原来的版本：'+(lu['说明']||''):'已更新到 '+v['当前']+'。'+v['mcp提示']);location.reload();return}}catch(_){}
    await new Promise(ok=>setTimeout(ok,3000));
  }
  toast('网页还没起来：稍后手动刷新，或看 _system/web.log');
}
