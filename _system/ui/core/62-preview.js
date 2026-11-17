// ==== 文件预览：按扩展名选预览器（注册表）。readFile(路径) 打开预览对话框，showImg 也走这里。
// 预览器：KB.preview.register({name, ext:['md',...], render(path, url, box, ctx)})，后注册的优先（插件可以覆盖内置的、加新格式）。
//   path 文件绝对路径；url = fileUrl(path)；box 预览区容器（往里写内容）；ctx = {start 起始行, info {name,size,mtime,type}, note 图片说明, bar 顶栏左侧容器}。
//   render 抛错 → 退回文字预览。ext 写 '*' = 兜底（内置的是文字 / 代码）。
// 第三方库在 /ui/vendor/（不走 CDN，内网也能用），loadVendor('名字' 或 '/ui/vendor/x.js') 第一次用到才加载，之后复用。
const VENDOR={marked:'/ui/vendor/marked.min.js',purify:'/ui/vendor/purify.min.js',hljs:'/ui/vendor/hljs/highlight.min.js',
  mermaid:'/ui/vendor/mermaid.min.js',mammoth:'/ui/vendor/mammoth.min.js',xlsx:'/ui/vendor/xlsx.full.min.js',idiomorph:'/ui/vendor/idiomorph.min.js'};
const _vend={};
function loadVendor(...names){return Promise.all(names.map(n=>_vend[n]||(_vend[n]=new Promise((ok,no)=>{
  const src=VENDOR[n]||n,css=/\.css(\?|$)/.test(src);
  const el=document.createElement(css?'link':'script');
  if(css){el.rel='stylesheet';el.href=src}else el.src=src;
  el.onload=()=>ok();el.onerror=()=>{delete _vend[n];el.remove();no(new Error('加载 '+src+' 失败'))};
  document.head.appendChild(el)}))))}

const PV=[];
const extOf=p=>{const n=String(p).split('/').pop().toLowerCase(),i=n.lastIndexOf('.');return i>0?n.slice(i+1):''};
const fsUrl=(p,q)=>'/fs'+String(p).split('/').map(enc).join('/')+(q?'?'+q:'');   // 按路径下发：html 里的相对资源能找到
const preview={
  register(p){if(!p||!p.render||!p.ext)throw new Error('预览器要有 ext 和 render');PV.unshift({name:p.name||p.ext[0],...p});return p},
  find(path){const e=extOf(path);return PV.find(p=>p.ext.includes(e))||PV.find(p=>p.ext.includes('*'))},
  list:()=>PV.map(p=>({name:p.name,ext:p.ext.slice()})),
  open:previewOpen,
};
async function previewOpen(p,start,opt={}){
  const name=String(p).split('/').pop(),url=fileUrl(p),pv0=preview.find(p),isHtml=['html','htm','xhtml','svg'].includes(extOf(p));
  openDlg(name,`<div class="pv-bar"><span class="pv-tools" id="pvTools"></span><span class="mut small" id="pvInfo"></span><span style="flex:1"></span>
    <a class="pv-a" href="${esc(url)}&dl=1" title="下载到本机">⬇ 下载</a><a class="pv-a" href="${esc(isHtml?fsUrl(p):url)}" target="_blank" rel="noopener" title="浏览器新标签页打开（html / svg 不执行脚本）">↗ 新窗口打开</a>
    <button type="button" id="pvFull" title="全屏 / 还原">⛶ 全屏</button></div><div class="pv" id="pvBox"><p class="mut">加载中…</p></div>`);
  dlg.classList.add('pvw');
  const box=$('#pvBox'),bar=$('#pvTools');
  $('#pvFull').onclick=()=>{dlg.classList.toggle('pvfull');box.dispatchEvent(new Event('pvresize'))};
  let info;try{info=await api('/api/fileinfo?path='+enc(p))}catch(e){box.innerHTML=`<p>${esc(e.message)}</p>`;return}
  if(!box.isConnected)return;
  $('#pvInfo').textContent=`${size(info.size)} · ${when(info.mtime)}`;
  const ctx={start:+start||1,info,note:opt.note,bar};
  // 带起始行（搜索结果跳到某一行）= 看文字；其他按扩展名
  const pv=ctx.start>1?TEXT_PV:pv0;
  try{await pv.render(p,url,box,ctx)}
  catch(e){if(!box.isConnected||pv===TEXT_PV){box.innerHTML=`<p>预览失败：${esc(e.message||e)}</p>`;return}
    bar.innerHTML='';box.innerHTML='';await TEXT_PV.render(p,url,box,ctx).catch(er=>box.innerHTML=`<p>预览失败：${esc(er.message||er)}</p>`);
    box.insertAdjacentHTML('afterbegin',`<p class="mut small">「${esc(pv.name)}」预览失败（${esc(e.message||e)}），下面是抽出来的文字</p>`)}
}

