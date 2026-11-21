// ==== 平台化：当前用户 / 项目切换、我的、成员、项目、审计、执行机、任务队列、资源、AI 账号、设备提供方（REQ-001~REQ-003 REQ-013 REQ-020 REQ-031 REQ-035 REQ-036 REQ-043~REQ-050）
// 所有模板共用：路由挂在 KB.routes（/me /members /project-admin /audit /runners /runner/<id> /queue /resources /settings/ai /settings/providers），
// 入口在用户栏菜单（#kbUser：经典版放顶栏，模板里浮在左下角）、经典侧栏、设置页顶部「团队与执行机」。
let platMe=null;
const platAdmin=()=>!!platMe&&platMe['系统角色']==='管理员';
const platPOwner=()=>platAdmin()||!!platMe&&(platMe['项目']||[]).some(p=>p['我的角色']==='负责人');
const platCurProj=()=>platMe?platMe['当前项目']||'':'';
const platProjName=id=>((platMe&&platMe['项目']||[]).find(p=>p.id===id)||{})['名称']||id||'';
const PLAT_OK=['完成','在线','可用','正常','已恢复','成功','已确认'],PLAT_RUN=['运行','已派','排空中'],PLAT_WAIT=['排队','等回答','冷却','待升级','待改密码','已排空','未检测','警告','未启用','进行中'],PLAT_BAD=['失败','离线','不可用','停用','取消','严重','拒绝'];
function stPill(s,tip){const c=PLAT_OK.includes(s)?'ok':PLAT_RUN.includes(s)?'run':PLAT_WAIT.includes(s)?'wait':PLAT_BAD.includes(s)?'bad':'';return `<span class="plat-st ${c}"${tip?` title="${esc(tip)}"`:''}>${esc(s||'—')}</span>`}
const platEmpty=(t,btn,id)=>`<div class="kb-state"><b>${esc(t)}</b>${btn?`<p><button class="pri" id="${esc(id)}">${esc(btn)}</button></p>`:''}</div>`;
function platErr(e){return e&&e.status===403?`<div class="kb-state"><b>没有权限</b><p class="mut">${esc(e.message)}</p></div>`
  :e&&e.status===404?`<div class="kb-state"><b>服务端还没有这个功能</b><p class="mut">知识库服务可能还没升级到新版本：升级并重启后刷新</p><p><button onclick="KB.render()">重试</button></p></div>`
  :`<div class="kb-state"><b>没加载出来</b><p class="mut">${esc(e&&e.message||e)}</p><p><button onclick="KB.render()">重试</button></p></div>`}
const platHead=(t,sub,right)=>`<div class="pagehead"><div><h1>${t}</h1>${sub?`<p class="mut">${sub}</p>`:''}</div>${right?`<div class="row" style="gap:8px">${right}</div>`:''}</div>`;
// 整页：超过 300ms 没回来先画标题 + 加载占位（不空白），出错画在页面里；返回 true = 画好了，可以绑事件
async function platPage(head,build){
  const key=location.hash;const t=setTimeout(()=>{if(location.hash===key&&app)app.innerHTML=head+'<div class="kb-skel">加载中…</div>'},300);
  let html;try{html=await build()}catch(e){clearTimeout(t);if(location.hash===key)app.innerHTML=head+platErr(e);return false}
  clearTimeout(t);if(location.hash!==key)return false;app.innerHTML=head+html;return true}
// 页面里的一小块：失败只影响这一块
async function platPart(sel,build,bind){const el=typeof sel==='string'?$(sel):sel;if(!el)return;
  const t=setTimeout(()=>{if(!el.innerHTML.trim())el.innerHTML='<div class="kb-skel">加载中…</div>'},300);
  try{const h=await build();clearTimeout(t);el.innerHTML=h;if(bind)bind(el)}catch(e){clearTimeout(t);el.innerHTML=platErr(e)}}
const platAct=async(p,b,after)=>{try{const r=await post(p,b);if(r&&r.msg)toast(r.msg);if(after)after(r);return r}catch(e){toast(e.message);return null}};
const platCopy=t=>{try{navigator.clipboard.writeText(t);toast('已复制')}catch(_){toast('复制失败，请手动选中复制')}};
function platOnce(title,rows){   // 只显示一次的令牌 / 命令
  openDlg(title,`<div class="form"><div class="notice">只显示这一次，关掉就看不到了，请马上复制保存。</div>${rows.map((r,i)=>`<label>${esc(r[0])}<div class="row" style="gap:6px;flex-wrap:nowrap"><input type="text" readonly class="mono" value="${esc(r[1])}" id="pOnce${i}" style="flex:1;min-width:0"><button data-pcp="${i}">复制</button></div></label>`).join('')}</div>`);
  $('#dlgB').querySelectorAll('[data-pcp]').forEach(b=>b.onclick=()=>platCopy(rows[+b.dataset.pcp][1]))}
const platLeft=t=>{if(!t)return'';const ms=new Date(String(t).replace(' ','T')).getTime()-Date.now();if(!(ms>0))return'已到期';const m=Math.ceil(ms/60000);return m<60?`剩 ${m} 分钟`:`剩 ${Math.floor(m/60)} 小时 ${m%60} 分`};
const platTags=xs=>(xs||[]).map(x=>`<span class="chip">${esc(x)}</span>`).join(' ');

// ---------------------------------------------------------------- 启动：当前用户、用户栏、侧栏入口
async function platStart(){
  try{platMe=await api('/api/me');KB.me=platMe}catch(_){return}
  platUserBar();
  document.querySelectorAll('[data-plat-admin]').forEach(x=>x.hidden=!platAdmin());
  document.querySelectorAll('[data-plat-powner]').forEach(x=>x.hidden=!platPOwner());
  try{const wp=await api('/api/web/port');if(wp['提示'])kbBanner(wp['提示'],{ms:30000})}catch(_){}   // 端口被占自动顺延了：顶部提示一次
}
function platUserBar(){
  let el=document.getElementById('kbUser');
  if(!el){el=document.createElement('span');el.id='kbUser';const tb=document.querySelector('.topbar #addBtn');
    if(tb)tb.parentNode.insertBefore(el,tb);else{el.classList.add('float');document.body.appendChild(el)}}
  const ps=platMe['项目']||[],m=platMe,single=m['单机模式'];
  el.innerHTML=`<span class="who" id="kbWho" title="${single?'单机模式：免登录，以本机用户（管理员）身份':'当前登录'}">👤 ${esc(m['用户名'])}${single?' <span class="chip">单机</span>':''} ▾</span>
    ${ps.length>1||!single?`<select id="kbProj" title="切换项目：首页、期、Bug、资源只看这个项目">${ps.map(p=>`<option value="${esc(p.id)}" ${p.id===m['当前项目']?'selected':''}>${esc(p['名称'])}</option>`).join('')||'<option value="">（没有项目）</option>'}</select>`:''}
    <div id="kbUserMenu" hidden>
      <a href="#/me">我的</a><a href="#/queue">任务队列</a><a href="#/resources">📦 资源</a>
      ${platPOwner()?'<hr><a href="#/project-admin">🗂 项目</a><a href="#/audit">🔐 审计</a>':''}
      ${platAdmin()?'<a href="#/members">👥 成员</a><a href="#/runners">🖥 执行机</a><a href="#/settings/ai">🤖 AI 账号</a><a href="#/settings/providers">📱 设备提供方</a>':''}
      ${single?'':'<hr><a id="kbOut">退出登录</a>'}</div>`;
  const menu=el.querySelector('#kbUserMenu');
  el.querySelector('#kbWho').onclick=e=>{e.stopPropagation();menu.hidden=!menu.hidden};
  document.addEventListener('click',e=>{if(!el.contains(e.target))menu.hidden=true});
  menu.querySelectorAll('a[href]').forEach(a=>a.onclick=()=>menu.hidden=true);
  if(el.querySelector('#kbOut'))el.querySelector('#kbOut').onclick=()=>logout();
  const sel=el.querySelector('#kbProj');if(sel)sel.onchange=async()=>{const r=await platAct('/api/me/project',{project:sel.value});if(!r){sel.value=m['当前项目'];return}
    try{platMe=await api('/api/me');KB.me=platMe}catch(_){}platUserBar();navRefresh();render()};
}

// ---------------------------------------------------------------- 实时事件：任务 / 执行机 / 资源 / 告警（REQ-013 REQ-020 REQ-048）
let platDeb=null;
function platOnEvt(ev,h,p){const k=ev.kind||ev.type;if(!['task','runner','resource','alert','queue'].includes(k))return false;
  if(p&&k==='task'&&(!ev.period||ev.period===p))platTaskBox(p);
  if(['/queue','/runners','/resources','/audit'].includes(h)||h.startsWith('/runner/')){clearTimeout(platDeb);platDeb=setTimeout(softRender,800)}
  return true}
function platTaskNotify(t){const p=curPeriod();if(p)setTimeout(()=>platTaskBox(p),300);
  if(t['状态']==='排队')setTimeout(()=>toast(`任务 ${t.id} 排队中${t['排队位置']?`：第 ${t['排队位置']} 位`:''}${t['等待原因']?'（'+t['等待原因']+'）':''}`),1800)}

