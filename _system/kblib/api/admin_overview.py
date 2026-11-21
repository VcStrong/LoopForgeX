"""管理员「📊 总览」看板的数据（T002，用户 2026-11-20 21:59 / 22:00）：全员执行进度、项目进展、账号、执行机、AI 账号、
任务队列、资源池、告警、安全审计、定时任务，一个接口聚合返回（GET /api/admin/overview，只给管理员；契约外补充接口）。

只读汇总，不改任何数据；每块单独 try，某块出错只在那块返回 error，不拖垮整页。
"""
import shutil
import time
from contextlib import contextmanager

from kblib.core import db as _db
from kblib.core.common import ROOT, _USER_CTX, current_ctx

ACTIVE = ('已派', '运行', '等回答')
DAY = 86400


@contextmanager
def _all_projects():
    """看板要看全部项目：临时去掉「按当前项目」的默认视图。"""
    tok = _USER_CTX.set({**current_ctx(), 'all_projects': True})
    try:
        yield
    finally:
        _USER_CTX.reset(tok)


def _safe(fn):
    try:
        return fn()
    except Exception as e:      # noqa: BLE001  某一块坏了不影响别的块
        return {'error': f'{type(e).__name__}: {e}'[:300]}


def _day(ts):
    return time.strftime('%Y-%m-%d', time.localtime(ts))


# ---------------------------------------------------------------- 人：执行进度、账号状态
def people(tasks, now):
    by = {}
    for t in tasks:
        by.setdefault(t.get('initiator') or '', []).append(t)
    out = []
    for u in _db.user_list():
        ts = by.get(u['name'], [])
        recent = [t for t in ts if (t.get('updated') or 0) >= now - 7 * DAY]
        st = u.get('status') or '正常'
        if st == '正常' and (u.get('locked_until') or 0) > now:
            st = '锁定'
        out.append({'用户': u['name'], '角色': u.get('sys_role') or '普通', '状态': st, '本机用户': bool(u.get('local')),
                    '最近登录': u.get('last_login') or '',
                    '在跑': sum(1 for t in ts if t['status'] in ('已派', '运行')),
                    '等回答': sum(1 for t in ts if t['status'] == '等回答'),
                    '排队': sum(1 for t in ts if t['status'] == '排队'),
                    '近7天完成': sum(1 for t in recent if t['status'] == '完成'),
                    '近7天失败': sum(1 for t in recent if t['status'] == '失败'),
                    '进行中': [{'任务': t['id'], '期': t.get('period') or '', '类型': t.get('type') or '', '状态': t['status'],
                               '执行机': t.get('runner') or ''} for t in ts if t['status'] in ACTIVE + ('排队',)][:8]})
    out.sort(key=lambda x: (-(x['在跑'] + x['等回答'] + x['排队']), x['用户']))
    return out


# ---------------------------------------------------------------- 项目 / 期：状态分布、待确认、Bug、近 7 天推进
def projects(now):
    import kb
    rows = {r['name']: r for r in _db.period_rows()}
    names = {p['id']: p['name'] for p in _db.project_list()}
    rec7 = _db.record_counts(time.strftime('%Y-%m-%d', time.localtime(now - 7 * DAY)))
    try:
        bugs = [e for e in kb.zt_load()['Bug'].values() if e.get('状态') != '已关闭' and not e.get('忽略')]
    except Exception:       # noqa: BLE001
        bugs = []
    open_bug = {}
    for e in bugs:
        if e.get('状态') == '激活' and (e.get('修复') or {}).get('状态') != '已修复':
            n = (e.get('归属') or {}).get('名称') or ''
            open_bug[n] = open_bug.get(n, 0) + 1
    periods, status_by_proj = [], {}
    for n in kb.period_names():
        m = kb.load_meta(kb.PERIODS / n)
        r = rows.get(n) or {}
        pid = r.get('project') or 'default'
        st = m.get('状态') or '收集中'
        kind = '事项' if kb.is_task(m) else '期'
        status_by_proj.setdefault(pid, {}).setdefault(st, 0)
        status_by_proj[pid][st] += 1
        periods.append({'名称': n, '类型': kind, '项目': names.get(pid, pid), '负责人': r.get('owner') or '', '状态': st,
                        '待确认': sum(1 for x in m.get('待确认') or [] if x.get('状态') == '待回答'),
                        '待处理问题': sum(1 for x in m.get('非阻断问题') or [] if x.get('状态') == '待处理'),
                        '未解决Bug': open_bug.get(n, 0), '近7天记录': rec7.get(n, 0), '更新': m.get('更新时间', ''),
                        '会话在跑': (kb.dev_session(n) or {}).get('状态') == '运行中'})
    periods.sort(key=lambda p: (p['状态'] in ('已完成',), -p['近7天记录'], p['名称']))
    projs = []
    for p in _db.project_list():
        projs.append({'id': p['id'], '名称': p['name'], '负责人': p.get('owner') or '', '成员数': len(_db.members_of(p['id'])),
                      '状态分布': status_by_proj.get(p['id'], {}),
                      '近7天记录': sum(x['近7天记录'] for x in periods if x['项目'] == p['name'])})
    days = _db.record_counts(time.strftime('%Y-%m-%d', time.localtime(now - 13 * DAY)), by_day=True)
    trend = [{'日期': _day(now - i * DAY), '记录': days.get(_day(now - i * DAY), 0)} for i in range(13, -1, -1)]
    return {'项目': projs, '期': periods[:200], '期总数': len(periods), '推进趋势': trend,
            'Bug': {'未关': len(bugs), '待归纳': sum(1 for e in bugs if (e.get('归属') or {}).get('类型') == '待归纳')}}


