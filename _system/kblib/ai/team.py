"""角色团：把角色套件（worker-agent）的专家以子 agent 身份挂进知识库后台会话。

  知识库继续管流程和拍板，专家只出意见：engines.engine_cmd 在 rules_text() 后面接 team_text(scene, …)，
  各会话入口只传 scene（管家 / 流程设计 / 需求 / 方案 / Bug分析·<端> / Bug修复·<端> / 开发 / 开发·<端> / 审查·<端> / 跟进 / 发布）。
  只对本机 Claude 引擎生效；全局关、期选「关」、scene 为空、续跑、远程主机会话都返回空串（命令行和现在逐字一致）。

  设置.json「角色团」{开启, 档位: 精简 / 标准, 每期上限}；期信息「角色团」= 关（不存 = 跟随全局）、「角色调用」{合计, 按角色, 超限已记}。
  场景表（契约，CR-001）：套件安装到 ~/.claude/role-agents/config/kb_scenes.json；文件缺失、解析失败、版本不是 1 时整表用内置兜底表，
  文件里缺某个场景（或缺当前档位名单）时该场景用兜底表的同名场景。不读 team.yaml：派单直接取当前档位的显式名单，再按已装 agent 过滤。
  统计：会话结束时从 stream-json 日志数主会话的 Agent / Task 调用（子 agent 自己的调用带 parent_tool_use_id，不算），
  写进会话记录「角色」{角色: 次数}，累加到期「角色调用」。
"""
import json
import re
import sys
from pathlib import Path

from kblib.core.common import KBError, load_settings, read_text, save_settings
from kblib.core.periods import load_meta, log, period_dir, save_meta

HOME = Path.home() / '.claude'
AGENTS_DIR = HOME / 'agents'
SUITE = HOME / 'role-agents'
MANIFEST = SUITE / 'manifest.json'
SCENES_FILE = SUITE / 'config' / 'kb_scenes.json'
INSTALL_HINT = 'cd <worker-agent 仓库> && python3 scripts/install_helper.py（装到用户级 ~/.claude）'

EXPERTS = ['product-manager', 'ux-designer', 'architect', 'frontend-engineer', 'client-engineer', 'backend-engineer', 'qa-expert',
           'sre-security', 'data-growth', 'ai-product-manager', 'ai-app-engineer', 'model-algorithm-engineer', 'ai-infra-mlops',
           'ai-eval-compliance']
ALL_AGENTS = EXPERTS + ['orchestrator', 'tech-scout']          # 套件装好应有 16 个
ROLE_CN = {'product-manager': '产品经理', 'ux-designer': '体验设计师', 'architect': '架构师', 'frontend-engineer': '前端工程师',
           'client-engineer': '客户端工程师', 'backend-engineer': '后端工程师', 'qa-expert': '测试专家', 'sre-security': 'SRE 与安全工程师',
           'data-growth': '数据与增长运营', 'ai-product-manager': 'AI 产品经理', 'ai-app-engineer': 'AI 应用工程师',
           'model-algorithm-engineer': '模型与算法工程师', 'ai-infra-mlops': 'AI Infra 工程师', 'ai-eval-compliance': 'AI 评测与合规专家',
           'orchestrator': '总控', 'tech-scout': '技术情报员'}
TIERS = ('精简', '标准')
DEFAULTS = {'开启': True, '档位': '精简', '每期上限': 30}       # 每期上限：设置.json 没有时用场景表「每期上限默认」，再没有用 30
# P0 验证（2026-11-16，记在期「角色团接入知识库」）：子 agent 继承 --disallowedTools（Write 不可用），Agent 可传 model。
# 改成 False：只读场景只派场景表「只读安全角色」（主导被滤掉由 architect 顶）；自动场景不派「自动场景不派」里的角色（而不是指定 sonnet）。
P0_INHERITS_DISALLOWED = True
P0_MODEL_PARAM = True