// ---------------------------------------------------------------- 我的（REQ-001 REQ-005 REQ-006 REQ-010 REQ-017 REQ-018）
async function platMePage(){
  let me,toks,accts=[],usage=null;
  const ok=await platPage(platHead('我的','账号、MCP 令牌、平台账号、通知、Gerrit 凭据、AI 账号。密码类只写不回显。'),async()=>{
    me=await api('/api/me');platMe=me;KB.me=me;
    toks=(await api('/api/me/tokens')).items||[];
    try{accts=((await api('/api/engine-accounts')).账号||[]).filter(a=>a['类型']==='个人'&&a['所属人']===me['用户名'])}catch(_){}
    try{usage=((await api('/api/usage?by=人&days=30')).行||[]).find(r=>r['对象']===me['用户名'])||null}catch(_){}
    const plat=Object.entries(me['平台账号']||{});
    return `<div class="bgrid" style="grid-template-columns:repeat(auto-fill,minmax(360px,1fr))">
    <div class="bcard"><b>基本信息</b><div class="plat-kv" style="margin-top:8px"><span>用户名</span><b>${esc(me['用户名'])}</b><span>系统角色</span><span>${esc(me['系统角色'])}</span>
      <span>状态</span><span>${stPill(me['状态'])}</span><span>当前项目</span><span>${esc(platProjName(me['当前项目'])||'—')}</span>
      <span>参与项目</span><span>${(me['项目']||[]).map(p=>`${esc(p['名称'])} <span class="chip">${esc(p['我的角色']||'')}</span>`).join('　')||'<span class="mut">还没加入项目，联系管理员</span>'}</span></div>
      ${me['单机模式']?'<p class="small mut">单机模式（只在本机访问）免登录，不需要密码。</p>':`<details style="margin-top:10px"><summary>改密码</summary><div class="form" style="margin-top:6px">
        <input type="password" id="mePo" placeholder="当前密码" autocomplete="current-password"><input type="password" id="mePn" placeholder="新密码（至少 8 位）" autocomplete="new-password">
        <input type="password" id="mePn2" placeholder="再输一次新密码" autocomplete="new-password"><div class="row"><button class="pri" id="mePw">修改</button></div></div></details>`}</div>
    <div class="bcard"><div class="row" style="justify-content:space-between"><b>MCP 令牌</b><button id="meTkNew">＋ 生成令牌</button></div>
      <p class="small mut">远处的 Claude Code / Codex 用它连知识库，权限跟你一样（只能碰你参与的项目）。</p>
      ${toks.length?`<div class="tw"><table><tr><th>名称</th><th>生成</th><th>最近使用</th><th></th></tr>${toks.map(t=>`<tr><td>${esc(t['名称'])}</td><td class="small">${esc(t['时间'])}</td><td class="small">${esc(t['最近使用']||'没用过')}</td><td><a data-tkdel="${esc(t['名称'])}">删除</a></td></tr>`).join('')}</table></div>`:'<div class="small mut">还没有令牌 · 点「生成令牌」</div>'}</div>
    <div class="bcard"><b>Bug 平台账号</b><p class="small mut">知识库用你的账号同步「我的 Bug」（REQ-006）；密码存钥匙串 / 加密文件，不回显。</p>
      <div id="mePlat">${(plat.length?plat:[['禅道','']]).map(([k,v])=>platRow(k,v)).join('')}</div>
      <div class="row" style="gap:8px"><a id="mePlatAdd">＋ 再加一个平台</a><span style="flex:1"></span><button id="mePlatTest">测试连接</button><button class="pri" id="mePlatSave">保存</button></div>
      ${plat.length?'':'<div class="small mut" style="margin-top:6px">还没填平台账号：填了才能同步你自己的 Bug。</div>'}</div>
    <div class="bcard"><b>通知</b><p class="small mut">群机器人 Webhook（飞书 / 钉钉 / 企业微信）：@到你的卡片、预算提醒、任务失败都发这里（REQ-010）。</p>
      <div class="row" style="gap:6px;flex-wrap:nowrap"><input type="text" id="meHook" value="${esc((me['通知']||{}).webhook||'')}" placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/…" style="flex:1;min-width:0"><button id="meHookTest">测试</button><button class="pri" id="meHookSave">保存</button></div>
      ${(me['通知']||{}).webhook?'':'<div class="small mut" style="margin-top:6px">还没填通知地址：只在网页里提醒。</div>'}</div>
    <div class="bcard"><b>Gerrit / Git 凭据</b><p class="small mut">推送用你自己的身份：会话开始时写进会话私有目录，结束即删（REQ-017）。不填就用项目 bot 账号推送，提交里带 Co-authored-by 你。</p>
      <div class="form"><input type="text" id="meGu" placeholder="Gerrit 用户名" value="${esc((me['gerrit']||{})['用户名']||'')}"><input type="password" id="meGk" placeholder="HTTP 密码 / 令牌（只写，不回显）" autocomplete="new-password">
      <div class="row"><button class="pri" id="meGSave">保存</button></div></div></div>
    <div class="bcard"><b>AI 账号</b><p class="small mut">默认用项目共享账号；绑定自己的并打开「优先用我的」后，你发起的会话先用你的（REQ-018）。个人登录态只给你和你发起的会话。</p>
      <div class="row" style="gap:14px;margin:6px 0">${['项目共享','自己的'].map(v=>`<label><input type="radio" name="meAi" value="${v}" ${me['AI账号']===v||(v==='项目共享'&&me['AI账号']!=='自己的')?'checked':''}> ${v==='自己的'?'优先用我的':'用项目共享'}</label>`).join('')}</div>
      ${accts.length?`<div class="tw"><table><tr><th>名称</th><th>引擎</th><th>状态</th><th>优先用我的</th><th></th></tr>${accts.map(a=>`<tr><td>${esc(a['名称'])}</td><td>${esc(a['引擎'])}</td><td>${stPill(a['状态'],a['原因'])}</td><td>${a['优先用我的']?'是':'否'}</td><td><a data-eaed="${esc(a.id)}">编辑</a>　<a data-eadel="${esc(a.id)}">删除</a></td></tr>`).join('')}</table></div>`:'<div class="small mut">还没绑定自己的 AI 账号（用项目共享的就不用绑）。</div>'}
      <div class="row" style="margin-top:8px"><button id="meEaNew">＋ 绑定我的账号</button></div>
      <div class="small" style="margin-top:8px">最近 30 天用量：${usage?`${usage['会话数']||0} 个会话 · ${Math.round((usage['时长秒']||0)/60)} 分钟 · 输入 ${usage['输入token']||0} / 输出 ${usage['输出token']||0} token${usage['估算']?' <span class="chip">估算</span>':''}${usage['预算日']?` · 今日预算已用 ${Math.round((usage['已用比']||0)*100)}%`:''}`:'<span class="mut">还没有用量</span>'}</div></div></div>`});
  if(!ok)return;
  const re=()=>platMePage();
  if($('#mePw'))$('#mePw').onclick=async()=>{const o=$('#mePo').value,n=$('#mePn').value;if(n.length<8)return toast('新密码至少 8 位');if(n!==$('#mePn2').value)return toast('两次输入的新密码不一样');
    await platAct('/api/me/password',{old:o,new:n},re)};
  $('#meTkNew').onclick=async()=>{const n=await kbPrompt('给令牌起个名字（用在哪台机器）','',{ph:'如：家里的 Mac'});if(!n)return;
    try{const r=await post('/api/me/token',{name:n});platOnce('MCP 令牌',[['令牌',r.token],['在那台机器上运行',r.mcpAdd]]);dlg.addEventListener('close',re,{once:true})}catch(e){toast(e.message)}};
  app.querySelectorAll('[data-tkdel]').forEach(a=>a.onclick=async()=>{if(await kbConfirm(`删除令牌「${a.dataset.tkdel}」？用它连接的地方会断开。`))platAct('/api/me/token/delete',{name:a.dataset.tkdel},re)});
  $('#mePlatAdd').onclick=()=>$('#mePlat').insertAdjacentHTML('beforeend',platRow('',''));
  const platBody=()=>{const a={},p={};$('#mePlat').querySelectorAll('.platRow').forEach(r=>{const k=r.querySelector('[data-pk]').value.trim();if(!k)return;
    a[k]=r.querySelector('[data-pa]').value.trim();const pw=r.querySelector('[data-pp]').value;if(pw)p[k]=pw});return {平台账号:a,平台密码:p}};
  $('#mePlatSave').onclick=()=>platAct('/api/me/profile',platBody(),re);
  $('#mePlatTest').onclick=async()=>{try{const r=await post('/api/me/test',{kind:'bug'});toast((r.ok?'✓ ':'✗ ')+r.msg)}catch(e){toast(e.message)}};
  $('#meHookSave').onclick=()=>platAct('/api/me/profile',{通知:{webhook:$('#meHook').value.trim()}},re);
  $('#meHookTest').onclick=async()=>{try{const r=await post('/api/me/test',{kind:'notify'});toast((r.ok?'✓ ':'✗ ')+r.msg)}catch(e){toast(e.message)}};
  $('#meGSave').onclick=()=>{const g={'用户名':$('#meGu').value.trim()};if($('#meGk').value)g['密钥']=$('#meGk').value;platAct('/api/me/profile',{gerrit:g},re)};
  app.querySelectorAll('[name=meAi]').forEach(r=>r.onchange=()=>platAct('/api/me/profile',{AI账号:r.value}));
  $('#meEaNew').onclick=()=>platEaDlg({类型:'个人'},re,true);
  app.querySelectorAll('[data-eaed]').forEach(a=>a.onclick=()=>platEaDlg(accts.find(x=>x.id===a.dataset.eaed),re,true));
  app.querySelectorAll('[data-eadel]').forEach(a=>a.onclick=async()=>{if(await kbConfirm('删除这个 AI 账号？'))platAct('/api/engine-account',{id:a.dataset.eadel,删除:true},re)});
}
const platRow=(k,v)=>`<div class="row platRow" style="gap:6px;flex-wrap:nowrap;margin-bottom:6px"><input type="text" data-pk value="${esc(k)}" placeholder="平台（如 禅道）" style="width:110px;flex:none"><input type="text" data-pa value="${esc(v)}" placeholder="账号" style="flex:1;min-width:0"><input type="password" data-pp placeholder="${v?'密码（不改留空）':'密码'}" autocomplete="new-password" style="flex:1;min-width:0"></div>`;

// ---------------------------------------------------------------- 成员（管理员，REQ-001 REQ-002 REQ-028）
async function platMembersPage(){
  let us=[];
  const ok=await platPage(platHead('👥 成员','管理员建账号、重置密码、改系统角色、停用交接。新账号第一次登录必须改密码。','<button class="pri" id="muNew">＋ 新建账号</button>'),async()=>{
    us=(await api('/api/users')).items||[];
    const others=us.filter(u=>!platMe||u['用户名']!==platMe['用户名']);
    return `${others.length?'':platEmpty('还没有其他成员','＋ 新建账号','muNew2')}
      <div class="tw"><table><tr><th>用户名</th><th>系统角色</th><th>状态</th><th>项目</th><th>最近登录</th><th style="width:260px"></th></tr>
      ${us.map(u=>{const me=platMe&&u['用户名']===platMe['用户名'];return `<tr><td><b>${esc(u['用户名'])}</b>${me?' <span class="chip">我</span>':''}${u['本机用户']?' <span class="chip">本机</span>':''}</td><td>${esc(u['系统角色'])}</td><td>${stPill(u['状态'])}</td>
        <td class="small">${(u['项目']||[]).map(p=>`${esc(platProjName(p['项目']))}·${esc(p['角色'])}`).join('、')||'<span class="mut">未加入</span>'}</td><td class="small">${esc(u['最近登录']||'从未')}</td>
        <td class="small"><a data-mupw="${esc(u['用户名'])}">重置密码</a>　${me?'':`<a data-murole="${esc(u['用户名'])}" data-r="${u['系统角色']==='管理员'?'普通':'管理员'}">设为${u['系统角色']==='管理员'?'普通':'管理员'}</a>　${u['状态']==='停用'?`<a data-muon="${esc(u['用户名'])}">启用</a>`:`<a data-muoff="${esc(u['用户名'])}" style="color:var(--s4)">停用</a>`}`}</td></tr>`}).join('')}</table></div>`});
  const re=()=>platMembersPage();
  const nb=()=>{openDlg('新建账号',`<div class="form"><label>用户名<input type="text" id="muU" placeholder="2~32 位：字母、数字、中文、. - _ @"></label>
    <label>初始密码<input type="text" id="muP" placeholder="至少 8 位；对方第一次登录要改"></label>
    <label>系统角色<select id="muR"><option>普通</option><option>管理员</option></select></label><div class="row"><button class="pri" id="muGo">新建</button></div></div>`);
    $('#muGo').onclick=async()=>{if($('#muP').value.length<8)return toast('初始密码至少 8 位');
      const r=await platAct('/api/user',{新建:true,username:$('#muU').value.trim(),password:$('#muP').value,系统角色:$('#muR').value});if(r){dlg.close();re()}}};
  if($('#muNew'))$('#muNew').onclick=nb;if(!ok)return;if($('#muNew2'))$('#muNew2').onclick=nb;
  app.querySelectorAll('[data-mupw]').forEach(a=>a.onclick=async()=>{const p=await kbPrompt(`给 ${a.dataset.mupw} 设一个新的初始密码（至少 8 位），对方登录后要改`,'',{title:'重置密码'});if(p==null)return;
    if(p.length<8)return toast('至少 8 位');platAct('/api/user',{username:a.dataset.mupw,password:p},re)});
  app.querySelectorAll('[data-murole]').forEach(a=>a.onclick=async()=>{if(await kbConfirm(`把 ${a.dataset.murole} 设为「${a.dataset.r}」？`))platAct('/api/user',{username:a.dataset.murole,系统角色:a.dataset.r},re)});
  app.querySelectorAll('[data-muon]').forEach(a=>a.onclick=()=>platAct('/api/user/enable',{username:a.dataset.muon},re));
  app.querySelectorAll('[data-muoff]').forEach(a=>a.onclick=()=>platDisable(a.dataset.muoff,us,re));
}
async function platDisable(name,us,re){
  let need;try{const r=await post('/api/user/disable',{username:name});toast(r.msg);return re()}catch(e){if(!e.need)return toast(e.message);need=e.need}
  const cands=us.filter(u=>u['用户名']!==name&&u['状态']!=='停用');
  openDlg(`停用 ${name} 并交接`,`<div class="form"><div class="small">停用后他的令牌、会话令牌立即失效，个人机密删除。先把他手上的东西交出去：</div>
    <div class="plat-kv"><span>负责的期 / 事项</span><span>${(need['期']||[]).map(esc).join('、')||'<span class="mut">无</span>'}</span>
      <span>负责的项目</span><span>${(need['负责项目']||[]).map(x=>esc(platProjName(x))).join('、')||'<span class="mut">无</span>'}</span>
      <span>在跑的任务</span><span>${(need['在跑任务']||[]).map(esc).join('、')||'<span class="mut">无</span>'}</span></div>
    <label>新负责人<select id="hoTo"><option value="">— 选一个 —</option>${cands.map(u=>`<option>${esc(u['用户名'])}</option>`).join('')}</select></label>
    <div>在跑的任务 <label><input type="radio" name="hoT" value="取消" checked> 取消</label> <label><input type="radio" name="hoT" value="转交"> 转给新负责人</label></div>
    <div class="row"><button class="danger" id="hoGo">确认停用</button></div></div>`);
  $('#hoGo').onclick=async()=>{const to=$('#hoTo').value;if(!to)return toast('先选新负责人');
    const r=await platAct('/api/user/disable',{username:name,handover:{新负责人:to,在跑任务:(app.ownerDocument.querySelector('[name=hoT]:checked')||{}).value||'取消'}});if(r){dlg.close();re()}}}

