"""期页面的「AI 实时状态一行」：AI 还在不在跑、几个 AI、在做哪份工作、到哪了、多久前有动静。

只用现有数据，不加 AI 摘要：
  - 会话：开发 / 复测 / 跟进会话、并行新会话、全栈各端会话、Bug 分析 / 修复会话（talk.period_sessions 同一套来源）；
  - 最后动静：会话 stream 日志的修改时间；在做：日志里最后一个动作；
  - 几个 AI：在跑的会话数 + 日志里发出了、还没返回的 Agent / Task 子 agent 调用数；
  - 进度：tasks.md 勾选 / SDD 的 step3 序号；还没任务时只给阶段；这次会话里总数变过标「约」。

状态按优先级：等你回答 > 暂停（用量限制）> 运行（前端按最后动静再分：≤5 分钟在跑 / 5–15 安静 / >15 疑似卡住）> 已停 / 已完成。
时间分档放在前端算（页面每 30 秒重算），后端只给时间戳，接口结果不会每分钟都变。
"""
import json
import re
import time
from pathlib import Path

from kblib.core.periods import load_meta, period_dir
from kblib.ai.devices import dev_activity
from kblib.ai.devsession import dev_session, session_flow
from kblib.ai.flows import flow_progress
from kblib.ai import supervise as _sup
from kblib import lazy
_talk = lazy('kblib.ai.talk')

QUIET_MIN, STUCK_MIN = 5, 15             # 前端分档用，接口里一起给出
_SUB = {}      # 日志路径 → [已读到的位置, {未返回的子 agent 调用 id}]
_TOTAL = {}    # (期名, 会话开始) → 第一次看到的任务总数：中途变了标「约」
_AGENT_RE = re.compile(r'"name"\s*:\s*"(?:Agent|Task)"')
_RES_RE = re.compile(r'"tool_use_id"\s*:\s*"([^"]+)"')


def open_agents(logf):
    """日志里发出了、还没返回的 Agent / Task 调用数（专家子 agent 在跑几个）。增量读，日志再大也只读新增部分。"""
    p = Path(logf or '')
    if not p.is_file():
        return 0
    key = str(p)
    pos, ids = _SUB.get(key) or [0, set()]
    size = p.stat().st_size
    if size < pos:
        pos, ids = 0, set()
    if size > pos:
        with p.open('rb') as fh:
            fh.seek(pos)
            data = fh.read()
        cut = data.rfind(b'\n') + 1
        for line in data[:cut].decode('utf-8', 'ignore').splitlines():
            if _AGENT_RE.search(line) and '"tool_use"' in line:
                try:
                    j = json.loads(line)
                except ValueError:
                    j = {}
                for c in ((j.get('message') or {}).get('content') or []) if isinstance(j, dict) else []:
                    if isinstance(c, dict) and c.get('type') == 'tool_use' and c.get('name') in ('Agent', 'Task') and c.get('id'):
                        ids.add(c['id'])
            if ids and '"tool_result"' in line:
                ids -= set(_RES_RE.findall(line))
        pos += cut
        _SUB[key] = [pos, ids]
    return len(ids)


def _mtime(logf):
    try:
        return Path(logf).stat().st_mtime if logf else 0
    except OSError:
        return 0


def _doing(logf):
    """最后一个动作，一句话。"""
    acts = dev_activity(logf, 5)
    for a in reversed(acts):
        if a['类型'] == '工具':
            return f'{a["工具"]} {a["内容"]}'.strip()[:80]
        if a['类型'] in ('说明', '输出'):
            return a['内容'].split('\n')[0][:80]
    return ''


def _logs(d, m):
    """在跑会话的键 → 日志路径。"""
    out = {}
    s = m.get('开发会话') or {}
    if s.get('状态') == '运行中':
        out['dev'] = s.get('日志', '')
    for x in m.get('并行会话') or []:
        if x.get('状态') == '运行中':
            out['px:' + x['编号']] = x.get('日志', '')
    for e, x in (m.get('各端开发') or {}).items():
        out[f'end:{e}'] = x.get('审查日志' if x.get('状态') == '审查中' else '日志') or ''
    try:
        for x in _talk._period_an(d.name):
            if x.get('状态') == '运行中':
                out['an:' + x['编号']] = x.get('日志', '')
    except Exception:          # Bug 索引读不到不影响其他会话
        pass
    return out