# ---------------------------------------------------------------- 内置兜底场景表（和套件 config/kb_scenes.json 同格式）
_BUILTIN = {
    '版本': 1,
    '档位': {'精简': 'lite', '标准': 'standard'},
    '端角色': {'Android': 'client-engineer', 'iOS': 'client-engineer', '鸿蒙': 'client-engineer', '小程序': 'client-engineer',
              '客户端': 'client-engineer', 'Unity': 'client-engineer', 'Web': 'frontend-engineer', '前端': 'frontend-engineer',
              'H5': 'frontend-engineer', '服务端': 'backend-engineer', '后端': 'backend-engineer', '默认': 'backend-engineer'},
    '只读安全角色': ['product-manager', 'ux-designer', 'qa-expert', 'architect'],
    '自动场景不派': ['product-manager', 'architect', 'ai-product-manager'],
    '自动场景模型': 'sonnet',
    '每期上限默认': 30,
    '产物目录': '其他资料/角色产物',
    '场景': {
        '管家': {'触发': '问管家：涉及流程或需求设计时派产品经理，涉及技术时派架构师；只是问答不派', '上限': 1, '只读': True,
               '标准': {'主导': 'product-manager', '参与': ['architect']}, '精简': {'主导': 'product-manager', '参与': ['architect']},
               '用途': {'product-manager': '流程、确认点、需求设计', 'architect': '技术判断、选型'}, '产物': ''},
        '流程设计': {'触发': '从技能生成流程（AI）：请产品经理设计开发步骤和确认点，最后只输出 JSON', '上限': 1, '只读': True,
                 '标准': {'主导': 'product-manager', '参与': []}, '精简': {'主导': 'product-manager', '参与': []},
                 '用途': {'product-manager': '设计开发步骤和确认点'}, '产物': ''},
        '需求': {'触发': '完善需求：产品经理按 /pm 11 章骨架补齐异常态、可测验收（当…时，应…）、埋点口径；AI 产品经理给 AI 机会评估；涉及埋点时加数据与增长运营',
               '上限': 3, '只读': True,
               '标准': {'主导': 'product-manager', '参与': ['ai-product-manager', 'data-growth']}, '精简': {'主导': 'product-manager', '参与': []},
               '用途': {'product-manager': 'PRD 主笔、异常态、验收标准', 'ai-product-manager': 'AI 机会评估', 'data-growth': '埋点方案与指标口径'},
               '产物': 'PRD'},
        '需求体检': {'触发': '管家巡检发现新需求资料（P3）：列待确认问题、缺的异常态、指标口径', '上限': 3, '只读': True,
                 '标准': {'主导': 'product-manager', '参与': ['ux-designer', 'ai-product-manager']}, '精简': {'主导': 'product-manager', '参与': []},
                 '用途': {'product-manager': '需求体检主笔', 'ux-designer': '交互缺口与异常态', 'ai-product-manager': 'AI 机会评估'}, '产物': '需求体检'},
        '方案': {'触发': '出方案：架构师主导；产品经理拆需求、写验收；体验设计师对照设计稿；需要新接口时加后端工程师', '上限': 4, '只读': True,
               '标准': {'主导': 'architect', '参与': ['product-manager', 'ux-designer', 'backend-engineer']},
               '精简': {'主导': 'architect', '参与': ['product-manager', 'backend-engineer']},
               '用途': {'architect': '技术方案、接口契约、风险', 'product-manager': '拆需求、验收标准', 'ux-designer': '对照设计稿、缺状态',
                      'backend-engineer': '新接口与数据变更'}, '产物': '方案'},
        '开发': {'触发': '开发包裹：只在流程确认点发 kb_ask 前请架构师或产品经理预审，意见附在 context，recommend 末尾注明「建议来自：<角色>」',
               '上限': 6, '每确认点上限': 1, '只读': False,
               '标准': {'主导': 'architect', '参与': ['product-manager']}, '精简': {'主导': 'architect', '参与': ['product-manager']},
               '用途': {'architect': '规划 / 接口 / 技术方案类确认点预审', 'product-manager': '需求 / 待确认问题类确认点预审'}, '产物': ''},
        '开发·端': {'触发': '全栈各端开发：确认点前请架构师预审，测试专家补用例', '上限': 6, '每确认点上限': 1, '只读': False,
                 '标准': {'主导': 'architect', '参与': ['qa-expert']}, '精简': {'主导': 'architect', '参与': ['qa-expert']},
                 '用途': {'architect': '确认点预审', 'qa-expert': '用例与自测覆盖'}, '产物': ''},
        '审查·端': {'触发': '全栈各端审查：架构师做 Code Review，测试专家看用例覆盖', '上限': 2, '只读': True,
                 '标准': {'主导': 'architect', '参与': ['qa-expert']}, '精简': {'主导': 'architect', '参与': ['qa-expert']},
                 '用途': {'architect': 'Code Review', 'qa-expert': '用例覆盖与回归'}, '产物': '测试报告'},
        'Bug分析': {'触发': 'Bug 分析：对应端工程师定位，测试专家补复现步骤和影响面（只读场景若子 agent 不继承禁用工具，工程师不派，改由测试专家 + 架构师）', '上限': 2, '只读': True,
                  '标准': {'主导': '@端', '参与': ['qa-expert']}, '精简': {'主导': '@端', '参与': ['qa-expert']},
                  '用途': {'@端': '定位根因', 'qa-expert': '复现步骤、影响面'}, '产物': 'Bug分析'},
        'Bug修复': {'触发': 'Bug 修复：对应端工程师', '上限': 1, '只读': False,
                  '标准': {'主导': '@端', '参与': []}, '精简': {'主导': '@端', '参与': []},
                  '用途': {'@端': '按通过的分析修复并自测'}, '产物': ''},
        '跟进': {'触发': '期里发跟进：先判类型。问题 → 不派角色直接回答；缺陷 → 端工程师 + 测试专家；需求扩展 → 产品经理先写变更说明（改什么、影响谁、延后什么）再 kb_ask',
               '上限': 2, '只读': False,
               '标准': {'主导': 'product-manager', '参与': ['@端', 'qa-expert']}, '精简': {'主导': 'product-manager', '参与': ['@端', 'qa-expert']},
               '用途': {'product-manager': '需求扩展的变更说明（默认不扩大范围）', '@端': '缺陷定位', 'qa-expert': '缺陷复现与影响面'},
               '产物': '变更说明'},
        '发布': {'触发': '出发布清单：标准档 SRE 与安全工程师，精简档由架构师兼', '上限': 1, '只读': True,
               '标准': {'主导': 'sre-security', '参与': []}, '精简': {'主导': 'architect', '参与': []},
               '用途': {'sre-security': '灰度、回滚、监控、安全检查', 'architect': '兼发布方案：灰度、回滚'}, '产物': '发布清单'},
    },
}