// ---------------------------------------------------------------- 项目（REQ-003）
async function platProjectsPage(){
  let ps=[],us=[];
  const ok=await platPage(platHead('🗂 项目','每个项目一块空间：期、Bug、资源、机密按项目隔开。项目负责人管成员和角色。',platAdmin()?'<button class="pri" id="pjNew">＋ 新建项目</button>':''),async()=>{
    ps=(await api('/api/projects')).items||[];if(platAdmin()){try{us=(await api('/api/users')).items||[]}catch(_){}}
    const can=p=>platAdmin()||p['我的角色']==='负责人';
    return ps.length?ps.map(p=>`<div class="card2" style="margin-bottom:12px"><div class="bd"><div class="row" style="justify-content:space-between"><div><b style="font-size:16px">${esc(p['名称'])}</b> <span class="mono mut small">${esc(p.id)}</span>${p.id===platCurProj()?' <span class="chip">当前</span>':''}</div>
      <span class="small">负责人 <b>${esc(p['负责人']||'—')}</b>　我的角色 ${esc(p['我的角色']||'—')}${platAdmin()?`　<a data-pjed="${esc(p.id)}">改名 / 换负责人</a>`:''}</span></div>
      <div class="tw" style="margin-top:8px"><table><tr><th>成员</th><th style="width:180px;flex:none">角色</th></tr>${(p['成员']||[]).map(m=>`<tr><td>${esc(m['用户名'])}</td><td>${can(p)?`<select data-pjm="${esc(p.id)}" data-u="${esc(m['用户名'])}">${['负责人','成员','只读'].map(r=>`<option ${r===m['角色']?'selected':''}>${r}</option>`).join('')}<option value="">移出项目</option></select>`:esc(m['角色'])}</td></tr>`).join('')||'<tr><td colspan="2" class="mut small">还没有成员</td></tr>'}</table></div>
      ${can(p)?`<div class="row" style="gap:6px;margin-top:8px">${us.length?`<select data-pjau="${esc(p.id)}"><option value="">— 加成员 —</option>${us.filter(u=>!(p['成员']||[]).some(m=>m['用户名']===u['用户名'])).map(u=>`<option>${esc(u['用户名'])}</option>`).join('')}</select>`:`<input type="text" data-pjau="${esc(p.id)}" placeholder="用户名">`}
        <select data-pjar="${esc(p.id)}"><option>成员</option><option>只读</option><option>负责人</option></select><button data-pjadd="${esc(p.id)}">加入</button></div>`:''}</div></div>`).join('')
      :platEmpty('还没有项目',platAdmin()?'＋ 新建项目':'','pjNew2')});
  const re=()=>platProjectsPage();
  const ed=p=>{openDlg(p?'改项目':'新建项目',`<div class="form"><label>名称<input type="text" id="pjN" value="${esc(p?p['名称']:'')}"></label>
    <label>负责人${us.length?`<select id="pjO">${us.map(u=>`<option ${p&&u['用户名']===p['负责人']?'selected':''}>${esc(u['用户名'])}</option>`).join('')}</select>`:`<input type="text" id="pjO" value="${esc(p?p['负责人']:'')}">`}</label>
    <div class="row"><button class="pri" id="pjGo">保存</button></div></div>`);
    $('#pjGo').onclick=async()=>{const b={名称:$('#pjN').value.trim(),负责人:$('#pjO').value};if(p)b.id=p.id;const r=await platAct('/api/project',b);if(r){dlg.close();platStart();re()}}};
  if($('#pjNew'))$('#pjNew').onclick=()=>ed(null);if(!ok)return;if($('#pjNew2'))$('#pjNew2').onclick=()=>ed(null);
  app.querySelectorAll('[data-pjed]').forEach(a=>a.onclick=()=>ed(ps.find(p=>p.id===a.dataset.pjed)));
  app.querySelectorAll('[data-pjm]').forEach(s=>s.onchange=async()=>{if(!s.value&&!await kbConfirm(`把 ${s.dataset.u} 移出项目？`))return re();
    platAct('/api/project/member',{project:s.dataset.pjm,username:s.dataset.u,角色:s.value||undefined},re)});
  app.querySelectorAll('[data-pjadd]').forEach(b=>b.onclick=()=>{const id=b.dataset.pjadd,u=app.querySelector(`[data-pjau="${CSS.escape(id)}"]`).value.trim();if(!u)return toast('选一个用户');
    platAct('/api/project/member',{project:id,username:u,角色:app.querySelector(`[data-pjar="${CSS.escape(id)}"]`).value},re)});
}

// ---------------------------------------------------------------- 审计（REQ-007）
let platAuditQ={page:1};
async function platAuditPage(){
  let ps=[];try{ps=(await api('/api/projects')).items||[]}catch(_){}
  const Q=platAuditQ,qs=Object.entries({project:Q.project,user:Q.user,secret:Q.secret,since:Q.since,page:Q.page,size:50}).filter(([,v])=>v).map(([k,v])=>k+'='+enc(v)).join('&');
  let r;
  const ok=await platPage(platHead('🔐 审计','谁在什么时候读了哪个机密（含 AI 会话），默认最近 30 天，最多查 90 天。'),async()=>{
    r=await api('/api/audit?'+qs);const pages=Math.max(1,Math.ceil((r.total||0)/50));
    return `<div class="row" style="gap:8px;flex-wrap:wrap;margin-bottom:10px"><select id="auP"><option value="">全部项目</option>${ps.map(p=>`<option value="${esc(p.id)}" ${p.id===Q.project?'selected':''}>${esc(p['名称'])}</option>`).join('')}</select>
      <input type="text" id="auU" placeholder="用户 / 会话" value="${esc(Q.user||'')}" style="width:140px;flex:none"><input type="text" id="auS" placeholder="机密编号" value="${esc(Q.secret||'')}" style="width:140px;flex:none">
      <label class="small">从 <input type="date" id="auD" value="${esc(Q.since||'')}"></label><button class="pri" id="auGo">查询</button><span class="small mut">共 ${r.total||0} 条</span></div>
      ${(r.items||[]).length?`<div class="tw"><table><tr><th style="width:150px;flex:none">时间</th><th>主体</th><th>机密</th><th>项目</th><th>结果</th><th>说明</th></tr>${r.items.map(x=>`<tr><td class="small">${esc(x['时间'])}</td><td>${esc(x['主体'])}</td><td class="mono small">${esc(x['机密'])}</td><td class="small">${esc(platProjName(x['项目']))}</td><td>${stPill(x['结果'])}</td><td class="small mut">${esc(x['说明']||'')}</td></tr>`).join('')}</table></div>
        <div class="row" style="gap:8px;margin-top:8px;justify-content:center">${Q.page>1?'<button id="auPrev">上一页</button>':''}<span class="small">第 ${Q.page} / ${pages} 页</span>${Q.page<pages?'<button id="auNext">下一页</button>':''}</div>`
        :platEmpty('这段时间没有读取记录')}`});
  if(!ok)return;
  $('#auGo').onclick=()=>{Object.assign(Q,{project:$('#auP').value,user:$('#auU').value.trim(),secret:$('#auS').value.trim(),since:$('#auD').value,page:1});platAuditPage()};
  if($('#auPrev'))$('#auPrev').onclick=()=>{Q.page--;platAuditPage()};if($('#auNext'))$('#auNext').onclick=()=>{Q.page++;platAuditPage()};
}

// ---------------------------------------------------------------- 执行机（管理员，REQ-011 REQ-012 REQ-014~REQ-016 REQ-020 REQ-035 REQ-037）
let platRunTab='list';
const PLAT_INSTALL=s=>`在要接入的机器上（装好 git、Python 3.9+）运行：\nkb runner --server ${location.origin} --token <注册令牌>\n只出站连接控制面，本机不开端口；Linux 可用 ./install.sh --runner。`;
function platRes(r){const c=r['CPU']||{},m=r['内存']||{},d=r['磁盘']||{},n=r['网络']||[],k=r['指标']||{};const x=[],pc=v=>Math.round(v*100)+'%';
  if(k['CPU']!=null)x.push(`CPU 负载/核 ${(+k['CPU']).toFixed(2)}`);else if(c['负载1分']!=null)x.push(`CPU ${(+c['负载1分']).toFixed(2)}${c['核数']?' / '+c['核数']+' 核':''}`);
  if(k['内存可用比']!=null)x.push(`内存可用 ${pc(k['内存可用比'])}`);else if(m['可用比']!=null)x.push(`内存可用 ${pc(m['可用比'])}`);
  if(k['磁盘使用比']!=null)x.push(`磁盘 ${pc(k['磁盘使用比'])}${d['可用GB']!=null?`（剩 ${(+d['可用GB']).toFixed(1)} GB）`:''}`);else if(d['总GB'])x.push(`磁盘 ${Math.round((1-(d['可用GB']||0)/d['总GB'])*100)}%（剩 ${(+d['可用GB']).toFixed(1)} GB）`);
  const net=Object.keys(k['网络']||{}).length?Object.entries(k['网络']).map(([t,v])=>({目标:t,...(typeof v==='object'?v:{延迟ms:v})})):n;
  net.forEach(t=>x.push(`${esc(t['目标'])} ${t['延迟ms']??'?'}ms${t['失败率']?' 失败 '+pc(t['失败率']):''}`));
  if((r['网络差']||[]).length)x.push(`<span style="color:var(--s1)">网络差：${esc(r['网络差'].join('、'))}（需要它的任务不派这台）</span>`);
  if(r['禁止新副本'])x.push('<span style="color:var(--s1)">磁盘 ≥80%：不再建新副本</span>');
  if(r['告警'])x.push(`<a href="#/runner/${enc(r.id)}" style="color:var(--s4)">⚠ ${r['告警']} 条告警</a>`);
  return x.join(' · ')}
