// 「自动推远程」开关（开发面板 / 全栈 / 事项共用）：v 是 /api/dev 或 /api/stack 的返回；总开关关着时置灰不能点
function apSw(v,id){const a=v['自动推送']||{开:true,总开关:true},g=a['总开关']!==false,r=a['最近'];   // 接口没带这个字段（服务还没重启）按默认值：开
  const tip=g?`这一期改完代码后由知识库推到远端（会话正常结束且有新提交 / 全栈某端审查通过 / Bug 修复合回）；关掉 = 只在本地提交${r?'。最近：'+(r['时间']+' '+r['时机']+' '+r['结果']+(r['原因']?'：'+r['原因']:r['sha']?' '+r['sha']:'')):''}`
    :'「⚙ 设置 → 常用 → 自动推送」总开关关着，所有期都只在本地提交';
  return `<span class="sw ${g&&a['开']?'on':''}" id="${id}" title="${esc(tip)}" ${g?'':'style="opacity:.5;cursor:not-allowed"'}><i></i>自动推远程${g&&r&&r['结果']==='失败'?' <b style="color:var(--warn)">上次失败</b>':''}</span> `}
function apBind(name,id,v,re){const a=v['自动推送']||{开:true,总开关:true},el=$('#'+id);if(!el||a['总开关']===false)return;el.onclick=async()=>{try{toast((await post('/api/autopush',{period:name,on:!a['开']})).msg)}catch(e){toast(e.message)}re()}}
// ==== 期页面对话框、开发面板、开始开发
// ---------------- 本期对话：期页面最上方的输入框 + 这一期所有会话的最近动作
let tkTarget={};const tkStaged={};   // 每一期「跟这一期说」已附的文件（还没发出去）
function talkBind(name){
  const mt=$('#tkMsg');if(!mt)return;
  mt.oninput=()=>drafts['tk:'+name]=mt.value;
  const st=tkStaged[name]=tkStaged[name]||[],show=()=>stagedShow($('#tkFiles'),st);show();
  const tf=$('#tkFile');if(tf)tf.onchange=e=>{stageFiles([...e.target.files],st,show);e.target.value=''};   // 期页面、事项页共用：页面上没有 📎 时跳过
  mt.addEventListener('paste',e=>{const fs=[...(e.clipboardData||{}).files||[]];if(fs.length){e.preventDefault();stageFiles(fs,st,show)}});
  const card=mt.closest('.talk');if(card&&!card._tkDrop){card._tkDrop=1;
    card.addEventListener('dragover',e=>{if([...(e.dataTransfer||{}).types||[]].includes('Files')){e.preventDefault();card.classList.add('tkdrop')}});
    card.addEventListener('dragleave',()=>card.classList.remove('tkdrop'));
    card.addEventListener('drop',async e=>{if(![...(e.dataTransfer||{}).types||[]].includes('Files'))return;e.preventDefault();card.classList.remove('tkdrop');
      const fs=await dropFiles(e,{n:2000,bytes:2*1024**3});if(fs&&fs.length)stageFiles(fs,tkStaged[curPeriod()]||st,()=>stagedShow($('#tkFiles'),tkStaged[curPeriod()]||st))})}
  const send=async()=>{const t=mt.value.trim();if(!t&&!st.length)return;if(stagedBusy(st))return toast('文件还在上传，传完再发');const b=$('#tkSend'),sel=$('#tkSel');
    const mode=(document.querySelector('input[name=tkM]:checked')||{}).value||'insert';
    const target=sel?sel.value:'';if(mode==='insert'&&sel&&!target)return toast('选一下发给哪个会话');
    b.disabled=true;
    try{const r=await post('/api/period/say',{period:name,text:t,target,mode,interrupt:!$('#tkWait').checked,files:st.map(f=>f.id)});delete drafts['tk:'+name];mt.value='';st.length=0;show();$('#tkWait').checked=false;toast(r.msg);talkBox(name)}
    catch(e){toast(e.message)}finally{b.disabled=false}};
  document.querySelectorAll('input[name=tkM]').forEach(r=>r.onchange=()=>talkBox(name));$('#tkWait').onchange=()=>talkBox(name);
  $('#tkSend').onclick=send;mt.onkeydown=e=>{if(e.key==='Enter'&&(e.metaKey||e.ctrlKey))send()};
  $('#tkAll').onclick=e=>{e.preventDefault();e.stopPropagation();allActs(name)};
  talkBox(name);
}
async function talkBox(name){
  if(!$('#tkMsg'))return;
  let v;try{v=await api('/api/period/talk?name='+enc(name))}catch(e){$('#tkHint').textContent=e.message;return}
  if(!$('#tkMsg')||curPeriod()!==name)return;
  const ss=v['会话'],to=$('#tkTo'),sel0=$('#tkSel');
  $('#tkMode').hidden=!ss.length;
  const mode=ss.length?((document.querySelector('input[name=tkM]:checked')||{}).value||'insert'):'push';
  if(!(sel0&&document.activeElement===sel0)){
    if(mode!=='insert')to.innerHTML='';
    else if(ss.length>1){const keys=ss.map(x=>x['键']),cur=tkTarget[name];
      const def=cur&&(cur==='all'||keys.includes(cur))?cur:keys.includes('dev')?'dev':'all';
      to.innerHTML=`→ <select id="tkSel">${ss.map(x=>`<option value="${esc(x['键'])}" ${x['键']===def?'selected':''}>${esc(x['名称'])}</option>`).join('')}<option value="all" ${def==='all'?'selected':''}>全部会话（${ss.length} 个）</option></select>`;
      $('#tkSel').onchange=()=>{tkTarget[name]=$('#tkSel').value;talkBox(name)}}
    else to.innerHTML=`→ <b>${esc(ss[0]['名称'])}</b>`;
  }
  const sel=$('#tkSel'),k=mode!=='insert'?'':sel?sel.value:ss.length?ss[0]['键']:'',toDev=k==='dev'||(k==='all'&&ss.some(x=>x['键']==='dev'));
  $('#tkIntL').hidden=!toDev;
  const q=v['排队']?`（已有 ${v['排队']} 条在排队）`:'';
  $('#tkHint').textContent=mode==='push'&&v['事项']?'没有会话在跑：发送后 AI 接着上次的上下文继续（如编译校验、提交），不走开发流程，要改代码会先问你'
    :mode==='push'?(v['状态']==='收集中'?'没有会话在跑，这一期还在收集中：发送的内容记进「需求」文字资料'
      :'没有会话在跑：发送后 AI 接着这一期上次的会话（保留上下文）按内容推动解决，要改代码按本期开发约定改、自测、提交，有取舍会先问你'+q)
    :mode==='new'?'另起一个新会话并行执行（不接上次的上下文）；和正在跑的会话改同一个仓库时，它只改、只提交自己需要的文件'
    :mode==='queue'?'先记下，这一期正在跑的会话都结束后，自动接着上次的会话按顺序处理'+q
    :toDev&&!$('#tkWait').checked?'立即插入：中止开发会话正在跑的这一步（如编译、测试命令），带着你的补充接着这次对话做'+(k==='all'?'；其他会话下次调用知识库工具时收到':'')
    :toDev?'这一步做完、下次调用知识库工具时收到（等你确认时立刻收到）；这一轮结束还没送到的，会带着它接着做，不会落空'
    :'Bug 分析 / 修复、新会话不能打断：下次调用知识库工具时收到';
  $('#tkMsgs').innerHTML=(v['对话']||[]).slice(-4).map(x=>`<div class="msg">🧑 ${esc(x['时间'])} · ${esc(x['目标']||x['方式'])} · ${x['状态']==='待送达'?'<span style="color:var(--s1)">等会话下次调用知识库工具时送达</span>':x['状态']==='排队中'?'<span style="color:var(--s1)">排队中：会话都结束后处理</span>':esc({推动解决:'已开始推动',新会话执行:'已启动新会话'}[x['方式']]||(x['方式']==='打断'?'已插入':'已送达'))}　<b title="${esc(x['内容'])}">${esc(x['显示']||(x['内容'].split('\n')[0].length>120||x['内容'].includes('\n')?x['内容'].split('\n')[0].slice(0,120)+'…':x['内容']))}</b></div>`).join('');
  const gs=v['动作'],box=$('#tkActs'),D=$('#tkActD');
  $('#tkActN').textContent=gs.length?`（${gs.length} 个会话${gs.some(g=>g['运行中'])?'，有会话在跑':''}）`:'（暂无）';
  if(!D.dataset.init){D.dataset.init=1;D.open=gs.some(g=>g['运行中'])}
  if(box.matches(':hover'))return;      // 正在看，先不刷新
  box.innerHTML=gs.map(g=>`<div class="sess">── ${esc(g['时间'])} · ${esc(g['名称'])}${g['运行中']?' · <span style="color:var(--s1)">● 运行中</span>':''}${g['参与']?' · '+esc(g['参与']):''} ──</div>${g['动作'].slice().reverse().map(actLine).join('')}`).join('')||'<span class="mut small">还没有会话动作</span>';
}
const STEP={done:['done','✓ 完成'],skipped:['skip','跳过'],pending:['','未开始']};
const fileLink=(p,t)=>p?`<a data-rf="${esc(p)}">${esc(t||p.split('/').pop())}</a>`:'';
function since(t){const ms=Date.now()-new Date(String(t).replace(' ','T')).getTime();if(!(ms>0))return'';const m=Math.floor(ms/60000);return m<60?m+' 分钟':Math.floor(m/60)+' 小时 '+(m%60)+' 分钟'}
async function devBox(name){
  const box=$('#devBox');if(!box)return;
  let v;try{v=await api('/api/dev?name='+enc(name))}catch(e){box.innerHTML=`<span class="mut">${esc(e.message)}</span>`;return}
  if(!$('#devBox'))return;
  const sig=JSON.stringify(v);
  if(box.dataset.sig===sig)return;
  const ae=document.activeElement,sb=$('#sessBar');if(box.dataset.sig&&ae&&(box.contains(ae)&&ae.matches('textarea,input')||sb&&sb.contains(ae)&&ae.matches('select'))){devPending=name;return}   // 正在填回答，填完再刷新
  const keep={};box.querySelectorAll('details[data-k]').forEach(x=>keep[x.dataset.k]=x.open);
  const oa=box.querySelector('.act'),oTop=oa?oa.scrollTop:0,oH=oa?oa.scrollHeight:0;
  const s=v['会话'],run=s&&s['状态']==='运行中',SP=!!v['全栈期'],legacy=s&&s['类型']!=='复测',pg=SP&&!legacy?null:v['进度'];
  const pend=v['待确认'].filter(q=>q['状态']==='待回答'),done=v['待确认'].filter(q=>q['状态']!=='待回答').reverse();
  const spRun=SP?((v['在跑']||[]).length?`<a id="dvToStack" title="到「全栈」看各端进度">${v['在跑'].map(x=>`<span class="badge st-开发中">● ${esc(x)}</span>`).join(' ')}</a>`:'<span class="mut">现在没有 AI 在跑</span>'):'';
  const bg=(v['后台']||[]).filter(x=>x['键']!=='dev');   // 开发会话以外的后台会话 / 插件任务（插件、管家、整理资料、出方案…）
  const sess=SP&&!run&&!legacy?spRun:!s?(bg.length?`<span class="badge st-开发中">● ${bg.length} 个后台任务在跑</span> <span class="small mut">还没有启动过后台开发</span>`:'<span class="mut">还没有启动过后台开发</span>'):run?`<span class="badge st-开发中">● 后台${s['类型']==='复测'?'复测':'开发'}中</span> <span class="small mut">已运行 <span data-since="${esc(s['开始时间'])}">${since(s['开始时间'])}</span> · ${esc((s['仓库']||'').split('/').pop())} · ${esc(s['分支']||'')} · ${esc(s['权限']||'')}${s['续跑次数']?' · 已自动续跑 '+s['续跑次数']+' 次':''}</span>`
    :`<span class="badge st-${s['状态']==='已结束'&&!s['出错']&&!s['退出码']?'已完成':'已暂停'}">${esc(s['状态'])}</span> <span class="small mut">${esc(s['结束时间']||'')}${s['退出码']?' · 退出码 '+s['退出码']:''}${s['费用']?' · $'+(+s['费用']).toFixed(2):''}${s['续跑次数']?' · 自动续跑 '+s['续跑次数']+' 次':''}</span>${s['参与']?` <span class="small" title="这次会话派过的角色团专家">${esc(s['参与'])}</span>`:''}${s['未续跑原因']?`<div class="small" style="color:var(--warn)">${esc(s['未续跑原因'])}</div>`:''}${s['状态']==='中断待续'?intrHtml(s['中断']):''}`;
  const can=!['已完成'].includes(v['状态']);
  const rt=v['复测']||{},ts2=rt['测试'],plan=rt['待复测'],phones=v['设备'];
  const freeN=phones?phones.filter(x=>x['空闲']||x['本期']).length:0;
  const btn=(run?`<button class="danger" id="dvStop">■ 停止${s['类型']==='复测'?'复测':'开发'}</button>`
      :SP&&!legacy?`<a class="small" id="dvToStack2" title="全栈期：各端按拍板的方案开发、互相审查">${['开发中','各端完成','已验收'].includes(v['全栈状态'])?'各端进度、证据包在「全栈」→':'开发在「全栈」：拍板后 ▶ 各端开工 →'}</a>`
      :can?`<button class="pri" id="dvStart">▶ ${s?'继续开发':'开始开发'}</button>`:'')
    +(!run&&ts2?'<button id="dvRetest" title="开发时手机不够、没连手机等没测成的 case，条件具备后补测">🔁 复测</button>':'');
  const URG=['设备不足','账号','环境'],KINDS=['设备不足','账号','环境','未验证','待排查','工具链','其他'];
  const iss=v['非阻断问题']||[],openI=iss.filter(x=>x['状态']==='待处理'),doneI=iss.filter(x=>x['状态']!=='待处理').reverse();
  // 问题卡片：一行短标题（从原标题取「人话」那半句，不改数据），TASK 号 / 编号 / 来源放右侧小字；完整标题和详情收进「详情」；按钮悬停才出现
  const issShort=tt=>{const s=String(tt||''),i=s.search(/(?<!:)[：:](?!:)/),tk=(s.match(/TASK-\d+(?:\/TASK-\d+)*/)||[''])[0];
    if(i<0)return{t:s.replace(tk,'').trim()||s,tk};
    const a=s.slice(0,i).trim(),b=s.slice(i+1).trim(),aa=a.replace(tk,'').trim();
    if(!/^TASK-\d/.test(a))return{t:a,tk};
    const triv=b.replace(/[—\-\s]/g,'').length<=4,human=!aa.includes('::')&&(aa.replace(/\b(?:AG|FC|SM|T\d)-[\w~、,，/\-:：]*/g,'').match(/[\u4e00-\u9fa5]/g)||[]).length>=2;   // 冒号前去掉用例号还有中文 = 说的是「什么事」
    return{t:triv?[aa,b].filter(x=>x&&!/^[—\-]$/.test(x)).join(' · ')||s:human?aa+'：'+b:b,tk}};
  const issCard=x=>{const sh=issShort(x['标题']),open=x['状态']==='待处理';
    return `<div class="iss${open?'':' done'}" data-i="${esc(x['编号'])}" tabindex="0"><div class="ihd"><b class="it" title="${esc(x['标题'])}">${esc(sh.t)}</b>
    <span class="mut small im">${sh.tk?esc(sh.tk)+' · ':''}${esc(x['编号'])} · ${esc(x['来源'])}${open?'':' · '+esc(x['状态'])}</span></div>
    <details class="small"><summary class="mut">详情</summary><div><b>${esc(x['标题'])}</b></div>${x['详情']?`<div class="md">${md(x['详情']).html}</div>`:''}</details>
    <div class="iacts">${open?`${['工具链','环境','账号','其他'].includes(x['类型'])?'<button data-iterm="1" title="打开「我的终端」（目录是这一期的仓库），自己敲命令处理">⌨ 去终端处理</button>':''}<button data-is="已处理">✓ 已处理</button><button data-is="已忽略">忽略</button>`:'<button data-is="待处理">恢复</button>'}</div></div>`};
  const issHtml=iss.length?`<details data-k="iss" ${openI.length?'open':''} style="margin-top:12px">
    <summary class="small"><b style="color:var(--warn)">⚠ 不阻断开发、需要你处理的问题</b> ${openI.length?`<span class="badge icnt">待处理 ${openI.length}</span>`:'<span class="mut">（没有待处理的）</span>'}${doneI.length?' <span class="mut">已处理 '+doneI.length+'</span>':''}<span class="mut">　手机不够、缺账号、环境不满足等；处理好后点「🔁 复测」补测</span></summary>
    ${KINDS.filter(k=>openI.some(x=>x['类型']===k)).map(k=>{const xs=openI.filter(x=>x['类型']===k);
      return `<details data-k="iss-${k}" class="igrp"><summary class="small"><b>${k}</b> <span class="n${URG.includes(k)?' w':''}">${xs.length}</span></summary>${xs.map(issCard).join('')}</details>`}).join('')}
    ${doneI.length?`<details data-k="issDone" class="igrp"><summary class="small mut">已处理 / 已忽略 <span class="n">${doneI.length}</span></summary>${doneI.map(issCard).join('')}</details>`:''}</details>`:'';
  const last=(rt['记录']||[]).slice(-1)[0];
  const testHtml=ts2?`<div class="small"><b>🧪 测试</b>　识别到 ${ts2['文件数']} 个测试文件${ts2['用例']?` · ${ts2['用例']} 个 case：`+Object.entries(ts2['统计']).map(([k,n])=>esc(k)+' '+n).join(' / '):''}${ts2['环境阻塞']?` · <b style="color:var(--warn)">${ts2['环境阻塞']} 个因手机 / 账号 / 环境没测成</b>`:''}
    ${plan?`<div style="margin-top:4px">${plan['自动']?`⏰ 空闲手机够 <b>${plan['手机']}</b> 台时自动复测（现在空闲 ${freeN} 台，每分钟检查）　<a data-noauto>取消自动复测</a>`:`🔁 待复测${plan['原因']?'：'+esc(plan['原因']):''}　<span class="mut">条件具备后点「🔁 复测」，或设成手机够了自动复测</span>`}${plan['失败']?`<div style="color:var(--warn)">上次自动复测没能开始：${esc(plan['失败'])}</div>`:''}</div>`:''}
    ${last?`<div class="mut" style="margin-top:4px">上次${esc(last['类型']==='复测'?'复测':'测试')}（${esc(last['时间'])}）：通过 ${last['通过']} / 失败 ${last['失败']} / 阻塞 ${last['阻塞']}${last['小结']?' · '+esc(last['小结'].slice(0,120)):''}</div>`:''}</div>`:'';
  const devHtml=phones?`<div class="small" style="margin-bottom:4px"><b>📱 手机</b>　<span class="mut">${phones.length} 台，空闲 ${phones.filter(x=>x['空闲']).length} · 多个期共用，后台不碰别的期占着的手机</span> <span id="instLatest">${instLatest[name]?instLatestBtn(instLatest[name]):''}</span></div>${phoneRows(phones,name)}`:'';
  const qCard=q=>`<div class="q" data-q="${esc(q['编号'])}"><div class="small mut">🔔 ${esc(q['编号'])}${q['阶段']?' · '+esc(q['阶段']):''} · ${esc(q['时间'])}</div>
    <div style="font-weight:600;margin-top:2px">${esc(q['问题'])}</div>
    ${q['背景']?`<div class="ctx md">${md(q['背景']).html}</div>`:''}
    ${q['文件'].length?`<div class="small">相关文件：${q['文件'].map(f=>fileLink(f)).join('　')}</div>`:''}
    ${q['AI建议']?`<div class="small" style="margin-top:4px">AI 建议：<b>${esc(q['AI建议'])}</b>${q['照建议率']&&q['照建议率']['次数']?` <span class="mut" title="行为账本统计：只算你本人的回答；只是参考，仍由你决定">· 「${esc(q['照建议率']['类别'])}」类问题你过去 ${q['照建议率']['次数']} 次里 ${q['照建议率']['照建议']} 次照建议</span>`:''}</div>`:''}
    <div class="opts">${q['选项'].map((o,i)=>`<button data-opt="${i}">${esc(o)}</button>`).join('')}${q['AI建议']?'<button data-ai="1">采用 AI 建议</button>':''}</div>
    <textarea rows="2" placeholder="写下你的回答 / 修改意见（Cmd+Enter 提交）">${esc(drafts[q['编号']]||'')}</textarea>
    <div class="row" style="margin-top:6px"><button class="pri" data-ans="1">提交回答</button><span class="mut small">回答后后台会话自动继续</span></div></div>`;
  let prog='';
  if(pg&&pg['类型']==='任务清单'){
    const pct=pg['总数']?Math.round(100*pg['完成']/pg['总数']):0;
    prog=`<h3 style="margin:14px 0 4px;font-size:14px">开发进度</h3>
    ${pg['总数']?`<div class="pbar"><span style="width:${pct}%;background:var(--acc)"></span></div>
      <div class="small">已完成 <b>${pg['完成']}/${pg['总数']}</b> 个任务${pg['当前']?' · 下一个：<b>'+esc(pg['当前'])+'</b>':' · 全部完成'}</div>`:'<div class="small mut">任务清单里还没有勾选框任务</div>'}
    <div class="small mut" style="margin-top:4px">任务清单：${fileLink(pg['文件'])}</div>`;
  }else if(pg){
    const t=pg['任务'],ts=pg['测试'],c=ts['统计'],tot=Object.values(c).reduce((a,b)=>a+b,0);
    const stepHtml=pg['步骤'].map(x=>{const [cls,lab]=STEP[x['状态']]||['run','进行中'];
      const extra=x['名称']==='代码实现'&&t['总数']?` · ${t['状态']==='done'?t['总数']:Math.max(0,t['序号']-(x['状态']==='done'?0:1))}/${t['总数']} 个 TASK`:'';
      return `<div class="pst ${cls}"><b>${x['名称']}</b><div class="small">${lab}${extra}${x['修订']&&x['修订']!=='0'?' · 修订 '+esc(x['修订'])+' 轮':''}</div>${x['文档']?`<div class="small">${fileLink(x['文档'])}</div>`:''}</div>`}).join('');
    const pct=t['总数']?Math.round(100*(pg['步骤'][3]['状态']==='done'?t['总数']:Math.max(0,t['序号']-1))/t['总数']):0;
    const COL={PASS:'var(--s3)',FAIL:'var(--warn)',BLOCKED:'var(--s1)',ERROR:'var(--s4)'};
    prog=`<h3 style="margin:14px 0 4px;font-size:14px">开发进度</h3><div class="prog">${stepHtml}</div>
    ${t['总数']?`<div class="pbar"><span style="width:${pct}%;background:var(--acc)"></span></div>
      <div class="small">当前：<b>${esc(t['当前']||'—')}</b>${t['阶段']?' · 阶段 '+esc(t['阶段']):''}${t['审查轮次']&&t['审查轮次']!=='0'?' · 审查第 '+esc(t['审查轮次'])+' 轮':''}${t['设备修复轮次']&&t['设备修复轮次']!=='0'?' · 设备修复第 '+esc(t['设备修复轮次'])+' 轮':''}</div>`:''}
    ${t['阻塞']?`<div class="small" style="color:var(--warn)">⛔ 阻塞：${esc(t['阻塞'])}</div>`:''}
    ${t['待决策']?`<div class="small" style="color:var(--s1)">⚖ 待决策：${esc(t['待决策'])}</div>`:''}
    <h3 style="margin:14px 0 4px;font-size:14px">测试进度</h3>
    <div class="small">${ts['用例']?`设备用例 <b>${ts['用例']}</b> 个（${Object.entries(ts['优先级']).map(([k,n])=>esc(k)+' '+n).join(' · ')}）`:'还没有生成测试用例'}${ts['黑盒用例']?' · '+fileLink(ts['黑盒用例'],'黑盒用例'):''}</div>
    ${tot?`<div class="pbar">${Object.entries(c).map(([k,n])=>`<span title="${k} ${n}" style="width:${100*n/tot}%;background:${COL[k]||'var(--mut)'}"></span>`).join('')}</div>
      <div class="legend">${Object.entries(c).map(([k,n])=>`<span><b style="background:${COL[k]||'var(--mut)'}"></b>${k} ${n}</span>`).join('')}<span class="mut">通过率 ${Math.round(100*(c.PASS||0)/tot)}%</span></div>`:''}
    ${t['设备发现']?(t['设备发现'].length>160?`<details class="small mut" style="margin-top:4px"><summary>设备验证：${esc(t['设备发现'].slice(0,120))}…</summary><div style="word-break:break-all">${esc(t['设备发现']).split(' | ').join('<br>')}</div></details>`:`<div class="small mut" style="margin-top:4px">设备验证：${esc(t['设备发现'])}</div>`):''}
    ${t['审查发现']?`<details class="small"><summary>代码审查发现</summary>${esc(t['审查发现']).split(' | ').join('<br>')}</details>`:''}
    ${ts['报告'].length||pg['审查报告'].length?`<div class="small" style="margin-top:4px">报告：${ts['报告'].map(f=>fileLink(f)).concat(pg['审查报告'].map(f=>fileLink(f))).join('　')}</div>`:''}
    <div class="small mut" style="margin-top:4px">SDD 目录：${esc(pg['目录'])}${pg['状态文件']?' · '+fileLink(pg['状态文件']):''}</div>`;
  }else prog=SP&&!legacy?'':`<div class="small mut" style="margin-top:8px">开始开发后，这里显示开发进度（按开发流程「${esc(v['当前流程'])}」的进度设置）。</div>`;
  const acts=v['动作'];
  const barHtml=`<div class="top"><b>会话</b>${sess}<span class="sp"></span>
 <label class="small mut" title="这一期 AI 开发用的引擎（全栈各端没单独指定时也用它）；在「⚙ 设置」里管理">AI <select id="dvEng" ${run?'disabled':''}><option value="">默认（${esc(v['默认引擎'])}）</option>${v['引擎列表'].map(x=>`<option value="${esc(x.id)}" ${x.id===v['引擎']?'selected':''}>${esc(x['名称'])}</option>`).join('')}</select></label>
 ${v['命令告警']?`<span class="small" style="color:var(--warn)" title="${esc(v['命令告警'])}">⚠ 流程用的 /${esc((v['流程依赖命令']||[]).slice(0,3).join('、/'))}${(v['流程依赖命令']||[]).length>3?'…':''} 是 Claude 专有，当前引擎没有，可能走不下去（悬停看建议）</span>`:''}
 ${(u=>`<label class="small mut" title="角色团：后台会话按场景请专家子 agent（产品经理、架构师…）出意见；Claude 用子 agent，其他引擎用内联专家意见。全局开关、安装 / 移除在「⚙ 设置 → 角色团」">🧑‍🤝‍🧑 <select id="dvTeam"><option value="跟随" ${v['角色团']!=='关'?'selected':''}>跟随全局（${v['角色团全局']?'开':'关'}）</option><option value="关" ${v['角色团']==='关'?'selected':''}>关</option></select>${
   v['角色团']!=='关'&&v['角色团全局']&&!v['角色团引擎支持']?' <span style="color:var(--warn)">这一期的引擎把角色团关了</span>':''}${
   u['合计']?` <span title="${esc(Object.entries(u['按角色']||{}).map(([k,n])=>k+' '+n).join('、'))}">本期已派 ${u['合计']} 次</span>`:''}</label>`)(v['角色调用']||{合计:0})}
    ${SP?'':`<span class="small mut" title="开发流程跟着端和仓库走：在「全栈 → 各端」里给每个端选">流程 ${(v['各端流程']||[]).length?v['各端流程'].map(x=>`${esc(x['端'])} <b>${esc(x['流程'])}</b>`).join('，'):`<b>${esc(v['当前流程'])}</b>`}${v['流程']?' <span class="badge" title="老的期在这里统一指定过流程">整期统一</span> <a id="dvFlowEnd" title="取消整期统一的流程，改为在「全栈 → 各端」按端选">改为按端</a>':''} · <a id="dvFlowGo">按端选 →</a></span>`}
    ${apSw(v,'dvPush')}<span class="sw ${v['免打扰']?'on':''}" id="dvDnd" title="整期生效：出方案、完善需求、各端开发、单端开发的提问都由 AI 按自己的建议决策并记录"><i></i>🌙 免打扰（整期）</span>${btn}</div>
    ${bg.length?`<div class="bglist small" style="margin:4px 0">${bg.map(x=>`<div class="row" style="gap:6px;padding:2px 0;align-items:center"><span class="badge st-开发中">● ${esc(x['名称'])}</span>
      <span class="mut">${esc(x['发起']||'后台')} 起 · ${esc(String(x['开始']||'').slice(5,16))} 开始 · 已运行 <span data-since="${esc(x['开始'])}">${since(x['开始'])}</span>${x['进度']?' · '+esc(x['进度']):''}</span>${x['内容']?` <span class="mut" title="${esc(x['内容'])}">· ${esc(x['内容'].length>40?x['内容'].slice(0,40)+'…':x['内容'])}</span>`:''}
      <span class="sp"></span>${x['可停止']?`<button class="danger small" data-bgstop="${esc(x['键'])}" data-bgname="${esc(x['名称'])}">■ 停止</button>`:''}</div>`).join('')}</div>`:''}
    ${v['代码基线']&&!SP?`<div class="small mut mono" title="${esc(v['代码基线详情']||'')}">📍 代码：${esc(v['代码基线'])}${v['推送状态']?' · '+esc(v['推送状态']):''}${v['工作副本']?` · <span title="工作副本池开工时分配的副本">🗂 ${esc(v['工作副本'])}</span>`:''}</div>`:''}
    ${v['免打扰']?`<div class="small mut" style="margin-top:4px">免打扰已开启（整期）：需要确认的地方由 AI 按自己的建议决策，结果记在「${SP?'确认与问题':'开发'}」里的「已处理的确认」和记录里。</div>`:''}`;
  const bar=$('#sessBar');if(bar&&bar.innerHTML!==barHtml)bar.innerHTML=barHtml;
  if(bar)bindIntr(bar,()=>devBox(name));
  if(bar)bar.querySelectorAll('[data-bgstop]').forEach(b=>b.onclick=async()=>{if(!await kbConfirm(`停止「${b.dataset.bgname}」？`))return;
    b.disabled=true;try{toast((await post('/api/period/bg/stop',{period:name,key:b.dataset.bgstop})).msg)}catch(e){toast(e.message)}devBox(name);talkBox(name)});
  const autoHtml=autoQsHtml(v['待确認'.replace('認','认')]);
  const doneHtml=done.length?`<details data-k="done" style="margin-top:8px"><summary class="small"><b>已处理的确认</b>（${done.length}）</summary>${done.map(q=>`<div class="small" style="padding:6px 0;border-bottom:1px solid var(--soft)"><span class="mut">${esc(q['编号'])} · ${esc(q['回答时间']||q['时间'])} · ${esc(q['回答人']||q['状态'])}</span><br><b>${esc(q['问题'])}</b><br>→ ${esc(q['回答']||q['状态'])}</div>`).join('')}</details>`:'';
  const side=`${issHtml?`<div class="card2 issbox"><div class="bd">${issHtml}</div></div>`:''}
    ${testHtml?`<div class="card2"><div class="bd">${testHtml}</div></div>`:''}
    ${devHtml?`<div class="card2"><div class="bd">${devHtml}</div></div>`:''}`;
  // 有待确认：双栏（左边回答问题）；没有待确认：整块单栏全宽，问题在前
  const ltc=`<div id="liveTasks">${liveTasksHtml(liveCache[name])}</div>`;   // ⏱ 进度卡片（63-live.js，liveBox 刷新）
  box.innerHTML=pend.length?`<div class="two"><div class="stack">${pend.map(qCard).join('')}${autoHtml}${ltc}${prog}${doneHtml}</div><div class="stack">${side}</div></div>`
    :`<div class="stack one">${autoHtml}${ltc}${side}${prog}${doneHtml}</div>`;
  box.dataset.sig=sig;
  box.querySelectorAll('details[data-k]').forEach(x=>{if(x.dataset.k in keep)x.open=keep[x.dataset.k]});
  const na=box.querySelector('.act');if(na&&oTop>0)na.scrollTop=oTop+(na.scrollHeight-oH);   // 看旧记录时，新动作加在上面也不跳
  devCache.name=name;devCache.html=box.innerHTML;devCache.bar=barHtml;devCache.sig=sig;
  const aa=box.querySelector('[data-allact]');if(aa)aa.onclick=e=>{e.preventDefault();e.stopPropagation();allActs(name)};
  box.querySelectorAll('[data-rf]').forEach(a=>a.onclick=()=>readFile(a.dataset.rf));
  if(phones)instLatestLoad(name);
  box.querySelectorAll('.q').forEach(el=>{const id=el.dataset.q,ta=el.querySelector('textarea'),q=pend.find(x=>x['编号']===id);
    ta.oninput=()=>drafts[id]=ta.value;
    el.querySelectorAll('[data-opt]').forEach(b=>b.onclick=()=>{ta.value=q['选项'][+b.dataset.opt];drafts[id]=ta.value;ta.focus()});
    const ai=el.querySelector('[data-ai]');if(ai)ai.onclick=()=>{ta.value=q['AI建议'];drafts[id]=ta.value;ta.focus()};
    const go=async()=>{try{const r=await post('/api/answer',{period:name,id,answer:ta.value});delete drafts[id];toast(r.msg);devBox(name)}catch(e){toast(e.message)}};
    el.querySelector('[data-ans]').onclick=go;ta.onkeydown=e=>{if(e.key==='Enter'&&(e.metaKey||e.ctrlKey))go()}});
  autoQsBind(box,name,()=>devBox(name));
  apBind(name,'dvPush',v,()=>devBox(name));
  $('#dvDnd').onclick=async()=>{try{const r=await post('/api/dnd',{period:name,on:!v['免打扰']});toast(r.msg);devBox(name);if($('#stackBox'))stackBox(name)}catch(e){toast(e.message)}};
  ['#dvToStack','#dvToStack2'].forEach(k=>{const a=$(k);if(a)a.onclick=()=>{const t=document.querySelector('[data-tab="stack"]');if(t)t.click();const p=document.querySelector('[data-pane="stack"]');if(p)p.scrollIntoView({behavior:'smooth',block:'start'})}});
  const st=$('#dvStart');if(st)st.onclick=()=>{
    openDlg((s?'继续':'开始')+'开发',`<div class="form"><p style="margin:0">这等同于<b>你本人批准开发</b>：会在目标仓库后台启动 Claude Code（${'auto'} 权限），按这一期的开发流程开发；需要确认的地方会出现在本页并弹系统通知。</p>
      <label>输入期名确认<input type="text" id="dvC" placeholder="${esc(name)}"></label>
      <label>备注（可空）<input type="text" id="dvN" placeholder="如：先做接口和数据层，UI 等设计稿定了再做"></label>
      <label class="row" style="color:var(--fg)"><input type="checkbox" id="dvD" ${v['免打扰']?'checked':''}> 开启免打扰（不再询问，AI 自行决策）</label>
      <button class="pri" id="dvGo">▶ 确认开始</button></div>`);
    $('#dvGo').onclick=async()=>{const b=$('#dvGo');b.disabled=true;b.textContent='启动中…';
      try{if($('#dvD').checked!==v['免打扰'])await post('/api/dnd',{period:name,on:$('#dvD').checked});
        await devStart({period:name,confirm:$('#dvC').value,note:$('#dvN').value})}
      catch(e){toast(e.message);b.disabled=false;b.textContent='▶ 确认开始'}};$('#dvC').focus()};
  const tg=$('#dvTeam');if(tg)tg.onchange=async()=>{try{const r=await post('/api/period/team',{period:name,mode:tg.value});toast(r.msg);
    tg.blur();devPending='';await devBox(name)}catch(e){toast(e.message)}};
  const eg=$('#dvEng');if(eg)eg.onchange=async()=>{try{const r=await post('/api/period/engine',{period:name,id:eg.value});toast(r.msg);
    // 下拉还聚焦时 devBox 会把刷新推迟到 focusout，告警会停留在旧引擎上；这里主动收起焦点、清掉挂起的刷新，
    // 并按本期新引擎重画开发面板和全栈各端（各端的告警用 role_engine，也会跟本期引擎走）。
    eg.blur();devPending='';await devBox(name);if($('#stackBox'))stackBox(name)}catch(e){toast(e.message)}};
  const fe=$('#dvFlowEnd');if(fe)fe.onclick=async()=>{if(!await kbConfirm('取消这一期统一指定的流程？之后每个端按「全栈 → 各端」里选的流程（没选就自动匹配）。'))return;
    try{const r=await post('/api/period/flow',{period:name,id:''});toast(r.msg);devBox(name);stackBox(name)}catch(e){toast(e.message)}};
  const fg=$('#dvFlowGo');if(fg)fg.onclick=()=>{const t=document.querySelector('[data-tab="stack"]');if(t)t.click();const p=document.querySelector('[data-pane="stack"]');if(p)p.scrollIntoView({block:'start'})};
  box.querySelectorAll('[data-iterm]').forEach(b=>b.onclick=()=>termOpen(name,((v['各端一览']||[])[0]||{})['仓库']||(s&&s['仓库'])||''));
  box.querySelectorAll('.iss').forEach(el=>el.querySelectorAll('[data-is]').forEach(b=>b.onclick=async()=>{
    try{const r=await post('/api/issue',{period:name,id:el.dataset.i,status:b.dataset.is});toast(r.msg);devBox(name)}catch(e){toast(e.message)}}));
  box.querySelectorAll('[data-dm]').forEach(b=>b.onclick=async()=>{
    if(b.dataset.dm==='release'&&!await kbConfirm(`解除 ${b.dataset.sn} 的占用？`))return;
    try{const r=await post('/api/device',{serial:b.dataset.sn,action:b.dataset.dm});toast(r.msg);devBox(name)}catch(e){toast(e.message)}});
  const mt=$('#dvMsg');if(mt){mt.oninput=()=>drafts.__chat=mt.value;
    const send=async()=>{const t=mt.value.trim();if(!t)return;const it=$('#dvInt'),b=$('#dvSend');b.disabled=true;
      try{const r=await post('/api/dev/chat',{period:name,text:t,interrupt:!!(it&&it.checked)});delete drafts.__chat;mt.value='';toast(r.msg);devBox(name)}
      catch(e){toast(e.message)}finally{b.disabled=false}};
    $('#dvSend').onclick=send;mt.onkeydown=e=>{if(e.key==='Enter'&&(e.metaKey||e.ctrlKey))send()}}
  const na2=box.querySelector('[data-noauto]');if(na2)na2.onclick=async()=>{try{const r=await post('/api/retest/plan',{period:name,auto:false});toast(r.msg);devBox(name)}catch(e){toast(e.message)}};
  const rb=$('#dvRetest');if(rb)rb.onclick=()=>{
    const need=Math.max(rt['需要手机']||1,(plan&&plan['手机'])||0);
    openDlg('复测',`<div class="form"><p style="margin:0">后台启动 AI，按开发流程「${esc(v['当前流程'])}」的复测步骤补测：申请手机、编译装包、逐条验证，结果追加到测试报告。不改期状态、不开发新功能；发现缺陷会先问你。</p>
      ${ts2['环境阻塞']?`<p class="small" style="margin:0">上次有 <b>${ts2['环境阻塞']}</b> 个 case 因手机 / 账号 / 环境没测成${openI.length?`，还有 ${openI.length} 个问题待处理（见「⚠ 需要你处理的问题」）`:''}。</p>`:''}
      <label class="row" style="color:var(--fg)"><input type="radio" name="rtS" value="未通过" checked> 只测上次没通过的（BLOCKED / FAIL / 未验证）</label>
      <label class="row" style="color:var(--fg)"><input type="radio" name="rtS" value="全部"> 全部重新测</label>
      <label>需要几台手机<span class="row"><input type="number" id="rtN" min="1" max="10" value="${need}" style="width:80px"><span class="mut small">现在空闲 ${freeN} 台${phones?'':'（本机找不到 adb）'}</span></span></label>
      <label>备注（可空，如「已接好 3 台手机，账号已重新登录」）<input type="text" id="rtNote" placeholder="如：已接好 3 台手机，测试账号已重新登录"></label>
      <div class="row"><button class="pri" id="rtGo">▶ 现在复测</button><button id="rtAuto">⏰ 手机够了自动复测</button></div>
      <p class="small mut" style="margin:0">「手机够了自动复测」：每分钟检查一次，空闲手机达到台数就自动开始并弹通知（只触发一次）。</p></div>`);
    const opt=()=>({scope:($('#dlgB').querySelector('input[name=rtS]:checked')||{}).value||'未通过',phones:+$('#rtN').value||1});
    $('#rtGo').onclick=async()=>{const o=opt();if(phones&&freeN<o.phones&&!await kbConfirm(`现在只有 ${freeN} 台空闲手机，少于 ${o.phones} 台。仍然现在复测？（测不了的会记成问题）`))return;
      const b=$('#rtGo');b.disabled=true;b.textContent='启动中…';
      try{const r=await post('/api/retest',{period:name,scope:o.scope,note:$('#rtNote').value});dlg.close();toast(r.msg);render()}catch(e){toast(e.message);b.disabled=false;b.textContent='▶ 现在复测'}};
    $('#rtAuto').onclick=async()=>{const o=opt();try{const r=await post('/api/retest/plan',{period:name,auto:true,phones:o.phones,scope:o.scope});dlg.close();toast(r.msg);devBox(name)}catch(e){toast(e.message)}};
  };
  const sp=$('#dvStop');if(sp)sp.onclick=async()=>{if(!await kbConfirm(s['类型']==='复测'?'停止复测？':'停止后台开发？进行中的步骤会中断，状态改为「已暂停」，之后可「继续开发」。'))return;
    try{const r=await post('/api/dev/stop',{period:name});toast(r.msg);render()}catch(e){toast(e.message)}};
}

