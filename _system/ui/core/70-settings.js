// ==== 设置：界面、管家、引擎、角色、流程、插件
// ---------------- 设置：开发流程（其他研发组可改成自己的流程）
function plDlg(d,p,mount){
  const on=new Set(p['时机']);
  const field=c=>{const v=p['配置'][c['键']]||'', id='plc_'+c['键'];
    const inp=c['类型']==='base'?`<select data-pc="${esc(c['键'])}"><option value="">（不选）</option>${(d.baseEntries||[]).map(b=>`<option value="${esc(b['编号'])}" ${b['编号']===v?'selected':''}>${esc(b['编号']+' '+b['名称'])}</option>`).join('')}</select>`
      :(c['类型']||'').startsWith('select:')?`<select data-pc="${esc(c['键'])}">${c['类型'].slice(7).split('|').map(o=>`<option ${o===v?'selected':''}>${esc(o)}</option>`).join('')}</select>`
      :`<input type="text" data-pc="${esc(c['键'])}" value="${esc(v)}" placeholder="${esc(c['示例']?'如：'+c['示例']:c['默认']?'默认 '+c['默认']:'')}">`;
    return `<label>${esc(c['标题'])}${inp}${c['说明']?`<span class="small mut">${esc(c['说明'])}</span>`:''}</label>`};
  openDlg(`${mount?'挂载':'设置'}插件 · ${p['名称']}`,`<div class="form"><div class="small mut">${esc(p['说明']||'')}</div>
    ${p['工具']?`<div class="small"><b>按需调用</b>　<span class="mut">不挂时机：在对话里把${esc(p['参数']||'参数')}给 Claude（它用 kb_plugin_call 调用），或终端 <code>kb plugin call ${esc(p.id)} …</code></span></div>`:`<div><b class="small">在什么时候运行</b> <span class="small mut">（同一个时机里先按「⏱ 时机」更新代码，再跑插件）</span>${d.timings.map(t=>`<label class="small" style="display:block;margin-top:4px${t['插件']?'':';opacity:.6'}" title="${esc(t['插件']||t['插件总是']?t['说明']:t['插件原因'])}"><input type="checkbox" ${t['插件']?`data-pe="${esc(t.id)}" ${on.has(t.id)?'checked':''}`:t['插件总是']?'checked disabled':'disabled'}> <b>${esc(t['名称'])}</b>　<span class="mut">${esc(t['插件']?t['说明']:t['插件总是']?'总是可以：插件卡片上的「运行一次」，或终端 kb plugin run':'— '+t['插件原因'])}</span></label>`).join('')}</div>`}
    ${(p['配置项']||[]).map(field).join('')}
    <div class="row"><button class="pri" id="plOk">${mount?'挂载':'保存'}</button></div></div>`);
  $('#plOk').onclick=async()=>{const events=[...$('#dlgB').querySelectorAll('[data-pe]:checked')].map(x=>x.dataset.pe),config={};
    $('#dlgB').querySelectorAll('[data-pc]').forEach(x=>config[x.dataset.pc]=x.value);
    try{toast((await post('/api/plugin',{id:p.id,enabled:mount||p['启用'],events,config})).msg);dlg.close();render()}catch(e){toast(e.message)}};
}
async function plRunDlg(d,p){
  let T={期:[],仓库:[]};try{T=await api('/api/plugin/targets')}catch(e){toast(e.message)}
  const pi=x=>`<div class="ddi" data-pt="${esc(x['名称'])}"><b>${esc(x['名称'])}</b> ${x['状态']==='已完成'?badge('已完成'):''}<div class="small mut">${esc(x['分支']||'分支未写')} · ${esc(x['仓库']?x['仓库'].split('/').pop():'未定仓库')}</div></div>`;
  openDlg(`运行一次 · ${p['名称']}`,`<div class="form" style="min-width:min(560px,86vw)"><div class="small mut">选一期或直接输分支：知识库会切到这个分支（本地没有就从远端拉）、拉最新代码，运行后把改动提交并推送，再切回原来的分支。仓库被占用时自动排队。</div>
    <label>期 / 分支<div class="combo"><input type="text" id="prT" placeholder="期名，或分支名如 feature-xxx-stable" autocomplete="off"><button type="button" id="prDd" title="选一期" style="width:44px;font-size:12px;line-height:1">▼</button>
      <div class="dd" id="prL" hidden>${T['期'].map(pi).join('')||'<div class="small mut" style="padding:8px">没有进行中的期</div>'}</div></div></label>
    <div class="small" id="prInfo"></div>
    <label id="prRL" hidden style="display:none">在哪个仓库运行<select id="prR"><option value="">— 选基础信息里的本地工程 —</option>${T['仓库'].map(r=>`<option value="${esc(r['路径'])}" ${r.git?'':'disabled'}>${esc(r['名称'])}　${esc(r['路径'])}${r['版本库']==='svn'?'（svn）':r.git?'':'（不是 git / svn 仓库）'}</option>`).join('')}</select></label>
    <div class="small row" style="gap:6px"><input type="checkbox" id="prPush" checked style="width:auto;margin:0"><span>有改动就提交并推送</span></div>
    <div class="row"><button class="pri" id="prGo">运行</button></div>
    <div class="act small" id="prSteps" style="white-space:pre-wrap"></div></div>`);
  const L=$('#prL'),inp=$('#prT');
  $('#prDd').onclick=e=>{e.preventDefault();L.hidden=!L.hidden};
  L.querySelectorAll('[data-pt]').forEach(x=>x.onclick=()=>{inp.value=x.dataset.pt;L.hidden=true;check()});
  const check=()=>{const v=inp.value.trim(),hit=T['期'].find(x=>x['名称']===v)||T['期'].find(x=>x['分支']&&x['分支']===v);
    const needRepo=!!v&&(!hit||!hit['仓库']);$('#prRL').hidden=!needRepo;$('#prRL').style.display=needRepo?'':'none';
    $('#prInfo').innerHTML=!v?'':hit?`→ 「${esc(hit['名称'])}」　分支 <b class="mono">${esc(hit['分支']||'（用仓库当前分支）')}</b>　${hit['仓库']?'仓库 '+esc(hit['仓库']):'<span style="color:var(--s4)">这一期还没有仓库，选一个</span>'}`
      :`<span class="mut">不是已有的期，当作新分支 <b class="mono">${esc(v)}</b>：选在哪个仓库运行</span>`};
  inp.oninput=check;
  $('#prGo').onclick=async()=>{const b=$('#prGo');b.disabled=true;b.textContent='运行中…';const st=$('#prSteps');
    try{const r=await post('/api/plugin/run2',{id:p.id,target:inp.value.trim(),repo:$('#prRL').hidden?'':$('#prR').value,push:$('#prPush').checked});
      const poll=async()=>{let j;try{j=await api('/api/plugin/job?id='+r.job)}catch(e){st.textContent=e.message;return}
        if(!dlg.open)return;
        st.textContent=j['步骤'].map(x=>x[0]+'  '+x[1]).join('\n');b.textContent=j['状态']==='排队中'?'排队中…':'运行中…';
        if(j['状态']==='完成'||j['状态']==='失败'){b.disabled=false;b.textContent='再运行一次';toast(j['结果'])}else setTimeout(poll,1500)};
      poll()}
    catch(e){toast(e.message);b.disabled=false;b.textContent='运行'}};
}
let setPrevTpl=null,setAdv=false;try{setAdv=!!localStorage.getItem('kb.setAdv')}catch(_){}
async function settingsPage(){
  await uiTemplate();
  const d=await api('/api/settings');
  const tName=id=>(d.timings.find(t=>t.id===id)||{})['名称']||id;
  const card=f=>`<div class="bcard"><div class="row" style="justify-content:space-between"><b>${esc(f['名称'])}</b>
      <span>${f['默认']?'<span class="badge st-开发中">默认</span> ':''}<span class="chip">${f['内置']?'内置'+(f['已修改']?' · 已修改':''):'自定义'}</span></span></div>
    <div class="small mut" style="margin:4px 0">${esc(f['说明']||'')}</div>
    <div class="small">识别文件：${f['识别文件'].length?f['识别文件'].map(x=>'<code>'+esc(x)+'</code>').join(' '):'不限（任何 git 仓库）'}</div>
    <div class="small">文档目录：${f['文档目录方式']==='命令'?'运行 <code>'+esc(f['文档目录命令'])+'</code>':'<code>'+esc(f['文档目录模板'])+'</code>'} · 准备方式 ${esc(f['准备方式'])} · 进度 ${esc(f['进度类型'])}</div>
    <div class="row" style="margin-top:8px"><button data-ed="${esc(f.id)}">编辑</button><button data-cp="${esc(f.id)}">复制为新流程</button>
      ${f['默认']?'':`<button data-df="${esc(f.id)}">设为默认</button>`}<button data-ex="${esc(f.id)}">导出</button>
      ${f['内置']?(f['已修改']?`<button data-rm="${esc(f.id)}">恢复默认</button>`:''):`<button class="danger" data-rm="${esc(f.id)}">删除</button>`}</div></div>`;
  const pcard=p=>`<div class="bcard"><div class="row" style="justify-content:space-between"><b>${esc(p['名称'])}</b>
      <span>${p['错误']?'<span class="badge st-已暂停">有错误</span>':p['启用']?'<span class="badge st-开发中">已挂载</span>':'<span class="chip">未挂载</span>'}${p['外部']?' <span class="chip">外部目录</span>':''}</span></div>
    <div class="small mut" style="margin:4px 0">${esc(p['错误']||p['说明']||'')}</div>
    ${p['错误']?'':`<div class="small">${p['工具']?'按需调用'+(p['参数']?'：'+esc(p['参数']):''):'时机：'+(p['启用']?p['时机'].map(tName).map(esc).join('、'):'（挂载后生效）'+(p['时机'].length?' 默认 '+p['时机'].map(tName).map(esc).join('、'):''))}
      ${(p['识别文件']||[]).length?' · 仓库里有 '+p['识别文件'].map(x=>'<code>'+esc(x)+'</code>').join(' ')+' 才运行':''}</div>
      ${(p['配置项']||[]).map(c=>{const v=p['配置'][c['键']]||'',b=c['类型']==='base'&&(d.baseEntries||[]).find(x=>x['编号']===v);
        return `<div class="small">${esc(c['标题'])}：<b>${esc(b?v+' '+b['名称']:v||'（空）')}</b></div>`}).join('')}
      <div class="small mut">${esc(p['版本']?'版本 '+p['版本']+' · ':'')}<span class="mono">${esc(p['目录'].replace(/^\/Users\/[^/]+/,'~'))}</span></div>`}
    <div class="row" style="margin-top:8px">${p['错误']?'':`<button class="${p['启用']?'':'pri'}" data-pmount="${esc(p.id)}">${p['启用']?'卸下':'挂载'}</button>
      <button data-pset="${esc(p.id)}">${p['工具']?'配置':'时机与配置'}</button>${p['工具']&&p['入口']&&p['启用']?`<a class="btn" href="#/tool/${enc(p.id)}"><button>${esc((p['入口']['图标']||'🔌')+' 打开入口')}</button></a>`:''}${p['工具']?'':`<button data-prun="${esc(p.id)}" title="选一期，用它的开发仓库运行一次">运行一次</button>`}`}
      ${p['外部']?`<button class="danger" data-prm="${esc(p.id)}">移除</button>`:''}</div></div>`;
  const st=e=>e['状态'],ecard=e=>`<div class="bcard"><div class="row" style="justify-content:space-between"><b>${esc(e['名称'])}</b>
      <span>${e['默认']?'<span class="badge st-开发中">默认</span> ':''}<span class="chip">${e['内置']?'内置'+(e['已修改']?' · 已修改':''):'自定义'}</span></span></div>
    <div class="small mut" style="margin:4px 0">${esc(e['说明']||'')}</div>
    <div class="small">${st(e)['已安装']?'✅ 已安装 '+esc(st(e)['版本']):'⚪ 未安装（找不到命令 <code>'+esc(e['命令'])+'</code>）'}
      · ${st(e)['已接入']===true?'✅ 已接入知识库':st(e)['已接入']===false?'⚠ 未接入知识库':'知识库接入情况未知'}</div>
    ${(h=>`<div class="small" style="margin-top:2px">能不能用：<b style="color:${ENG_C[h['状态']]||ENG_C['未检测']}">● ${esc(h['状态']||'未检测')}</b>${h['原因']?` <span style="color:var(--s4)">${esc(h['原因'])}</span>`:''}${h['时间']?` <span class="mut">（${esc(h['时间'])} · ${esc(h['来源']||'')}）</span>`:''}</div>`)(e['健康']||{})}
    <div class="row" style="margin-top:8px"><button data-eed="${esc(e.id)}">编辑</button><button data-ecp="${esc(e.id)}">复制为新引擎</button>
      ${e['默认']?'':`<button data-edf="${esc(e.id)}">设为默认</button>`}
      ${st(e)['已安装']&&st(e)['已接入']!==true?(e['接入命令']?`<button class="pri" data-eln="${esc(e.id)}">接入知识库</button>`:e['接入说明']?`<button data-ehp="${esc(e.id)}">怎么接入</button>`:''):''}
      ${st(e)['已安装']?`<button data-eprobe2="${esc(e.id)}" title="极短的自检：只回复 OK，看能不能用（登录、额度、网络）">检测</button>`:''}
      ${st(e)['已安装']?`<button data-ets="${esc(e.id)}" title="让它调用一次 kb_overview，验证能运行、能用知识库工具（约半分钟）">测试</button>`:''}
      ${e['内置']?(e['已修改']?`<button data-erm="${esc(e.id)}">恢复默认</button>`:''):`<button class="danger" data-erm="${esc(e.id)}">删除</button>`}</div></div>`;
  let sw=null;try{sw=await api('/api/steward')}catch(_){}
  let pl=null;try{pl=await api('/api/pool')}catch(_){}
  let up=null;try{up=await api('/api/update')}catch(_){}
  const sws=sw?sw['设置']:null;
  app.innerHTML=`<h1>⚙ 设置</h1>
    ${(()=>{const L=uiList.length?uiList:[{名称:'经典',说明:'',目录:'classic'}],cur=uiSaved||'经典',sel=setPrevTpl||cur,dir=(uiList.find(x=>x['名称']===sel)||{})['目录'];
      return `<h2>界面</h2><div class="bcard"><div class="small mut" style="margin-bottom:8px">同一份数据，不同的摆法；随时能换，也随时能回「经典总览」。下面是示例数据的预览，可以切换阶段看它怎么变。</div>
        <div class="row" style="gap:6px;flex-wrap:wrap">${L.map(x=>`<button class="${x.名称===sel?'pri':''}" data-uit="${esc(x.名称)}" title="${esc(x.说明)}">${esc(x.名称)}${x.名称===cur?' ✓':''}</button>`).join('')}</div>
        <div class="small" style="margin:8px 0">${esc((L.find(x=>x.名称===sel)||{}).说明||'')}</div>
        ${sel==='经典'?'<div class="small mut" style="padding:16px;border:1px dashed var(--line);border-radius:10px">经典总览就是现在的总览页：<a href="#/classic">打开看看</a>。</div>'
          :!(window.KBUI&&KBUI.has(sel))?'<div class="small mut" style="padding:16px;border:1px dashed var(--line);border-radius:10px">这套模板没有示例预览，点下面「新窗口试整站」直接看。</div>'
          :`<iframe src="/ui-preview?t=${enc(sel)}" title="界面预览" style="width:100%;height:600px;border:1px solid var(--line);border-radius:12px;background:var(--bg)"></iframe>`}
        <div class="row" style="margin-top:10px;gap:8px">${sel===cur?'<span class="small mut">正在用这套</span>':`<button class="pri" id="uiUse">用「${esc(sel)}」</button>`}${dir?`<a class="small" href="/?ui=${enc(dir)}" target="_blank">新窗口试整站（不改设置）</a>`:''}</div></div>`})()}
    ${(()=>{const de=d.engines.find(e=>e['默认'])||{},es=de['状态']||{};
      return `<h2>常用</h2><div class="bcard"><div class="row" style="gap:10px;flex-wrap:wrap;margin:2px 0">
        <b style="width:96px">AI 引擎</b><select id="smEng">${d.engines.map(e=>`<option value="${esc(e.id)}" ${e['默认']?'selected':''}>${esc(e['名称'])}</option>`).join('')}</select>
        <span class="small">${es['已安装']?'✅ 已安装'+(es['已接入']===true?' · ✅ 已接入知识库':es['已接入']===false?' · ⚠ 还没接入知识库（到高级设置里点「接入知识库」）':''):'⚪ 本机没装，选一个装了的，或装好后刷新'}</span></div>
        <div class="small mut" style="margin:0 0 8px 106px">后台干活用的编程 AI：出方案、开发、审查、Bug 分析都默认用它。想让不同的活用不同的 AI，打开下面的高级设置。</div>
        <div class="row" style="gap:10px;flex-wrap:wrap;margin:2px 0"><b style="width:96px">开发流程</b><select id="smFlow">${d.flows.map(f=>`<option value="${esc(f.id)}" ${f['默认']?'selected':''}>${esc(f['名称'])}</option>`).join('')}</select></div>
        <div class="small mut" style="margin:0 0 4px 106px">AI 开发时按什么步骤走。不知道选哪个就用「通用」；团队有自己的流程命令或技能，到高级设置里「从命令 / 技能添加」。</div>
        <div class="row" style="gap:10px;flex-wrap:wrap;margin:8px 0 2px"><b style="width:96px">自动归档</b><select id="smArc" style="width:auto">${[[7,'已完成满 7 天'],[14,'已完成满 14 天'],[30,'已完成满 30 天'],[0,'不自动（只手动归档）']].map(([n,t])=>`<option value="${n}" ${n===d.archiveDays?'selected':''}>${t}</option>`).join('')}</select></div>
        <div class="small mut" style="margin:0 0 4px 106px">做完的期和事项过一段时间不再出现在侧栏、总览和各种列表里，在「📦 归档」页能搜到、能恢复；又来了 Bug / ANR 会自动回来。</div>
        <div class="row" style="gap:10px;flex-wrap:wrap;margin:8px 0 2px"><b style="width:96px">网页终端</b><label class="row small" style="gap:4px"><input type="checkbox" id="smTerm" ${d.term?'checked':''} style="width:auto;margin:0"> 打开</label></div>
        <div class="small mut" style="margin:0 0 4px 106px">期页面「终端 → ⌨ 我的终端」：在各端仓库里直接敲命令（AI 被拦、要登录、要推送时用）。等于在网页上开放 shell，只能你本人在网页上用；部署到不放心的环境可以关掉。</div>
        <div class="row" style="gap:10px;flex-wrap:wrap;margin:8px 0 2px"><b style="width:96px">自动推送</b><label class="row small" style="gap:4px"><input type="checkbox" id="smPush" ${d.autopush!==false?'checked':''} style="width:auto;margin:0"> 打开（总开关）</label></div>
        <div class="small mut" style="margin:0 0 4px 106px">改完代码后由知识库把开发分支推到远端：单仓库开发 / 跟进 / 事项推动的会话正常结束且有新提交时、全栈某端审查通过时、Bug 修复合回后。每一期还可以在开发面板 / 全栈 / 事项顶部单独关。
          关掉总开关 = 所有期都只在本地提交（全栈验收时照旧推送）。不强推；工作区有没提交的改动、HEAD 游离、和远端冲突、没权限时不推，在那一期的记录里写原因并出管家卡片。Gerrit 仓库推 refs/for 送审；svn 不自动提交。</div></div>`})()}
    ${pl?`<h3 style="margin-top:14px">工作副本池</h3>
    <p class="small mut">全局开关：远程仓库开工时从 <span class="mono">${esc(pl['目录'])}/&lt;仓库&gt;/&lt;n&gt;</span> 里挑一份空闲副本，全占了就新拉一份，到上限排队。
      各个工程 / 远程仓库自己的东西（地址、基线分支、端、流程、代码更新、运行模块）都在 <a href="#/base">🗂 基础信息</a> 里填，副本状态也在那里看。</p>
    <div class="row small" style="gap:10px;flex-wrap:wrap;margin:8px 0">每仓库最多 <input type="number" id="plLim" min="1" value="${pl['设置']['上限']}" style="width:60px"> 份 · 闲置 <input type="number" id="plIdle" min="1" value="${pl['设置']['闲置天数']}" style="width:60px"> 天自动清理 · 新副本用
      <select id="plMode" style="width:auto">${[['worktree','git worktree（最快、最省盘）'],['reference','clone --reference'],['clone','完整克隆']].map(([v,t])=>`<option value="${v}" ${v===pl['设置']['新副本方式']?'selected':''}>${t}</option>`).join('')}</select>
      <button id="plSave">保存</button><button id="plClean" title="现在就清理：N 天没用、没人占、干净、没有未推送提交的副本（第 1 份保留）">🧹 清理闲置</button></div>`:''}
    ${(()=>{const mp=d.plugins.filter(p=>p['启用']&&!p['工具']&&!p['Bug平台']&&!p['错误']);
      return `<h2 style="margin-top:18px">⏱ 时机</h2>
    <p class="small mut">知识库在这些时候自动更新代码、跑插件：同一个时机里<b>先更新代码，再跑插件</b>。「代码更新」改的是全局，某个工程要不一样就到「🗂 基础信息 → 工程设置」里单独选（工程 &gt; 全局 &gt; 默认）。每次更新都会在那一期的记录里写一条「更新代码」：时机 · 仓库@分支 · 设置 · 实际结果 · 落后远端几个提交。</p>
    <div class="tw"><table><tr><th>时机</th><th style="width:150px">代码更新</th>${mp.map(p=>`<th style="width:90px;text-align:center" title="勾上 = 这个时机运行插件「${esc(p['名称'])}」">🔌 ${esc(p['名称'])}</th>`).join('')}</tr>
      ${d.timings.map(t=>`<tr><td><b>${esc(t['名称'])}</b><div class="small mut">${esc(t['说明'])}</div></td>
        <td>${t['锁定']?`<span title="${esc(t['原因'])}">🔒 ${esc(t['当前'])}</span>`
          :`<select data-tmc="${esc(t.id)}" title="${esc(t['原因']||'')}">${t['选项'].map(o=>`<option value="${esc(o)}" ${o===t['当前']?'selected':''}>${esc(o)}${o===t['默认']?'（默认）':''}</option>`).join('')}</select>`}</td>
        ${mp.map(p=>`<td style="text-align:center">${t['插件']?`<input type="checkbox" data-tmp="${esc(p.id)}" data-tmt="${esc(t.id)}" ${p['时机'].includes(t.id)?'checked':''}>`
          :t['插件总是']?`<span class="mut" title="手动运行总是可以：插件卡片「运行一次」或终端 kb plugin run">总是</span>`:`<span class="mut" title="${esc(t['插件原因'])}">—</span>`}</td>`).join('')}</tr>`).join('')}</table></div>
    <div class="small mut" style="margin:6px 0">任何设置都绕不过的安全保护：工作区有未提交的改动时只 fetch；仓库被别的会话（开发、修复、插件任务）占着时只 fetch；pull --rebase 冲突就撤销、保持原样；Bug 修复在独立 worktree 里做，不动主工作区。${mp.length?'':'　还没有挂载会自动运行的插件（下面「插件」里挂载）。'}</div>
    <div class="row"><button id="tmReset">恢复默认</button></div>`})()}
    ${teamCard(d.team)}
    ${sw?`<h2>🤖 管家</h2>
    <p class="mut">管家每 5 分钟巡检一次：<b>明确又能重做的事自己做</b>（下面勾选的）；<b>你点了就等于批准的</b>（验收、推动、修复、svn 提交）只提醒你，从不代点；拍板、开工在方案明确时按下面「明确的事自动拍板」由 AI 自动定（可撤回）；<b>拿不准的</b>只问你一个问题。每天早上 8 点后出一份日报。</p>
    <div class="bcard"><div class="row" style="gap:16px;flex-wrap:wrap">
      <label><input type="checkbox" id="swOn" ${sws['开启']?'checked':''}> 开启管家</label>
      <span>自主度 ${sw['自主度选项'].map(l=>`<label style="margin-right:8px"><input type="radio" name="swLv" value="${esc(l)}" ${l===sws['自主度']?'checked':''}> ${esc(l)}</label>`).join('')}</span>
      <label>每天最多自动做 <input type="number" id="swLim" min="0" max="200" value="${sws['每日上限']}" style="width:70px"> 件</label>
      <label title="更早的老 Bug 不自动分析（省 token）；你打回的照样重新分析">只自动分析最近 <input type="number" id="swNd" min="1" max="60" value="${sws['新Bug天数']}" style="width:60px"> 天新建的 Bug</label></div>
      <div class="small mut" style="margin:6px 0">保守 = 只建议、都等你点「现在就做」；标准 = 勾选的动作自动做；放手 = 同标准（以后会把更多动作放进来）。</div>
      ${sw['动作'].map(a=>`<div style="margin:4px 0"><label><input type="checkbox" data-swa="${esc(a.id)}" ${a['开启']?'checked':''}> <b>${esc(a['名称'])}</b></label> <span class="small mut">${esc(a['说明'])}</span></div>`).join('')}
      <div style="margin:8px 0 4px"><label><input type="checkbox" id="swRes" ${sws['中断续跑']!==false?'checked':''}> <b>中断后自动接着做</b></label>
        <span class="small mut">后台会话被用量限制、网络断开、引擎崩溃、机器重启打断时，到点（能读出恢复时间就按它，读不出按 5 → 15 → 60 分钟）用原会话接着做，先核对文档和 git 的实际进度；不受上面的开关和每日上限限制。</span>
        <label style="margin-left:8px">连续最多 <input type="number" id="swResMax" min="1" max="50" value="${sws['续跑上限']||5}" style="width:56px"> 次，超过就停下问你</label></div>
      <div style="margin:4px 0"><label><input type="checkbox" id="swAuto" ${sws['自动拍板']!==false?'checked':''}> <b>明确的事自动拍板</b></label>
        <span class="small mut">不管开没开免打扰，AI 自己判断要不要问你：需求清楚、有建议、不扩大范围、能回退的（如改法明确的修复、没有待确认的全栈方案），AI 直接定下来接着做，页面上显示「AI 已自动定：xx（理由）」，你可以一键改回重做；拿不准、扩大范围、影响线上或不可回退的才停下问你。验收推送、svn 提交仍由你点。</span></div>
      <div style="margin:4px 0"><label><input type="checkbox" id="swLearn" ${sws['学习']!==false?'checked':''}> <b>从我的操作里学习</b></label>
        <span class="small mut">在本机记下你亲手做的操作（回答 AI、归档、推动、Bug 审核 / 修复…），按类别统计后只出提议卡片（如「以后完成 7 天自动归档」「接着推动」「这类问题你几次里几次照建议」），你点了才生效；开始开发、拍板、Bug 审核、修复、推动永远只由你点。</span></div>
      <div class="row" style="margin-top:8px"><button class="pri" id="swSave">保存</button></div></div>
    <div class="bcard" style="margin-top:10px"><div class="row" style="justify-content:space-between;flex-wrap:nowrap;gap:8px;align-items:flex-start"><span><b>管家规矩</b> <span class="small mut">你定的长期要求，附在每个后台 AI 会话（方案、开发、审查、Bug 分析、问管家）的提示词后面。在「🤖 问管家」里说「以后……」也会记到这里。标「团队默认」的来自程序里的 团队默认.json，同事新装就有。</span></span>
        <button id="rlTeam" style="flex-shrink:0" title="把本机的规矩和上面的管家设置写进 _system/团队默认.json；提交推送后同事 git pull 即生效">⬆ 设为团队默认</button></div>
      ${(sw['规矩']||[]).map(r=>`<div class="row" style="margin:4px 0;gap:8px;align-items:flex-start;flex-wrap:nowrap"><span class="small mut mono" style="flex-shrink:0" title="规矩编号（管家回答里提到的就是它）">${esc(r['编号']||'')}</span><span style="flex:1;min-width:0;overflow-wrap:anywhere;word-break:break-word">${esc(r['内容'])}${r['来自']==='团队默认'?' <span class="chip small">团队默认</span>':''}</span><span class="small mut" style="flex-shrink:0;white-space:nowrap">${esc((r['时间']||'').slice(5))}</span><a class="small" style="flex-shrink:0;white-space:nowrap" data-rdel="${esc(r['编号'])}">删除</a></div>`).join('')||'<div class="small mut" style="margin:6px 0">还没有规矩。</div>'}
      <div class="row" style="margin-top:6px;gap:8px"><input type="text" id="rlIn" style="flex:1" placeholder="如：Bug 分析结论先写复现步骤／审查不要因为代码风格打回／接口字段一律用小驼峰"><button id="rlAdd">记下</button></div></div>
    <div class="bcard" style="margin-top:10px" id="docBox"><b>🩺 体检</b> <span class="small mut">和团队默认对比中…</span></div>`:''}
    ${up?`<h2>⬆ 更新</h2><div class="bcard">
      <div class="small">当前版本 <b class="mono">${esc(up['当前']||'未知')}</b>${up['分支']?' · 分支 '+esc(up['分支']):''}${up['检查时间']?' · 上次检查 '+esc(up['检查时间']):' · 还没检查过'}
        ${up['落后']?` · <b style="color:var(--s2)">有 ${up['落后']} 项更新（到 ${esc(up['远端'])}）</b>`:up['检查时间']&&!up['错误']?' · ✓ 已是最新':''}${up['错误']?` · <span style="color:var(--warn)">${esc(up['错误'])}</span>`:''}</div>
      ${(up['不能更新']||[]).length?`<div class="small" style="color:var(--warn);margin-top:4px">只提示不更新：${esc(up['不能更新'].join('；'))}</div>`:''}
      ${up['上次更新']&&up['上次更新']['时间']?`<div class="small mut" style="margin-top:4px">上次更新：${esc(up['上次更新']['时间'])} ${esc(up['上次更新']['结果']||'')}${up['上次更新']['说明']?' · '+esc(up['上次更新']['说明']):''}</div>`:''}
      <div class="row" style="gap:14px;flex-wrap:wrap;margin-top:8px">${up['模式选项'].map(m=>`<label><input type="radio" name="upMode" value="${esc(m)}" ${m===up['设置']['模式']?'checked':''}> ${esc({关:'关',只提示:'只提示（默认）',自动:'空闲时自动更新'}[m]||m)}</label>`).join('')}
        <label><input type="checkbox" id="upPush" ${up['设置']['推送']?'checked':''}> 发现新版本时推送通知（系统通知 + 群机器人）</label></div>
      <div class="small mut" style="margin:6px 0">管家每小时在程序目录 git fetch 一次，跟 origin/main 比较，版本号就是提交号。「⬆ 更新并重启」：git pull --ff-only → 依赖变了就 pip install → 重启网页 → 自检，失败自动退回原来的提交。
        程序文件有本地改动、分支不是 main、和远端分叉时只提示不更新；有后台 AI 会话在跑时等空闲再更新。期、基础信息、设置都不进仓库，更新不会动。${esc(up['mcp提示'])}</div>
      <div class="row" style="gap:8px"><button class="pri" id="upSave">保存</button><button id="upChk">现在检查</button>${up['落后']&&!(up['不能更新']||[]).length?`<button id="upGo">⬆ 更新并重启</button>`:''}</div></div>`:''}
    <p style="margin:18px 0 6px"><a id="advTog">${setAdv?'▾ 收起高级设置':'▸ 高级设置：引擎详情、按角色分配 AI、自定义开发流程'}</a></p>
    <div id="advBox" ${setAdv?'':'hidden'}>
    <div class="row" style="justify-content:space-between"><h2>AI 引擎</h2><button id="enNew">＋ 新建引擎</button></div>
    <p class="mut">后台干活用哪个编程 AI：开始开发、模糊添加、来源更新、生成流程。开发面板可以给每一期单独选，其余用默认引擎。
    引擎要<b>能无人值守运行</b>并<b>接入知识库</b>（用 kb_ask 等工具在网页上找你确认）。权限相关的启动参数决定 AI 能在仓库里做什么，内置预设只用常规权限模式，请按团队安全要求调整。</p>
    <div class="bgrid">${d.engines.map(ecard).join('')}</div>
    <h3 style="margin-top:16px">按角色分配</h3>
    <p class="mut small">你只下发任务、拍板，后面的活交给不同的 AI：每类工作选用哪个引擎（留空 = 跟随上一级：按端 → 这一类 → 这一期选的 → 默认）。审查建议和开发用不同的引擎。</p>
    ${(()=>{const R=d.roles,opt=v=>`<option value="">— 跟随 —</option>${d.engines.map(e=>`<option value="${esc(e.id)}" ${e.id===v?'selected':''}>${esc(e['名称'])}${e['默认']?'（默认）':''}</option>`).join('')}`;
      return `<div class="tw"><table><tr><th style="width:110px">角色</th><th>做什么</th><th style="width:220px">用哪个引擎</th></tr>
        ${R['角色'].map(r=>`<tr><td><b>${esc(r['角色'])}</b></td><td class="small mut">${esc(r['说明'])}</td><td><select data-role="${esc(r['角色'])}">${opt(r['引擎'])}</select></td></tr>`).join('')}</table></div>
      <details style="margin-top:6px"><summary class="small">按端单独指定（如 iOS 开发用 Codex）</summary><div class="tw"><table><tr><th>端</th><th>开发</th><th>审查</th></tr>
        ${['服务端','Web','iOS','Android','鸿蒙'].map(e=>`<tr><td><b>${e}</b></td><td><select data-role="开发·${e}">${opt(R['按端']['开发·'+e])}</select></td><td><select data-role="审查·${e}">${opt(R['按端']['审查·'+e])}</select></td></tr>`).join('')}</table></div></details>`})()}
    <div class="row" style="justify-content:space-between;margin-top:18px"><h2>开发流程</h2><div class="row"><button id="flImp">导入</button><button id="flNew">＋ 新建空白流程</button><button class="pri" id="flCmd">＋ 从命令 / 技能添加</button></div></div>
    <p class="mut">点「开始开发」后，后台 AI 按开发流程干活：怎么认代码仓库、文档目录放哪、按什么步骤推进、什么时候要你确认、进度怎么看。
    默认是「通用模板」；已有自己的流程命令（如 <code>/my-flow</code>）的，点<b>「从命令 / 技能添加」</b>选中它即可自动生成，不用写脚本；
    也可以复制「通用模板」手动改，用「导出 / 导入」在同事之间共享。
    每一期可以在开发面板单独选流程；不选就按工程的默认流程（「🗂 基础信息 → 工程设置」里指定），再按仓库里的识别文件自动匹配，都没有用默认流程。详见 <a href="#/d/使用说明">使用说明</a>。</p>
    <div class="bgrid">${d.flows.map(card).join('')}</div></div>
    <div class="row" style="justify-content:space-between;margin-top:18px"><h2>插件</h2><button id="plAdd">＋ 添加插件目录</button></div>
    <p class="mut">插件不属于知识库本身（如公司定制的多语言拉取），挂载后在选定的时机自动运行：能挂的时机有 ${d.timings.filter(t=>t['插件']).map(t=>`<b>${esc(t['名称'])}</b>`).join('、')}，在上面「⏱ 时机」表里勾选，或点插件的「时机与配置」。
    把插件文件夹（含 <code>plugin.json</code>）放进 <code>${esc(d.pluginDir.replace(/^\/Users\/[^/]+/,'~'))}</code>，或添加其他目录；运行失败会记成「需要你处理的问题」，不会拦住开发。详见 <a href="#/d/使用说明">使用说明</a>。</p>
    <div class="bgrid">${d.plugins.map(pcard).join('')||'<div class="mut">还没有插件</div>'}</div>`;
  const byId=id=>d.flows.find(f=>f.id===id),eById=id=>d.engines.find(e=>e.id===id),pById=id=>d.plugins.find(p=>p.id===id);
  if($('#rtSave'))$('#rtSave').onclick=async()=>{
    try{toast((await post('/api/team',{on:$('#rtOn').checked,tier:(app.querySelector('[name=rtTier]:checked')||{}).value})).msg)}catch(e){return toast(e.message)}
    settingsPage()};
  const rtSuite=async(b,btn)=>{if(btn){btn.disabled=true;btn.textContent='处理中…'}
    try{toast((await post('/api/team/suite',b)).msg.split('\n')[0])}catch(e){toast(e.message)}settingsPage()};
  if($('#rtInstall'))$('#rtInstall').onclick=e=>rtSuite({action:'install',home:($('#rtHome')||{}).value||''},e.target);
  const rtRm=async(b,btn)=>{btn.disabled=true;btn.textContent='处理中…';let m;
    try{m=(await post('/api/team/suite',b)).msg}catch(e){toast(e.message);return settingsPage()}
    await kbAsk(m,{title:'移除结果',ok:'知道了',cancel:'关闭',danger:false});settingsPage()};   // 已删 / 仍残留 / 在跑会话提示，整段给用户看
  if($('#rtRemove'))$('#rtRemove').onclick=async e=>{const pj=(e.target.dataset.pj||'').split('\n').filter(Boolean);
    if(await kbConfirm('移除角色套件？会清掉：套件装进 ~/.claude 的文件（按安装清单：agents、skills、role-agents）和套件加的 hook、套件留下的 .bak 备份、自动学习定时任务'
      +(pj.length?`，以及 ${pj.length} 个项目级安装（${pj.join('、')}）`:'')+'；你自己的 agent / skill 不动。删完逐项自检，全部先备份，可以一键恢复。角色团会关掉。'))rtRm({action:'remove'},e.target)};
  if($('#rtClean'))$('#rtClean').onclick=e=>rtRm({action:'remove'},e.target);
  if($('#rtRestore'))$('#rtRestore').onclick=e=>rtSuite({action:'restore',backup:e.target.dataset.b||''},e.target);
  const rl=async b=>{try{toast((await post('/api/chat/rule',b)).msg.split('\n')[0])}catch(e){toast(e.message)}settingsPage()};
  if($('#rlTeam'))$('#rlTeam').onclick=async()=>{if(!await kbConfirm('把本机的管家规矩和管家设置（自主度、每日上限等）设为团队默认？会写进程序目录的 _system/团队默认.json，提交推送后同事 git pull 就带过去。'))return;
    try{toast((await post('/api/doctor/team-default',{})).msg)}catch(e){toast(e.message)}settingsPage()};
  if($('#docBox'))docLoad();
  if($('#rlAdd'))$('#rlAdd').onclick=()=>{const v=$('#rlIn').value.trim();if(v)rl({text:v})};
  app.querySelectorAll('[data-rdel]').forEach(a=>a.onclick=async()=>{if(await kbConfirm('删掉这条规矩？'))rl({delete:a.dataset.rdel})});
  app.querySelectorAll('[data-uit]').forEach(b=>b.onclick=()=>{setPrevTpl=b.dataset.uit;settingsPage()});
  if($('#uiUse'))$('#uiUse').onclick=async()=>{try{await setTemplate(setPrevTpl)}catch(e){toast(e.message)}};
  if($('#upSave'))$('#upSave').onclick=async()=>{try{toast((await post('/api/update/settings',{mode:(app.querySelector('[name=upMode]:checked')||{}).value,push:$('#upPush').checked})).msg)}catch(e){toast(e.message)}settingsPage()};
  if($('#upChk'))$('#upChk').onclick=async()=>{$('#upChk').disabled=true;$('#upChk').textContent='检查中…';try{const v=await post('/api/update/check',{});toast(v['落后']?`有 ${v['落后']} 项更新`:v['错误']||'已是最新版本')}catch(e){toast(e.message)}settingsPage()};
  if($('#upGo'))$('#upGo').onclick=()=>updRun($('#upGo'));
  $('#advTog').onclick=()=>{setAdv=!setAdv;try{localStorage.setItem('kb.setAdv',setAdv?'1':'')}catch(_){}settingsPage()};
  $('#smEng').onchange=async e=>{try{toast((await post('/api/engine/default',{id:e.target.value})).msg)}catch(err){toast(err.message)}settingsPage()};
  if($('#plSave'))$('#plSave').onclick=async()=>{try{toast((await post('/api/pool/settings',{limit:$('#plLim').value,idle:$('#plIdle').value,mode:$('#plMode').value})).msg)}catch(e){toast(e.message)}settingsPage()};
  if($('#plClean'))$('#plClean').onclick=async()=>{try{toast((await post('/api/pool/cleanup',{})).msg)}catch(e){toast(e.message)}settingsPage()};
  const tmSet=async b=>{try{toast((await post('/api/timing',b)).msg)}catch(e){toast(e.message)}settingsPage()};
  app.querySelectorAll('[data-tmc]').forEach(s=>s.onchange=()=>tmSet({id:s.dataset.tmc,policy:s.value}));
  if($('#tmReset'))$('#tmReset').onclick=async()=>{if(await kbConfirm('时机的代码更新都恢复默认？（工程设置里单独选的不变，插件勾选不变）'))tmSet({reset:true})};
  app.querySelectorAll('[data-tmp]').forEach(c=>c.onchange=async()=>{const p=d.plugins.find(x=>x.id===c.dataset.tmp),ev=new Set(p['时机']);
    c.checked?ev.add(c.dataset.tmt):ev.delete(c.dataset.tmt);
    try{toast((await post('/api/plugin',{id:p.id,enabled:true,events:[...ev],config:p['配置']})).msg)}catch(e){toast(e.message)}settingsPage()});
  $('#smArc').onchange=async e=>{try{toast((await post('/api/archive/days',{days:+e.target.value})).msg)}catch(err){toast(err.message)}settingsPage()};
  $('#smPush').onchange=async e=>{try{toast((await post('/api/autopush',{on:e.target.checked})).msg)}catch(err){toast(err.message)}settingsPage()};
  $('#smTerm').onchange=async e=>{try{toast((await post('/api/term/enable',{on:e.target.checked})).msg)}catch(err){toast(err.message)}settingsPage()};
  $('#smFlow').onchange=async e=>{try{toast((await post('/api/settings/default',{id:e.target.value})).msg)}catch(err){toast(err.message)}settingsPage()};
  if($('#swSave'))$('#swSave').onclick=async()=>{const acts={};app.querySelectorAll('[data-swa]').forEach(x=>acts[x.dataset.swa]=x.checked);
    try{toast((await post('/api/steward/settings',{on:$('#swOn').checked,level:(app.querySelector('[name=swLv]:checked')||{}).value,limit:$('#swLim').value,new_days:$('#swNd').value,resume:$('#swRes').checked,learn:$('#swLearn').checked,auto:$('#swAuto').checked,resume_max:$('#swResMax').value,actions:acts})).msg)}catch(e){toast(e.message)}};
  const plSave=async(p,enabled,events,config)=>{try{toast((await post('/api/plugin',{id:p.id,enabled,events,config})).msg);render()}catch(e){toast(e.message)}};
  app.querySelectorAll('[data-pmount]').forEach(b=>b.onclick=()=>{const p=pById(b.dataset.pmount);
    if(p['启用'])return plSave(p,false,p['时机'],p['配置']);plDlg(d,p,true)});
  app.querySelectorAll('[data-pset]').forEach(b=>b.onclick=()=>plDlg(d,pById(b.dataset.pset),false));
  app.querySelectorAll('[data-prun]').forEach(b=>b.onclick=()=>plRunDlg(d,pById(b.dataset.prun)));
  app.querySelectorAll('[data-prm]').forEach(b=>b.onclick=async()=>{const p=pById(b.dataset.prm);if(!await kbConfirm(`从列表移除插件「${p['名称']}」？（不删除文件）`))return;
    try{toast((await post('/api/plugin/remove',{id:p.id})).msg);render()}catch(e){toast(e.message)}});
  $('#plAdd').onclick=()=>{openDlg('添加插件目录',`<div class="form"><label>插件文件夹的绝对路径（里面要有 plugin.json）<input type="text" id="plP" placeholder="/Users/…/my-plugin"></label>
      <div class="row"><button class="pri" id="plGo">添加</button></div></div>`);
    $('#plGo').onclick=async()=>{try{toast((await post('/api/plugin/dir',{path:$('#plP').value.trim()})).msg);dlg.close();render()}catch(e){toast(e.message)}}};
  const strip=o=>{['id','内置','已修改','默认','状态','接入说明'].forEach(k=>delete o[k]);return o};
  app.querySelectorAll('[data-role]').forEach(sel=>sel.onchange=async()=>{try{toast((await post('/api/engine/role',{role:sel.dataset.role,engine:sel.value})).msg)}catch(e){toast(e.message)}});
  app.querySelectorAll('[data-eed]').forEach(b=>b.onclick=()=>engineDlg(d,eById(b.dataset.eed)));
  app.querySelectorAll('[data-ecp]').forEach(b=>b.onclick=()=>{const e=strip({...eById(b.dataset.ecp)});e['名称']+='（副本）';engineDlg(d,e)});
  $('#enNew').onclick=()=>{const e=strip({...eById('claude')});e['名称']='';e['说明']='';e['命令']='';e['接入命令']='';engineDlg(d,e)};
  app.querySelectorAll('[data-edf]').forEach(b=>b.onclick=async()=>{try{toast((await post('/api/engine/default',{id:b.dataset.edf})).msg);render()}catch(e){toast(e.message)}});
  app.querySelectorAll('[data-eln]').forEach(b=>b.onclick=async()=>{b.disabled=true;try{toast((await post('/api/engine/link',{id:b.dataset.eln})).msg);render()}catch(e){toast(e.message);b.disabled=false}});
  app.querySelectorAll('[data-ehp]').forEach(b=>b.onclick=()=>{const e=eById(b.dataset.ehp);openDlg(`把知识库接入 ${e['名称']}`,`<pre style="white-space:pre-wrap">${esc(e['接入说明'])}</pre><button id="ehC">复制</button>`);$('#ehC').onclick=()=>{navigator.clipboard.writeText(e['接入说明']);toast('已复制')}});
  app.querySelectorAll('[data-eprobe2]').forEach(b=>b.onclick=async()=>{b.disabled=true;b.textContent='检测中…';
    try{const r=await post('/api/engine/probe',{id:b.dataset.eprobe2});toast(`${r['名称']}：${r['状态']}${r['原因']?' — '+r['原因']:''}（${r['耗时']} 秒）`);engLoad();render()}catch(e){toast(e.message);b.disabled=false;b.textContent='检测'}});
  app.querySelectorAll('[data-ets]').forEach(b=>b.onclick=async()=>{const e=eById(b.dataset.ets);b.disabled=true;b.textContent='测试中…';
    try{const r=await post('/api/engine/test',{id:e.id});openDlg(`测试 ${e['名称']}：${r['成功']?'✅ 成功':'❌ 没通过'}`,`<p class="small mut">退出码 ${r['退出码']} · 用时 ${r['耗时']} 秒</p><pre style="white-space:pre-wrap">${esc(r['结果']||'（没有输出）')}</pre>${r['成功']?'':'<p class="small">没通过时：确认已安装、已登录，「接入知识库」后再试；仍不行就检查启动参数（权限模式是否允许调用知识库工具）。</p>'}`)}
    catch(e){toast(e.message)}b.disabled=false;b.textContent='测试'});
  app.querySelectorAll('[data-erm]').forEach(b=>b.onclick=async()=>{const e=eById(b.dataset.erm);
    if(!await kbConfirm(e['内置']?`把「${e['名称']}」恢复为默认设置？`:`删除引擎「${e['名称']}」？用它的期会改回默认引擎。`))return;
    try{toast((await post('/api/engine/delete',{id:e.id})).msg);render()}catch(x){toast(x.message)}});
  app.querySelectorAll('[data-ed]').forEach(b=>b.onclick=()=>flowDlg(d,byId(b.dataset.ed)));
  app.querySelectorAll('[data-cp]').forEach(b=>b.onclick=()=>{const f={...byId(b.dataset.cp)};['id','内置','已修改','默认'].forEach(k=>delete f[k]);f['名称']+='（副本）';flowDlg(d,f)});
  app.querySelectorAll('[data-df]').forEach(b=>b.onclick=async()=>{try{toast((await post('/api/settings/default',{id:b.dataset.df})).msg);render()}catch(e){toast(e.message)}});
  app.querySelectorAll('[data-rm]').forEach(b=>b.onclick=async()=>{const f=byId(b.dataset.rm);
    if(!await kbConfirm(f['内置']?`把「${f['名称']}」恢复为内置的默认设置？你的修改会丢失。`:`删除流程「${f['名称']}」？用它的期会改回「自动」。`))return;
    try{toast((await post('/api/settings/flow/delete',{id:f.id})).msg);render()}catch(e){toast(e.message)}});
  app.querySelectorAll('[data-ex]').forEach(b=>b.onclick=()=>{const f={...byId(b.dataset.ex)};['id','内置','已修改','默认'].forEach(k=>delete f[k]);
    const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(f,null,2)],{type:'application/json'}));a.download=`开发流程-${f['名称']}.json`;a.click()});
  $('#flNew').onclick=()=>{const f={...d.flows.find(x=>x.id==='generic')};['id','内置','已修改','默认'].forEach(k=>delete f[k]);f['名称']='';f['说明']='';flowDlg(d,f)};
  $('#flCmd').onclick=()=>cmdDlg(d);
  $('#flImp').onclick=()=>{openDlg('导入开发流程',`<div style="display:grid;gap:8px"><input type="file" id="fiF" accept=".json,application/json"><textarea id="fiT" rows="10" placeholder="或粘贴导出的 JSON"></textarea><button class="pri" id="fiGo">导入为新流程</button></div>`);
    $('#fiF').onchange=async e=>{const x=e.target.files[0];if(x)$('#fiT').value=await x.text()};
    $('#fiGo').onclick=async()=>{let f;try{f=JSON.parse($('#fiT').value)}catch(_){return toast('不是有效的 JSON')}
      delete f.id;try{const r=await post('/api/settings/flow',{flow:f});dlg.close();toast(r.msg);render()}catch(e){toast(e.message)}}};
}
function teamCard(t){   // 「⚙ 设置」角色团卡片：安装 / 移除 / 恢复、开关、档位、全部 agent（只协助、不设卡：没有每期次数上限）
  if(!t)return'';
  const S=t['状态']||{},ok=S['套件已装'],miss=S['缺']||[],df=S['场景表差异']||[],bk=(S['备份']||[]).filter(b=>!b['已恢复']),R=t['角色']||[];
  const inst=!ok?`<span style="color:var(--s4)">⚪ 未安装角色套件</span>`
    :miss.length?`<span style="color:var(--warn)">⚠ 已安装 ${S['已安装']}/${S['应有']}，缺：${esc(miss.join('、'))}</span> <span class="small mut">（缺的角色不会被派）</span>`
    :`✅ 已安装 ${S['已安装']}/${S['应有']} · 套件 ${esc(S['套件版本']||'')}`;
  const sc=xs=>xs.map(x=>x.endsWith('（主导）')?`<b>${esc(x.replace('（主导）',''))}</b>`:esc(x)).join('、');
  const rows=R.map(r=>`<tr><td><b>${esc(r['中文名'])}</b><div class="small mut mono">${esc(r['名称'])}</div></td>
    <td class="small">${esc(r['负责'])}${r['说明']?`<div class="mut">${esc(r['说明'])}</div>`:''}</td>
    <td style="text-align:center">${r['已安装']?'✅':'<span class="mut">—</span>'}</td>
    <td class="small">${r['场景'].length?sc(r['场景']):'<span class="mut">本档不派</span>'}${r['其他档场景'].length?`<div class="mut">${esc(r['其他档'])}档还会派：${sc(r['其他档场景'])}</div>`:''}</td></tr>`).join('');
  return `<h2>🧑‍🤝‍🧑 角色团</h2><div class="bcard" id="rtCard">
    <div class="small mut" style="margin-bottom:6px">角色套件（worker-agent）只协助、不设卡：后台会话（问管家、完善需求、出方案、Bug 分析 / 修复、开发确认点、审查、跟进、发布清单）按场景用 Agent 请专家子 agent 出意见，流程命令照旧，产物照样交回知识库，拍板还是你在网页上点。只对 Claude 引擎生效；每一期可以在开发面板单独关。默认不装、默认关。</div>
    <div class="small">${inst}${ok?` · ${S['kb接入']?'✅ kb MCP 已接入':'⚠ kb MCP 未接入（子 agent 查不了知识库）'} · 场景表：${esc(S['场景表']||'')}${df.length?` · <span style="color:var(--warn)" title="${esc(df.join('、'))}">⚠ 套件场景表与内置兜底不同（以套件为准）：${esc(df.slice(0,6).join('、'))}${df.length>6?'…':''}</span>`:''}`:''}</div>
    <div class="row" style="gap:8px;flex-wrap:wrap;margin-top:6px;align-items:center">
      <label class="small">套件目录 <input type="text" class="mono" id="rtHome" value="${esc(S['套件目录']||'')}" placeholder="worker-agent 仓库目录" style="width:280px"></label>
      <button id="rtInstall" title="运行 &lt;套件目录&gt;/scripts/install_helper.py --user，装到 ~/.claude（已装时按 --update 更新）">${ok?'⬆ 更新':'📦 安装'}</button>
      ${ok?`<button id="rtRemove" data-pj="${esc((S['项目级安装']||[]).join('\n'))}" title="按安装清单删套件的文件和 hook，连同 .bak、自动学习定时任务、项目级安装；删前备份，删完自检">🗑 移除</button>`:''}
      ${!ok&&(S['残留']||[]).length?`<button id="rtClean" title="${esc(S['残留'].join('\n'))}">🧹 清理残留（${S['残留'].length} 项）</button>`:''}
      ${bk.length?`<button id="rtRestore" data-b="${esc(bk[0]['名称'])}" title="把移除前的备份放回 ~/.claude">↩ 恢复（${esc(bk[0]['名称'])}，${bk[0]['文件数']} 个文件）</button>`:''}
      ${S['套件目录可用']||ok?'':'<span class="small" style="color:var(--warn)">这个目录下没有 scripts/install_helper.py</span>'}</div>
    ${ok&&(S['项目级安装']||[]).length?`<div class="small mut" style="margin-top:4px">项目级安装（移除时一并清掉）：${esc(S['项目级安装'].join('、'))}</div>`:''}
    ${!ok&&(S['残留']||[]).length?`<div class="small" style="color:var(--warn);margin-top:4px">⚠ 套件已移除，但还有残留：${esc(S['残留'].join('；'))}</div>`:''}
    ${ok&&!S['默认引擎支持']?'<div class="small" style="color:var(--warn);margin-top:4px">当前默认引擎不支持角色团，只在 Claude 引擎的会话生效</div>':''}
    <div class="row" style="gap:16px;flex-wrap:wrap;margin-top:8px">
      <label title="${ok?'':'先安装角色套件'}"><input type="checkbox" id="rtOn" ${t['开启']?'checked':''} ${ok?'':'disabled'}> 开启</label>
      <span>档位 ${['精简','标准'].map(x=>`<label style="margin-right:8px" title="${x==='精简'?'每个场景只派少数核心角色（设计由产品经理兼、发布由架构师兼），省钱':'按场景表派全部相关专家'}"><input type="radio" name="rtTier" value="${x}" ${t['档位']===x?'checked':''}> ${x}</label>`).join('')}</span>
      <button class="pri" id="rtSave">保存</button></div>
    <div class="small mut" style="margin-top:4px">不限每期次数；单次会话里每个场景最多派几次（避免反复派同一个专家）。管家自动发起的会话派单用 sonnet。</div>
    <h3 style="margin-top:10px">套件里的全部 agent（${R.length} 个）</h3>
    <div class="tw"><table><tr><th style="width:150px">agent</th><th>负责什么</th><th style="width:60px;text-align:center">已安装</th><th style="width:260px" title="按当前档位的场景表；加粗 = 这个场景的主导">会在哪些场景被派（${esc(t['档位'])}档）</th></tr>${rows}</table></div></div>`;
}
function engineDlg(d,e){
  const fld=([k,lab,typ,help])=>{const v=e[k]??'';let inp;
    if(typ==='textarea')inp=`<textarea data-k="${k}" rows="3">${esc(v)}</textarea>`;
    else if(typ==='lines')inp=`<textarea data-k="${k}" rows="6" style="font-family:monospace">${esc((v||[]).join('\n'))}</textarea>`;
    else if(typ.startsWith('select:'))inp=`<select data-k="${k}">${typ.slice(7).split('|').map(o=>`<option ${o===v?'selected':''}>${esc(o)}</option>`).join('')}</select>`;
    else inp=`<input type="text" data-k="${k}" value="${esc(v)}" style="width:100%">`;
    return `<label style="display:block"><b class="small">${esc(lab)}</b>${inp}<div class="small mut">${esc(help)}</div></label>`};
  openDlg(e.id?`编辑 AI 引擎：${e['名称']}`:'新建 AI 引擎',`<div style="display:grid;gap:10px">
    ${e['内置']?'<div class="notice small" style="display:block">这是内置引擎：保存后存为你的修改，随时可在设置页「恢复默认」。</div>':''}
    ${d.engineFields.map(fld).join('')}
    <div class="small mut">参数里可用变量：${Object.entries(d.engineVars).map(([k,v])=>`<code>{${esc(k)}}</code> ${esc(v)}`).join('；')}。参数逐个传给命令，不经过 shell。</div>
    <div class="row"><button class="pri" id="enGo">保存</button></div></div>`);
  $('#enGo').onclick=async()=>{const o={id:e.id||''};$('#dlgB').querySelectorAll('[data-k]').forEach(x=>o[x.dataset.k]=x.value);
    try{const r=await post('/api/engine',{engine:o});dlg.close();toast(r.msg);render()}catch(x){toast(x.message)}};
}
async function cmdDlg(d){
  openDlg('从命令 / 技能添加开发流程','<span class="mut small">正在查找本机的技能和命令…</span>');
  let cs;try{cs=(await api('/api/settings/commands')).items}catch(e){return $('#dlgB').textContent=e.message}
  const row=(c,i)=>`<label class="row cmdrow" data-t="${esc((c['名称']+' '+c['说明']+' '+c['类型']).toLowerCase())}" style="align-items:flex-start;margin:2px 0"><input type="radio" name="cm" value="${i}">
    <span><b>${esc(c['名称'])}</b> <span class="chip">${esc(c['类型'])}</span><br><span class="small mut">${esc(c['说明']||'')}</span></span></label>`;
  $('#dlgB').innerHTML=`<div style="display:grid;gap:8px">
    <p class="small mut" style="margin:0">选中你平时用来开发的命令或技能（如 <code>/my-flow</code>），自动生成一套开发流程，生成后可以再改。
    个人技能 / 命令任何仓库都能用；仓库里的命令只匹配带这个命令的仓库。</p>
    <input type="text" id="cmQ" placeholder="搜索，如 plan、sdd">
    <div id="cmL" style="max-height:46vh;overflow:auto;border:1px solid var(--line);border-radius:8px;padding:6px 10px">${cs.map(row).join('')||'<span class="mut">没有找到技能或命令</span>'}</div>
    <div class="row"><button class="pri" id="cmAI" title="Claude 读说明文件，按它的实际做法填好文档目录、步骤、确认点、进度（约 1 分钟）">让 Claude 读说明生成（推荐）</button>
      <button id="cmQk" title="按固定模板填写，不调用 AI">快速生成</button><span class="mut small" id="cmS"></span></div></div>`;
  $('#cmQ').oninput=()=>{const q=$('#cmQ').value.trim().toLowerCase();$('#cmL').querySelectorAll('.cmdrow').forEach(x=>x.style.display=!q||x.dataset.t.includes(q)?'':'none')};
  $('#cmQ').focus();
  const go=async ai=>{const v=($('#cmL').querySelector('input[name=cm]:checked')||{}).value;if(v===undefined)return toast('先选一个命令或技能');
    const bs=[$('#cmAI'),$('#cmQk')];bs.forEach(b=>b.disabled=true);$('#cmS').textContent=ai?'Claude 正在读说明并生成，约 1 分钟…':'生成中…';
    try{const r=await post('/api/settings/flow/draft',{path:cs[v]['路径'],ai});flowDlg(d,r.flow)}
    catch(e){toast(e.message);bs.forEach(b=>b.disabled=false);$('#cmS').textContent=''}};
  $('#cmAI').onclick=()=>go(true);$('#cmQk').onclick=()=>go(false);
}
function flowDlg(d,f){
  const fld=([k,lab,typ,help])=>{const v=f[k]??'';let inp;
    if(typ==='textarea')inp=`<textarea data-k="${k}" rows="${k==='开发步骤'?8:3}">${esc(v)}</textarea>`;
    else if(typ==='lines')inp=`<textarea data-k="${k}" rows="2">${esc((v||[]).join('\n'))}</textarea>`;
    else if(typ.startsWith('select:'))inp=`<select data-k="${k}">${typ.slice(7).split('|').map(o=>`<option ${o===v?'selected':''}>${esc(o)}</option>`).join('')}</select>`;
    else inp=`<input type="text" data-k="${k}" value="${esc(v)}" style="width:100%">`;
    return `<label style="display:block"><b class="small">${esc(lab)}</b>${inp}<div class="small mut">${esc(help)}</div></label>`};
  openDlg(f.id?`编辑开发流程：${f['名称']}`:'新建开发流程',`<div style="display:grid;gap:10px">
    ${f['内置']?'<div class="notice small" style="display:block">这是内置流程：保存后存为你的修改，随时可在设置页「恢复默认」。</div>':''}
    ${d.fields.map(fld).join('')}
    <div class="small mut">开发步骤、确认点、续跑提示、开发约定、需求变更提示里可用变量：${Object.entries(d.vars).map(([k,v])=>`<code>{${esc(k)}}</code> ${esc(v)}`).join('；')}</div>
    <div class="row"><button class="pri" id="flGo">保存</button><span class="mut small">⌘ + 回车 保存</span></div></div>`);
  const go=async()=>{const o={id:f.id||''};$('#dlgB').querySelectorAll('[data-k]').forEach(x=>o[x.dataset.k]=x.value);
    try{const r=await post('/api/settings/flow',{flow:o});dlg.close();toast(r.msg);render()}catch(e){toast(e.message)}};
  $('#flGo').onclick=go;$('#dlgB').onkeydown=e=>{if(e.key==='Enter'&&(e.metaKey||e.ctrlKey))go()};
}

