"""角色团：把角色套件（worker-agent）的专家以子 agent 身份挂进知识库后台会话。

  知识库继续管流程和拍板，专家只出意见：engines.engine_cmd 在 rules_text() 后面接 team_text(scene, …)，
  各会话入口只传 scene（管家 / 流程设计 / 需求 / 方案 / Bug分析·<端> / Bug修复·<端> / 开发 / 开发·<端> / 审查·<端> / 跟进 / 发布）。
  按引擎能力注入（team_mode）：Claude 用 Agent 子 agent（原样，命令行逐字不变）；Codex / Gemini / Hermes / 自定义引擎降级为「内联专家意见」
  （场景专家的说明拼进提示词，主会话按专家视角出意见、打 [kb-role:<角色>] 标记计数）；引擎「角色团方式」可强制 子agent / 内联 / 关。
  非 Claude 引擎可在设置里装适配（<专家目录>/<角色>/SKILL.md，见「其他引擎的适配层」）。
  全局关、期选「关」、引擎关、scene 为空、续跑、远程主机会话都返回空串（命令行和现在逐字一致）。

  设置.json「角色团」{开启, 档位: 精简 / 标准, 套件目录}；期信息「角色团」= 关（不存 = 跟随全局）、「角色调用」{合计, 按角色}（只统计，不设上限）。
  角色套件只协助、不设卡：没有每期次数上限；新装知识库默认关（设置.json 没有「角色团」时：本机已装套件 = 老用户保持开，没装 = 关）。
  套件可在设置页安装（调套件 scripts/install_helper.py --user）/ 移除 / 恢复。移除按 manifest 清单删，连同套件留下的 .bak、自动学习定时任务、
  项目级安装一并清掉，删前全部备份到 _system/cache/角色套件备份/；删完逐项自检，结果列「已删 / 仍残留」，并提示已开会话到结束前仍能调用。
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
DEFAULTS = {'开启': False, '档位': '精简'}       # 默认关；设置.json 没有「角色团」但本机已装套件的老用户保持开（见 team_settings）
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
    raw = load_settings().get('角色团')
    s = raw if isinstance(raw, dict) else {}
    out = dict(DEFAULTS)
    if raw is None and _manifest() is not None:
        out['开启'] = True          # 老用户：以前默认开、已装了套件，保持现状
    if isinstance(s.get('开启'), bool):
        out['开启'] = s['开启']
    if s.get('档位') in TIERS:
        out['档位'] = s['档位']
    return out


def _save_team(**kw):
    """写设置.json「角色团」：先把当前生效值写全（老用户的隐式「开」落成显式），再改传了的字段；其他字段（套件目录）原样保留。"""
    s = load_settings()
    cur = s.get('角色团') if isinstance(s.get('角色团'), dict) else {}
    cur.pop('每期上限', None)       # 旧版字段：已去掉
    s['角色团'] = {**cur, **team_settings(), **kw}
    save_settings(s)


def set_team(on=None, tier=None, limit=None):
    """网页「⚙ 设置 → 角色团」：只传要改的字段。limit 是旧版的每期上限，已去掉，传了也忽略。"""
    cur = team_settings()
    new = dict(cur)
    if on is not None:
        new['开启'] = bool(on)
    if tier not in (None, ''):
        if tier not in TIERS:
            raise KBError(f'档位只能是：{"、".join(TIERS)}')
        new['档位'] = tier
    raw = load_settings().get('角色团')
    if new == cur and isinstance(raw, dict) and '每期上限' not in raw:
        return '没有变化'
    _save_team(**new)
    return '角色团' + ('已关闭：后台会话不再派角色' if not new['开启'] else f'已开启（{new["档位"]}档）')


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


TEAM_MODES = ('自动', '子agent', '内联', '关')


def team_mode(eng):
    """引擎挂角色团的方式 → '子agent'（Agent 工具派专家子 agent，Claude 原样）/ '内联'（专家角色说明拼进提示词，主会话按专家视角出意见）/ ''（不挂）。
    引擎配置「角色团方式」：自动（默认）= Claude 子agent、其他引擎内联；子agent / 内联 / 关 = 强制。"""
    if not eng:
        return ''
    v = eng.get('角色团方式') or '自动'
    if v == '关':
        return ''
    if v in ('子agent', '内联'):
        return v
    return '子agent' if is_claude(eng) else '内联'


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


ROLE_DUTY = {'product-manager': '需求、PRD、验收标准、范围取舍', 'ux-designer': '交互、视觉、设计稿走查', 'architect': '技术方案、选型、Code Review',
             'frontend-engineer': 'Web / H5 / 管理后台实现', 'client-engineer': 'Android / iOS / 鸿蒙 / 小程序实现',
             'backend-engineer': '服务端、数据库、数据链路', 'qa-expert': '测试策略、用例、复现与影响面', 'sre-security': '发布、灰度、监控、安全',
             'data-growth': '埋点、数据分析、运营增长', 'ai-product-manager': 'AI 功能该不该做、怎么衡量', 'ai-app-engineer': '大模型应用、RAG、Agent',
             'model-algorithm-engineer': '推荐 / 搜索 / 训练微调', 'ai-infra-mlops': '推理部署、MLOps', 'ai-eval-compliance': 'AI 评测与安全合规',
             'orchestrator': '总控：拆阶段、选角、调度（/orchestrate 用）', 'tech-scout': '侦察技术变化、更新技能（/skills-refresh 用）'}


def _agent_desc(path):
    """agent .md 头部 description 里「什么时候用我：」那一句（没有就第一句）；读不到返回空串。"""
    try:
        head = read_text(path).split('\n---', 1)[0]
    except OSError:
        return ''
    mm = re.search(r'^description:\s*(.+)$', head, re.M)
    if not mm:
        return ''
    d = mm.group(1).strip().strip('"\'')
    return (d.split('什么时候用我：', 1)[1] if '什么时候用我：' in d else d).split('。')[0]


def _scene_map(tier):
    """{agent: [场景（主导）…]}：按这一档的场景表反查，@端 算各端工程师。"""
    t, _ = load_scenes(tier)
    ends = set((t.get('端角色') or _BUILTIN['端角色']).values())
    where = {}
    for name, c in (t.get('场景') or {}).items():
        pick = c.get(tier) or {}
        for r in [pick.get('主导')] + list(pick.get('参与') or []):
            for x in (sorted(ends) if r == '@端' else [r] if r else []):
                lst = where.setdefault(x, [])
                if name not in [y.split('（')[0] for y in lst]:
                    lst.append(name + ('（主导）' if r == pick.get('主导') else ''))
    return where


def agent_list(tier=None):
    """设置卡片：套件全部 agent → [{名称, 中文名, 负责, 说明, 已安装, 场景, 其他档场景}]。
    场景 = 当前档位会被派的场景；其他档场景 = 换到另一档才会派的。"""
    tier = tier or team_settings()['档位']
    other = next(x for x in TIERS if x != tier)
    cur, alt = _scene_map(tier), _scene_map(other)
    home = (_manifest() or {}).get('home') or _suite_home()
    have = set(installed_agents())
    out = []
    for a in ALL_AGENTS:
        f = AGENTS_DIR / f'{a}.md' if a in have else (Path(home) / 'agents' / f'{a}.md' if home else None)
        names = [x.split('（')[0] for x in cur.get(a, [])]
        out.append({'名称': a, '中文名': role_cn(a), '负责': ROLE_DUTY.get(a, ''), '说明': _agent_desc(f) if f else '',
                    '已安装': a in have, '场景': cur.get(a, []), '其他档': other,
                    '其他档场景': [x for x in alt.get(a, []) if x.split('（')[0] not in names]})
    return out


def team_status():
    """设置卡片用：开关 / 档位 + 安装状态 + 全部 agent + 备份（契约 TeamSettings）。"""
    from kblib.ai.engines import get_engine        # 同层；engines 也要 import 本模块，放到用时
    st = team_settings()
    man = _manifest()
    have = installed_agents()
    try:
        dft = bool(team_mode(get_engine()))
    except KBError:
        dft = False
    _, src = load_scenes()
    diff = scene_diff()
    home = _suite_home()
    st['状态'] = {'已安装': len(have), '应有': len(ALL_AGENTS), '缺': [a for a in ALL_AGENTS if a not in have],
                 '套件版本': (man or {}).get('suite_version', '') if man else '', '套件已装': man is not None,
                 'kb接入': _kb_linked(), '场景表': src, '场景表差异': diff or [], '默认引擎支持': dft, '安装命令': INSTALL_HINT,
                 '套件目录': home or '', '套件目录可用': bool(home and _helper(home).is_file()), '备份': _backups()[:5],
                 '项目级安装': _projects(home), '残留': [] if man is not None else suite_residue() + adapter_residue(),
                 '引擎': adapter_status()}
    st['角色'] = agent_list(st['档位'])
    return st


# ---------------------------------------------------------------- 安装 / 移除 / 恢复（只动套件 manifest 清单里的文件）
SUITE_MARK = 'role-agents-suite'       # 套件 settings_merge.py 给自己的 hook 打的标记


def _base():
    return AGENTS_DIR.parent           # ~/.claude（测试里换成临时目录）


def _backup_root():
    from kblib.core.common import SYS
    return SYS / 'cache' / '角色套件备份'


def _helper(home):
    return Path(home).expanduser() / 'scripts' / 'install_helper.py'


def _suite_home():
    """套件仓库目录：设置里记的 > manifest 的 home > 最近备份 manifest 的 home > ~/Documents/worker-agent、~/worker-agent。"""
    cands = [(load_settings().get('角色团') or {}).get('套件目录') if isinstance(load_settings().get('角色团'), dict) else None,
             (_manifest() or {}).get('home')]
    for b in _backups()[:1]:
        try:
            cands.append(json.loads(read_text(_backup_root() / b['名称'] / 'manifest.json')).get('home'))
        except (OSError, ValueError):
            pass
    cands += [str(Path.home() / 'Documents' / 'worker-agent'), str(Path.home() / 'worker-agent')]
    for c in cands:
        if c and _helper(c).is_file():
            return str(Path(c).expanduser())
    return next((str(c) for c in cands if c), '')


def _backups():
    root = _backup_root()
    if not root.is_dir():
        return []
    out = []
    for d in sorted(root.iterdir(), reverse=True):
        if (d / 'manifest.json').is_file():
            try:
                info = json.loads(read_text(d / '备份说明.json'))
            except (OSError, ValueError):
                info = {}
            out.append({'名称': d.name, '文件数': info.get('文件数', 0), '套件版本': info.get('套件版本', ''), '已恢复': bool(info.get('已恢复'))})
    return out


def _settings_json():
    p = _base() / 'settings.json'
    try:
        return json.loads(read_text(p) or '{}'), p
    except (OSError, ValueError):
        return None, p


def _split_hooks(settings):
    """settings.json 的 hooks 拆成 (去掉套件标记 hook 后的 hooks, 套件的 hooks {事件: [组]})。"""
    keep, ours = {}, {}
    for ev, groups in ((settings or {}).get('hooks') or {}).items():
        for g in groups if isinstance(groups, list) else []:
            hs = (g or {}).get('hooks') or []
            mine = [h for h in hs if SUITE_MARK in str((h or {}).get('command', ''))]
            rest = [h for h in hs if h not in mine]
            if mine:
                ours.setdefault(ev, []).append(dict(g, hooks=mine))
            if rest:
                keep.setdefault(ev, []).append(dict(g, hooks=rest))
    return keep, ours


def team_install(home='', engine=''):
    """「安装 / 更新」：前台跑 python3 <套件>/scripts/install_helper.py --user（--update 当已装）。装好开关不动（新用户仍是关）。
    已装适配的其他引擎跟着重新生成。engine = 非 Claude 引擎 id：给这个引擎装 / 更新适配（套件还没装时先装套件，~/.claude 是唯一真源）。"""
    if engine and engine != 'claude':
        eng = next((e for e in _engines() if e['id'] == engine), None)
        if not eng:
            raise KBError(f'没有这个 AI 引擎：{engine}')
        if not is_claude(eng):
            first = ''
            if _manifest() is None:
                first = team_install(home).split('\n')[0] + '\n'
            return first + adapt_engine(engine)
    import subprocess
    home = str(Path((home or '').strip() or _suite_home()).expanduser()) if (home or '').strip() or _suite_home() else ''
    if not home or not _helper(home).is_file():
        raise KBError(f'找不到角色套件：{home or "（没填套件目录）"} 下没有 scripts/install_helper.py。填 worker-agent 仓库目录后再装')
    first = (load_settings().get('角色团') is None) and _manifest() is None
    args = [sys.executable, str(_helper(home)), '--user'] + (['--update'] if _manifest() is not None else [])
    try:
        r = subprocess.run(args, cwd=home, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.SubprocessError) as e:
        raise KBError(f'安装没跑起来：{e}')
    if first:
        _save_team(开启=False)        # 新装：落成显式「关」，不因为装了套件就被当成老用户自动打开
    s = load_settings()
    cur = s.get('角色团') if isinstance(s.get('角色团'), dict) else {}
    s['角色团'] = {**cur, **team_settings(), '套件目录': home}
    save_settings(s)
    tail = '\n'.join(((r.stdout or '') + (r.stderr or '')).strip().splitlines()[-8:])
    if r.returncode != 0:
        raise KBError(f'安装失败（退出码 {r.returncode}）：{tail}')
    ad = _refresh_adapters() if _manifest() is not None else []
    return (f'角色套件已安装（{len(installed_agents())}/{len(ALL_AGENTS)} 个 agent）' + ('；角色团开关没动，要用时在这里打开' if not team_settings()['开启'] else '')
            + (f'；其他引擎适配：{"；".join(ad)}' if ad else '') + f'\n{tail}')


SCHED_LABEL = 'com.worker-agent.skills-refresh'       # 套件 auto_refresh.py --install-schedule 装的 launchd 任务
CRON_MARK = '# worker-agent skills-refresh'            # 同上，非 macOS 装进 crontab 的行尾标记
BUILD_MARK = 'scripts/build.py 生成'                    # 套件 build.py 生成的 agent 文件头注释
_BAK = re.compile(r'\.bak-\d{8,}$')                    # 套件 install_helper / settings_merge 留下的 <文件>.bak-<时间>


def _plist():
    return Path.home() / 'Library' / 'LaunchAgents' / f'{SCHED_LABEL}.plist'


def _launchctl(*args):
    import subprocess
    if sys.platform != 'darwin':          # LaunchAgent 是 Mac 专用：Linux 执行机 / 服务器上跳过（REQ-011）
        return
    try:
        subprocess.run(['launchctl', *args], capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        pass


def _crontab_read():
    import subprocess
    try:
        r = subprocess.run(['crontab', '-l'], capture_output=True, text=True, timeout=30)
        return r.stdout if r.returncode == 0 else ''
    except (OSError, subprocess.SubprocessError):
        return ''


def _crontab_write(text):
    import subprocess
    try:
        subprocess.run(['crontab', '-'], input=text, text=True, capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        pass


def _cron_lines():
    return [ln for ln in _crontab_read().splitlines() if CRON_MARK in ln]


def _schedule_left():
    out = []
    if _plist().is_file():
        out.append(f'自动学习定时任务 {_plist().name}（launchd）')
    if _cron_lines():
        out.append('自动学习定时任务（crontab）')
    return out


def _stale_baks(base, rels=()):
    """base 下套件留下的 .bak-<时间>：settings.json.bak-* 里带套件 hook 标记的；agents/ skills/ 下套件文件（清单里的或套件同名 agent）的 .bak-*。
    用户自己的 settings.json.bak、.bak-figma 这类不带时间戳 / 不含标记的不碰。返回相对 base 的路径。"""
    out = []
    if not base.is_dir():
        return out
    for f in base.glob('settings.json.bak-*'):
        if f.is_file() and _BAK.search(f.name):
            try:
                if SUITE_MARK in read_text(f):
                    out.append(f.name)
            except OSError:
                pass
    names = set(rels) | {f'agents/{a}.md' for a in ALL_AGENTS}
    for sub in ('agents', 'skills'):
        d = base / sub
        for f in d.rglob('*.bak-*') if d.is_dir() else []:
            if f.is_file() and _BAK.search(f.name):
                rel = str(f.relative_to(base))
                if _BAK.sub('', rel) in names:
                    out.append(rel)
    return sorted(out)


def _stray_agents(base, rels=()):
    """清单外、但由套件 build.py 生成的同名角色 agent（老版本装的、清单丢了的），一并清掉。"""
    out = []
    for a in ALL_AGENTS:
        r = f'agents/{a}.md'
        f = base / r
        if r in rels or not f.is_file():
            continue
        try:
            if BUILD_MARK in read_text(f):
                out.append(r)
        except OSError:
            pass
    return out


def _projects(home=''):
    """套件登记过的项目级安装（<套件目录>/state/projects.json），只列 <项目>/.claude/role-agents/manifest.json 还在的。"""
    home = home or (_manifest() or {}).get('home') or _suite_home()
    if not home:
        return []
    try:
        st = json.loads(read_text(Path(home).expanduser() / 'state' / 'projects.json'))
    except (OSError, ValueError):
        return []
    out = []
    for p in st.get('projects') or [] if isinstance(st, dict) else []:
        if isinstance(p, str) and p and (Path(p) / '.claude' / 'role-agents' / 'manifest.json').is_file():
            out.append(p)
    return out


def _mv(src, dst):
    import shutil
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))


def _remove_at(base, dst):
    """把 base（~/.claude 或 <项目>/.claude）下的套件整套删掉，删前全部备份到 dst：
    files/<清单文件>、manifest.json、hooks.json、settings.json（改前）、bak/<残留 .bak>。返回 (文件数, hook 组数, .bak 数, 清单相对路径)。"""
    import shutil
    mp = base / 'role-agents' / 'manifest.json'
    try:
        man = json.loads(read_text(mp))
    except (OSError, ValueError):
        man = None
    rels = [r for r in ((man or {}).get('files') or {}) if isinstance(r, str)]
    bad = [r for r in rels if r.startswith('/') or '..' in Path(r).parts]
    if bad:
        raise KBError(f'安装清单里有不安全的路径，不删：{"、".join(bad[:3])}')
    rels += _stray_agents(base, rels)
    dst.mkdir(parents=True, exist_ok=True)
    files = []
    for r in rels:
        f = base / r
        if f.is_file():
            (dst / 'files' / r).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dst / 'files' / r)
            files.append(r)
    if man is not None:
        shutil.copy2(mp, dst / 'manifest.json')
    p = base / 'settings.json'
    try:
        settings = json.loads(read_text(p) or '{}') if p.is_file() else None
    except (OSError, ValueError):
        settings = None
    keep, ours = _split_hooks(settings)
    (dst / 'hooks.json').write_text(json.dumps(ours, ensure_ascii=False, indent=2), 'utf-8')
    # 备份齐了再删
    for r in files:
        f = base / r
        f.unlink()
        d = f.parent
        while d != base and base in d.parents and d.is_dir() and not any(d.iterdir()):
            d.rmdir()
            d = d.parent
    if mp.is_file():
        mp.unlink()
    sd = base / 'role-agents'
    if sd.is_dir() and not any(sd.iterdir()):
        sd.rmdir()
    if ours and settings is not None:
        shutil.copy2(p, dst / 'settings.json')
        settings = dict(settings)
        if keep:
            settings['hooks'] = keep
        else:
            settings.pop('hooks', None)
        p.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + '\n', 'utf-8')
    baks = _stale_baks(base, rels)
    for r in baks:                                   # 移进备份（Claude 不加载，但属于残留），恢复时放回
        _mv(base / r, dst / 'bak' / r)
    return len(files), sum(map(len, ours.values())), len(baks), rels


def _remove_schedule(dst):
    out = []
    pl = _plist()
    if pl.is_file():
        _launchctl('unload', str(pl))
        _mv(pl, dst / 'schedule' / pl.name)
        out.append('launchd')
    lines = _cron_lines()
    if lines:
        (dst / 'schedule').mkdir(parents=True, exist_ok=True)
        (dst / 'schedule' / 'crontab.txt').write_text('\n'.join(lines) + '\n', 'utf-8')
        _crontab_write('\n'.join(ln for ln in _crontab_read().splitlines() if CRON_MARK not in ln) + '\n')
        out.append('crontab')
    return out


def suite_residue(base=None, rels=(), projects=None):
    """自检：Claude 还能不能加载到套件的东西。返回 [残留说明]，空 = 干净。"""
    base = base or _base()
    left = []
    ag = sorted({a for a in ALL_AGENTS if (base / 'agents' / f'{a}.md').is_file()}
                | {Path(r).stem for r in rels if r.startswith('agents/') and (base / r).is_file()})
    if ag:
        left.append(f'~/.claude/agents 里还有角色 agent：{"、".join(ag)}（没删掉，或不是套件装的所以没动；不要了请手动删，或在 ~/.claude/agents 里改名）')
    sk = sorted({r.split('/')[1] for r in rels if r.startswith('skills/') and r.count('/') >= 2 and (base / r).is_file()})
    if sk:
        left.append(f'~/.claude/skills 里还有套件 skill：{"、".join(sk[:8])}{"…" if len(sk) > 8 else ""}')
    try:
        settings = json.loads(read_text(base / 'settings.json') or '{}') if (base / 'settings.json').is_file() else None
    except (OSError, ValueError):
        settings = None
    _, ours = _split_hooks(settings)
    if ours:
        left.append(f'settings.json 里还有 {sum(map(len, ours.values()))} 组套件 hook')
    if (base / 'role-agents').exists():
        left.append('~/.claude/role-agents/ 还在')
    bk = _stale_baks(base, rels)
    if bk:
        left.append(f'套件留下的备份文件：{"、".join(bk[:4])}{"…" if len(bk) > 4 else ""}')
    left += _schedule_left()
    for p in (projects if projects is not None else _projects()):
        left.append(f'项目级安装：{p}/.claude')
    return left


def _running_sessions():
    """正在跑的知识库后台会话（管家「正在跑的 AI」同一份），拿不到就空。"""
    try:
        from kblib.steward import brief
        return [f'{x.get("角色", "")} · {x.get("对象", "")}（{x.get("状态", "")}）' for x in brief._activities() if x.get('状态') != '要你看']
    except Exception:
        return []


def _has_anything():
    return _manifest() is not None or bool(adapter_residue()) or bool(_stray_agents(_base())) or bool(_stale_baks(_base())) or bool(_schedule_left()) or bool(_projects())


def team_remove(projects=True, engine=''):
    """「移除」：把套件在本机的东西清干净，并自检。
      1. 按 manifest 清单删 ~/.claude 下的套件文件（agents / skills / role-agents），清单外由套件 build.py 生成的同名角色 agent 一并删；
         settings.json 里带套件标记的 hook 删掉；用户自己的 agent / skill / hook 不碰。
      2. 套件留下的 settings.json.bak-<时间>（含套件 hook）、agents / skills 下套件文件的 .bak-<时间> 移进备份。
      3. 自动学习定时任务（launchd com.worker-agent.skills-refresh / crontab「# worker-agent skills-refresh」）卸掉、进备份。
      4. projects=True 时，套件 state/projects.json 登记的项目级安装（<项目>/.claude）同样处理。
      5. 逐项自检，结果列「已删 / 仍残留」；提示已经开着的会话到结束前仍能调用，并列出在跑的知识库后台会话。
      6. 其他引擎的适配（<专家目录>/<角色>/SKILL.md）按各自清单一并删，备份到 角色套件备份/<引擎 id>/<时间>/。
    全部先备份到 _system/cache/角色套件备份/<时间>/，可一键恢复（适配不恢复，要用时重新装适配）。移除后角色团关。
    engine = 非 Claude 引擎 id：只移除这个引擎的适配，~/.claude 的套件和开关不动。"""
    import shutil
    from datetime import datetime
    if engine and engine != 'claude':
        return remove_adapter(engine)
    if not _has_anything():
        raise KBError('没装角色套件（~/.claude/role-agents/manifest.json 不存在），也没有残留，没什么可移除的')
    man = _manifest() or {}
    home = man.get('home') or _suite_home()
    projs = _projects(home)
    base = _base().resolve()
    dst = _backup_root() / datetime.now().strftime('%Y%m%d-%H%M%S')
    n = 1
    while dst.exists():
        n += 1
        dst = _backup_root() / (datetime.now().strftime('%Y%m%d-%H%M%S') + f'-{n}')
    nf, nh, nb, rels = _remove_at(base, dst)
    sched = _remove_schedule(dst)
    adapted = _remove_all_adapters()
    done_p, fail_p = [], []
    if projects:
        for i, p in enumerate(projs):
            try:
                pf, ph, pb, _ = _remove_at(Path(p) / '.claude', dst / 'projects' / str(i))
                done_p.append({'目录': p, '子目录': str(i), '文件数': pf})
            except (KBError, OSError) as e:
                fail_p.append(f'{p}（{e}）')
    if not (dst / 'manifest.json').is_file():
        (dst / 'manifest.json').write_text(json.dumps({'home': home, 'files': {}}, ensure_ascii=False), 'utf-8')   # 只清残留时也能列进备份
    (dst / '备份说明.json').write_text(json.dumps({'文件数': nf, '套件版本': man.get('suite_version', ''), 'hook组数': nh, '套件目录': home,
                                                 '残留备份数': nb, '定时任务': sched, '项目': done_p}, ensure_ascii=False, indent=2), 'utf-8')
    _save_team(开启=False, 套件目录=home)
    done = [f'{nf} 个套件文件（agent / skill / role-agents）'] if nf else []
    done += [f'settings.json 里 {nh} 组套件 hook'] if nh else []
    done += [f'{nb} 个套件留下的 .bak 备份文件（移进备份）'] if nb else []
    done += [f'自动学习定时任务（{"、".join(sched)}）'] if sched else []
    done += [f'项目级安装 {x["目录"]}/.claude（{x["文件数"]} 个文件）' for x in done_p]
    done += adapted
    left = suite_residue(base, rels, projects=[p for p in _projects(home)])
    left += adapter_residue()
    left += [f'项目级安装没删掉：{x}' for x in fail_p]
    if not projects and projs:
        left.append('（这次没选一并移除项目级安装）')
    run = _running_sessions()
    lines = [f'已移除角色套件，角色团已关。你自己的 agent / skill / hook 没动。备份在 {dst.name}，可点「恢复」放回。',
             '已删：' + ('；'.join(done) if done else '没有要删的'),
             '仍残留：' + ('；'.join(left) if left else '无（自检通过：~/.claude 里没有角色 agent、套件 skill、套件 hook、自动学习定时任务）'),
             '提示：已经开着的 Claude 会话在启动时就加载了 agent 列表，到会话结束前仍能调用角色 agent；要彻底断开，结束或重开这些会话。'
             + ('当前在跑的知识库后台会话：' + '；'.join(run) if run else '当前没有在跑的知识库后台会话。')]
    return '\n'.join(lines)


def _restore_at(base, src):
    """把 _remove_at 备份的一份放回 base：文件、manifest、.bak、hook（合并，不覆盖之后的其他改动）。返回 (文件数, hook 组数)。"""
    import shutil
    n = 0
    for sub in ('files', 'bak'):
        for f in (src / sub).rglob('*') if (src / sub).is_dir() else []:
            if f.is_file():
                to = base / f.relative_to(src / sub)
                to.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, to)
                n += sub == 'files'
    try:
        man = json.loads(read_text(src / 'manifest.json'))
    except (OSError, ValueError):
        man = None
    if man and man.get('files'):
        mp = base / 'role-agents' / 'manifest.json'
        mp.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / 'manifest.json', mp)
    try:
        ours = json.loads(read_text(src / 'hooks.json'))
    except (OSError, ValueError):
        ours = {}
    if ours:
        sp = base / 'settings.json'
        try:
            settings = json.loads(read_text(sp) or '{}') if sp.is_file() else {}
        except (OSError, ValueError):
            settings = {}
        settings = settings if isinstance(settings, dict) else {}
        keep, _ = _split_hooks(settings)           # 去掉现有的套件 hook 再加，重复恢复不会叠加
        for ev, groups in ours.items():
            keep.setdefault(ev, []).extend(groups)
        settings['hooks'] = keep
        sp.parent.mkdir(parents=True, exist_ok=True)
        sp.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + '\n', 'utf-8')
    return n, sum(map(len, ours.values()))


def team_restore(name=''):
    """「恢复」：把某次（默认最近一次）移除前的备份放回：~/.claude 的文件和 hook、.bak、自动学习定时任务、项目级安装。开关不动。"""
    import shutil
    bs = _backups()
    b = next((x for x in bs if x['名称'] == name), None) if name else (bs[0] if bs else None)
    if not b:
        raise KBError('没有可恢复的角色套件备份')
    src = _backup_root() / b['名称']
    info_f = src / '备份说明.json'
    try:
        info = json.loads(read_text(info_f))
    except (OSError, ValueError):
        info = {}
    n, nh = _restore_at(_base(), src)
    extra = []
    sd = src / 'schedule'
    pl = sd / f'{SCHED_LABEL}.plist'
    if pl.is_file() and sys.platform == 'darwin':
        _plist().parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pl, _plist())
        _launchctl('load', str(_plist()))
        extra.append('自动学习定时任务（launchd）')
    if (sd / 'crontab.txt').is_file():
        cur = [ln for ln in _crontab_read().splitlines() if CRON_MARK not in ln]
        _crontab_write('\n'.join(cur + read_text(sd / 'crontab.txt').splitlines()) + '\n')
        extra.append('自动学习定时任务（crontab）')
    for x in info.get('项目') or []:
        ps = src / 'projects' / str(x.get('子目录', ''))
        if x.get('目录') and ps.is_dir():
            _restore_at(Path(x['目录']) / '.claude', ps)
            extra.append(f'项目级安装 {x["目录"]}')
    info['已恢复'] = True
    info_f.write_text(json.dumps(info, ensure_ascii=False, indent=2), 'utf-8')
    return (f'已从备份 {b["名称"]} 恢复角色套件（{n} 个文件' + (f'、{nh} 组 hook' if nh else '') + '）'
            + (f'，另外放回：{"、".join(extra)}' if extra else '') + '。角色团开关没动，要用时在这里打开')


# ---------------------------------------------------------------- 其他引擎的适配层（Codex / Gemini / Hermes / 自定义）
# 唯一真源是知识库装进 ~/.claude/agents 的角色定义（套件 build.py 生成）；其他引擎按「专家目录」生成 Agent Skills 格式的
# <专家目录>/<角色>/SKILL.md（去掉 Claude 专用的 tools / model），清单写 <专家目录>/.kb-role-agents.json，
# 移除时备份到 _system/cache/角色套件备份/<引擎 id>/<时间>/。设置.json「角色团」「适配」{引擎 id: 专家目录} 记着装到了哪，引擎配置改了也能删干净。
# 内联模式不依赖适配：提示词里给的完整角色说明路径优先用适配目录里的 SKILL.md，没有就用 ~/.claude/agents/<角色>.md。
ADAPT_MANIFEST = '.kb-role-agents.json'
ADAPT_MARK = '由知识库从角色套件生成'
DEFAULT_ROLE_DIRS = {'codex': '~/.codex/skills', 'gemini': '~/.gemini/skills', 'hermes': '~/.hermes/skills'}
ADAPT_ROLES = EXPERTS                      # orchestrator / tech-scout 依赖 Claude 的 Agent / 定时任务，不生成


def _engines():
    from kblib.ai.engines import engines         # 同层；engines 也要 import 本模块，放到用时
    return engines()


def engine_role_dir(eng):
    """引擎的专家目录（展开 ~）；Claude（原生子 agent）和没填、按命令名也推不出的 → ''。"""
    if not eng or is_claude(eng):
        return ''
    d = (eng.get('专家目录') or '').strip() or DEFAULT_ROLE_DIRS.get(Path(eng.get('命令') or '').name, '')
    return str(Path(d).expanduser()) if d else ''


def _adapt_records():
    raw = load_settings().get('角色团')
    return dict((raw or {}).get('适配') or {}) if isinstance(raw, dict) else {}


def _save_adapt_records(rec):
    s = load_settings()
    cur = s.get('角色团') if isinstance(s.get('角色团'), dict) else {}
    cur = {**cur, **team_settings()}
    if rec:
        cur['适配'] = rec
    else:
        cur.pop('适配', None)
    s['角色团'] = cur
    save_settings(s)


def _adapt_manifest(d):
    try:
        man = json.loads(read_text(Path(d) / ADAPT_MANIFEST))
    except (OSError, ValueError, TypeError):
        return None
    return man if isinstance(man, dict) else None


def _adapt_dir_of(eng):
    """引擎当前装着适配的目录：记录里的优先（引擎配置后来改了目录也认得），否则按配置推的目录里有清单才算。"""
    if not eng:
        return ''
    d = _adapt_records().get(eng.get('id') or '')
    if d and _adapt_manifest(d) is not None:
        return d
    d = engine_role_dir(eng)
    return d if d and _adapt_manifest(d) is not None else ''


def role_file(slug, eng=None):
    """内联提示词里给的完整角色说明：引擎适配目录里的 SKILL.md > ~/.claude/agents/<角色>.md。"""
    d = _adapt_dir_of(eng)
    if d and f'{slug}/SKILL.md' in ((_adapt_manifest(d) or {}).get('files') or {}) and (Path(d) / slug / 'SKILL.md').is_file():
        return str(Path(d) / slug / 'SKILL.md')
    return str(AGENTS_DIR / f'{slug}.md')


def _sha(text):
    import hashlib
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:12]


def _skill_text(slug, src):
    """~/.claude/agents/<角色>.md → Agent Skills 格式的 SKILL.md：frontmatter 只留 name / description（≤500 字），正文原样，前面加一段用法。"""
    text = read_text(src)
    head, body = '', text
    if text.startswith('---'):
        parts = text.split('\n---', 1)
        if len(parts) == 2:
            head, body = parts[0], parts[1].lstrip('-').lstrip('\n')
    mm = re.search(r'^description:\s*(.+)$', head, re.M)
    desc = (mm.group(1).strip().strip('"\'') if mm else '') or f'{role_cn(slug)}：{ROLE_DUTY.get(slug, "")}'
    desc = desc.replace('"', '“')[:500]
    return (f'---\nname: {slug}\ndescription: "{desc}"\n---\n'
            f'<!-- {ADAPT_MARK}（{src}），请勿手改：在知识库「⚙ 设置 → 🧑‍🤝‍🧑 角色团」更新 / 移除 -->\n\n'
            f'> 这是角色套件里「{role_cn(slug)}」的角色说明。被要求以这个专家的视角出意见时，按下面的身份、职责和方法工作；'
            '只给意见、补能力，不设卡、不替用户做决定。正文里提到的 Agent 子 agent、Claude 专用工具在本引擎没有的，按同样的思路用本引擎的工具完成。\n\n'
            + body)


def adapt_engine(eid):
    """给一个非 Claude 引擎生成 / 更新适配：<专家目录>/<角色>/SKILL.md。用户自己的同名 skill 不覆盖；装过的被手改了先备份再覆盖。"""
    import shutil
    from datetime import datetime
    eng = next((e for e in _engines() if e['id'] == eid), None)
    if not eng:
        raise KBError(f'没有这个 AI 引擎：{eid}')
    if is_claude(eng):
        return f'{eng["名称"]} 用原生子 agent，套件装在 ~/.claude，不用另装适配'
    d = engine_role_dir(eng)
    if not d:
        raise KBError(f'{eng["名称"]} 没有专家目录：在「⚙ 设置 · AI 引擎」编辑它，填「角色套件专家目录」后再装（内联专家意见不装适配也能用）')
    if _manifest() is None:
        raise KBError('还没装角色套件：先装套件（~/.claude 是角色定义的唯一真源），再给其他引擎装适配')
    old = _adapt_records().get(eid)
    if old and old != d and _adapt_manifest(old) is not None:
        remove_adapter(eid, quiet=True)                 # 专家目录换了：旧目录里的先清掉
    base = Path(d)
    man = _adapt_manifest(base) or {}
    had = man.get('files') or {}
    files, skipped, backed = {}, [], []
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    for a in ADAPT_ROLES:
        src = AGENTS_DIR / f'{a}.md'
        if not src.is_file():
            continue
        rel = f'{a}/SKILL.md'
        f = base / rel
        txt = _skill_text(a, src)
        if f.is_file() and rel not in had:
            skipped.append(a)                            # 用户自己的同名 skill：不碰
            continue
        if f.is_file():
            cur = read_text(f)
            if _sha(cur) != (had.get(rel) or {}).get('sha') and cur != txt:
                bk = _backup_root() / eid / stamp / 'files' / rel
                bk.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, bk)
                backed.append(a)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(txt, 'utf-8')
        files[rel] = {'sha': _sha(txt)}
    for rel in set(had) - set(files):                    # 上一版有、这一版没有的
        f = base / rel
        if f.is_file() and ADAPT_MARK in read_text(f):
            f.unlink()
            if f.parent.is_dir() and not any(f.parent.iterdir()):
                f.parent.rmdir()
    base.mkdir(parents=True, exist_ok=True)
    (base / ADAPT_MANIFEST).write_text(json.dumps({'engine': eid, 'source': str(AGENTS_DIR), 'suite_version': (_manifest() or {}).get('suite_version', ''),
                                                    'generated_at': stamp, 'files': files}, ensure_ascii=False, indent=2), 'utf-8')
    rec = _adapt_records()
    rec[eid] = d
    _save_adapt_records(rec)
    msg = f'已给 {eng["名称"]} 装好角色套件适配：{len(files)} 个专家 → {d}/<角色>/SKILL.md'
    if skipped:
        msg += f'；同名 skill 是你自己的，没覆盖：{"、".join(skipped)}'
    if backed:
        msg += f'；手改过的先备份到 角色套件备份/{eid}/{stamp}：{"、".join(backed)}'
    return msg


def remove_adapter(eid, quiet=False):
    """按清单删一个引擎的适配（只删带生成标记的），删前备份到 角色套件备份/<引擎 id>/<时间>/。"""
    import shutil
    from datetime import datetime
    rec = _adapt_records()
    eng = next((e for e in _engines() if e['id'] == eid), None)
    d = rec.get(eid) or (engine_role_dir(eng) if eng else '')
    man = _adapt_manifest(d) if d else None
    if man is None:
        rec.pop(eid, None)
        _save_adapt_records(rec)
        if quiet:
            return ''
        raise KBError(f'{(eng or {}).get("名称", eid)} 没装角色套件适配')
    base = Path(d)
    dst = _backup_root() / eid / datetime.now().strftime('%Y%m%d-%H%M%S')
    n = 0
    for rel in man.get('files') or {}:
        if rel.startswith('/') or '..' in Path(rel).parts:
            continue
        f = base / rel
        if f.is_file() and ADAPT_MARK in read_text(f):
            (dst / 'files' / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dst / 'files' / rel)
            f.unlink()
            n += 1
            if f.parent.is_dir() and not any(f.parent.iterdir()):
                f.parent.rmdir()
    dst.mkdir(parents=True, exist_ok=True)
    shutil.move(str(base / ADAPT_MANIFEST), str(dst / ADAPT_MANIFEST))
    rec.pop(eid, None)
    _save_adapt_records(rec)
    return f'已移除 {(eng or {}).get("名称", eid)} 的角色套件适配（{n} 个文件，备份在 角色套件备份/{eid}/{dst.name}）'


def adapter_status():
    """设置卡片：各引擎 → [{id, 名称, 方式, 专家目录, 适配已装, 适配数, 应有}]。"""
    out = []
    have = [a for a in ADAPT_ROLES if (AGENTS_DIR / f'{a}.md').is_file()]
    for e in _engines():
        d = _adapt_dir_of(e)
        man = _adapt_manifest(d) if d else None
        out.append({'id': e['id'], '名称': e['名称'], '方式': team_mode(e) or '关', '原生': is_claude(e),
                    '专家目录': d or engine_role_dir(e), '适配已装': man is not None, '适配数': len((man or {}).get('files') or {}),
                    '应有': len(have)})
    return out


def adapter_residue():
    """适配残留：记录里或各引擎专家目录里还有适配清单的 → ['<引擎>：<目录>'…]。"""
    seen, out = set(), []
    names = {e['id']: e['名称'] for e in _engines()}
    cands = list(_adapt_records().items()) + [(e['id'], engine_role_dir(e)) for e in _engines()]
    for eid, d in cands:
        if d and d not in seen and _adapt_manifest(d) is not None:
            seen.add(d)
            out.append(f'{names.get(eid, eid)} 的角色套件适配：{d}')
    return out


def _refresh_adapters():
    """套件装好 / 更新后，已装适配的引擎跟着重新生成。→ [结果]"""
    out = []
    for eid in list(_adapt_records()):
        try:
            out.append(adapt_engine(eid))
        except KBError as e:
            out.append(f'{eid} 适配没更新：{e}')
    return out


def _remove_all_adapters():
    out = []
    for eid in {**_adapt_records(), **{e['id']: '' for e in _engines() if _adapt_dir_of(e)}}:
        try:
            m = remove_adapter(eid, quiet=True)
            if m:
                out.append(m)
        except (KBError, OSError) as e:
            out.append(f'{eid} 适配没删掉：{e}')
    return out


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
    """这个会话挂不挂角色团：全局开、期没选关、引擎没关角色团（Claude 子 agent / 其他引擎内联）、套件装了（~/.claude 是唯一真源）。"""
    if not team_settings()['开启'] or not team_mode(eng):
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
    """期「角色调用」→ {合计, 按角色}：只统计，不设上限。"""
    u = (m or {}).get('角色调用') or {}
    return {'合计': int(u.get('合计') or 0), '按角色': dict(u.get('按角色') or {})}


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
    if eng is not None and team_mode(eng) == '内联':
        return _inline_text(scene, cfg, roles, eng)
    t, _ = load_scenes(team_settings()['档位'])
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
           + '，避免反复派同一个专家；够了就不再派，按原流程继续。',
           '- 派单必须前台运行：不要 run_in_background，等子 agent 交回结果再继续。'
           + (f'这是自动发起的会话：派单时 model 参数填 "{model}"。' if auto and P0_MODEL_PARAM else ''),
           '- 任务说明开头写「知识库会话：只把正文交回，不写文件、不写 workspace/」；子 agent 只交回正文，不写文件。',
           ('- 产物由你用知识库工具落库：PRD / 方案 / Bug 分析照旧走 kb_write_doc / kb_stack_plan / kb_bug_analysis；'
            f'本场景的角色产物「{prod}」用 kb_write_doc 写到 category=其他资料、filename={sub}/<阶段序号>-{prod}.md，'
            '同名覆盖更新，不要新建重复文件；不要在仓库或知识库目录里建 workspace/。' if prod and prod not in _OWN_TOOL_PRODUCTS else
            '- 专家的意见由你吸收进原来的产出' + (f'（{prod} 照旧用原来的知识库工具交）' if prod else '')
            + '，本场景不用另写角色产物文件；不要在仓库或知识库目录里建 workspace/。'),
           f'- 要用户确认时照旧 kb_ask（stage 填 立项 / 技术方案 / 上线放量 / 确认点），recommend 由主导角色给出，末尾注明「建议来自：{role_cn(lead)}」'
           '（实际是谁的建议就写谁）；明确、不扩大范围的带 sure=true。kb_ask 返回「AI 已自动定」或提示免打扰时按 recommend 执行，并用 kb_log 写明采用的建议和「建议来自：<角色>」。'
           '产品经理的选项里有扩大范围和不扩大两类时，recommend 选不扩大。',
           '- 开始开发、方案拍板、Bug 审核只能用户本人在网页上点：不要调用 kb approve，也不要替用户操作网页。']
    if cfg.get('只读'):
        out.append('- 这是只读会话：专家同样不能改代码或本机文件，只读代码、给意见。')
    return '\n'.join(out)


def _role_lines(roles, eng):
    out = []
    for r, why, lead in roles:
        out.append(f'- {r}（{role_cn(r)}{"，主导" if lead else ""}）' + (f'：{why}' if why else '')
                   + f'\n  负责：{ROLE_DUTY.get(r, "")}；完整角色说明：{role_file(r, eng)}')
    return out


def _inline_text(scene, cfg, roles, eng):
    """内联专家意见：引擎没有 Agent 子 agent 时，把场景专家的说明拼进提示词，主会话按专家视角出意见（只给意见、不设卡）。
    每段意见开头单独一行 [kb-role:<英文名>]，会话结束按标记统计「👤 参与」。"""
    cap = int(cfg.get('上限') or 1)
    per = cfg.get('每确认点上限')
    t, _ = load_scenes(team_settings()['档位'])
    pdir = t.get('产物目录') or _BUILTIN['产物目录']
    sub = pdir.split('/', 1)[1] if '/' in pdir else '角色产物'
    prod = cfg.get('产物') or ''
    lead = next((r for r, _, ld in roles if ld), roles[0][0])
    out = ['\n\n【角色团·内联专家意见】流程命令照旧，专家只出意见：开发流程、斜杠命令、知识库工具照原来的要求用，专家意见不替代它们。',
           f'这个引擎没有子 agent：本场景「{scene}」需要专业意见时，由你换到下面专家的视角自己出意见（先读完整角色说明，按其中的职责、方法和交付模板来）：',
           *_role_lines(roles, eng),
           f'什么时候用：{cfg.get("触发") or "需要专业意见时"}（说「派 / 请某专家」的地方，都按「换到该专家视角出意见」理解）。不需要就不用。',
           f'- 次数：本会话最多出 {cap} 段专家意见' + (f'（每个确认点最多 {per} 段）' if per else '') + '，同一个专家不要反复出；够了就按原流程继续。',
           '- 格式：每段专家意见开头单独一行写标记「[kb-role:<英文名>]」（英文名用上面列表里的，如列表第一个就写它的英文名），'
           '下一行起写意见正文；知识库按这个标记统计参与的专家。意见写在你的回复里，不另建文件。',
           ('- 产物由你用知识库工具落库：PRD / 方案 / Bug 分析照旧走 kb_write_doc / kb_stack_plan / kb_bug_analysis；'
            f'本场景的角色产物「{prod}」用 kb_write_doc 写到 category=其他资料、filename={sub}/<阶段序号>-{prod}.md，'
            '同名覆盖更新，不要新建重复文件；不要在仓库或知识库目录里建 workspace/。' if prod and prod not in _OWN_TOOL_PRODUCTS else
            '- 专家意见由你吸收进原来的产出' + (f'（{prod} 照旧用原来的知识库工具交）' if prod else '')
            + '，本场景不用另写角色产物文件；不要在仓库或知识库目录里建 workspace/。'),
           f'- 要用户确认时照旧 kb_ask（stage 填 立项 / 技术方案 / 上线放量 / 确认点），recommend 按主导专家的意见给，末尾注明「建议来自：{role_cn(lead)}」'
           '（实际按谁的视角就写谁）；明确、不扩大范围的带 sure=true。kb_ask 返回「AI 已自动定」或提示免打扰时按 recommend 执行，并用 kb_log 写明采用的建议和「建议来自：<角色>」。'
           '产品经理视角的选项里有扩大范围和不扩大两类时，recommend 选不扩大。',
           '- 专家只协助、不设卡：不因为专家视角拦下、拒绝或要求额外确认原流程里的步骤。',
           '- 开始开发、方案拍板、Bug 审核只能用户本人在网页上点：不要调用 kb approve，也不要替用户操作网页。']
    if cfg.get('只读'):
        out.append('- 这是只读会话：专家视角同样只读代码、给意见，不改代码或本机文件。')
    return '\n'.join(out)


def push_rule(m, eng):
    """跟进（talk._push_prompt）第 1 步后补的「按类型派角色」；不挂角色团时空串。"""
    if not team_on(m, eng) or not scene_roles('跟进', _meta(m))[1]:
        return ''
    if team_mode(eng) == '内联':
        return ('\n   按类型出专家意见：只需回答的问题不用；缺陷按对应端工程师视角定位、测试专家视角补复现和影响面；'
                '需求扩展先按 product-manager 视角写变更说明（改什么、影响谁、延后什么），再用 kb_ask 确认。')
    return ('\n   按类型派角色：只需回答的问题不派角色；缺陷请对应端工程师定位、测试专家补复现和影响面；'
            '需求扩展先请 product-manager 写变更说明（改什么、影响谁、延后什么），再用 kb_ask 确认。')


# ---------------------------------------------------------------- 统计：会话日志里的 Agent / Task 调用
def team_count(logf):
    """数主会话的 Agent / Task 调用 → {subagent_type: 次数}（子 agent 内部的调用不算）；内联模式数 AI 回复里的 [kb-role:<角色>] 标记。"""
    try:
        fh = open(logf, encoding='utf-8', errors='ignore')
    except (OSError, TypeError):
        return {}
    with fh:
        return _count_lines(fh)


def team_count_text(text):
    """同 team_count，数的是前台会话（engine_run）的输出文本。"""
    return _count_lines((text or '').splitlines())


_MARK = re.compile(r'\[kb-role:([a-z][a-z0-9-]*)\]')
# 内联标记只数 AI 自己说的话：最终汇总、工具返回、命令输出、子 agent 的行里重复出现的不算
_MARK_SKIP = ('"type":"result"', '"type": "result"', '"tool_result"', '"function_call_output"', '"command_execution"',
              '"type":"user"', '"type": "user"', '"tool_output"', '"aggregated_output"', '"type":"reasoning"', '"type": "reasoning"')


def _count_marks(line, out):
    if '[kb-role:' not in line or any(x in line for x in _MARK_SKIP) or '"parent_tool_use_id":"' in line.replace(' ', ''):
        return
    for r in sorted(set(_MARK.findall(line))):
        out[r] = out.get(r, 0) + 1


def _count_lines(lines):
    out = {}
    for line in lines:
        _count_marks(line, out)
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
        u.pop('超限已记', None)          # 旧版字段：已没有每期上限
    cap = _cap_of(scene or rec.get('场景', ''))
    used = sum(have.values()) if cumulative else n          # cumulative=False：一份日志就是一个会话（各端开发 / 审查每轮一份）
    if d is not None and cap and used > cap:
        log(d, '角色团', f'超限提醒：「{scene or rec.get("场景", "")}」会话调用了 {used} 次角色，场景上限 {cap}（{roles_text(got)}）')
    return got


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
        u.pop('超限已记', None)
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
