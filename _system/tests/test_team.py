"""角色团（期「角色团接入知识库」REQ-002 ~ REQ-017、REQ-023）的单测和接口测试。

    cd _system && .venv/bin/python -m unittest tests.test_team -v     （没有 .venv 用 python3）

用临时 KB_ROOT 和临时 HOME（假的 ~/.claude/agents、manifest），不碰真实数据，不起 AI 会话。
REQ-023 的「兜底表与已安装场景表一致」读真实 HOME 下的 ~/.claude/role-agents/config/kb_scenes.json，没装就跳过。
"""
import inspect
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REAL_HOME = Path.home()
TMP = Path(tempfile.mkdtemp(prefix='kb-team-test-'))
os.environ['KB_ROOT'] = str(TMP / 'kb')
os.environ['HOME'] = str(TMP / 'home')
(TMP / 'kb').mkdir()
(TMP / 'home').mkdir()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import kb  # noqa: E402
from kblib.ai import engines, team  # noqa: E402
from webapp import router  # noqa: E402
import webapp.server  # noqa: E402,F401  — 登记全部接口
from webapp.shared import api_period  # noqa: E402

AGENTS = TMP / 'home' / '.claude' / 'agents'
SUITE = TMP / 'home' / '.claude' / 'role-agents'
team.AGENTS_DIR, team.SUITE, team.MANIFEST = AGENTS, SUITE, SUITE / 'manifest.json'
team.SCENES_FILE = SUITE / 'config' / 'kb_scenes.json'
CRON = {'text': ''}                                          # 假 crontab：测试不碰真实 launchd / crontab
LAUNCHCTL = []
team._crontab_read = lambda: CRON['text']
team._crontab_write = lambda t: CRON.__setitem__('text', t)
team._launchctl = lambda *a: LAUNCHCTL.append(a)
P = '角色团测试期'
CLAUDE = dict(engines.BUILTIN_ENGINES['claude'], id='claude', 名称='Claude Code', 命令='/bin/echo')
CODEX = dict(engines.BUILTIN_ENGINES['codex'], id='codex', 名称='Codex CLI', 命令='/bin/echo')
CUSTOM = dict(engines.BUILTIN_ENGINES['claude'], id='engine-x', 名称='自定义', 命令='/bin/echo')


def install(agents=team.ALL_AGENTS, manifest=True):
    shutil.rmtree(TMP / 'home' / '.claude', ignore_errors=True)
    AGENTS.mkdir(parents=True)
    for a in agents:
        (AGENTS / f'{a}.md').write_text(f'---\nname: {a}\n---\n', 'utf-8')
    if manifest:
        (SUITE / 'config').mkdir(parents=True)
        (SUITE / 'manifest.json').write_text(json.dumps({'suite_version': '1.0.0'}), 'utf-8')


def set_settings(**kv):
    s = kb.load_settings()
    s.pop('角色团', None)
    if kv:
        s['角色团'] = kv
    kb.save_settings(s)


def old_engine_cmd(e, prompt, title='kb', resume_id=''):
    """改动前的 engine_cmd（逐字节对照）。"""
    prompt = prompt + engines.rules_text()
    args = e.get('续跑参数') if resume_id and e.get('续跑参数') else e.get('启动参数')
    v = {'提示词': prompt, '标题': title, '会话ID': resume_id}
    return [engines.engine_bin(e)] + [re.sub(r'\{(提示词|标题|会话ID)\}', lambda mm: v[mm.group(1)], a) for a in args or []]


class FakeReq:
    """路由处理器用的假请求：记下状态码和返回体（_guard 同 webapp/server.py）。"""
    def __init__(self):
        self.code, self.body = None, None

    def _send(self, code, body, *a):
        self.code, self.body = code, body

    def _guard(self, fn):
        try:
            self._send(200, fn())
        except kb.KBNeed as e:
            self._send(409, {'error': str(e), 'need': e.data})
        except kb.KBError as e:
            self._send(400, {'error': str(e)})
        except Exception as e:      # noqa: BLE001
            self._send(500, {'error': repr(e)})


def call(method, path, body=None, q=None):
    r = router.find(method, path)
    assert r, f'{method} {path} 没登记'
    h = FakeReq()
    r.fn(h, None, q or {}) if method == 'GET' else r.fn(h, None, 0, body or {})
    return h, r


def assistant(name, sub, parent=None):
    return json.dumps({'type': 'assistant', 'parent_tool_use_id': parent, 'message': {'content': [
        {'type': 'tool_use', 'name': name, 'input': {'subagent_type': sub, 'description': 'x', 'prompt': 'y'}}]}}, ensure_ascii=False)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        kb.init()
        try:
            kb.period_dir(P)
        except kb.KBError:
            kb.create_period(P)

    def setUp(self):
        install()
        set_settings()
        d = kb.period_dir(P)
        m = kb.load_meta(d)
        for k in ('角色团', '角色调用'):
            m.pop(k, None)
        kb.save_meta(d, m)
        team._WARNED.clear()

    def meta(self):
        return kb.load_meta(kb.period_dir(P))


