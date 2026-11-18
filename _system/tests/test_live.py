"""AI 实时状态一行（kblib/ai/live.py）的单测。

    cd _system && .venv/bin/python -m unittest tests.test_live -v     （没有 .venv 用 python3）

用临时 KB_ROOT、临时 HOME，伪造会话记录和 stream 日志，不起真的 AI 会话。
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix='kb-live-test-'))
os.environ['KB_ROOT'] = str(TMP / 'kb')
os.environ['HOME'] = str(TMP / 'home')
(TMP / 'kb').mkdir()
(TMP / 'home').mkdir()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import kb  # noqa: E402
from kblib.ai import live, supervise  # noqa: E402


def _line(j):
    return json.dumps(j, ensure_ascii=False) + '\n'


def agent_call(i):
    return _line({'type': 'assistant', 'message': {'content': [
        {'type': 'tool_use', 'id': f'toolu_{i}', 'name': 'Agent', 'input': {'description': f'专家 {i}'}}]}})


def tool_result(i):
    return _line({'type': 'user', 'message': {'content': [{'type': 'tool_result', 'tool_use_id': f'toolu_{i}', 'content': 'ok'}]}})


class LiveTest(unittest.TestCase):
    def setUp(self):
        kb.init()
        self.name = f'期{self._testMethodName}'
        kb.create_period(self.name)
        self.d = kb.period_dir(self.name)
        self.docs = TMP / f'docs-{self._testMethodName}'
        self.docs.mkdir()
        self.log = self.d / kb.DEV_DIR / 'dev.jsonl'
        self.log.parent.mkdir(exist_ok=True)
        self.log.write_text('')

    def meta(self, **kv):
        m = kb.load_meta(self.d)
        m.update(kv)
        kb.save_meta(self.d, m)

    def running(self, **kv):
        self.meta(状态='开发中', 开发流程='generic', SDD目录=str(self.docs),
                  开发会话={'状态': '运行中', 'pid': os.getpid(), '日志': str(self.log), '开始时间': '2026-11-18 10:00', **kv})

    def test_open_agents(self):
        self.log.write_text(agent_call(1) + agent_call(2) + tool_result(1))
        self.assertEqual(live.open_agents(self.log), 1)
        with self.log.open('a') as fh:            # 增量：只读新增部分
            fh.write(tool_result(2))
        self.assertEqual(live.open_agents(self.log), 0)

    def test_running_with_tasks(self):
        (self.docs / 'tasks.md').write_text('- [x] 一\n- [x] 二\n- [ ] 三 冷却态复测\n- [ ] 四\n')
        self.log.write_text(agent_call(9))
        self.running()
        v = kb.period_live(self.name)
        self.assertEqual(v['状态'], '运行')
        self.assertEqual(len(v['会话']), 1)
        self.assertEqual(v['AI数'], 2)                       # 会话 1 个 + 没返回的子 agent 1 个
        self.assertGreater(v['最后动静'], 0)
        pg = v['进度']
        self.assertEqual((pg['完成'], pg['总数'], pg['约']), (2, 4, False))
        self.assertIn('冷却态复测', pg['当前'])
        (self.docs / 'tasks.md').write_text('- [x] 一\n- [x] 二\n- [ ] 三\n- [ ] 四\n- [ ] 五\n')
        self.assertTrue(kb.period_live(self.name)['进度']['约'])     # 总数中途变了

    def test_planning_no_tasks(self):
        self.running()
        pg = kb.period_live(self.name)['进度']
        self.assertTrue(pg is None or (pg['总数'] == 0 and pg['阶段'] == '规划中'))
        (self.docs / 'tasks.md').write_text('写方案中\n')
        pg = kb.period_live(self.name)['进度']
        self.assertEqual((pg['总数'], pg['阶段']), (0, '规划中'))

    @unittest.skip('公开版没有内置公司流程')
    def test_sdd_planning_stage(self):
        self.running()
        self.meta(开发流程='company-flow')
        (self.docs / 'sdd-status.md').write_text('step1_pm: done\nstep1_api: skipped\nstep2: in_progress\n')
        pg = kb.period_live(self.name)['进度']
        self.assertEqual((pg['总数'], pg['阶段'], pg['阶段序号'], pg['阶段总数']), (0, '技术规划', 3, 4))
        (self.docs / 'sdd-status.md').write_text('step1_pm: done\nstep1_api: done\nstep2: done\nstep3: in_progress\n'
                                                 'step3_current_index: 5\nstep3_task_total: 8\nstep3_current_task: TASK-5 冷却态复测\n')
        pg = kb.period_live(self.name)['进度']
        self.assertEqual((pg['完成'], pg['总数'], pg['当前']), (4, 8, 'TASK-5 冷却态复测'))

    def test_pending_question_first(self):
        self.running()
        self.meta(待确认=[{'编号': 'Q001', '状态': '待回答', '问题': '方案可以吗'}])
        v = kb.period_live(self.name)
        self.assertEqual(v['状态'], '等你回答')
        self.assertEqual(v['问题']['编号'], 'Q001')

    def test_paused_by_limit(self):
        self.meta(状态='开发中', 开发会话={'状态': '中断待续', '日志': str(self.log)})
        supervise.record(self.name, 'dev', {'原因': '用量限制', '详情': 'limit', '恢复时间': '2026-11-18 15:12'}, 1, log=str(self.log))
        v = kb.period_live(self.name)
        self.assertEqual(v['状态'], '暂停')
        self.assertEqual(v['恢复时间'][11:16], '15:12')

    def test_stopped_and_brief(self):
        self.meta(状态='开发中', 开发会话={'状态': '已结束', '日志': str(self.log)})
        self.assertEqual(kb.period_live(self.name)['状态'], '已停')
        self.assertEqual(kb.live_brief(self.name)['状态'], '已停')
        self.meta(状态='收集中', 开发会话=None)
        self.assertIsNone(kb.live_brief(self.name))


if __name__ == '__main__':
    unittest.main()


# ---- SDD：子进度、子 agent 次数、预计剩余时间（kblib/ai/eta.py）
from datetime import datetime, timedelta, timezone  # noqa: E402
from kblib.ai import eta  # noqa: E402

T0 = datetime(2026, 11, 18, 1, 0, tzinfo=timezone.utc)


def _iso(m):
    return (T0 + timedelta(minutes=m)).isoformat().replace('+00:00', 'Z')


def call(cid, kind, desc, start, end=None):
    """一次子 agent 调用：tool_use 行 + （可选）tool_result 行；中间插一条带 tool_use_id 的 system 事件（不算返回）。"""
    s = _line({'type': 'assistant', 'timestamp': _iso(start), 'message': {'content': [
        {'type': 'tool_use', 'id': cid, 'name': 'Agent', 'input': {'subagent_type': kind, 'description': desc}}]}})
    s += _line({'type': 'system', 'subtype': 'task_started', 'tool_use_id': cid})
    if end is not None:
        s += _line({'type': 'user', 'timestamp': _iso(end), 'message': {'content': [
            {'type': 'tool_result', 'tool_use_id': cid, 'content': '{"timestamp":"2000-01-01T00:00:00Z"}'}]}})
    return s


def task(n, start, dev=10, rev=5, dvc=15):
    """一个完整 TASK：开发 → 审查 → 真机 → 提交，返回 (日志, 结束分钟)。"""
    t, s = start, ''
    for kind, label, d in (('sdd-dev-agent', '开发', dev), ('sdd-reviewer-agent', '审查', rev),
                           ('sdd-agentdevice-agent', '真机验证', dvc), ('sdd-task-commit-agent', '提交', 1)):
        s += call(f'toolu_{n}_{label}', kind, f'SDD TASK-{n} {label}', t, t + d)
        t += d
    return s, t


class EtaTest(unittest.TestCase):
    def setUp(self):
        kb.init()
        self.name = f'SDD{self._testMethodName}'
        kb.create_period(self.name)
        self.d = kb.period_dir(self.name)
        (self.d / eta.DEV_DIR).mkdir(exist_ok=True)
        self.log = self.d / eta.DEV_DIR / 'Android开发-20261118-090000.jsonl'
        self.docs = TMP / f'sdd-{self._testMethodName}'
        self.docs.mkdir()

    def status(self, idx, total=5, phase='device', step3='in_progress', **kv):
        lines = ['step1_pm: done', 'step1_api: done', 'step2: done', f'step3: {step3}', f'step3_current_index: {idx}',
                 f'step3_task_total: {total}', f'step3_current_task: TASK-{idx}', f'step3_task_phase: {phase}']
        lines += [f'{k}: {v}' for k, v in kv.items()]
        (self.docs / 'sdd-status.md').write_text('\n'.join(lines) + '\n')

    def test_durations_and_eta(self):
        a, t = task(1, 0, dev=10, rev=5, dvc=15)            # 31 分钟
        b, t = task(2, t, dev=20, rev=5, dvc=25)            # 51 分钟
        cur = call('toolu_3_dev', 'sdd-dev-agent', 'SDD TASK-3 开发', t, t + 12) + call('toolu_3_rev', 'sdd-reviewer-agent', 'SDD TASK-3 审查', t + 12)
        self.log.write_text(a + b + cur)
        self.status(3, total=5, phase='review')
        now = (T0 + timedelta(minutes=t + 14)).timestamp()
        r = eta.sdd_eta(self.d, self.docs, [str(self.log)], now=now)
        rows = {x['任务']: x for x in r['任务表']}
        self.assertEqual((rows['TASK-1']['耗时'], rows['TASK-2']['耗时'], rows['TASK-1']['状态']), (31, 51, '已完成'))
        self.assertEqual(rows['TASK-3']['状态'], '进行中')
        self.assertEqual(rows['TASK-5']['状态'], '未开始')
        self.assertEqual(r['子agent']['已派'], 10)
        self.assertEqual([x['类型'] for x in r['子agent']['在跑']], ['sdd-reviewer-agent'])
        self.assertEqual(r['子进度']['当前'], '审查')
        self.assertEqual([s['状态'] for s in r['子进度']['步骤']], ['done', 'cur', 'todo', 'todo'])
        e = r['预估']
        self.assertFalse(e['约'])                           # 已完成 2 个：用实际耗时 31–51
        self.assertEqual(e['剩余TASK'], 2)
        # 当前剩余 ≈ 审查(中位 5 − 已跑 2) + 真机(15/25 中位) + 提交 1；+ 2×(31..51) + 1
        self.assertTrue(60 <= e['低'] <= 100 and 100 <= e['高'] <= 160, e)
        self.assertEqual(e['低'] % 5, 0)
        self.assertIn('31–51', e['依据'])

    def test_overrun_and_blocked(self):
        a, t = task(1, 0)
        self.log.write_text(a + call('toolu_2_d', 'sdd-agentdevice-agent', 'SDD TASK-2 真机验证', t))
        self.status(2, total=3, phase='device')
        now = (T0 + timedelta(minutes=t + 200)).timestamp()            # 真机跑了 200 分钟：比历来最长的还久
        e = eta.sdd_eta(self.d, self.docs, [str(self.log)], now=now)['预估']
        self.assertTrue(e['约'])                                       # 只完成 1 个
        self.assertGreater(e['超出'], 0)
        self.status(2, total=3, phase='device', step3_task_blocked_reason='缺第二台手机')
        r = eta.sdd_eta(self.d, self.docs, [str(self.log)], now=now)
        self.assertIsNone(r['预估'])
        self.assertIn('第二台手机', r['阻塞'])

    def test_planning_no_eta(self):
        self.log.write_text(call('toolu_p', 'sdd-arch-planner-agent', 'SDD arch planner', 0))
        (self.docs / 'sdd-status.md').write_text('step1_pm: done\nstep2: pending\nstep3: pending\n')
        r = eta.sdd_eta(self.d, self.docs, [str(self.log)], now=(T0 + timedelta(minutes=20)).timestamp())
        self.assertIsNone(r['预估'])
        self.assertEqual(r['总体']['规划'], '进行中')
        self.assertEqual(r['总体']['规划步骤']['已跑'], 20)

    def test_not_sdd(self):
        self.assertIsNone(eta.sdd_eta(self.d, '', []))
