"""SDD 流程的子进度、子 agent 次数和预计剩余时间（期页面「AI 实时状态一行」用）。

只读现有数据，不改 SDD 流程、不加埋点：
  - sdd-status.md：在第几个 TASK、TASK 总数、审查 / 真机修复第几轮、阻塞原因；
  - 会话 stream 日志（期目录 开发会话/*.jsonl）：每次子 agent 调用（tool_use name=Agent）的类型、说明、开始时间，
    返回（tool_result 带同一 id）的时间 → 每次调用的耗时；说明里的「TASK-n」把调用归到 TASK。

预估规则（变更说明：其他资料/角色产物-01-变更说明.md，固定公式，能核对）：
  单个 TASK 区间：本期已完成 ≥2 个 → 它们子 agent 实际耗时的最小～最大；1 个 → ×0.8～×1.3（约）；
               0 个 → 各步中位数之和 ×1～×1.7（约），中位数先用本期、没有再用所有期、再没有用内置默认值。
  当前 TASK 剩余：当前步（中位数 − 已跑，至少 1 分钟）+ 后面各步中位数；返工轮次 ≥2（审查第 2 轮起 / 真机修复已开始）上限再加一轮「审查 + 真机」。
  总剩余：当前 TASK 剩余 + 没开始的 TASK 数 × 单个区间 + 归档 1 分钟；用量限制暂停时加上等到恢复的时间。
  当前步跑得比这类子 agent 历来最长的还久：标「超出预计，已超 N 分钟」。
  规划阶段（step3 没开始）不估总时间；等你回答 / 阻塞时不估（前端在疑似卡住时也不显示）。时间取整到 5 分钟。
"""
import json
import re
import time
from datetime import datetime
from pathlib import Path

from kblib.core.common import PERIODS
from kblib.ai.devices import _sdd_status

DEV_DIR = '开发会话'
# 子 agent 类型 → 步骤名；每个 TASK 的固定链
KIND = {'sdd-dev-agent': '开发', 'sdd-reviewer-agent': '审查', 'sdd-fix-agent': '修复', 'sdd-agentdevice-agent': '真机验证',
        'sdd-task-commit-agent': '提交'}
CHAIN = ['开发', '审查', '真机验证', '提交']
DEFAULT_MED = {'开发': 12.0, '审查': 5.0, '真机验证': 17.0, '提交': 0.5, '修复': 1.0}     # 没有任何历史时用（T2-54 实测中位数）
PLAN_KINDS = ('sdd-arch-planner-agent', 'sdd-plan-reviser-agent', 'sdd-plan-reviewer-agent')
_TASK_RE = re.compile(r'TASK-(\d+)', re.I)
_TS_RE = re.compile(r'"timestamp"\s*:\s*"([^"]+)"')
_RES_RE = re.compile(r'"tool_use_id"\s*:\s*"([^"]+)"')
_CALLS = {}    # 日志路径 → [已读位置, [调用...], {未返回 id: 调用}]
_HIST = {'at': 0, 'med': None, 'max': None}


def _ts(s):
    try:
        return datetime.fromisoformat(s.replace('Z', '+00:00')).timestamp()
    except (ValueError, AttributeError):
        return 0


def calls_of(logf):
    """日志里的子 agent 调用：[{id, 类型, 说明, TASK, 开始, 结束(0=没返回)}]。增量读。"""
    p = Path(logf or '')
    if not p.is_file():
        return []
    key = str(p)
    pos, calls, open_ = _CALLS.get(key) or [0, [], {}]
    size = p.stat().st_size
    if size < pos:
        pos, calls, open_ = 0, [], {}
    if size > pos:
        with p.open('rb') as fh:
            fh.seek(pos)
            data = fh.read()
        cut = data.rfind(b'\n') + 1
        for line in data[:cut].decode('utf-8', 'ignore').splitlines():
            if '"tool_use"' in line and ('"Agent"' in line or '"Task"' in line):
                try:
                    j = json.loads(line)
                except ValueError:
                    j = {}
                t = _ts(j.get('timestamp', '')) if isinstance(j, dict) else 0
                for c in ((j.get('message') or {}).get('content') or []) if isinstance(j, dict) else []:
                    if isinstance(c, dict) and c.get('type') == 'tool_use' and c.get('name') in ('Agent', 'Task') and c.get('id'):
                        i = c.get('input') or {}
                        desc = str(i.get('description') or '')
                        mm = _TASK_RE.search(desc)
                        x = {'id': c['id'], '类型': str(i.get('subagent_type') or ''), '说明': desc[:80],
                             'TASK': int(mm.group(1)) if mm else 0, '开始': t, '结束': 0, '后台': bool(i.get('run_in_background'))}
                        calls.append(x)
                        open_[c['id']] = x
            if open_ and '"tool_result"' in line and '"tool_use_id"' in line:   # system/task_started 也带 tool_use_id，不算返回
                ids = [i for i in _RES_RE.findall(line) if i in open_]
                if ids:
                    try:                                # 行里的工具输出也可能带 "timestamp"，取这一行自己的
                        t2 = _ts(json.loads(line).get('timestamp', ''))
                    except (ValueError, AttributeError):
                        t2 = 0
                    if not t2:
                        mm = _TS_RE.findall(line)
                        t2 = _ts(mm[-1]) if mm else 0
                    for i in ids:
                        x = open_.pop(i)
                        x['结束'] = t2 or x['开始']
        pos += cut
        _CALLS[key] = [pos, calls, open_]
    return calls


