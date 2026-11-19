"""明确的事 AI 自动定（不依赖免打扰）的单测：kb_ask 自判断、全栈方案自动拍板 / 撤回、改回重做。

    cd _system && .venv/bin/python -m unittest tests.test_autodecide -v     （没有 .venv 用 python3）

临时 KB_ROOT，不碰真实数据，不起 AI 会话（开工、发消息都换成假的）。
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TMP = Path(tempfile.mkdtemp(prefix='kb-auto-test-'))
os.environ['KB_ROOT'] = str(TMP / 'kb')
os.environ['HOME'] = str(TMP / 'home')
(TMP / 'kb').mkdir()
(TMP / 'home').mkdir()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import kb  # noqa: E402
from kblib.ai import devsession, talk  # noqa: E402
from kblib.stack import dev as sdev, plan as splan  # noqa: E402
from kblib.steward import patrol  # noqa: E402


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


def q_of(name, qid):
    return next(x for x in kb.load_meta(kb.period_dir(name))['待确认'] if x['编号'] == qid)


class NeedUserTest(unittest.TestCase):
    def test_rules(self):
        nu = devsession.need_user
        self.assertEqual(nu('改 A 还是 B', '改 A', sure=True), '')
        self.assertEqual(nu('改 A 还是 B', '改 A', sure='true'), '')
        self.assertEqual(nu('改 A 还是 B', '改 A'), '拿不准')                      # 没说有把握：按拿不准，旧会话照旧问
        self.assertEqual(nu('改 A 还是 B', '', sure=True), '没给建议')
        self.assertEqual(nu('改 A 还是 B', '改 A', sure=True, expands=True), '要扩大范围')
        self.assertEqual(nu('改 A 还是 B', '改 A', sure=True, irreversible='true'), '影响线上或不可回退')
        self.assertTrue(nu('要不要 svn 提交', '提交', sure=True))                   # 对外不可撤回的：一律问
        self.assertTrue(nu('放量比例', '10%', sure=True, stage='上线放量'))
        self.assertEqual(nu('改 A 还是 B', '改 A', sure=True, expands='false'), '')
        # 否定说法不算（Q001 实例：「提交不推送」曾被误判成对外操作）；权限 / 账号是话题词，不算
        self.assertEqual(nu('按默认方案实现：install.sh 放行 mcp__kb', '按上述改法实现，提交不推送', sure=True), '')
        self.assertEqual(nu('改 Claude 权限设置', '按说明改，不影响线上', sure=True), '')
        self.assertEqual(nu('改法', '不用 push，本地提交', sure=True), '')
        self.assertTrue(nu('要不要 svn 提交', '提交', sure=True))               # 「要不要」是问句，不是否定
        self.assertTrue(nu('要不要推送到远端', '推送', sure=True))
        self.assertTrue(nu('是否上线', '先上线 10%', sure=True))


class AskTest(unittest.TestCase):
    def test_auto_without_dnd(self):
        period('自判期')
        r = kb.ask('自判期', '空列表显示什么', recommend='显示「暂无数据」', sure=True, reason='沿用现有空态文案')
        self.assertIn('不用等用户', r)
        q = q_of('自判期', 'Q001')
        self.assertEqual(q['状态'], 'AI决策')
        self.assertEqual(q['回答人'], 'AI（自动判断）')
        self.assertEqual(q['自动理由'], '沿用现有空态文案')

    def test_unsure_waits(self):
        period('拿不准期')
        r = kb.ask('拿不准期', '要不要顺便重构登录页', recommend='不重构', sure=True, expands=True)
        self.assertIn('已发到网页', r)
        q = q_of('拿不准期', 'Q001')
        self.assertEqual(q['状态'], '待回答')
        self.assertEqual(q['要问理由'], '要扩大范围')

    def test_setting_off_waits(self):
        period('关开关期')
        patrol.steward_set(auto=False)
        try:
            self.assertFalse(patrol.steward_settings()['自动拍板'])
            r = kb.ask('关开关期', '空列表显示什么', recommend='暂无数据', sure=True)
            self.assertIn('已发到网页', r)
        finally:
            patrol.steward_set(auto=True)
        self.assertTrue(patrol.steward_settings()['自动拍板'])

    def test_dnd_still_decides_unsure(self):
        period('免打扰期', 免打扰=True)
        r = kb.ask('免打扰期', '要不要顺便重构', recommend='不重构')
        self.assertIn('免打扰已开启', r)
        self.assertEqual(q_of('免打扰期', 'Q001')['回答人'], 'AI（免打扰）')

    def test_override_redo(self):
        period('改回期')
        kb.ask('改回期', '空列表显示什么', recommend='暂无数据', sure=True)
        with mock.patch.object(talk, 'period_sessions', return_value=[]), \
                mock.patch.object(talk, 'period_say', return_value='已发给 AI') as say:
            r = kb.answer('改回期', 'Q001', '显示「去添加」按钮')
        self.assertIn('已改回', r)
        self.assertIn('改回重做', say.call_args[0][1])
        q = q_of('改回期', 'Q001')
        self.assertEqual((q['状态'], q['AI原决定']), ('已回答', '暂无数据'))

    def test_plain_answer_no_redo(self):
        period('普通回答期')
        kb.ask('普通回答期', '要不要扩大', recommend='不扩大')
        with mock.patch.object(talk, 'period_say') as say:
            self.assertEqual(kb.answer('普通回答期', 'Q001', '不扩大'), '已回答 Q001')
        say.assert_not_called()


def _plan(qs=None, expand=False):
    return {'摘要': '加个列表', '需求': [{'编号': 'REQ-001', '标题': '列表', '验收': ['当…时，应…'], '端': ['Android']}],
            '各端': [{'端': 'Android', '涉及': True, '改动': ['列表页']}], '接口': [], '待确认': qs or [],
            '假设': ['分页 20 条，沿用现有列表'], '扩大范围': expand}


class StackAutoTest(unittest.TestCase):
    def setUp(self):
        self.repo = TMP / 'repo'
        self.repo.mkdir(exist_ok=True)

    def _period(self, name, **kw):
        return period(name, 各端={'Android': {'仓库': str(self.repo), '分支': 'main'}},
                      全栈={'状态': '出方案中'}, 全栈会话={'状态': '运行中', '发起': '用户（网页）'}, **kw)

    def test_clear_plan_auto_approves_and_starts(self):
        d = self._period('自动拍板期')
        with mock.patch.object(sdev, 'stack_dev_start', return_value='已开工：Android') as start:
            r = kb.stack_plan_submit('自动拍板期', json.dumps(_plan()))
        self.assertIn('AI 已自动拍板', r)
        start.assert_called_once()
        st = kb.load_meta(d)['全栈']
        self.assertEqual(st['状态'], '已拍板')
        self.assertEqual(st['自动拍板']['方案版本'], 'v1')
        self.assertTrue(st['自动拍板']['开工'])
        self.assertEqual(st['自动拍板']['假设'], ['分页 20 条，沿用现有列表'])
        # 撤回：回到待拍板，自动锁的方案版本删掉
        msg = kb.stack_auto_revert('自动拍板期')
        self.assertIn('待拍板', msg)
        st = kb.load_meta(d)['全栈']
        self.assertEqual(st['状态'], '待拍板')
        self.assertNotIn('自动拍板', st)
        self.assertFalse((splan._sdir(d) / '方案.v1.json').exists())
        # 你再手动拍板：照常 v1
        self.assertIn('已拍板', kb.stack_approve('自动拍板期'))
        self.assertEqual(kb.load_meta(d)['全栈']['方案版本'], 'v1')

    def test_open_question_waits(self):
        d = self._period('有问题期')
        with mock.patch.object(sdev, 'stack_dev_start') as start:
            r = kb.stack_plan_submit('有问题期', json.dumps(_plan([{'问题': '要不要做 iOS', '建议': '先不做'}])))
        self.assertIn('等用户在网页上拍板', r)
        start.assert_not_called()
        st = kb.load_meta(d)['全栈']
        self.assertEqual((st['状态'], st['等拍板理由']), ('待拍板', '有 1 条待确认'))

    def test_expand_waits(self):
        self._period('扩大期')
        r = kb.stack_plan_submit('扩大期', json.dumps(_plan(expand=True)))
        self.assertIn('等用户在网页上拍板', r)

    def test_steward_plan_approves_but_no_start(self):
        d = period('管家出方案期', 各端={'Android': {'仓库': str(self.repo), '分支': 'main'}},
                   全栈={'状态': '出方案中'}, 全栈会话={'状态': '运行中', '发起': '管家', '自动发起': True})
        with mock.patch.object(sdev, 'stack_dev_start') as start:
            r = kb.stack_plan_submit('管家出方案期', json.dumps(_plan()))
        self.assertIn('开工等你点', r)
        start.assert_not_called()
        self.assertEqual(kb.load_meta(d)['全栈']['状态'], '已拍板')

    def test_dnd_approves_with_questions(self):
        d = self._period('免打扰方案期', 免打扰=True)
        with mock.patch.object(sdev, 'stack_dev_start', return_value='已开工'):
            kb.stack_plan_submit('免打扰方案期', json.dumps(_plan([{'问题': '要不要做 iOS', '建议': '先不做'}])))
        plan = json.loads((splan._sdir(d) / '方案.json').read_text('utf-8'))
        self.assertEqual(plan['待确认'][0]['结论'], '先不做')


if __name__ == '__main__':
    unittest.main()