# ---------------------------------------------------------------- 设置与状态
def team_settings():
    s = load_settings().get('角色团') or {}
    out = dict(DEFAULTS)
    try:
        dft = load_scenes()[0].get('每期上限默认')
        if isinstance(dft, int) and not isinstance(dft, bool) and 0 <= dft <= 200:
            out['每期上限'] = dft
    except Exception:          # noqa: BLE001 — 场景表坏了也不影响读设置
        pass
    if isinstance(s.get('开启'), bool):
        out['开启'] = s['开启']
    if s.get('档位') in TIERS:
        out['档位'] = s['档位']
    if isinstance(s.get('每期上限'), int) and not isinstance(s.get('每期上限'), bool) and 0 <= s['每期上限'] <= 200:
        out['每期上限'] = s['每期上限']
    return out


def _int_limit(v):
    if isinstance(v, bool):
        raise KBError('每期上限要是 0 到 200 之间的整数')
    try:
        n = int(v) if isinstance(v, int) or (isinstance(v, str) and re.fullmatch(r'\s*\d+\s*', v)) else None
    except ValueError:
        n = None
    if n is None or isinstance(v, float) or not 0 <= n <= 200:
        raise KBError('每期上限要是 0 到 200 之间的整数')
    return n


def set_team(on=None, tier=None, limit=None):
    """网页「⚙ 设置 → AI 引擎 → 角色团」：只传要改的字段。"""
    cur = team_settings()
    new = dict(cur)
    if on is not None:
        new['开启'] = bool(on)
    if tier not in (None, ''):
        if tier not in TIERS:
            raise KBError(f'档位只能是：{"、".join(TIERS)}')
        new['档位'] = tier
    if limit not in (None, ''):
        new['每期上限'] = _int_limit(limit)
    if new == cur and (load_settings().get('角色团') or None) is not None:
        return '没有变化'
    s = load_settings()
    s['角色团'] = new
    save_settings(s)
    return '角色团' + ('已关闭：后台会话不再派角色' if not new['开启'] else f'已开启（{new["档位"]}档，每期最多调用 {new["每期上限"]} 次）')