async function platRunnersPage(){
  const tabs=`<div class="plat-tabs"><a data-rt="list" class="${platRunTab==='list'?'on':''}">执行机</a><a data-rt="alerts" class="${platRunTab==='alerts'?'on':''}">告警</a></div>`;
  let v;
  const ok=await platPage(platHead('🖥 执行机','干活的机器：心跳、标签、容量、在跑的任务、副本、手机和资源。离线 2 分钟任务回队列，副本保留。','<button class="pri" id="rnTok">＋ 生成注册令牌</button>')+tabs,async()=>{
    if(platRunTab==='alerts')return platAlertsHtml();
    v=await api('/api/runners');const xs=v.items||[];
    if(!xs.length)return `<div class="kb-state"><b>还没有执行机</b><p class="mut">单机模式下开工由本机直接跑；要多台机器干活，先点「生成注册令牌」。</p><pre class="mono small" style="text-align:left;display:inline-block;white-space:pre-wrap">${esc(v.installHelp||PLAT_INSTALL())}</pre></div>`;
    return xs.map(r=>{const fail=(r['自检']||[]).filter(c=>!c['通过']);return `<div class="card2" style="margin-bottom:12px"><div class="bd">
      <div class="row" style="justify-content:space-between;gap:8px"><div><b style="font-size:16px">${esc(r['名称']||r.id)}</b> ${stPill(r['状态'])} <span class="mono mut small">${esc(r.id)}</span>${r['版本']?` <span class="chip">v ${esc(r['版本'])}</span>`:''}
        ${r['状态']==='离线'?` <span class="small" style="color:var(--s4)">最后心跳 ${esc(r['最后心跳']||'—')}</span>`:r['最后心跳']?` <span class="small mut">心跳 ${esc(r['最后心跳'])}</span>`:''}</div>
        <div class="row" style="gap:6px"><a href="#/runner/${enc(r.id)}">详情 / 曲线</a>${['停用','已排空'].includes(r['状态'])||r['状态']==='排空中'?`<button data-rnact="启用" data-id="${esc(r.id)}">启用</button>`:''}${['在线','离线','待升级'].includes(r['状态'])?`<button data-rnact="排空" data-id="${esc(r.id)}" title="不再接新任务，在跑的跑完">排空</button>`:''}${r['状态']!=='停用'?`<button data-rnact="停用" data-id="${esc(r.id)}" title="令牌失效，运行中的任务回队列">停用</button>`:''}<button class="danger" data-rnact="删除" data-id="${esc(r.id)}">删除</button></div></div>
      <div class="small" style="margin:6px 0">${platRes(r)||'<span class="mut">还没有资源数据</span>'}</div>
      <div class="plat-kv small"><span>自动标签</span><span>${platTags(r['自动标签'])||'<span class="mut">无</span>'}</span>
        <span>自定义标签</span><span class="row" style="gap:6px;flex-wrap:nowrap"><input type="text" data-rntag="${esc(r.id)}" value="${esc((r['自定义标签']||[]).join(', '))}" placeholder="逗号隔开，如 gpu, 北京" style="flex:1;min-width:0">
          容量 <input type="number" min="0" data-rncap="${esc(r.id)}" value="${esc(r['容量']??2)}" style="width:64px"><button data-rnsave="${esc(r.id)}">保存</button></span>
        ${fail.length?`<span>自检不通过</span><span style="color:var(--s4)">${fail.map(c=>`${esc(c['项'])}：${esc(c['原因']||'')}`).join('；')}</span>`:''}
        <span>在跑</span><span>${(r['运行中']||[]).map(t=>`${esc(t['类型'])} · <a href="#/p/${enc(t['期']||'')}">${esc(t['期']||'')}</a> · ${esc(t['发起人']||'')} ${stPill(t['状态'])}`).join('<br>')||'<span class="mut">空闲</span>'}</span>
        <span>工作副本</span><span>${(r['副本']||[]).map(c=>`<span class="mono">${esc(c['仓库'])}/${esc(c['序号'])}</span> ${esc(c['分支']||'')}${c['占用会话']?' · 占用 '+esc(c['占用会话']):''}${c['可能有改动']?' <span class="plat-st wait" title="执行机掉线时它在用：保留不删，可导出补丁">可能有改动</span>':''}`).join('<br>')||'<span class="mut">无</span>'}</span>
        <span>手机</span><span>${(r['手机']||[]).map(p=>`${esc(p['型号']||'')} <span class="mono">${esc(p['序列号'])}</span>${p['占用会话']?' · 占用 '+esc(p['占用会话']):' · 空闲'}`).join('<br>')||'<span class="mut">无</span>'}</span></div></div></div>`}).join('')});
  const re=()=>platRunnersPage();
  $('#rnTok').onclick=async()=>{const n=await kbPrompt('给这台执行机起个名字（可空）','',{ph:'如：mac-mini-1'});if(n==null)return;
    try{const r=await post('/api/runners/token',{name:n});platOnce(`注册令牌（${r.expires||'24 小时'}前有效，只能用一次）`,[['令牌',r.token],['在那台机器上运行',r.command||`kb runner --server ${location.origin} --token ${r.token}`]])}catch(e){toast(e.message)}};
  app.querySelectorAll('[data-rt]').forEach(a=>a.onclick=()=>{platRunTab=a.dataset.rt;re()});
  if(!ok)return;
  if(platRunTab==='alerts')return platAlertsBind(re);
  app.querySelectorAll('[data-rnact]').forEach(b=>b.onclick=async()=>{const a=b.dataset.rnact;
    if(a!=='启用'&&!await kbConfirm({排空:'排空这台执行机？不再接新任务，在跑的跑完后变「已排空」。',停用:'停用这台执行机？它的令牌立即失效，运行中的任务回队列。',删除:'删除这台执行机？要重新接入得再生成注册令牌。'}[a],{danger:a!=='排空'}))return;
    platAct('/api/runners/update',{id:b.dataset.id,动作:a},re)});
  app.querySelectorAll('[data-rnsave]').forEach(b=>b.onclick=()=>{const id=b.dataset.rnsave,tags=app.querySelector(`[data-rntag="${CSS.escape(id)}"]`).value.split(/[,，\s]+/).filter(Boolean);
    platAct('/api/runners/update',{id,自定义标签:tags,容量:+app.querySelector(`[data-rncap="${CSS.escape(id)}"]`).value},re)});
}
let platAlertSt='进行中';
async function platAlertsHtml(runner){
  const v=await api('/api/alerts?state='+enc(runner?'全部':platAlertSt));let xs=v['告警']||[];if(runner)xs=xs.filter(a=>a['执行机']===runner);
  return `${runner?'':`<div class="row" style="gap:8px;margin-bottom:8px"><select id="alSt">${['进行中','已恢复','全部'].map(s=>`<option ${s===platAlertSt?'selected':''}>${s}</option>`).join('')}</select><span class="small mut">最近 7 天；同一告警 30 分钟内只通知一次，恢复时再通知一次。</span></div>`}
    ${xs.length?`<div class="tw"><table><tr><th>执行机</th><th>类型</th><th>级别</th><th>开始</th><th>恢复</th><th>确认人</th><th></th></tr>${xs.map(a=>`<tr><td>${esc(a['执行机'])}</td><td>${esc(a['类型'])}</td><td>${stPill(a['级别'])}</td><td class="small">${esc(a['开始']||'')}</td><td class="small">${a['恢复']?stPill('已恢复')+' '+esc(a['恢复']):''}</td><td class="small">${esc(a['确认人']||'')}</td><td>${a['恢复']||a['确认人']?'':`<a data-alack="${esc(a.id)}">确认 / 静默</a>`}</td></tr>`).join('')}</table></div>`
      :platEmpty(runner?'这台执行机最近 7 天没有告警':platAlertSt==='进行中'?'现在没有告警':'没有告警')}`}
function platAlertsBind(re){if($('#alSt'))$('#alSt').onchange=()=>{platAlertSt=$('#alSt').value;re()};
  app.querySelectorAll('[data-alack]').forEach(a=>a.onclick=async()=>{const m=await kbPrompt('确认这条告警。要静默多少分钟（0 = 只确认）？','0',{title:'确认告警'});if(m==null)return;
    platAct('/api/alerts/ack',{id:a.dataset.alack,静默分钟:+m||0},re)})}
function platChart(pts,key,o={}){   // inline SVG 折线：pts=[{时间(秒), ...}]，key 取值函数或字段名；多条线 o.series=[[名称,取值]]
  const series=o.series||[[o.name||'',typeof key==='function'?key:p=>p[key]]],W=600,H=110,P=26;
  const ts=pts.map(p=>p['时间']),t0=Math.min(...ts),t1=Math.max(...ts)||t0+1;
  const vals=series.flatMap(([,f])=>pts.map(f).filter(v=>v!=null&&!isNaN(v)));
  if(!pts.length||!vals.length)return `<div class="kb-state small" style="padding:14px">还没有数据（刚上线或离线中）</div>`;
  const vmax=o.max??Math.max(...vals,0.0001),vmin=o.min??0;
  const X=t=>P+(W-P-6)*((t-t0)/((t1-t0)||1)),Y=v=>H-14-(H-24)*((v-vmin)/((vmax-vmin)||1));
  const colors=['var(--acc)','var(--s3)','var(--s1)','var(--s4)','var(--s0)'];
  const tl=t=>new Date(t*1000).toLocaleTimeString('zh-CN',{hour12:false,hour:'2-digit',minute:'2-digit'});
  return `<svg class="plat-chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="${esc(o.title||'')}">
    <line class="ax" x1="${P}" y1="${H-14}" x2="${W}" y2="${H-14}"/><text x="2" y="12">${esc(o.fmt?o.fmt(vmax):vmax.toFixed(2))}</text><text x="2" y="${H-16}">${esc(o.fmt?o.fmt(vmin):vmin)}</text>
    <text x="${P}" y="${H-2}">${tl(t0)}</text><text x="${W-40}" y="${H-2}">${tl(t1)}</text>
    ${series.map(([n,f],i)=>`<polyline style="stroke:${colors[i%colors.length]}" points="${pts.filter(p=>f(p)!=null&&!isNaN(f(p))).map(p=>X(p['时间']).toFixed(1)+','+Y(f(p)).toFixed(1)).join(' ')}"><title>${esc(n)}</title></polyline>`).join('')}</svg>
    ${series.length>1?`<div class="small">${series.map(([n],i)=>`<span style="color:${colors[i%colors.length]}">━ ${esc(n)}</span>`).join('　')}</div>`:''}`}
async function platRunnerDetail(id){
  let r,m,hours=+(sessionStorage.getItem('kb.rnHours')||24);
  const ok=await platPage(platHead(`🖥 执行机详情`,`<a href="#/runners">← 全部执行机</a>`),async()=>{
    const all=(await api('/api/runners')).items||[];r=all.find(x=>x.id===id);if(!r)throw new Error('没有这台执行机（可能已删除）');
    m=(await api(`/api/runners/metrics?id=${enc(id)}&hours=${hours}`))['点']||[];
    const nets=[...new Set(m.flatMap(p=>Object.keys(p['网络']||{})))];
    return `<div class="card2"><div class="bd"><b style="font-size:16px">${esc(r['名称']||r.id)}</b> ${stPill(r['状态'])} ${r['状态']==='离线'?`<span class="small" style="color:var(--s4)">最后心跳 ${esc(r['最后心跳']||'—')}</span>`:''}
      <div class="small" style="margin-top:6px">${platRes(r)||'<span class="mut">还没有实时数据</span>'}</div>
      <div class="row small" style="gap:6px;margin-top:6px">范围 ${[1,6,24,72,168].map(h=>`<a data-rnh="${h}" style="${h===hours?'font-weight:700':''}">${h<24?h+' 小时':h/24+' 天'}</a>`).join(' · ')}<span class="mut">（15 秒采样，按 1 分钟聚合）</span></div></div></div>
      <div class="bgrid" style="grid-template-columns:repeat(auto-fill,minmax(420px,1fr));margin-top:12px">
        <div class="bcard"><b>CPU 负载 / 核数（1 分钟）</b>${platChart(m,'CPU',{title:'CPU'})}</div>
        <div class="bcard"><b>内存可用</b>${platChart(m,'内存可用比',{max:1,fmt:v=>Math.round(v*100)+'%',title:'内存'})}</div>
        <div class="bcard"><b>磁盘使用</b>${platChart(m,'磁盘使用比',{max:1,fmt:v=>Math.round(v*100)+'%',title:'磁盘'})}</div>
        <div class="bcard"><b>网络延迟（ms）</b>${nets.length?platChart(m,null,{series:nets.map(n=>[n,p=>{const x=(p['网络']||{})[n];return x==null?null:typeof x==='object'?x['延迟ms']:x}]),title:'网络'}):platChart([],'x')}</div></div>
      <h2 style="margin-top:16px">告警</h2>${await platAlertsHtml(r['名称']||r.id).catch(e=>platErr(e))}`});
  if(!ok)return;
  app.querySelectorAll('[data-rnh]').forEach(a=>a.onclick=()=>{sessionStorage.setItem('kb.rnHours',a.dataset.rnh);platRunnerDetail(id)});
  platAlertsBind(()=>platRunnerDetail(id));
}

