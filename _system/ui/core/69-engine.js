// ==== AI 引擎能不能用：在每套界面的「实时连接」小圆点旁边常驻一个「AI ●」，点开看各引擎状态、重新检测
let engH=null;
const ENG_C={可用:'#16a34a',不可用:'#dc2626',部分不可用:'#f59e0b',未检测:'#94a3b8'};
function engPill(){
  const live=$('#live');if(!live)return null;
  let b=$('#aiSt');
  if(!b){b=document.createElement('button');b.id='aiSt';b.type='button';
    b.style.cssText='display:inline-flex;align-items:center;gap:4px;padding:1px 7px;margin-left:6px;border:1px solid var(--line,#e5e7eb);border-radius:999px;background:transparent;font-size:11px;line-height:16px;cursor:pointer;color:inherit;flex:none;width:auto;height:auto';
    live.insertAdjacentElement('afterend',b);b.onclick=engDlg}
  return b;
}
function engRender(){
  const b=engPill();if(!b||!engH)return;
  const st=engH['总体']||'未检测',c=ENG_C[st]||ENG_C['未检测'];
  b.innerHTML=`<i style="width:7px;height:7px;border-radius:50%;background:${c};display:inline-block"></i>AI`;
  b.setAttribute('aria-label','AI 引擎：'+st);
  b.title=`AI 引擎：${st}（默认 ${engH['默认']||''}）\n`+(engH['引擎']||[]).filter(x=>x['在用']||x['状态']!=='未检测').map(x=>`${x['名称']}：${x['状态']}${x['原因']?' — '+x['原因']:''}${x['时间']?'（'+x['时间']+' · '+(x['来源']||'')+'）':''}`).join('\n')+'\n点开看详情、重新检测';
}
async function engLoad(){try{engH=await api('/api/engines/health');engRender();if($('#engList'))engList()}catch(_){}}
function engList(){
  const L=$('#engList');if(!L||!engH)return;
  const rows=(engH['引擎']||[]).filter(x=>x['在用']||x['状态']!=='未检测');
  L.innerHTML=`<table><tr><th>引擎</th><th style="width:90px">状态</th><th>说明</th><th style="width:90px"></th></tr>${rows.map(x=>`<tr>
    <td><b>${esc(x['名称'])}</b>${x['默认']?' <span class="badge">默认</span>':''}${x['在用']?'':' <span class="mut small">没在用</span>'}</td>
    <td><span style="color:${ENG_C[x['状态']]||ENG_C['未检测']};font-weight:600">● ${esc(x['状态'])}</span></td>
    <td class="small">${x['原因']?`<div style="color:var(--s4,#dc2626)">${esc(x['原因'])}</div>`:''}<div class="mut">${x['时间']?esc(x['时间'])+' · 来自'+esc(x['来源']||''):'还没有结果：等下一次会话结束或点「检测」'}${x['上次失败']?' · 上次失败：'+esc(x['上次失败']):''}</div></td>
    <td><button class="small" data-eprobe="${esc(x.id)}">检测</button></td></tr>`).join('')}</table>`;
  L.querySelectorAll('[data-eprobe]').forEach(b=>b.onclick=async()=>{b.disabled=true;b.textContent='检测中…';
    try{const r=await post('/api/engine/probe',{id:b.dataset.eprobe});toast(`${r['名称']}：${r['状态']}${r['原因']?' — '+r['原因']:''}（${r['耗时']} 秒）`)}catch(e){toast(e.message)}
    await engLoad()});
}
function engDlg(){
  openDlg('AI 引擎状态',`<div class="form"><p class="small mut" style="margin:0">整个平台的后台工作（开发、全栈、Bug 分析、管家、资料整理）都靠 AI 引擎。每次会话结束会自动更新状态（不额外花钱）；6 小时没有会话时自动做一次极短的自检（只回复 OK）。
    不可用时会弹系统通知。</p><div id="engList" class="tw"></div>
    <div class="row" style="gap:8px"><a href="#/settings" onclick="dlg.close()">去设置 AI 引擎 →</a><span class="small mut">没登录：在终端运行对应命令登录（如 claude 后输入 /login）；额度 / 限流：等一会或换引擎</span></div></div>`);
  engList();
}
function engInit(){engLoad();KB.on('engine',engLoad);KB.on('*',()=>{if(engH&&!$('#aiSt'))engRender()});KB.on('live',ev=>{if(ev.on)engLoad()});setInterval(engLoad,300000)}