class TestInject(Base):
    """REQ-004：单点注入，关闭 / 不支持 / 续跑 / 无 scene 时命令行逐字节不变。"""
    def test_no_scene_identical(self):
        self.assertEqual(engines.engine_cmd(CLAUDE, '自检', 'kb·自检'), old_engine_cmd(CLAUDE, '自检', 'kb·自检'))

    def test_global_off_identical(self):
        set_settings(开启=False, 档位='精简')
        for sc in ('管家', '方案', '需求', 'Bug分析·Android', '开发', '开发·iOS', '审查·Web', '跟进', '发布', '流程设计', 'Bug修复·Android'):
            self.assertEqual(engines.engine_cmd(CLAUDE, '任务', 't', '', sc, self.meta()), old_engine_cmd(CLAUDE, '任务', 't'), sc)

    def test_period_off(self):
        self.assertIn('【角色团】', engines.engine_cmd(CLAUDE, '任务', 't', '', '方案', self.meta())[2])
        self.assertEqual(kb.set_period_team(P, '关'), f'「{P}」的角色团改为「关」')
        self.assertEqual(engines.engine_cmd(CLAUDE, '任务', 't', '', '方案', self.meta()), old_engine_cmd(CLAUDE, '任务', 't'))
        self.assertEqual(engines.engine_cmd(CLAUDE, '任务', 't', '', '方案', P), old_engine_cmd(CLAUDE, '任务', 't'))

    def test_other_engines(self):
        for e in (CODEX, CUSTOM):
            self.assertNotIn('【角色团】', ' '.join(engines.engine_cmd(e, '任务', 't', '', '方案', self.meta())))
        hermes = dict(engines.BUILTIN_ENGINES['hermes'], id='hermes', 名称='Hermes', 命令='/bin/echo')
        self.assertNotIn('【角色团】', ' '.join(engines.engine_cmd(hermes, '任务', 't', '', '方案', self.meta())))

    def test_custom_claude_command(self):
        d = Path(tempfile.mkdtemp(dir=TMP))
        c = d / 'claude'
        c.write_text('#!/bin/sh\n')
        c.chmod(0o755)
        e = dict(CUSTOM, 命令=str(c))
        self.assertIn('【角色团】', ' '.join(engines.engine_cmd(e, '任务', 't', '', '方案', self.meta())))

    def test_resume_not_repeated(self):
        self.assertEqual(engines.engine_cmd(CLAUDE, '续跑', 't', 'sid-1', '开发', self.meta()), old_engine_cmd(CLAUDE, '续跑', 't', 'sid-1'))

    def test_suite_missing(self):
        install(manifest=False)
        self.assertEqual(engines.engine_cmd(CLAUDE, '任务', 't', '', '方案', self.meta()), old_engine_cmd(CLAUDE, '任务', 't'))

    def test_remote_popen_has_no_scene(self):
        from kblib.stack import dev
        src = inspect.getsource(dev._remote_popen)
        self.assertIn('engine_cmd(eng, prompt, title, resume)', src)
        self.assertNotIn('scene', src)


class TestScenes(Base):
    """REQ-005 / REQ-006 / REQ-008 / REQ-015：场景表、档位、@端、安装过滤、提示词内容。"""
    def test_plan_text_lite_and_standard(self):
        t = team.team_text('方案', self.meta(), eng=CLAUDE)
        self.assertTrue(t.lstrip().startswith('【角色团】流程命令照旧，专家只出意见'))
        for r in ('architect', 'product-manager', 'backend-engineer'):
            self.assertIn(f'- {r}（', t)
        self.assertNotIn('ux-designer', t)
        self.assertIn('本会话最多派 4 次', t)
        self.assertIn('不要 run_in_background', t)
        self.assertIn('建议来自：架构师', t)
        self.assertNotIn('本期还能派', t)
        self.assertNotIn('上限', t)
        set_settings(开启=True, 档位='标准')
        self.assertIn('- ux-designer（', team.team_text('方案', self.meta(), eng=CLAUDE))

    def test_every_scene_has_required_phrases(self):
        for sc in team._BUILTIN['场景']:
            name = sc.replace('·端', '·Android')
            t = team.team_text(name, self.meta(), eng=CLAUDE)
            self.assertIn('流程命令照旧', t, sc)
            self.assertIn('不要 run_in_background', t, sc)

    def test_no_period_cap(self):
        """只协助、不设卡：本期派了再多次也照常挂角色团，不提「用完」。"""
        d = kb.period_dir(P)
        m = self.meta()
        m['角色调用'] = {'合计': 999, '按角色': {'architect': 999}, '超限已记': True}
        kb.save_meta(d, m)
        t = team.team_text('方案', self.meta(), eng=CLAUDE)
        self.assertIn('- architect（', t)
        self.assertNotIn('用完', t)
        self.assertIn('本会话最多派 4 次', t)

    def test_release_lite_architect(self):
        self.assertEqual([r for r, *_ in team.scene_roles('发布', tier='精简')[1]], ['architect'])
        self.assertEqual([r for r, *_ in team.scene_roles('发布', tier='标准')[1]], ['sre-security'])

    def test_scene_with_separate_end(self):
        t = team.team_text('Bug分析', self.meta(), 'iOS', eng=CLAUDE)
        self.assertIn('- client-engineer（客户端工程师，主导）', t)
        self.assertIn('- client-engineer（', ' '.join(engines.engine_cmd(CLAUDE, 'x', 't', '', 'Bug分析', self.meta(), end='Android')))
        t = team.team_text('审查·端', self.meta(), 'Web', eng=CLAUDE)            # 审查·端 名单里没有 @端
        self.assertIn('- architect（架构师，主导）', t)
        self.assertIn('本会话最多派 2 次', t)
        self.assertIn('- client-engineer（', team.team_text('Bug修复', None, 'Android', eng=CLAUDE))

    def test_end_mapping(self):
        f = lambda end: team.scene_roles(f'Bug分析·{end}')[1][0][0]
        for end, want in (('Android', 'client-engineer'), ('iOS', 'client-engineer'), ('鸿蒙', 'client-engineer'), ('小程序', 'client-engineer'),
                          ('Web', 'frontend-engineer'), ('H5', 'frontend-engineer'), ('服务端', 'backend-engineer'), ('kb', 'backend-engineer'),
                          ('角色套件', 'backend-engineer')):
            self.assertEqual(f(end), want, end)
        self.assertEqual([r for r, *_ in team.scene_roles('Bug分析·Android')[1]], ['client-engineer', 'qa-expert'])

    def test_uninstalled_removed_and_lead_replaced(self):
        install([a for a in team.ALL_AGENTS if a != 'architect'])
        roles = team.scene_roles('方案')[1]
        self.assertEqual([r for r, *_ in roles], ['product-manager', 'backend-engineer'])
        self.assertTrue(roles[0][2], '主导没装时参与里第一个顶上')
        install([a for a in team.ALL_AGENTS if a != 'sre-security' and a != 'architect'])
        self.assertEqual(team.team_text('发布', self.meta(), eng=CLAUDE), '')

    def test_unknown_scene(self):
        self.assertEqual(team.team_text('不存在的场景', self.meta(), eng=CLAUDE), '')

    def test_auto_model(self):
        self.assertIn('model 参数填 "sonnet"', team.team_text('方案', self.meta(), eng=CLAUDE, auto=True))
        self.assertNotIn('model 参数', team.team_text('方案', self.meta(), eng=CLAUDE))

    def test_p0_not_inherited_branch(self):
        old = team.P0_INHERITS_DISALLOWED
        team.P0_INHERITS_DISALLOWED = False
        try:
            self.assertEqual([r for r, *_ in team.scene_roles('Bug分析·Android')[1]], ['architect', 'qa-expert'])
            for sc, cfg in team._BUILTIN['场景'].items():
                if cfg['只读']:
                    for tier in team.TIERS:
                        rs = [r for r, *_ in team.scene_roles(sc.replace('·端', '·Android'), tier=tier)[1]]
                        self.assertFalse([r for r in rs if r.endswith('-engineer')], (sc, tier, rs))
            self.assertIn('client-engineer', [r for r, *_ in team.scene_roles('Bug修复·Android')[1]])
        finally:
            team.P0_INHERITS_DISALLOWED = old

    def test_p0_no_model_param_branch(self):
        old = team.P0_MODEL_PARAM
        team.P0_MODEL_PARAM = False
        try:
            self.assertEqual(team.scene_roles('方案', auto=True)[1][0][0], 'backend-engineer')
            self.assertEqual(team.scene_roles('管家', auto=True)[1], [])
        finally:
            team.P0_MODEL_PARAM = old

    def test_suite_file_used_and_fallbacks(self):
        t = json.loads(json.dumps(team._BUILTIN))
        t['场景']['方案']['上限'] = 3
        t['场景']['方案']['精简'] = {'主导': 'architect', '参与': ['qa-expert']}
        del t['场景']['发布']
        team.SCENES_FILE.write_text(json.dumps(t, ensure_ascii=False), 'utf-8')
        self.assertEqual(team.load_scenes()[1], '套件')
        txt = team.team_text('方案', self.meta(), eng=CLAUDE)
        self.assertIn('本会话最多派 3 次', txt)
        self.assertIn('- qa-expert（', txt)
        self.assertEqual(team.scene_roles('发布')[1][0][0], 'architect')       # 缺的场景用兜底
        self.assertIn('方案.上限', team.scene_diff())
        self.assertTrue(team.team_status()['状态']['场景表差异'])
        for bad in ('{坏 json', json.dumps(dict(t, 版本=2))):
            team.SCENES_FILE.write_text(bad, 'utf-8')
            self.assertEqual(team.load_scenes()[1], '内置兜底')
            self.assertEqual(team.team_status()['状态']['场景表'], '内置兜底')
        team.SCENES_FILE.unlink()
        self.assertEqual(team.load_scenes()[1], '内置兜底')

    def test_push_rule(self):
        self.assertIn('需求扩展先请 product-manager 写变更说明', team.push_rule(self.meta(), CLAUDE))
        self.assertEqual(team.push_rule(self.meta(), CODEX), '')
        set_settings(开启=False)
        self.assertEqual(team.push_rule(self.meta(), CLAUDE), '')


