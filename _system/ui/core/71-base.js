// ==== 基础信息
// ---------------- 基础知识库：所有期共用的账号、测试账号、代理 / VPN、环境配置
async function basePage(){
  const d=await api('/api/base'),es=d['条目'];
  let P=null,PL=null;try{[P,PL]=await Promise.all([api('/api/repos/settings'),api('/api/pool')])}catch(_){}
  // 远程仓库条目：显示工作副本池里这个克隆地址的副本状态
  const copies=e=>{if(e['分类']!=='远程仓库'||!PL)return'';const u=((e['字段']||[]).find(f=>f['名']==='克隆地址')||{})['值'],r=PL['仓库'].find(x=>x['克隆地址']===u);
    if(!r||!r['副本'].length)return'<div class="small mut">工作副本：还没拉（第一次选它开工时自动克隆）</div>';
    return `<div class="small" style="margin-top:4px"><span class="mut">工作副本（${esc(r['名称'])}${r['Gerrit']?' · Gerrit':''}）</span>${r['副本'].map(c=>`<div>第 ${c['序号']} 份 <span class="mono">${esc(c['分支']||'游离')}</span> · ${c['占用']?`<b style="color:var(--s1)">${esc(c['占用'])}</b>`:'空闲'}${c['改动']?` · ${c['改动']} 个未提交`:''}${c['未推送']?` · <span style="color:var(--warn)">${c['未推送']} 个未推送</span>`:''} <span class="mut">· ${esc(c['最后使用'])}</span></div>`).join('')}</div>`};
  const projSec=()=>P&&P.items.length?`<h2>💻 工程设置</h2>
    <p class="small mut">每个工程（本机路径、远程仓库）是哪一端、默认用哪套开发流程、代码怎么更新、运行到手机编哪个模块，随时可改。「自动」：端按仓库里的文件识别；流程按识别文件匹配，匹配不上用「⚙ 设置」里的默认流程。
      某一期在开发面板单独选了流程以那一期为准；在全栈「各端」里给某端选了认不出的工程，也会记到这里。</p>
    <div class="tw"><table><tr><th>工程</th><th style="width:180px">端</th><th style="width:230px">默认流程</th><th style="width:150px" title="对这个工程所有可选的时机生效（锁定的不受影响）；跟随全局 = 按下面「⏱ 时机」表">代码更新</th><th style="width:200px" title="「▶ 运行到手机」（Android）：./gradlew <模块>:install<变体>。空 = 自动识别 application 模块、变体 debug">运行到手机</th></tr>
      ${P.items.map(r=>`<tr><td><b>${esc(r['名称'])}</b>${r['分支']?` <span class="small mut">${esc(r['分支'])}</span>`:''}<div class="small mut mono">${esc(r['路径'].replace(/^\/Users\/[^/]+/,'~'))}</div></td>
        <td><select data-rpend="${esc(r['路径'])}"><option value="">自动${r['远程']?'':r['识别端'].length?'（'+esc(r['识别端'].join(' / '))+'）':'（认不出）'}</option>${(r['端']&&!P.ends.includes(r['端'])?P.ends.concat([r['端']]):P.ends).map(e=>`<option ${e===r['端']?'selected':''}>${esc(e)}</option>`).join('')}</select></td>
        <td><select data-rpflow="${esc(r['路径'])}"><option value="">自动</option>${P.flows.map(f=>`<option value="${esc(f.id)}" ${f.id===r['默认流程']?'selected':''}>${esc(f['名称'])}</option>`).join('')}</select></td>
        <td><select data-rpcode="${esc(r['路径'])}"><option value="">跟随全局</option>${['拉最新','只fetch','不动'].map(o=>`<option ${o===r['代码更新']?'selected':''}>${o}</option>`).join('')}</select></td>
        <td>${r['远程仓库']?'<span class="mut small">在上面「远程仓库」条目里填</span>':r['远程']?'<span class="mut small">远程工程不支持</span>':`<div class="row" style="gap:4px"><input type="text" class="mono" data-rprun="${esc(r['路径'])}" data-k="模块" value="${esc(r['运行模块']||'')}" placeholder="模块：自动" title="application 模块，如 :app" style="width:96px"><input type="text" class="mono" data-rprun="${esc(r['路径'])}" data-k="变体" value="${esc(r['运行变体']||'')}" placeholder="debug" title="变体，有渠道时写全，如 prodDebug" style="width:96px"></div>`}</td></tr>`).join('')}</table></div>`:'';
  const field=(e,f)=>`<tr><td>${esc(f['名'])}</td><td>${f['机密']?`<span class="sec" data-sv="${esc(e['编号'])}" data-sf="${esc(f['名'])}">●●●●●●</span> <a class="small" data-show>显示</a> · <a class="small" data-copy>复制</a>`:`${/^https?:\/\//.test(f['值'])?`<a href="${esc(f['值'])}" target="_blank" rel="noopener">${esc(f['值'])}</a>`:esc(f['值'])} <a class="small" data-cp="${esc(f['值'])}">复制</a>`}</td></tr>`;
  const card=e=>`<div class="bcard"><div class="t"><b>${esc(e['名称'])}</b><span class="small"><span class="mut">${esc(e['编号'])}</span> <a data-edit="${esc(e['编号'])}">编辑</a></span></div>
    ${e['适用']?`<div class="small mut">适用：${esc(e['适用'])}</div>`:''}
    <table>${(e['字段']||[]).map(f=>field(e,f)).join('')}</table>
    ${e['附件']&&e['附件'].length?`<div class="small">附件：${e['附件'].map(a=>`<a data-rf="${esc(d.dir+'/'+a)}">${esc(a.split('/').pop())}</a>`).join('　')}</div>`:''}
    ${e['备注']?`<div class="small md" style="margin-top:4px">${md(e['备注']).html}</div>`:''}${copies(e)}</div>`;
  app.innerHTML=`<div class="row" style="justify-content:space-between"><h1>🗂 基础知识库</h1><button class="pri" id="bAdd">＋ 新增</button></div>
    <p class="mut">所有期共用的基础上下文：网站账号、测试账号、Wi-Fi / 代理 / VPN、环境配置、远程仓库、各工程的设置等。Claude 回答和开发时会自动参考。
    密码等<b>机密字段只存钥匙串（Mac）或加密文件（服务器）</b>，页面默认隐藏；也可以直接跟 Claude 说「把 XX 测试账号记到基础知识库」。</p>
    ${es.length?d.cats.filter(c=>es.some(e=>e['分类']===c)).map(c=>`<h2>${esc(c)}</h2><div class="bgrid">${es.filter(e=>e['分类']===c).map(card).join('')}</div>`).join('')
      :'<p class="mut">还没有内容，点右上角「＋ 新增」。</p>'}
    ${projSec()}
    ${d.files.length?`<h2>文件</h2><ul class="files">${d.files.map(f=>`<li><a data-rf="${esc(f.path)}">${esc(f.path.slice(d.dir.length+1))}</a><span class="mut small">${size(f.size)} · ${when(f.mtime)}</span></li>`).join('')}</ul>`:''}
    <p class="mut small">目录：${esc(d.dir)}</p>`;
  app.querySelectorAll('[data-rf]').forEach(a=>a.onclick=()=>readFile(a.dataset.rf));
  const rpSet=async b=>{try{toast((await post('/api/repos/settings',b)).msg)}catch(e){toast(e.message)}basePage()};
  app.querySelectorAll('[data-rpend]').forEach(s=>s.onchange=()=>rpSet({path:s.dataset.rpend,end:s.value}));
  app.querySelectorAll('[data-rpflow]').forEach(s=>s.onchange=()=>rpSet({path:s.dataset.rpflow,flow:s.value}));
  app.querySelectorAll('[data-rpcode]').forEach(s=>s.onchange=()=>rpSet({path:s.dataset.rpcode,code:s.value}));
  app.querySelectorAll('[data-rprun]').forEach(s=>s.onchange=()=>rpSet({path:s.dataset.rprun,run:{[s.dataset.k]:s.value.trim()}}));
  app.querySelectorAll('[data-cp]').forEach(a=>a.onclick=()=>{navigator.clipboard.writeText(a.dataset.cp);toast('已复制')});
  const secret=async el=>(await post('/api/base/secret',{id:el.dataset.sv,field:el.dataset.sf})).value;
  app.querySelectorAll('[data-show]').forEach(a=>a.onclick=async()=>{const el=a.previousElementSibling;
    if(el.dataset.shown){el.textContent='●●●●●●';delete el.dataset.shown;a.textContent='显示';return}
    try{el.textContent=await secret(el);el.dataset.shown=1;a.textContent='隐藏'}catch(e){toast(e.message)}});
  app.querySelectorAll('[data-copy]').forEach(a=>a.onclick=async()=>{try{await navigator.clipboard.writeText(await secret(a.parentElement.querySelector('[data-sv]')));toast('已复制')}catch(e){toast(e.message)}});
  const TPL=d.templates||{},NUM='①②③④⑤⑥⑦⑧⑨⑩';
  const edit=e=>{
    const fs=e?e['字段']:[{'名':'账号','值':'','机密':false},{'名':'密码','值':'','机密':true}];
    const row=f=>`<div class="frow"><input type="text" class="fN" placeholder="字段名，如 账号 / 密码 / 代理地址" value="${esc(f['名'])}">
      <input type="${f['机密']?'password':'text'}" class="fV" placeholder="${f['机密']&&e?'留空 = 不修改':'值'}" value="${esc(f['机密']?'':f['值'])}">
      <label class="small row"><input type="checkbox" class="fS" ${f['机密']?'checked':''}>机密</label><a class="small" data-del>删除</a></div>`;
    // 分类有模板（如「远程仓库」）：按顺序出固定字段，带提示；模板外的字段照旧可增删
    const trow=(t,i,v)=>`<div class="frow" data-tpl="1"><input type="text" class="fN" value="${esc(t['名'])}" readonly title="模板字段" style="background:var(--bg2,transparent)">
      <input type="text" class="fV" placeholder="${esc(t['提示']||'')}" value="${esc(v||'')}" title="${esc(t['提示']||'')}">
      <span class="small mut" style="white-space:nowrap">${NUM[i]||i+1}${t['必填']?' <b style="color:var(--s1)">必填</b>':' 可空'}</span><input type="checkbox" class="fS" style="display:none"></div>`;
    const fill=cat=>{const T=TPL[cat];if(!T)return false;
      const cur={};$('#bF').querySelectorAll('.frow').forEach(r=>{const n=r.querySelector('.fN').value.trim();if(n)cur[n]=r.querySelector('.fV').value});
      (e&&e['分类']===cat?e['字段']:[]).forEach(f=>{if(!(f['名'] in cur)&&!f['机密'])cur[f['名']]=f['值']});
      const names=T.map(t=>t['名']);
      $('#bF').innerHTML=T.map((t,i)=>trow(t,i,cur[t['名']])).join('')+Object.keys(cur).filter(n=>!names.includes(n)&&cur[n]).map(n=>row({'名':n,'值':cur[n],'机密':false})).join('');
      return true};
    openDlg(e?'编辑 '+e['编号']:'新增基础信息',`<div class="form">
      <label>分类<select id="bC">${d.cats.map(c=>`<option ${e&&e['分类']===c?'selected':''}>${c}</option>`).join('')}</select></label>
      <label>名称<input type="text" id="bN" value="${esc(e?e['名称']:'')}" placeholder="如：测试账号（国内）/ 办公室 Wi-Fi / 抓包代理 / 示例工程 alpha（远程仓库）"></label>
      <label>适用（产品 / 场景，可空）<input type="text" id="bS" value="${esc(e?e['适用']:'')}" placeholder="如：国内版 / 测试环境 / 所有产品"></label>
      <div class="small" id="bTip" style="color:var(--s1)"></div>
      <div><div class="small mut" style="margin-bottom:4px">字段（勾选「机密」的单独加密保存）</div><div id="bF" style="display:grid;gap:6px">${fs.map(row).join('')}</div>
        <a class="small" id="bFA" style="display:inline-block;margin-top:6px">＋ 添加字段</a></div>
      <label>备注（可空，支持 Markdown）<textarea id="bR" rows="3" placeholder="如：用之前先连公司 VPN；密码每月 1 号更换">${esc(e?e['备注']:'')}</textarea></label>
      <div class="row"><button class="pri" id="bGo">保存</button>${e?'<button class="danger" id="bDel">删除这条</button>':''}</div></div>`);
    const bind=()=>{$('#bF').querySelectorAll('[data-del]').forEach(a=>a.onclick=()=>a.parentElement.remove());
      $('#bF').querySelectorAll('.fS').forEach(c=>c.onchange=()=>c.closest('.frow').querySelector('.fV').type=c.checked?'password':'text')};
    bind();$('#bFA').onclick=()=>{$('#bF').insertAdjacentHTML('beforeend',row({'名':'','值':'','机密':false}));bind()};
    const tip=()=>{const c=$('#bC').value;$('#bTip').innerHTML=c==='远程仓库'?'一个马甲（app 模块 + 它的 master）填一条；同一个克隆地址的几条共用工作副本池。填好后在「开始开发」/ 事项「工程」/ 全栈「各端」的下拉里选它，开工时自动分配副本、切到开发分支（没有就从基线分支拉）。':''};
    if(fill($('#bC').value))bind();tip();
    $('#bC').onchange=()=>{if(fill($('#bC').value))bind();tip()};
    $('#bGo').onclick=async()=>{const fields=[...$('#bF').querySelectorAll('.frow')].map(r=>({'名':r.querySelector('.fN').value.trim(),'值':r.querySelector('.fV').value,'机密':r.querySelector('.fS').checked})).filter(f=>f['名']);
      try{const r=await post('/api/base/save',{id:e?e['编号']:'',category:$('#bC').value,name:$('#bN').value,scope:$('#bS').value,note:$('#bR').value,fields});dlg.close();toast(r.msg.split('\n')[0]);render()}catch(x){toast(x.message)}};
    if(e)$('#bDel').onclick=async()=>{if(!await kbConfirm('删除「'+e['名称']+'」？机密字段也会一起删除。'))return;
      try{const r=await post('/api/base/save',{id:e['编号'],delete:true});dlg.close();toast(r.msg);render()}catch(x){toast(x.message)}};
    $('#bN').focus()};
  $('#bAdd').onclick=()=>edit(null);
  app.querySelectorAll('[data-edit]').forEach(a=>a.onclick=()=>edit(es.find(x=>x['编号']===a.dataset.edit)));
}
