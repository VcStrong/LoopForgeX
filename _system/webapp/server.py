"""网页服务：请求处理器（鉴权、本人操作检查、按注册表分发）和启动（后台循环）。接口本身在 webapp/routes/。"""
import importlib
import json
import selectors
import socket

from webapp.shared import *  # noqa: F401,F403
from webapp import router, policy
from kblib.core import authz, db as kbdb
from kblib.core.common import set_user_ctx, _USER_CTX

ROUTE_MODULES = ['static', 'periods', 'dev', 'stack', 'term', 'bugs', 'anr', 'plugins', 'settings', 'base', 'steward', 'update',
                 'users', 'runners', 'resources', 'admin']

for _m in ROUTE_MODULES:
    importlib.import_module(f'webapp.routes.{_m}')


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype='application/json; charset=utf-8'):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def _guard(self, fn):
        try:
            self._send(200, fn())
        except authz.Denied as e:
            self._send(403, {'error': str(e)})
        except kb.KBNeed as e:
            self._send(409, {'error': str(e), 'need': e.data})
        except kb.KBError as e:
            self._send(400, {'error': str(e)})
        except kbdb.DBBusy:
            self._send(500, {'error': '数据库忙，稍后重试'})
        except Exception as e:
            self._send(500, {'error': repr(e)})

    def _events(self):
        q = queue.Queue()
        with _SUBS_LOCK:
            _SUBS.add(q)
        try:
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(b'retry: 3000\n\n')
            self.wfile.flush()
            proj = self.ident.get('project', '') if getattr(self, 'ident', None) else ''
            while True:
                try:
                    ev = q.get(timeout=20)
                    if proj and ev.get('period') and kb.period_project(ev['period']) != proj:
                        continue                  # SSE 按项目过滤（REQ-031）
                    if proj and ev.get('project') and ev['project'] != proj:
                        continue
                    self.wfile.write(f'data: {json.dumps(ev, ensure_ascii=False)}\n\n'.encode())
                except queue.Empty:
                    self.wfile.write(b': ping\n\n')     # 保活，也用来发现浏览器已关闭
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            with _SUBS_LOCK:
                _SUBS.discard(q)

    def _from_page(self):
        # 开始/停止开发、回答、免打扰只接受本页面发起的请求（浏览器必带 Origin）
        origin = self.headers.get('Origin', '')
        return bool(origin) and origin.endswith('//' + self.headers.get('Host', ''))

    def _bearer(self):
        auth = self.headers.get('Authorization', '')
        return auth[7:].strip() if auth.startswith('Bearer ') else ''

    def _mcp(self, n):
        """/mcp：按令牌解析人 / 项目 / 会话，注入上下文；工具处理函数按当前用户判权限（REQ-005）。"""
        tok = self._bearer()
        sess = kb.remote_session(tok) if tok else None           # 旧的 ssh 远程会话令牌（进程内）
        ident = None if sess else kb.token_identity(tok)
        if ident and ident['kind'] not in ('个人', '会话'):
            ident = None
        if sess is None and ident is None:
            return self._send(401, {'error': '令牌无效：到网页「我的 → 令牌」生成个人令牌；会话令牌在会话结束后失效', 'login': True})
        try:
            msg = json.loads(self.rfile.read(n) or b'{}')
        except ValueError:
            return self._send(400, {'error': 'bad json'})
        from kblib.api import mcp as _m
        from kblib.core.common import with_sess_env, with_user
        if sess:
            env = {'KB_PERIOD': sess['KB_PERIOD'], 'KB_SESSION': sess['KB_SESSION']}
            ctx = {'via': 'AI', 'user': sess.get('发起人') or kb.local_user(), 'project': kb.period_project(sess['KB_PERIOD']),
                   'period': sess['KB_PERIOD'], 'session': sess['KB_SESSION'], 'engine': 'claude'}
        elif ident['kind'] == '会话':
            env = {'KB_PERIOD': ident['period'], 'KB_SESSION': ident['session']}
            ctx = {'via': 'AI', 'user': ident['user'], 'project': ident['project'], 'period': ident['period'],
                   'session': ident['session'], 'engine': ident['engine'] or '引擎'}
        else:
            env = {'KB_SESSION': '', 'KB_PERIOD': ''}
            prof = kb.profile_get(ident['user'])
            ctx = {'via': 'AI', 'user': ident['user'], 'project': '', 'engine': ident['name'] or '个人令牌',
                   'scope_project': prof.get('当前项目', '')}

        def run(x):
            return with_user(ctx, with_sess_env, env, _m.handle, x)
        if isinstance(msg, list):
            out = [r for r in (run(x) for x in msg) if r is not None]
        else:
            out = run(msg)
        if out is None or out == []:
            self.send_response(202)
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        return self._send(200, out)

    def _local_only(self):
        # 防止其他网页跨站调用写接口
        origin = self.headers.get('Origin', '')
        host = self.headers.get('Host', '')
        return not origin or origin.endswith('//' + host)

    def _term_proxy(self, u):
        """/term/<编号>/… 原样转发到这个终端的 ttyd（UNIX socket，不开端口）：普通请求转一次就关，WebSocket 升级后双向对拷。
        能走到这里已经过了登录校验；WebSocket 还要求是本页面发起的（Origin 和 Host 一致），防别的网站连进来。"""
        parts = u.path.split('/')
        sock = kb.term_socket(parts[2] if len(parts) > 2 else '')
        if not sock:
            return self._send(404, {'error': '终端不在了：回到期页面重新打开一个'})
        ws = self.headers.get('Upgrade', '').lower() == 'websocket'
        if ws and not self._from_page():
            return self._send(403, {'error': '只能在知识库网页上由本人使用'})
        up = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            up.connect(sock)
        except OSError as e:
            up.close()
            return self._send(502, {'error': f'连不上终端：{e}'})
        hdrs = ''.join(f'{k}: {v}\r\n' for k, v in self.headers.items() if ws or k.lower() not in ('connection', 'keep-alive'))
        up.sendall(f'{self.command} {self.path} HTTP/1.1\r\n{hdrs}{"" if ws else "Connection: close" + chr(13) + chr(10)}\r\n'.encode('latin-1'))
        self.close_connection = True
        cli = self.connection
        sel = selectors.DefaultSelector()
        sel.register(cli, selectors.EVENT_READ, up)
        sel.register(up, selectors.EVENT_READ, cli)
        try:
            while True:
                ev = sel.select(timeout=6 * 3600)
                if not ev:
                    break
                for key, _ in ev:
                    data = key.fileobj.recv(65536)
                    if not data:
                        return
                    key.data.sendall(data)
        except OSError:
            pass
        finally:
            sel.close()
            up.close()

    def _cookie(self):
        c = SimpleCookie(self.headers.get('Cookie', ''))
        return c['kb_s'].value if 'kb_s' in c else ''

    def _identity(self):
        """当前用户 = f(请求)（REQ-022：以后接 SSO 只换这一处）。返回上下文字典或 None（没登录）。
        单机模式（只听本机、没有带密码的账号）免登录，以本机用户（管理员）身份。"""
        if kb.single_mode():
            u = kb.local_user()
            prof = kb.profile_get(u)
            return {'user': u, 'via': '网页', 'project': kb.current_project_for(u, prof.get('当前项目', '')), 'single': True,
                    'status': '正常'}
        u = kb.check_session(self._cookie())
        if not u:
            return None
        prof = u.get('profile') or {}
        return {'user': u['name'], 'via': '网页', 'project': kb.current_project_for(u['name'], prof.get('当前项目', '')),
                'status': u.get('status') or '正常'}

    def _authed(self):
        self.ident = self._identity()
        if self.ident:
            self._ctx_tok = set_user_ctx(self.ident)
        return bool(self.ident)

    def _must_change(self, path):
        return self.ident and self.ident.get('status') == '待改密码' and path not in policy.MUST_CHANGE_OK and not path.startswith('/ui/')

    def handle_one_request(self):
        try:
            super().handle_one_request()
        finally:
            tok = getattr(self, '_ctx_tok', None)
            if tok is not None:
                try:
                    _USER_CTX.reset(tok)
                except ValueError:
                    pass
                self._ctx_tok = None

    def _https(self):
        return self.headers.get('X-Forwarded-Proto', '') == 'https'

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path == '/login':
            return self._send(200, LOGIN_PAGE, 'text/html; charset=utf-8')
        if u.path == '/api/web/whoami':                  # 启动时判断「占着端口的是不是这个知识库」用，不要登录、不带隐私
            from kblib.infra import webport as _wp
            return self._send(200, {'kb': True, 'root': _wp.root_id()})
        if u.path == '/api/artifact/download' and self._bearer().startswith('kbr_'):    # 执行机拉产物（REQ-049）
            return self.do_GET_runner(u, q)
        if not self._authed():
            if u.path == '/':
                self.send_response(302)
                self.send_header('Location', '/login')
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            return self._send(401, {'error': '要先登录', 'login': True})
        if self._must_change(u.path):
            return self._send(403, {'error': kb.MUST_CHANGE, 'mustChangePassword': True})
        if u.path.startswith('/term/'):                  # 网页终端：转发给这个终端的 ttyd（含 WebSocket）
            return self._term_proxy(u)
        if u.path.startswith('/ui/'):                    # 共用功能层和各套界面模板的静态文件
            r = ui_file(u.path[4:])
            return self._send(200, *r) if r else self._send(404, {'error': 'not found'})
        if u.path.startswith('/fs/'):                    # 资料文件按路径下发：html 预览里的相对资源（img/a.png）能找到
            why = self._path_denied(unquote(u.path[3:]))
            if why:
                return self._send(403, {'error': why})
            return send_file(self, unquote(u.path[3:]), q)
        for k in ('path', 'file'):                      # /file?path=、/api/read?path= 这类按路径读的也查项目（REQ-003）
            if q.get(k):
                why = self._path_denied(q[k])
                if why:
                    return self._send(403, {'error': why})
        if q.get('name') and u.path in ('/api/period', '/api/period/talk', '/api/period/live', '/api/task/memo') or \
                (q.get('period') and u.path.startswith('/api/')):
            pn = q.get('name') if u.path in ('/api/period', '/api/period/talk', '/api/period/live', '/api/task/memo') else q.get('period')
            if pn and (kb.PERIODS / pn).is_dir() and not authz.can(self.ident, '看', pn)[0]:
                return self._send(403, {'error': authz.MSG_PROJ})
        r = router.find('GET', u.path)
        if not r:
            return self._send(404, {'error': 'not found'})
        return r.fn(self, u, q)

    def _path_denied(self, p):
        """按路径访问期目录里的文件：不是自己项目的期返回原因（REQ-003）。"""
        try:
            rp = Path(p).expanduser().resolve()
            base = kb.PERIODS.resolve()
        except (OSError, ValueError):
            return ''
        if base in rp.parents:
            name = rp.relative_to(base).parts[0]
            if not authz.can(self.ident, '看', name)[0]:
                return authz.MSG_PROJ
        return ''

    def _login(self, n):
        try:
            b = json.loads(self.rfile.read(n) or b'{}') or {}
            t, must = kb.web_login(b.get('username', ''), b.get('password', ''))
        except (ValueError, kb.KBError) as e:
            return self._send(403, {'error': str(e) if isinstance(e, kb.KBError) else 'bad json'})
        body = json.dumps({'msg': '已登录', 'mustChangePassword': must}, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Set-Cookie', f'kb_s={t}; Path=/; HttpOnly; SameSite=Strict; Max-Age={kb.SESS_DAYS * 86400}'
                         + ('; Secure' if self._https() else ''))
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        u = urlparse(self.path)
        n = int(self.headers.get('Content-Length') or 0)
        if u.path.startswith('/api/runner/'):           # 执行机：Bearer 执行机令牌，绕过 Cookie（REQ-011）
            r = router.find('POST', u.path)
            if not r:
                return self._send(404, {'error': 'not found'})
            return r.fn(self, u, n, None)
        if not self._local_only():
            return self._send(403, {'error': 'forbidden'})
        if u.path == '/mcp':           # 个人令牌 kb_、会话令牌 kbs_（REQ-005）
            return self._mcp(n)
        if u.path == '/api/login':
            return self._login(n)
        if u.path == '/api/logout':
            kb.web_logout(self._cookie())
            self.send_response(200)
            self.send_header('Set-Cookie', 'kb_s=; Path=/; Max-Age=0')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        if not self._authed():
            return self._send(401, {'error': '要先登录', 'login': True})
        if self._must_change(u.path):
            return self._send(403, {'error': kb.MUST_CHANGE, 'mustChangePassword': True})
        r = router.find('POST', u.path)
        if not r:
            return self._send(404, {'error': 'not found'})
        if r.page_only and not self._from_page():
            return self._send(403, {'error': '只能在知识库网页上由本人操作'})
        if r.raw:
            return r.fn(self, u, n, None)
        try:
            body = json.loads(self.rfile.read(n) or b'{}')
        except ValueError:
            return self._send(400, {'error': '请求体不是 JSON'})
        act = policy.action_for(u.path, body if isinstance(body, dict) else {})
        if act:                                         # 登录人 + 角色权限（REQ-002）
            pn = policy.period_of(body if isinstance(body, dict) else {})
            pn = pn if pn and (kb.PERIODS / pn).is_dir() else None
            ok, why = authz.can(self.ident, act, pn, None if pn else (self.ident.get('project') if act != '管理' else None))
            if not ok:
                return self._send(403, {'error': why})
        return r.fn(self, u, n, body)

    def do_GET_runner(self, u, q):
        r = router.find('GET', u.path)
        return r.fn(self, u, q) if r else self._send(404, {'error': 'not found'})