// ---- 不支持的格式：文件信息 + 下载
function pvInfo(box,p,info,why){
  box.innerHTML=`<div class="pv-info"><div style="font-size:40px">📄</div><b>${esc(info.name)}</b>
    <div class="mut small">${size(info.size)} · ${when(info.mtime)}${info.type?' · '+esc(info.type):''}</div><div class="mut small mono">${esc(p)}</div>
    <p class="mut">${esc(why||'这种格式不能在网页里预览')}</p><a class="pv-a pri" href="${esc(fileUrl(p))}&dl=1">⬇ 下载</a></div>`}

// ---- 文字 / 代码：按段读（每次 3000 行），「加载更多」；常见语言用 highlight.js 高亮
const HL={js:'javascript',mjs:'javascript',cjs:'javascript',jsx:'javascript',ts:'typescript',tsx:'typescript',vue:'xml',kt:'kotlin',kts:'kotlin',
  java:'java',py:'python',sh:'bash',zsh:'bash',bash:'bash',go:'go',rs:'rust',swift:'swift',m:'objectivec',mm:'objectivec',h:'cpp',c:'c',cc:'cpp',
  cpp:'cpp',hpp:'cpp',cs:'csharp',php:'php',rb:'ruby',lua:'lua',pl:'perl',r:'r',json:'json',har:'json',xml:'xml',plist:'xml',xsl:'xml',
  yaml:'yaml',yml:'yaml',toml:'ini',ini:'ini',cfg:'ini',properties:'ini',gradle:'java',sql:'sql',css:'css',scss:'scss',less:'less',
  diff:'diff',patch:'diff',makefile:'makefile',md:'markdown',markdown:'markdown',graphql:'graphql',aidl:'java',pro:'ini'};
const TEXT_PV={name:'文字',ext:['*'],async render(p,url,box,ctx){
  const lang=HL[extOf(p)];let next=ctx.start,total=0;
  box.innerHTML=`<div class="pv-code"><pre class="pv-ln" aria-hidden="true"></pre><pre class="pv-src"><code class="${lang?'hljs language-'+lang:''}"></code></pre></div><div class="pv-more row small"></div>`;
  const ln=box.querySelector('.pv-ln'),code=box.querySelector('code'),more=box.querySelector('.pv-more');
  if(lang)loadVendor('hljs').catch(()=>{});
  const load=async()=>{
    const r=await api(`/api/text?path=${enc(p)}&start=${next}&n=3000`);
    if(!r.ok){pvInfo(box,p,ctx.info,'这种文件抽不出文字，不能在网页里预览');return}
    total=r.total;const txt=r.lines.join('\n')+(r.end<r.total?'\n':'');
    let h=esc(txt);
    if(lang&&txt.length<400000)try{await loadVendor('hljs');if(hljs.getLanguage(lang))h=hljs.highlight(txt,{language:lang,ignoreIllegals:true}).value}catch(_){}
    code.insertAdjacentHTML('beforeend',h);
    ln.insertAdjacentText('beforeend',Array.from({length:r.end-r.start+1},(_,i)=>r.start+i).join('\n')+(r.end<r.total?'\n':''));
    next=r.end+1;
    more.innerHTML=(r.end<r.total?`<button type="button" data-more>加载更多（已显示到第 ${r.end} 行，共 ${r.total} 行）</button>`:`<span class="mut">共 ${r.total} 行</span>`)
      +(ctx.start>1?' <button type="button" data-top>从第 1 行看</button>':'');
    more.querySelector('[data-more]')?.addEventListener('click',e=>{e.target.disabled=true;load().catch(er=>toast(er.message))});
    more.querySelector('[data-top]')?.addEventListener('click',()=>{ctx.start=1;TEXT_PV.render(p,url,box,ctx)});
    if(!r.lines.length&&!r.total)code.textContent='（空文件）';
  };
  await load();
}};
preview.register(TEXT_PV);

