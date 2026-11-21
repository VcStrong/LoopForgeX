"""网页端口：启动前改（kb port / web.json / KB_PORT）、被占自动顺延、启动后在平台里改并重启、执行机跟着改连。

    cd _system && python3 -m unittest tests.test_webport -v
"""
import http.client
import json
import os
import socket
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path

if not os.environ.get('KB_ROOT', '').startswith(tempfile.gettempdir().rstrip('/')):
    TMP = Path(tempfile.mkdtemp(prefix='kb-port-test-'))
    os.environ['KB_ROOT'] = str(TMP / 'kb')
    os.environ['HOME'] = str(TMP / 'home')
    os.environ['KB_SECRET_BACKEND'] = 'file'
    os.environ['KB_MASTER_KEY'] = 'test-master-key'
    (TMP / 'kb').mkdir()
    (TMP / 'home').mkdir()
os.environ.pop('KB_PORT', None)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import kb  # noqa: E402
from kblib.core import db, queue  # noqa: E402
from kblib.infra import webport  # noqa: E402
from webapp.server import H  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402

kb.init()
SRV = ThreadingHTTPServer(('127.0.0.1', 0), H)
PORT = SRV.server_address[1]
threading.Thread(target=SRV.serve_forever, daemon=True).start()
webport.RUNNING.update(port=PORT, host='127.0.0.1')


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


def call(method, path, body=None):
    c = http.client.HTTPConnection('127.0.0.1', PORT, timeout=10)
    h = {'Content-Type': 'application/json', 'Origin': f'http://127.0.0.1:{PORT}', 'Host': f'127.0.0.1:{PORT}'}
    c.request(method, path, json.dumps(body) if body is not None else None, h)
    r = c.getresponse()
    return r.status, json.loads(r.read() or b'{}')


class Port(unittest.TestCase):
    def tearDown(self):
        webport.CONF.unlink(missing_ok=True)
        os.environ.pop('KB_PORT', None)

    def test_precedence(self):
        self.assertEqual(webport.resolve(None), (11555, '默认'))
        webport.save(11666)
        self.assertEqual(webport.resolve(None), (11666, '_system/web.json'))
        os.environ['KB_PORT'] = '11777'
        self.assertEqual(webport.resolve(None), (11777, '环境变量 KB_PORT'))
        self.assertEqual(webport.resolve(11888), (11888, '命令行 --port'))
        with self.assertRaises(kb.KBError):
            webport.save(80)
        with self.assertRaises(kb.KBError):
            webport.save('abc')
        self.assertEqual(webport.reset(), 11555)

    def test_busy_fallback(self):
        s = socket.socket()
        s.bind(('127.0.0.1', 0))
        s.listen(1)
        busy = s.getsockname()[1]
        try:
            p, note = webport.pick('127.0.0.1', busy)
            self.assertNotEqual(p, busy)
            self.assertIn(f'端口 {busy} 被别的程序占用，已改用 {p}', note)
        finally:
            s.close()

    def test_already_running_this_kb(self):
        with self.assertRaises(kb.KBError) as cm:
            webport.pick('127.0.0.1', PORT)          # 占着的就是本库的网页
        self.assertIn('已经在', str(cm.exception))

    def test_already_running_on_fallback_port(self):
        # 上次顺延到的端口上本库还活着（pid 在）：换配置端口也要认出来，不再起第二份
        webport.STATE.parent.mkdir(parents=True, exist_ok=True)
        webport.STATE.write_text(json.dumps({'端口': PORT, 'pid': os.getppid()}), 'utf-8')
        try:
            with self.assertRaises(kb.KBError) as cm:
                webport.pick('127.0.0.1', free_port())
            self.assertIn(str(PORT), str(cm.exception))
        finally:
            webport.STATE.unlink(missing_ok=True)

    def test_autostart_sync_and_argv(self):
        d = Path(tempfile.mkdtemp())
        pl, unit = d / 'a.plist', d / 'kb-web.service'
        kbbin = str(kb.SYS.parent / 'kb')
        pl.write_text(f'<array><string>{kbbin}</string><string>web</string><string>--host</string><string>127.0.0.1</string>'
                      '<string>--port</string><string>11555</string></array>')
        unit.write_text(f'ExecStart={kbbin} web --host 127.0.0.1 --port 11555\n')
        old = webport.LAUNCH_AGENT, webport.SYSTEMD_UNIT
        webport.LAUNCH_AGENT, webport.SYSTEMD_UNIT = pl, unit
        try:
            webport.save(12001)
            self.assertNotIn('--port', pl.read_text())
            self.assertNotIn('--port', unit.read_text())
        finally:
            webport.LAUNCH_AGENT, webport.SYSTEMD_UNIT = old
        self.assertEqual(webport.strip_port_args(['py', 'kb.py', 'web', '--port', '11555', '--host', 'x', '--port=9']),
                         ['py', 'kb.py', 'web', '--host', 'x'])

    def test_api_change_port(self):
        st, b = call('GET', '/api/web/port')
        self.assertEqual((st, b['端口'], b['可在线修改']), (200, PORT, True))
        st, b = call('GET', '/api/web/whoami')
        self.assertEqual((st, b['root']), (200, webport.root_id()))
        # 被占 / 不合法 / 同一个 → 400
        s = socket.socket()
        s.bind(('127.0.0.1', 0))
        s.listen(1)
        try:
            self.assertEqual(call('POST', '/api/web/port', {'port': s.getsockname()[1]})[0], 400)
        finally:
            s.close()
        self.assertEqual(call('POST', '/api/web/port', {'port': 80})[0], 400)
        self.assertEqual(call('POST', '/api/web/port', {'port': PORT})[0], 400)
        # 成功：写配置、给执行机下发新端口、安排重启（这里把重启换成记录）
        got = {}
        orig = webport.restart_after_notify
        webport.restart_after_notify = lambda argv, wait=20: got.update(argv=argv, wait=wait)
        try:
            new = free_port()
            st, b = call('POST', '/api/web/port', {'port': new})
        finally:
            webport.restart_after_notify = orig
        self.assertEqual(st, 200, b)
        self.assertEqual((b['port'], b['url']), (new, f'http://127.0.0.1:{new}/'))
        self.assertEqual(webport.configured(), (new, '_system/web.json'))
        self.assertNotIn('--port', got['argv'])
        self.assertEqual(webport.note_runner('R1'), {'新端口': new, '旧端口': PORT})
        webport.MOVE['端口'] = 0

    def test_docker_readonly(self):
        os.environ['KB_IN_DOCKER'] = '1'
        try:
            st, b = call('GET', '/api/web/port')
            self.assertFalse(b['可在线修改'])
            self.assertIn('KB_PUBLISH', b['不能改的原因'])
            self.assertEqual(call('POST', '/api/web/port', {'port': free_port()})[0], 400)
        finally:
            os.environ.pop('KB_IN_DOCKER')