class TestReviewRound1(Base):
    """审查第 1 轮打回的问题。"""
    def test_artifact_sentence_only_for_role_products(self):
        for sc in ('管家', '开发', 'Bug修复', '需求', '方案', 'Bug分析'):
            t = team.team_text(sc, self.meta(), 'Android', eng=CLAUDE)
            self.assertNotIn('角色产物/', t, sc)
            self.assertIn('本场景不用另写角色产物文件', t, sc)
        for sc, prod in (('审查·端', '测试报告'), ('跟进', '变更说明'), ('发布', '发布清单')):
            t = team.team_text(sc, self.meta(), 'Android', eng=CLAUDE)
            self.assertIn(f'filename=角色产物/<阶段序号>-{prod}.md', t, sc)

    def test_bad_types_fall_back(self):
        t = json.loads(json.dumps(team._BUILTIN))
        t['场景']['方案']['上限'] = '四'
        t['场景']['审查·端']['精简'] = {'主导': 'architect', '参与': 'qa-expert'}
        t['场景']['管家']['用途'] = ['x']
        t['场景']['怪场景'] = {'上限': 'x'}
        t['端角色'] = ['client-engineer']
        t['只读安全角色'] = 'architect'
        team.SCENES_FILE.write_text(json.dumps(t, ensure_ascii=False), 'utf-8')
        sc, src = team.load_scenes()
        self.assertEqual(src, '套件')
        for k in ('方案', '审查·端', '管家'):
            self.assertEqual(sc['场景'][k], team._BUILTIN['场景'][k], k)
        self.assertNotIn('怪场景', sc['场景'])
        self.assertEqual(sc['端角色'], team._BUILTIN['端角色'])
        self.assertIn('本会话最多派 4 次', team.team_text('方案', self.meta(), eng=CLAUDE))
        self.assertIn('- client-engineer（', team.team_text('Bug分析', self.meta(), 'iOS', eng=CLAUDE))

    def test_engine_cmd_survives_team_error(self):
        old = team.team_text
        team.team_text = lambda *a, **k: 1 / 0
        try:
            self.assertEqual(engines.engine_cmd(CLAUDE, '任务', 't', '', '方案', self.meta()), old_engine_cmd(CLAUDE, '任务', 't'))
        finally:
            team.team_text = old

    def test_unknown_scene_logged_to_period(self):
        team._WARNED_P.clear()
        team.team_text('没这个场景', self.meta(), eng=CLAUDE)
        team.team_text('没这个场景', self.meta(), eng=CLAUDE)
        logs = (kb.period_dir(P) / '记录.md').read_text('utf-8')
        self.assertEqual(logs.count('场景「没这个场景」不在场景表里'), 1)

    def test_bug_multi_period(self):
        from kblib.bugs import analysis
        try:
            kb.period_dir('角色团测试期二')
        except kb.KBError:
            kb.create_period('角色团测试期二')
        d1, d2 = kb.period_dir(P), kb.period_dir('角色团测试期二')
        m2 = kb.load_meta(d2)
        m2.pop('角色团', None)
        m2['角色调用'] = {'合计': 25}
        kb.save_meta(d2, m2)
        bugs = [{'归属': {'类型': '期', '名称': P}}, {'归属': {'类型': '期', '名称': '角色团测试期二'}}]
        self.assertIn('- client-engineer（', team.team_text('Bug分析', analysis._owner_meta(bugs), 'Android', eng=CLAUDE))
        kb.set_period_team('角色团测试期二', '关')
        self.assertEqual(team.team_text('Bug分析', analysis._owner_meta(bugs), 'Android', eng=CLAUDE), '')
        self.assertEqual(analysis._owner_meta([bugs[0]])['名称'], P)
        self.assertIsNone(analysis._owner_meta([{}]))
        kb.set_period_team('角色团测试期二', '跟随')

    def test_followup_resume_adds_followup_rules(self):
        from kblib.ai import talk
        d = kb.period_dir(P)
        m = self.meta()
        m['开发会话'] = {'状态': '已结束', '场景': '开发'}
        kb.save_meta(d, m)
        old = talk.period_engine
        talk.period_engine = lambda m: CLAUDE
        try:
            p = talk._push_prompt(P, '/tmp/x', 'main', '加个按钮', True)
            self.assertIn('按类型派角色', p)
            self.assertIn('本场景「跟进」', p)
            p = talk._push_prompt(P, '/tmp/x', 'main', '加个按钮', False)
            self.assertIn('按类型派角色', p)
            self.assertNotIn('本场景「跟进」', p, '新开会话的场景段落由 engine_cmd 附加')
            m = self.meta()
            m['开发会话']['场景'] = '跟进'
            kb.save_meta(d, m)
            self.assertNotIn('本场景「跟进」', talk._push_prompt(P, '/tmp/x', 'main', '再改', True), '上一轮就是跟进，不重复')
            set_settings(开启=False)
            p = talk._push_prompt(P, '/tmp/x', 'main', '再改', True)
            self.assertNotIn('按类型派角色', p)
            self.assertNotIn('【角色团】', p)
        finally:
            talk.period_engine = old

    def test_views_carry_participation(self):
        from kblib.stack import launch
        d = kb.period_dir(P)
        m = self.meta()
        m['新产品会话'] = {'需求': {'状态': '已结束', '参与': '👤 参与：产品经理（1 次）', '警告': '只读会话改了文件：a'}}
        kb.save_meta(d, m)
        v = launch.launch_view(d, self.meta())
        self.assertEqual(v['新产品会话']['需求']['参与'], '👤 参与：产品经理（1 次）')
        self.assertEqual(v['新产品会话']['需求']['警告'], '只读会话改了文件：a')
        from kblib.steward import chat
        src = inspect.getsource(chat._route)
        self.assertIn("'参与': team.roles_text(res['角色'])", src)
        self.assertIn("team.wc_alarm(None, snap, '问管家')", src)
        src = inspect.getsource(launch._spawn)
        self.assertIn('[str(ROOT)]', src)