// ---- 不能预览的二进制：直接给信息 + 下载（不去抽文字）
preview.register({name:'二进制',ext:['zip','apk','aab','apks','so','jar','aar','dex','7z','rar','gz','tgz','tar','bz2','xz','dmg','pkg','exe','bin','class','hprof','ipa','iso','db','sqlite','keystore','jks','p12'],
  render(p,url,box,ctx){pvInfo(box,p,ctx.info)}});

// ---- Markdown：marked + DOMPurify，相对路径的图片 / 链接按文件所在目录找；代码块高亮、mermaid 按需加载
preview.register({name:'Markdown',ext:['md','markdown','mdown','mkd'],async render(p,url,box,ctx){
  await loadVendor('marked','purify');
  const r=await api(`/api/text?path=${enc(p)}&n=20000`);
  if(!r.ok)throw new Error('读不出文字');
  let src=r.lines.join('\n');
  const dir=p.replace(/\/[^/]*$/,''),out=md(src,{base:dir});
  ctx.bar.innerHTML='<label class="small"><input type="checkbox" id="pvRaw"> 看源码</label>';
  box.innerHTML=`<article class="md pv-md">${out.html}${r.end<r.total?`<p class="mut small">只显示了前 ${r.end} 行（共 ${r.total} 行），完整内容请下载</p>`:''}</article>`;
  mdEnhance(box.firstElementChild);
  $('#pvRaw').onchange=e=>{if(e.target.checked)TEXT_PV.render(p,url,box,{...ctx,start:1});else{box.innerHTML=`<article class="md pv-md">${md(src,{base:dir}).html}</article>`;mdEnhance(box.firstElementChild)}};
}});

// ---- HTML：<iframe sandbox>，默认不执行脚本（服务端也带 CSP sandbox）；「允许脚本」也只是隔离的不透明来源，碰不到知识库接口
preview.register({name:'HTML',ext:['html','htm','xhtml'],render(p,url,box,ctx){
  ctx.bar.innerHTML='<label class="small" title="页面在隔离的沙箱里运行，拿不到知识库的登录和接口"><input type="checkbox" id="pvJs"> 允许脚本</label> <label class="small"><input type="checkbox" id="pvSrc"> 看源码</label>';
  const show=js=>{box.innerHTML=`<iframe class="pv-frame pv-white" sandbox="${js?'allow-scripts':''}" referrerpolicy="no-referrer" src="${esc(fsUrl(p,js?'js=1':''))}"></iframe>`};
  show(false);
  $('#pvJs').onchange=e=>{$('#pvSrc').checked=false;show(e.target.checked)};
  $('#pvSrc').onchange=e=>e.target.checked?TEXT_PV.render(p,url,box,{...ctx,start:1}):show($('#pvJs').checked);
}});