def set_period_team(period, mode='跟随', by='用户（网页）'):
    """期级覆盖：跟随全局 / 关（仿 set_period_engine）。"""
    if not (period or '').strip():
        raise KBError('没有期名')
    mode = mode or '跟随'
    if mode not in ('跟随', '关'):
        raise KBError('角色团只能选「跟随」或「关」')
    d = period_dir(period)
    m = load_meta(d)
    if (m.get('角色团') or '跟随') == mode:
        return '没有变化'
    if mode == '关':
        m['角色团'] = '关'
    else:
        m.pop('角色团', None)
    save_meta(d, m)
    log(d, '角色团', ('关：这一期的后台会话不派角色' if mode == '关' else f'跟随全局（现在{"开" if team_settings()["开启"] else "关"}）') + f'（{by}）')
    return f'「{d.name}」的角色团改为「{"关" if mode == "关" else "跟随全局"}」'


def is_claude(eng):
    """同 launch._ro 的判断：内置 claude，或命令名是 claude 的自定义引擎。"""
    return bool(eng) and (eng.get('id') == 'claude' or Path(eng.get('命令') or '').name == 'claude')


def installed_agents():
    return [a for a in ALL_AGENTS if (AGENTS_DIR / f'{a}.md').is_file()]


def _manifest():
    try:
        return json.loads(read_text(MANIFEST))
    except (OSError, ValueError):
        return None


def _suite_scenes():
    """安装后的套件场景表；缺失、JSON 坏了、版本不是 1、没有「场景」→ None。"""
    try:
        t = json.loads(read_text(SCENES_FILE))
    except (OSError, ValueError):
        return None
    if isinstance(t, dict) and t.get('版本') == 1 and isinstance(t.get('场景'), dict) and t['场景']:
        return t
    return None


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _ok_tier(x):
    return (isinstance(x, dict) and isinstance(x.get('主导'), str) and bool(x['主导'])
            and isinstance(x.get('参与', []), list) and all(isinstance(r, str) for r in x.get('参与', [])))


def _ok_scene(c, tier=None):
    """场景配置字段类型都对（上限是正整数、名单是字符串、用途是 dict…）；tier 给了还要这一档有名单。"""
    if not isinstance(c, dict) or not _is_int(c.get('上限', 1)) or c.get('上限', 1) < 0:
        return False
    if '每确认点上限' in c and c['每确认点上限'] is not None and not _is_int(c['每确认点上限']):
        return False
    if not isinstance(c.get('用途', {}), dict) or not all(isinstance(v, str) for v in (c.get('用途') or {}).values()):
        return False
    if not isinstance(c.get('触发', ''), str) or not isinstance(c.get('产物', ''), str):
        return False
    if any(x in c and not _ok_tier(c[x]) for x in TIERS):
        return False
    return _ok_tier(c.get(tier)) if tier else True


_TOP_TYPES = {'端角色': dict, '只读安全角色': list, '自动场景不派': list, '自动场景模型': str, '产物目录': str}


def load_scenes(tier=None):
    """→ (场景表, 来源 套件 / 内置兜底)。整表不可用时用兜底；文件里缺某场景、缺这一档的名单或字段类型不对时，该场景用兜底的同名场景
    （兜底里也没有的就去掉）；顶层字段类型不对用兜底的值。"""
    t = _suite_scenes()
    if t is None:
        return _BUILTIN, '内置兜底'
    t = dict(t)
    sc = {k: c for k, c in t['场景'].items() if isinstance(k, str) and _ok_scene(c)}
    for k, c in _BUILTIN['场景'].items():
        if not all(_ok_scene(sc.get(k), x) for x in ([tier] if tier else TIERS)):
            sc[k] = c
    t['场景'] = sc
    for k, typ in _TOP_TYPES.items():
        v = t.get(k)
        if not v or not isinstance(v, typ) or (typ is dict and not all(isinstance(x, str) for x in v.values())) \
                or (typ is list and not all(isinstance(x, str) for x in v)):
            t[k] = _BUILTIN[k]
    return t, '套件'


_DIFF_KEYS = ('上限', '每确认点上限', '只读', '标准', '精简', '产物')


