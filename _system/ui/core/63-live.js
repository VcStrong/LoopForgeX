// ==== AI 实时状态一行：期页面顶部状态条 + 悬停浮框，总览卡片的小圆点，侧栏的「疑似卡住」提示
// 数据：/api/period/live（期页面）、overview 每期的 live（总览 / 侧栏）。时间分档在这里按现在的时间算：
//   ≤5 分钟有动静 = 在跑，5–15 分钟 = 安静（灰），超过 15 分钟 = 疑似卡住
const liveCache={};
function liveAge(lv){const t=lv&&lv['最后动静'];return t?Math.max(0,Math.floor(Date.now()/1000-t)/60):null}
function liveAgo(lv){const a=liveAge(lv);if(a===null)return'';const m=Math.floor(a);return m<1?'刚刚':m<60?m+' 分钟前':Math.floor(m/60)+' 小时前'}
// → [键, 文字, 颜色]：等你回答 / 暂停 / 在跑 / 安静 / 卡住 / 已停 / 已完成
function liveTier(lv){
  const st=lv['状态'],a=liveAge(lv),q=lv['安静分钟']||5,k=lv['卡住分钟']||15;
  if(st==='等你回答')return['ask','● 等你回答','var(--s1)'];
  if(st==='暂停')return['pause',`⏸ 暂停${lv['恢复时间']?'，'+String(lv['恢复时间']).slice(11,16)+' 恢复':''}`,'var(--s1)'];
  if(st==='运行'){if(a===null||a<=q)return['run','● 在跑','var(--s2)'];if(a<=k)return['quiet','● 安静','var(--mut)'];return['stuck','⚠ 疑似卡住','var(--warn)']}
  if(st==='已完成')return['done','✓ 已完成','var(--s3)'];
  return['stop','○ 已停','var(--mut)'];
}
// 进度文字：有任务 → 「约 5/8」；规划阶段没任务 → 「规划中 · 第 2/4 阶段」（不显示假百分比）
function livePg(pg,full){
  if(!pg)return'';
  if(pg['总数'])return`${full&&pg['当前']?esc(pg['当前'])+' · ':''}${pg['约']?'约 ':''}${pg['完成']}/${pg['总数']}`;
  if(pg['阶段'])return`${pg['阶段']==='规划中'?'规划中':'规划中 · '+esc(pg['阶段'])}${pg['阶段总数']?` · 第 ${pg['阶段序号']}/${pg['阶段总数']} 阶段`:''}`;
  return'';
}
function liveShow(lv){return !!lv&&(['已批准开发','开发中'].includes(lv['期状态'])||lv['会话'].length||lv['状态']==='等你回答'||lv['状态']==='暂停')}
// 标题旁的状态：开发中的期把「开发中」换成实时状态（在跑 / 安静 / 疑似卡住…），其他照旧显示期状态
function liveHead(name,st){const lv=liveCache[name];if(!lv||!liveShow(lv))return badge(st);const [,t,c]=liveTier(lv);
  return `<span class="badge" style="color:${c};border-color:${c};cursor:default">${t}</span>`}
