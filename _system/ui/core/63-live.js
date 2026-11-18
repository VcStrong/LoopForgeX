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
  const p=livePg(pg,true);if(work||p)parts.push([work,p].filter(Boolean).join(' '));
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
  b.hidden=false;b.innerHTML=liveLine(name,lv);if(open){const d=b.querySelector('details');if(d)d.open=true}
  const hb=$('#pStBadge');if(hb)hb.innerHTML=liveHead(name,lv['期状态']);
  b.querySelectorAll('[data-lv]').forEach(a=>a.onclick=async e=>{e.preventDefault();const k=a.dataset.lv;
    if(k==='acts')return allActs(name);
    if(k==='stop'){if(!confirm('停止这一期的开发会话？'))return;try{toast((await post('/api/dev/stop',{period:name})).msg)}catch(x){toast(x.message)}return liveBox(name)}
    if(k==='ask'){const t=app.querySelector('[data-tab="dev"]');if(t&&!t.classList.contains('on'))t.click();
      setTimeout(()=>{const q=lv['问题']&&app.querySelector(`.q[data-q="${CSS.escape(lv['问题']['编号'])}"]`)||$('#devBox');if(q){q.scrollIntoView({behavior:'smooth',block:'center'});const ta=q.querySelector&&q.querySelector('textarea');if(ta)ta.focus({preventScroll:true})}},150)}});
}
// 总览卡片：圆点 + x/y + N 分钟前
function liveMini(lv){
  if(!lv)return'';const [k,t,c]=liveTier({...lv,安静分钟:5,卡住分钟:15}),p=lv['总数']?`${lv['约']?'约 ':''}${lv['完成']}/${lv['总数']}`:lv['阶段']||'';
  return `<span class="badge" style="color:${c};border-color:${c}" title="${esc(t)}${lv['AI数']?` · ${lv['AI数']} 个 AI`:''}">${k==='run'||k==='quiet'?'●':t.split(' ')[0]}${p?' '+esc(p):''}${lv['最后动静']?' · '+liveAgo(lv):''}</span>`;
}
const liveStuck=lv=>!!lv&&liveTier({...lv,安静分钟:5,卡住分钟:15})[0]==='stuck';
// 页面停着不动时也要按时间分档：期页面 30 秒重算一次；有 AI 在跑时总览 / 侧栏 60 秒重拉一次
setInterval(()=>{const p=curPeriod();if(p&&$('#liveBar'))liveBox(p)},30000);
setInterval(()=>{if(!(ovPeriods||[]).some(p=>p.live&&p.live['状态']==='运行'))return;
  const h=decodeURIComponent(location.hash.slice(1)||'/');if(h==='/'&&!KB.hooks.nav)softRender();else navRefresh()},60000);
