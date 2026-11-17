"""接口 · 页面和静态文件。每个接口一个函数，用 @get / @post 注册（见 webapp/router.py）。"""
from webapp.router import get, post  # noqa: F401
from webapp.shared import *  # noqa: F401,F403


@get('/')
def _get_(self, u, q):
    return self._send(200, ui_page(q.get('ui', '')), 'text/html; charset=utf-8')


@get('/favicon.ico')
def _get_favicon_ico(self, u, q):
    return self._send(200, b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="8" fill="#3b5bdb"/><text x="16" y="21" font-size="13" text-anchor="middle" fill="#fff" font-family="sans-serif" font-weight="700">KB</text></svg>', 'image/svg+xml')


@get('/ui-templates.js')
def _get_ui_templates_js(self, u, q):
    return self._send(200, (PAGE.parent / 'ui-templates.js').read_bytes(), 'text/javascript; charset=utf-8')


@get('/ui-preview')
def _get_ui_preview(self, u, q):
    return self._send(200, (PAGE.parent / 'ui-preview.html').read_bytes(), 'text/html; charset=utf-8')


@get('/file')
def _get_file(self, u, q):
    """资料文件原样下发：Range、流式、CSP sandbox（见 shared.send_file）。"""
    return send_file(self, q.get('path', ''), q)


@get('/api/fileinfo')
def _get_api_fileinfo(self, u, q):
    """预览用：文件名、大小、修改时间（只限知识库和已登记仓库里的文件）。"""
    def run():
        p = Path(q.get('path', '')).expanduser().resolve()
        if not p.is_file() or not kb._allowed(p):
            raise kb.KBError('无此文件')
        st = p.stat()
        return {'name': p.name, 'path': str(p), 'size': st.st_size, 'mtime': st.st_mtime,
                'type': FILE_TYPES.get(p.suffix.lower()) or mimetypes.guess_type(p.name)[0] or ''}
    return self._guard(run)