// 开始开发：仓库、分支自动判断；判断不了时弹窗让本人选，选完带着选择重新开始
async function devStart(body){
  try{const r=await post('/api/dev/start',body);dlg.close();toast(r.msg);render()}
  catch(e){if(!e.need)throw e;needDlg(body,e.need)}
}
function needDlg(body,n){
  const radio=(v,label,dis,chk)=>`<label class="row" style="align-items:flex-start;margin:4px 0;${dis?'opacity:.55':''}"><input type="radio" name="nd" value="${esc(v)}" ${dis?'disabled':''} ${chk?'checked':''}><span>${label}</span></label>`;
  let h;
  if(n['需要']==='仓库'){
    const cs=n['候选']||[],first=cs.findIndex(x=>x['可用']);
    h=`<p>${esc(n['说明']||(cs.length?'找到多个可能的代码仓库，确定不了用哪个，请选一个：':'本期文字资料里没有找到本机代码路径，请填写：'))}</p>
      ${cs.map((x,i)=>radio(x['路径'],`${x['远程仓库']?`<b>${esc(x['远程仓库'])}</b> <span class="small mut">远程仓库，开工时自动分配空闲副本</span>`:`<code>${esc(x['路径'])}</code>`}　<span class="mut small">当前分支 ${esc(x['当前分支']||'—')} · 来自 ${esc(x['依据'].join('、'))} · ${x['可用']?'流程 '+esc(x['流程']):'没有匹配的开发流程（可在「全栈 → 各端」给端选流程）'}</span>`,!x['可用'],i===first)).join('')}
      ${radio('*','其他路径　<input type="text" id="ndP" style="width:420px" placeholder="/Users/…/代码仓库根目录">',false,first<0)}
      ${n['分支']&&n['分支'].length?`<p class="mut small">本期登记的开发分支：${esc(n['分支'].join('、'))}</p>`:''}
      <p class="mut small">选定后会记在这一期，下次开始开发直接使用。远程仓库在「🗂 基础信息 → ＋ 新增 → 分类选 远程仓库」按模板填。</p>`;
  }else{
    const dirty=n['未提交数']||0;
    h=`<p>仓库 <code>${esc(n['仓库'])}</code> 当前在分支 <b>${esc(n['当前分支']||'（游离）')}</b>，本期登记的开发分支是 <b>${esc(n['分支'].join('、'))}</b>。SDD 文档目录按分支名生成，请确认：</p>
      ${n['分支'].map((b,i)=>radio('sw:'+b,`切换到 <b>${esc(b)}</b>　<span class="mut small">切换前在当前分支、切换后在新分支各执行一次 <code>git pull --rebase</code>；本地没有会从 origin 拉取，两边都没有会提示你先建好</span>`,false,i===0)).join('')}
      ${radio('sw:*','切换到其他分支　<input type="text" id="ndB" style="width:340px" placeholder="分支名">',false,false)}
      ${radio('keep','就用当前分支 <b>'+esc(n['当前分支'])+'</b> 开发　<span class="mut small">先执行一次 <code>git pull --rebase</code></span>',!!dirty,false)}
      ${dirty?`<div class="notice small" style="display:block">已跟踪的文件有 ${dirty} 处未提交的改动：切分支时知识库会先 <code>git stash</code> 收起（说明里写期名、时间、原分支），并在本期记一条待处理问题（stash 名、原分支、文件数、恢复命令）；「就用当前分支」要先拉代码，有改动时不能选：<pre style="margin:6px 0 0">${esc(n['未提交'].join('\n'))}</pre></div>`:''}`;
  }
  openDlg(n['需要']==='仓库'?'选择开发仓库':'确认开发分支',`<div>${h}<div class="row" style="margin-top:10px"><button class="pri" id="ndGo">继续开始开发</button><button onclick="dlg.close()">取消</button></div></div>`);
  $('#dlgB').querySelectorAll('input[type=text]').forEach(t=>t.onfocus=()=>{t.closest('label').querySelector('input[type=radio]').checked=true});
  $('#ndGo').onclick=async()=>{
    const v=($('#dlgB').querySelector('input[name=nd]:checked')||{}).value;if(!v)return toast('请选一项');
    const b={...body};
    if(n['需要']==='仓库'){b.repo=v==='*'?$('#ndP').value.trim():v;if(!b.repo)return toast('请填写代码路径')}
    else{b.repo=n['仓库'];if(v==='keep')b.keepBranch=true;else{b.branch=v==='sw:*'?$('#ndB').value.trim():v.slice(3);if(!b.branch)return toast('请填写分支名')}}
    const g=$('#ndGo');g.disabled=true;g.textContent='处理中…';
    try{await devStart(b)}catch(e){toast(e.message);g.disabled=false;g.textContent='继续开始开发'}};
}

