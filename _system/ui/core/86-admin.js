// 「📊 总览」管理员看板（T002）：全员执行进度、项目进展、账号、执行机、AI 账号、队列、资源、告警、审计、定时任务。
// 数据：GET /api/admin/overview（只给管理员）；图表：Apache ECharts（ui/vendor/echarts.min.js，打开本页才加载，不走外网）。
// 配色：分类色固定顺序 4 个槽（已用 dataviz 校验脚本在本站卡片底色上跑过：浅色 / 深色都过）；状态色只表示状态、配文字。
// 每张图旁边都有表格（浅色下两种分类色对比度不足 3:1，表格是替代读法）。30 秒自动刷新，任务 / 执行机事件来了也刷新。
const ADM_SLOTS={light:['#2a78d6','#eb6834','#1baf7a','#eda100'],dark:['#3987e5','#d95926','#199e70','#c98500']};
const ADM_STATUS={good:'#0ca30c',warning:'#fab219',serious:'#ec835a',critical:'#d03b3b'};
let admCharts=[],admTimer=null,admLast=0;
const admDark=()=>{const t=document.documentElement.dataset.theme;return t?t==='dark':matchMedia('(prefers-color-scheme: dark)').matches};
const admCss=n=>getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const admPct=v=>v==null||isNaN(v)?'—':Math.round(v*100)+'%';
const admNum=v=>v==null?'—':Number(v).toLocaleString('zh-CN');
function admBase(){const fg=admCss('--fg')||'#1c2433',mut=admCss('--mut')||'#667085',line=admCss('--line')||'#e3e7ef';
  return {color:ADM_SLOTS[admDark()?'dark':'light'],textStyle:{color:fg,fontFamily:'inherit'},animation:false,
    grid:{left:44,right:16,top:28,bottom:28,containLabel:true},
    tooltip:{trigger:'axis',backgroundColor:admCss('--card')||'#fff',borderColor:line,textStyle:{color:fg},axisPointer:{type:'line',lineStyle:{color:mut}}},
    legend:{top:0,textStyle:{color:mut},itemWidth:12,itemHeight:8},
    xAxis:{axisLine:{lineStyle:{color:line}},axisTick:{show:false},axisLabel:{color:mut},splitLine:{show:false}},
    yAxis:{axisLine:{show:false},axisTick:{show:false},axisLabel:{color:mut},splitLine:{lineStyle:{color:line,opacity:.6}}}}}
function admChart(id,opt){const el=document.getElementById(id);if(!el||!window.echarts)return;
  const b=admBase(),c=echarts.init(el,null,{renderer:'svg'});
  c.setOption({...b,...opt,xAxis:{...b.xAxis,...(opt.xAxis||{})},yAxis:{...b.yAxis,...(opt.yAxis||{})},grid:{...b.grid,...(opt.grid||{})},
    tooltip:{...b.tooltip,...(opt.tooltip||{})},legend:opt.legend===false?{show:false}:{...b.legend,...(opt.legend||{})}});admCharts.push(c)}
function admDispose(){admCharts.forEach(c=>{try{c.dispose()}catch(_){}});admCharts=[]}
addEventListener('resize',()=>admCharts.forEach(c=>c.resize()));
const admLine=(w=2)=>({type:'line',showSymbol:false,symbolSize:8,lineStyle:{width:w},emphasis:{focus:'series'}});
const admBar={type:'bar',barMaxWidth:22,itemStyle:{borderRadius:[4,4,0,0],borderColor:'transparent'}};
const admTile=(k,v,sub,tone)=>`<div class="stat adm-tile"${tone?` data-tone="${tone}"`:''}><div class="small mut">${esc(k)}</div><b>${v}</b>${sub?`<div class="small mut">${sub}</div>`:''}</div>`;
const admSec=(t,sub,body)=>`<section class="adm-sec"><h2>${t}${sub?` <span class="small mut" style="font-weight:400">${sub}</span>`:''}</h2>${body}</section>`;
const admErr=x=>x&&x.error?`<div class="kb-state small">这一块暂时取不到<span class="mut">（${esc(x.error)}）</span></div>`:'';
const admTbl=(head,rows,empty)=>rows.length?`<div class="tw"><table><tr>${head.map(h=>`<th>${esc(h)}</th>`).join('')}</tr>${rows.join('')}</table></div>`:`<div class="kb-state small">${esc(empty)}</div>`;
function admStatusPill(s){const m={正常:'ok',在线:'ok',可用:'ok',待改密码:'wait',冷却:'wait',排空中:'run',锁定:'bad',停用:'bad',离线:'bad',不可用:'bad',严重:'bad',警告:'wait'};
  return `<span class="plat-st ${m[s]||''}">${esc(s||'—')}</span>`}

