// ==== 构建
// ---------------- 构建：这一期在构建平台（如 Jenkins）上的产出，按分支自动对应任务
const BRES={SUCCESS:['st-已完成','成功'],FAILURE:['st-已暂停2','失败'],UNSTABLE:['st-已暂停','不稳定'],ABORTED:['','已中止'],进行中:['st-开发中','● 构建中']};
async function buildBox(name,fresh){
  const box=$('#buildBox');if(!box)return;
  if(fresh)box.innerHTML='<span class="mut small">刷新中…</span>';
  let v;try{v=await api('/api/period/builds?name='+enc(name)+(fresh?'&fresh=1':''))}catch(e){box.innerHTML=`<span class="mut small">${esc(e.message)}</span>`;return}
  if(!$('#buildBox')||curPeriod()!==name)return;
  const n=v['任务'].reduce((a,j)=>a+j['构建'].length,0),tb=document.querySelector('[data-tab="build"]');
  if(tb&&n)tb.innerHTML=`构建<span class="n">${n}</span>`;
  if(!v['插件']){app.querySelectorAll('[data-tab="build"],[data-pane="build"]').forEach(x=>x.style.display='none');   // 没挂构建插件：不显示这个页签（安装说明在「⚙ 设置 → 插件」）
    box.innerHTML=`<div class="mut small">还没有挂载构建插件。把 Jenkins 地址填进「◈ 基础信息」（字段「地址」，如 http://jenkins.example.com/jenkins），再到「⚙ 设置 → 插件 → Jenkins」挂载并选这个条目；之后每一期按分支自动对应 Jenkins 任务。</div>`;return}
  const art=(b,k)=>b['产物'].filter(a=>a['类型']===k);
  const row=b=>{const r=BRES[b['进行中']?'进行中':b['结果']]||['',b['结果']||'?'],apk=art(b,'安装包'),mp=art(b,'mapping')[0],qr=art(b,'二维码')[0];
    return `<tr><td><a href="${esc(b['地址'])}" target="_blank" rel="noopener" class="mono">#${b['编号']}</a></td><td><span class="badge ${r[0]}">${esc(r[1])}</span></td>
      <td class="mono small">${esc(b['版本']||'')}</td><td class="small">${esc(b['时间'])}<div class="mut">${b['耗时']} 分钟</div></td>
      <td class="mono small" title="${esc(b['提交'])}">${esc((b['提交']||'').slice(0,10))}${b['分支']&&!v['分支'].includes(b['分支'])?`<div class="mut">${esc(b['分支'])}</div>`:''}</td>
      <td class="small">${v['安装']&&b['结果']==='SUCCESS'&&!b['进行中']&&apk.some(a=>/\.apk$/i.test(a['路径'])&&!/unsigned/i.test(a['路径']))?`<button class="small" data-inst="${esc(b['地址'])}" title="下载这次构建的 apk，用 adb 装到手机（默认渠道按：本期上次选的 → 插件配置的「默认渠道匹配」→ 路径最短）">📲 安装到手机</button> `:''}${apk.length?`<details><summary>安装包 ${apk.length} 个</summary>${apk.map(a=>`<div><a href="${esc(a['地址'])}">${esc(a['名称'])}</a></div>`).join('')}</details>`:''}
        ${qr?`<a data-qr="${esc(qr['地址'])}">二维码</a>`:''}
        ${mp?` · <a href="${esc(mp['地址'])}">mapping</a> <a data-bmap="${esc(mp['地址'])}" data-ver="${esc(b['版本'])}" title="归档到 ANR / 崩溃，这个版本的崩溃日志自动还原">归档</a>`:''}</td>
      <td class="small">${(b['Bug']||[]).length?`<a href="#/bugs" title="Bug 平台上记的提测包是这一次构建">🐞 ${b['Bug'].map(esc).join('、')}</a>`:''}</td></tr>`};
  box.innerHTML=`<div class="row small" style="gap:10px;flex-wrap:wrap;margin-bottom:8px"><span class="mut">分支 ${v['分支'].map(x=>(v['历史分支']||[]).includes(x)?`<span class="mono" title="换下来的老分支">${esc(x)}（历史）</span>`:`<b class="mono">${esc(x)}</b>`).join('、')||'（没写，在资料里加一条「分支」文字资料）'}</span>
      <span class="sp" style="flex:1"></span><button class="small" id="bdEdit">改对应任务</button><button class="small" id="bdFresh">↻ 刷新</button></div>
    ${v['任务'].length?v['任务'].map(j=>`<div style="margin-bottom:12px"><div class="small"><b>${esc(v['插件'])}</b> <a href="${esc(j['地址'])}" target="_blank" rel="noopener" class="mono">${esc(j['名称'])}</a> <span class="mut">（${j['依据']==='手动'?'手动指定':'按分支自动匹配'}）</span>${typeof j['分支']==='string'&&j['分支']?` <span class="mono mut">🌿 ${esc(j['分支'])}</span>`:''}${j['历史']?' <span class="badge" title="这一期已经换到新分支，老分支的构建照旧能查、能取 mapping">历史</span>':''}</div>
      ${j['错误']?`<div class="small" style="color:var(--warn)">${esc(j['错误'])}</div>`:j['构建'].length?`<div class="tw"><table><tr><th style="width:60px">构建</th><th style="width:80px">结果</th><th style="width:110px">版本</th><th style="width:130px">时间</th><th style="width:110px">提交</th><th>产物</th><th style="width:140px">这个包上的 Bug</th></tr>${j['构建'].map(row).join('')}</table></div>`:'<div class="mut small">还没有构建</div>'}</div>`).join('')
      :`<div class="mut small">没匹配到构建任务（任务名里要含分支名）。点「改对应任务」填任务名或地址。</div>`}`;
  $('#bdFresh').onclick=()=>buildBox(name,true);
  $('#bdEdit').onclick=()=>{openDlg('这一期对应的构建任务',`<div class="form"><label>任务名或地址（可多个，换行隔开；只换当前分支的任务，老分支的保留；留空 = 新老分支一起重新自动匹配）<textarea id="bdJ" rows="3" class="mono" placeholder="如：app_android_feature-x&#10;或 http://jenkins…/job/app_android_feature-x/">${esc(v['任务'].map(j=>j['地址']||j['名称']).join('\n'))}</textarea></label>
      <div class="row"><button class="pri" id="bdGo">保存</button></div></div>`);
    $('#bdGo').onclick=async()=>{try{const r=await post('/api/period/builds',{period:name,jobs:$('#bdJ').value});toast(r.msg);dlg.close();buildBox(name,true)}catch(e){toast(e.message)}}};
  box.querySelectorAll('[data-inst]').forEach(a=>a.onclick=()=>installDlg(name,a.dataset.inst));
  box.querySelectorAll('[data-qr]').forEach(a=>a.onclick=()=>openDlg('扫码安装',`<img src="${esc(a.dataset.qr)}" style="max-width:280px;display:block;margin:auto">`));
  box.querySelectorAll('[data-bmap]').forEach(a=>a.onclick=async()=>{if(!a.dataset.ver)return toast('这次构建认不出版本号，到 ANR / 崩溃页「＋ 归档 mapping」手动填');
    if(!await kbConfirm(`把这次构建的 mapping 归档为版本 ${a.dataset.ver}？`))return;
    a.textContent='归档中…';try{const r=await post('/api/build/mapping',{url:a.dataset.bmap,version:a.dataset.ver});openDlg('已归档 mapping',`<pre style="white-space:pre-wrap">${esc(r.msg)}</pre>`)}catch(e){toast(e.message)}finally{a.textContent='归档'}});
}
// ---------------- 安装到手机：构建行「📲 安装到手机」、开发面板手机卡片「安装最新构建」共用（只做 Android .apk）
async function installDlg(name,build){
  openDlg('安装到手机','<span class="mut small">读取渠道包和手机…</span>');
  let o;try{o=await api('/api/build/install?name='+enc(name)+'&build='+enc(build||''))}catch(e){$('#dlgB').innerHTML=`<div class="small" style="color:var(--warn)">${esc(e.message)}</div>`;return}
  const ph=o['手机'];
  $('#dlgB').innerHTML=`<div class="form"><div class="small"><b class="mono">${esc(o['任务'])}</b> #${o['构建']['编号']} · 版本 ${esc(o['构建']['版本']||'?')} · ${esc(o['构建']['时间'])}</div>
    <label>渠道包 <span class="mut small">（默认：${esc(o['默认依据'])}；改了会记到本期，下次默认选它）</span><select id="inP" class="mono">${o['包'].map(a=>`<option value="${esc(a['路径'])}" ${a['路径']===o['默认']?'selected':''}>${esc(a['路径'])}${a['已缓存']?'（已下载）':''}</option>`).join('')}</select></label>
    <div><div class="small" style="margin-bottom:4px">手机</div>${ph.length?ph.map(x=>`<label class="row small" style="gap:6px;color:${x['可装']?'var(--fg)':'var(--mut)'}"><input type="radio" name="inS" value="${esc(x['序列号'])}" data-force="${x['可装']?'':'1'}" ${x['序列号']===o['默认手机']?'checked':''} ${x['可装']||x['可强制']?'':'disabled'}>
      <b>${esc(x['型号']||'未知型号')}</b> <span class="mono">${esc(x['序列号'])}</span> · ${esc(x['说明'])}${x['可强制']?'（强制装要二次确认）':''}</label>`).join(''):'<div class="small mut">没有连着的手机</div>'}</div>
    <div id="inSt" class="small"></div>
    <div class="row"><button class="pri" id="inGo" ${ph.some(x=>x['可装']||x['可强制'])?'':'disabled'}>📲 安装</button></div></div>`;
  const go=async(extra={})=>{const r=document.querySelector('input[name="inS"]:checked'),st=$('#inSt'),b=$('#inGo');
    if(!r)return toast('选一台手机');
    let force=!!extra.force;
    if(r.dataset.force&&!force){const x=ph.find(p=>p['序列号']===r.value);
      if(!await kbConfirm(`${x['序列号']} ${x['说明']}。\n往它装包会覆盖对方的包、打断对方测试，装成功后记为本期占用。确定强制安装？`,{ok:'强制安装',danger:true}))return;force=true}
    b.disabled=true;let jid;
    try{jid=(await post('/api/build/install',{period:name,build:o['构建']['地址'],path:$('#inP').value,serial:r.value,force,uninstall:!!extra.uninstall})).job}
    catch(e){b.disabled=false;if(e.need&&e.need['需要']==='强制安装'){if(await kbConfirm(e.message+'。确定强制安装？',{ok:'强制安装',danger:true}))go({...extra,force:true});return}return toast(e.message)}
    for(;;){let j;try{j=await api('/api/build/install/job?id='+jid)}catch(e){st.textContent=e.message;b.disabled=false;return}
      if(!$('#inSt'))return;
      const mb=n=>(n/1048576).toFixed(1)+' MB';
      if(j['状态']==='下载中'){st.innerHTML=`下载中 ${j['进度']}%${j['总']?`（${mb(j['已下'])} / ${mb(j['总'])}）`:''}<div class="pbar"><span style="width:${j['进度']}%;background:var(--acc)"></span></div>`;await new Promise(f=>setTimeout(f,700));continue}
      if(j['状态']==='安装中'){st.innerHTML=`${j['缓存']?'已下载过，不再下载；':''}adb 安装中…`;await new Promise(f=>setTimeout(f,1000));continue}
      b.disabled=false;
      if(j['状态']==='成功'){st.innerHTML=`<b style="color:var(--s3)">✓ 已装到 ${esc(j['序列号'])}</b>，记为本期占用（沿用超时释放）`;toast('安装成功');if($('#devBox'))devBox(name);return}
      if(j['状态']==='签名冲突'){st.innerHTML=`<div style="color:var(--warn)">签名和手机上已装的不一致${j['包名']?'（'+esc(j['包名'])+'）':''}</div><pre class="small" style="white-space:pre-wrap">${esc(j['输出'])}</pre>`;
        if(await kbConfirm(`手机上已装的 ${j['包名']||'这个应用'} 签名不一致，要先卸载再装。\n卸载会清空这个应用在手机上的数据（登录、缓存等）。确定卸载重装？`,{ok:'卸载重装',danger:true}))go({force,uninstall:true});return}
      st.innerHTML=`<div style="color:var(--warn)">安装失败（占用状态没变）：</div><pre class="small" style="white-space:pre-wrap;max-height:240px;overflow:auto">${esc(j['输出'])}</pre>`;return}};
  $('#inGo').onclick=()=>go();
}

