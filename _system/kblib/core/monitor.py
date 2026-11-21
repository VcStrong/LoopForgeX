"""执行机资源监控与告警（REQ-035）：心跳指标入库（15 秒一条，保留 7 天，看板按 1 分钟聚合），按「持续时间」判阈值，
同一告警去重（30 分钟内只通知一次），恢复时发一次「已恢复」。

告警行（alerts 表）的生命周期：
  条件第一次出现 → 记一条 start=0 的「待定」行（since=出现时间）；
  持续够久 → start=now，变成「进行中」并通知管理员；
  条件消失 → 待定的直接删；进行中的 end=now，通知「已恢复」。
派活排除（queue 用）：磁盘 ≥90%、内存可用 <8%、某仓库网络差 —— 取最新一次心跳的值，不等持续时间。
"""
import json
import threading
import urllib.request

from kblib.core import db as _db
from kblib.core.common import uuid_hex

DISK_WARN, DISK_CRIT = 0.80, 0.90
MEM_WARN, MEM_STOP = 0.15, 0.08
CPU_WARN = 1.5
NET_MS, NET_FAIL = 1000, 0.2
SUSTAIN = {'磁盘': 300, '内存': 300, 'CPU': 600, '网络差': 0}
OFFLINE_ALERT = 600        # 心跳丢失 >10 分钟发离线告警
NET_WINDOW = 300
DEDUPE = 1800
KEEP_DAYS = 7


def now():
    return _db.now_ts()


# ---------------------------------------------------------------- 通知（按人路由，REQ-010）
def _webhook(hook, title, text):
    body = json.dumps({'msg_type': 'text', 'content': {'text': f'{title}\n{text}'},
                       'msgtype': 'text', 'text': {'content': f'{title}\n{text}'}}, ensure_ascii=False).encode()
    try:
        req = urllib.request.Request(hook, data=body, headers={'Content-Type': 'application/json'})
        urllib.request.urlopen(req, timeout=10).close()
    except Exception as e:  # noqa: BLE001 通知失败不影响主流程
        print('通知发送失败：', hook[:40], repr(e), flush=True)


def notify_people(users, title, text):
    """发给指定的人：各自「我的 → 通知」里填的 Webhook；再走一次全局通知（本机系统通知 + 通知插件）。"""
    for u in {x for x in users if x}:
        r = _db.user_get(u) or {}
        hook = ((r.get('profile') or {}).get('通知') or {}).get('webhook', '')
        if hook:
            threading.Thread(target=_webhook, args=(hook, title, text), daemon=True).start()
    try:
        from kblib.ai.devsession import _notify
        _notify(title, text)
    except Exception as e:  # noqa: BLE001
        print('通知出错：', repr(e), flush=True)


def admins():
    return [u['name'] for u in _db.user_list() if u.get('sys_role') == '管理员' and u.get('status') != '停用']


def project_owner(project):
    p = _db.project_get(project) if project else None
    return (p or {}).get('owner') or ''