# ---------------------------------------------------------------- 执行机：在线、指标、24 小时趋势、告警
def runners(now):
    from kblib.core import queue
    rs = queue.runners_view()
    remote = [r for r in rs if not r['本机']]
    items = [{'id': r['id'], '名称': r['名称'], '状态': r['状态'], '本机': r['本机'], 'CPU': r['指标'].get('CPU'),
              '内存可用比': r['指标'].get('内存可用比'), '磁盘使用比': r['指标'].get('磁盘使用比'),
              '在跑': len(r['运行中']), '容量': r['容量'], '告警': r['告警'], '最后心跳': r['最后心跳'], '版本': r['版本'],
              '网络差': r['网络差']} for r in rs]
    trend = {}
    for r in remote[:10]:
        trend[r['名称']] = [{'时间': p['b'], 'CPU': p['cpu'], '内存可用比': p['mem'], '磁盘使用比': p['disk']}
                          for p in _db.metrics_agg(r['id'], now - DAY, 600)]
    alerts = []
    names = {r['id']: r['名称'] for r in rs}
    for a in _db.rows('alerts', extra='end IS NULL', order='start DESC', limit=50):
        alerts.append({'执行机': names.get(a['runner'], a['runner']), '类型': a['kind'], '级别': a['level'],
                       '开始': _db.ts_str(a['start']) if a.get('start') else '', '说明': a.get('detail') or '',
                       '已确认': bool(a.get('ack_by'))})
    return {'在线': sum(1 for r in remote if r['状态'] in ('在线', '排空中')), '总数': len(remote),
            '离线': sum(1 for r in remote if r['状态'] == '离线'), '列表': items, '趋势': trend, '告警': alerts}


# ---------------------------------------------------------------- 任务队列
def queue_stats(tasks, now):
    q = [t for t in tasks if t['status'] == '排队']
    waits = [now - (t.get('waiting_since') or t.get('created') or now) for t in q]
    week = [t for t in tasks if (t.get('updated') or 0) >= now - 7 * DAY]
    done = sum(1 for t in week if t['status'] == '完成')
    fail = sum(1 for t in week if t['status'] == '失败')
    by_type = {}
    for t in week:
        by_type.setdefault(t.get('type') or '其他', {'完成': 0, '失败': 0, '其他': 0})
        k = t['status'] if t['status'] in ('完成', '失败') else '其他'
        by_type[t.get('type') or '其他'][k] += 1
    reasons = {}
    for t in q:
        w = (t.get('wait_reason') or '等执行机领取').split('：')[0][:30]
        reasons[w] = reasons.get(w, 0) + 1
    return {'排队': len(q), '运行': sum(1 for t in tasks if t['status'] in ACTIVE),
            '平均已等分钟': round(sum(waits) / len(waits) / 60, 1) if waits else 0,
            '最长已等分钟': round(max(waits) / 60, 1) if waits else 0,
            '近7天完成': done, '近7天失败': fail, '近7天失败率': round(fail / (done + fail), 3) if done + fail else 0,
            '近7天重试次数': sum(t.get('retries') or 0 for t in week), '按类型': by_type,
            '等待原因': sorted(([k, v] for k, v in reasons.items()), key=lambda x: -x[1])[:8]}


# ---------------------------------------------------------------- 资源池
def resources_stats():
    from kblib.core import resources
    out = {}
    for r in resources.overview():
        x = out.setdefault(r['类型'], {'总数': 0, '占用中': 0, '排队': 0, '不可用': 0})
        x['总数'] += 1
        x['占用中'] += 1 if r['占用'] else 0
        x['排队'] += len(r['队列'])
        x['不可用'] += 0 if r['可用'] else 1
    for x in out.values():
        x['占用率'] = round(x['占用中'] / x['总数'], 3) if x['总数'] else 0
    return out