def scene_diff():
    """套件场景表和内置兜底逐场景比较（场景名、上限、每确认点上限、只读、两档名单、产物）→ ['场景.字段', …]；没装套件 → None。"""
    t = _suite_scenes()
    if t is None:
        return None
    a, b = t['场景'], _BUILTIN['场景']
    out = [f'{k}（{"套件没有" if k not in a else "兜底没有"}）' for k in sorted(set(a) ^ set(b))]
    for k in b:
        if k in a:
            out += [f'{k}.{f}' for f in _DIFF_KEYS if (a[k] or {}).get(f) != b[k].get(f)]
    return out


def _kb_linked():
    try:
        return 'kb' in (json.loads(read_text(Path.home() / '.claude.json')).get('mcpServers') or {})
    except (OSError, ValueError):
        return False


def team_status():
    """设置卡片用：开关 / 档位 / 每期上限 + 安装状态（契约 TeamSettings）。"""
    from kblib.ai.engines import get_engine        # 同层；engines 也要 import 本模块，放到用时
    st = team_settings()
    man = _manifest()
    have = installed_agents()
    try:
        dft = is_claude(get_engine())
    except KBError:
        dft = False
    _, src = load_scenes()
    diff = scene_diff()
    st['状态'] = {'已安装': len(have), '应有': len(ALL_AGENTS), '缺': [a for a in ALL_AGENTS if a not in have],
                 '套件版本': (man or {}).get('suite_version', '') if man else '', '套件已装': man is not None,
                 'kb接入': _kb_linked(), '场景表': src, '场景表差异': diff or [], '默认引擎支持': dft, '安装命令': INSTALL_HINT}
    return st


# ---------------------------------------------------------------- 开关判断与提示词
def _meta(period):
    if isinstance(period, dict):
        return period
    if period:
        try:
            return load_meta(period_dir(period))
        except KBError:
            return None
    return None


def team_on(m, eng):
    """这个会话挂不挂角色团：全局开、期没选关、本机 Claude 引擎、套件装了。"""
    if not team_settings()['开启'] or not is_claude(eng):
        return False
    if (_meta(m) or {}).get('角色团') == '关':
        return False
    return _manifest() is not None


def _scene_key(scene, table):
    """「开发·Android」→ 开发·端（有的话）/ 开发；返回 (场景键, 端)。"""
    base, _, end = scene.partition('·')
    if end and f'{base}·端' in table:
        return f'{base}·端', end
    if scene in table:
        return scene, ''
    return (base if base in table else ''), end


def _end_roles(end, m, t):
    """@端 换成工程师：端名查「端角色」，查不到（如 kb、角色套件）用「默认」；没有端时按这一期的各端。"""
    ends = t.get('端角色') or _BUILTIN['端角色']
    if end:
        return [ends.get(end) or ends.get('默认') or 'backend-engineer']
    names = list(((m or {}).get('各端') or {}).keys())
    out = []
    for e in names or ['默认']:
        r = ends.get(e) or ends.get('默认') or 'backend-engineer'
        if r not in out:
            out.append(r)
    return out


_WARNED = set()
_WARNED_P = set()


def _warn_unknown(scene, m):
    """未知场景：除了网页日志，也在这一期的记录里记一次（同一期同一场景只记一次）。"""
    name = (m or {}).get('名称') if isinstance(m, dict) else ''
    if not name or (name, scene) in _WARNED_P:
        return
    _WARNED_P.add((name, scene))
    try:
        log(period_dir(name), '角色团', f'⚠ 场景「{scene}」不在场景表里（套件 kb_scenes.json 和内置兜底都没有），这个会话没挂角色团')
    except (KBError, OSError):
        pass