// ---------------- ▶ 运行到手机：用当前工作区代码编译、装到手机并拉起启动页（kblib/plugins/devrun.py）
// 入口：开发面板「📱 手机」、全栈各端行、事项「补充」的工程旁；任何带 data-devrun="期名"（可带 data-repo）的元素点了都打开这个弹框
document.addEventListener('click',e=>{const b=e.target.closest&&e.target.closest('[data-devrun]');if(!b)return;e.preventDefault();runDlg(b.dataset.devrun,b.dataset.repo||'',b.dataset.serial||'')});
const drBusy=s=>!['成功','失败','已取消'].includes(s);
async function runDlg(name,repo,serial){
  openDlg('▶ 运行到手机','<span class="mut small">读取工程和手机…</span>');
  let o;try{o=await api('/api/devrun/options?name='+enc(name)+'&repo='+enc(repo||''))}catch(e){$('#dlgB').innerHTML=`<div class="small" style="color:var(--warn)">${esc(e.message)}</div>`;return}
  if(o['在跑'])return drWatch(name,o['在跑']['编号']);
  const r=o['工程'].find(x=>x['路径']===o['当前'])||o['工程'][0],ph=o['手机'],ok=o['平台支持'].includes(r['平台']);
  const pick=(ph.find(p=>p['可装']&&p['序列号']===serial)||ph.find(p=>p['可装'])||{})['序列号'];
  const cands=r['模块候选']||[],mod=r['记住模块']||(cands[0]||{})['模块']||'',vr=r['记住变体']||'debug',last=o['上次'];
  const plat=x=>x['平台']||'认不出';
  $('#dlgB').innerHTML=`<div class="form">
    <p class="small mut" style="margin:0">用工程<b>当前工作区</b>的代码编译（不切分支、不拉代码；代码更新按「⚙ 设置 → ⏱ 时机 → 运行到手机前」，现在：${esc(r['代码更新']||'不动')}），装到手机并拉起启动页。工程被别的会话占着时自动排队。</p>
    <label>工程${o['工程'].length>1?'':' <span class="mut small">（'+esc(plat(r))+'）</span>'}${o['工程'].length>1?`<select id="drRepo">${o['工程'].map(x=>`<option value="${esc(x['路径'])}" ${x['路径']===r['路径']?'selected':''}>${esc(x['端']?x['端']+' · ':'')}${esc(x['名称'])}（${esc(plat(x))}）</option>`).join('')}</select>`:`<div class="mono small">${esc(r['路径'])}</div>`}</label>
    ${r['分支']!==undefined?`<div class="small">📍 <span class="mono">${esc(r['分支']||'?')} @${esc(r['提交']||'?')}</span>${r['未提交']?` · <span style="color:var(--warn)">${r['未提交']} 个未提交改动（会一起编进去）</span>`:''}
      ${o['期分支']&&r['分支']&&o['期分支']!==r['分支']?`<div style="color:var(--warn)">这一期的分支是 <span class="mono">${esc(o['期分支'])}</span>，工作区现在在 <span class="mono">${esc(r['分支'])}</span>：按工作区现在的代码编译（不切分支）</div>`:''}</div>`:''}
    ${!ok?`<div class="small" style="color:var(--warn)">${r['平台']?esc(r['平台'])+' 工程的「运行到手机」还没做（现在只支持 Android），后续再加。':'认不出是什么工程（没有 gradlew + settings.gradle）：现在只支持 Android gradle 工程。'}</div>`:`
    <div class="row" style="gap:8px;flex-wrap:wrap"><label style="flex:1;min-width:160px">模块 <span class="mut small">${cands.length?'（自动识别到 '+cands.length+' 个）':'（没识别到，手动填）'}</span><input type="text" id="drMod" class="mono" list="drModL" value="${esc(mod)}" placeholder=":app"><datalist id="drModL">${cands.map(c=>`<option value="${esc(c['模块'])}">${esc(c['目录'])}${c['有渠道']?' · 有渠道，变体要写全':''}</option>`).join('')}</datalist></label>
      <label style="width:180px">变体 <input type="text" id="drVar" class="mono" value="${esc(vr)}" placeholder="debug"></label></div>
    <label class="row small" style="gap:4px"><input type="checkbox" id="drRem" style="width:auto;margin:0"> 记住到这个工程（也可在「⚙ 设置 → 按工程」改）</label>
    <div><div class="small" style="margin-bottom:4px">手机 <span class="mut">（只装到能用的：本期占着的或空闲的；别的期 / 本人在用的不装）</span></div>${!o['adb']?'<div class="small" style="color:var(--warn)">本机找不到 adb：装好 Android SDK platform-tools 后再试</div>':ph.length?ph.map((x,i)=>`<label class="row small" style="gap:6px;color:${x['可装']?'var(--fg)':'var(--mut)'}"><input type="radio" name="drS" value="${esc(x['序列号'])}" ${x['序列号']===pick?'checked':''} ${x['可装']?'':'disabled'}>
      <b>${esc(x['型号']||'未知型号')}</b> <span class="mono">${esc(x['序列号'])}</span> · ${esc(x['说明'])}</label>`).join(''):'<div class="small mut">没有连着的手机</div>'}</div>
    <div class="row"><button class="pri" id="drGo" ${ph.some(x=>x['可装'])?'':'disabled'}>▶ 编译并运行</button></div>`}
    ${last?`<div class="small mut">上次（${esc(last['开始'])}）：${esc(last['状态'])} · ${esc(last['工程名'])}@${esc(last['分支']||'?')}@${esc(last['提交']||'?')} → ${esc(last['型号']||'')}（${esc(last['序列号'])}）　<a data-drlast="${esc(last['编号'])}">看日志</a></div>`:''}</div>`;
  if($('#drRepo'))$('#drRepo').onchange=e=>runDlg(name,e.target.value,serial);
  const lb=document.querySelector('[data-drlast]');if(lb)lb.onclick=()=>drWatch(name,lb.dataset.drlast);
  if(!$('#drGo'))return;
  $('#drGo').onclick=async()=>{const s=document.querySelector('input[name="drS"]:checked');if(!s)return toast('选一台手机');
    const b=$('#drGo');b.disabled=true;
    try{const j=(await post('/api/devrun',{period:name,repo:r['路径'],serial:s.value,module:$('#drMod').value.trim(),variant:$('#drVar').value.trim(),remember:$('#drRem').checked})).job;drWatch(name,j)}
    catch(e){b.disabled=false;toast(e.message)}};
}
async function drWatch(name,jid){
  $('#dlgT').textContent='▶ 运行到手机';
  $('#dlgB').innerHTML=`<div class="form"><div id="drHd" class="small"></div><div id="drSt" class="small"></div>
    <pre id="drLog" class="small mono" style="white-space:pre-wrap;max-height:320px;min-height:120px;overflow:auto;background:var(--soft);padding:6px;margin:0"></pre>
    <div class="row" id="drAct"></div></div>`;
  for(;;){if(!$('#drLog'))return;
    let j;try{j=await api('/api/devrun/job?id='+enc(jid))}catch(e){$('#drSt').textContent=e.message;return}
    if(!$('#drLog'))return;
    $('#drHd').innerHTML=`<b>${esc(j['工程名'])}</b> <span class="mono">${esc(j['分支']||'')}${j['提交']?' @'+esc(j['提交']):''}</span> · <span class="mono">${esc(j['模块'])}:install${esc(j['变体'][0].toUpperCase()+j['变体'].slice(1))}</span> → <b>${esc(j['型号']||'')}</b> <span class="mono">${esc(j['序列号'])}</span>　<span class="mut">${esc(j['开始'])} · ${esc(j['发起'])}</span>`;
    const L=$('#drLog'),atEnd=L.scrollTop+L.clientHeight>=L.scrollHeight-20;L.textContent=(j['日志']||[]).join('\n');if(atEnd)L.scrollTop=L.scrollHeight;
    const busy=drBusy(j['状态']);
    $('#drSt').innerHTML=busy?`<span class="badge st-开发中">● ${esc(j['状态'])}</span> ${esc(j['步骤']||'')}`
      :j['状态']==='成功'?`<b style="color:var(--s3)">✓ 已装好并启动</b>　${esc(j['摘要']||'')}<div class="mut">已记到这一期的记录；手机记为本期占用（沿用超时释放）</div>`
      :j['状态']==='已取消'?`<span class="mut">已取消</span>`
      :`<b style="color:var(--s4)">✗ 失败</b><pre class="small" style="white-space:pre-wrap;color:var(--s4);margin:4px 0">${esc(j['摘要']||'')}</pre>${(j['可选变体']||[]).length?`<div>选一个变体重新运行：${j['可选变体'].slice(0,12).map(v=>`<button class="small" data-drv="${esc(v)}">${esc(v)}</button>`).join(' ')}</div>`:''}`;
    $('#drAct').innerHTML=busy?'<button id="drStop" class="danger">■ 取消</button><span class="mut small">关掉弹框不影响后台编译；再点「▶ 运行到手机」能回到这里</span>':'<button id="drAgain">▶ 再运行一次</button>';
    if($('#drStop'))$('#drStop').onclick=async()=>{if(!await kbConfirm('取消这次编译 / 安装？'))return;try{toast((await post('/api/devrun/cancel',{id:jid})).msg)}catch(e){toast(e.message)}};
    if($('#drAgain'))$('#drAgain').onclick=()=>runDlg(name,j['工程']);
    $('#dlgB').querySelectorAll('[data-drv]').forEach(b=>b.onclick=async()=>{b.disabled=true;
      try{drWatch(name,(await post('/api/devrun',{period:name,repo:j['工程'],serial:j['序列号'],module:j['模块'],variant:b.dataset.drv,remember:true})).job)}catch(e){b.disabled=false;toast(e.message)}});
    if(!busy)return;
    await new Promise(f=>setTimeout(f,1200));
  }
}