// ---------------------------------------------------------------- 任务队列（REQ-013 REQ-019 REQ-036）
let platQ={mine:'1',status:'',period:''};
const PLAT_LIVE=['排队','已派','运行','等回答'];
function platTaskRow(t,o={}){
  const wait=t['状态']==='排队'?`${t['排队位置']?`第 ${t['排队位置']} 位`:''}${t['等待原因']?`<div class="small" style="color:var(--s1)">${esc(t['等待原因'])}</div>`:''}${t['重试次数']?`<div class="small mut">失败重试中（第 ${t['重试次数']} 次）</div>`:''}`:esc(t['等待原因']||'');
  const acct=t['引擎账号']||t['账号']||'';
  return `<tr><td class="mono small">${esc(t.id)}</td><td>${esc(t['类型'])}</td>${o.noPeriod?'':`<td>${t['期']?`<a href="#/p/${enc(t['期'])}">${esc(t['期'])}</a>`:''}</td>`}<td class="small">${esc(t['发起人']||'')}</td>
    <td>${stPill(t['状态'])}</td><td class="small">${wait}</td><td class="small">${esc(t['执行机']||'')}${acct?`<div class="mut">用 ${esc(typeof acct==='object'?acct['名称']||acct.id:acct)}</div>`:''}</td><td class="small">${t['重试次数']||0}</td><td class="small">${esc(t['创建时间']||'')}</td>
    <td class="small" style="white-space:nowrap">${PLAT_LIVE.includes(t['状态'])?`<a data-qcan="${esc(t.id)}">${t['状态']==='排队'?'取消':'停止'}</a>　`:''}<a data-qsc="${esc(t.id)}" title="各候选执行机的分数明细和排除原因">为什么派到这台</a>${o.ev?`　<a data-qev="${esc(t.id)}">证据</a>`:''}</td></tr>`}
const platTaskHead=o=>`<tr><th>任务</th><th>类型</th>${o&&o.noPeriod?'':'<th>期</th>'}<th>发起人</th><th>状态</th><th>排队 / 原因</th><th>执行机</th><th>重试</th><th>创建</th><th></th></tr>`;
function platTaskBind(root,re){
  root.querySelectorAll('[data-qcan]').forEach(a=>a.onclick=async()=>{if(await kbConfirm(`停止任务 ${a.dataset.qcan}？排队中的直接取消，运行中的通知执行机结束会话。`))platAct('/api/queue/cancel',{id:a.dataset.qcan},re)});
  root.querySelectorAll('[data-qsc]').forEach(a=>a.onclick=()=>platScore(a.dataset.qsc));
  root.querySelectorAll('[data-qev]').forEach(a=>a.onclick=()=>platEvidence(a.dataset.qev))}
async function platScore(id){
  openDlg(`为什么派到这台 · ${id}`,'<div class="kb-skel">加载中…</div>');
  try{const v=await api('/api/task/score?id='+enc(id)),cs=v['候选']||[],keys=[...new Set(cs.flatMap(c=>Object.keys(c['明细']||{})))];
    $('#dlgB').innerHTML=`<div class="small mut" style="margin-bottom:6px">分数 = 40×空闲容量比 + 25×内存余量比 + 15×CPU 余量比 + 10×磁盘余量比 + 10×亲和；同分选最近最少用的。磁盘 ≥90%、内存 &lt;8%、排空、待升级、离线直接排除。</div>
      ${cs.length?`<div class="tw"><table><tr><th>执行机</th><th>总分</th>${keys.map(k=>`<th>${esc(k)}</th>`).join('')}<th>排除原因</th></tr>${cs.map(c=>`<tr style="${c['执行机']===v['选中']?'font-weight:700;background:var(--s3b)':''}"><td>${esc(c['执行机'])}${c['执行机']===v['选中']?' ✓':''}</td><td>${c['排除原因']?'—':(+c['总分']||0).toFixed(1)}</td>${keys.map(k=>`<td>${(c['明细']||{})[k]!=null?(+c['明细'][k]).toFixed(1):''}</td>`).join('')}<td class="small" style="color:var(--s4)">${esc(c['排除原因']||'')}</td></tr>`).join('')}</table></div>`
        :platEmpty('还没派过：没有候选执行机')}`}
  catch(e){$('#dlgB').innerHTML=platErr(e)}}
async function platEvidence(task){
  openDlg(`真机证据 · ${task}`,'<div class="kb-skel">加载中…</div>');
  try{const v=await api('/api/evidence?task='+enc(task)),xs=v['证据']||v.items||[],mock=xs.some(x=>x['模拟']);
    const url=x=>'/api/evidence/file?id='+enc(x.id);
    $('#dlgB').innerHTML=xs.length?`${mock?'<div class="notice"><span class="plat-mock">模拟数据</span> 来自模拟设备提供方，不算进真机通过率</div>':''}
      <div class="plat-ev">${xs.map(x=>`<figure class="${x['失败']?'fail':''}">${x['类型']==='截图'?`<img src="${url(x)}" alt="${esc(x['步骤']||'截图')}" data-evimg="${url(x)}">`:`<a href="${url(x)}" target="_blank" rel="noopener" data-evlink>${x['类型']==='logcat'?'📄 logcat':'ℹ '+esc(x['类型'])}</a>`}
        <figcaption>${x['失败']?'<b style="color:var(--s4)">✗ 失败的一步</b> · ':''}${esc(x['步骤']||'')}${x['模拟']?' <span class="plat-mock">模拟</span>':''}<br>${esc(x['时间']||'')}${x['大小']?' · '+size(x['大小']):''}</figcaption></figure>`).join('')}</div>`
      :platEmpty('这个任务还没有证据（截图、logcat、设备信息）');
    // 文件状态统一处理（审查第 2 轮）：404 已清理、403 无权限、其他失败；截图和 logcat / 设备信息一个样
    const evState=(el,st)=>{const m=st===404?'🗑 文件已清理（超过保留期或已删除），记录还在':st===403?'🔒 没有权限看这条证据':`加载失败${st?'（'+st+'）':'（断网？）'}，稍后再试`;
      el.outerHTML=`<div class="kb-state small" data-evst="${st||0}">${m}</div>`};
    const probe=async u=>{const ctl=new AbortController();try{const r=await fetch(u,{credentials:'same-origin',signal:ctl.signal});ctl.abort();return r.status}catch(_){return 0}};
    $('#dlgB').querySelectorAll('[data-evimg]').forEach(i=>{i.onclick=()=>window.open(i.dataset.evimg,'_blank');
      i.onerror=async()=>evState(i,await probe(i.dataset.evimg))});
    // 链接保持原生点击打开（同步、在点击手势里，Safari 不拦）；打开弹窗时先探一下状态，不可用的直接换成说明
    $('#dlgB').querySelectorAll('[data-evlink]').forEach(async a=>{const st=await probe(a.href);if(st&&st!==200&&a.isConnected)evState(a,st)})}
  catch(e){$('#dlgB').innerHTML=platErr(e)}}
async function platQueuePage(){
  const Q=platQ;let xs=[];
  const ok=await platPage(platHead('任务队列','开工、Bug 分析 / 修复、全栈各端、插件、运行到手机、构建安装都排在这里，按标签和负载派给执行机。'),async()=>{
    const qs=Object.entries({mine:Q.mine,status:Q.status,period:Q.period,project:platCurProj()}).filter(([,v])=>v!=='').map(([k,v])=>k+'='+enc(v)).join('&');
    xs=(await api('/api/queue?'+qs)).items||[];
    return `<div class="row" style="gap:8px;flex-wrap:wrap;margin-bottom:10px"><select id="qMine"><option value="1" ${Q.mine==='1'?'selected':''}>我发起的</option><option value="0" ${Q.mine==='0'?'selected':''}>本项目全部</option></select>
      <select id="qSt"><option value="">全部状态</option>${['排队','已派','运行','等回答','完成','失败','取消'].map(s=>`<option ${s===Q.status?'selected':''}>${s}</option>`).join('')}</select>
      <input type="text" id="qPe" placeholder="期名（可空）" value="${esc(Q.period)}" style="width:180px;flex:none"><button id="qGo">筛选</button></div>
      ${xs.length?`<div class="tw"><table>${platTaskHead()}${xs.map(t=>platTaskRow(t,{ev:true})).join('')}</table></div>`:platEmpty(Q.mine==='1'?'你现在没有任务 · 去期页面点开工':'队列是空的')}`});
  if(!ok)return;
  $('#qGo').onclick=()=>{Object.assign(Q,{mine:$('#qMine').value,status:$('#qSt').value,period:$('#qPe').value.trim()});platQueuePage()};
  $('#qMine').onchange=$('#qSt').onchange=$('#qGo').onclick;
  platTaskBind(app,platQueuePage);
}

