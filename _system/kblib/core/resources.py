"""统一资源池（REQ-045 REQ-047 REQ-048）：手机、测试账号、引擎名额共用「资源 + 占用 + 队列」，状态全在数据库。

  资源（resources）：id 形如 phone:<执行机>:<序列号>、acct:<测试账号编号>、eng:<引擎账号编号>；capacity = 允许同时使用数。
  占用（holds）：按会话（任务编号 / 开发会话 / period:<期名>）占；方式 申请 / 自动 / 本人；有闲置上限和占用上限。
  队列（waits）：有匹配但都被占时排队，先来先到；前面的人没拿到的资源，后来的不能插队拿走。
  并发安全：判断和写占用在同一个 BEGIN IMMEDIATE 事务里（db.tx），10 个会话同时抢 1 台只有 1 个拿到。
  调度 tick() 每 5 秒：释放过期 / 闲置超时的占用、队列超时、按先来先到发给队首；每小时清一次过期产物和证据。
  时钟走 db.now_ts()：测试库（_system/测试库 存在）里 set_fake_offset() 可以把时钟拨快（验证 12 小时超时）。
"""
import os
import re
import time

from kblib.core import db as _db
from kblib.core.common import SYS, KBError, uuid_hex

TYPES = ('手机', '测试账号', '引擎名额')
PHONE_IDLE = {'自动': 30 * 60, '申请': 12 * 3600, '本人': 4 * 3600}      # 沿用现状
ACCT_IDLE, ACCT_MAX = 15 * 60, 60 * 60
WAIT_LIMIT = {'手机': 60 * 60, '测试账号': 30 * 60, '引擎名额': 0}      # 排队超时（0 = 不超时）
PHONE_WAIT_PER_USER, PHONE_WAIT_PER_PROJECT = 2, 20
NO_MATCH = '条件无匹配'
_LAST = {'prune': 0}


def now():
    return _db.now_ts()


def set_fake_offset(sec):
    """测试用假时钟：只在测试库启用（REQ-048）。"""
    if not (SYS / '测试库').exists():
        raise KBError('假时钟只在测试库启用（_system/测试库）')
    os.environ['KB_FAKE_CLOCK'] = str(float(sec))


# ---------------------------------------------------------------- 资源登记
def phone_id(runner, serial):
    return f'phone:{runner or "local"}:{serial}'


def sync_runner_phones(runner_id, phones):
    """执行机心跳 / 本机 adb 上报的手机：登记或更新资源行；这次没报的置为不可用（占用保留，手机可能只是拔了一下）。"""
    runner_id = runner_id or 'local'
    seen = set()
    with _db.tx():
        for p in phones or []:
            sn = str(p.get('序列号') or '').strip()
            if not sn:
                continue
            rid = phone_id(runner_id, sn)
            seen.add(rid)
            old = _db.get('resources', id=rid) or {}
            attrs = dict(old.get('attrs') or {})
            attrs.update({k: v for k, v in {'序列号': sn, '型号': p.get('型号') or '', '系统': p.get('系统') or attrs.get('系统') or 'Android',
                                            '版本': str(p.get('版本') or attrs.get('版本') or ''), '品牌': p.get('品牌') or attrs.get('品牌') or ''}.items()})
            _db.upsert('resources', 'id', id=rid, type='手机', provider=p.get('提供方') or ('local' if runner_id == 'local' else 'runner'),
                       runner=runner_id, attrs=attrs, capacity=1, enabled=1)
        for r in _db.rows('resources', {'type': '手机', 'runner': runner_id}):
            if r['id'] not in seen and r['enabled']:
                _db.update('resources', {'id': r['id']}, enabled=0)
    return len(seen)


def put_resource(rid, rtype, attrs, capacity=1, provider='', runner='', enabled=1):
    _db.upsert('resources', 'id', id=rid, type=rtype, provider=provider, runner=runner, attrs=attrs, capacity=max(0, int(capacity)),
               enabled=1 if enabled else 0)


