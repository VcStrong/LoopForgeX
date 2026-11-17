# 第三方库（文件预览用）

放在仓库里、不走 CDN：服务器 / 内网部署可能不能上网。core 第一次用到时才加载（`loadVendor`，见 `ui/core/62-preview.js`），不拖慢首屏。
许可证原文在 `licenses/`。升级：换掉对应文件、改下表版本号即可。

| 文件 | 项目 | 版本 | 许可证 | 用途 |
|---|---|---|---|---|
| marked.min.js | [marked](https://github.com/markedjs/marked)（lib/marked.umd.js） | 18.2.0 | MIT | Markdown 渲染（KB.md） |
| purify.min.js | [DOMPurify](https://github.com/cure53/DOMPurify) | 3.4.18 | Apache-2.0 / MPL-2.0 | 过滤 md / docx / xlsx 转出来的 HTML，防 XSS |
| hljs/highlight.min.js | [highlight.js](https://github.com/highlightjs/highlight.js)（@highlightjs/cdn-assets，常用语言包） | 11.12.0 | BSD-3-Clause | 代码 / 日志 / json / xml 高亮（配色在 core.css） |
| mermaid.min.js | [mermaid](https://github.com/mermaid-js/mermaid) | 12.3.0 | MIT | md 里的 ```mermaid 代码块画图 |
| mammoth.min.js | [mammoth.js](https://github.com/mwilliamson/mammoth.js)（mammoth.browser.min.js） | 1.13.0 | BSD-2-Clause | Word（docx）转 HTML |
| xlsx.full.min.js | [SheetJS CE](https://git.sheetjs.com/sheetjs/sheetjs)（cdn.sheetjs.com 官方包，npm 上的 0.18.5 有已知漏洞） | 0.20.3 | Apache-2.0 | Excel / csv 转表格 |
| idiomorph.min.js | [idiomorph](https://github.com/bigskysoftware/idiomorph)（dist/idiomorph.min.js，htmx / Turbo 8 用的 DOM morph） | 0.8.0 | 0BSD | 局部刷新：期页面实时更新、全栈块重画时只改变了的节点，保住滚动、展开、焦点（morphHTML，见 ui/core/10-base.js） |
