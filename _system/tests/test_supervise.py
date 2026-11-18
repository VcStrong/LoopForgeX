"""监工（中断后自动接着做）的单测和端到端测试。

    cd _system && .venv/bin/python -m unittest tests.test_supervise -v     （没有 .venv 用 python3）

用临时 KB_ROOT、临时 HOME 和一个假引擎（按控制文件依次输出「被用量限制打断」或「正常做完」，
续跑时检查 --resume 带的是原会话号），不碰真实数据，不起真的 AI 会话。
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix='kb-sup-test-'))
os.environ['KB_ROOT'] = str(TMP / 'kb')
os.environ['HOME'] = str(TMP / 'home')
(TMP / 'kb').mkdir()
(TMP / 'home').mkdir()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import kb  # noqa: E402
from kblib.ai import devsession, engines, supervise as sup  # noqa: E402
from kblib.stack import dev as stackdev  # noqa: E402
from kblib.stack.plan import _sdir  # noqa: E402

CTRL = TMP / 'ctrl.json'          # 假引擎接下来每次运行的结果：["limit", "ok", ...]
CALLS = TMP / 'calls.jsonl'       # 假引擎每次被调用的参数
FAKE = TMP / 'fake-engine'
FAKE.write_text(f'''#!{sys.executable}
import json, os, sys, time
args = sys.argv[1:]
ctrl = {str(CTRL)!r}
xs = json.load(open(ctrl))
mode = xs.pop(0) if xs else 'ok'
json.dump(xs, open(ctrl, 'w'))
resume = args[args.index('--resume') + 1] if '--resume' in args else ''
sid = resume or 'sess-%d' % os.getpid()
open({str(CALLS)!r}, 'a').write(json.dumps({{'mode': mode, 'resume': resume, 'sid': sid, 'prompt': args[args.index('-p') + 1]}}, ensure_ascii=False) + '\\n')
print(json.dumps({{'type': 'system', 'subtype': 'init', 'session_id': sid}}), flush=True)
print(json.dumps({{'type': 'assistant', 'message': {{'content': [{{'type': 'text', 'text': '我在讨论 rate limit 的代码（正文，不能当成被限流）'}}]}}}}), flush=True)
time.sleep(float(os.environ.get('FAKE_SLEEP', '1.5')))
if mode == 'limit':
    print(json.dumps({{'type': 'assistant', 'error': 'rate_limit', 'message': {{'content': [{{'type': 'text', 'text': "You've hit your limit · resets 3pm (Asia/Shanghai)"}}]}}}}), flush=True)
    print(json.dumps({{'type': 'result', 'subtype': 'success', 'is_error': True, 'result': "You've hit your limit · resets 3pm (Asia/Shanghai)", 'session_id': sid}}), flush=True)
    sys.exit(1)
print(json.dumps({{'type': 'result', 'subtype': 'success', 'is_error': False, 'result': '做完了', 'num_turns': 3, 'session_id': sid}}), flush=True)
''', 'utf-8')
FAKE.chmod(0o755)
ENG = dict(engines.BUILTIN_ENGINES['claude'], id='fake', 名称='假引擎', 命令=str(FAKE))
devsession.period_engine = lambda m: ENG
stackdev.role_engine = lambda *a, **k: ENG


def ctrl(*modes):
    CTRL.write_text(json.dumps(list(modes)), 'utf-8')


def calls():
    try:
        return [json.loads(x) for x in CALLS.read_text('utf-8').splitlines()]
    except OSError:
        return []


def repo(name):
    r = TMP / 'repos' / name
    r.mkdir(parents=True)
    subprocess.run(['git', 'init', '-q', str(r)], check=True)
    return r


def period(name, **meta):
    try:
        d = kb.period_dir(name)
    except kb.KBError:
        kb.create_period(name)
        d = kb.period_dir(name)
    m = kb.load_meta(d)
    m.update(meta)
    kb.save_meta(d, m)
    return d


def wait(cond, sec=20):
    end = time.time() + sec
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.2)
    return False


def due_now(eid):
    sup._update(eid, 恢复时间=(datetime.now() - timedelta(minutes=1)).strftime('%Y-%m-%d %H:%M'))


class ClassifyTest(unittest.TestCase):
    def test_parse_reset(self):
        base = datetime(2026, 11, 18, 10, 0)
        self.assertEqual(sup.parse_reset("You've hit your limit · resets 3pm (Asia/Shanghai)", base), datetime(2026, 11, 18, 15, 0))
        self.assertEqual(sup.parse_reset('resets 9am', base), datetime(2026, 11, 19, 9, 0))          # 已经过了：明天
        self.assertEqual(sup.parse_reset('Claude AI usage limit reached|1795000000', base), datetime.fromtimestamp(1795000000))
        self.assertEqual(sup.parse_reset('try again in 1 hour 5 minutes', base), base + timedelta(minutes=65))
        self.assertEqual(sup.parse_reset("You've hit your usage limit. Try again at 3:05 PM.", base), datetime(2026, 11, 18, 15, 5))
        self.assertEqual(sup.parse_reset('rate limited, retry-after: 120', base), base + timedelta(seconds=120))
        self.assertIsNone(sup.parse_reset('something else', base))

    def _log(self, lines):
        f = TMP / f'log-{time.time_ns()}.jsonl'
        f.write_text('\n'.join([json.dumps({'type': 'kb.engine', 'engine': 'claude', 'format': 'Claude stream-json'})] + lines) + '\n', 'utf-8')
        return f

    def test_classify(self):
        ok = self._log([json.dumps({'type': 'result', 'is_error': False, 'result': '好了'})])
        self.assertEqual(sup.classify(ok, 0)['类别'], '做完')
        lim = self._log([json.dumps({'type': 'result', 'is_error': True, 'result': 'Claude AI usage limit reached|1795000000'})])
        c = sup.classify(lim, 1)
        self.assertEqual((c['类别'], c['原因']), ('外部中断', '用量限制'))
        self.assertTrue(c['恢复时间'])
        net = self._log(['Error: connect ECONNRESET 1.2.3.4:443'])
        self.assertEqual(sup.classify(net, 1)['原因'], '网络')
        crash = self._log([json.dumps({'type': 'system', 'session_id': 'x'})])
        self.assertEqual(sup.classify(crash, -9)['原因'], '引擎崩溃')
        self.assertEqual(sup.classify(crash, None)['原因'], '机器重启')
        # 正文里讨论 rate limit、但最后是正常的失败结果：真失败，不是限流
        talk = self._log([json.dumps({'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': 'handle 429 rate limit'}]}}),
                          json.dumps({'type': 'result', 'is_error': True, 'result': '编译失败：缺少依赖'})])
        self.assertEqual(sup.classify(talk, 1)['类别'], '真失败')
        self.assertEqual(sup.classify(lim, 1, stopped=True)['类别'], '用户停止')

    def test_backoff_and_limit(self):
        x = sup.record('台账测试', 'dev', {'原因': '网络', '详情': ''}, count=2)
        self.assertEqual(x['状态'], '待续')
        at = datetime.strptime(x['恢复时间'], '%Y-%m-%d %H:%M')
        self.assertTrue(timedelta(minutes=13) < at - datetime.now() <= timedelta(minutes=15))
        y = sup.record('台账测试', 'dev', {'原因': '网络', '详情': ''}, count=sup.settings()['上限'] + 1)
        self.assertEqual(y['状态'], '已停下')
        self.assertEqual(sup.get(x['编号'])['状态'], '已取代')


class EndToEndTest(unittest.TestCase):
    def setUp(self):
        CALLS.unlink(missing_ok=True)
        self._fd = devsession.flow_done

    def tearDown(self):
        devsession.flow_done = self._fd

    def _dev(self, name, task=False):
        root = repo(name)
        d = period(name, 状态='开发中', 开发仓库=str(root), **({'类型': '事项'} if task else {}))
        extra = dict(续跑次数=0, 类型='跟进', 仅对话=True) if task else dict(续跑次数=0)
        devsession._dev_spawn(d, root, 'main', '开始做', '', **extra)
        return d

    def _session(self, d):
        return kb.load_meta(d).get('开发会话') or {}

    def test_dev_resume_and_message_once(self):
        ctrl('limit', 'ok')
        d = self._dev('单仓库测试期')
        m = kb.load_meta(d)                 # 会话这一轮从工具结果里收到一条网页消息（已交给会话，还没处理）
        msg = {'时间': kb.now(), '内容': '顺便把按钮文案改成「保存」', '方式': '排队', '状态': '待送达', '会话': 'dev'}
        m.setdefault('对话', []).append(msg)
        sup.deliver([m['对话'][-1]], m['开发会话']['日志'])
        kb.save_meta(d, m)
        self.assertTrue(wait(lambda: self._session(d).get('状态') == '中断待续'))
        s = self._session(d)
        self.assertEqual(s['中断']['原因'], '用量限制')
        self.assertTrue(s['中断']['恢复时间'].endswith('15:02'))      # resets 3pm + 2 分钟
        self.assertNotIn('未续跑原因', s)
        x = sup.entries('单仓库测试期', states=sup.OPEN)[0]
        self.assertGreater(x['恢复时间'], kb.now())
        self.assertEqual(sup.tick(), [])                       # 没到点不续
        devsession.flow_done = lambda *a, **k: True          # 续上这一轮就算流程走完（不再自动续跑）
        due_now(x['编号'])
        out = sup.tick()
        self.assertEqual(len(out), 1, out)
        self.assertTrue(wait(lambda: self._session(d).get('状态') == '已结束'))
        c = calls()
        self.assertEqual(len(c), 2)
        self.assertEqual(c[1]['resume'], s['会话号'])                 # 用原会话号接着做
        self.assertEqual(c[1]['resume'], c[0]['sid'])
        self.assertIn('核对实际进度', c[1]['prompt'])
        self.assertEqual(c[1]['prompt'].count('顺便把按钮文案改成「保存」'), 1)   # 没处理的消息补发一次
        m = kb.load_meta(d)
        self.assertEqual(m['对话'][-1]['状态'], '已处理')
        self.assertEqual(sup.get(x['编号'])['状态'], '已续')
        self.assertEqual(sup.tick(), [])                       # 不会再续第二遍
        self.assertEqual(len(calls()), 2)

    def test_task_item_resume(self):
        ctrl('limit', 'ok')
        d = self._dev('事项条目测试', task=True)
        self.assertTrue(wait(lambda: self._session(d).get('状态') == '中断待续'))
        x = sup.entries('事项条目测试', states=sup.OPEN)[0]
        self.assertEqual(x['会话'], '事项条目会话')
        due_now(x['编号'])
        sup.tick()
        self.assertTrue(wait(lambda: self._session(d).get('状态') == '已结束'))
        c = calls()
        self.assertEqual(c[1]['resume'], c[0]['sid'])
        self.assertIn('被打断前用户交代的事', c[1]['prompt'])

    def test_user_stop_not_resumed(self):
        ctrl('limit')
        d = self._dev('停止测试期')
        self.assertTrue(wait(lambda: self._session(d).get('状态') == '中断待续'))
        x = sup.entries('停止测试期', states=sup.OPEN)[0]
        kb.dev_stop('停止测试期')
        due_now(x['编号'])
        self.assertEqual(sup.tick(), [])
        self.assertEqual(sup.get(x['编号'])['状态'], '不用了')
        self.assertEqual(self._session(d)['状态'], '已停止')
        self.assertEqual(len(calls()), 1)

    def test_running_user_stop_is_not_interruption(self):
        ctrl('limit')
        d = self._dev('运行中停止测试')
        time.sleep(0.5)
        kb.dev_stop('运行中停止测试')
        time.sleep(1)
        self.assertEqual(sup.entries('运行中停止测试'), [])

    def test_stack_end_resume(self):
        ctrl('limit', 'ok')
        root = repo('stack-android')
        d = period('全栈测试期', 状态='开发中', 全栈={'状态': '开发中'})
        _sdir(d).mkdir(parents=True, exist_ok=True)
        (_sdir(d) / '方案.json').write_text(json.dumps({'各端': [{'端': 'Android', '涉及': True, '改动': ['加按钮']}], '需求': []}), 'utf-8')
        m = kb.load_meta(d)
        m['各端开发'] = {'Android': {'状态': '开发中', '轮次': 0, '仓库': str(root), '分支': '', '版本库': 'git', '起点': 'HEAD'}}
        kb.save_meta(d, m)
        proc, logf, _ = stackdev._spawn(d, 'Android', '开发', '做 Android', str(root))
        m = kb.load_meta(d)
        m['各端开发']['Android'].update(pid=proc.pid, 日志=str(logf))
        kb.save_meta(d, m)
        end = lambda: (kb.load_meta(d).get('各端开发') or {}).get('Android') or {}
        self.assertTrue(wait(lambda: end().get('状态') == '中断待续'))
        self.assertNotEqual(end()['状态'], '失败')
        self.assertEqual(end()['中断']['原因'], '用量限制')
        x = sup.entries('全栈测试期', 'end', states=sup.OPEN)[0]
        due_now(x['编号'])
        out = sup.tick()
        self.assertEqual(len(out), 1, out)
        self.assertEqual(end()['状态'], '开发中')
        kb.stack_report('全栈测试期', 'Android', '完成', '接着做完了')       # 续上的这一轮按要求汇报
        self.assertTrue(wait(lambda: end().get('状态') in ('待审查', '审查中', '要你看', '审查通过')))
        # 汇报后会起审查会话（假引擎），等它结束再收尾，别让它吃掉下一个用例的假引擎脚本
        self.assertTrue(wait(lambda: end().get('状态') in ('要你看', '审查通过'), 30))
        c = calls()
        self.assertEqual(c[1]['resume'], c[0]['sid'])
        self.assertIn('kb_stack_report', c[1]['prompt'])
        self.assertEqual(sup.get(x['编号'])['状态'], '已续')


if __name__ == '__main__':
    unittest.main()