def drop_resource(rid):
    with _db.tx():
        _db.delete('holds', {'resource': rid})
        _db.delete('resources', {'id': rid})


# ---------------------------------------------------------------- 条件匹配
def _ver(s):
    m = re.match(r'\s*(\d+)(?:\.(\d+))?', str(s or ''))
    return (int(m.group(1)), int(m.group(2) or 0)) if m else None


def _match(r, cond):
    a = r.get('attrs') or {}
    for k, v in (cond or {}).items():
        if v in (None, '', [], {}):
            continue
        if k == '版本范围':
            lo, hi = (list(v) + [None, None])[:2] if isinstance(v, (list, tuple)) else (v, v)
            cur = _ver(a.get('版本'))
            if cur is None or (lo not in (None, '') and cur < _ver(lo)) or (hi not in (None, '') and cur[0] > _ver(hi)[0]):
                return False
        elif k == '执行机':
            if r.get('runner') != v:
                return False
        elif k == '提供方':
            if r.get('provider') != v:
                return False
        elif k == '序列号':
            if a.get('序列号') not in (v if isinstance(v, (list, tuple)) else [v]):
                return False
        elif k == '机型':
            if str(v).lower() not in str(a.get('型号') or '').lower():
                return False
        elif k == '标签':
            if not set(v if isinstance(v, (list, tuple)) else [v]) <= set(a.get('标签') or []):
                return False
        elif k in ('系统', '品牌', '项目', '环境'):
            if str(a.get(k) or ('Android' if k == '系统' else '')).lower() != str(v).lower():
                return False
        elif k == '账号':
            if r['id'] != f'eng:{v}' and a.get('账号') != v:
                return False
        elif k == 'id':
            if r['id'] != v and r['id'].split(':', 1)[-1] != v:
                return False
        elif k == 'AI':
            if v and not a.get('允许AI', True):
                return False
    return True


def candidates(rtype, cond, runner=None):
    c = dict(cond or {})
    if runner:
        c['执行机'] = runner
    return [r for r in _db.rows('resources', {'type': rtype, 'enabled': 1}, order='last_used, id') if _match(r, c)]


# ---------------------------------------------------------------- 占用视图
def _limits(rtype, mode):
    """(闲置上限秒, 占用上限秒)"""
    if rtype == '手机':
        return PHONE_IDLE.get(mode, PHONE_IDLE['自动']), 0
    if rtype == '测试账号':
        return ACCT_IDLE, ACCT_MAX
    return 0, 24 * 3600


def _due(h):
    t = []
    if h.get('idle_limit'):
        t.append((h.get('last_active') or h['start']) + h['idle_limit'])
    if h.get('expires'):
        t.append(h['expires'])
    return min(t) if t else 0


def hold_view(h, rtype=''):
    d = _due(h)
    return {'资源': h['resource'], '类型': rtype or _rtype_of(h['resource']), '会话': h.get('session') or '', '期': h.get('period') or '',
            '人': h.get('user') or '', '方式': h.get('mode') or '', '开始': _db.ts_str(h['start']), '到期': _db.ts_str(d) if d else '',
            '项目': h.get('project') or '', **({'信息': h['info']} if h.get('info') else {})}


def _rtype_of(rid):
    return {'phone': '手机', 'acct': '测试账号', 'eng': '引擎名额'}.get(rid.split(':', 1)[0], '')


def holds_of(resource):
    return _db.rows('holds', {'resource': resource}, order='start')


def _used(rid):
    return _db.count('holds', {'resource': rid})


def _free(r):
    return max(0, (r.get('capacity') or 0) - _used(r['id']))


# ---------------------------------------------------------------- 申请
def _res(ok, holds=(), pos=0, mins=0, ticket='', error=''):
    return {'ok': ok, 'holds': [hold_view(h) for h in holds], '位置': pos, '预计分钟': mins, 'ticket': ticket, 'error': error}


