"""执行机代理 kb runner（REQ-011 REQ-014 REQ-017 REQ-029 REQ-037 REQ-039）。

    cd _system && python3 -m unittest tests.test_runner_agent -v

起一个最小的假控制面（只实现 /api/runner/*，记下收到的请求），执行机作为子进程（独立 KB_ROOT / HOME）跑 mock 引擎的完整周期。
"""
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SYS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SYS_DIR))


class Fake:
    """假控制面：tasks 队列按顺序派出；cancel_on 里的任务在第一次回传日志后要求取消。"""

    def __init__(self):
        self.reqs = []
        self.tasks = []
        self.cancel_on = set()
        self.homes = {}            # 任务 → 回传日志时看到的私有 HOME 状态
        self.data = None
        self.lock = threading.Lock()
        self.proto = 1

    def handle(self, path, body, auth):
        with self.lock:
            self.reqs.append((path, body, auth))
        if path == '/api/runner/register':
            if auth != 'Bearer kbreg_good':
                return 401, {'error': '注册令牌无效'}
            return 200, {'id': 'r1', 'token': 'kbr_secret', '标签': body.get('自动标签')}
        if auth != 'Bearer kbr_secret':
            return 401, {'error': 'bad'}
        if path == '/api/runner/heartbeat':
            return 200, {'cancel': [], '状态': '在线'}
        if path == '/api/runner/claim':
            if body.get('协议版本') != self.proto:
                return 409, {'error': '协议版本不一致', 'need': {'协议版本': self.proto}}
            with self.lock:
                t = self.tasks.pop(0) if self.tasks else None
            if not t:
                time.sleep(0.5)
            return 200, {'task': t}
        if path == '/api/runner/event':
            tid = body['task']
            h = Path(self.data) / 'sessions' / tid / 'home'
            if h.exists() and tid not in self.homes:
                self.homes[tid] = {
                    'mode': stat.S_IMODE(h.stat().st_mode),
                    'cred': (h / '.claude' / '.credentials.json').read_text() if (h / '.claude' / '.credentials.json').exists() else '',
                    'gitcred_mode': stat.S_IMODE((h / '.git-credentials').stat().st_mode) if (h / '.git-credentials').exists() else 0,
                    'gitcred': (h / '.git-credentials').read_text() if (h / '.git-credentials').exists() else ''}
            return 200, {'cancel': tid in self.cancel_on and bool(body.get('日志'))}
        return 200, {'msg': 'ok'}

    def serve(self):
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get('Content-Length') or 0)
                raw = self.rfile.read(n)
                try:
                    body = json.loads(raw or b'{}')
                except ValueError:
                    body = {'raw': len(raw)}
                code, out = fake.handle(self.path, body, self.headers.get('Authorization', ''))
                b = json.dumps(out, ensure_ascii=False).encode()
                self.send_response(code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(b)))
                self.end_headers()
                self.wfile.write(b)

        self.srv = ThreadingHTTPServer(('127.0.0.1', 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        return f'http://127.0.0.1:{self.srv.server_address[1]}'

    def of(self, path):
        with self.lock:
            return [b for p, b, _ in self.reqs if p == path]


def start_agent(url, data, token, extra_env=None, capacity=2):
    env = {k: v for k, v in os.environ.items() if not k.startswith('KB_')}
    env.update(KB_ROOT=str(Path(data) / 'root'), KB_RUNNER_DATA=str(data), HOME=str(Path(data) / 'realhome'),
               PYTHONPATH=str(SYS_DIR), KB_MOCK_STEP='0.4', KB_SECRET_BACKEND='file', **(extra_env or {}))
    (Path(data) / 'realhome').mkdir(parents=True, exist_ok=True)
    return subprocess.Popen([sys.executable, '-m', 'kblib.runner.agent', '--server', url, '--token', token, '--data', str(data),
                             '--capacity', str(capacity), '--name', 'test-runner'], env=env, cwd=str(SYS_DIR),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def wait(fn, timeout=40):
    t = time.time()
    while time.time() - t < timeout:
        v = fn()
        if v:
            return v
        time.sleep(0.2)
    return None


class TestRunnerAgent(unittest.TestCase):
    def test_bad_token_exit2(self):
        f = Fake()
        url = f.serve()
        with tempfile.TemporaryDirectory() as d:
            p = start_agent(url, d, 'kbreg_bad')
            out, _ = p.communicate(timeout=60)
            self.assertEqual(p.returncode, 2, out)
            self.assertIn('注册令牌无效或已用过，到网页重新生成', out)

    def test_full_cycle(self):
        f = Fake()
        url = f.serve()
        d = tempfile.mkdtemp(prefix='kb-runner-test-')
        f.data = d
        acc = lambda c: {'id': 'acc-' + c, '名称': c, '登录态': {'文件': {'.claude/.credentials.json': '{"who": "%s"}' % c}}}
        f.tasks = [
            {'id': 'T1', '类型': '开发', '期': '期一', '引擎': 'mock', '提示词': 'REFRESH 正常跑', 'sessionToken': 'kbs_x',
             '引擎账号': acc('A'), '凭据': {'git': [{'url': 'https://git.example.com', '用户名': 'alice', '密码': 'p@ss'}],
                                         '作者': {'name': 'alice', 'email': 'a@x.com'}}},
            {'id': 'T2', '类型': '开发', '期': '期二', '引擎': 'mock', '提示词': 'SLOW 等取消', '引擎账号': acc('B')},
        ]
        f.cancel_on = {'T2'}
        p = start_agent(url, d, 'kbreg_good')
        try:
            self.assertTrue(wait(lambda: len(f.of('/api/runner/done')) >= 2, 90), f.reqs[-5:])
            f.tasks.append({'id': 'T3', '类型': 'Bug分析', '期': '期一', '引擎': 'mock', '提示词': 'FAIL429'})
            self.assertTrue(wait(lambda: len(f.of('/api/runner/done')) >= 3, 60))
            self.assertTrue(wait(lambda: len(f.of('/api/runner/heartbeat')) >= 1, 20))
        finally:
            p.terminate()
            out = p.communicate(timeout=20)[0]
        done = {b['task']: b for b in f.of('/api/runner/done')}
        # 令牌文件 600（REQ-011）
        cfg = Path(d) / 'runner.json'
        self.assertEqual(stat.S_IMODE(cfg.stat().st_mode), 0o600)
        self.assertEqual(json.loads(cfg.read_text())['token'], 'kbr_secret')
        # 注册带自检 / 标签 / 协议版本
        reg = f.of('/api/runner/register')[0]
        self.assertEqual(reg['协议版本'], 1)
        self.assertTrue(any(t in ('darwin', 'linux') for t in reg['自动标签']))
        # 私有 HOME 0700、凭据 0600、两个会话互相看不到对方登录态（REQ-017 REQ-039）
        self.assertEqual(f.homes['T1']['mode'], 0o700)
        self.assertEqual(f.homes['T1']['gitcred_mode'], 0o600)
        self.assertIn('alice:p%40ss@git.example.com', f.homes['T1']['gitcred'])
        self.assertIn('"A"', f.homes['T1']['cred'])
        self.assertIn('"B"', f.homes['T2']['cred'])
        self.assertNotIn('"A"', f.homes['T2']['cred'])
        # 结束后私有目录删除
        self.assertFalse(any((Path(d) / 'sessions').iterdir()) if (Path(d) / 'sessions').exists() else False)
        # 日志增量回传（REQ-014）
        ev = [b for b in f.of('/api/runner/event') if b['task'] == 'T1' and b['日志']]
        self.assertTrue(ev)
        offs = [b['偏移'] for b in ev]
        self.assertEqual(offs, sorted(offs))
        self.assertIn('mock 引擎：完成', ''.join(b['日志'] for b in ev))
        # 完成 / 取消 / 外部原因失败
        self.assertEqual(done['T1']['结果'], '完成', out)
        self.assertEqual(done['T1']['登录态回写']['账号'], 'acc-A')          # 会话里刷新的登录态回写
        self.assertEqual(done['T2']['结果'], '取消')
        self.assertNotEqual(done['T2']['退出码'], 0)
        self.assertEqual(done['T3']['结果'], '失败')
        self.assertTrue(done['T3']['外部原因'])
        # 确认带副本
        acks = f.of('/api/runner/ack')
        self.assertTrue(all(a.get('副本', {}).get('占用会话') for a in acks))
        # 心跳字段
        hb = f.of('/api/runner/heartbeat')[0]
        for k in ('自动标签', '空闲容量', '副本', '手机', '磁盘', 'CPU', '内存', '网络', '会话'):
            self.assertIn(k, hb)

    def test_repo_copy_and_commits(self):
        """带仓库的任务：在本机池里分配副本（ack 上报副本信息），done 带本次新提交和未推送标记（REQ-015）。"""
        f = Fake()
        url = f.serve()
        d = tempfile.mkdtemp(prefix='kb-runner-repo-')
        f.data = d
        origin, seed = Path(d) / 'origin.git', Path(d) / 'seed'
        g = lambda p, *a: subprocess.run(['git', '-C', str(p), '-c', 'user.name=t', '-c', 'user.email=t@t', *a], check=True, capture_output=True)
        subprocess.run(['git', 'init', '-q', '--bare', str(origin)], check=True)
        subprocess.run(['git', 'init', '-q', str(seed)], check=True)
        (seed / 'a.txt').write_text('a')
        g(seed, 'add', '.')
        g(seed, 'commit', '-qm', 'init')
        g(seed, 'push', '-q', str(origin), 'HEAD:refs/heads/main')
        f.tasks = [{'id': 'R1', '类型': '开发', '期': '期一', '引擎': 'mock', '提示词': 'COMMIT', '仓库': 'app',
                    '克隆地址': 'file://' + str(origin), '分支': 'main',
                    '凭据': {'作者': {'name': 'alice', 'email': 'a@x.com'}}}]
        p = start_agent(url, d, 'kbreg_good')
        try:
            self.assertTrue(wait(lambda: f.of('/api/runner/done'), 90), f.reqs[-5:])
        finally:
            p.terminate()
            out = p.communicate(timeout=20)[0]
        dn = f.of('/api/runner/done')[0]
        self.assertEqual(dn['结果'], '完成', out + json.dumps(dn, ensure_ascii=False))
        self.assertEqual(len(dn['提交']), 1)
        self.assertTrue(dn['副本有未推送提交'])
        ack = f.of('/api/runner/ack')[0]['副本']
        self.assertEqual((ack['仓库'], ack['序号']), ('app', 1))
        self.assertTrue(ack['路径'].startswith(str((Path(d) / 'root').resolve())))
        log = subprocess.run(['git', '-C', ack['路径'], 'log', '-1', '--format=%an'], capture_output=True, text=True).stdout.strip()
        self.assertEqual(log, 'alice')                          # 提交作者是发起人（私有 HOME 的 .gitconfig）

    def test_proto_mismatch_stops_claim(self):
        f = Fake()
        f.proto = 2
        url = f.serve()
        with tempfile.TemporaryDirectory() as d:
            f.data = d
            p = start_agent(url, d, 'kbreg_good')
            try:
                self.assertTrue(wait(lambda: len(f.of('/api/runner/claim')) >= 1, 30))
                time.sleep(3)
                n = len(f.of('/api/runner/claim'))
                self.assertLessEqual(n, 2)                     # 不再频繁领活
                self.assertTrue(wait(lambda: len(f.of('/api/runner/heartbeat')) >= 1, 20))   # 心跳照常
            finally:
                p.terminate()
                out = p.communicate(timeout=20)[0]
            self.assertIn('控制面要求协议版本 2，本机是 1', out)

    def test_no_listen(self):
        """执行机只出站：代码里没有监听端口。"""
        src = ''.join(p.read_text('utf-8') for p in (SYS_DIR / 'kblib' / 'runner').glob('*.py'))
        self.assertIsNone(re.search(r'\.bind\(|\.listen\(|HTTPServer|socketserver', src))

    def test_gitlock_serial(self):
        from kblib.runner import gitlock
        with tempfile.TemporaryDirectory() as d:
            spans = []

            def job():
                with gitlock.repo_lock('repo', d):
                    t0 = time.time()
                    time.sleep(0.3)
                    spans.append((t0, time.time()))
            ts = [threading.Thread(target=job) for _ in range(3)]
            [t.start() for t in ts]
            [t.join() for t in ts]
            spans.sort()
            for a, b in zip(spans, spans[1:]):
                self.assertLessEqual(a[1], b[0] + 0.01)

    def test_disk_cleanup_keeps_changed(self):
        """磁盘 ≥80%：删空闲 7 天以上的干净副本，有改动的不删、占用中的不删、第 1 份不删（REQ-037）。"""
        script = r'''
import json, os, subprocess, sys, time
from pathlib import Path
root = Path(os.environ['KB_ROOT'])
origin = root.parent / 'origin.git'
subprocess.run(['git', 'init', '-q', '--bare', str(origin)], check=True)
seed = root.parent / 'seed'
subprocess.run(['git', 'init', '-q', str(seed)], check=True)
(seed / 'a.txt').write_text('a')
g = lambda p, *a: subprocess.run(['git', '-C', str(p), '-c', 'user.name=t', '-c', 'user.email=t@t', *a], check=True, capture_output=True)
g(seed, 'add', '.'); g(seed, 'commit', '-qm', 'init'); g(seed, 'push', '-q', str(origin), 'HEAD:refs/heads/master')
(root / '代码仓库.json').write_text(json.dumps({'app': {'克隆地址': 'file://' + str(origin)}}))
from kblib.infra import pool
from kblib.runner import disk
for i in (1, 2, 3, 4, 5):
    subprocess.run(['git', 'clone', '-q', 'file://' + str(origin), str(pool.repo_home('app') / str(i))], check=True)
(pool.repo_home('app') / '3' / 'a.txt').write_text('changed')
old = time.time() - 8 * 86400
with pool._locked() as s:
    for i in (1, 2, 3, 5):
        s[str((pool.repo_home('app') / str(i)).resolve())] = {'最后使用': old, '最后占用': 'bob'}
    s[str((pool.repo_home('app') / '4').resolve())] = {'最后使用': time.time()}
busy = [str(pool.repo_home('app') / '5')]
ratio = lambda: 0.85
res = disk.cleanup(str(root.parent), busy, ratio_fn=ratio)
left = sorted(p.name for p in pool.copies('app'))
print(json.dumps({'left': left, 'gone': [x['副本'] for x in res['清理']], 'kept': [x['副本'] for x in res['保留']],
                  'who': [x.get('最后谁用') for x in res['清理']]}, ensure_ascii=False))
'''
        with tempfile.TemporaryDirectory() as d:
            env = {k: v for k, v in os.environ.items() if not k.startswith('KB_')}
            env.update(KB_ROOT=str(Path(d) / 'root'), PYTHONPATH=str(SYS_DIR), HOME=d, KB_SECRET_BACKEND='file')
            (Path(d) / 'root').mkdir()
            r = subprocess.run([sys.executable, '-c', script], env=env, capture_output=True, text=True, cwd=str(SYS_DIR), timeout=120)
            self.assertEqual(r.returncode, 0, r.stderr)
            out = json.loads(r.stdout.strip().splitlines()[-1])
            self.assertEqual(out['left'], ['1', '3', '4', '5'])           # 2 删了；1 是基础克隆，3 有改动，4 最近用过，5 占用中
            self.assertIn('app 第 2 份', out['gone'])
            self.assertIn('app 第 3 份', out['kept'])
            self.assertIn('bob', out['who'])

    def test_session_git_creds(self):
        """会话凭据（REQ-017）：bot 推送时提交带 Co-authored-by 发起人；缺凭据时 pre-push 拒绝，不退回系统用户身份。"""
        from kblib.runner.home import SessionHome
        with tempfile.TemporaryDirectory() as d:
            origin, repo = Path(d) / 'o.git', Path(d) / 'w'
            subprocess.run(['git', 'init', '-q', '--bare', str(origin)], check=True)
            subprocess.run(['git', 'clone', '-q', str(origin), str(repo)], check=True, capture_output=True)
            hook = repo / '.git' / 'hooks' / 'commit-msg'
            hook.write_text('#!/bin/sh\necho "Change-Id: I123" >> "$1"\n')
            hook.chmod(0o755)

            def git(h, *a):
                env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
                env.update(h.env())
                env.update(GIT_AUTHOR_EMAIL='x@x', GIT_COMMITTER_EMAIL='x@x', GIT_AUTHOR_NAME='bot', GIT_COMMITTER_NAME='bot')
                return subprocess.run(['git', '-C', str(repo), *a], env=env, capture_output=True, text=True)
            h = SessionHome(d, 'B1').create()
            h.write_git({'git': {'方式': 'bot', '用户名': 'kb-bot', '密钥': 'tok', 'coauthor': 'alice'}})
            self.assertEqual(stat.S_IMODE((h.home / '.kb-git-cred').stat().st_mode), 0o600)
            (repo / 'a').write_text('1')
            git(h, 'add', 'a')
            self.assertEqual(git(h, 'commit', '-qm', '改动').returncode, 0)
            msg = git(h, 'log', '-1', '--format=%B').stdout
            self.assertIn('Co-authored-by: alice', msg)
            self.assertIn('Change-Id: I123', msg)                    # 仓库自己的钩子照跑
            h2 = SessionHome(d, 'B2').create()
            h2.write_git({'git': {'方式': '缺', '缺': '没有个人 Gerrit 凭据'}})
            r = git(h2, 'push', 'origin', 'HEAD:refs/heads/main')
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('没有个人 Gerrit 凭据', r.stderr)

    def test_session_home_cleanup_leftovers(self):
        from kblib.runner.home import SessionHome, cleanup_leftovers
        with tempfile.TemporaryDirectory() as d:
            h = SessionHome(d, 'X1').create()
            h.write_login({'文件': {'.codex/auth.json': {'k': 1}}, '环境': {'OPENAI_API_KEY': 'sk'}})
            self.assertEqual(stat.S_IMODE((h.home / '.codex' / 'auth.json').stat().st_mode), 0o600)
            self.assertEqual(h.env()['HOME'], str(h.home))
            with self.assertRaises(ValueError):
                h.write_login({'文件': {'../../escape': 'x'}})
            self.assertEqual(cleanup_leftovers(d, []), ['X1'])
            self.assertFalse(h.root.exists())


if __name__ == '__main__':
    unittest.main()