# ---------------------------------------------------------------- AI 引擎账号：健康、并发 / RPM 使用率、预算、30 天 token
def ai_stats(now):
    from kblib.ai import accounts
    calls = _db.calls_since(now - 60)
    accs = []
    for a in accounts.all_accounts():
        v = accounts.view(a)
        accs.append({'名称': v['名称'], '引擎': v['引擎'], '类型': v['类型'], '项目': v['项目'], '状态': v['状态'], '原因': v['原因'],
                     '冷却到': v['冷却到'], '并发使用率': round(v['运行中'] / v['并发上限'], 3) if v['并发上限'] else 0,
                     '运行中': v['运行中'], '并发上限': v['并发上限'],
                     'RPM使用率': round(calls.get(a['id'], 0) / v['RPM上限'], 3) if v['RPM上限'] else 0, 'RPM上限': v['RPM上限']})
    rows = _db.usage_rows(now - 30 * DAY)
    per_day = {}
    for r in rows:
        d = per_day.setdefault(_day(r['ts']), {'输入': 0, '输出': 0, '估算': False})
        d['输入'] += r.get('tin') or 0
        d['输出'] += r.get('tout') or 0
        d['估算'] = d['估算'] or bool(r.get('estimated'))
    trend = [{'日期': _day(now - i * DAY), **per_day.get(_day(now - i * DAY), {'输入': 0, '输出': 0, '估算': False})}
             for i in range(29, -1, -1)]
    day0 = time.mktime(time.localtime(now)[:3] + (0, 0, 0, 0, 0, -1))
    tok = lambda xs: sum((r.get('tin') or 0) + (r.get('tout') or 0) for r in xs)    # noqa: E731
    month0 = time.mktime(time.localtime(now)[:2] + (1, 0, 0, 0, 0, 0, -1))
    budget = [{'项目': x['对象'], '已用比': x['已用比'], '预算日': x['预算日'], '预算月': x['预算月'], '估算': x['估算']}
              for x in accounts.usage_report('项目', 30) if x['预算日'] or x['预算月']]
    status = {}
    for a in accs:
        status[a['状态']] = status.get(a['状态'], 0) + 1
    return {'账号': accs, '状态分布': status, '今日token': tok([r for r in rows if r['ts'] >= day0]),
            '本月token': tok([r for r in rows if r['ts'] >= month0]), '趋势': trend, '预算': budget,
            '本机登录态模式': not accs}


# ---------------------------------------------------------------- 安全审计、定时任务、系统
def audit_stats(now):
    c = _db.audit_counts(now - 7 * DAY)
    kinds = {}
    for (k, res), n in c.items():
        x = kinds.setdefault(k, {'成功': 0, '失败': 0})
        x['成功' if res == '成功' else '失败'] += n
    sec = kinds.get('机密', {'成功': 0, '失败': 0})
    return {'机密读取': sec['成功'], '机密拒绝': sec['失败'], '按类型': kinds}


def schedules():
    return [{'名称': s['name'], '周期秒': s.get('period') or 0, '上次运行': _db.ts_str(s['last_run']) if s.get('last_run') else '',
             '结果': (s.get('last_result') or '')[:120], '持有者': s.get('holder') or '', '次数': s.get('runs') or 0}
            for s in _db.schedules()]


def system():
    from kblib.infra import webport
    try:
        from kblib.steward.update import code_version
        ver = code_version()[:12]
    except Exception:       # noqa: BLE001
        ver = ''
    du = shutil.disk_usage(ROOT)
    return {'版本': ver, '数据库': _db.status()['驱动'], '端口': webport.RUNNING.get('port') or webport.configured()[0],
            '数据目录磁盘使用比': round(du.used / du.total, 3), '数据目录剩余GB': round(du.free / 2 ** 30, 1)}


def admin_overview():
    now = _db.now_ts()
    with _all_projects():
        tasks = _safe(lambda: _db.rows('tasks', extra='(updated >= ? OR status IN (?, ?, ?, ?))', args=(now - 30 * DAY, '排队', *ACTIVE)))
        bad = tasks if isinstance(tasks, dict) else None    # 任务表取不到：「人」「队列」两块报错，其余照常
        ppl = bad or _safe(lambda: people(tasks, now))
        prj = _safe(lambda: projects(now))
    run = _safe(lambda: runners(now))
    return {'时间': _db.ts_str(now), '系统': _safe(system), '人': ppl, '项目': prj, '执行机': run,
            '队列': bad or _safe(lambda: queue_stats(tasks, now)), '资源': _safe(resources_stats), 'AI': _safe(lambda: ai_stats(now)),
            '审计': _safe(lambda: audit_stats(now)), '定时任务': _safe(schedules),
            '概要': {'成员': len(ppl) if isinstance(ppl, list) else 0,
                   '活跃成员': sum(1 for p in ppl if p['在跑'] + p['等回答'] + p['排队']) if isinstance(ppl, list) else 0,
                   '告警': len(run.get('告警') or []) if isinstance(run, dict) else 0}}