def _mine(rtype, cands, session, period):
    """这个会话（手机：同一期的非本人占用也算）已占着的匹配资源。"""
    ids = {r['id'] for r in cands}
    out = []
    for h in _db.rows('holds', {'session': session}) if session else []:
        if h['resource'] in ids:
            out.append(h)
    if rtype == '手机' and period and not out:
        out = [h for h in _db.rows('holds', {'period': period}) if h['resource'] in ids and h['mode'] != '本人'
               and (not session or h['session'] in ('', f'period:{period}', session))]
    return out


def _blocked(rtype, before_id=None):
    """排在前面还没拿到的申请圈住的资源：后来的不能拿（先来先到）。"""
    out = set()
    for w in _db.rows('waits', {'rtype': rtype, 'status': '排队'}, order='id'):
        if before_id is not None and w['id'] >= before_id:
            break
        out |= {r['id'] for r in candidates(rtype, w['cond'])}
    return out


def _take(rtype, rs, session, period, user, project, mode, info=None):
    t = now()
    idle, mx = _limits(rtype, mode)
    out = []
    for r in rs:
        hid = _db.insert('holds', resource=r['id'], session=session, period=period, user=user, mode=mode, start=t,
                         expires=(t + mx) if mx else 0, last_active=t, idle_limit=idle, ticket='', info=info or {}, project=project)
        _db.update('resources', {'id': r['id']}, last_used=t)
        out.append(_db.get('holds', id=hid))
    return out


def _pick(cands, n, skip):
    free = [r for r in cands if r['id'] not in skip and _free(r) > 0]
    free.sort(key=lambda r: (r.get('last_used') or 0, r['id']))     # 最近最少用的先给，轮流
    return free[:n] if len(free) >= n else None


def _position(w):
    mine = {r['id'] for r in candidates(w['rtype'], w['cond'])}
    pos = 0
    for x in _db.rows('waits', {'rtype': w['rtype'], 'status': '排队'}, order='id'):
        if x['id'] > w['id']:
            break
        if x['id'] == w['id'] or mine & {r['id'] for r in candidates(w['rtype'], x['cond'])}:
            pos += 1
    return pos


def _eta(w, pos):
    """按当前占用剩余时间估算要等几分钟。"""
    rem = []
    for r in candidates(w['rtype'], w['cond']):
        for h in holds_of(r['id']):
            d = _due(h)
            rem.append(max(0, d - now()) if d else 30 * 60)
    if not rem:
        return 0
    rem.sort()
    k = min(len(rem) - 1, max(0, pos + (w.get('n') or 1) - 2))
    return int(round(rem[k] / 60)) or 1


def _wait_view(w):
    pos = _position(w)
    return pos, _eta(w, pos)


