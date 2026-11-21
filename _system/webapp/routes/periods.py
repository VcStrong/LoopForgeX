"""接口 · 期 / 事项、资料、记录、检索、模糊添加。每个接口一个函数，用 @get / @post 注册（见 webapp/router.py）。"""
from webapp.router import get, post  # noqa: F401
from webapp.shared import *  # noqa: F401,F403


@get('/api/events')
def _get_api_events(self, u, q):
    return self._events()


@get('/api/overview')
def _get_api_overview(self, u, q):
    def run():
        if q.get('project'):
            from kblib.core import authz
            if q['project'] not in authz.visible_projects(self.ident.get('user', '')):
                raise authz.Denied(authz.MSG_PROJ)
            self.ident['project'] = q['project']
        v = api_overview()
        v['project'] = self.ident.get('project', '')
        return v
    return self._guard(run)


@get('/api/period')
def _get_api_period(self, u, q):
    def run():
        from kblib.core import authz
        v = api_period(q.get('name', ''))
        v.update(kb.period_info(v['name']))                       # 负责人 / 项目 / 版本号（REQ-002 REQ-003 REQ-030）
        v['can'] = authz.period_can(self.ident, v['name'])
        return v
    return self._guard(run)


@get('/api/adds')
def _get_api_adds(self, u, q):
    return self._guard(lambda: {'items': kb.add_sessions()})


@get('/api/task/memo')
def _get_api_task_memo(self, u, q):
    return self._guard(lambda: kb.task_memo(q.get('name', '')))


@get('/api/repos/choices')
def _get_api_repos_choices(self, u, q):
    return self._guard(lambda: {'items': kb.repo_choices()})


@get('/api/period/talk')
def _get_api_period_talk(self, u, q):
    return self._guard(lambda: kb.period_talk(q.get('name', '')))


@get('/api/period/live')
def _get_api_period_live(self, u, q):
    return self._guard(lambda: kb.period_live(q.get('name', '')))


@get('/api/search')
def _get_api_search(self, u, q):
    return self._guard(lambda: dict(zip(('hits', 'mode'), kb.search_hits(
        q.get('q', ''), q.get('period') or None, q.get('cat') or None,
        q.get('scope', 'all'), None, int(q.get('limit', 30))))))


@get('/api/read')
def _get_api_read(self, u, q):
    return self._guard(lambda: {'text': kb.read_file(q.get('path', ''), int(q.get('start', 1)), 3000)})


@get('/api/text')
def _get_api_text(self, u, q):
    """预览用：按行取文本（不带行号），编码自动识别；docx / pdf / pptx 等取抽出来的文字。
    {lines, start, end, total, ok}：ok=False 表示这种文件抽不出文字（前端显示文件信息 + 下载）。"""
    def run():
        p = Path(q.get('path', '')).expanduser().resolve()
        if not p.is_file() or not kb._allowed(p):
            raise kb.KBError('只能读取知识库和已登记仓库里的文件')
        text = kb.extract(p)
        if text is None:
            return {'ok': False, 'lines': [], 'start': 1, 'end': 0, 'total': 0}
        lines = text.splitlines()
        start = max(1, int(q.get('start') or 1))
        n = max(1, min(20000, int(q.get('n') or 3000)))
        end = min(len(lines), start + n - 1)
        return {'ok': True, 'lines': lines[start - 1:end], 'start': start, 'end': end, 'total': len(lines)}
    return self._guard(run)


def _same_machine(h):
    """浏览器和网页服务在同一台机器：直连 127.0.0.1 / ::1，且不是反向代理转进来的（有 X-Forwarded-For / X-Real-IP 说明真实用户在别处）。"""
    ip = (h.client_address[0] if h.client_address else '') or ''
    if ip.startswith('::ffff:'):
        ip = ip[7:]
    return ip in ('127.0.0.1', '::1') and not h.headers.get('X-Forwarded-For') and not h.headers.get('X-Real-IP')


