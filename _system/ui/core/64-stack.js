// ==== 全栈：各端仓库、方案、拍板、开工、验收
// ---------------- 全栈：各端仓库 → AI 出影响分析和接口契约 → 你拍板（拍板 1）
let stackRepos=null;
async function stackBox(name){
  const box=$('#stackBox');if(!box)return;
  let v;try{v=await api('/api/stack?name='+enc(name))}catch(e){box.innerHTML=`<span class="mut small">${esc(e.message)}</span>`;box._html=null;return}
  if(!$('#stackBox')||curPeriod()!==name)return;
  if(!stackRepos){try{stackRepos=(await api('/api/stack/repos')).items}catch(_){stackRepos=[]}}
  const st=v['状态'],run=v['会话']&&v['会话']['状态']==='运行中',P=v['方案'],used=new Set(v['各端'].filter(r=>r['仓库']).map(r=>r['仓库']));
  const opts=r=>{const fit=stackRepos.filter(x=>x['端'].includes(r['端'])),rest=stackRepos.filter(x=>!x['端'].includes(r['端'])&&x['路径']!==r['仓库']);
    return `${r['仓库']?'':'<option value="">— 选仓库 —</option>'}${r['仓库']?`<option value="${esc(r['仓库'])}" selected>${esc(r['显示'])}</option>`:''}
      ${fit.filter(x=>x['路径']!==r['仓库']).map(x=>`<option value="${esc(x['路径'])}">${esc(x['名称'])}　${esc(x['分支']||'')}</option>`).join('')}
      ${rest.length?`<optgroup label="其他仓库">${rest.map(x=>`<option value="${esc(x['路径'])}">${esc(x['名称'])}${x['端'].length?' ('+esc(x['端'].join('/'))+')':x['远程']?' (远程)':''}</option>`).join('')}</optgroup>`:''}
      ${PICK_OPT}<option value="__other">填写地址（本机路径或 user@主机:/路径）…</option>`};
  const have=v['各端'].filter(r=>r['仓库']).map(r=>r['端']),common=v['常用平台']||['服务端','Web','iOS','Android','鸿蒙','小程序','桌面端'];
  const FL=v['流程列表']||[],BLD=v['建工程']||{},canBuild=['已拍板','开发中','各端完成'].includes(st),NS=v['新产品会话']||{};
  const bldTag=e=>{const b=BLD[e];if(!b)return'';return b['在跑']?` <span class="badge st-开发中" title="${esc(b['目录']||'')}">● 生成脚手架中</span>`
    :b['状态']==='已完成'?` <span class="badge st-已完成" title="${esc(b['结果']||'')}">脚手架已生成</span>`
    :b['状态']==='失败'?` <span class="badge st-已暂停2" title="${esc(b['结果']||'')}">脚手架没生成</span> <button class="small" data-sretry="${esc(e)}">重试脚手架</button>`:''};
  const flowSel=r=>r['远程']?`<div class="small mut" style="margin-top:2px">流程：${esc(r['流程'])}</div>`
    :`<div class="small mut row" style="margin-top:4px;gap:4px"><span>流程</span><select data-sfl="${esc(r['端'])}" style="width:auto;max-width:220px;font-size:12px" title="这个端按哪套流程开发（全栈各端开发、单端「开始开发」都按它）"><option value="">自动（${esc(r['自动流程']||'')}）</option>${FL.map(x=>`<option value="${esc(x.id)}" ${x.id===r['流程id']?'selected':''}>${esc(x['名称'])}</option>`).join('')}</select></div>`;
  const ends=v['各端'].map(r=>`<tr ${r['待添加']?'style="background:var(--s4b)"':''}><td><b>${esc(r['端'])}</b>${r['待添加']?'<div class="small" style="color:var(--s4)">方案要做，还没加</div>':''}</td>
    <td><select data-send="${esc(r['端'])}" style="max-width:340px">${opts(r)}</select>${r['远程']?' <span class="badge">远程</span>':''}${r['版本库']==='svn'?' <span class="badge st-收集中" title="svn：AI 只改工作副本、不提交；审查通过、你验收后由知识库提交">svn</span>':''}${r['版本库']==='本地'?' <span class="badge" title="不是 git / svn：AI 直接改目录里的文件，不提交、不推送">本地目录</span>':''}${bldTag(r['端'])}${r['待添加']&&canBuild?` <span class="small mut">或</span> <button class="small pri" data-snew="${esc(r['端'])}" title="还没有代码：选个父目录，知识库建目录、git init、按方案生成脚手架并首次提交">🛠 新建工程</button>`:''}</td>
    <td>${r['仓库']&&r['版本库']==='本地'?`<span class="small mut">本地目录，没有分支</span>${flowSel(r)}`:r['仓库']?`<input type="text" data-sbr="${esc(r['端'])}" list="sbl-${esc(r['端'])}" value="${esc(r['分支']||'')}" placeholder="${esc(r['当前分支']?'当前 '+r['当前分支']:'开发分支')}" title="可从下拉里选分支，也可以手动改" style="width:100%;max-width:240px"><datalist id="sbl-${esc(r['端'])}"></datalist>${flowSel(r)}${r['版本库']==='git'?`<div class="small mut row" style="gap:4px;margin-top:4px"><span title="验收时知识库把开发分支（连同标签）推到这里；AI 不推送">推送到</span><input type="text" data-srm="${esc(r['端'])}" value="${esc(r['远端']||'')}" placeholder="远端仓库地址，如 https://github.com/you/app.git" style="flex:1;min-width:0;max-width:240px;font-size:12px"></div>`:''}`:''}</td>
    <td>${r['仓库']?`<button class="small" data-sdel="${esc(r['端'])}" title="这一期不涉及 ${esc(r['端'])}，移除" aria-label="移除 ${esc(r['端'])}">✕</button>`:''}</td></tr>`).join('');
  const addOpts=common.filter(e=>!v['各端'].some(r=>r['端']===e)).map(e=>`<option>${esc(e)}</option>`).join('');
  const addRow=`<tr><td colspan="4"><div class="row" style="gap:6px;flex-wrap:wrap"><select id="stAddE" style="width:auto"><option value="">＋ 添加端…</option>${addOpts}<option value="__other">其他（自己填名字，如 Unity、嵌入式、脚本）…</option></select>
    <span id="stAddRw" hidden><select id="stAddR" style="max-width:340px"></select> <button id="stAddGo">添加</button> <a class="small mut" id="stAddX">取消</a></span></div></td></tr>`;
  const W={小:'st-已完成',中:'st-收集中',大:'st-已暂停2'};
  const plan=P?`<div style="margin-top:14px"><div><b>方案</b> <span class="mut small">${esc(P['时间']||'')}</span>　${badge(st==='已拍板'?'已拍板':st==='待拍板'?'待拍板':st||'')}${v['全栈']['契约版本']?` <span class="badge st-开发中">契约 ${esc(v['全栈']['契约版本'])}</span>`:''}</div>
    <p>${esc(P['摘要']||'')}</p>
    ${(P['需求']||[]).length?`<details ${st==='待拍板'?'open':''} style="margin-bottom:10px"><summary class="small"><b>需求 ${P['需求'].length} 条</b> <span class="mut">验收标准 ${P['需求'].reduce((a,r)=>a+(r['验收']||[]).length,0)} 条${v['规格路径']?' · '+esc(v['规格路径']):''}</span></summary>
      <div class="tw"><table><tr><th style="width:80px">编号</th><th>需求与验收</th><th style="width:120px">端</th><th style="width:160px">设计</th></tr>
      ${P['需求'].map(r=>`<tr><td class="mono small">${esc(r['编号'])}</td><td><b>${esc(r['标题']||'')}</b>${r['描述']?`<div class="small mut">${esc(r['描述'])}</div>`:''}
        ${(r['验收']||[]).length?`<ul class="small" style="margin:4px 0 0;padding-left:16px">${r['验收'].map(a=>`<li>${esc(a)}</li>`).join('')}</ul>`:'<div class="small" style="color:var(--s4)">没有验收标准</div>'}</td>
        <td class="small">${esc((r['端']||[]).join('、'))}</td><td class="small">${(r['设计']||[]).length?esc(r['设计'].join('、')):'<span style="color:var(--s4)">无</span>'}</td></tr>`).join('')}</table></div></details>`:''}
    ${(()=>{const dc=P['设计对照']||{},L=(k,t)=>(dc[k]||[]).length?`<div class="small"><b>${t}（${dc[k].length}）</b><ul style="margin:2px 0 6px;padding-left:16px">${dc[k].map(x=>`<li>${esc(typeof x==='string'?x:(x['页面']||'')+' 缺 '+(x['缺']||''))}</li>`).join('')}</ul></div>`:'';
      const body=L('缺设计','需求有、设计没画')+L('设计多出','设计画了、需求没写')+L('缺状态','缺状态')+L('端不一致','各端不一致')+(dc['无法核对']?`<div class="small" style="color:var(--s4)">无法核对：${esc(dc['无法核对'])}</div>`:'');
      return P['设计对照']?`<div class="bcard" style="margin-bottom:10px;border-color:${body?'var(--s1)':'var(--line)'}"><b class="small">设计稿对照</b> ${body?'':'<span class="badge st-已完成">需求和设计一致</span>'}<div style="margin-top:4px">${body}</div></div>`:''})()}
    <div class="bcards">${(P['各端']||[]).map(x=>`<div class="bcard" style="flex:1;min-width:220px;${x['涉及']?'':'opacity:.55'}"><b>${esc(x['端'])}</b> ${x['涉及']?`<span class="badge ${W[x['工作量']]||''}">${esc(x['工作量']||'')}</span>`:'<span class="badge">不涉及</span>'}
      <div class="small mut">${esc(x['原因']||'')}</div>${(x['改动']||[]).length?`<ul class="small" style="margin:4px 0 0;padding-left:16px">${x['改动'].map(c=>`<li>${esc(c)}</li>`).join('')}</ul>`:''}
      ${x['风险']?`<div class="small" style="color:var(--s4);margin-top:4px">风险：${esc(x['风险'])}</div>`:''}
      ${x['技术栈']?`<div class="small" style="margin-top:4px"><b>技术栈</b>　${esc(x['技术栈'])}</div>`:''}
      ${x['目录']?`<details class="small" style="margin-top:2px"><summary class="mut">目录结构</summary><pre class="small" style="margin:2px 0;white-space:pre-wrap">${esc(x['目录'])}</pre></details>`:''}
      ${x['脚手架']?`<div class="small mut" style="margin-top:2px">脚手架：${esc(x['脚手架'])}</div>`:''}</div>`).join('')}</div>
    ${(P['接口']||[]).length?`<div style="margin-top:10px"><b class="small">接口</b><div class="tw"><table><tr><th style="width:70px">方法</th><th>路径</th><th>说明</th><th style="width:60px">类型</th><th style="width:140px">调用端</th></tr>
      ${P['接口'].map(a=>`<tr><td class="mono small">${esc(a['方法'])}</td><td class="mono small">${esc(a['路径'])}</td><td class="small">${esc(a['说明']||'')}</td><td class="small">${esc(a['类型']||'')}</td><td class="small">${esc((a['调用端']||[]).join('、'))}</td></tr>`).join('')}</table></div></div>`:''}
    ${(P['数据']||[]).length?`<div style="margin-top:8px"><b class="small">数据</b><ul class="small" style="margin:2px 0;padding-left:16px">${P['数据'].map(x=>`<li>${esc(x)}</li>`).join('')}</ul></div>`:''}
    ${(P['待确认']||[]).length&&st!=='待拍板'?`<div style="margin-top:8px"><b class="small">${P['待确认'].some(q=>q['结论'])?'拍板时确认的问题':'待确认'}（${P['待确认'].length}）</b><ol class="small" style="margin:2px 0;padding-left:20px">${P['待确认'].map(q=>`<li>${esc(q['问题'])}${q['结论']?`<div>结论：${esc(q['结论'])} <span class="mut">（${esc(q['结论来源']==='用户'?'你的意见':'按 AI 建议')}）</span></div>`:q['建议']?`<div class="mut">建议：${esc(q['建议'])}</div>`:''}</li>`).join('')}</ol></div>`:''}
    ${v['契约']?`<details style="margin-top:8px"><summary class="small"><b>接口契约</b> <span class="mut mono">${esc(v['契约路径'])}</span></summary><pre class="small" style="max-height:420px;overflow:auto">${esc(v['契约'])}</pre></details>`:''}</div>`:'';
  const SB={排队中:'st-收集中',开发中:'st-开发中',待审查:'st-已批准开发',审查中:'st-已批准开发',打回修改:'st-已暂停',审查通过:'st-已完成',要你看:'st-已暂停2',失败:'st-已暂停2',已停止:'',中断待续:'st-已暂停'};
  const D=v['开发']||[],canDev=['已拍板','开发中','各端完成'].includes(st);
  const CR=v['变更']||[],CRS={待评估:'st-收集中',评估中:'st-开发中',待拍板:'st-已批准开发',已纳入:'st-已完成',已驳回:''};
  const crBox=(P||CR.length)?`<div style="margin-top:14px"><div class="row" style="gap:8px"><b>变更</b><span class="mut small">做的过程中发现要改需求、要别的端配合、要改接口：提出来，AI 出增量方案，你拍板后受影响的端自动补做</span></div>
    ${CR.length?`<div class="tw" style="margin-top:6px"><table><tr><th style="width:70px">编号</th><th>内容</th><th style="width:130px">来源</th><th style="width:90px">状态</th><th style="width:60px"></th></tr>
      ${CR.slice().reverse().map(x=>`<tr><td class="mono small">${esc(x['编号'])}</td><td><b class="small">${esc(x['标题'])}</b>${x['说明']?`<div class="small mut">${esc(x['说明'])}</div>`:''}${(x['影响端']||[]).length?`<div class="small mut">可能影响：${esc(x['影响端'].join('、'))}</div>`:''}</td>
        <td class="small mut">${esc(x['来源'])}<div>${esc(x['时间'])}</div></td><td><span class="badge ${CRS[x['状态']]||''}">${esc(x['状态'])}</span>${x['纳入方案']?`<div class="small mut">方案 ${esc(x['纳入方案'])}</div>`:''}</td>
        <td>${['待评估','待拍板'].includes(x['状态'])?`<a class="small mut" data-crx="${esc(x['编号'])}">驳回</a>`:''}</td></tr>`).join('')}</table></div>`:''}
    <div class="row" style="gap:6px;margin-top:6px;flex-wrap:wrap"><input type="text" id="crT" placeholder="要变什么，如：iOS 也要兼容新的返回字段" style="flex:1;min-width:260px">
      ${(have.length?have:common.slice(0,5)).map(e=>`<label class="small row" style="gap:2px"><input type="checkbox" data-cre="${e}" style="width:auto;margin:0">${e}</label>`).join('')}<button id="crGo">＋ 变更</button></div></div>`:'';
  const EV=v['证据'],evBox=EV&&D.length?`<div style="margin-top:14px"><div class="row" style="gap:8px"><b>证据包</b><span class="mut small">需求 × 端：开发会话汇报的完成度、审查结论</span><span style="flex:1"></span>
      ${EV['可验收']?`<button class="pri" id="stAcc">✓ 验收（拍板 2）</button>`:''}${(v['全栈']||{})['状态']==='已验收'?'<span class="badge st-已完成">已验收</span>':''}</div>
    <div class="tw" style="margin-top:6px"><table><tr><th style="width:80px">需求</th><th>标题</th>${[...new Set(EV['需求'].flatMap(r=>Object.keys(r['端'])))].map(e=>`<th style="width:120px">${esc(e)}</th>`).join('')}</tr>
      ${(()=>{const es=[...new Set(EV['需求'].flatMap(r=>Object.keys(r['端'])))];return EV['需求'].map(r=>`<tr><td class="mono small">${esc(r['编号'])}</td><td class="small">${esc(r['标题'])} <span class="mut">验收 ${r['验收']} 条</span></td>
        ${es.map(e=>{const c=r['端'][e];if(!c)return'<td class="mut small">—</td>';const ok=c['审查']==='通过'&&String(c['完成度']).startsWith('完成');
          return `<td class="small"><span class="badge ${ok?'st-已完成':c['高']?'st-已暂停2':c['完成度']?'st-已暂停':''}">${ok?'✓ 通过':esc(c['完成度']||c['状态'])}</span>${c['问题']?`<div class="mut">问题 ${c['问题']} 个</div>`:''}</td>`}).join('')}</tr>`).join('')})()}</table></div>
    ${EV['可验收']&&!EV['全部通过']?'<div class="small" style="color:var(--s4);margin-top:4px">还有没完全通过的需求：可以提变更、重新开工某一端，或者带着问题验收</div>':''}</div>`:'';
  const devTbl=canDev?`<div style="margin-top:14px"><div class="row" style="gap:8px"><b>各端开发</b><span class="mut small">同时最多 ${v['并行上限']} 个端在跑；每端做完由独立的审查会话对照需求、契约、设计挑错，打回最多 ${v['审查轮数']} 轮</span>
      <span style="flex:1"></span>${!D.length?'<button class="pri" id="stDev">▶ 各端开工</button>':''}</div>
    ${D.length?`<div class="tw" style="margin-top:6px"><table><tr><th style="width:70px">端</th><th style="width:110px">状态</th><th>汇报</th><th>审查</th><th style="width:120px"></th></tr>
    ${D.map(x=>{const rp=x['汇报']||{},rv=x['审查']||{};return `<tr><td><b>${esc(x['端'])}</b></td>
      <td><span class="badge ${SB[x['状态']]||''}">${x['在跑']?'● ':''}${esc(x['状态'])}</span>${x['轮次']?`<div class="small mut">第 ${x['轮次']} 轮修改</div>`:''}${x['等待']?`<div class="small mut">等待：${esc(x['等待'])}</div>`:''}${x['用mock']?'<div class="small mut">按契约 mock</div>':''}${x['联调']?'<div class="small mut">联调轮</div>':''}${x['状态']==='中断待续'?intrHtml(x['中断']):''}${x['参与']?`<div class="small" title="这一端的开发 / 审查会话派过的角色团专家">${esc(x['参与'])}</div>`:''}${x['代码基线']?`<div class="small mut mono" title="${esc('开工前更新代码后的代码基线（分支 @提交 · 时间 · 哪个时机 · 落后远端）')}">📍 代码：${esc(x['代码基线'])}</div>`:''}${x['版本库']==='本地'?'<div class="small mut">本地目录：改动留在目录里</div>':x['版本库']==='svn'?`<div class="small mut">${x['已提交']?(x['已提交']==='无改动'?'svn 无改动':'svn 已提交 r'+esc(x['已提交'])):'svn：验收时提交'}</div>`:`<div class="small mut">${x['已推送']?(x['已推送'].includes('@')?'已推送 '+esc(x['已推送'].split('@')[0]):esc(x['已推送'])):'验收时推送'}</div>`}</td>
      <td class="small">${rp['总结']?esc(rp['总结']):'<span class="mut">—</span>'}${rp['提交']?`<div class="mono mut">${esc(rp['提交'])}</div>`:''}${rp['证据']?`<details><summary class="mut">证据</summary>${esc(rp['证据'])}</details>`:''}
        ${x['结果']&&['失败','要你看'].includes(x['状态'])?`<div style="color:var(--s4)">${esc(x['结果'])}</div>`:''}</td>
      <td class="small">${rv['结论']?`<span class="badge ${rv['结论']==='通过'?'st-已完成':'st-已暂停'}">${esc(rv['结论'])}</span> ${esc(rv['总结']||'')}
        ${(rv['问题']||[]).length?`<details><summary class="mut">问题 ${rv['问题'].length} 个</summary><ul style="margin:2px 0;padding-left:16px">${rv['问题'].map(i=>`<li>[${esc(i['严重度']||'')}] ${esc(i['需求']||'')} ${esc(i['问题']||'')} <span class="mut mono">${esc(i['位置']||'')}</span></li>`).join('')}</ul></details>`:''}`:'<span class="mut">—</span>'}</td>
      <td>${x['在跑']||x['状态']==='排队中'?`<button class="small" data-stop="${esc(x['端'])}">停止</button>`:`<button class="small" data-redev="${esc(x['端'])}">重新开工</button>`}</td></tr>`}).join('')}</table></div>`:''}</div>`:'';
  const RL=v['发布'],RS=NS['发布']||{},QS=NS['需求']||{};
  const newBox=v['新产品']?`<div class="bcard" style="margin-bottom:10px"><div class="row" style="gap:8px;flex-wrap:wrap"><b>🌱 新产品</b><span class="small mut">资料 → 完善需求 → 出方案 → 拍板 → 建工程 → 开发 → 验收 → 发布</span><span style="flex:1"></span>
      ${!have.length&&!P?'<a class="small mut" id="stNewOff">不是新产品</a>':''}</div>
    <div class="row" style="gap:8px;margin-top:6px;flex-wrap:wrap"><span class="small"><b>需求</b>　${v['PRD']?`<a data-read="${esc(v['PRD'])}">需求/PRD.md</a>`:v['需求已跳过']?'<span class="mut">已跳过</span>':'<span style="color:var(--s4)">还没有 PRD</span>'}</span>
      ${QS['在跑']?`<span class="badge st-开发中">● AI 正在追问、写 PRD</span><span class="small mut">问题在「开发 → 待确认」里回答</span>`
        :`<button class="${v['PRD']||v['需求已跳过']||P?'':'pri'}" id="stPrd">💬 ${v['PRD']?'重新完善需求':'完善需求'}</button><input type="text" id="stPrdN" placeholder="补充 / 意见（可空）" style="flex:1;min-width:200px">${!v['PRD']&&!v['需求已跳过']?'<a class="small mut" id="stPrdSkip">需求已经很清楚，跳过</a>':''}`}</div>
    ${!QS['在跑']&&(QS['参与']||QS['警告'])?`<div class="small" style="margin-top:4px">${QS['参与']?`<span title="完善需求会话派过的角色团专家">${esc(QS['参与'])}</span> `:''}${QS['警告']?`<span style="color:var(--warn)">⚠ ${esc(QS['警告'])}</span>`:''}</div>`:''}
    ${QS['状态']==='已结束'&&!v['PRD']&&!QS['在跑']&&QS['结果']?`<div class="small" style="color:var(--s4);margin-top:4px">上次没写出 PRD：${esc(QS['结果'].slice(0,160))}</div>`:''}</div>`
    :!have.length&&!P?`<div class="row small" style="gap:8px;margin-bottom:8px"><span class="mut">全新产品、还没有代码？</span><button id="stNewOn">🌱 这是新产品</button><span class="mut">AI 先追问需求写 PRD，再建议技术栈、帮你建工程，最后列发布清单</span></div>`:'';
  const relBox=RL?`<div style="margin-top:14px"><div class="row" style="gap:8px;flex-wrap:wrap"><b>发布</b><span class="mut small">上线前要做的事：逐条做完勾上，全勾完这一期才算上线</span><span style="flex:1"></span>
      ${RL['状态']==='已上线'?`<span class="badge st-已完成">已上线 ${esc(RL['上线时间']||'')}</span>`:RS['在跑']?'<span class="badge st-开发中">● 正在列发布清单</span>'
        :`<button class="${RL['清单']?'':'pri'}" id="stRel">📦 ${RL['清单']?'重出发布清单':'出发布清单'}</button>`}${!RS['在跑']&&RS['参与']?`<span class="small" title="发布清单会话派过的角色团专家">${esc(RS['参与'])}</span>`:''}${!RS['在跑']&&RS['警告']?`<span class="small" style="color:var(--warn)">⚠ ${esc(RS['警告'])}</span>`:''}</div>
    ${(RL['清单']||[]).length?`<div class="tw" style="margin-top:6px"><table><tr><th style="width:40px"></th><th style="width:80px">端</th><th>要做的事</th><th style="width:200px">要你提供</th></tr>
      ${RL['清单'].map(x=>`<tr style="${x['完成']?'opacity:.6':''}"><td><input type="checkbox" data-rel="${esc(x['编号'])}" ${x['完成']?'checked':''} ${RL['状态']==='已上线'?'disabled':''} style="width:auto;margin:0"></td>
        <td class="small">${esc(x['端'])}</td><td><b class="small">${esc(x['项'])}</b>${x['说明']?`<div class="small mut">${esc(x['说明'])}</div>`:''}</td>
        <td class="small" style="${x['需要']&&!x['完成']?'color:var(--s4)':''}">${esc(x['需要']||'—')}</td></tr>`).join('')}</table></div>
      <div class="small mut" style="margin-top:4px">已完成 ${RL['清单'].filter(x=>x['完成']).length} / ${RL['清单'].length}；缺的账号、证书记在「开发 → 问题」里</div>`:''}</div>`:'';
  const QN=(P&&P['待确认'])||[];let qMine=QN.filter(q=>(q['回答']||{})['方式']==='自定义').length,qOk=QN.filter(q=>q['回答']).length,qLeft=QN.length-qOk;
  const qCnt=()=>`已确认 ${qOk} / ${QN.length}${qLeft?'，没确认的拍板时按 AI 建议':''}`;
  const qBox=st==='待拍板'&&!run&&QN.length?`<div class="bcard" style="margin-top:12px;border-color:var(--s1)"><div class="row" style="gap:8px;flex-wrap:wrap"><b>拍板前先确认这 ${QN.length} 个问题</b>
      <span class="small mut" id="stQCnt">${qCnt()}</span><span style="flex:1"></span><a class="small" id="stQAll" ${qLeft?'':'hidden'}>剩下的全部按建议</a></div>
    <ol style="margin:8px 0 0;padding-left:20px">${QN.map((q,i)=>{const a=q['回答']||{},mine=a['方式']==='自定义';return `<li style="margin-bottom:10px"><b class="small">${esc(q['问题'])}</b>
      ${q['建议']?`<div class="small mut">AI 建议：${esc(q['建议'])}</div>`:''}
      <div class="row small" style="gap:12px;margin-top:4px;flex-wrap:wrap"><label class="row" style="gap:3px"><input type="radio" name="sq${i}" data-sqa="${i}" value="按建议" ${a['方式']==='按建议'?'checked':''} style="width:auto;margin:0">✓ 按建议</label>
        <label class="row" style="gap:3px"><input type="radio" name="sq${i}" data-sqa="${i}" value="自定义" ${mine?'checked':''} style="width:auto;margin:0">✎ 我的意见</label>${a['方式']?'':`<span data-squn="${i}" style="color:var(--s4)">未确认</span>`}</div>
      <div data-sqw="${i}" ${mine?'':'hidden'} class="row" style="gap:6px;margin-top:4px"><textarea data-sqt="${i}" rows="2" style="flex:1;min-width:0" placeholder="写你的决定或意见，重出方案时 AI 按它改">${esc(a['内容']||'')}</textarea><button class="small" data-sqs="${i}">保存</button></div></li>`}).join('')}</ol></div>`:'';
  const tabN=()=>{const stb=document.querySelector('[data-tab="stack"]');if(stb&&st==='待拍板')stb.innerHTML=`全栈<span class="n w">${QN.length?qLeft||'✓':1}</span>`};tabN();
  const actBtns=()=>qMine?`<button class="pri" id="stReAns">按我的回答重出方案（${qMine} 条意见）</button><a class="small mut" id="stOk">不重出，直接拍板（按我的意见执行）</a>`
    :`<button class="pri" id="stOk">✓ 拍板${QN.length?(qLeft?'（其余按 AI 建议）':'（按确认的结论）'):''}</button>`;
  const dndSw=`<span class="sw ${v['免打扰']?'on':''}" id="stDnd" title="整期生效：出方案、完善需求、各端开发的提问都由 AI 按自己的建议决策并记录（和「确认与问题」上方是同一个开关）"><i></i>🌙 免打扰（整期）</span>`;
  const html=`${newBox}<div class="small mut" style="margin-bottom:8px">选这一期涉及的端和仓库（远程机器写 user@主机:/路径），AI 读资料、设计稿和代码，出需求规格、设计对照、影响分析和接口契约，你拍板一次后各端再开工。</div>
    ${v['统一流程']?`<div class="small" style="margin-bottom:6px;color:var(--s4)">这一期在会话面板统一指定过流程「${esc(v['统一流程'])}」：没单独选流程的端都按它。到「开发」的会话栏点「改为按端」可以取消。</div>`:''}
    <div class="tw"><table><tr><th style="width:90px">端</th><th>仓库</th><th style="width:240px">分支 · 开发流程</th><th style="width:40px"></th></tr>${ends||'<tr><td colspan="4" class="small mut">还没有加端：用下面「＋ 添加端」选这一期涉及的平台和仓库</td></tr>'}${addRow}</table></div>
    ${qBox}<div class="row" style="gap:8px;margin-top:10px;flex-wrap:wrap">
      ${run?`<span class="small"><span class="badge st-开发中">● 出方案中</span> <span class="mut">${esc(v['会话']['引擎']||'')} · ${esc(v['会话']['开始时间']||'')} 开始</span></span>`
        :st==='待拍板'?`<span id="stAct" style="display:contents">${actBtns()}</span><input type="text" id="stNote" placeholder="方案别的地方不对，写意见后重出" style="flex:1;min-width:240px"><button id="stRe">重出方案</button>`
        :`<button class="pri" id="stGo">🧭 ${P?'重出方案':'出方案'}</button>${P?'<input type="text" id="stNote" placeholder="意见（可空）" style="flex:1;min-width:240px">':''}`}
      <span style="flex:1"></span>${dndSw}
      ${!run&&v['会话']&&v['会话']['参与']?`<span class="small" title="方案会话派过的角色团专家">${esc(v['会话']['参与'])}</span>`:''}${!run&&v['会话']&&v['会话']['警告']?`<span class="small" style="color:var(--warn)">⚠ ${esc(v['会话']['警告'])}</span>`:''}
      ${st==='出方案失败'&&v['会话']?`<span class="small" style="color:var(--s4)">上次没出来：${esc((v['会话']['结果']||'').slice(0,160))}</span>`:''}</div>
    ${devTbl}${evBox}${relBox}${crBox}${plan}`;
  // 整块重写会把展开的 details 收起、页面变矮、滚动被拉回：HTML 没变就不碰 DOM，变了用 morph 只改有变化的地方
  if(box._html!==html){morphHTML(box,html);box._html=html}
  // 回答问题只局部更新：这一条的状态、计数、拍板按钮，不重画整块
  const qSync=()=>{qOk=QN.filter(q=>q['回答']).length;qLeft=QN.length-qOk;qMine=QN.filter(q=>(q['回答']||{})['方式']==='自定义').length;
    if($('#stQCnt'))$('#stQCnt').textContent=qCnt();if($('#stQAll'))$('#stQAll').hidden=!qLeft;
    if($('#stAct')){$('#stAct').innerHTML=actBtns();bindAct()}tabN()};
  const qSet=(i,mode,text)=>{QN[i]['回答']={'方式':mode,'内容':text||''};const u=box.querySelector(`[data-squn="${i}"]`);if(u)u.remove();
    const r=box.querySelector(`[data-sqa="${i}"][value="${mode}"]`);if(r)r.checked=true;
    if(mode==='按建议'){const w=box.querySelector(`[data-sqw="${i}"]`);if(w)w.hidden=true}};
  const ans=async(i,mode,text)=>{try{toast((await post('/api/stack/answer',{period:name,idx:+i,mode,text:text||''})).msg);qSet(+i,mode,text);qSync()}catch(e){toast(e.message)}};
  box.querySelectorAll('[data-sqa]').forEach(r=>r.onchange=()=>{const i=r.dataset.sqa;
    if(r.value==='按建议')return ans(i,'按建议');const w=box.querySelector(`[data-sqw="${i}"]`);w.hidden=false;w.querySelector('textarea').focus({preventScroll:true})});
  box.querySelectorAll('[data-sqs]').forEach(b=>b.onclick=()=>{const t=box.querySelector(`[data-sqt="${b.dataset.sqs}"]`).value.trim();if(!t)return toast('写一下你的意见');ans(b.dataset.sqs,'自定义',t)});
  if($('#stQAll'))$('#stQAll').onclick=async()=>{for(const [i,q] of QN.entries())if(!q['回答'])try{await post('/api/stack/answer',{period:name,idx:i,mode:'按建议'});qSet(i,'按建议')}catch(e){toast(e.message);break}qSync()};
  if($('#stDnd'))$('#stDnd').onclick=async()=>{try{toast((await post('/api/dnd',{period:name,on:!v['免打扰']})).msg)}catch(e){toast(e.message)}stackBox(name);if($('#devBox'))devBox(name)};
  const P2=async(u,b)=>{try{toast((await post(u,{period:name,...b})).msg)}catch(e){toast(e.message)}stackBox(name)};
  if($('#stNewOn'))$('#stNewOn').onclick=()=>P2('/api/stack/newproduct',{on:true});
  if($('#stNewOff'))$('#stNewOff').onclick=async()=>{if(await kbConfirm('不按新产品流程走？（还是先选仓库）'))P2('/api/stack/newproduct',{on:false})};
  if($('#stPrd'))$('#stPrd').onclick=()=>P2('/api/stack/prd',{note:$('#stPrdN').value.trim()});
  if($('#stPrdSkip'))$('#stPrdSkip').onclick=async()=>{if(await kbConfirm('跳过完善需求，直接出方案？'))P2('/api/stack/prd',{skip:true})};
  box.querySelectorAll('[data-read]').forEach(a=>a.onclick=()=>readFile(a.dataset.read));
  if($('#stRel'))$('#stRel').onclick=()=>P2('/api/stack/release',{});
  box.querySelectorAll('[data-rel]').forEach(c=>c.onchange=()=>P2('/api/stack/release/check',{id:c.dataset.rel,done:c.checked}));
  box.querySelectorAll('[data-sretry]').forEach(b=>b.onclick=()=>P2('/api/stack/create',{end:b.dataset.sretry,retry:1}));
  box.querySelectorAll('[data-snew]').forEach(b=>b.onclick=async()=>{const end=b.dataset.snew;
    const parent=await pickDir('');if(!parent)return;
    let def='';try{def=(await api('/api/stack/newdir?name='+enc(name)+'&end='+enc(end))).dir}catch(_){}
    const dir=await kbPrompt(`在 ${parent} 下新建哪个目录？（留空 = 直接用 ${parent}，要求是空目录）`,def);if(dir===null)return;
    await P2('/api/stack/create',{end,parent,dir:dir.trim()});stackRepos=null});
  if($('#crGo'))$('#crGo').onclick=async()=>{const t=$('#crT').value.trim();if(!t)return toast('写一句要变什么');
    const ends=[...box.querySelectorAll('[data-cre]:checked')].map(c=>c.dataset.cre);
    try{toast((await post('/api/stack/change',{period:name,title:t,ends})).msg)}catch(e){toast(e.message)}stackBox(name)};
  box.querySelectorAll('[data-crx]').forEach(a=>a.onclick=async()=>{if(!await kbConfirm(`驳回 ${a.dataset.crx}？`))return;try{toast((await post('/api/stack/reject',{period:name,id:a.dataset.crx})).msg)}catch(e){toast(e.message)}stackBox(name)});
  if($('#stAcc'))$('#stAcc').onclick=async()=>{const pubE=(v['开发']||[]).filter(x=>x['状态']==='审查通过'&&x['版本库']!=='本地'&&!(x['版本库']==='svn'?x['已提交']:x['已推送'])).map(x=>x['端']+(x['版本库']==='svn'?'（svn 提交）':'（git 推送）'));
    if(!await kbConfirm('验收这一期？看过证据包，确认各端做完（这一期会标为已完成，之后照常处理 Bug）。'+(pubE.length?`\n\n验收时会把审查通过的端送上服务器：${pubE.join('、')}。svn 只提交这次改的文件，git 只推开发分支。`:'')))return;
    try{toast((await post('/api/stack/accept',{period:name})).msg)}catch(e){toast(e.message)}render()};
  if($('#stDev'))$('#stDev').onclick=async()=>{if(!await kbConfirm('各端开工？涉及的端会各起一个 AI 会话按拍板的方案开发（= 你批准开发）。'))return;
    try{toast((await post('/api/stack/dev',{period:name})).msg)}catch(e){toast(e.message)}stackBox(name)};
  bindIntr(box,()=>stackBox(name));
  box.querySelectorAll('[data-stop]').forEach(b=>b.onclick=async()=>{if(!await kbConfirm(`停止 ${b.dataset.stop}？`))return;try{toast((await post('/api/stack/stop',{period:name,end:b.dataset.stop})).msg)}catch(e){toast(e.message)}stackBox(name)});
  box.querySelectorAll('[data-redev]').forEach(b=>b.onclick=async()=>{if(!await kbConfirm(`${b.dataset.redev} 重新开工？`))return;try{toast((await post('/api/stack/dev',{period:name,end:b.dataset.redev})).msg)}catch(e){toast(e.message)}stackBox(name)});
  const setEnd=async(end,repo)=>{if(repo==='__other'){repo=(await kbPrompt(`${end} 的仓库地址：本机写路径，远程写 user@主机:/路径`,'',{title:'填写仓库地址',ph:'/路径 或 user@主机:/路径'})||'').trim();if(!repo){stackBox(name);return}}
    if(!repo||repo==='__pick')return;
    const known=(stackRepos||[]).find(x=>x['路径']===repo);
    if(repo.startsWith('/')||repo.startsWith('~'))try{if(!known||known['版本库']==='本地'){const f=await api('/api/stack/dirkind?path='+enc(repo));
      if(!f['版本库']&&await kbConfirm(`${f['路径']} 不是 git 仓库。\n\n初始化为 git 仓库？（git init 并首次提交，之后 AI 在分支上开发、验收时推送）\n不初始化 = 当普通本地目录用（AI 直接改文件，不提交）`,{ok:'初始化为 git',cancel:'当普通目录用'})){
        toast((await post('/api/stack/gitinit',{path:f['路径']})).msg);repo=f['路径']}}}catch(_){}
    try{toast((await post('/api/stack/end',{period:name,end,repo})).msg);stackRepos=null}catch(e){toast(e.message)}stackBox(name)};
  box.querySelectorAll('[data-send]').forEach(sel=>sel.onchange=()=>setEnd(sel.dataset.send,sel.value));
  box.querySelectorAll('[data-sdel]').forEach(b=>b.onclick=async()=>{const e=b.dataset.sdel;if(!await kbConfirm(`这一期不涉及 ${e}？会从各端里移除（仓库本身不受影响）。`))return;
    try{toast((await post('/api/stack/end',{period:name,end:e,repo:''})).msg)}catch(x){toast(x.message)}stackBox(name)});
  let addEnd='';
  const addSel=$('#stAddE'),addRw=$('#stAddRw');
  if(addSel)addSel.onchange=async()=>{let e=addSel.value;
    if(e==='__other'){e=(await kbPrompt('端的名字（如 Unity、嵌入式、脚本），最多 20 个字','',{title:'添加端'})||'').trim();if(!e){addSel.value='';return}}
    if(!e){addRw.hidden=true;return}
    const dup=v['各端'].find(r=>r['端'].toLowerCase()===e.toLowerCase());
    if(dup&&dup['仓库']){toast(`已经有 ${dup['端']} 了`);addSel.value='';return}
    addEnd=dup?dup['端']:e;$('#stAddR').innerHTML=opts({端:addEnd});addRw.hidden=false;
    if(addSel.value==='__other'){const o=document.createElement('option');o.value=addEnd;o.textContent=addEnd;addSel.insertBefore(o,addSel.lastElementChild);addSel.value=addEnd}};
  const addR=$('#stAddR');if(addR)addR.onchange=()=>{if(addR.value==='__other'&&addEnd)setEnd(addEnd,'__other')};  // 选「填写地址」直接弹框，填完就加
  if($('#stAddX'))$('#stAddX').onclick=()=>{addSel.value='';addRw.hidden=true};
  if($('#stAddGo'))$('#stAddGo').onclick=()=>{const r=$('#stAddR').value;if(!addEnd)return toast('先选平台');if(!r||r==='__pick')return toast(`给 ${addEnd} 选一个仓库`);setEnd(addEnd,r)};
  box.querySelectorAll('[data-sbr]').forEach(i=>i.onfocus=async()=>{if(i.dataset.loaded)return;i.dataset.loaded='1';
    const r=v['各端'].find(x=>x['端']===i.dataset.sbr),dl=document.getElementById('sbl-'+i.dataset.sbr);if(!r||!dl)return;
    try{const b=await api('/api/stack/branches?repo='+enc(r['仓库']));dl.innerHTML=(b['分支']||[]).map(x=>`<option value="${esc(x)}">${x===b['当前']?'当前分支':''}</option>`).join('')}catch(_){}});
  box.querySelectorAll('[data-srm]').forEach(i=>i.onchange=async()=>{const u=i.value.trim();if(!u)return;
    try{toast((await post('/api/stack/remote',{period:name,end:i.dataset.srm,url:u})).msg)}catch(e){toast(e.message)}stackBox(name)});
  box.querySelectorAll('[data-sfl]').forEach(s=>s.onchange=async()=>{try{toast((await post('/api/stack/flow',{period:name,end:s.dataset.sfl,flow:s.value})).msg)}catch(e){toast(e.message)}stackBox(name)});
  box.querySelectorAll('[data-sbr]').forEach(i=>i.onchange=async()=>{const r=v['各端'].find(x=>x['端']===i.dataset.sbr);
    try{toast((await post('/api/stack/end',{period:name,end:r['端'],repo:r['仓库'],branch:i.value.trim()})).msg)}catch(e){toast(e.message)}stackBox(name)});
  const go=async()=>{try{toast((await post('/api/stack/plan',{period:name,note:$('#stNote')?$('#stNote').value.trim():''})).msg)}catch(e){toast(e.message)}stackBox(name)};
  if($('#stGo'))$('#stGo').onclick=go;if($('#stRe'))$('#stRe').onclick=go;
  const bindAct=()=>{if($('#stReAns'))$('#stReAns').onclick=async()=>{if(await kbConfirm(`把你对 ${qMine} 个问题的意见交给 AI 重出方案？按建议的那些也会一起定下来，不再重复问。`))go()};
  if($('#stOk'))$('#stOk').onclick=async()=>{if(!await kbConfirm('拍板这份方案？接口契约会锁成一个版本，之后各端按它开发。'+(qLeft?`\n\n还有 ${qLeft} 个问题没确认，将按 AI 建议处理。`:'')+(qMine?`\n\n你写了意见的 ${qMine} 个问题按你的意见执行，但方案正文不会按它改；想让 AI 先改方案就点「按我的回答重出方案」。`:'')))return;
    try{toast((await post('/api/stack/approve',{period:name})).msg)}catch(e){toast(e.message)}stackBox(name)}};
  bindAct();
  if(run||QS['在跑']||RS['在跑']||Object.values(BLD).some(b=>b['在跑'])||D.some(x=>x['在跑']||['排队中','待审查','打回修改'].includes(x['状态'])))setTimeout(()=>{if(curPeriod()===name)stackBox(name)},6000);
}