def _logs(d):
    dd = Path(d) / DEV_DIR
    return sorted(dd.glob('*.jsonl')) if dd.exists() else []


def _mins(x):
    return max(0.0, (x['结束'] - x['开始']) / 60) if x['结束'] and x['开始'] else None


def _stats(calls):
    """每类步骤的中位数、最大值（分钟），只算已返回、非后台的调用。"""
    by = {}
    for x in calls:
        k = KIND.get(x['类型'])
        v = _mins(x)
        if k and v is not None and not x['后台']:
            by.setdefault(k, []).append(v)
    med = {k: sorted(v)[len(v) // 2] for k, v in by.items()}
    return med, {k: max(v) for k, v in by.items()}


def _history():
    """所有期的各步中位数 / 最大值（10 分钟算一次）。"""
    if _HIST['med'] is None or time.time() - _HIST['at'] > 600:
        calls = []
        for d in PERIODS.iterdir() if PERIODS.exists() else []:
            if d.is_dir():
                for f in _logs(d):
                    calls += calls_of(f)
        _HIST.update(at=time.time(), med=None, max=None)
        _HIST['med'], _HIST['max'] = _stats(calls)
    return _HIST['med'], _HIST['max']


def _r5(v, up=False):
    """取整到 5 分钟（下限向下、上限向上，至少 5）。"""
    import math
    return max(5, int((math.ceil if up else math.floor)(v / 5) * 5))


def sdd_eta(d, docs, running_logs=(), resume_at=0, now=None):
    """→ {总体, 子进度, 子agent, 任务表, 预估} 或 None（不是SDD / 没有状态文件）。
    running_logs：在跑会话的日志（只有它们里面没返回的调用才算「正在跑」）；resume_at：用量限制暂停的恢复时间戳。"""
    if not docs or not (Path(docs) / 'sdd-status.md').exists():
        return None
    now = now or time.time()
    st = _sdd_status(docs)
    run_set = {str(x) for x in running_logs if x}
    calls = []
    for f in _logs(d):
        for x in calls_of(f):
            calls.append(dict(x, 日志=str(f)))
    calls.sort(key=lambda x: x['开始'] or 0)
    plan_end = max([i for i, x in enumerate(calls) if x['类型'] in PLAN_KINDS] + [-1])   # 重新规划过：只看最后一次规划之后的 TASK
    task_calls = [x for x in calls[plan_end + 1:] if x['TASK']]
    running = [x for x in calls if not x['结束'] and x['日志'] in run_set and not x['后台']]
    med, mx = _stats(task_calls)
    hmed, hmax = _history()
    for k, v in DEFAULT_MED.items():
        med.setdefault(k, hmed.get(k, v))
        mx.setdefault(k, hmax.get(k, v * 2))

    total = int(st.get('step3_task_total') or 0) if str(st.get('step3_task_total') or '').isdigit() else 0
    idx = int(st.get('step3_current_index') or 0) if str(st.get('step3_current_index') or '').isdigit() else 0
    s3 = st.get('step3', 'pending')
    planning = s3 not in ('done', 'in_progress') and not idx
    all_done = s3 == 'done'

    # 每个 TASK 一行：状态、子 agent 实际耗时、次数、返工
    rows = {}
    for x in task_calls:
        r = rows.setdefault(x['TASK'], {'任务': f'TASK-{x["TASK"]}', '耗时': 0.0, '次数': 0, '返工': 0, '已提交': False})
        r['次数'] += 1
        v = _mins(x)
        r['耗时'] += v or 0
        if x['类型'] == 'sdd-fix-agent' or ('修复' in x['说明'] and x['类型'] == 'sdd-dev-agent'):
            r['返工'] += 1
        if x['类型'] == 'sdd-task-commit-agent' and x['结束']:
            r['已提交'] = True
    table = []
    for n in range(1, max([total] + list(rows)) + 1):
        r = rows.get(n) or {'任务': f'TASK-{n}', '耗时': 0.0, '次数': 0, '返工': 0, '已提交': False}
        done = r['已提交'] or all_done or (idx and n < idx)
        table.append({'任务': r['任务'], '状态': '已完成' if done else '进行中' if n == idx else '未开始',
                      '耗时': round(r['耗时']), '次数': r['次数'], '返工': r['返工']})

    # 当前 TASK 的子进度
    cur_kind = KIND.get(running[-1]['类型']) if running else ''
    phase = (st.get('step3_task_phase') or '').lower()
    if not cur_kind:
        cur_kind = {'dev': '开发', 'review': '审查', 'fix': '修复', 'device': '真机验证', 'agentdevice': '真机验证',
                    'commit': '提交'}.get(phase, '')
    step_now = '审查' if cur_kind == '修复' else cur_kind
    ci = CHAIN.index(step_now) if step_now in CHAIN else -1
    rr = int(st.get('step3_task_review_round') or 0) if str(st.get('step3_task_review_round') or '').isdigit() else 0
    fr = int(st.get('step3_device_fix_round') or 0) if str(st.get('step3_device_fix_round') or '').isdigit() else 0
    elapsed = (now - running[-1]['开始']) / 60 if running and running[-1]['开始'] else 0
    sub = None
    if idx and not all_done:
        sub = {'任务': st.get('step3_current_task') or f'TASK-{idx}',
               '步骤': [{'名称': k, '状态': 'done' if ci > i else 'cur' if ci == i else 'todo',
                        '轮次': (rr if k == '审查' and rr > 1 else fr + 1 if k == '真机验证' and fr else 0)} for i, k in enumerate(CHAIN)],
               '当前': cur_kind, '已跑': round(elapsed), '一般': round(med.get(cur_kind, 0)) if cur_kind else 0}
    overall = {'规划': '进行中' if planning else '已完成', '完成': (total if all_done else max(0, idx - 1)), '总数': total,
               '归档': '已完成' if all_done else '未开始'}
    if planning and running:
        x = running[-1]
        same = sorted(v for v in (_mins(c) for c in calls if c['类型'] == x['类型']) if v is not None)
        overall['规划步骤'] = {'类型': x['类型'], '已跑': round(elapsed), '一般': round(same[len(same) // 2]) if same else 0}
    agents = {'已派': len(calls), '在跑': [{'类型': x['类型'], '说明': x['说明'], '已跑': round((now - x['开始']) / 60) if x['开始'] else 0}
                                         for x in running]}

    # 预估
    est = None
    blocked = st.get('step3_task_blocked_reason') or st.get('step3_device_block_reason') or st.get('step3_pending_decision')
    if not planning and not all_done and total and not blocked:
        done_d = [r['耗时'] for n, r in sorted(rows.items()) if r['已提交'] and r['耗时'] > 0]
        base = sum(med[k] for k in CHAIN)
        if len(done_d) >= 2:
            per, approx, why = (min(done_d), max(done_d)), False, f'本期已完成 {len(done_d)} 个 TASK，每个子 agent 实际用了 {round(min(done_d))}–{round(max(done_d))} 分钟'
        elif done_d:
            per, approx, why = (done_d[0] * 0.8, done_d[0] * 1.3), True, f'本期只完成 1 个 TASK（{round(done_d[0])} 分钟），按 ×0.8～×1.3 估'
        else:
            per, approx, why = (base, base * 1.7), True, f'本期还没完成 TASK，按各步中位数（{"+".join(f"{k}{round(med[k])}" for k in CHAIN)} 分钟）估'
        if ci >= 0:
            cur_lo = max(1.0, med[step_now] - elapsed) + sum(med[k] for k in CHAIN[ci + 1:])
        else:
            cur_lo = base
        cur_hi = cur_lo + ((med['审查'] + med['真机验证']) if rr >= 2 or fr >= 1 else 0)
        left = max(0, total - max(idx, 1))
        lo = cur_lo + left * per[0] + 1
        hi = max(lo, cur_hi + left * per[1] + 1)
        over = 0
        if running and cur_kind and elapsed > mx.get(cur_kind, 0) > 0:
            over = round(elapsed - mx[cur_kind])
        wait = max(0.0, (resume_at - now) / 60) if resume_at else 0
        est = {'低': _r5(lo), '高': _r5(hi, up=True), '约': approx, '依据': why, '超出': over,
               '等待恢复': round(wait), '剩余TASK': left}
    return {'总体': overall, '子进度': sub, '子agent': agents, '任务表': table, '预估': est,
            '阻塞': (blocked or '')[:160]}