def main(port=None, host=''):
    kb.init()
    host = host or os.environ.get('KB_HOST') or '127.0.0.1'
    from kblib.infra import webport as _wp        # 端口：--port > KB_PORT > _system/web.json > 11555；被占自动顺延
    want, source = _wp.resolve(port)
    try:
        port, note = _wp.pick(host, want)
    except kb.KBError as e:
        print(f'没有启动：{e}', flush=True)
        sys.exit(0 if '已经在' in str(e) else 2)
    if note:
        print(f'！{note}', flush=True)
    _wp.record_actual(port, want, source, note)
    _wp.RUNNING.update(port=port, host=host)
    msg = kb.ensure_migrated()                   # 升级后第一次启动：自动迁移到数据库（REQ-027）
    if msg:
        print(msg, flush=True)
    try:
        kb.check_bind(host)
    except kb.KBError as e:
        print(f'没有启动：{e}', flush=True)
        sys.exit(2)
    os.environ['KB_WEB_PORT'] = str(port)        # 各端远程会话的 ssh 反向通道连这个端口
    kb.stackdev_port(port)
    srv = ThreadingHTTPServer((host, port), H)
    print(f'知识库网页：http://{host}:{port}' + ('（团队模式：要登录）' if kb.auth_required() else '（单机模式：免登录）'), flush=True)
    kb.dev_reattach()
    from kblib.steward import update as _upd     # 程序更新：记下网页进程能重启自己；刚更新完的做一次自检，守护进程据此决定要不要退回
    threading.Thread(target=_upd.update_startup, args=(port, [sys.executable, *sys.argv], lambda: (api_overview(), kb.steward_view())),
                     daemon=True).start()
    threading.Thread(target=watcher, daemon=True).start()
    threading.Thread(target=dev_tick, daemon=True).start()
    threading.Thread(target=health_auto, daemon=True).start()
    from kblib.core import queue as _queue        # 任务队列 / 执行机掉线 / 资源池 / 监控：每 5 秒，租约保证只跑一份（REQ-009 REQ-013）
    threading.Thread(target=_queue.tick_loop, daemon=True).start()
    if kb.ZT_HOURS > 0:
        kb.ZT_STATE['next'] = time.strftime('%Y-%m-%d %H:%M', time.localtime(time.time() + 120))
        threading.Thread(target=zentao_auto, daemon=True).start()
    if STEWARD_MIN > 0:
        threading.Thread(target=steward_auto, daemon=True).start()
    if REFRESH_HOURS > 0:
        AUTO['next'] = time.strftime('%Y-%m-%d %H:%M', time.localtime(time.time() + 300))
        threading.Thread(target=auto_refresh, daemon=True).start()
    srv.serve_forever()