class TestFlowDraft(Base):
    """REQ-016 / REQ-017：flow_draft 提示词、超时、KB_SESSION；engine_run 的 env_extra。"""
    def run_draft(self, on):
        set_settings(开启=on, 档位='精简')
        seen = {}

        def fake_run(e, prompt, cwd, timeout=300, extra_args=None, title='kb·单次', scene='', period=None, env_extra=None, auto=False):
            seen.update(prompt=prompt, timeout=timeout, scene=scene, env=env_extra)
            return 0, {'结果': '没有 JSON'}
        skill = TMP / 'home' / '.claude' / 'skills' / 'demo' / 'SKILL.md'
        skill.parent.mkdir(parents=True, exist_ok=True)
        skill.write_text('---\nname: demo\ndescription: 演示\n---\n步骤', 'utf-8')
        old_run, old_get = engines.engine_run, engines.get_engine
        engines.engine_run, engines.get_engine = fake_run, lambda eid='': CLAUDE
        try:
            with self.assertRaises(kb.KBError) as cm:
                engines.flow_draft(str(skill), use_ai=True)
            self.assertIn('先用「快速生成」再手动改', str(cm.exception))
        finally:
            engines.engine_run, engines.get_engine = old_run, old_get
        return seen

    def test_on(self):
        s = self.run_draft(True)
        self.assertIn('只可调用 Agent 工具请产品经理', s['prompt'])
        self.assertNotIn('不要调用任何工具', s['prompt'])
        self.assertEqual((s['timeout'], s['scene'], s['env']), (900, '流程设计', {'KB_SESSION': 'flow'}))

    def test_off_prompt_unchanged(self):
        s = self.run_draft(False)
        self.assertTrue(s['prompt'].endswith('不要输出其他文字，不要调用任何工具。'))
        self.assertEqual((s['timeout'], s['scene'], s['env']), (900, '', {'KB_SESSION': 'flow'}))

    def test_engine_run_env_extra(self):
        sh = TMP / 'envdump.sh'
        sh.write_text('#!/bin/sh\necho "KB_SESSION=$KB_SESSION"\n')
        sh.chmod(0o755)
        e = dict(CUSTOM, 命令=str(sh), 输出格式='纯文本', 启动参数=['{提示词}'])
        self.assertIn('KB_SESSION=steward', engines.engine_run(e, 'x', str(TMP), env_extra={'KB_SESSION': 'steward'})[1]['结果'])
        os.environ.pop('KB_SESSION', None)
        self.assertEqual(engines.engine_run(e, 'x', str(TMP))[1]['结果'].strip(), 'KB_SESSION=')

    def test_steward_passes_scene_and_env(self):
        from kblib.steward import chat
        src = inspect.getsource(chat._route)
        self.assertIn("scene='管家', env_extra={'KB_SESSION': 'steward'}", src)