def claim(rtype, cond, n, session, period, user, project, mode='自动', wait=True, runner=None, info=None):
    """申请资源：整单分配（要么都拿到，要么都不拿）。返回 {ok, holds, 位置, 预计分钟, ticket, error}。"""
    if rtype not in TYPES:
        raise KBError(f'资源类型只能是：{" / ".join(TYPES)}')
    n = max(1, min(int(n or 1), 10))
    cond = dict(cond or {})
    if runner:
        cond['执行机'] = runner
    session = session or (f'period:{period}' if period else '')
    with _db.tx():
        allc = [r for r in _db.rows('resources', {'type': rtype, 'enabled': 1}) if _match(r, {k: v for k, v in cond.items() if k != 'AI'})]
        cands = [r for r in allc if _match(r, cond)]
        if not cands:
            if allc and cond.get('AI'):
                return _res(False, error='线上环境的测试账号默认不给 AI 会话用（项目负责人可以对单个账号放开「允许AI」）')
            return _res(False, error=NO_MATCH)
        if len(cands) < n:
            return _res(False, error=f'{NO_MATCH}：满足条件的只有 {len(cands)} 个，要 {n} 个')
        have = _mine(rtype, cands, session, period)
        for h in have:
            _db.update('holds', {'id': h['id']}, last_active=now())
        if len(have) >= n:
            _done_waits(session, rtype)
            return _res(True, have[:n])
        w = next((x for x in _db.rows('waits', {'session': session, 'rtype': rtype, 'status': '排队'})
                  if x['cond'] == cond), None) if session else None
        if w:
            pos, mins = _wait_view(w)
            return _res(False, have, pos, mins, w['ticket'], f'排队中：第 {pos} 位，预计 {mins} 分钟')
        got = _pick(cands, n - len(have), _blocked(rtype) | {h['resource'] for h in have})
        if got is not None:
            return _res(True, have + _take(rtype, got, session, period, user, project, mode, info))
        busy = sum(1 for r in cands if _free(r) == 0)
        if not wait:
            fake = {'id': 10 ** 12, 'rtype': rtype, 'cond': cond, 'n': n}
            pos = sum(1 for _ in _db.rows('waits', {'rtype': rtype, 'status': '排队'})) + 1
            return _res(False, have, pos, _eta(fake, pos), '', f'都被占用（{busy}/{len(cands)}）')
        if rtype == '手机':
            if user and _db.count('waits', {'rtype': '手机', 'user': user, 'status': '排队'}) >= PHONE_WAIT_PER_USER:
                return _res(False, have, error=f'每人同时最多排 {PHONE_WAIT_PER_USER} 个手机申请')
            if project and _db.count('waits', {'rtype': '手机', 'project': project, 'status': '排队'}) >= PHONE_WAIT_PER_PROJECT:
                return _res(False, have, error=f'本项目手机队列已满（{PHONE_WAIT_PER_PROJECT}），稍后再试')
        t = now()
        lim = WAIT_LIMIT.get(rtype) or 0
        tk = uuid_hex()[:12]
        wid = _db.insert('waits', rtype=rtype, cond=cond, n=n - len(have), session=session, period=period, user=user, project=project,
                         created=t, deadline=(t + lim) if lim else 0, status='排队', granted=[], ticket=tk, mode=mode)
        w = _db.get('waits', id=wid)
        pos, mins = _wait_view(w)
        label = '手机' if rtype == '手机' else rtype
        return _res(False, have, pos, mins, tk, f'等{label}：第 {pos} 位，预计 {mins} 分钟，当前 {busy} 台都被占用'
                    if rtype == '手机' else f'等{label}：第 {pos} 位，预计 {mins} 分钟')


def _done_waits(session, rtype):
    for w in _db.rows('waits', {'session': session, 'rtype': rtype, 'status': '已发放'}):
        _db.update('waits', {'id': w['id']}, status='已取')


def wait_status(ticket):
    w = _db.get('waits', ticket=ticket)
    if not w:
        return None
    out = {'状态': w['status'], 'ticket': ticket, '资源': w.get('granted') or []}
    if w['status'] == '排队':
        out['位置'], out['预计分钟'] = _wait_view(w)
    return out


def cancel_wait(ticket):
    return _db.update('waits', {'ticket': ticket, 'status': '排队'}, status='取消')


# ---------------------------------------------------------------- 发放（队列）
def _grant():
    """按到达顺序给排队的申请分配；拿不到的圈住它的候选资源，后面的不能插队。"""
    blocked = set()
    out = []
    for w in _db.rows('waits', {'status': '排队'}, order='id'):
        cands = [r for r in candidates(w['rtype'], w['cond'])]
        have = _mine(w['rtype'], cands, w['session'], w['period'])
        need = max(0, (w['n'] or 1) - 0)
        got = _pick(cands, need, blocked | {h['resource'] for h in have})
        if got is None:
            blocked |= {r['id'] for r in cands}
            continue
        hs = _take(w['rtype'], got, w['session'], w['period'], w['user'], w['project'], w['mode'] or '自动')
        _db.update('waits', {'id': w['id']}, status='已发放', granted=[h['resource'] for h in hs])
        out.append((w, hs))
        _db.event_add('资源发放', w['project'] or '', {'会话': w['session'], '期': w['period'], '资源': [h['resource'] for h in hs]})
    return out


