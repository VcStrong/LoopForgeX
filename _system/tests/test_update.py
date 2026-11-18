"""程序更新（检查、保护、排队、MCP 提示、守护进程自检 / 退回）的单测。

    cd _system && .venv/bin/python -m unittest tests.test_update -v     （没有 .venv 用 python3）

用临时 KB_ROOT、临时「远端」裸仓库和它的克隆当程序目录（KB_PROG_DIR），不碰真实数据、不重启真的网页。
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix='kb-upd-test-'))
ORIGIN, PROG = TMP / 'origin.git', TMP / 'prog'
os.environ['KB_ROOT'] = str(TMP / 'kb')
os.environ['KB_PROG_DIR'] = str(PROG)
(TMP / 'kb').mkdir()
GENV = {**os.environ, 'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t', 'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@t'}


def git(cwd, *a):
    return subprocess.run(['git', '-C', str(cwd), *a], check=True, capture_output=True, text=True, env=GENV).stdout.strip()


def commit(cwd, name, text, msg):
    (Path(cwd) / name).parent.mkdir(parents=True, exist_ok=True)
    (Path(cwd) / name).write_text(text)
    git(cwd, 'add', name)
    git(cwd, 'commit', '-q', '-m', msg)


subprocess.run(['git', 'init', '-q', '--bare', '-b', 'main', str(ORIGIN)], check=True)
SEED = TMP / 'seed'
subprocess.run(['git', 'init', '-q', '-b', 'main', str(SEED)], check=True)
commit(SEED, '_system/requirements.txt', 'a\n', 'init')
git(SEED, 'remote', 'add', 'origin', str(ORIGIN))
git(SEED, 'push', '-q', 'origin', 'main')
subprocess.run(['git', 'clone', '-q', str(ORIGIN), str(PROG)], check=True)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kblib.steward import update as upd  # noqa: E402

SENT = []
upd._notify = lambda t, x, k='': SENT.append((t, x, k))
# 别的测试先 import 过 kb 时，模块里的路径已按它们的环境定了：这里全部钉到本测试的临时目录，绝不碰真实程序目录和数据
upd.PROG, upd.SYS, upd.PERIODS = PROG, TMP / 'kb' / '_system', TMP / 'kb' / '期'
upd.STATE = upd.SYS / '管家' / '更新.json'
_SET = TMP / '设置.json'
upd.load_settings = lambda: json.loads(_SET.read_text('utf-8')) if _SET.exists() else {}
upd.save_settings = lambda s: _SET.write_text(json.dumps(s, ensure_ascii=False), 'utf-8')


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


class T(unittest.TestCase):
    def setUp(self):
        git(PROG, 'checkout', '-q', 'main')
        git(PROG, 'fetch', '-q', 'origin')
        git(PROG, 'reset', '-q', '--hard', 'origin/main')
        upd.STATE.unlink(missing_ok=True)
        upd.WEB['on'] = False
        SENT.clear()

    def push(self, msgs):
        git(SEED, 'pull', '-q', 'origin', 'main')
        for i, m in enumerate(msgs):
            commit(SEED, f'f{time.time_ns()}_{i}.txt', m, m)
        git(SEED, 'push', '-q', 'origin', 'main')

    def test_version_and_behind(self):
        self.assertEqual(upd.code_version(), git(PROG, 'rev-parse', 'HEAD'))
        self.assertEqual(upd.update_check()['落后'], 0)
        self.push(['feat: 新功能 A', 'fix: 修好 B', 'docs: 说明'])
        v = upd.update_check()
        self.assertEqual(v['落后'], 3)
        self.assertEqual([c['类型'] for c in v['提交']], ['其他', '修复', '新功能'])
        self.assertEqual(v['不能更新'], [])
        self.assertEqual(len(SENT), 1)                    # 推送一次
        upd.update_check()
        self.assertEqual(len(SENT), 1)                    # 同一个远端提交不重复推
        cards = upd.update_cards()
        self.assertTrue(cards and cards[0]['动作'] == 'update' and '3 项更新' in cards[0]['标题'])
        git(PROG, 'pull', '-q', '--ff-only')              # 手动 pull 过：不再显示落后
        self.assertEqual(upd.update_status()['落后'], 0)

    def test_blockers(self):
        self.push(['feat: x'])
        (PROG / '_system' / 'requirements.txt').write_text('changed\n')
        v = upd.update_check()
        self.assertTrue(any('本地改动' in w for w in v['不能更新']))
        self.assertEqual(upd.update_cards()[0]['动作'], '')
        git(PROG, 'checkout', '-q', '--', '.')
        commit(PROG, 'local.txt', 'x', 'local')
        self.assertTrue(any('分叉' in w for w in upd.update_check()['不能更新']))
        git(PROG, 'checkout', '-q', '-b', 'dev')
        self.assertTrue(any('不是 main' in w for w in upd.update_check()['不能更新']))
        upd.WEB['on'] = True
        with self.assertRaises(upd.KBError):
            upd.update_apply()

    def test_busy_queues(self):
        self.push(['feat: y'])
        d = upd.PERIODS / '某期'
        d.mkdir(parents=True, exist_ok=True)
        (d / '期信息.json').write_text(json.dumps({'各端开发': {'Android': {'状态': '开发中', 'pid': os.getpid()}}}, ensure_ascii=False))
        try:
            self.assertEqual(upd.update_check()['在跑'], ['某期'])
            with self.assertRaises(upd.KBError):           # 不是网页进程：不能更新
                upd.update_apply()
            upd.WEB['on'] = True
            old = git(PROG, 'rev-parse', 'HEAD')
            self.assertIn('等它们结束', upd.update_apply())
            self.assertEqual(git(PROG, 'rev-parse', 'HEAD'), old)      # 排队，没动代码
            self.assertTrue(upd.update_status()['排队'])
        finally:
            (d / '期信息.json').unlink()

    def test_settings(self):
        self.assertEqual(upd.update_settings(), {'模式': '只提示', '推送': True})
        upd.update_set('自动', False)
        self.assertEqual(upd.update_settings(), {'模式': '自动', '推送': False})
        with self.assertRaises(upd.KBError):
            upd.update_set('乱写')
        upd.update_set('关')
        self.push(['feat: z'])
        upd.update_check()
        self.assertEqual(upd.update_cards(), [])
        self.assertEqual(upd.update_tick(), '')
        upd.update_set('只提示', True)

    def test_mcp_notice(self):
        upd._BOOT, upd._BOOT_SAID[0] = upd.code_version(), False
        self.assertEqual(upd.update_mcp_notice(), '')
        self.push(['feat: m'])
        git(PROG, 'pull', '-q', '--ff-only')
        self.assertIn('/mcp', upd.update_mcp_notice())
        self.assertEqual(upd.update_mcp_notice(), '')      # 只提示一次

    def _watch(self, port, old, new, wait, argv):
        args = {'state': str(upd.STATE), 'prog': str(PROG), 'old': old, 'new': new, 'port': port, 'pid': 999999, 'wait': wait,
                'req': False, 'py': sys.executable, 'managed': '', 'label': '', 'argv': argv, 'log': str(TMP / 'web.log')}
        subprocess.run([sys.executable, '-c', upd._WATCH, json.dumps(args)], timeout=60, capture_output=True)
        return json.loads(upd.STATE.read_text('utf-8'))

    def test_watch_ok_and_rollback(self):
        old = git(PROG, 'rev-parse', 'HEAD')
        self.push(['feat: w'])
        git(PROG, 'pull', '-q', '--ff-only')
        new = git(PROG, 'rev-parse', 'HEAD')
        port = free_port()
        srv = subprocess.Popen([sys.executable, '-c', f'''
import http.server
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
    def log_message(self, *a): pass
http.server.HTTPServer(("127.0.0.1", {port}), H).serve_forever()'''])
        try:
            time.sleep(0.5)
            upd._save({'阶段': '重启中', '上次更新': {'旧': old, '新': new}, '启动': {'提交': new, '自检': '通过'}})
            st = self._watch(port, old, new, 10, [])
            self.assertEqual(st['上次更新']['结果'], '已更新')
            self.assertEqual(git(PROG, 'rev-parse', 'HEAD'), new)
            # 自检失败：退回原提交，按原命令重新拉起（这里拉起一个监听同端口的假网页）
            upd._save({'阶段': '重启中', '上次更新': {'旧': old, '新': new}, '启动': {'提交': new, '自检': '失败', '错误': 'boom'}})
        finally:
            srv.terminate()
            srv.wait()
        argv = [sys.executable, '-c', f'''
import http.server
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers()
    def log_message(self, *a): pass
http.server.HTTPServer(("127.0.0.1", {port}), H).handle_request()''']
        st = self._watch(port, old, new, 10, argv)
        self.assertEqual(st['上次更新']['结果'], '已退回')
        self.assertIn('boom', st['上次更新']['说明'])
        self.assertEqual(git(PROG, 'rev-parse', 'HEAD'), old)


if __name__ == '__main__':
    unittest.main()