// 手机卡片「安装最新构建」：本期是 Android 端（或认不出端）且有成功的 apk 构建才显示；其他端不显示
const instLatest={};
const instLatestBtn=x=>x['可装']?`<button class="small" data-instl="${esc(x['地址'])}" title="${esc(x['任务'])} #${x['编号']}${x['版本']?' · 版本 '+esc(x['版本']):''}">📲 安装最新构建 #${x['编号']}</button>`:'';
async function instLatestLoad(name){
  const bind=()=>{const b=document.querySelector('#instLatest [data-instl]');if(b)b.onclick=()=>installDlg(name,b.dataset.instl)};bind();
  const t=instLatest[name];if(t&&Date.now()-t._t<90000)return;
  try{const x=await api('/api/build/install/latest?name='+enc(name));x._t=Date.now();instLatest[name]=x;
    const el=$('#instLatest');if(el&&curPeriod()===name){el.innerHTML=instLatestBtn(x);bind()}}catch(_){}
}

// 「AI 已自动定」：明确的事 AI 没等你就定了（或免打扰时替你定的），显示定了什么、为什么，可一键改回让 AI 重做（/api/answer 改回 → 发给会话）
function autoQsHtml(qs){
  const xs=(qs||[]).filter(q=>q['状态']==='AI决策').slice(-6).reverse();if(!xs.length)return '';
  return `<details data-k="autoq" open style="margin-top:8px"><summary class="small"><b>🤖 AI 已自动定</b>（${xs.length}）<span class="mut">　明确的事不等你拍板；不对就改回，AI 会按你的意见重做</span></summary>${xs.map(q=>`<div class="q" data-aq="${esc(q['编号'])}" style="border-color:var(--soft)">
    <div class="small">AI 已自动定：<b>${esc(q['回答']||'')}</b> <span class="mut">（${esc(q['自动']?(q['自动理由']||'问题明确、有把握、不扩大范围、可回退'):'免打扰开着，按 AI 建议定')}）</span></div>
    <div class="small mut" style="margin-top:2px">${esc(q['编号'])}${q['阶段']?' · '+esc(q['阶段']):''} · ${esc(q['回答时间']||q['时间'])} · ${esc(q['问题'])}</div>
    <details class="small" style="margin-top:4px"><summary>↩ 改回重做</summary><textarea rows="2" placeholder="写你的意见（如：不要改接口，改成在客户端兜底）；提交后发给 AI 按它改过来"></textarea>
      <div class="row" style="margin-top:4px"><button class="pri" data-aqgo="1">改回并让 AI 重做</button></div></details></div>`).join('')}</details>`}
function autoQsBind(box,name,refresh){
  box.querySelectorAll('[data-aq]').forEach(el=>{const b=el.querySelector('[data-aqgo]');if(!b)return;
    b.onclick=async()=>{const t=el.querySelector('textarea').value.trim();if(!t){toast('写一下要改成什么');return}
      b.disabled=true;try{toast((await post('/api/answer',{period:name,id:el.dataset.aq,answer:t})).msg);refresh()}catch(e){toast(e.message)}finally{b.disabled=false}}});
}