def tick():
    """5 秒一次：过期 / 闲置超时释放，队列超时，按先来先到发给队首；每小时清一次产物和证据。"""
    t = now()
    released, timed = [], []
    with _db.tx():
        for h in _db.rows('holds'):
            d = _due(h)
            if d and t > d:
                _db.delete('holds', {'id': h['id']})
                released.append(h)
        for w in _db.rows('waits', {'status': '排队'}):
            if w.get('deadline') and t > w['deadline']:
                _db.update('waits', {'id': w['id']}, status='超时')
                timed.append(w)
        granted = _grant()
    if t - _LAST['prune'] > 3600:
        _LAST['prune'] = t
        try:
            artifacts_prune()
            evidence_prune()
        except Exception as e:  # noqa: BLE001 清理失败不影响调度
            print('产物 / 证据清理出错：', repr(e), flush=True)
    return {'释放': len(released), '超时': len(timed), '发放': len(granted)}


def touch(resource, session):
    _db.update('holds', {'resource': resource, 'session': session}, last_active=now())


# ---------------------------------------------------------------- 释放
def release(resource_id, session=None, by='', force=False):
    """释放一个资源的占用（给 session 只放这个会话的）。force：管理员强制收回——写审计、记「被收回」、通知原会话。"""
    with _db.tx():
        hs = [h for h in holds_of(resource_id) if session is None or h['session'] == session]
        if not hs:
            return '没有这个占用（可能已经释放了）'
        for h in hs:
            _db.delete('holds', {'id': h['id']})
            if force:
                _db.insert('recalls', resource=resource_id, session=h['session'], period=h['period'], ts=now(), by=by)
                _db.audit('资源', by or '管理员', resource_id, h.get('project') or '', '成功',
                          f'强制释放；原占用者 {h.get("user") or "?"}（期 {h.get("period") or "—"}，会话 {h.get("session") or "—"}，'
                          f'开始 {_db.ts_str(h["start"])}）')
                _db.event_add('资源收回', h.get('project') or '', {'资源': resource_id, '会话': h['session'], '期': h['period'],
                                                               '人': h.get('user'), '操作人': by})
        _grant()
    if force:
        for h in hs:
            if h.get('period'):
                try:
                    from kblib.core.periods import log, period_dir
                    log(period_dir(h['period']), '资源被收回', f'{resource_id} 被管理员 {by} 强制释放，之后不能再操作它')
                except Exception:  # noqa: BLE001 期不在了也不影响释放
                    pass
    return f'已{"强制" if force else ""}释放 {resource_id}' + (f'（原占用 {len(hs)} 个）' if len(hs) > 1 else '')


def recalled(resource_id, session='', period=''):
    """这个会话 / 期是不是刚被管理员收回了这个资源（24 小时内，且现在没再占着）。"""
    t = now()
    for r in _db.rows('recalls', {'resource': resource_id}, order='ts DESC', limit=5):
        if t - (r['ts'] or 0) > 86400:
            break
        if (session and r['session'] == session) or (period and r['period'] == period):
            if not any((session and h['session'] == session) or (period and h['period'] == period) for h in holds_of(resource_id)):
                return r
    return None


def release_session(session):
    """会话结束 / 取消：放掉它占的全部资源、撤掉它的排队。"""
    if not session:
        return 0
    with _db.tx():
        n = _db.delete('holds', {'session': session})
        _db.update('waits', {'session': session, 'status': '排队'}, status='取消')
        _grant()
    return n


