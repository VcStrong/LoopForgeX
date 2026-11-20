"""期里的后台任务：插件在后台跑的任务登记进注册表、开发面板能看到能停；删期时一并停掉，删后后台写回不会让这一期再出现。

    cd _system && .venv/bin/python -m unittest tests.test_bgjobs -v     （没有 .venv 用 python3）

临时 KB_ROOT / HOME，假的「QA用例」插件（sleep 后像 case 生成平台那样 mkdir -p 写产物），不碰真实数据、不连平台。
"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix='kb-bg-test-'))
os.environ['KB_ROOT'] = str(TMP / 'kb')
os.environ['HOME'] = str(TMP / 'home')
(TMP / 'kb').mkdir()
(TMP / 'home').mkdir()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import kb  # noqa: E402
from kblib.plugins import registry  # noqa: E402
from kblib.infra import bgjobs  # noqa: E402

REAL = Path(__file__).resolve().parents[2] / '插件' / 'case生成平台' / 'main.py'
PLUG = TMP / 'plug'
PLUG.mkdir()
(PLUG / 'main.py').write_text('''import os, sys, time
from pathlib import Path
time.sleep(float(os.environ.get('FAKE_SLEEP', '30')))
d = Path(os.environ['KB_PERIOD_DIR'])
(d / '其他资料' / 'QA用例').mkdir(parents=True, exist_ok=True)      # 老插件的写法：期删了也会把目录建回来
(d / '其他资料' / 'QA用例' / 'agent-test.json').write_text('{}')
print('已拉回 1 个产物')
''', 'utf-8')
FAKE = {'id': 'fakeqa', '名称': '假 case 平台', '启用': True, '能力': ['QA用例'], '命令': ['{python}', 'main.py'],
        '目录': str(PLUG), '超时': 120, '配置': {}}
registry.tool_plugins = lambda: [FAKE]
registry._plugin_base = lambda p, by: {}


def wait(cond, sec=10):
    for _ in range(sec * 10):
        if cond():
            return True
        time.sleep(0.1)
    return False


class BgJobs(unittest.TestCase):
    def setUp(self):
        kb.init()

    def test_plugin_job_visible_and_stoppable(self):
        kb.create_period('综合版21-房间新人进场欢迎A')
        n = '综合版21-房间新人进场欢迎A'
        kb.qa_case_start(kb.period_dir(n), by='插件测试')
        self.assertTrue(wait(lambda: any(x['键'].startswith('job:') and bgjobs.jobs_of(n, True)[0].get('子进程')
                                         for x in kb.period_bg(n))))
        x = next(x for x in kb.period_bg(n) if x['键'].startswith('job:'))
        self.assertIn('假 case 平台', x['名称'])
        self.assertEqual(x['发起'], '插件测试')
        self.assertTrue(x['开始'] and x['可停止'])
        pid = bgjobs.jobs_of(n, True)[0]['子进程'][0]
        acts = kb.period_activity(n)
        self.assertTrue(any('假 case 平台' in g['名称'] and g['运行中'] for g in acts))
        print(kb.period_bg_stop(n, x['键']))
        self.assertTrue(wait(lambda: not kb.period_bg(n)))
        self.assertFalse(bgjobs._alive(pid))
        self.assertEqual(bgjobs.jobs_of(n)[0]['状态'], '已停止')
        self.assertFalse((kb.period_dir(n) / '其他资料' / 'QA用例' / 'agent-test.json').exists())

    def test_delete_stops_jobs_and_stays_deleted(self):
        n = '综合版21-房间新人进场欢迎B'
        kb.create_period(n)
        d = kb.period_dir(n)
        kb.qa_case_start(d, by='插件测试')
        self.assertTrue(wait(lambda: kb.period_bg(n)))
        msg = kb.delete_period(n)
        print(msg)
        self.assertIn('停掉了在跑的', msg)
        self.assertNotIn(n, kb.period_names())
        self.assertTrue(wait(lambda: not bgjobs.jobs_of(n, True)))      # 线程收尾要一点时间
        # 后台晚到的写入（插件老写法 / 别的模块 mkdir -p）把目录建回来：列表不认，巡检收走
        (d / '其他资料' / 'QA用例').mkdir(parents=True)
        kb.log(d, '插件', '晚到的记录')
        self.assertNotIn(n, kb.period_names())
        with self.assertRaises(kb.KBError):
            kb.period_dir(n)
        self.assertEqual(kb.tomb_sweep(), [n])
        self.assertFalse(d.exists())
        kb.log(d, '插件', '目录没了也不建回来')
        self.assertFalse(d.exists())
        # 同名重新建一期：墓碑清掉
        kb.create_period(n)
        self.assertIn(n, kb.period_names())

    @unittest.skipUnless(REAL.exists(), '没有 case 生成平台插件（公开版不带）')
    def test_plugin_refuses_deleted_period(self):
        """真插件：期信息.json 不在（期已删）时不写回产物。"""
        import subprocess
        real = REAL
        code = ('import sys, runpy, os; sys.argv=["main.py"]; g=runpy.run_path(%r, run_name="x"); '
                'from pathlib import Path; g["save_state"](Path(os.environ["KB_PERIOD_DIR"]), {})' % str(real))
        gone = TMP / 'kb' / '期' / '已删的期'
        r = subprocess.run([sys.executable, '-c', code], env=dict(os.environ, KB_PERIOD_DIR=str(gone), KB_PERIOD='已删的期',
                                                                 KB_PLUGIN_CONFIG='{}'), capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(gone.exists())


if __name__ == '__main__':
    unittest.main()


class StopRace(unittest.TestCase):
    def test_stop_before_subprocess_starts(self):
        """登记了任务、插件子进程还没起来时点停止：插件不再跑。"""
        kb.init()
        n = '综合版21-停止竞态'
        kb.create_period(n)
        jid = bgjobs.job_start(n, '测试', '调用插件')
        bgjobs.job_stop(jid, '测试')
        t0 = time.time()
        with self.assertRaises(kb.KBError):
            registry.call_plugin('fakeqa', ['自动', n], period=n)
        self.assertLess(time.time() - t0, 5)
        bgjobs.job_end(jid)