async function adminPage(){
  const head=platHead('📊 总览看板','全员执行进度、项目进展、执行机、AI 账号、队列、资源、告警 · 只有管理员能看',
    `<span class="small mut" id="admAt"></span><button id="admRe">刷新</button>`);
  if(KB.me&&KB.me['系统角色']!=='管理员'){app.innerHTML=head+`<div class="kb-state"><b>没有权限</b><p class="mut">只有管理员能看总览</p></div>`;return}
  let d;
  const ok=await platPage(head,async()=>{const [x]=await Promise.all([api('/api/admin/overview'),loadVendor('echarts').catch(()=>null)]);d=x;return admHtml(d)});
  if(!ok)return;
  admDispose();admDraw(d);admLast=Date.now();
  $('#admAt').textContent='更新于 '+d['时间']+(window.echarts?'':'（图表库没加载出来，只显示表格）');
  $('#admRe').onclick=()=>adminPage();
  app.querySelectorAll('[data-adm-period]').forEach(a=>a.onclick=()=>{location.hash='#/p/'+enc(a.dataset.admPeriod)});
  clearInterval(admTimer);admTimer=setInterval(()=>{if(location.hash!=='#/admin'){clearInterval(admTimer);admDispose();return}
    if(document.visibilityState==='visible'&&canRender())adminPage()},30000);
}
function admHtml(d){
  const s=d['概要']||{},q=d['队列']||{},r=d['执行机']||{},ai=d['AI']||{},sy=d['系统']||{},au=d['审计']||{},pr=d['项目']||{};
  const accs=ai['账号']||[],okAcc=accs.filter(a=>a['状态']==='可用').length;
  const tiles=[admTile('活跃成员',`${s['活跃成员']??'—'} / ${s['成员']??'—'}`,'有任务在跑 / 排队 / 等回答'),
    admTile('在跑任务',admNum(q['运行']),`排队 ${admNum(q['排队'])} · 最久等 ${q['最长已等分钟']??0} 分钟`),
    admTile('执行机在线',`${r['在线']??'—'} / ${r['总数']??'—'}`,r['离线']?`离线 ${r['离线']} 台`:'本机执行机另算',r['离线']?'bad':''),
    admTile('进行中告警',admNum(s['告警']),'执行机 CPU / 内存 / 磁盘 / 网络 / 心跳',s['告警']?'bad':''),
    admTile('AI 账号可用',ai['本机登录态模式']?'本机登录态':`${okAcc} / ${accs.length}`,`今日 token ${admNum(ai['今日token'])}`,accs.length&&okAcc<accs.length?'wait':''),
    admTile('近 7 天失败率',admPct(q['近7天失败率']),`完成 ${admNum(q['近7天完成'])} · 失败 ${admNum(q['近7天失败'])} · 重试 ${admNum(q['近7天重试次数'])}`,q['近7天失败率']>0.2?'bad':''),
    admTile('机密读取（7 天）',admNum(au['机密读取']),`拒绝 ${admNum(au['机密拒绝'])} 次`,au['机密拒绝']?'wait':''),
    admTile('系统',esc(sy['数据库']||'—'),`版本 ${esc(sy['版本']||'—')} · 端口 ${esc(sy['端口']||'—')} · 数据盘 ${admPct(sy['数据目录磁盘使用比'])}`,sy['数据目录磁盘使用比']>0.85?'bad':'')];
  const ppl=Array.isArray(d['人'])?d['人']:[];
  const people=admErr(d['人'])||admTbl(['成员','角色','账号状态','最近登录','在跑','等回答','排队','近 7 天完成','近 7 天失败','进行中'],
    ppl.map(p=>`<tr><td><b>${esc(p['用户'])}</b>${p['本机用户']?' <span class="small mut">本机</span>':''}</td><td class="small">${esc(p['角色'])}</td><td>${admStatusPill(p['状态'])}</td>
      <td class="small">${esc(p['最近登录']||'—')}</td><td>${p['在跑']}</td><td>${p['等回答']}</td><td>${p['排队']}</td><td>${p['近7天完成']}</td><td>${p['近7天失败']||''}</td>
      <td class="small">${(p['进行中']||[]).map(t=>`<a data-adm-period="${esc(t['期'])}" title="${esc(t['任务'])}">${esc(t['期']||t['类型'])}</a>·${esc(t['状态'])}`).join('　')||'<span class="mut">—</span>'}</td></tr>`),'还没有成员');
  const projs=pr['项目']||[],pers=pr['期']||[];
  const projTbl=admErr(pr)||admTbl(['项目','负责人','成员','各状态','近 7 天记录'],projs.map(p=>`<tr><td><b>${esc(p['名称'])}</b></td><td>${esc(p['负责人']||'—')}</td><td>${p['成员数']}</td>
    <td class="small">${Object.entries(p['状态分布']||{}).map(([k,v])=>`${esc(k)} ${v}`).join('　')||'—'}</td><td>${p['近7天记录']}</td></tr>`),'还没有项目');
  const perTbl=admTbl(['期 / 事项','项目','负责人','状态','待确认','待处理问题','未解决 Bug','近 7 天记录','更新'],pers.slice(0,40).map(p=>`<tr><td><a data-adm-period="${esc(p['名称'])}">${esc(p['名称'])}</a>${p['类型']==='事项'?' <span class="small mut">事项</span>':''}${p['会话在跑']?' <span class="plat-st run">会话在跑</span>':''}</td>
    <td class="small">${esc(p['项目'])}</td><td class="small">${esc(p['负责人']||'—')}</td><td class="small">${esc(p['状态'])}</td><td>${p['待确认']||''}</td><td>${p['待处理问题']||''}</td><td>${p['未解决Bug']||''}</td><td>${p['近7天记录']||''}</td><td class="small mut">${esc(p['更新']||'')}</td></tr>`),'还没有期或事项');
  const rl=r['列表']||[];
  const runTbl=admErr(r)||admTbl(['执行机','状态','CPU','内存可用','磁盘','在跑/容量','告警','心跳','版本'],rl.map(x=>`<tr><td><b>${esc(x['名称'])}</b>${x['本机']?' <span class="small mut">本机</span>':''}${(x['网络差']||[]).length?' <span class="plat-st wait">网络差</span>':''}</td>
    <td>${admStatusPill(x['状态'])}</td><td>${admPct(x['CPU'])}</td><td>${admPct(x['内存可用比'])}</td><td>${admPct(x['磁盘使用比'])}</td><td>${x['在跑']} / ${x['容量']}</td><td>${x['告警']||''}</td><td class="small mut">${esc((x['最后心跳']||'').slice(11))}</td><td class="small mono" title="${esc(x['版本']||'')}">${esc((x['版本']||'').slice(0,7))}</td></tr>`),'还没有执行机：到「🖥 执行机」生成注册令牌接入');
  const al=r['告警']||[];
  const alTbl=admTbl(['执行机','类型','级别','开始','说明'],al.map(a=>`<tr><td>${esc(a['执行机'])}</td><td>${esc(a['类型'])}</td><td>${admStatusPill(a['级别'])}${a['已确认']?' <span class="small mut">已确认</span>':''}</td><td class="small">${esc(a['开始'])}</td><td class="small">${esc(a['说明'])}</td></tr>`),'没有进行中的告警');
  const aiTbl=admErr(ai)||(ai['本机登录态模式']?`<div class="kb-state small">还没登记 AI 账号：会话用各机器本机的登录态。到「⚙ 设置 → 🤖 AI 账号」登记后这里显示健康和用量</div>`:
    admTbl(['账号','引擎','类型','项目','状态','并发','RPM（近 1 分钟）','原因'],accs.map(a=>`<tr><td><b>${esc(a['名称'])}</b></td><td>${esc(a['引擎'])}</td><td class="small">${esc(a['类型'])}</td><td class="small">${esc(a['项目']||'—')}</td>
      <td>${admStatusPill(a['状态'])}${a['冷却到']?` <span class="small mut">到 ${esc(a['冷却到'])}</span>`:''}</td><td>${a['运行中']} / ${a['并发上限']}（${admPct(a['并发使用率'])}）</td><td>${admPct(a['RPM使用率'])} / ${a['RPM上限']}</td><td class="small">${esc(a['原因']||'')}</td></tr>`),''));
  const bud=(ai['预算']||[]);
  const budTbl=bud.length?admTbl(['项目','已用（日 / 月较高者）','日预算','月预算'],bud.map(b=>`<tr><td>${esc(b['项目'])}</td><td>${admPct(b['已用比'])}${b['估算']?' <span class="small mut">含估算</span>':''}</td><td>${admNum(b['预算日'])}</td><td>${admNum(b['预算月'])}</td></tr>`),''):'<div class="small mut">还没设预算（0 = 不限，只统计）</div>';
  const res=d['资源']||{},rk=Object.keys(res);
  const resTbl=admErr(res)||admTbl(['资源','总数','占用中','占用率','排队','不可用'],rk.map(k=>`<tr><td><b>${esc(k)}</b></td><td>${res[k]['总数']}</td><td>${res[k]['占用中']}</td><td>${admPct(res[k]['占用率'])}</td><td>${res[k]['排队']}</td><td>${res[k]['不可用']||''}</td></tr>`),'还没有手机 / 测试账号 / 引擎名额');
  const why=(q['等待原因']||[]),qt=q['按类型']||{};
  const qTbl=admErr(q)||`<div class="row small" style="gap:18px;flex-wrap:wrap;margin-bottom:6px"><span>平均已等 <b>${q['平均已等分钟']}</b> 分钟</span><span>最久 <b>${q['最长已等分钟']}</b> 分钟</span></div>`+
    admTbl(['在等什么','任务数'],why.map(([k,v])=>`<tr><td>${esc(k)}</td><td>${v}</td></tr>`),'现在没有排队的任务')+
    (Object.keys(qt).length?admTbl(['类型（近 7 天）','完成','失败','进行中 / 其他'],Object.entries(qt).map(([k,v])=>`<tr><td>${esc(k)}</td><td>${v['完成']}</td><td>${v['失败']||''}</td><td>${v['其他']||''}</td></tr>`),''):'');
  const sch=Array.isArray(d['定时任务'])?d['定时任务']:[];
  const schTbl=admErr(d['定时任务'])||admTbl(['定时任务','周期','上次运行','结果','持有者','次数'],sch.map(x=>`<tr><td>${esc(x['名称'])}</td><td class="small">${Math.round(x['周期秒'])} 秒</td><td class="small">${esc(x['上次运行']||'—')}</td><td class="small">${esc(x['结果']||'')}</td><td class="small mono">${esc(x['持有者']||'')}</td><td>${x['次数']}</td></tr>`),'还没有定时任务跑过');
  const auk=au['按类型']||{};
  const auTbl=admErr(au)||admTbl(['类型（近 7 天）','成功','失败 / 拒绝'],Object.entries(auk).map(([k,v])=>`<tr><td>${esc(k)}</td><td>${v['成功']}</td><td>${v['失败']||''}</td></tr>`),'近 7 天没有审计记录');
  const chart=(id,h=220)=>`<div id="${id}" class="adm-chart" style="height:${h}px"></div>`;
  return `<div class="adm-tiles">${tiles.join('')}</div>
  ${admSec('👥 成员执行进度','每人在跑、排队、等回答的任务，近 7 天结果',people)}
  <div class="adm-two">${admSec('🗂 项目进展','各项目的期 / 事项状态',chart('admProj',Math.max(160,projs.length*42+60))+projTbl)}
    ${admSec('📈 近 14 天推进','每天的记录条数（决策、资料、开发、验收…）',chart('admRec')+admTbl(['日期','记录'],(pr['推进趋势']||[]).slice(-7).map(x=>`<tr><td>${esc(x['日期'])}</td><td>${x['记录']}</td></tr>`),''))}</div>
  ${admSec('📋 期 / 事项',`共 ${pr['期总数']??0} 个；按进行中、近 7 天活跃排序，最多显示 40 个`,perTbl)}
  <div class="adm-two">${admSec('🖥 执行机',`在线 ${r['在线']??0} / ${r['总数']??0}；CPU 负载 = 1 分钟负载 / 核数`,chart('admCpu')+runTbl)}
    ${admSec('🚨 进行中的告警','',alTbl)}</div>
  <div class="adm-two">${admSec('🤖 AI 账号','健康、并发和请求频次使用率',aiTbl+'<h3 class="small" style="margin:10px 0 4px">预算</h3>'+budTbl)}
    ${admSec('🔢 近 30 天 token','每天输入 / 输出 token；引擎没报的按时长估算',chart('admTok')+`<div class="small mut">今日 ${admNum(ai['今日token'])} · 本月 ${admNum(ai['本月token'])}</div>`)}</div>
  <div class="adm-two">${admSec('☰ 任务队列',`排队 ${q['排队']??0} · 在跑 ${q['运行']??0}`,qTbl)}${admSec('📦 资源池','手机、测试账号、引擎名额',chart('admRes',170)+resTbl)}</div>
  <div class="adm-two">${admSec('🔐 安全审计','近 7 天',auTbl)}${admSec('⏱ 定时任务','租约保证只跑一份；持有者 = 哪个进程在跑',schTbl)}</div>`;
}
function admDraw(d){
  if(!window.echarts)return;
  const pr=d['项目']||{},projs=pr['项目']||[];
  if(projs.length){                                    // 状态分布：前 3 种状态各一色，其余并成「其他」（固定 4 色，不循环）
    const tot={};projs.forEach(p=>Object.entries(p['状态分布']||{}).forEach(([k,v])=>tot[k]=(tot[k]||0)+v));
    const top=Object.keys(tot).sort((a,b)=>tot[b]-tot[a]).slice(0,3),keys=[...top,...(Object.keys(tot).length>3?['其他']:[])];
    const val=(p,k)=>k==='其他'?Object.entries(p['状态分布']||{}).filter(([s])=>!top.includes(s)).reduce((a,[,v])=>a+v,0):(p['状态分布']||{})[k]||0;
    admChart('admProj',{tooltip:{trigger:'axis',axisPointer:{type:'shadow'}},grid:{left:8,top:30},
      xAxis:{type:'value',minInterval:1},yAxis:{type:'category',data:projs.map(p=>p['名称']),inverse:true,splitLine:{show:false}},
      series:keys.map(k=>({...admBar,name:k,stack:'s',barMaxWidth:18,itemStyle:{borderColor:admCss('--card')||'#fff',borderWidth:1},data:projs.map(p=>val(p,k))}))})}
  const rec=pr['推进趋势']||[];
  if(rec.length)admChart('admRec',{legend:false,xAxis:{type:'category',data:rec.map(x=>x['日期'].slice(5)),boundaryGap:false},yAxis:{type:'value',minInterval:1},
    series:[{...admLine(),name:'记录',data:rec.map(x=>x['记录']),areaStyle:{opacity:.08}}]});
  const tr=(d['执行机']||{})['趋势']||{};                // CPU：最多画 3 台（全部在下面的表里）
  const names=Object.keys(tr).sort((a,b)=>{const m=n=>Math.max(0,...tr[n].map(p=>p['CPU']||0));return m(b)-m(a)}).slice(0,3);
  const el=document.getElementById('admCpu');
  if(names.length&&names.some(n=>tr[n].length)){
    const ts=[...new Set(names.flatMap(n=>tr[n].map(p=>p['时间'])))].sort((a,b)=>a-b);
    admChart('admCpu',{legend:names.length>1?{}:false,xAxis:{type:'category',data:ts.map(t=>new Date(t*1000).toLocaleTimeString('zh-CN',{hour12:false,hour:'2-digit',minute:'2-digit'})),boundaryGap:false},
      yAxis:{type:'value',axisLabel:{formatter:v=>Math.round(v*100)+'%'}},tooltip:{valueFormatter:v=>admPct(v)},
      series:names.map(n=>{const m=Object.fromEntries(tr[n].map(p=>[p['时间'],p['CPU']]));return {...admLine(),showSymbol:ts.length<4,name:n,connectNulls:true,data:ts.map(t=>m[t]??null),
        markLine:{silent:true,symbol:'none',label:{formatter:'告警线 150%',color:admCss('--mut')},lineStyle:{color:ADM_STATUS.serious,type:'dashed',width:1},data:[{yAxis:1.5}]}}})})}
  else if(el)el.outerHTML='<div class="kb-state small">远程执行机还没有 24 小时指标（刚接入或都离线）</div>';
  const tok=(d['AI']||{})['趋势']||[];
  if(tok.some(x=>x['输入']||x['输出']))admChart('admTok',{tooltip:{trigger:'axis',axisPointer:{type:'shadow'},valueFormatter:admNum},
    xAxis:{type:'category',data:tok.map(x=>x['日期'].slice(5)+(x['估算']?'*':''))},yAxis:{type:'value'},
    series:[{...admBar,name:'输入',stack:'t',itemStyle:{borderRadius:0}},{...admBar,name:'输出',stack:'t'}].map((s,i)=>({...s,data:tok.map(x=>i?x['输出']:x['输入'])}))});
  else{const e=document.getElementById('admTok');if(e)e.outerHTML='<div class="kb-state small">近 30 天还没有 AI 用量记录</div>'}
  const res=d['资源']||{},rk=Object.keys(res).filter(k=>!res[k].error);
  if(rk.length)admChart('admRes',{legend:false,tooltip:{trigger:'axis',axisPointer:{type:'shadow'},valueFormatter:admPct},grid:{top:12},
    xAxis:{type:'category',data:rk},yAxis:{type:'value',max:1,axisLabel:{formatter:v=>Math.round(v*100)+'%'}},
    series:[{...admBar,name:'占用率',data:rk.map(k=>res[k]['占用率']),label:{show:true,position:'top',color:admCss('--fg'),formatter:p=>admPct(p.value)}}]});
  else{const e=document.getElementById('admRes');if(e)e.remove()}
}
function adminOnEvt(ev,h){if(h!=='/admin')return;const k=ev.kind||ev.type;if(['task','runner','alert','resource'].includes(k)&&Date.now()-admLast>5000&&canRender())adminPage()}
Object.assign(KB.routes,{'/admin':adminPage});
Object.assign(KB.pages,{admin:adminPage});
