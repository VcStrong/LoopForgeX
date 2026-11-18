"""行为账本（管家学你的操作习惯）的单测。

    cd _system && .venv/bin/python -m unittest tests.test_behavior -v     （没有 .venv 用 python3）

临时 KB_ROOT，不碰真实数据，不起 AI 会话。
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix='kb-beh-test-'))
os.environ['KB_ROOT'] = str(TMP / 'kb')
os.environ['HOME'] = str(TMP / 'home')
(TMP / 'kb').mkdir()
(TMP / 'home').mkdir()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import kb  # noqa: E402
from kblib.steward import behavior as beh, patrol  # noqa: E402


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


class BehaviorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 账本还不存在时，从「待确认」回填：只算你本人的回答
        qs = [{'编号': f'Q{i:03d}', '时间': '2026-11-10 10:00', '阶段': '改法确认', '问题': '这样改可以吗', 'AI建议': '按方案 A 改',
               '选项': ['按方案 A 改', '不改'], '状态': '已回答', '回答': '按方案 A 改' if i < 8 else '先别改', '回答人': '用户',
               '回答时间': '2026-11-10 10:05'} for i in range(9)]
        qs.append({'编号': 'Q100', '时间': '2026-11-10 10:00', '阶段': '确认点', '问题': '?', 'AI建议': 'x', '状态': 'AI决策',
                   '回答': 'x', '回答人': 'AI（免打扰）', '回答时间': '2026-11-10 10:00'})
        period('回填期', 待确认=qs)

    def test_kind_and_adopted(self):
        self.assertEqual(beh.ask_kind({'阶段': 'E001 改法确认'}), '改法')
        self.assertEqual(beh.ask_kind({'阶段': '', '问题': '根因定位对吗'}), '定位')
        self.assertEqual(beh.adopted({'AI建议': '方案 A：只改 X', '回答': '方案 A：只改 X'}), '照建议')
        self.assertEqual(beh.adopted({'AI建议': '方案 A', '回答': '同意'}), '照建议')
        self.assertEqual(beh.adopted({'AI建议': '方案 A，只改 X', '回答': '不行，换 B'}), '改了')
        self.assertEqual(beh.adopted({'AI建议': '', '回答': '随便'}), '无建议')

    def test_backfill_and_rate(self):
        r = beh.ask_rate('改法')
        self.assertGreaterEqual(r['次数'], 9)                 # 别的用例可能又答了一次（照建议）
        self.assertEqual(r['次数'] - r['照建议'], 1)          # 只有「先别改」那一次没照
        self.assertEqual(beh.ask_rate('确认点')['次数'], 0)          # 免打扰时 AI 自己定的不算

    def test_ask_shows_rate_and_answer_is_recorded(self):
        period('提问期', 免打扰=False)
        kb.ask('提问期', '这样改行吗', recommend='改 A', stage='改法确认')
        q = kb.load_meta(kb.period_dir('提问期'))['待确认'][-1]
        self.assertEqual(q['照建议率']['类别'], '改法')
        self.assertGreaterEqual(q['照建议率']['次数'], 9)
        n = len(beh.events(event='ask_answer'))
        kb.answer('提问期', q['编号'], '改 A')
        last = beh.events(event='ask_answer')[-1]
        self.assertEqual(len(beh.events(event='ask_answer')), n + 1)
        self.assertEqual((last['对象'], last['采纳']), ('提问期', '照建议'))

    def test_ai_actions_not_learned(self):
        n = len(beh.events())
        beh.emit('bug_analyze', '1', by='管家（自动）')
        beh.emit('archive', 'x', by='AI')
        self.assertEqual(len(beh.events()), n)

    def test_archive_card_and_apply(self):
        for i in range(3):
            period(f'归档{i}', 状态='已完成')
            kb.period_archive(f'归档{i}')
        c = next(c for c in patrol.steward_view()['卡片'] if c['id'].startswith('learn:archive:'))
        self.assertEqual(c['动作'], 'learn')
        msg = patrol.steward_answer(c['id'], '')
        self.assertIn('7 天', msg)
        self.assertEqual(kb.archive_days(), 7)
        self.assertFalse(any(c['id'].startswith('learn:archive:') for c in patrol.steward_view()['卡片']))
        for i in range(2):                    # 自动归档后又恢复了 2 次：提议改回去
            kb.period_archive(f'归档{i}', on=False)
        self.assertTrue(any(c['id'].startswith('learn:archive-back') for c in patrol.steward_view()['卡片']))

    def test_push_again_card(self):
        period('推动事项', 类型='事项', 状态='开发中', 条目状态={'说明': {'状态': '推动中', '推动时间': kb.now()}},
               开发会话={'状态': '已结束', '结束时间': '2099-01-01 00:00'})
        c = [c for c in patrol.steward_view()['卡片'] if c['id'].startswith('learn:push:推动事项:')]
        self.assertEqual(len(c), 1)
        self.assertEqual(c[0]['动作'], 'push-item')
        period('推动事项', 开发会话={'状态': '运行中', 'pid': os.getpid()})          # 会话在跑：不提
        self.assertFalse(any(c['id'].startswith('learn:push:推动事项:') for c in patrol.steward_view()['卡片']))

    def test_upgrade_counts_manual_actions_elsewhere(self):
        patrol.steward_set(level='保守')
        for i in range(3):
            beh.emit('bug_analyze', str(i), by='用户（网页）')
        self.assertTrue(any(c['id'] == 'upgrade:Bug分析' for c in patrol.steward_view()['卡片']))

    def test_learning_switch(self):
        patrol.steward_set(learn=False)
        try:
            self.assertEqual(beh.learned_cards(), [])
        finally:
            patrol.steward_set(learn=True)


if __name__ == '__main__':
    unittest.main()