def scene_roles(scene, m=None, end='', auto=False, tier=None):
    """本场景可派的角色（已按档位名单、安装、只读、自动场景过滤）→ (场景配置, [(角色, 用途, 主导?)])。
    主导被滤掉时由参与里第一个可派的顶上；都派不了返回空列表（不挂段落）。"""
    tier = tier or team_settings()['档位']
    t, _ = load_scenes(tier)
    table = t.get('场景') or {}
    key, e2 = _scene_key(scene, table)
    cfg = table.get(key)
    if not cfg:
        if scene not in _WARNED:
            _WARNED.add(scene)
            print(f'角色团：场景「{scene}」不在场景表里，不挂角色团', file=sys.stderr, flush=True)
        return None, []
    end = end or e2
    pick = cfg.get(tier) or {}
    use = cfg.get('用途') or {}
    have = set(installed_agents())
    safe = set(t.get('只读安全角色') or _BUILTIN['只读安全角色'])
    no_auto = set(t.get('自动场景不派') or _BUILTIN['自动场景不派'])
    ro_filter = bool(cfg.get('只读')) and not P0_INHERITS_DISALLOWED

    def expand(r):
        return _end_roles(end, m, t) if r == '@端' else [r]

    def ok(x):
        return x in have and not (ro_filter and x not in safe) and not (auto and not P0_MODEL_PARAM and x in no_auto)
    out = []

    def add(x, why, lead):
        if x and ok(x) and all(x != y[0] for y in out):
            out.append((x, why, lead))
    lead = pick.get('主导')
    for x in expand(lead) if lead else []:
        if ro_filter and x not in safe:                 # P0 不继承禁用：只读场景被滤掉的主导由 architect 顶
            add('architect', use.get('architect') or use.get(lead) or '', True)
        else:
            add(x, use.get(lead) or use.get(x) or '', True)
    for r in pick.get('参与') or []:
        for x in expand(r):
            add(x, use.get(r) or use.get(x) or '', False)
    if out and not any(ld for _, _, ld in out):          # 主导没装：参与里第一个顶上
        out[0] = (out[0][0], out[0][1], True)
    return cfg, out


def role_cn(slug):
    return ROLE_CN.get(slug) or slug


def team_usage(m):
    """期「角色调用」→ 契约 TeamUsage（上限取当前全局设置）。"""
    u = (m or {}).get('角色调用') or {}
    return {'合计': int(u.get('合计') or 0), '上限': team_settings()['每期上限'], '按角色': dict(u.get('按角色') or {}),
            '超限已记': bool(u.get('超限已记'))}


_OWN_TOOL_PRODUCTS = ('PRD', '方案', 'Bug分析')        # 有原来的工具（kb_write_doc 需求 / kb_stack_plan / kb_bug_analysis），不另写角色产物


def team_text(scene, m=None, end='', eng=None, auto=False):
    """角色团提示词段落；不挂时返回空串。m：期信息（dict）或期名，没有归属期时 None。"""
    if not scene:
        return ''
    m = _meta(m)
    if eng is not None and not team_on(m, eng):
        return ''
    cfg, roles = scene_roles(scene, m, end, auto)
    if cfg is None:
        _warn_unknown(scene, m)
    if not cfg or not roles:
        return ''
    t, _ = load_scenes(team_settings()['档位'])
    limit = team_settings()['每期上限']
    if m is not None:
        left = limit - team_usage(m)['合计']
        if left <= 0:
            return '\n\n【角色团】本期角色调用已用完，不要再派角色，按原流程继续。'
    else:
        left = None
    cap = int(cfg.get('上限') or 1)
    per = cfg.get('每确认点上限')
    pdir = t.get('产物目录') or _BUILTIN['产物目录']
    sub = pdir.split('/', 1)[1] if '/' in pdir else '角色产物'
    prod = cfg.get('产物') or ''
    model = t.get('自动场景模型') or 'sonnet'
    lines = [f'- {r}（{role_cn(r)}{"，主导" if lead else ""}）' + (f'：{why}' if why else '') for r, why, lead in roles]
    lead = next((r for r, _, ld in roles if ld), roles[0][0])
    out = [f'\n\n【角色团】流程命令照旧，专家只出意见：开发流程、斜杠命令、知识库工具照原来的要求用，专家子 agent 不替代它们。',
           f'本场景「{scene}」可以用 Agent 工具（subagent_type 填英文名）请这些专家：',
           *lines,
           f'什么时候派：{cfg.get("触发") or "需要专业意见时"}。不需要就不派。',
           f'- 次数：本会话最多派 {cap} 次' + (f'（每个确认点最多 {per} 次）' if per else '')
           + (f'；本期还能派 {left} 次（每期上限 {limit}）' if left is not None else '')
           + '。到了上限就不再派角色，按原流程继续，并用 kb_log 记一句「角色团：已到上限，不再派角色」。',
           '- 派单必须前台运行：不要 run_in_background，等子 agent 交回结果再继续。'
           + (f'这是自动发起的会话：派单时 model 参数填 "{model}"。' if auto and P0_MODEL_PARAM else ''),
           '- 任务说明开头写「知识库会话：只把正文交回，不写文件、不写 workspace/」；子 agent 只交回正文，不写文件。',
           ('- 产物由你用知识库工具落库：PRD / 方案 / Bug 分析照旧走 kb_write_doc / kb_stack_plan / kb_bug_analysis；'
            f'本场景的角色产物「{prod}」用 kb_write_doc 写到 category=其他资料、filename={sub}/<阶段序号>-{prod}.md，'
            '同名覆盖更新，不要新建重复文件；不要在仓库或知识库目录里建 workspace/。' if prod and prod not in _OWN_TOOL_PRODUCTS else
            '- 专家的意见由你吸收进原来的产出' + (f'（{prod} 照旧用原来的知识库工具交）' if prod else '')
            + '，本场景不用另写角色产物文件；不要在仓库或知识库目录里建 workspace/。'),
           f'- 要用户确认时照旧 kb_ask（stage 填 立项 / 技术方案 / 上线放量 / 确认点），recommend 由主导角色给出，末尾注明「建议来自：{role_cn(lead)}」'
           '（实际是谁的建议就写谁）；kb_ask 提示免打扰时按 recommend 执行，并用 kb_log 写明采用的建议和「建议来自：<角色>」。'
           '产品经理的选项里有扩大范围和不扩大两类时，recommend 选不扩大。',
           '- 开始开发、方案拍板、Bug 审核只能用户本人在网页上点：不要调用 kb approve，也不要替用户操作网页。']
    if cfg.get('只读'):
        out.append('- 这是只读会话：专家同样不能改代码或本机文件，只读代码、给意见。')
    return '\n'.join(out)