def _open_in_desktop(p: Path):
    """在本机文件管理器里打开目录：macOS 用 open，Linux 有桌面时用 xdg-open。打开了返回 True。"""
    import subprocess
    if sys.platform == 'darwin':
        cmd = ['open', str(p)]
    elif sys.platform.startswith('linux') and (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')) \
            and shutil.which('xdg-open'):
        cmd = ['xdg-open', str(p)]
    else:
        return False
    try:
        return subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


DIR_LIST_MAX = 1000


@post('/api/open_dir', page_only=True)
def _post_api_open_dir(self, u, n, body):
    """点资料里的目录：同一台机器就在 Finder / 文件管理器里打开；远端访问、打不开或 list=true 时返回目录内容，网页弹窗列出。
    路径只限知识库和已登记仓库（kb._allowed，和文件预览同一个白名单）；列表跳过 . 开头的隐藏文件。"""
    def run():
        raw = str(body.get('path') or '')
        if not raw:
            raise kb.KBError('缺少目录路径')
        p = Path(raw).expanduser().resolve()
        if not kb._allowed(p):
            raise kb.KBError('只能打开知识库和已登记仓库里的目录')
        if not p.is_dir():
            raise kb.KBError(f'目录不存在：{p}')
        if not body.get('list') and _same_machine(self) and _open_in_desktop(p):
            return {'opened': True, 'path': str(p), 'msg': f'已在{"Finder" if sys.platform == "darwin" else "文件管理器"}打开：{p.name}'}
        items = []
        for x in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            if x.name.startswith('.'):
                continue
            try:
                st = x.stat()
                isdir = x.is_dir()
            except OSError:
                continue
            items.append({'name': x.name, 'path': str(x), 'isDir': isdir, 'size': 0 if isdir else st.st_size, 'mtime': st.st_mtime})
            if len(items) >= DIR_LIST_MAX:
                break
        parent = str(p.parent) if p.parent != p and kb._allowed(p.parent) else ''
        return {'opened': False, 'path': str(p), 'name': p.name, 'parent': parent, 'items': items,
                'truncated': len(items) >= DIR_LIST_MAX}
    return self._guard(run)


@post('/api/upload', raw=True)
def _post_api_upload(self, u, n, body):
    period = unquote(self.headers.get('X-Period', ''))
    cat = unquote(self.headers.get('X-Category', '')) or None
    fname = kb.safe_name(unquote(self.headers.get('X-Filename', '')))
    tmp = kb.SYS / 'tmp' / uuid.uuid4().hex
    tmp.mkdir(parents=True)
    f = tmp / fname
    with f.open('wb') as fp:
        left = n
        while left > 0:
            chunk = self.rfile.read(min(left, 1 << 20))
            if not chunk:
                break
            fp.write(chunk)
            left -= len(chunk)

    def run():
        try:
            return {'msg': kb.add_material(period, f, cat, '网页上传')}
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return self._guard(run)


@post('/api/task/nav', page_only=True)
def _post_api_task_nav(self, u, n, body):
    return self._guard(lambda: {'msg': kb.task_nav(body.get('period', ''), bool(body.get('show')))})


@post('/api/task/item', page_only=True)
def _post_api_task_item(self, u, n, body):
    def run():
        p, k, act = body.get('period', ''), body.get('key', ''), body.get('action', '')
        if act == 'add':
            return {'msg': kb.task_item_add(p, body.get('text', ''))}
        if act == 'delete':
            return {'msg': kb.task_item_delete(p, k)}
        if act == 'repo':
            return {'msg': '；'.join(kb._setup_task(p, '', body.get('text', ''), '用户（网页）')) or '没有变化'}
        if act == 'push':
            return {'msg': kb.task_item_push(p, k, body.get('text', ''))}
        return {'msg': kb.task_item_set(p, k, body.get('status', ''), body.get('text', ''), by='用户（网页）')}
    return self._guard(run)


@post('/api/task/push', page_only=True)
def _post_api_task_push(self, u, n, body):
    return self._guard(lambda: {'msg': kb.task_push(body.get('period', ''), body.get('text', ''))})


@post('/api/period/say', page_only=True)
def _post_api_period_say(self, u, n, body):
    return self._guard(lambda: {'msg': kb.period_say(body.get('period', ''), body.get('text', ''), body.get('target', ''),
                                                     body.get('interrupt', True) is not False, mode=body.get('mode') or 'insert',
                                                     files=body.get('files') or [])})


@post('/api/new')
def _post_api_new(self, u, n, body):
    return self._guard(lambda: {'msg': kb.create_period(body.get('name', ''), body.get('summary', ''))})


@post('/api/task')
def _post_api_task(self, u, n, body):
    return self._guard(lambda: {'msg': kb.create_task(body.get('name', ''), body.get('summary', ''), body.get('kind', ''),
                                                      body.get('source', ''), body.get('priority', ''), body.get('bugs'),
                                                      by='用户（网页）'), 'name': kb.safe_name(body.get('name', ''))})


@post('/api/period/delete', page_only=True)
def _post_api_period_delete(self, u, n, body):
    def run():
        if body.get('confirm', '').strip() != kb.period_dir(body.get('period', '')).name:
            raise kb.KBError('输入的名称不一致，没有删除')
        return {'msg': kb.delete_period(body.get('period', ''))}
    return self._guard(run)


@post('/api/task/meta')
def _post_api_task_meta(self, u, n, body):
    return self._guard(lambda: {'msg': kb.update_task(body.get('period', ''), body.get('kind'), body.get('source'),
                                                      body.get('priority'), body.get('summary'))})


@post('/api/inbox')
def _post_api_inbox(self, u, n, body):
    def run():
        fs = [p for p in kb.INBOX.iterdir() if not p.name.startswith('.')]
        names = body.get('files')
        fs = [p for p in fs if not names or p.name in names]
        return {'msg': '\n'.join(kb.add_material(body.get('period', ''), p) for p in fs) or '收件箱是空的'}
    return self._guard(run)


@post('/api/note')
def _post_api_note(self, u, n, body):
    def run():
        f = {k: body.get(k) for k in ('content', 'category', 'kind', 'title', 'url', 'source')}
        if not body.get('id'):
            msg = kb.add_note(body.get('period', ''), **f)
            new, olds = kb.design_conflicts(body.get('period', ''), f'{f["url"] or ""} {f["content"] or ""}')
            return {'msg': msg, 'new': new, 'replace': olds}
        return {'msg': kb.update_note(body.get('period', ''), body['id'], bool(body.get('delete')),
                                      内容=f['content'], 分类=f['category'], 类型=f['kind'],
                                      标题=f['title'], 链接=f['url'], 来自=f['source'])}
    return self._guard(run)


@post('/api/log')
def _post_api_log(self, u, n, body):
    return self._guard(lambda: {'msg': kb.add_log(body.get('period', ''), body.get('note', ''))})


@post('/api/status')
def _post_api_status(self, u, n, body):
    def run():
        if body.get('status') == '已批准开发':
            raise kb.KBError('批准开发请在终端运行 kb approve')
        return {'msg': kb.set_status(body.get('period', ''), body.get('status', ''), body.get('note', ''),
                                     by_human=True)}
    return self._guard(run)


@post('/api/verify')
def _post_api_verify(self, u, n, body):
    return self._guard(lambda: {'msg': kb.verify_source(body.get('period', ''), body.get('sid', ''),
                                                        body.get('status', ''), body.get('note', ''))})


@post('/api/fixlink')
def _post_api_fixlink(self, u, n, body):
    return self._guard(lambda: {'msg': kb.fix_link(body.get('period', ''), body.get('old', ''), body.get('new', ''),
                                                   body.get('reason', ''), by='用户（网页）')})


@post('/api/refresh')
def _post_api_refresh(self, u, n, body):
    return self._guard(lambda: {'msg': kb.refresh_sources(body.get('period', ''), body.get('sid', ''), by='用户（网页）')})


@post('/api/refresh/claude', page_only=True)
def _post_api_refresh_claude(self, u, n, body):
    return self._guard(lambda: {'msg': kb.claude_update(body.get('period', ''))})


@post('/api/add', page_only=True)
def _post_api_add(self, u, n, body):
    return self._guard(lambda: {'msg': kb.claude_add(body.get('text', ''), body.get('period', ''))})


@post('/api/reindex')
def _post_api_reindex(self, u, n, body):
    return self._guard(lambda: {'msg': kb.reindex(body.get('target', 'all'))})


@get('/api/archive')
def _get_api_archive(self, u, q):
    return self._guard(lambda: {'items': kb.archive_list(q.get('q', ''), q.get('kind', '')), '天数': kb.archive_days()})


@post('/api/archive', page_only=True)
def _post_api_archive(self, u, n, body):
    return self._guard(lambda: {'msg': kb.period_archive(body.get('period', ''), body.get('on', True) is not False)})


@post('/api/archive/days', page_only=True)
def _post_api_archive_days(self, u, n, body):
    def run():
        s = kb.load_settings()
        s['归档天数'] = max(0, int(body.get('days') or 0))
        kb.save_settings(s)
        return {'msg': f'已完成满 {s["归档天数"]} 天自动归档' if s['归档天数'] else '不自动归档（只手动归档）'}
    return self._guard(run)