class TestTally(Base):
    """REQ-009 / REQ-010：统计、去重、上限记录、超限提醒、Bug 归属。"""
    def logf(self, lines):
        f = Path(tempfile.mkstemp(dir=TMP, suffix='.jsonl')[1])
        f.write_text('\n'.join([json.dumps({'type': 'kb.engine', 'engine': 'claude'})] + lines) + '\n', 'utf-8')
        return f

    def test_count_and_text(self):
        f = self.logf([assistant('Agent', 'product-manager'), assistant('Task', 'product-manager'), assistant('Agent', 'architect'),
                       assistant('SubagentHandback', 'x', parent='toolu_1'), assistant('Agent', 'qa-expert', parent='toolu_1'),
                       '{"type":"user","message":{}}', 'plain text'])
        c = team.team_count(f)
        self.assertEqual(c, {'product-manager': 2, 'architect': 1})
        self.assertEqual(team.roles_text(c), '👤 参与：产品经理 · 架构师（3 次）')
        self.assertEqual(team.roles_text({}), '')

    def test_p0_log(self):
        p0 = Path('/tmp/kb-p0/p0.jsonl')
        if not p0.exists():
            self.skipTest('没有 P0 日志')
        self.assertEqual(team.team_count(p0), {'product-manager': 1})

    def test_tally_dedupe_no_limit(self):
        set_settings(开启=True, 档位='精简')
        d = kb.period_dir(P)
        m = self.meta()
        rec = {}
        f = self.logf([assistant('Agent', 'architect')] * 3)
        team.tally(rec, f, m, d, '方案')
        team.tally(rec, f, m, d, '方案')                      # 同一份日志再收尾一次（_reap）不重复累加
        kb.save_meta(d, m)
        self.assertEqual(rec['角色'], {'architect': 3})
        self.assertEqual(rec['参与'], '👤 参与：架构师（3 次）')
        self.assertEqual(team.team_usage(self.meta()), {'合计': 3, '按角色': {'architect': 3}})
        f2 = self.logf([assistant('Agent', 'product-manager')])
        m = self.meta()
        m['角色调用']['超限已记'] = True                     # 旧版字段：累加时清掉
        team.tally({}, f2, m, d, '管家')
        kb.save_meta(d, m)
        self.assertNotIn('超限已记', self.meta()['角色调用'])
        self.assertNotIn('本期已用', (d / '记录.md').read_text('utf-8'))
        self.assertIn('- architect（', team.team_text('方案', self.meta(), eng=CLAUDE))

    def test_over_scene_cap(self):
        d = kb.period_dir(P)
        m = self.meta()
        team.tally({}, self.logf([assistant('Agent', 'architect')] * 2), m, d, '管家')
        self.assertIn('超限提醒：「管家」会话调用了 2 次角色，场景上限 1', (d / '记录.md').read_text('utf-8'))

    def test_no_roles_no_line(self):
        rec = {}
        team.tally(rec, self.logf(['{"type":"result","result":"ok"}']), self.meta(), None, '方案')
        self.assertNotIn('角色', rec)
        self.assertNotIn('参与', rec)

    def test_bug_without_period(self):
        rec = {}
        team.tally_dirs(rec, self.logf([assistant('Agent', 'qa-expert')]), [], 'Bug分析·Android')
        self.assertEqual(rec['角色'], {'qa-expert': 1})
        d = kb.period_dir(P)
        team.tally_dirs({}, self.logf([assistant('Agent', 'qa-expert')]), [d], 'Bug分析·Android')
        self.assertEqual(team.team_usage(self.meta())['按角色'], {'qa-expert': 1})

    def test_wc_alarm(self):
        import subprocess
        repo = Path(tempfile.mkdtemp(dir=TMP))
        subprocess.run(['git', 'init', '-q'], cwd=repo, check=True)
        rec = {'git前': team.wc_snapshot([str(repo)])}
        self.assertEqual(team.wc_alarm(None, dict(rec), 'x'), [])
        (repo / 'new.txt').write_text('x')
        d = kb.period_dir(P)
        from kblib.ai import devsession
        sent, old = [], devsession._notify
        devsession._notify = lambda *a, **k: sent.append(a)
        try:
            changed = team.wc_alarm(d, rec, '方案会话')
        finally:
            devsession._notify = old
        self.assertTrue(sent and '只读会话改了文件' in sent[0][1])
        self.assertEqual(changed, [f'{repo.name}/new.txt'])
        self.assertIn('只读会话改了文件：', (d / '记录.md').read_text('utf-8'))


class TestApi(Base):
    """契约 openapi.v1.yaml：每个接口的成功和主要错误码。"""
    def test_get_settings_team(self):
        h, _ = call('GET', '/api/settings')
        self.assertEqual(h.code, 200)
        t = h.body['team']
        self.assertEqual((t['开启'], t['档位']), (True, '精简'))           # 没存过设置、已装套件：老用户保持开
        self.assertNotIn('每期上限', t)
        self.assertEqual(len(t['角色']), 16)
        pm = next(r for r in t['角色'] if r['名称'] == 'product-manager')
        self.assertEqual((pm['中文名'], pm['已安装']), ('产品经理', True))
        self.assertIn('方案', pm['场景'])
        self.assertIn('需求（主导）', pm['场景'])
        ux = next(r for r in t['角色'] if r['名称'] == 'ux-designer')
        self.assertEqual((ux['场景'], ux['其他档']), ([], '标准'))
        self.assertIn('方案', ux['其他档场景'])
        self.assertTrue(all(r['负责'] for r in t['角色']))
        s = t['状态']
        self.assertEqual((s['已安装'], s['应有'], s['缺'], s['套件版本'], s['场景表']), (16, 16, [], '1.0.0', '内置兜底'))
        for k in ('kb接入', '默认引擎支持'):
            self.assertIsInstance(s[k], bool)
        install([a for a in team.ALL_AGENTS if a != 'qa-expert'])
        self.assertEqual(call('GET', '/api/settings')[0].body['team']['状态']['缺'], ['qa-expert'])
        install(manifest=False)
        self.assertFalse(call('GET', '/api/settings')[0].body['team']['状态']['套件已装'])

    def test_post_team(self):
        h, r = call('POST', '/api/team', {'on': True, 'tier': '标准', 'limit': 50})       # limit 是旧版字段：忽略
        self.assertTrue(r.page_only)
        self.assertEqual((h.code, h.body), (200, {'msg': '角色团已开启（标准档）'}))
        self.assertEqual(kb.team_settings(), {'开启': True, '档位': '标准'})
        self.assertNotIn('每期上限', kb.load_settings()['角色团'])
        h, _ = call('POST', '/api/team', {'on': False})
        self.assertEqual(h.code, 200)
        self.assertEqual(kb.team_settings(), {'开启': False, '档位': '标准'}, '只改传了的字段')
        h, _ = call('POST', '/api/team', {'tier': '豪华'})
        self.assertEqual(h.code, 400)
        self.assertEqual(kb.team_settings()['档位'], '标准', '出错不保存')
        set_settings(开启=True, 档位='精简', 每期上限=30)                           # 旧版设置文件：保存时去掉每期上限
        call('POST', '/api/team', {'tier': '标准'})
        self.assertNotIn('每期上限', kb.load_settings()['角色团'])

    def test_post_period_team(self):
        h, r = call('POST', '/api/period/team', {'period': P, 'mode': '关'})
        self.assertTrue(r.page_only)
        self.assertEqual((h.code, h.body['msg']), (200, f'「{P}」的角色团改为「关」'))
        log1 = (kb.period_dir(P) / '记录.md').read_text('utf-8')
        self.assertIn('｜ 角色团 ｜ 关', log1)
        h, _ = call('POST', '/api/period/team', {'period': P, 'mode': '关'})
        self.assertEqual(h.body['msg'], '没有变化')
        self.assertEqual((kb.period_dir(P) / '记录.md').read_text('utf-8'), log1, '没变化不记日志')
        h, _ = call('POST', '/api/period/team', {'period': P})
        self.assertEqual((h.code, self.meta().get('角色团')), (200, None))
        for bad in ({'period': '没有这一期'}, {'period': P, 'mode': '开'}, {}):
            h, _ = call('POST', '/api/period/team', bad)
            self.assertEqual(h.code, 400, bad)

    def test_get_period(self):
        v = api_period(P)
        self.assertEqual(v['角色团'], '跟随')
        self.assertEqual(v['角色调用'], {'合计': 0, '按角色': {}})
        h, _ = call('GET', '/api/period', q={'name': '没有这一期'})
        self.assertEqual(h.code, 400)