// ---------------------------------------------------------------- 📦 资源（REQ-016 REQ-044~REQ-048）+ 测试账号清单（REQ-046）
let platResTab='手机',platTaEnv='';
async function platResourcesPage(){
  const T=platResTab,tabs=`<div class="plat-tabs">${['手机','测试账号','引擎名额','测试账号清单'].map(t=>`<a data-rst="${t}" class="${t===T?'on':''}">${t}</a>`).join('')}</div>`;
  let rs=[],pv=[],tas=[];
  const ok=await platPage(platHead('📦 资源','手机、测试账号、AI 引擎名额共用一套占用和排队：先来先到，超时自动释放；占用者和管理员可以释放。')+tabs,async()=>{
    if(T==='测试账号清单'){tas=(await api('/api/test-accounts?project='+enc(platCurProj())+(platTaEnv?'&env='+enc(platTaEnv):''))).账号||[];return platTaHtml(tas)}
    rs=(await api('/api/resources?type='+enc(T)+'&project='+enc(platCurProj())))['资源']||[];
    if(T==='手机'){try{pv=((await api('/api/device-providers'))['提供方']||[]).filter(p=>p['启用']&&p['状态']==='不可用')}catch(_){}}
    const me=platMe?platMe['用户名']:'';
    const card=r=>`<div class="card2" style="margin-bottom:10px"><div class="bd"><div class="row" style="justify-content:space-between;gap:8px"><div><b>${esc(r['属性']&&(r['属性']['型号']||r['属性']['名称'])||r.id)}</b> <span class="mono mut small">${esc(r.id)}</span>
        ${r['提供方']?` <span class="chip">${esc(r['提供方'])}</span>`:''}${r['执行机']?` <span class="chip">🖥 ${esc(r['执行机'])}</span>`:''}${/mock|模拟/.test(r['提供方']||'')?' <span class="plat-mock">模拟</span>':''}</div>
        <span class="small">${(r['占用']||[]).length}/${r['允许同时使用数']||1} 在用${(r['队列']||[]).length?` · ${(r['队列']||[]).length} 个在排队`:''}</span></div>
      <div class="small mut" style="margin:4px 0">${Object.entries(r['属性']||{}).filter(([k])=>!['密码'].includes(k)).map(([k,v])=>`${esc(k)} ${esc(Array.isArray(v)?v.join('/'):v)}`).join(' · ')}</div>
      ${(r['占用']||[]).map(h=>`<div class="row small" style="gap:8px;padding:4px 0;border-top:1px solid var(--line)"><span>👤 ${esc(h['人']||'')}</span><span>${h['期']?`<a href="#/p/${enc(h['期'])}">${esc(h['期'])}</a>`:''}</span><span class="mono mut">${esc(h['会话']||'')}</span><span class="chip">${esc(h['方式']||'')}</span><span class="mut">${esc(platLeft(h['到期']))}</span><span style="flex:1"></span>
        ${h['人']===me||platAdmin()?`<a data-rsrel="${esc(r.id)}" data-s="${esc(h['会话']||'')}" data-f="${h['人']===me?'':'1'}">${h['人']===me?'释放':'强制释放'}</a>`:''}</div>`).join('')}
      ${(r['队列']||[]).length?`<details style="margin-top:4px"><summary class="small">排队 ${(r['队列']||[]).length} 个</summary><table><tr><th>位置</th><th>人</th><th>期</th><th>排队时间</th><th>预计</th></tr>${r['队列'].map(w=>`<tr><td>第 ${esc(w['位置'])} 位</td><td>${esc(w['人']||'')}</td><td>${esc(w['期']||'')}</td><td class="small">${esc(w['排队时间']||'')}</td><td class="small">${w['预计分钟']!=null?`约 ${esc(w['预计分钟'])} 分钟`:''}</td></tr>`).join('')}</table></details>`:''}</div></div>`;
    const empty={手机:'还没有手机：执行机连上 adb 手机后自动出现，或在「设置 → 📱 设备提供方」启用模拟远程平台',测试账号:'还没有测试账号',引擎名额:'还没有 AI 账号名额：在「设置 → 🤖 AI 账号」添加'}[T];
    return `${pv.map(p=>`<div class="notice">⚠ 设备提供方「${esc(p['名称'])}」不可用，它的手机暂时申请不到</div>`).join('')}${rs.length?rs.map(card).join(''):platEmpty(empty,T==='测试账号'?'＋ 添加测试账号':T==='引擎名额'&&platPOwner()?'去配置 AI 账号':'',T==='测试账号'?'rsTaAdd':'rsEaGo')}`});
  const re=()=>platResourcesPage();
  app.querySelectorAll('[data-rst]').forEach(a=>a.onclick=()=>{platResTab=a.dataset.rst;re()});
  if(!ok)return;
  if($('#rsTaAdd'))$('#rsTaAdd').onclick=()=>platTaDlg(null,re);
  if($('#rsEaGo'))$('#rsEaGo').onclick=()=>location.hash='#/settings/ai';
  app.querySelectorAll('[data-rsrel]').forEach(a=>a.onclick=async()=>{const f=!!a.dataset.f;
    if(!await kbConfirm(f?`强制收回 ${a.dataset.rsrel}？会通知原占用的会话并写审计；它之后再操作这个资源会被拒绝。`:`释放 ${a.dataset.rsrel}？`,{danger:f}))return;
    platAct('/api/resource/release',{资源:a.dataset.rsrel,会话:a.dataset.s,强制:f},re)});
  if(T==='测试账号清单')platTaBind(tas,re);
}
function platTaHtml(tas){
  return `<div class="row" style="gap:8px;margin-bottom:10px"><span class="small">项目 <b>${esc(platProjName(platCurProj())||'—')}</b></span><select id="taEnv"><option value="">全部环境</option>${['测试','预发','线上'].map(e=>`<option ${e===platTaEnv?'selected':''}>${e}</option>`).join('')}</select><span style="flex:1"></span><button class="pri" id="taAdd">＋ 添加</button></div>
    ${tas.length?`<div class="tw"><table><tr><th>名称</th><th>账号</th><th>密码</th><th>环境</th><th>标签</th><th>同时使用</th><th>允许 AI</th><th>占用</th><th></th></tr>${tas.map(a=>`<tr><td>${esc(a['名称'])}</td><td class="mono">${esc(a['账号'])}</td><td class="mut">••••••</td><td>${esc(a['环境'])}</td><td>${platTags(a['标签'])}</td><td>${esc(a['允许同时使用数']??1)}</td><td>${a['允许AI']?'是':'<span title="线上环境默认不给 AI 用">否</span>'}</td>
      <td class="small">${(a['占用']||[]).map(h=>esc(h['人']||h['会话']||'')).join('、')||'<span class="mut">空闲</span>'}</td><td class="small"><a data-taed="${esc(a.id)}">编辑</a>　<a data-tadel="${esc(a.id)}">删除</a></td></tr>`).join('')}</table></div>
      <p class="small mut">密码只存钥匙串 / 加密存储；AI 会话用 kb_account_claim 申请，用 kb_base_secret 读明文（写审计）。</p>`:platEmpty('还没有测试账号','＋ 添加','taAdd2')}`}
function platTaBind(tas,re){$('#taEnv').onchange=()=>{platTaEnv=$('#taEnv').value;re()};
  $('#taAdd').onclick=()=>platTaDlg(null,re);if($('#taAdd2'))$('#taAdd2').onclick=()=>platTaDlg(null,re);
  app.querySelectorAll('[data-taed]').forEach(a=>a.onclick=()=>platTaDlg(tas.find(x=>x.id===a.dataset.taed),re));
  app.querySelectorAll('[data-tadel]').forEach(a=>a.onclick=async()=>{if(await kbConfirm('删除这个测试账号？'))platAct('/api/test-account',{id:a.dataset.tadel,删除:true},re)})}
function platTaDlg(a,re){a=a||{};const ps=(platMe&&platMe['项目'])||[];
  openDlg(a.id?'编辑测试账号':'添加测试账号',`<div class="form"><label>项目<select id="taP">${ps.map(p=>`<option value="${esc(p.id)}" ${p.id===(a['项目']||platCurProj())?'selected':''}>${esc(p['名称'])}</option>`).join('')}</select></label>
    <label>环境<select id="taE">${['测试','预发','线上'].map(e=>`<option ${e===(a['环境']||'测试')?'selected':''}>${e}</option>`).join('')}</select></label>
    <label>名称<input type="text" id="taN" value="${esc(a['名称']||'')}" placeholder="如：VIP 老用户"></label><label>账号<input type="text" id="taA" value="${esc(a['账号']||'')}"></label>
    <label>密码<input type="password" id="taW" placeholder="${a.id?'不改留空':''}" autocomplete="new-password"></label>
    <label>标签<input type="text" id="taT" value="${esc((a['标签']||[]).join(', '))}" placeholder="逗号隔开：VIP, 新用户, 海外"></label>
    <label>允许同时使用数<input type="number" id="taC" min="1" value="${esc(a['允许同时使用数']||1)}"></label>
    <label class="row"><input type="checkbox" id="taAi" ${a.id?(a['允许AI']?'checked':''):'checked'} style="width:auto"> 允许 AI 会话申请（线上环境默认不允许）</label>
    <div class="row"><button class="pri" id="taGo">保存</button></div></div>`);
  $('#taE').onchange=()=>{if($('#taE').value==='线上')$('#taAi').checked=false};
  $('#taGo').onclick=async()=>{const b={项目:$('#taP').value,环境:$('#taE').value,名称:$('#taN').value.trim(),账号:$('#taA').value.trim(),标签:$('#taT').value.split(/[,，]+/).map(s=>s.trim()).filter(Boolean),允许同时使用数:+$('#taC').value||1,允许AI:$('#taAi').checked};
    if(a.id)b.id=a.id;if($('#taW').value)b['密码']=$('#taW').value;const r=await platAct('/api/test-account',b);if(r){dlg.close();re()}}}

