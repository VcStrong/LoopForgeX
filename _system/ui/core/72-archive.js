// ==== 📦 归档：做完一段时间的期 / 事项（不再出现在侧栏、总览和各种列表里），在这里搜、看、恢复
let arcQ='',arcK='';
async function archivePage(){
  let v;try{v=await api('/api/archive?q='+enc(arcQ)+'&kind='+enc(arcK))}catch(e){app.innerHTML=`<p class="mut">${esc(e.message)}</p>`;return}
  const xs=v.items,byMonth={};xs.forEach(x=>{const k=(x['完成时间']||'').slice(0,7)||'更早';(byMonth[k]=byMonth[k]||[]).push(x)});
  const row=x=>`<tr><td><a href="#/p/${enc(x['名称'])}"><b>${esc(x['名称'])}</b></a>${x['手动']?' <span class="badge" title="手动归档">手动</span>':''}<div class="small mut">${esc(x['说明']||'')}</div></td>
    <td class="small">${esc(x['类型'])}${x['事项类型']?' · '+esc(x['事项类型']):''}</td><td class="small mut">${esc(x['完成时间']||'')}</td>
    <td class="small">${x['结论']?`<a data-arcr="${esc(x['结论'])}">${esc(x['结论'].split('/').pop())}</a>`:'<span class="mut">—</span>'}</td>
    <td><button class="small" data-arcu="${esc(x['名称'])}" title="重新出现在侧栏、总览和列表里">↩ 恢复</button></td></tr>`;
  app.innerHTML=`<div class="pagehead"><div><h1>📦 归档</h1><p class="mut">做完的期和事项${v['天数']?`在「已完成」满 ${v['天数']} 天后自动归档`:'只手动归档（不自动）'}：不再占侧栏、总览和各种列表，资料、记录、结论都还在，全站搜索和 AI 照样搜得到。又来了 Bug / ANR、或你在它的页面里说话时会自动回到列表。<a href="#/settings">改归档天数</a></p></div></div>
    <div class="row" style="gap:8px;margin-bottom:10px;flex-wrap:wrap"><input type="text" id="arcQ" value="${esc(arcQ)}" placeholder="搜名字、简介、结论里的内容" style="flex:1;min-width:220px">
      <select id="arcK" style="width:auto">${[['','期和事项'],['期','只看期'],['事项','只看事项']].map(([k,t])=>`<option value="${k}" ${k===arcK?'selected':''}>${t}</option>`).join('')}</select><button id="arcGo">搜索</button></div>
    ${xs.length?Object.entries(byMonth).map(([k,ys])=>`<h3 style="margin:14px 0 6px">${esc(k)} <span class="mut small">${ys.length} 个</span></h3>
      <div class="tw"><table><tr><th>名称</th><th style="width:150px">类型</th><th style="width:130px">完成时间</th><th style="width:150px">结论</th><th style="width:80px"></th></tr>${ys.map(row).join('')}</table></div>`).join('')
      :`<p class="mut">${arcQ||arcK?'没有搜到。':'还没有归档的期和事项。'}</p>`}`;
  const go=()=>{arcQ=$('#arcQ').value.trim();arcK=$('#arcK').value;archivePage()};
  $('#arcGo').onclick=go;$('#arcK').onchange=go;$('#arcQ').onkeydown=e=>{if(e.key==='Enter')go()};
  app.querySelectorAll('[data-arcr]').forEach(a=>a.onclick=()=>readFile(a.dataset.arcr));
  app.querySelectorAll('[data-arcu]').forEach(b=>b.onclick=async()=>{try{toast((await post('/api/archive',{period:b.dataset.arcu,on:false})).msg);await navRefresh();archivePage()}catch(e){toast(e.message)}});
  if(typeof classicNav==='function')classicNav();
}