class TestPushRule(Base):
    """REQ-024 / CR-002：SDD 开发会话、跟进会话提示词写明推送规则；全栈各端、Bug 修复不改。"""
    RULE = '推送：只可以 git push <远端> HEAD:refs/for/<分支> 推 Gerrit 送审'

    def test_dev_prompt(self):
        from kblib.ai import devsession
        p = devsession._dev_prompt(P, '/tmp/x', 'main', devsession.get_flow(), '/tmp/docs')
        self.assertIn(self.RULE, p)
        self.assertIn('git reset --hard、git checkout -- .、删文件等常用命令直接用，不用 kb_ask', p)
        self.assertLess(p.index(self.RULE), p.index('【完成】'), '放在【完成】一节前')

    def test_followup_prompt(self):
        from kblib.ai import talk
        set_settings(开启=False)            # 没开角色团 / 没装套件时同样有（约定说明，不依赖 hook）
        for resumed in (False, True):
            self.assertIn(self.RULE, talk._push_prompt(P, '/tmp/x', 'main', '加个按钮', resumed))
            self.assertNotIn(self.RULE, talk._push_prompt(P, '/tmp/x', 'main', '加个按钮', resumed, stack=True),
                             '全栈多端跟进照旧「不要 push」')

    def test_stack_and_bugfix_unchanged(self):
        from kblib.stack import dev
        from kblib.bugs import fix
        for src in (inspect.getsource(dev), inspect.getsource(fix)):
            self.assertNotIn('refs/for', src)
        self.assertIn('【不要 git push，不要加 / 改 remote】', inspect.getsource(dev._dev_prompt))


class TestMcpRule(unittest.TestCase):
    """REQ-012。"""
    def test_rule5(self):
        from kblib.api import mcp
        src = inspect.getsource(mcp)
        i5, i8 = src.index('\n5. 开发按这一期'), src.index('\n8. 基础知识库')
        self.assertIn('角色团开启时，用 Agent 调专家子 agent 不算换流程；专家不替代流程命令', src[i5:i8])


class TestSuiteSync(unittest.TestCase):
    """REQ-023：内置兜底表与已安装的套件场景表一致（没装套件就跳过）。"""
    def test_builtin_matches_installed(self):
        f = REAL_HOME / '.claude' / 'role-agents' / 'config' / 'kb_scenes.json'
        if not f.exists():
            self.skipTest(f'没装套件场景表：{f}')
        a = json.loads(f.read_text('utf-8'))['场景']
        b = team._BUILTIN['场景']
        diff = [f'{k}：{"套件没有" if k not in a else "兜底没有"}' for k in sorted(set(a) ^ set(b))]
        for k in b:
            if k in a:
                diff += [f'{k}.{x}：套件 {a[k].get(x)!r} ≠ 兜底 {b[k].get(x)!r}' for x in team._DIFF_KEYS if a[k].get(x) != b[k].get(x)]
        self.assertFalse(diff, '\n'.join(diff))