// ---- 图片：缩放（滚轮 / 按钮）、拖动、双击适应窗口。svg 也放 <img> 里，不内联（<img> 里的 svg 不执行脚本）
async function imgNote(p){try{const t=(await api('/api/read?path='+enc(p))).text||'';const i=t.indexOf('\n\n');const n=i<0?'':t.slice(i+2).trim();return n==='（暂无说明）'?'':n}catch(_){return ''}}
function imgViewer(box,src,bar){
  bar.innerHTML=`<button type="button" data-z="-" title="缩小">−</button><button type="button" data-z="fit" title="适应窗口">适应</button><button type="button" data-z="1" title="原始大小">1:1</button><button type="button" data-z="+" title="放大">＋</button><span class="mut small" data-zl></span>`;
  const stage=document.createElement('div');stage.className='pv-stage';stage.title='滚轮缩放 · 拖动平移 · 双击适应窗口';
  stage.innerHTML='<img draggable="false" alt="">';box.appendChild(stage);
  const im=stage.firstChild,zl=bar.querySelector('[data-zl]');let s=1,x=0,y=0;
  const W=()=>im.naturalWidth||800,H=()=>im.naturalHeight||600;
  const ap=()=>{im.style.transform=`translate(${x}px,${y}px) scale(${s})`;zl.textContent=`${Math.round(s*100)}% · ${im.naturalWidth}×${im.naturalHeight}`};
  const center=()=>{x=(stage.clientWidth-W()*s)/2;y=Math.max(0,(stage.clientHeight-H()*s)/2)};
  const fit=()=>{s=Math.min(1,stage.clientWidth/W(),stage.clientHeight/H())||1;center();ap()};
  const zoomAt=(k,cx=stage.clientWidth/2,cy=stage.clientHeight/2)=>{const ns=Math.min(32,Math.max(.02,s*k));x=cx-(cx-x)*ns/s;y=cy-(cy-y)*ns/s;s=ns;ap()};
  im.onload=()=>{if(!im.naturalWidth){im.style.width='800px'}fit()};
  im.onerror=()=>{stage.innerHTML='<p class="mut" style="padding:20px">图片加载失败（浏览器可能不支持这种格式，可以下载后看）</p>'};
  im.src=src;
  stage.addEventListener('wheel',e=>{e.preventDefault();const r=stage.getBoundingClientRect();zoomAt(Math.exp(-e.deltaY*(e.ctrlKey?.01:.0015)),e.clientX-r.left,e.clientY-r.top)},{passive:false});
  stage.onpointerdown=e=>{if(e.button)return;stage.setPointerCapture(e.pointerId);stage.classList.add('drag');const sx=e.clientX-x,sy=e.clientY-y;
    stage.onpointermove=ev=>{x=ev.clientX-sx;y=ev.clientY-sy;ap()};stage.onpointerup=stage.onpointercancel=()=>{stage.onpointermove=null;stage.classList.remove('drag')}};
  stage.ondblclick=fit;
  bar.onclick=e=>{const z=e.target.dataset.z;if(!z)return;if(z==='fit')fit();else if(z==='1'){const cx=stage.clientWidth/2,cy=stage.clientHeight/2;zoomAt(1/s,cx,cy)}else zoomAt(z==='+'?1.25:.8)};
  box.addEventListener('pvresize',()=>setTimeout(fit,30));
}
preview.register({name:'图片',ext:['png','jpg','jpeg','gif','webp','svg','bmp','ico','avif','apng','jfif'],async render(p,url,box,ctx){
  box.innerHTML='';
  const note=ctx.note!==undefined?ctx.note:await imgNote(p);
  if(!box.isConnected)return;
  box.insertAdjacentHTML('beforeend',note?`<p class="pv-note">${esc(note)}</p>`:'<p class="mut small pv-note">暂无说明，可让 Claude「把设计图都看一遍补上说明」</p>');
  imgViewer(box,url,ctx.bar);
}});

// ---- PDF：浏览器自带的阅读器
preview.register({name:'PDF',ext:['pdf'],render(p,url,box){box.innerHTML=`<iframe class="pv-frame" src="${esc(url)}"></iframe>`}});