def release_type(session, rtype):
    with _db.tx():
        n = 0
        for h in _db.rows('holds', {'session': session}):
            if _rtype_of(h['resource']) == rtype:
                n += _db.delete('holds', {'id': h['id']})
        _grant()
    return n


def release_runner(runner_id):
    """执行机掉线 2 分钟：它上面手机的占用全部释放（REQ-045）。"""
    with _db.tx():
        n = 0
        for r in _db.rows('resources', {'runner': runner_id}):
            n += _db.delete('holds', {'resource': r['id']})
        _grant()
    return n


# ---------------------------------------------------------------- 总览（📦 资源页）
def overview(rtype=None, project=None):
    out = []
    waits = _db.rows('waits', {'status': '排队'}, order='id')
    wc = {w['id']: {r['id'] for r in candidates(w['rtype'], w['cond'])} for w in waits}
    for r in _db.rows('resources', {'type': rtype} if rtype else None, order='type, id'):
        a = r.get('attrs') or {}
        if project and r['type'] != '手机' and a.get('项目') not in (project, None, ''):
            continue
        hs = holds_of(r['id'])
        if not r['enabled'] and not hs:
            continue
        q = []
        for w in waits:
            if r['id'] in wc[w['id']]:
                pos, mins = _wait_view(w)
                q.append({'位置': pos, '人': w['user'] or '', '期': w['period'] or '', '排队时间': _db.ts_str(w['created']), '预计分钟': mins,
                          '会话': w['session'] or ''})
        out.append({'id': r['id'], '类型': r['type'], '提供方': r['provider'] or '', '执行机': r['runner'] or '', '属性': a,
                    '允许同时使用数': r['capacity'], '可用': bool(r['enabled']), '占用': [hold_view(h, r['type']) for h in hs], '队列': q})
    return out


# ---------------------------------------------------------------- 产物 / 证据清理（REQ-049 REQ-050）
ART_DIR = SYS / 'cache' / '产物'
EVI_DIR = SYS / 'cache' / '证据'
ART_DAYS, ART_KEEP = 7, 20
EVI_DAYS = 14


def artifacts_prune():
    """保留期：7 天内的，或每项目最近 20 个（取多），其余删文件和登记。"""
    t = now()
    gone = 0
    by_proj = {}
    for a in _db.rows('artifacts', order='ts DESC'):
        by_proj.setdefault(a.get('project') or '', []).append(a)
    for items in by_proj.values():
        for i, a in enumerate(items):
            if i < ART_KEEP or t - (a['ts'] or 0) < ART_DAYS * 86400:
                continue
            try:
                if a.get('path'):
                    os.remove(a['path'])
            except OSError:
                pass
            _db.delete('artifacts', {'id': a['id']})
            gone += 1
    return gone


def evidence_prune():
    t = now()
    gone = 0
    for e in _db.rows('evidence', {'pinned': 0}):
        if t - (e['ts'] or 0) > EVI_DAYS * 86400:
            try:
                if e.get('path'):
                    os.remove(e['path'])
            except OSError:
                pass
            _db.delete('evidence', {'id': e['id']})
            gone += 1
    return gone


def artifact_install_check(session, serial):
    """安装只能装到本会话占用的手机（REQ-049）。返回 (能装, 原因)。"""
    hs = [h for h in _db.rows('holds', {'session': session}) if h['resource'].startswith('phone:')
          and h['resource'].rsplit(':', 1)[-1] == serial]
    if hs:
        return True, ''
    other = [h for r in _db.rows('resources', {'type': '手机'}) if (r.get('attrs') or {}).get('序列号') == serial
             for h in holds_of(r['id'])]
    if other:
        return False, f'手机 {serial} 正被{other[0].get("user") or "别人"}（{other[0].get("period") or "—"}）占用，不能装'
    return False, f'手机 {serial} 不是本会话占用的，先申请再装'


def stamp(t):
    return time.strftime('%Y-%m-%d %H:%M', time.localtime(t))
