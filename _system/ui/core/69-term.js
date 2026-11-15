// ==== 期页面「终端」：AI 动作（只读，66-dev.js 渲染）/ ⌨ 我的终端（开源 ttyd，嵌在 iframe 里，经网页服务 /term/<编号>/ 转发）
const TM={name:'',cur:''};
function termBind(name){
  const D=$('#tkActD');if(!D||!D.querySelector('[data-tv]'))return;
  if(TM.name!==name){TM.name=name;TM.cur=''}
  const show=v=>{D.querySelectorAll('[data-tv]').forEach(a=>a.classList.toggle('on',a.dataset.tv===v));
    $('#tkActs').hidden=v!=='ai';$('#tmBox').hidden=v!=='me';D.open=true;if(v==='me')tmRender(name)};
  D.querySelectorAll('[data-tv]').forEach(a=>a.onclick=e=>{e.preventDefault();e.stopPropagation();show(a.dataset.tv)});
  TM.show=show;
  api('/api/term/list?name='+enc(name)).then(v=>{const n=v['终端'].length,el=$('#tmN');if(el)el.textContent=n?`（${n} 个开着）`:'';
    if(TM.cur&&v['终端'].some(t=>t.id===TM.cur))show('me')}).catch(()=>{});
}
// 别处（如问题卡片「⌨ 去终端处理」）打开我的终端：cwd 给了就直接在那个目录新开一个
async function termOpen(name,cwd){
  const D=$('#tkActD');if(!D)return;
  if(cwd)try{TM.cur=(await post('/api/term/open',{period:name,cwd})).id}catch(e){toast(e.message)}
  if(TM.show)TM.show('me');D.scrollIntoView({behavior:'smooth',block:'start'});
}
async function tmRender(name){
  const box=$('#tmBox');if(!box)return;
  let v;try{v=await api('/api/term/list?name='+enc(name))}catch(e){box.innerHTML=`<div class="small mut">${esc(e.message)}</div>`;return}
  if(!v['开启']){box.innerHTML='<div class="small mut" style="padding:6px 0">网页终端已在「⚙ 设置 → 常用」里关掉。</div>';return}
  if(!v['ttyd']){box.innerHTML=`<div class="small" style="padding:6px 0">网页终端用开源的 <a href="https://github.com/tsl0922/ttyd" target="_blank" rel="noopener">ttyd</a>，这台机器还没装：<code>${esc(v['安装'])}</code>，装好后刷新。</div>`;return}
  const ts=v['终端'];if(TM.cur&&!ts.some(t=>t.id===TM.cur))TM.cur='';
  if(!TM.cur&&ts.length)TM.cur=ts[ts.length-1].id;
  const cur=ts.find(t=>t.id===TM.cur);
  const keep=cur&&box.querySelector(`iframe[data-tm="${cur.id}"]`);      // 同一个终端不重建 iframe（重建 = 断开、shell 结束）
  const bar=`<div class="tmbar">${ts.map(t=>`<span class="tmtab ${t.id===TM.cur?'on':''}" data-tmt="${t.id}" title="${esc(t['目录'])} · ${esc(t['开始'])} 打开">● ${esc(t['目录'].split('/').pop()||t['目录'])}</span>`).join('')}
      <select id="tmDir" style="width:auto;max-width:300px;font-size:12px">${v['目录'].map(x=>`<option value="${esc(x['路径'])}">${esc(x['名称'])}</option>`).join('')}<option value="__other">其他目录（本机路径或 user@主机:/路径）…</option></select>
      <button class="small" id="tmNew">＋ 新终端</button><span style="flex:1"></span>
      ${cur?`<button class="small" id="tmSend" title="把这个终端最近的输出发给正在跑的会话（没有会话就新开一个接着处理）">↩ 发给会话</button><a class="small" href="${esc(cur['地址'])}" target="_blank" title="在新标签页里全屏用">↗</a><button class="small" id="tmClose">✕ 关闭</button>`:''}</div>`;
  if(keep){box.querySelector('.tmbar').outerHTML=bar}
  else box.innerHTML=bar+(cur?`<iframe class="tmwin" data-tm="${cur.id}" src="${esc(cur['地址'])}" title="终端" style="width:100%;border:0"></iframe>`
      :'<div class="small mut" style="padding:8px 0">在这一期的仓库里开一个真正的终端（开源的 ttyd：中文输入、复制粘贴、vim、ssh 输密码都行）：选目录后点「＋ 新终端」。AI 被拦、要登录、要推送时在这里处理，处理完点「↩ 发给会话」让 AI 接着做。</div>')
    +'<div class="small mut">只能你本人在网页上用，会话里的 AI 用不了；刷新页面会重开 shell（之前的输出录在 期/'+esc(name)+'/终端/）。</div>';
  box.querySelectorAll('[data-tmt]').forEach(a=>a.onclick=()=>{TM.cur=a.dataset.tmt;box.innerHTML='';tmRender(name)});
  $('#tmNew').onclick=async()=>{let cwd=$('#tmDir').value;if(cwd==='__other'){cwd=(prompt('在哪个目录开终端？本机写路径，远程写 user@主机:/路径','')||'').trim();if(!cwd)return}
    try{TM.cur=(await post('/api/term/open',{period:name,cwd})).id;box.innerHTML='';tmRender(name);termBind(name)}catch(e){toast(e.message)}};
  if($('#tmClose'))$('#tmClose').onclick=async()=>{if(!confirm('关闭这个终端？里面在跑的命令会被结束。'))return;
    try{toast((await post('/api/term/close',{id:TM.cur})).msg)}catch(e){toast(e.message)}TM.cur='';box.innerHTML='';tmRender(name);termBind(name)};
  if($('#tmSend'))$('#tmSend').onclick=async()=>{let t;try{t=(await api('/api/term/tail?id='+enc(TM.cur)))['文本']}catch(e){return toast(e.message)}
    if(!t.trim())return toast('终端还没有输出');
    const msg=`我在网页终端（${cur['目录']}）里处理了下面这些，请据此接着做：\n\`\`\`\n${t.trim()}\n\`\`\``;
    if(!confirm(`把这个终端最近的输出（${t.length} 字）发给会话？\n\n有会话在跑：它下次调用知识库工具时收到；没有：新开会话接着处理。`))return;
    try{toast((await post('/api/period/say',{period:name,text:msg,target:'all',interrupt:false})).msg)}catch(e){toast(e.message)}};
}