function liveLine(name,lv){
  const [k,t,c]=liveTier(lv),ss=lv['会话'],pg=lv['进度'];
  const work=ss.length===1?esc(ss[0]['名称'].replace(/会话$/,'')):ss.length?'':'';
  const parts=[`<b style="color:${c}">${t}</b>`];
  if(k==='ask'&&lv['问题'])parts[0]=`<a data-lv="ask" style="color:${c}" title="${esc(lv['问题']['问题'])}"><b>${t}</b>${lv['问题']['个数']>1?'（'+lv['问题']['个数']+' 个）':''}：${esc(lv['问题']['问题'].slice(0,40))}${lv['问题']['问题'].length>40?'…':''}</a>`;
  if(ss.length)parts.push(`${lv['AI数']} 个 AI`);
  const sd=lv['SDD'],sub=sd&&sd['子进度'];
  const fin=sd&&!sub&&sd['总体']['总数']&&sd['总体']['完成']>=sd['总体']['总数'],ra=fin&&sd['子agent']['在跑'][0];
  if(fin)parts.push(`${work?work+' ':''}TASK ${sd['总体']['总数']}/${sd['总体']['总数']} 已完成${ra?` · 收尾：${esc(ra['说明']||ra['类型'])} ${ra['已跑']} 分钟`:''}`);
  else if(sub){const o=sd['总体'];parts.push(`${work?work+' ':''}TASK ${Math.min(o['完成']+1,o['总数'])}/${o['总数']}${sub['当前']?' · '+esc(sub['当前'])+liveRound(sub):''}`)}
  else{const p=livePg(pg,true);if(work||p)parts.push([work,p].filter(Boolean).join(' '))}
  const et=etaText(lv,k);if(et)parts.push(et);
  if(lv['最后动静'])parts.push(k==='stuck'?`${Math.floor(liveAge(lv))} 分钟没动静`:`${liveAgo(lv)}有动静`);
  const acts=k==='stuck'?` <a data-lv="acts">查看</a>${lv['可停止']?' <a data-lv="stop" style="color:var(--warn)">停止</a>':''}`:'';
  const more=ss.length>1?`<details data-k="liveMore" style="display:inline-block;margin-left:6px"><summary class="small mut">展开 ${ss.length} 个会话</summary>${ss.map(liveSess).join('')}</details>`:'';
  return `<span class="small">${parts.join(' · ')}</span>${acts}${more}`;
}
function liveSess(x){
  const a=liveAge(x),q=a===null?'':a<=5?'var(--s2)':a<=15?'var(--mut)':'var(--warn)';
  return `<div class="small" style="padding:2px 0"><span style="color:${q}">●</span> <b>${esc(x['名称'])}</b>${x['子agent']?` · 专家子 agent ${x['子agent']} 个`:''}${x['在做']?' · <span class="mono">'+esc(x['在做'])+'</span>':''}${a!==null?' · '+liveAgo(x):''}</div>`;
}
// 悬停浮框：总进度条 + 每个会话一行
function livePop(lv){
  const [,t,c]=liveTier(lv),pg=lv['进度'],ss=lv['会话'];
  const bar=pg&&pg['总数']?`<div style="height:6px;background:var(--line);border-radius:3px;margin:6px 0"><div style="height:6px;border-radius:3px;background:${c};width:${Math.round(100*pg['完成']/pg['总数'])}%"></div></div>`:'';
  return `<div><b style="color:${c}">${t}</b>${lv['最后动静']?' <span class="mut small">· '+liveAgo(lv)+'有动静</span>':''}</div>
    ${pg?`<div class="small" style="margin-top:4px">进度：${livePg(pg,true)||'—'}${pg['总数']&&pg['阶段']?' · 阶段：'+esc(pg['阶段']):''}${pg['约']?' <span class="mut">（任务总数中途变过）</span>':''}</div>${bar}`:''}
    ${ss.length?`<div class="small mut" style="margin-top:4px">${ss.length} 个会话在跑${lv['AI数']>ss.length?`，另有专家子 agent ${lv['AI数']-ss.length} 个`:''}</div>${ss.map(liveSess).join('')}`:'<div class="small mut" style="margin-top:4px">现在没有 AI 在跑</div>'}
    ${sddPop(lv)}
    ${lv['问题']?`<div class="small" style="margin-top:4px;color:var(--s1)">🔔 ${esc(lv['问题']['编号'])} ${esc(lv['问题']['问题'])}</div>`:''}`;
}
function livePopBind(name){
  let pop=$('#livePop');if(!pop){pop=document.createElement('div');pop.id='livePop';pop.className='livepop';pop.hidden=true;document.body.appendChild(pop)}
  app.querySelectorAll('[data-livepop]').forEach(el=>{if(el._lp)return;el._lp=1;
    el.addEventListener('mouseenter',()=>{const lv=liveCache[name];if(!lv||!liveShow(lv))return;pop.innerHTML=livePop(lv);pop.hidden=false;
      const r=el.getBoundingClientRect();pop.style.left=Math.min(r.left,innerWidth-pop.offsetWidth-12)+'px';pop.style.top=(r.bottom+6)+'px'});
    el.addEventListener('mouseleave',()=>pop.hidden=true)});
}
async function liveBox(name,refetch=true){
  const box=$('#liveBar');if(!box)return;
  if(refetch){try{liveCache[name]=await api('/api/period/live?name='+enc(name))}catch(_){return}}
  const lv=liveCache[name];if(!lv||curPeriod()!==name||!$('#liveBar'))return;
  const b=$('#liveBar'),open=!!(b.querySelector('details[open]'));
  if(!liveShow(lv)){b.hidden=true;b.innerHTML='';const hb=$('#pStBadge');if(hb)hb.innerHTML=liveHead(name,lv['期状态']);return}
  const lt=$('#liveTasks');if(lt){const h=liveTasksHtml(lv);if(lt.innerHTML!==h)lt.innerHTML=h}
  b.hidden=false;b.innerHTML=liveLine(name,lv);if(open){const d=b.querySelector('details');if(d)d.open=true}
  const hb=$('#pStBadge');if(hb)hb.innerHTML=liveHead(name,lv['期状态']);
  b.querySelectorAll('[data-lv]').forEach(a=>a.onclick=async e=>{e.preventDefault();const k=a.dataset.lv;
    if(k==='acts')return allActs(name);
    if(k==='stop'){if(!confirm('停止这一期的开发会话？'))return;try{toast((await post('/api/dev/stop',{period:name})).msg)}catch(x){toast(x.message)}return liveBox(name)}
    if(k==='ask'){const t=app.querySelector('[data-tab="dev"]');if(t&&!t.classList.contains('on'))t.click();
      setTimeout(()=>{const q=lv['问题']&&app.querySelector(`.q[data-q="${CSS.escape(lv['问题']['编号'])}"]`)||$('#devBox');if(q){q.scrollIntoView({behavior:'smooth',block:'center'});const ta=q.querySelector&&q.querySelector('textarea');if(ta)ta.focus({preventScroll:true})}},150)}});
}