// ---------------------------------------------------------------- ⚙ 设置 → 🤖 AI 账号（REQ-018 REQ-038~REQ-042）
let platUsageBy='项目',platCoolT=null;
async function platAiPage(){
  let xs=[],usage=[];
  const ok=await platPage(platHead('🤖 AI 账号','引擎账号池：每个项目配 1~2 个共享账号，按备用链做故障切换（不做轮询绕限流）。<a href="#/settings">← 设置</a>','<button class="pri" id="eaNew">＋ 添加账号</button>'),async()=>{
    xs=(await api('/api/engine-accounts?project='+enc(platCurProj())))['账号']||[];
    try{usage=(await api('/api/usage?by='+enc(platUsageBy)+'&days=30&project='+enc(platCurProj())))['行']||[]}catch(_){}
    const st=a=>a['状态']==='冷却'?`${stPill('冷却')} <span class="small" data-cool="${esc(a['冷却到']||'')}">${esc(platLeft(a['冷却到']))}</span>`:stPill(a['状态'],a['原因']);
    return `${xs.length?`<div class="tw"><table class="etbl"><tr><th>备用顺序</th><th>名称</th><th>引擎</th><th>类型</th><th>项目 / 所属人</th><th>状态</th><th>原因</th><th>并发</th><th>RPM</th><th>运行中</th><th>模型不可用</th><th>换引擎</th><th></th></tr>
      ${xs.slice().sort((a,b)=>(a['备用顺序']||0)-(b['备用顺序']||0)).map(a=>`<tr><td>${esc(a['备用顺序']??'')}</td><td><b>${esc(a['名称'])}</b></td><td>${esc(a['引擎'])}</td><td>${esc(a['类型'])}</td><td class="small">${esc(a['类型']==='个人'?a['所属人']:platProjName(a['项目']))}</td>
        <td>${st(a)}</td><td class="small mut">${esc(a['原因']||'')}</td><td>${esc(a['并发上限'])}</td><td>${esc(a['RPM上限'])}</td><td>${esc(a['运行中']||0)}</td><td class="small">${(a['模型不可用']||[]).map(esc).join('、')}</td><td>${a['允许换引擎']?'允许':'—'}</td>
        <td class="small" style="white-space:nowrap"><a data-eaprobe="${esc(a.id)}" title="极短自检，不调工具；连续 2 次成功才恢复">探测</a>　<a data-eaed="${esc(a.id)}">编辑</a>　<a data-eadel="${esc(a.id)}">删除</a></td></tr>`).join('')}</table></div>
      <p class="small mut">登录失效 / 额度用完 → 不可用（不自动恢复，通知负责人）；429 / 529 / 503 / 网络 → 冷却（60 秒起、上限 15 分钟），不换号；连续 3 次同类失败才改状态。最近 1 小时 429 超过 2% 自动把并发和 RPM 下调 20%。</p>`
      :`<div class="kb-state"><b>本项目还没有 AI 账号</b><p class="mut">没有可用账号时任务会排队并提示「请项目负责人在 设置 → AI 账号 配置」。</p><p><button class="pri" id="eaNew2">＋ 添加共享账号</button></p></div>`}
    <h2 style="margin-top:18px">用量（最近 30 天）</h2><div class="plat-tabs">${['人','项目','账号'].map(b=>`<a data-uby="${b}" class="${b===platUsageBy?'on':''}">按${b}</a>`).join('')}</div>
    ${usage.length?`<div class="tw"><table><tr><th>${esc(platUsageBy)}</th><th>会话数</th><th>时长</th><th>输入 token</th><th>输出 token</th><th>预算（日 / 月）</th><th>已用</th></tr>${usage.map(r=>`<tr><td>${esc(platUsageBy==='项目'?platProjName(r['对象']):r['对象'])}</td><td>${esc(r['会话数']||0)}</td><td>${Math.round((r['时长秒']||0)/60)} 分钟</td><td>${esc(r['输入token']||0)}${r['估算']?' <span class="chip" title="引擎没报，按时长估算">估算</span>':''}</td><td>${esc(r['输出token']||0)}</td>
      <td class="small">${r['预算日']||r['预算月']?`${esc(r['预算日']||'不限')} / ${esc(r['预算月']||'不限')}`:'<span class="mut">不限制</span>'}</td><td>${r['已用比']!=null&&(r['预算日']||r['预算月'])?`<span style="color:${r['已用比']>=1?'var(--s4)':r['已用比']>=.8?'var(--s1)':'inherit'}">${Math.round(r['已用比']*100)}%</span>`:''}</td></tr>`).join('')}</table></div>`:platEmpty('还没有用量')}
    ${platPOwner()?`<h2 style="margin-top:18px">预算</h2><div class="bcard"><div class="row" style="gap:8px;flex-wrap:wrap">
      <select id="bgS"><option>项目</option>${platAdmin()?'<option>人</option>':''}</select><input type="text" id="bgO" value="${esc(platCurProj())}" placeholder="项目编号 / 用户名" style="width:150px;flex:none">
      日上限 <input type="number" id="bgD" min="0" value="0" style="width:110px"> 月上限 <input type="number" id="bgM" min="0" value="0" style="width:110px"> token
      超额 <select id="bgP"><option>排队</option><option>降级</option></select><input type="text" id="bgC" placeholder="降级模型（开降级才填）" style="width:170px;flex:none"><button class="pri" id="bgGo">保存</button></div>
      <div class="small mut" style="margin-top:6px">0 = 不限制只统计。到 80% 通知本人和项目负责人一次；到 100% 新任务排队，开了「降级」改用便宜模型。单个会话超过项目日预算 30% 会暂停问发起人。</div>
      <div class="row" style="gap:8px;margin-top:10px"><b class="small">临时加额</b><input type="number" id="grA" min="1" placeholder="额度（token）" style="width:140px;flex:none"><input type="text" id="grR" placeholder="原因" style="flex:1;min-width:120px"><button id="grGo">加额（24 小时有效）</button></div></div>`:''}`});
  const re=()=>platAiPage();
  if($('#eaNew'))$('#eaNew').onclick=()=>platEaDlg({类型:'项目共享',项目:platCurProj()},re);if(!ok)return;
  if($('#eaNew2'))$('#eaNew2').onclick=()=>platEaDlg({类型:'项目共享',项目:platCurProj()},re);
  app.querySelectorAll('[data-uby]').forEach(a=>a.onclick=()=>{platUsageBy=a.dataset.uby;re()});
  app.querySelectorAll('[data-eaed]').forEach(a=>a.onclick=()=>platEaDlg(xs.find(x=>x.id===a.dataset.eaed),re));
  app.querySelectorAll('[data-eadel]').forEach(a=>a.onclick=async()=>{if(await kbConfirm('删除这个 AI 账号？用它的排队任务会换备用账号。'))platAct('/api/engine-account',{id:a.dataset.eadel,删除:true},re)});
  app.querySelectorAll('[data-eaprobe]').forEach(a=>a.onclick=async()=>{a.textContent='探测中…';try{const r=await post('/api/engine-account/probe',{id:a.dataset.eaprobe});toast(`${r['名称']||''}：${r['状态']||''}${r['原因']?'（'+r['原因']+'）':''}`)}catch(e){toast(e.message)}re()});
  if($('#bgGo'))$('#bgGo').onclick=()=>platAct('/api/budget',{范围:$('#bgS').value,对象:$('#bgO').value.trim(),日上限:+$('#bgD').value||0,月上限:+$('#bgM').value||0,超额策略:$('#bgP').value,降级模型:$('#bgC').value.trim()},re);
  if($('#grGo'))$('#grGo').onclick=async()=>{const n=+$('#grA').value;if(!(n>0))return toast('填加多少');if(await kbConfirm(`给本项目临时加 ${n} token，24 小时有效（写审计）？`))platAct('/api/budget/grant',{项目:platCurProj(),额度:n,原因:$('#grR').value.trim()},re)};
  clearInterval(platCoolT);platCoolT=setInterval(()=>{const els=document.querySelectorAll('[data-cool]');if(!els.length)return clearInterval(platCoolT);els.forEach(x=>x.textContent=platLeft(x.dataset.cool))},15000);
}
function platEaDlg(a,re,mine){a=a||{};const ps=(platMe&&platMe['项目'])||[],personal=mine||a['类型']==='个人';
  openDlg(a.id?'编辑 AI 账号':personal?'绑定我的 AI 账号':'添加 AI 账号',`<div class="form">
    <label>引擎<select id="eaE">${['claude','codex'].concat(a['引擎']&&!['claude','codex'].includes(a['引擎'])?[a['引擎']]:[]).map(e=>`<option ${e===a['引擎']?'selected':''}>${esc(e)}</option>`).join('')}</select></label>
    <label>名称<input type="text" id="eaN" value="${esc(a['名称']||'')}" placeholder="如：团队 Claude Max 1"></label>
    ${personal?'':`<label>类型<select id="eaK"><option ${a['类型']!=='个人'?'selected':''}>项目共享</option><option ${a['类型']==='个人'?'selected':''}>个人</option></select></label>
      <label>项目<select id="eaP">${ps.map(p=>`<option value="${esc(p.id)}" ${p.id===(a['项目']||platCurProj())?'selected':''}>${esc(p['名称'])}</option>`).join('')}</select></label>`}
    <label>登录态 / API Key<textarea id="eaL" rows="2" class="mono" placeholder="${a.id?'不改留空（只写不读）':'API Key，或 ~/.claude/.credentials.json 的内容'}"></textarea></label>
    <div class="row" style="gap:10px"><label>同时会话上限<input type="number" id="eaC" min="1" value="${esc(a['并发上限']||3)}" style="width:90px"></label><label>每分钟请求上限<input type="number" id="eaR" min="1" value="${esc(a['RPM上限']||30)}" style="width:90px"></label>${personal?'':`<label>备用顺序<input type="number" id="eaO" value="${esc(a['备用顺序']??'')}" style="width:80px"></label>`}</div>
    ${personal?`<label class="row"><input type="checkbox" id="eaM" ${a['优先用我的']?'checked':''} style="width:auto"> 优先用我的（个人账号故障时会先问你一次再回落到共享账号）</label>`
      :`<label class="row"><input type="checkbox" id="eaS" ${a['允许换引擎']?'checked':''} style="width:auto"> 允许换引擎（只对 Bug 分析、方案这类任务生效；开发、修复不换）</label>`}
    <div class="row"><button class="pri" id="eaGo">保存</button></div></div>`);
  $('#eaGo').onclick=async()=>{const b={引擎:$('#eaE').value,名称:$('#eaN').value.trim(),并发上限:+$('#eaC').value||3,RPM上限:+$('#eaR').value||30};
    if(a.id)b.id=a.id;if($('#eaL').value.trim())b['登录态']=$('#eaL').value.trim();
    if(personal){b['类型']='个人';b['优先用我的']=$('#eaM').checked}else{b['类型']=$('#eaK').value;b['项目']=$('#eaP').value;b['允许换引擎']=$('#eaS').checked;if($('#eaO').value!=='')b['备用顺序']=+$('#eaO').value}
    const r=await platAct('/api/engine-account',b);if(r){dlg.close();re()}}}

// ---------------------------------------------------------------- ⚙ 设置 → 📱 设备提供方（REQ-043）
async function platProvidersPage(){
  let xs=[];
  const ok=await platPage(platHead('📱 设备提供方','手机从哪来：本机 adb、执行机上的手机、远程设备平台（本期只有模拟实现）。申请、排队、释放对它们一视同仁。<a href="#/settings">← 设置</a>'),async()=>{
    xs=(await api('/api/device-providers'))['提供方']||[];
    return xs.length?xs.map(p=>`<div class="card2" style="margin-bottom:10px"><div class="bd"><div class="row" style="justify-content:space-between"><div><b>${esc(p['名称']||p.id)}</b> <span class="chip">${esc(p['类型'])}</span> ${p['启用']?stPill(p['状态']):stPill('未启用')} ${p['模拟']?'<span class="plat-mock">模拟数据</span>':''}</div>
        <span class="small">${p['启用']?`${esc(p['设备数']||0)} 台设备`:''}</span></div>
      <div class="row small" style="gap:12px;margin-top:8px;flex-wrap:wrap"><label class="row"><input type="checkbox" data-pvon="${esc(p.id)}" ${p['启用']?'checked':''} style="width:auto"> 启用</label>
        ${p['模拟']||p['类型']==='remote'?`<label>模拟行为 <select data-pvbh="${esc(p.id)}">${['正常','故意失败','故意慢'].map(b=>`<option ${b===(p['模拟行为']||'正常')?'selected':''}>${b}</option>`).join('')}</select></label>`:''}
        ${p['类型']==='remote'?`<details><summary>配置</summary><textarea data-pvcf="${esc(p.id)}" rows="4" class="mono" style="width:420px;max-width:80vw" placeholder='{"地址":"https://…","令牌机密编号":"B0xx"} 或 模拟：{"设备":[{"品牌":"Xiaomi","系统":"Android","版本":"13"}]}'>${esc(p['配置']?JSON.stringify(p['配置'],null,1):'')}</textarea></details>`:''}
        <button data-pvsave="${esc(p.id)}">保存</button></div>
      ${p['类型']==='remote'&&!p['启用']?'<div class="small mut" style="margin-top:6px">远程设备平台未启用：不影响本机和执行机上的手机。启用「模拟」可以走通 申请→安装→执行→截图 / 日志→释放，报告上标「模拟数据」。</div>':''}</div></div>`).join('')
      :platEmpty('还没有设备提供方')});
  if(!ok)return;
  app.querySelectorAll('[data-pvsave]').forEach(b=>b.onclick=()=>{const id=b.dataset.pvsave,q=s=>app.querySelector(`[${s}="${CSS.escape(id)}"]`),body={id,启用:q('data-pvon').checked};
    if(q('data-pvbh'))body['模拟行为']=q('data-pvbh').value;
    if(q('data-pvcf')&&q('data-pvcf').value.trim()){try{body['配置']=JSON.parse(q('data-pvcf').value)}catch(_){return toast('配置要是 JSON')}}
    platAct('/api/device-provider',body,platProvidersPage)});
}

// ---------------------------------------------------------------- 设置页顶部：团队与执行机入口、配额、按执行机分组的副本池（REQ-015 REQ-019 REQ-020）
async function platSettingsBox(){
  const box=$('#platSet');if(!box||!platMe)return;
  const L=[['#/admin','📊 总览看板','全员进度、项目、执行机、AI 账号、告警',platAdmin()],['#/me','👤 我的','令牌、平台账号、通知、AI 账号',1],['#/queue','☰ 任务队列','排队、在跑、派活分数',1],['#/resources','📦 资源','手机、测试账号、引擎名额',1],
    ['#/project-admin','🗂 项目','成员和角色',platPOwner()],['#/audit','🔐 审计','机密读取记录',platPOwner()],['#/members','👥 成员','账号、停用交接',platAdmin()],
    ['#/runners','🖥 执行机','接入、标签、容量、告警',platAdmin()],['#/settings/ai','🤖 AI 账号','账号池、备用链、用量、预算',platPOwner()],['#/settings/providers','📱 设备提供方','本机 / 执行机 / 远程（模拟）',platAdmin()]].filter(x=>x[3]);
  let quota=null;if(platAdmin()){try{quota=await api('/api/quota')}catch(_){}}
  let pool=null;try{pool=await api('/api/pool')}catch(_){}
  const q=quota||{};
  box.innerHTML=`<h2>团队与执行机</h2><div class="bgrid" style="grid-template-columns:repeat(auto-fill,minmax(200px,1fr))">${L.map(x=>`<a class="bcard" href="${x[0]}" style="color:inherit;text-decoration:none"><b>${x[1]}</b><div class="small mut">${x[2]}</div></a>`).join('')}</div>
    ${platAdmin()?`<div class="bcard" style="margin-top:10px"><b>配额</b> <span class="small mut">同时运行的会话数和排队上限，改完下一次派活就生效（REQ-019）</span>
      <div class="row small" style="gap:10px;flex-wrap:wrap;margin-top:6px">每人 <input type="number" id="qtU" min="1" value="${esc(q['每人']??3)}" style="width:64px"> 每项目 <input type="number" id="qtP" min="1" value="${esc(q['每项目']??8)}" style="width:64px"> 每项目队列 <input type="number" id="qtQ" min="1" value="${esc(q['队列每项目']??50)}" style="width:72px"><button id="qtGo">保存</button></div></div>`:''}
    ${pool&&Array.isArray(pool.items)&&pool.items.length?`<div class="bcard" style="margin-top:10px"><b>工作副本（按执行机）</b>${pool.items.map(g=>`<div style="margin-top:6px"><div class="small"><b>🖥 ${esc(g['执行机']||'本机')}</b></div>${(g['副本']||[]).length?`<div class="tw"><table><tr><th>仓库</th><th>序号</th><th>分支</th><th>马甲线</th><th>占用</th><th></th></tr>${g['副本'].map(c=>`<tr><td>${esc(c['仓库'])}</td><td>${esc(c['序号'])}</td><td class="mono small">${esc(c['分支']||'')}</td><td class="small">${esc(c['马甲线']||'')}</td><td class="small">${esc(c['占用会话']||'空闲')}</td><td>${c['可能有改动']?'<span class="plat-st wait">可能有改动</span>':''}</td></tr>`).join('')}</table></div>`:'<div class="small mut">没有副本</div>'}</div>`).join('')}</div>`:''}`;
  if($('#qtGo'))$('#qtGo').onclick=()=>platAct('/api/quota',{每人:+$('#qtU').value,每项目:+$('#qtP').value,队列每项目:+$('#qtQ').value});
  if(platAdmin())platPortBox(box);
}

