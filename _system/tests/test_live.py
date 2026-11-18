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

    def test_sdd_planning_stage(self):
        self.running()
        self.meta(开发流程='sdd-android')
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