class RunnerFollow(unittest.TestCase):
    def test_follow_port(self):
        from kblib.runner import agent
        d = Path(tempfile.mkdtemp())
        cfg = {'server': 'http://10.0.0.5:11555', 'token': 'kbr_x', 'tokens': {'http://10.0.0.5:11555': 'kbr_x'}}
        fake = types.SimpleNamespace(client=types.SimpleNamespace(server='http://10.0.0.5:11555', token='kbr_x'), cfg=cfg,
                                     data=str(d), server='http://10.0.0.5:11555')
        agent.Agent.follow_port(fake, 11666, 11555)
        self.assertEqual((fake.client.server, fake.server, cfg['server']), ('http://10.0.0.5:11666',) * 3)
        saved = json.loads(agent.cfg_path(d).read_text())
        self.assertEqual(saved['tokens']['http://10.0.0.5:11666'], 'kbr_x')
        self.assertEqual(saved['搬到']['http://10.0.0.5:11555'], 'http://10.0.0.5:11666')
        # 经反向代理（地址里的端口不是旧端口）的不动
        fake2 = types.SimpleNamespace(client=types.SimpleNamespace(server='https://kb.example.com', token='t'), cfg={}, data=str(d), server='')
        agent.Agent.follow_port(fake2, 11666, 11555)
        self.assertEqual(fake2.client.server, 'https://kb.example.com')

    def test_heartbeat_carries_port(self):
        rid = 'Rporttest'
        db.upsert('runners', 'id', id=rid, name='p', status='在线', local=0, disabled=0, last_hb=queue.now())
        webport.begin_move(12345, 11555, [rid])
        try:
            r = queue.heartbeat(rid, {})
            self.assertEqual((r['新端口'], r['旧端口']), (12345, 11555))
            self.assertIn(rid, webport.MOVE['通知到'])
        finally:
            webport.MOVE['端口'] = 0


class ZAuth(unittest.TestCase):
    """契约 v2 /api/web/port 的 401 / 403：有了用户以后（放最后跑，前面的用例是单机免登录模式）。"""

    def test_port_needs_admin(self):
        for n, r in (('wpadmin', '管理员'), ('wpmem', '普通')):
            if not db.user_get(n):
                kb.user_create_strict(n, n + 'pass1234', r)
                db.user_put(n, status='正常')

        def login(n):
            c = http.client.HTTPConnection('127.0.0.1', PORT, timeout=10)
            h = {'Content-Type': 'application/json', 'Origin': f'http://127.0.0.1:{PORT}', 'Host': f'127.0.0.1:{PORT}'}
            c.request('POST', '/api/login', json.dumps({'username': n, 'password': n + 'pass1234'}), h)
            r = c.getresponse()
            r.read()
            return (r.getheader('Set-Cookie') or '').split(';')[0]

        def as_(cookie, method, path, body=None):
            c = http.client.HTTPConnection('127.0.0.1', PORT, timeout=10)
            h = {'Content-Type': 'application/json', 'Origin': f'http://127.0.0.1:{PORT}', 'Host': f'127.0.0.1:{PORT}'}
            if cookie:
                h['Cookie'] = cookie
            c.request(method, path, json.dumps(body) if body is not None else None, h)
            r = c.getresponse()
            return r.status, json.loads(r.read() or b'{}')
        self.assertEqual(as_('', 'GET', '/api/web/port')[0], 401)
        self.assertEqual(as_('', 'POST', '/api/web/port', {'port': free_port()})[0], 401)
        st, b = as_('', 'GET', '/api/web/whoami')                     # 免登录
        self.assertEqual((st, b.get('kb')), (200, True))
        mem, adm = login('wpmem'), login('wpadmin')
        self.assertEqual(as_(mem, 'GET', '/api/web/port')[0], 200)
        st, b = as_(mem, 'POST', '/api/web/port', {'port': free_port()})
        self.assertEqual(st, 403, b)
        self.assertEqual(as_(adm, 'POST', '/api/web/port', {'port': 80})[0], 400)


if __name__ == '__main__':
    unittest.main()
