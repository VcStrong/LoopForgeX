// ==== Markdown 渲染、文档页
// md(文字, {base}) → {html, toc}：marked（GFM：表格、删除线、任务列表、嵌套引用、图片）+ DOMPurify 过滤脚本。
// 两个库在 /ui/vendor/，core 加载后异步拉（不拖慢首屏）；拉到之前用下面的轻量解析（mdLite），拉到后当前页重画一次。
// base：md 文件所在目录，相对路径的图片 / 链接改成 fileUrl。toc：[{n 级别, id, t 文字}]，标题 id 是 h0 h1 …
const SAFE_URL=u=>!/^\s*(javascript|vbscript|data):/i.test(String(u).replace(/[\u0000-\u001f\s]+/g,''));
function mdInline(t){
  const codes=[];t=esc(t).replace(/`([^`]+)`/g,(_,c)=>{codes.push(c);return '\u0000'+(codes.length-1)+'\u0000'});
  t=t.replace(/\*\*([^*]+)\*\*/g,'<b>$1</b>')
     .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g,(_,a,u)=>SAFE_URL(u.replace(/&amp;/g,'&'))?`<a href="${u}" target="_blank" rel="noopener">${a}</a>`:a)
     .replace(/(^|[\s（(：:])(https?:\/\/[^\s<）)，。]+)/g,'$1<a href="$2" target="_blank" rel="noopener">$2</a>');
  return t.replace(/\u0000(\d+)\u0000/g,(_,i)=>`<code>${codes[i]}</code>`);
}
function mdLite(src){
  const L=src.replace(/\r/g,'').split('\n'),out=[],toc=[];let i=0;
  const isList=l=>/^\s*([-*+]|\d+\.)\s+/.test(l);
  while(i<L.length){
    const l=L[i];
    if(/^```/.test(l)){const b=[];i++;while(i<L.length&&!/^```/.test(L[i]))b.push(L[i++]);i++;out.push(`<pre><code>${esc(b.join('\n'))}</code></pre>`);continue}
    let m=l.match(/^(#{1,4})\s+(.*)$/);
    if(m){const n=m[1].length,id='h'+toc.length;toc.push({n,id,t:m[2].replace(/[`*]/g,'')});out.push(`<h${n} id="${id}">${mdInline(m[2])}</h${n}>`);i++;continue}
    if(/^\s*(---+|\*\*\*+)\s*$/.test(l)){out.push('<hr>');i++;continue}
    if(/^>/.test(l)){const b=[];while(i<L.length&&/^>/.test(L[i]))b.push(L[i++].replace(/^>\s?/,''));out.push(`<blockquote>${b.map(mdInline).join('<br>')}</blockquote>`);continue}
    if(/^\|/.test(l)&&i+1<L.length&&/^\|?\s*:?-+/.test(L[i+1])){
      const row=r=>r.trim().replace(/^\||\|$/g,'').split(/(?<!\\)\|/).map(c=>mdInline(c.trim().replace(/\\\|/g,'|')));
      const h=row(l);i+=2;const rs=[];while(i<L.length&&/^\|/.test(L[i]))rs.push(row(L[i++]));
      out.push(`<div class="tw"><table><tr>${h.map(c=>`<th>${c}</th>`).join('')}</tr>${rs.map(r=>`<tr>${r.map(c=>`<td>${c}</td>`).join('')}</tr>`).join('')}</table></div>`);continue}
    if(isList(l)){
      const items=[];while(i<L.length&&(isList(L[i])||(/^\s{2,}\S/.test(L[i])&&items.length))){
        const mm=L[i].match(/^(\s*)([-*+]|\d+\.)\s+(.*)$/);
        if(mm)items.push({d:Math.floor(mm[1].length/2),o:/\d/.test(mm[2]),t:mm[3]});else items[items.length-1].t+=' '+L[i].trim();i++}
      let h='',st=[];
      for(const it of items){
        while(st.length>it.d+1)h+=`</li></${st.pop()}>`;
        if(st.length<it.d+1){const tg=it.o?'ol':'ul';h+=`<${tg}>`;st.push(tg)}else h+='</li>';
        h+=`<li>${mdInline(it.t)}`}
      while(st.length)h+=`</li></${st.pop()}>`;out.push(h);continue}
    if(!l.trim()){i++;continue}
    const p=[];while(i<L.length&&L[i].trim()&&!/^(#{1,4}\s|```|>|\|)/.test(L[i])&&!isList(L[i]))p.push(L[i++]);
    out.push(`<p>${p.map(mdInline).join('<br>')}</p>`);
  }
  return {html:out.join('\n'),toc};
}
let _mdM=null;const _mdSt={toc:[],base:''};
const isRel=u=>u&&!/^([a-z][a-z0-9+.-]*:|\/\/|#|\/)/i.test(u);
const relPath=(base,u)=>{const parts=(base+'/'+decodeURI(u.split(/[?#]/)[0])).split('/'),out=[];
  for(const x of parts){if(x==='..')out.pop();else if(x&&x!=='.')out.push(x)}return '/'+out.join('/')};
function mdEngine(){
  if(_mdM||!window.marked||!window.DOMPurify)return _mdM;
  const m=new marked.Marked({gfm:true,breaks:true});
  m.use({renderer:{
    heading({tokens,depth,text}){const id='h'+_mdSt.toc.length;_mdSt.toc.push({n:depth,id,t:String(text).replace(/[`*]/g,'')});
      return `<h${depth} id="${id}">${this.parser.parseInline(tokens)}</h${depth}>\n`},
    link({href,title,tokens}){const t=this.parser.parseInline(tokens);if(!SAFE_URL(href))return t;
      if(_mdSt.base&&isRel(href))return `<a data-read="${esc(relPath(_mdSt.base,href))}">${t}</a>`;   // 相对链接 = 知识库里的另一个文件，点了在预览里打开
      return `<a href="${esc(href)}"${title?` title="${esc(title)}"`:''} target="_blank" rel="noopener">${t}</a>`},
    image({href,title,text}){if(!SAFE_URL(href)&&!/^data:image\/(png|jpe?g|gif|webp);/i.test(href))return esc(text);
      const src=_mdSt.base&&isRel(href)?fileUrl(relPath(_mdSt.base,href)):href;
      return `<img src="${esc(src)}" alt="${esc(text)}"${title?` title="${esc(title)}"`:''} loading="lazy">`},
  }});
  DOMPurify.addHook('afterSanitizeAttributes',n=>{if(n.tagName!=='A')return;const h=n.getAttribute('href')||'';
    if(!h||h.startsWith('#')){n.removeAttribute('target');n.removeAttribute('rel')}else{n.setAttribute('target','_blank');n.setAttribute('rel','noopener')}});
  return _mdM=m}
function md(src,opt={}){
  const m=mdEngine();if(!m)return mdLite(String(src??''));
  _mdSt.toc=[];_mdSt.base=(opt.base||'').replace(/\/+$/,'');
  let html;try{html=m.parse(String(src??''))}catch(e){return mdLite(String(src??''))}
  html=DOMPurify.sanitize(html,{ADD_ATTR:['target','data-read'],FORBID_TAGS:['style','form','iframe','object','embed','base','meta','link'],FORBID_ATTR:['style']})
    .replace(/<table>/g,'<div class="tw"><table>').replace(/<\/table>/g,'</table></div>');
  return {html,toc:_mdSt.toc.slice()}}
// md 渲染后的增强（预览、文档页用）：代码块高亮、mermaid 画图、相对链接在预览里打开。都是用到才加载库
async function mdEnhance(el){
  if(!el)return;
  el.querySelectorAll('a[data-read]').forEach(a=>{a.style.cursor='pointer';a.onclick=e=>{e.preventDefault();readFile(a.dataset.read)}});
  const mer=[...el.querySelectorAll('pre>code.language-mermaid')];
  const codes=[...el.querySelectorAll('pre>code[class*="language-"]')].filter(c=>!c.classList.contains('language-mermaid'));
  if(codes.length)loadVendor('hljs').then(()=>codes.forEach(c=>{const l=(c.className.match(/language-([\w+-]+)/)||[])[1];
    if(l&&hljs.getLanguage(l))try{c.innerHTML=hljs.highlight(c.textContent,{language:l,ignoreIllegals:true}).value;c.classList.add('hljs')}catch(_){}})).catch(()=>{});
  if(mer.length)try{await loadVendor('mermaid');
    mermaid.initialize({startOnLoad:false,securityLevel:'strict',theme:matchMedia('(prefers-color-scheme: dark)').matches&&document.documentElement.dataset.theme!=='light'||document.documentElement.dataset.theme==='dark'?'dark':'default'});
    for(const c of mer){try{const {svg}=await mermaid.render('mmd'+Math.random().toString(36).slice(2),c.textContent);
      const d=document.createElement('div');d.className='mmd';d.innerHTML=DOMPurify.sanitize(svg,{USE_PROFILES:{svg:true,svgFilters:true,html:true},ADD_TAGS:['foreignObject']});c.parentElement.replaceWith(d)}catch(e){c.parentElement.insertAdjacentHTML('beforebegin',`<div class="mut small">mermaid 画图失败：${esc(e.message||e)}</div>`)}}
  }catch(e){toast(e.message)}}
async function docs(name){
  const list=(await api('/api/docs')).docs;
  if(!list.length){app.innerHTML='<p class="mut">知识库根目录下还没有说明文档（.md）。</p>';return}
  const cur=list.find(x=>x.name===name)||list[0];
  const d=await api('/api/doc?name='+enc(cur.name)),r=md(d.text,{base:String(d.path||'').replace(/\/[^/]*$/,'')});
  app.innerHTML=`<div class="docs"><nav class="dnav">${list.map(x=>`<a href="#/d/${enc(x.name)}" class="${x.name===cur.name?'on':''}">${esc(x.name)}</a>
    ${x.name===cur.name?`<div class="toc">${r.toc.filter(t=>t.n>1&&t.n<4).map(t=>`<a class="h${t.n}" data-id="${t.id}">${esc(t.t)}</a>`).join('')}</div>`:''}`).join('')}</nav>
    <article class="md" id="docMd">${r.html}<p class="mut small" style="margin-top:24px">${esc(d.path)}　·　更新于 ${when(d.mtime)}</p></article></div>`;
  mdEnhance($('#docMd'));
  app.querySelectorAll('.toc a').forEach(a=>a.onclick=()=>document.getElementById(a.dataset.id).scrollIntoView({behavior:'smooth'}));
}