class TestSuiteManage(Base):
    """默认不装、默认关；安装（调套件 install_helper.py）/ 移除（按清单删、先备份、不碰用户自己的）/ 恢复。"""
    def suite_install(self):
        """装一份带 files 清单的套件 + 用户自己的 agent / skill + settings.json 里一条用户 hook 和一条套件 hook。"""
        install()
        base = TMP / 'home' / '.claude'
        files = {f'agents/{a}.md': {'sha': 'x'} for a in team.ALL_AGENTS}
        for r in ('skills/who/SKILL.md', 'role-agents/config/kb_scenes.json', 'role-agents/rules/arbitration.md'):
            (base / r).parent.mkdir(parents=True, exist_ok=True)
            (base / r).write_text('suite', 'utf-8')
            files[r] = {'sha': 'x'}
        (base / 'role-agents' / 'config' / 'kb_scenes.json').write_text('{}', 'utf-8')
        team.MANIFEST.write_text(json.dumps({'suite_version': '1.0.0', 'home': str(TMP / 'nosuite'), 'files': files}), 'utf-8')
        (base / 'agents' / 'my-own.md').write_text('mine', 'utf-8')
        (base / 'skills' / 'mine').mkdir(parents=True)
        (base / 'skills' / 'mine' / 'SKILL.md').write_text('mine', 'utf-8')
        user_hook = {'matcher': 'Bash', 'hooks': [{'type': 'command', 'command': 'echo me'}]}
        suite_hook = {'matcher': 'Task|Agent', 'hooks': [{'type': 'command', 'command': 'python3 x.py # role-agents-suite'}]}
        (base / 'settings.json').write_text(json.dumps({'model': 'opus', 'hooks': {'PreToolUse': [user_hook, suite_hook]}}), 'utf-8')
        return base, user_hook, suite_hook

    def test_default_off(self):
        install(manifest=False)
        self.assertFalse(kb.team_settings()['开启'], '新装（没装套件、没存设置）默认关')
        install()
        self.assertTrue(kb.team_settings()['开启'], '已装套件的老用户保持开')
        set_settings(开启=False, 档位='精简')
        self.assertFalse(kb.team_settings()['开启'])

    def test_remove_and_restore(self):
        base, user_hook, suite_hook = self.suite_install()
        shutil.rmtree(team._backup_root(), ignore_errors=True)
        h, r = call('POST', '/api/team/suite', {'action': 'remove'})
        self.assertTrue(r.page_only)
        self.assertEqual(h.code, 200, h.body)
        self.assertIn('已删：19 个套件文件', h.body['msg'])
        self.assertIn('仍残留：无', h.body['msg'])
        self.assertEqual(team.installed_agents(), [])
        self.assertFalse(team.MANIFEST.exists())
        self.assertFalse((base / 'skills' / 'who').exists())
        self.assertFalse((base / 'role-agents').exists())
        self.assertEqual((base / 'agents' / 'my-own.md').read_text('utf-8'), 'mine', '用户自己的 agent 不动')
        self.assertTrue((base / 'skills' / 'mine' / 'SKILL.md').is_file(), '用户自己的 skill 不动')
        st = json.loads((base / 'settings.json').read_text('utf-8'))
        self.assertEqual((st['model'], st['hooks']), ('opus', {'PreToolUse': [user_hook]}))
        self.assertFalse(kb.team_settings()['开启'], '移除后关')
        bk = team.team_status()['状态']['备份']
        self.assertEqual((len(bk), bk[0]['文件数']), (1, 19))
        h, _ = call('POST', '/api/team/suite', {'action': 'remove'})
        self.assertEqual(h.code, 400, '没装时移除报错')
        st['theme'] = 'dark'                                    # 移除后用户又改了 settings.json：恢复不覆盖
        (base / 'settings.json').write_text(json.dumps(st), 'utf-8')
        h, _ = call('POST', '/api/team/suite', {'action': 'restore'})
        self.assertEqual(h.code, 200, h.body)
        self.assertEqual(len(team.installed_agents()), 16)
        self.assertTrue(team.MANIFEST.is_file())
        self.assertEqual((base / 'skills' / 'who' / 'SKILL.md').read_text('utf-8'), 'suite')
        st = json.loads((base / 'settings.json').read_text('utf-8'))
        self.assertEqual((st['theme'], st['hooks']['PreToolUse']), ('dark', [user_hook, suite_hook]))
        call('POST', '/api/team/suite', {'action': 'restore'})
        st = json.loads((base / 'settings.json').read_text('utf-8'))
        self.assertEqual(st['hooks']['PreToolUse'], [user_hook, suite_hook], '重复恢复 hook 不叠加')
        self.assertTrue(team.team_status()['状态']['备份'][0]['已恢复'])

    def test_remove_cleans_leftovers_and_restores(self):
        """移除连带清掉：带套件 hook 的 settings.json.bak-<时间>、套件文件的 .bak-<时间>、清单外套件生成的同名 agent、
        自动学习定时任务（launchd / crontab）、项目级安装；自检报干净，提示在跑会话；恢复全部放回。用户自己的 .bak 不碰。"""
        base, user_hook, suite_hook = self.suite_install()
        shutil.rmtree(team._backup_root(), ignore_errors=True)
        man = json.loads(team.MANIFEST.read_text('utf-8'))
        man['files'].pop('agents/tech-scout.md')              # 清单外、但由套件 build.py 生成的同名 agent
        (base / 'agents' / 'tech-scout.md').write_text('---\nname: tech-scout\n---\n<!-- 由 scripts/build.py 生成 -->', 'utf-8')
        team.MANIFEST.write_text(json.dumps(man), 'utf-8')
        (base / 'settings.json.bak-20261119102729870414').write_text('{"x": "python3 a.py # role-agents-suite"}', 'utf-8')
        (base / 'settings.json.bak').write_text('{"mine": 1}', 'utf-8')
        (base / 'settings.json.bak-figma').write_text('{"x": "# role-agents-suite"}', 'utf-8')
        (base / 'settings.json.bak-20261001000000').write_text('{"mine": 2}', 'utf-8')
        (base / 'agents' / 'architect.md.bak-20261119103108').write_text('old', 'utf-8')
        (base / 'agents' / 'my-own.md.bak-20261119103108').write_text('mine', 'utf-8')
        pl = team._plist()
        pl.parent.mkdir(parents=True, exist_ok=True)
        pl.write_text('<plist/>', 'utf-8')
        CRON['text'] = '0 9 * * * echo me\n0 10 * * * python3 auto_refresh.py --run # worker-agent skills-refresh\n'
        home = TMP / 'nosuite'
        proj = TMP / 'proj'
        pb = proj / '.claude'
        (pb / 'agents').mkdir(parents=True)
        (pb / 'role-agents').mkdir(parents=True)
        (pb / 'agents' / 'qa-expert.md').write_text('p', 'utf-8')
        (pb / 'role-agents' / 'manifest.json').write_text(json.dumps({'home': str(home), 'files': {'agents/qa-expert.md': {}}}), 'utf-8')
        (pb / 'settings.json').write_text(json.dumps({'hooks': {'Stop': [suite_hook]}}), 'utf-8')
        (home / 'state').mkdir(parents=True, exist_ok=True)
        (home / 'state' / 'projects.json').write_text(json.dumps({'projects': [str(proj), str(TMP / 'gone')]}), 'utf-8')
        self.assertEqual(team.team_status()['状态']['项目级安装'], [str(proj)])
        real, team._running_sessions = team._running_sessions, lambda: ['开发 · 某期（开发中）']
        try:
            h, _ = call('POST', '/api/team/suite', {'action': 'remove'})
        finally:
            team._running_sessions = real
        self.assertEqual(h.code, 200, h.body)
        msg = h.body['msg']
        self.assertIn('仍残留：无', msg)
        self.assertIn('2 个套件留下的 .bak', msg)
        self.assertIn('自动学习定时任务（launchd、crontab）', msg)
        self.assertIn(f'项目级安装 {proj}/.claude', msg)
        self.assertIn('开发 · 某期（开发中）', msg)
        self.assertIn('到会话结束前仍能调用', msg)
        self.assertFalse((base / 'agents' / 'tech-scout.md').exists(), '清单外套件生成的同名 agent 也删')
        self.assertFalse((base / 'settings.json.bak-20261119102729870414').exists())
        self.assertFalse((base / 'agents' / 'architect.md.bak-20261119103108').exists())
        for f in ('settings.json.bak', 'settings.json.bak-figma', 'settings.json.bak-20261001000000', 'agents/my-own.md.bak-20261119103108'):
            self.assertTrue((base / f).is_file(), f'用户自己的 {f} 不碰')
        self.assertFalse(pl.exists())
        self.assertIn(('unload', str(pl)), LAUNCHCTL)
        self.assertEqual(CRON['text'].strip(), '0 9 * * * echo me', 'crontab 只删套件那一行')
        self.assertFalse((pb / 'agents' / 'qa-expert.md').exists())
        self.assertFalse((pb / 'role-agents').exists())
        self.assertNotIn('role-agents-suite', (pb / 'settings.json').read_text('utf-8'))
        self.assertEqual(team.suite_residue(), [])
        self.assertEqual(team.team_status()['状态']['残留'], [])
        h, _ = call('POST', '/api/team/suite', {'action': 'remove'})
        self.assertEqual(h.code, 400, '清干净后再移除报没什么可移除的')
        # 恢复：全部放回
        h, _ = call('POST', '/api/team/suite', {'action': 'restore'})
        self.assertEqual(h.code, 200, h.body)
        self.assertIn('自动学习定时任务（launchd）', h.body['msg'])
        self.assertEqual(len(team.installed_agents()), 16)
        self.assertTrue((base / 'settings.json.bak-20261119102729870414').is_file())
        self.assertTrue((base / 'agents' / 'architect.md.bak-20261119103108').is_file())
        self.assertTrue(pl.is_file())
        self.assertIn(('load', str(pl)), LAUNCHCTL)
        self.assertIn('# worker-agent skills-refresh', CRON['text'])
        self.assertIn('echo me', CRON['text'])
        self.assertTrue((pb / 'agents' / 'qa-expert.md').is_file())
        self.assertTrue((pb / 'role-agents' / 'manifest.json').is_file())
        self.assertIn('role-agents-suite', (pb / 'settings.json').read_text('utf-8'))
        CRON['text'] = ''

    def test_remove_keep_projects_and_residue_reported(self):
        """projects=false 不动项目级安装，仍残留里列出来；套件已删但有残留时，设置卡片报残留、可再点「清理残留」。"""
        base, _, _ = self.suite_install()
        home = TMP / 'nosuite'
        proj = TMP / 'proj2'
        (proj / '.claude' / 'role-agents').mkdir(parents=True)
        (proj / '.claude' / 'role-agents' / 'manifest.json').write_text(json.dumps({'files': {}}), 'utf-8')
        (home / 'state').mkdir(parents=True, exist_ok=True)
        (home / 'state' / 'projects.json').write_text(json.dumps({'projects': [str(proj)]}), 'utf-8')
        h, _ = call('POST', '/api/team/suite', {'action': 'remove', 'projects': False})
        self.assertEqual(h.code, 200, h.body)
        self.assertIn(f'仍残留：项目级安装：{proj}/.claude', h.body['msg'])
        (base / 'agents').mkdir(parents=True, exist_ok=True)
        (base / 'agents' / 'architect.md').write_text('mine', 'utf-8')     # 用户自己写的同名 agent：报残留，不删
        left = team.team_status()['状态']['残留']
        self.assertTrue(any('architect' in x for x in left), left)
        self.assertTrue(any(str(proj) in x for x in left), left)
        h, _ = call('POST', '/api/team/suite', {'action': 'remove'})   # 「清理残留」
        self.assertEqual(h.code, 200, h.body)
        self.assertFalse((proj / '.claude' / 'role-agents').exists())
        self.assertEqual((base / 'agents' / 'architect.md').read_text('utf-8'), 'mine')
        self.assertIn('仍残留：~/.claude/agents 里还有角色 agent：architect', h.body['msg'])
        (home / 'state' / 'projects.json').unlink()

    def test_remove_rejects_unsafe_manifest(self):
        install()
        team.MANIFEST.write_text(json.dumps({'files': {'../evil.txt': {}}}), 'utf-8')
        h, _ = call('POST', '/api/team/suite', {'action': 'remove'})
        self.assertEqual(h.code, 400)
        self.assertTrue(team.MANIFEST.is_file())

    def test_install(self):
        install(manifest=False)
        shutil.rmtree(TMP / 'home' / '.claude' / 'agents')
        set_settings()
        home = TMP / 'suite'
        (home / 'scripts').mkdir(parents=True, exist_ok=True)
        (home / 'scripts' / 'install_helper.py').write_text(
            'import json, os, sys\n'
            'assert "--user" in sys.argv\n'
            'b = os.path.expanduser("~/.claude")\n'
            'os.makedirs(b + "/agents", exist_ok=True); os.makedirs(b + "/role-agents", exist_ok=True)\n'
            'for a in %r: open(b + "/agents/" + a + ".md", "w").write("---\\nname: " + a + "\\n---\\n")\n'
            'json.dump({"suite_version": "9", "home": %r, "files": {}}, open(b + "/role-agents/manifest.json", "w"))\n'
            'print("ok")\n' % (team.ALL_AGENTS, str(home)), 'utf-8')
        h, _ = call('POST', '/api/team/suite', {'action': 'install', 'home': str(TMP / 'nope')})
        self.assertEqual(h.code, 400)
        h, _ = call('POST', '/api/team/suite', {'action': 'install', 'home': str(home)})
        self.assertEqual(h.code, 200, h.body)
        self.assertEqual(len(team.installed_agents()), 16)
        self.assertFalse(kb.team_settings()['开启'], '新装后开关仍是关')
        self.assertEqual(kb.load_settings()['角色团']['套件目录'], str(home))
        self.assertEqual(team.team_status()['状态']['套件目录'], str(home))
        h, _ = call('POST', '/api/team/suite', {'action': 'nope'})
        self.assertEqual(h.code, 400)


if __name__ == '__main__':
    unittest.main()