// ---------------------------------------------------------------- 🌐 网页端口：启动后在平台里改并重启（启动前改用 kb port set）
async function platPortBox(box){
  let w;try{w=await api('/api/web/port')}catch(e){return}
  const el=document.createElement('div');el.className='bcard';el.style.marginTop='10px';el.id='wpBox';
  el.innerHTML=`<b>🌐 网页端口</b> <span class="small mut">网页、远程 MCP、执行机都连这个端口；本机 Claude Code 走 stdio 不受影响</span>
    <div class="small" style="margin-top:6px">正在用 <b class="mono">${esc(w['端口'])}</b>${w['配置端口']!==w['端口']?` · 配置的是 <span class="mono">${esc(w['配置端口'])}</span>`:''} · 来源 ${esc(w['来源'])}</div>
    ${w['提示']?`<div class="small" style="margin-top:4px;color:var(--warn,#b26a00)">${esc(w['提示'])}</div>`:''}
    ${w['可在线修改']?`<div class="row small" style="gap:8px;margin-top:6px;flex-wrap:wrap">新端口 <input type="number" id="wpNew" min="1024" max="65535" placeholder="${esc(w['端口'])}" style="width:100px"><button id="wpGo">保存并重启</button>
      <span class="mut">保存后网页自动重启并跳到新地址；在线执行机自动改连。启动前改：<code>kb port set &lt;端口&gt;</code></span></div>`
      :`<div class="small mut" style="margin-top:6px">${esc(w['不能改的原因'])}</div>`}
    <div id="wpMsg" class="small" style="margin-top:6px"></div>`;
  const old=$('#wpBox');if(old)old.remove();box.appendChild(el);
  if(!$('#wpGo'))return;
  $('#wpGo').onclick=async()=>{const p=+$('#wpNew').value;if(!p)return toast('先填新端口');
    if(!await kbConfirm(`把网页端口改成 ${p} 并重启？\n重启期间网页、MCP 会断开十几秒；kb token 接入的远程 Claude Code 要改地址。`))return;
    let r;try{r=await post('/api/web/port',{port:p})}catch(e){return toast(e.message)}
    $('#wpMsg').innerHTML=`<b>${esc(r.msg)}</b>${(r['提示']||[]).map(t=>`<div class="mut">· ${esc(t)}</div>`).join('')}<div class="mut" id="wpWait">等新地址 ${esc(r.url)} 起来…</div>`;
    $('#wpGo').disabled=true;platPortWait(r.url,0)};
}
function platPortWait(url,n){   // 新端口跨源：no-cors 能连通就算起来了，然后带上当前页面跳过去（Cookie 不分端口，不用重新登录）
  if(n>60){const w=$('#wpWait');if(w)w.innerHTML=`还没连上：稍后手动打开 <a href="${esc(url)}">${esc(url)}</a>`;return}
  setTimeout(()=>fetch(url+'login',{mode:'no-cors',cache:'no-store'}).then(()=>{location.href=url+location.hash}).catch(()=>platPortWait(url,n+1)),n?1000:2500);
}

// ---------------------------------------------------------------- 期页面：负责人 / 项目 / 版本号、按 can 置灰、任务 / 产物 / 证据（REQ-002 REQ-003 REQ-013 REQ-030 REQ-049 REQ-050）
function platMetaHtml(d){if(d['版本号']==null&&!d['负责人'])return'';
  return `<span>👤 负责人 <b>${esc(d['负责人']||'—')}</b> · 项目 <b>${esc(platProjName(d['项目'])||'—')}</b> <a id="pOwnEd" title="改负责人 / 所属项目">改</a></span>`}
function platOwnerBind(d){const a=$('#pOwnEd');if(!a)return;a.onclick=async()=>{
  let us=[];if(platAdmin()){try{us=(await api('/api/users')).items.filter(u=>u['状态']!=='停用')}catch(_){}}
  const ps=(platMe&&platMe['项目'])||[];
  openDlg('改负责人 / 所属项目',`<div class="form"><label>负责人${us.length?`<select id="poU">${us.map(u=>`<option ${u['用户名']===d['负责人']?'selected':''}>${esc(u['用户名'])}</option>`).join('')}</select>`:`<input type="text" id="poU" value="${esc(d['负责人']||'')}">`}</label>
    <label>所属项目<select id="poP">${ps.map(p=>`<option value="${esc(p.id)}" ${p.id===d['项目']?'selected':''}>${esc(p['名称'])}</option>`).join('')}</select></label>
    <div class="small mut">别人同时改过这一期时会提示刷新，不会覆盖对方的修改。</div><div class="row"><button class="pri" id="poGo">保存</button></div></div>`);
  $('#poGo').onclick=async()=>{const b={name:d.name,version:d['版本号']};if($('#poU').value.trim()!==(d['负责人']||''))b['负责人']=$('#poU').value.trim();if($('#poP').value!==d['项目'])b.project=$('#poP').value;
    try{const r=await post('/api/period/owner',b);toast(r.msg);dlg.close();render()}catch(e){toast(e.message);if(e.status===409){dlg.close();render()}}}}}
// 按 can 置灰：不在 can 里的按钮加 .plat-dis + 原因，点了只提示（异步画出来的按钮也盖住）
const PLAT_CAN_SEL={拍板:'#stOk,#stReAns',开工:'#stDev,[data-redev],#dvGo,#dvStart,#dvRetest',验收:'#stAcc',删除:'#delP,#mmDel,#puDel,#pDel',svn提交:'[data-bsvn]',推动:'#stGo,#stRe,[data-devrun],#rtGo',加资料:'#tkSend,#logGo'};
let platCanObs=null,platCanD=null;
function platCan(d){if(platCanObs){platCanObs.disconnect();platCanObs=null}platCanD=null;if(!Array.isArray(d.can))return;platCanD=d;
  const apply=()=>{if(!platCanD||curPeriod()!==platCanD.name){if(platCanObs){platCanObs.disconnect();platCanObs=null}return}
    Object.entries(PLAT_CAN_SEL).forEach(([act,sel])=>{if(platCanD.can.includes(act))return;
      app.querySelectorAll(sel).forEach(el=>{if(el.classList.contains('plat-dis'))return;el.classList.add('plat-dis');el.dataset.platWhy=['加资料','推动'].includes(act)?'只读账号不能修改':act==='删除'?'只有本人或管理员能删除':'只有本期负责人或项目负责人能'+act;el.title='没有权限：'+el.dataset.platWhy})})};
  apply();platCanObs=new MutationObserver(()=>apply());platCanObs.observe(app,{childList:true,subtree:true})}
document.addEventListener('click',e=>{const el=e.target.closest&&e.target.closest('.plat-dis');if(!el)return;e.preventDefault();e.stopImmediatePropagation();toast('没有权限：'+(el.dataset.platWhy||''))},true);
async function platTaskBox(name){
  let box=$('#platTasks');if(!box){const dv=$('#devBox');if(!dv)return;box=document.createElement('div');box.id='platTasks';box.style.marginTop='10px';dv.after(box)}
  let ts=[],arts=[];
  try{ts=(await api('/api/queue?period='+enc(name)+'&mine=0')).items||[]}catch(e){if(e.status&&e.status!==404&&e.status!==403)box.innerHTML=`<div class="small mut">任务：${esc(e.message)}</div>`;return}
  try{arts=(await api('/api/artifacts?period='+enc(name)))['产物']||[]}catch(_){}
  if(curPeriod()!==name||!$('#platTasks'))return;
  const live=ts.filter(t=>PLAT_LIVE.includes(t['状态'])),old=ts.filter(t=>!PLAT_LIVE.includes(t['状态'])).slice(-8).reverse();
  if(!ts.length&&!arts.length){box.innerHTML='';return}
  const tip=t=>t['状态']==='排队'&&/掉线|离线|回队列/.test(t['等待原因']||'')?'<div class="notice small">执行机掉线，任务已回队列；原副本保留（标「可能有改动」），原执行机恢复后优先派回。</div>':'';
  box.innerHTML=`<div class="card2"><div class="bd"><b>任务</b> <span class="small mut">本期在队列里的会话（开工、修复、运行到手机……）<a href="#/queue">全部任务 →</a></span>
    ${live.map(tip).join('')}
    ${live.length?`<div class="tw" style="margin-top:6px"><table>${platTaskHead({noPeriod:1})}${live.map(t=>platTaskRow(t,{noPeriod:1,ev:true})).join('')}</table></div>`:'<div class="small mut" style="margin-top:4px">现在没有排队或在跑的任务</div>'}
    ${old.length?`<details style="margin-top:6px"><summary class="small">最近结束的 ${old.length} 个</summary><div class="tw"><table>${platTaskHead({noPeriod:1})}${old.map(t=>platTaskRow(t,{noPeriod:1,ev:true})).join('')}</table></div></details>`:''}
    ${arts.length?`<div style="margin-top:10px"><b>构建产物</b> <span class="small mut">执行机上编译登记的，保留 7 天或每项目最近 20 个</span><div class="tw"><table><tr><th>版本</th><th>分支</th><th>提交</th><th>SHA256</th><th>大小</th><th>执行机</th><th>时间</th><th></th></tr>${arts.map(a=>`<tr><td class="mono small">${esc(a['版本']||'')}</td><td class="mono small">${esc(a['分支']||'')}</td><td class="mono small">${esc((a['提交']||'').slice(0,10))}</td><td class="mono small" title="${esc(a.sha256||'')}">${esc((a.sha256||'').slice(0,12))}</td><td class="small">${a['大小']?size(a['大小']):''}</td><td class="small">${esc(a['执行机']||'')}</td><td class="small">${esc(a['时间']||'')}</td><td><a href="/api/artifact/download?id=${enc(a.id)}">下载</a></td></tr>`).join('')}</table></div></div>`:''}</div></div>`;
  platTaskBind(box,()=>platTaskBox(name));
}

// ---------------------------------------------------------------- 路由
Object.assign(KB.routes,{'/me':platMePage,'/members':platMembersPage,'/project-admin':platProjectsPage,'/audit':platAuditPage,'/runners':platRunnersPage,
  '/runner/':platRunnerDetail,'/queue':platQueuePage,'/resources':platResourcesPage,'/settings/ai':platAiPage,'/settings/providers':platProvidersPage});
Object.assign(KB.pages,{me:platMePage,members:platMembersPage,projectAdmin:platProjectsPage,audit:platAuditPage,runners:platRunnersPage,queue:platQueuePage,resources:platResourcesPage});
KB.me=null;