def _progress(d, m, s):
    """{完成, 总数, 约, 当前, 阶段, 阶段序号, 阶段总数}；没有可显示的进度返回 None。"""
    try:
        pg = flow_progress(session_flow(m), m.get('SDD目录'))
    except Exception:
        pg = None
    if not pg:
        return None
    if pg.get('类型') == 'SDD':
        t, steps = pg.get('任务') or {}, pg.get('步骤') or []
        done, total, cur = (t.get('序号') or 0), t.get('总数') or 0, t.get('当前') or ''
        if total and done:
            done -= 1                      # step3_current_index 是正在做的第几个，已做完的少一个
        if steps and all(x['状态'] in ('done', 'skipped') for x in steps):
            done = total
        i = next((k for k, x in enumerate(steps) if x['状态'] not in ('done', 'skipped')), len(steps))
        stage = steps[i]['名称'] if i < len(steps) else '已完成'
        out = {'完成': done, '总数': total, '当前': cur, '阶段': stage, '阶段序号': min(i + 1, len(steps)), '阶段总数': len(steps)}
    else:
        out = {'完成': pg.get('完成', 0), '总数': pg.get('总数', 0), '当前': pg.get('当前', ''), '阶段': '' if pg.get('总数') else '规划中',
               '阶段序号': 0, '阶段总数': 0}
    if out['总数']:
        key = (d.name, (s or {}).get('开始时间') or '')
        first = _TOTAL.setdefault(key, out['总数'])
        out['约'] = first != out['总数']
    else:
        out['约'] = False
    return out


def period_live(name):
    """期页面状态条 / 总览卡片 / 侧栏用的实时状态。时间都是秒级时间戳（前端按现在的时间分档）。"""
    d = period_dir(name)
    s = dev_session(d.name) or {}
    m = load_meta(d)
    logs = _logs(d, m)
    sess = []
    try:
        running = _talk.period_sessions(d.name)
    except Exception:
        running = []
    for x in running:
        lf = logs.get(x['键'], '')
        sess.append({'键': x['键'], '名称': x['名称'], '开始': x.get('开始', ''), '最后动静': _mtime(lf),
                     '在做': _doing(lf), '子agent': open_agents(lf)})
    pend = [q for q in m.get('待确认') or [] if q.get('状态') == '待回答']
    pause = [x for x in _sup.entries(period=d.name, states=('待续',)) if x.get('原因') == '用量限制']
    if pend:
        st = '等你回答'
    elif pause and not sess:
        st = '暂停'
    elif sess:
        st = '运行'
    else:
        st = '已完成' if m.get('状态') == '已完成' else '已停'
    last = max([x['最后动静'] for x in sess] + [0]) or max([_mtime(s.get('日志'))] + [0])
    return {'状态': st, '期状态': m.get('状态', ''), '会话': sess, 'AI数': len(sess) + sum(x['子agent'] for x in sess),
            '最后动静': last, '现在': time.time(), '安静分钟': QUIET_MIN, '卡住分钟': STUCK_MIN,
            '问题': {'编号': pend[0].get('编号') or pend[0].get('id', ''), '问题': (pend[0].get('问题') or '')[:120], '个数': len(pend)} if pend else None,
            '恢复时间': min(x.get('恢复时间') or '' for x in pause) if pause else '',
            '进度': _progress(d, m, s), '可停止': any(x['键'] == 'dev' for x in sess)}


def live_brief(name):
    """总览卡片 / 侧栏用的精简版：只算在开发的期，别的返回 None。"""
    m = load_meta(period_dir(name))
    if m.get('状态') not in ('已批准开发', '开发中') and not (m.get('开发会话') or {}).get('状态') == '运行中':
        return None
    v = period_live(name)
    pg = v['进度'] or {}
    return {'状态': v['状态'], '最后动静': v['最后动静'], 'AI数': v['AI数'], '在跑': len(v['会话']),
            '完成': pg.get('完成', 0), '总数': pg.get('总数', 0), '约': pg.get('约', False), '阶段': pg.get('阶段', ''),
            '恢复时间': v['恢复时间']}