// ---- Word（docx）：mammoth 转成干净的 HTML（保留标题、列表、表格、图片），再过 DOMPurify。老的 .doc 走文字
preview.register({name:'Word',ext:['docx'],async render(p,url,box,ctx){
  if(ctx.info.size>50<<20)return pvInfo(box,p,ctx.info,'文件太大（超过 50MB），请下载后看');
  await loadVendor('mammoth','purify');
  const buf=await (await fetch(url)).arrayBuffer();
  const r=await mammoth.convertToHtml({arrayBuffer:buf});
  box.innerHTML=`<article class="md pv-md pv-doc">${DOMPurify.sanitize(r.value,{FORBID_TAGS:['style','form'],FORBID_ATTR:['style']}).replace(/<table>/g,'<div class="tw"><table>').replace(/<\/table>/g,'</table></div>')}</article>`;
}});

// ---- Excel / csv：SheetJS 转成表格，多个工作表切换；只画前 2000 行
const SHEET_ROWS=2000;
preview.register({name:'表格',ext:['xlsx','xlsm','xls','xlsb','ods','csv','tsv'],async render(p,url,box,ctx){
  if(ctx.info.size>40<<20)return pvInfo(box,p,ctx.info,'文件太大（超过 40MB），请下载后看');
  await loadVendor('xlsx','purify');
  const e=extOf(p),opt={sheetRows:SHEET_ROWS+1,cellStyles:false,cellHTML:false};
  let wb;
  if(e==='csv'||e==='tsv'){const r=await api(`/api/text?path=${enc(p)}&n=${SHEET_ROWS+1}`);
    wb=XLSX.read(r.lines.join('\n'),{...opt,type:'string',FS:e==='tsv'?'\t':undefined})}
  else wb=XLSX.read(await (await fetch(url)).arrayBuffer(),{...opt,type:'array'});
  const names=wb.SheetNames;if(!names.length)throw new Error('没有工作表');
  ctx.bar.innerHTML=names.length>1?names.map((n,i)=>`<button type="button" data-sh="${i}">${esc(n)}</button>`).join(''):'';
  const show=i=>{const ws=wb.Sheets[names[i]];
    ctx.bar.querySelectorAll('[data-sh]').forEach(b=>b.classList.toggle('pri',+b.dataset.sh===i));
    const full=ws['!fullref']?XLSX.utils.decode_range(ws['!fullref']).e.r+1:0;
    const html=ws['!ref']?XLSX.utils.sheet_to_html(ws,{header:'',footer:''}):'<p class="mut">（空表）</p>';
    box.innerHTML=`<div class="tw pv-sheet">${DOMPurify.sanitize(html,{FORBID_TAGS:['style'],FORBID_ATTR:['style']})}</div>`
      +(full>SHEET_ROWS?`<p class="mut small">只显示前 ${SHEET_ROWS} 行（共 ${full} 行），完整内容请下载</p>`:'')};
  ctx.bar.onclick=ev=>{const i=ev.target.dataset.sh;if(i!=null)show(+i)};
  show(0);
}});

// ---- 音视频：浏览器自带播放器（服务端支持 Range，能拖进度）
preview.register({name:'视频',ext:['mp4','webm','m4v','mov','ogv'],render(p,url,box){
  box.innerHTML=`<video class="pv-media" controls preload="metadata" src="${esc(url)}"></video>`;
  box.firstChild.onerror=()=>box.insertAdjacentHTML('beforeend','<p class="mut">浏览器播放不了这个视频（编码不支持），可以下载后看</p>')}});
preview.register({name:'音频',ext:['mp3','wav','ogg','oga','m4a','aac','flac','opus'],render(p,url,box){
  box.innerHTML=`<div class="pv-info"><div style="font-size:40px">🎵</div><audio controls preload="metadata" src="${esc(url)}" style="width:min(520px,100%)"></audio></div>`;
  box.querySelector('audio').onerror=()=>box.insertAdjacentHTML('beforeend','<p class="mut">浏览器播放不了这个音频，可以下载后听</p>')}});
// 关掉预览对话框：清空内容（视频 / 音频停止播放），去掉宽屏 / 全屏样式
dlg.addEventListener('close',()=>{if(dlg.classList.contains('pvw'))$('#dlgB').innerHTML='';dlg.classList.remove('pvw','pvfull')});