def push_rule(m, eng):
    """跟进（talk._push_prompt）第 1 步后补的「按类型派角色」；不挂角色团时空串。"""
    if not team_on(m, eng) or not scene_roles('跟进', _meta(m))[1]:
        return ''
    return ('\n   按类型派角色：只需回答的问题不派角色；缺陷请对应端工程师定位、测试专家补复现和影响面；'
            '需求扩展先请 product-manager 写变更说明（改什么、影响谁、延后什么），再用 kb_ask 确认。')


# ---------------------------------------------------------------- 统计：会话日志里的 Agent / Task 调用
def team_count(logf):
    """数主会话的 Agent / Task 调用 → {subagent_type: 次数}（子 agent 内部的调用不算）。"""
    try:
        fh = open(logf, encoding='utf-8', errors='ignore')
    except (OSError, TypeError):
        return {}
    with fh:
        return _count_lines(fh)


def team_count_text(text):
    """同 team_count，数的是前台会话（engine_run）的输出文本。"""
    return _count_lines((text or '').splitlines())


def _count_lines(lines):
    out = {}
    for line in lines:
        if '"assistant"' not in line[:40] or ('"Agent"' not in line and '"Task"' not in line):
            continue          # 同 devices._parse_act：工具返回、系统事件不解析
        try:
            j = json.loads(line)
        except ValueError:
            continue
        if not isinstance(j, dict) or j.get('parent_tool_use_id'):
            continue
        for c in (j.get('message') or {}).get('content') or []:
            if isinstance(c, dict) and c.get('type') == 'tool_use' and c.get('name') in ('Agent', 'Task'):
                r = str((c.get('input') or {}).get('subagent_type') or 'general-purpose')
                out[r] = out.get(r, 0) + 1
    return out


def roles_text(counts):
    """会话卡片：「👤 参与：产品经理 · 架构师（3 次）」；没派角色返回空串。"""
    if not counts:
        return ''
    xs = sorted(counts.items(), key=lambda kv: -kv[1])
    return f'👤 参与：{" · ".join(role_cn(k) for k, _ in xs)}（{sum(counts.values())} 次）'


def _cap_of(scene):
    t, _ = load_scenes(team_settings()['档位'])
    key, _ = _scene_key(scene or '', t.get('场景') or {})
    return int(((t.get('场景') or {}).get(key) or {}).get('上限') or 0)