// ---- SDD：子进度、子 agent 次数、预计剩余时间（后端 kblib/ai/eta.py；规则见变更说明 角色产物-01）
function liveRound(sub){const s=(sub['步骤']||[]).find(x=>x['状态']==='cur');return s&&s['轮次']>1?`（第 ${s['轮次']} 轮）`:''}
// → 「预计还要 40–70 分钟」；等你回答 / 疑似卡住 / 阻塞时不给时间
function etaText(lv,k){
  const sd=lv['SDD'];if(!sd)return'';k=k||liveTier(lv)[0];
  const o=sd['总体'],ps=o&&o['规划步骤'];
  if(ps&&o['规划']==='进行中')return `规划中 · ${esc(ps['类型'].replace(/^sdd-|-agent$/g,''))} 已 ${ps['已跑']} 分钟${ps['一般']?`（一般约 ${ps['一般']}）`:''}`;
  const e=sd['预估'];if(!e||k==='ask'||k==='stuck'||k==='done')return'';
  if(e['超出'])return `<span style="color:var(--warn)">超出预计，已超 ${e['超出']} 分钟</span>`;
  const r=`${e['约']?'约 ':''}${e['低']===e['高']?e['低']:e['低']+'–'+e['高']} 分钟`;
  return k==='pause'&&lv['恢复时间']?`暂停中，${String(lv['恢复时间']).slice(11,16)} 恢复，之后${r}`:`预计还要 ${r}`;
}
function sddPop(lv){
  const sd=lv['SDD'];if(!sd)return'';const o=sd['总体'],sub=sd['子进度'],a=sd['子agent'],e=sd['预估'];
  const chain=sub?sub['步骤'].map(s=>s['状态']==='done'?`${s['名称']} ✓`:s['状态']==='cur'?`<b>${s['名称']} ▶${sub['已跑']?' 已 '+sub['已跑']+' 分钟':''}${s['轮次']>1?'（第 '+s['轮次']+' 轮）':''}</b>`:`<span class="mut">${s['名称']}</span>`).join(' → '):'';
  return `<div class="small" style="margin-top:6px;border-top:1px solid var(--line);padding-top:6px">
    <div>总体：规划 ${o['规划']==='已完成'?'✓':'▶'} → TASK ${o['完成']}/${o['总数']||'?'} 已完成 → 归档 ${o['归档']==='已完成'?'✓':''}</div>
    ${sub?`<div>当前 ${esc(sub['任务'])}：${chain}${sub['一般']?` <span class="mut">（这一步一般 ${sub['一般']} 分钟）</span>`:''}</div>`:''}
    <div>子 agent：本期已派 <b>${a['已派']}</b> 次${a['在跑'].length?`，正在跑 ${a['在跑'].map(x=>`${esc(x['类型'])}「${esc(x['说明'])}」${x['已跑']} 分钟`).join('、')}`:''}</div>
    ${sd['阻塞']?`<div style="color:var(--warn)">⛔ ${esc(sd['阻塞'])}</div>`:''}
    ${e?`<div class="mut">预估依据：${esc(e['依据'])}；还剩 ${e['剩余TASK']} 个 TASK 没开始</div>`:''}</div>`;
}
// 开发面板「进度」卡片：每个 TASK 一行（实际耗时 = 这个 TASK 所有子 agent 的耗时之和）
function liveTasksHtml(lv){
  const sd=lv&&lv['SDD'];if(!sd||!(sd['任务表']||[]).length)return'';const e=sd['预估'];
  return `<div class="card2"><div class="bd"><div class="small"><b>⏱ 进度</b>　<span class="mut">子 agent 本期已派 ${sd['子agent']['已派']} 次${e?` · ${etaText(lv)||'预计时间暂不显示'}`:''}</span></div>
    <div class="tw" style="margin-top:6px"><table class="small"><tr><th>TASK</th><th>状态</th><th>实际耗时</th><th>子 agent</th><th>返工</th></tr>
    ${sd['任务表'].map(r=>`<tr><td>${esc(r['任务'])}</td><td>${r['状态']==='已完成'?'✓ 已完成':r['状态']==='进行中'?'<b style="color:var(--s2)">▶ 进行中</b>':'<span class="mut">未开始</span>'}</td><td>${r['耗时']?r['耗时']+' 分钟':'—'}</td><td>${r['次数']||'—'}</td><td>${r['返工']||'—'}</td></tr>`).join('')}</table></div>
    <div class="small mut" style="margin-top:4px">预估：单个 TASK 用本期已完成 TASK 的实际耗时（最少～最多；只完成 1 个按 ×0.8～×1.3；没完成按各步中位数 ×1～×1.7，标「约」）× 没开始的 TASK 数 + 当前 TASK 剩下的步骤；返工第 2 轮起上限加一轮；暂停时加上等到恢复的时间。${e?' '+esc(e['依据'])+'。':''}</div></div></div>`;
}
// 总览卡片：圆点 + x/y + N 分钟前
function liveMini(lv){
  if(!lv)return'';const [k,t,c]=liveTier({...lv,安静分钟:5,卡住分钟:15}),p=lv['总数']?`${lv['约']?'约 ':''}${lv['完成']}/${lv['总数']}`:lv['阶段']||'';
  const e=lv['预估'],et=e&&(k==='run'||k==='quiet')?(e['超出']?' · 超出预计':` · 还要${e['约']?'约':''} ${e['低']===e['高']?e['低']:e['低']+'–'+e['高']} 分`):'';
  return `<span class="badge" style="color:${c};border-color:${c}" title="${esc(t)}${lv['AI数']?` · ${lv['AI数']} 个 AI`:''}">${k==='run'||k==='quiet'?'●':t.split(' ')[0]}${p?' '+esc(p):''}${lv['最后动静']?' · '+liveAgo(lv):''}${et}</span>`;
}
const liveStuck=lv=>!!lv&&liveTier({...lv,安静分钟:5,卡住分钟:15})[0]==='stuck';
// 页面停着不动时也要按时间分档：期页面 30 秒重算一次；有 AI 在跑时总览 / 侧栏 60 秒重拉一次
setInterval(()=>{const p=curPeriod();if(p&&$('#liveBar'))liveBox(p)},30000);
setInterval(()=>{if(!(ovPeriods||[]).some(p=>p.live&&p.live['状态']==='运行'))return;
  const h=decodeURIComponent(location.hash.slice(1)||'/');if(h==='/'&&!KB.hooks.nav)softRender();else navRefresh()},60000);