// 体检（kb doctor 同一份）：规矩、管家设置、kb 工具放行、auto 权限模式、角色套件残留、MCP 接入，和团队默认对比；不一致标红、可一键修复
async function docLoad(){
  const box=$('#docBox');if(!box)return;let r;
  try{r=await api('/api/doctor')}catch(e){box.innerHTML=`<b>🩺 体检</b> <span class="small" style="color:var(--warn)">${esc(e.message)}</span>`;return}
  const col={ok:'var(--ok, #2a8)',warn:'var(--warn)',bad:'#d33'},mk={ok:'✓',warn:'!',bad:'✗'};
  box.innerHTML=`<div class="row" style="justify-content:space-between"><span><b>🩺 体检</b> <span class="small mut">本机和团队默认（同事新装就是这样）对比；终端 <code>kb doctor</code> 是同一份。</span></span>
      ${r.items.some(x=>x['修复'])?'<button class="pri" id="docAll">一键修复全部</button>':''}</div>
    <div class="tw"><table><tr><th style="width:120px">项目</th><th>本机</th><th>团队默认</th><th>说明</th><th style="width:120px"></th></tr>
    ${r.items.map(x=>`<tr><td style="color:${col[x['状态']]};white-space:nowrap"><b>${mk[x['状态']]} ${esc(x['项目'])}</b></td><td class="small" style="color:${x['状态']==='bad'?col.bad:''};overflow-wrap:anywhere">${esc(x['本机'])}</td>
      <td class="small mut">${esc(x['团队默认'])}</td><td class="small" style="overflow-wrap:anywhere">${esc(x['说明'])}</td>
      <td>${x['修复']?`<button data-dfx="${esc(x.id)}">${esc(x['修复'])}</button>`:''}</td></tr>`).join('')}</table></div>`;
  const fix=async(id,b)=>{b.disabled=true;b.textContent='修复中…';try{toast((await post('/api/doctor/fix',{id})).msg.split('\n').join('；'))}catch(e){toast(e.message)}settingsPage()};
  box.querySelectorAll('[data-dfx]').forEach(b=>b.onclick=()=>fix(b.dataset.dfx,b));
  if($('#docAll'))$('#docAll').onclick=e=>fix('all',e.target);
}