# ---------------------------------------------------------------- 入库
def _num(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def sample_of(hb):
    """心跳 → (cpu=负载/核数, mem=可用比, disk=使用比, net={目标: {延迟ms, 失败率}})。缺的给 None。"""
    c = hb.get('CPU') or {}
    load, cores = _num(c.get('负载1分')), _num(c.get('核数')) or 1
    cpu = load / cores if load is not None else None
    mem = _num((hb.get('内存') or {}).get('可用比'))
    d = hb.get('磁盘') or {}
    free, total = _num(d.get('可用GB')), _num(d.get('总GB'))
    disk = (1 - free / total) if free is not None and total else None
    net = {}
    for x in hb.get('网络') or []:
        if isinstance(x, dict) and x.get('目标'):
            net[str(x['目标'])] = {'延迟ms': _num(x.get('延迟ms'), 0), '失败率': _num(x.get('失败率'), 0)}
    return cpu, mem, disk, net


def ingest(runner, hb):
    """每次心跳调一次：写一条指标、按阈值更新告警。返回 info 里要合并的监控字段。"""
    cpu, mem, disk, net = sample_of(hb)
    t = now()
    _db.insert('metrics', runner=runner, ts=t, cpu=cpu, mem=mem, disk=disk, net=net)
    _resolve(runner, '离线')
    bad = net_bad_targets(runner)
    checks = {
        '磁盘': (disk is not None and disk >= DISK_WARN, '严重' if disk is not None and disk >= DISK_CRIT else '警告',
                 f'磁盘使用 {disk:.0%}' if disk is not None else ''),
        '内存': (mem is not None and mem < MEM_WARN, '严重' if mem is not None and mem < MEM_STOP else '警告',
                 f'内存可用 {mem:.0%}' if mem is not None else ''),
        'CPU': (cpu is not None and cpu > CPU_WARN, '警告', f'负载 / 核数 {cpu:.2f}' if cpu is not None else ''),
        '网络差': (bool(bad), '警告', '、'.join(bad)),
    }
    for kind, (on, level, detail) in checks.items():
        if on:
            _raise(runner, kind, level, detail, SUSTAIN[kind])
        else:
            _resolve(runner, kind)
    return {'指标': {'CPU': cpu, '内存可用比': mem, '磁盘使用比': disk, '网络': net, '时间': t},
            '禁止新副本': disk is not None and disk >= DISK_WARN, '网络差': bad}


def net_bad_targets(runner):
    """5 分钟窗口里平均延迟 >1 秒或失败率 >20% 的目标。"""
    rows = _db.metrics_window(runner, now() - NET_WINDOW)
    agg = {}
    for r in rows:
        for k, v in (r.get('net') or {}).items():
            a = agg.setdefault(k, [0, 0.0, 0.0])
            a[0] += 1
            a[1] += _num(v.get('延迟ms'), 0)
            a[2] += _num(v.get('失败率'), 0)
    return sorted(k for k, (n, ms, fail) in agg.items() if n and (ms / n > NET_MS or fail / n > NET_FAIL))


# ---------------------------------------------------------------- 告警状态机
def _open(runner, kind):
    rs = _db.rows('alerts', {'runner': runner, 'kind': kind}, extra='end IS NULL', order='since DESC', limit=1)
    return rs[0] if rs else None


def _recently_notified(runner, kind):
    rs = _db.rows('alerts', {'runner': runner, 'kind': kind}, extra='notified > ?', args=(now() - DEDUPE,), limit=1)
    return bool(rs)


def _raise(runner, kind, level, detail, sustain):
    t = now()
    a = _open(runner, kind)
    if not a:
        a = {'id': f'A{uuid_hex()[:10]}', 'runner': runner, 'kind': kind, 'level': level, 'since': t, 'start': 0,
             'detail': detail}
        _db.insert('alerts', **a)
    if not a['start']:
        if t - a['since'] < sustain:
            _db.update('alerts', {'id': a['id']}, level=level, detail=detail)
            return
        _db.update('alerts', {'id': a['id']}, start=t, level=level, detail=detail)
        _fire(a['id'], runner, kind, level, detail)
    elif level != a['level']:
        _db.update('alerts', {'id': a['id']}, level=level, detail=detail)
        if level == '严重':
            _fire(a['id'], runner, kind, level, detail, again=True)
    else:
        _db.update('alerts', {'id': a['id']}, detail=detail)


def _name(runner):
    r = _db.get('runners', id=runner)
    return (r or {}).get('name') or runner


def _fire(aid, runner, kind, level, detail, again=False):
    a = _db.get('alerts', id=aid) or {}
    if (a.get('silence_until') or 0) > now():
        return
    if not again and _recently_notified(runner, kind):
        return
    _db.update('alerts', {'id': aid}, notified=now())
    _db.event_add('alert', '', {'id': aid, 'runner': runner, 'kind': kind, 'level': level})
    notify_people(admins(), f'执行机告警（{level}）：{_name(runner)} {kind}', detail or kind)


def _resolve(runner, kind):
    a = _open(runner, kind)
    if not a:
        return
    if not a['start']:
        _db.delete('alerts', {'id': a['id']})
        return
    _db.update('alerts', {'id': a['id']}, end=now())
    if (a.get('silence_until') or 0) <= now():
        notify_people(admins(), f'执行机告警已恢复：{_name(runner)} {kind}', a.get('detail') or '')


def tick():
    """每 5 秒（queue.tick 调）：心跳丢失 >10 分钟发离线告警；清 7 天前的指标、告警。"""
    t = now()
    for r in _db.rows('runners', {'local': 0, 'disabled': 0}):
        if r['last_hb'] and t - r['last_hb'] > OFFLINE_ALERT:
            a = _open(r['id'], '离线')
            if not a:
                aid = f'A{uuid_hex()[:10]}'
                _db.insert('alerts', id=aid, runner=r['id'], kind='离线', level='严重', since=r['last_hb'], start=t,
                           detail=f'最后心跳 {_db.ts_str(r["last_hb"])}')
                _fire(aid, r['id'], '离线', '严重', f'最后心跳 {_db.ts_str(r["last_hb"])}')
    if int(t) % 3600 < 6:
        _db.metrics_prune(KEEP_DAYS)
        _db.delete('alerts', extra='end IS NOT NULL AND end < ?', args=(t - KEEP_DAYS * 86400,))


# ---------------------------------------------------------------- 给派活 / 网页用
def latest(runner):
    rs = _db.rows('metrics', {'runner': runner}, order='ts DESC', limit=1)
    return rs[0] if rs else None


def exclude_reason(runner, repo=''):
    """派活排除：磁盘 ≥90%、内存可用 <8%、需要的仓库网络差。返回原因或 ''。"""
    m = latest(runner)
    if m:
        if m.get('disk') is not None and m['disk'] >= DISK_CRIT:
            return f'磁盘 {m["disk"]:.0%}'
        if m.get('mem') is not None and m['mem'] < MEM_STOP:
            return f'内存可用 {m["mem"]:.0%}'
    if repo and repo in net_bad_targets(runner):
        return f'到 {repo} 网络差'
    return ''


def alerts_view(state='进行中'):
    t = now()
    extra, args = 'start > 0 AND since > ?', [t - KEEP_DAYS * 86400]
    if state == '进行中':
        extra += ' AND end IS NULL'
    elif state == '已恢复':
        extra += ' AND end IS NOT NULL'
    out = []
    for a in _db.rows('alerts', extra=extra, args=tuple(args), order='start DESC'):
        out.append({'id': a['id'], '执行机': _name(a['runner']), '执行机id': a['runner'], '类型': a['kind'], '级别': a['level'],
                    '开始': _db.ts_str(a['start']), '恢复': _db.ts_str(a['end']) if a.get('end') else '',
                    '确认人': a.get('ack_by') or '', '说明': a.get('detail') or '',
                    '静默到': _db.ts_str(a['silence_until']) if (a.get('silence_until') or 0) > t else ''})
    return out


def alert_ack(aid, user, minutes=0):
    a = _db.get('alerts', id=aid)
    if not a:
        from kblib.core.common import KBError
        raise KBError('没有这条告警')
    f = {'ack_by': user}
    if minutes and int(minutes) > 0:
        f['silence_until'] = now() + int(minutes) * 60
    _db.update('alerts', {'id': aid}, **f)
    _db.audit('告警', user, aid, '', '成功', f'确认{"，静默 " + str(minutes) + " 分钟" if minutes else ""}')
    return '已确认' + (f'，{minutes} 分钟内不再通知' if minutes else '')


def metrics_view(runner, hours=24):
    hours = max(1, min(int(hours or 24), 168))
    pts = _db.metrics_agg(runner, now() - hours * 3600, 60)
    return {'点': [{'时间': int(p['b']), 'CPU': p['cpu'], '内存可用比': p['mem'], '磁盘使用比': p['disk'], '网络': p['net'] or {}}
                  for p in pts]}