def tally(rec, logf, m=None, d=None, scene='', cumulative=True):
    """会话结束：把这份日志里的角色调用记进会话记录 rec「角色」，累加到期 m「角色调用」（m 由调用方保存）。
    同一份日志只算一次（rec「角色日志」记着算过的），网页重启后 _reap 补收尾也不会重复累加。→ 本次新数到的 {角色: 次数}。"""
    if not logf or rec is None:
        return {}
    done = rec.setdefault('角色日志', [])
    if str(logf) in done:
        return {}
    done.append(str(logf))
    got = team_count(logf)
    if not got:
        if not rec.get('角色'):
            rec.pop('角色', None)
        return {}
    have = rec.setdefault('角色', {})
    for k, v in got.items():
        have[k] = have.get(k, 0) + v
    rec['参与'] = roles_text(have)               # 会话卡片直接显示
    n = sum(got.values())
    if m is not None:
        u = m.setdefault('角色调用', {})
        u['合计'] = int(u.get('合计') or 0) + n
        br = u.setdefault('按角色', {})
        for k, v in got.items():
            br[k] = br.get(k, 0) + v
        _check_limit(m, d)
    cap = _cap_of(scene or rec.get('场景', ''))
    used = sum(have.values()) if cumulative else n          # cumulative=False：一份日志就是一个会话（各端开发 / 审查每轮一份）
    if d is not None and cap and used > cap:
        log(d, '角色团', f'超限提醒：「{scene or rec.get("场景", "")}」会话调用了 {used} 次角色，场景上限 {cap}（{roles_text(got)}）')
    return got


def _check_limit(m, d):
    u = m.setdefault('角色调用', {})
    lim = team_settings()['每期上限']
    if int(u.get('合计') or 0) >= lim:
        if not u.get('超限已记'):
            u['超限已记'] = True
            if d is not None:
                log(d, '角色团', f'本期已用 {u["合计"]}/{lim}，之后不再派角色')
    else:
        u.pop('超限已记', None)          # 上限调大了：恢复可派，下次用完再记一次


def tally_dirs(rec, logf, dirs, scene=''):
    """Bug 分析 / 修复：按 Bug 归属的期累加（没有归属只记在会话记录里）。rec 由调用方保存。"""
    got = tally(rec, logf, None, None, scene)
    if not got:
        return got
    for d in dirs or []:
        try:
            m = load_meta(d)
        except (OSError, ValueError):
            continue
        u = m.setdefault('角色调用', {})
        u['合计'] = int(u.get('合计') or 0) + sum(got.values())
        br = u.setdefault('按角色', {})
        for k, v in got.items():
            br[k] = br.get(k, 0) + v
        _check_limit(m, d)
        save_meta(d, m)
        cap = _cap_of(scene)
        if cap and sum(rec.get('角色', {}).values()) > cap:
            log(d, '角色团', f'超限提醒：「{scene}」会话调用了 {sum(rec["角色"].values())} 次角色，场景上限 {cap}')
    return got


# ---------------------------------------------------------------- 只读会话：开始 / 结束对比 git 状态（REQ-008）
def wc_snapshot(paths):
    """本机 git 仓库的 git status --porcelain → {路径: 输出}（远程、非 git 的跳过）。"""
    import subprocess
    out = {}
    for p in paths or []:
        if not p or ':' in str(p).split('/')[0] or not (Path(p) / '.git').exists():
            continue
        try:
            out[str(p)] = subprocess.run(['git', 'status', '--porcelain'], cwd=p, capture_output=True, text=True, timeout=60).stdout
        except (OSError, subprocess.SubprocessError):
            pass
    return out


def wc_alarm(d, rec, label):
    """会话结束：和 rec「git前」对比，不一致就在期记录和通知里报警「只读会话改了文件：<列表>」。→ 改动文件列表。"""
    before = rec.pop('git前', None)
    if not before:
        return []
    after = wc_snapshot(list(before))
    changed = []
    for p, b in before.items():
        if p in after and after[p] != b:
            old = set(b.splitlines())
            changed += [f'{Path(p).name}/{ln[3:].strip()}' for ln in after[p].splitlines() if ln.strip() and ln not in old]
            changed += [f'{Path(p).name}/（恢复了）{ln[3:].strip()}' for ln in old if ln.strip() and ln not in set(after[p].splitlines())]
    if changed:
        msg = f'只读会话改了文件：{"、".join(changed[:20])}' + ('…' if len(changed) > 20 else '')
        rec['警告'] = msg
        if d is not None:
            log(d, '⚠ 只读会话', f'{label}：{msg}（git 分不出是谁改的：同时有别的会话在改这些仓库时，也可能是它们改的）')
        try:
            from kblib.ai.devsession import _notify
            _notify(f'知识库 · ⚠ {label}', msg)
        except Exception:       # noqa: BLE001
            pass
    return changed
