"""任务队列与派活（REQ-011~REQ-014 REQ-019 REQ-026 REQ-029 REQ-036；流程架构 §3）。

任务：开发、Bug 分析 / 修复、全栈端、插件、运行到手机、构建安装。状态 排队 → 已派 → 运行 ⇄ 等回答 → 完成 / 失败 / 取消。
会话 id = 任务 id（资源占用、会话令牌都按它）。

两种执行：
  本机（REQ-026 单机模式）：没有在线的远程执行机、且调用方给了 local（原来的启动逻辑）时，同进程的「本机执行机」
      （runners.id='local'）立即领走并调用 local()；完成状态靠 tick() 按 payload['local_check'] 看会话还在不在。
  执行机：任务进队列，执行机长轮询 claim；只把任务给「打分最高、此刻正在等活」的执行机。
派活：标签全匹配 → 排除（离线 / 停用 / 排空 / 待升级 / 磁盘 ≥90% / 内存 <8% / 仓库网络差 / 满）→
      打分 40×空闲容量比 + 25×内存余量比 + 15×CPU 余量比 + 10×磁盘余量比 + 10×亲和；亲和机比最高分低 25 分以上放弃亲和；
      同分选最近最少派过的；回队列的任务优先派回原执行机。
派活前依次检查：每人 / 每项目在跑上限 → 预算（accounts.budget_state）→ AI 账号（accounts.pick / acquire）→ 手机（resources.claim）。
"""
import os
import threading
import time
from pathlib import Path

from kblib.core import db as _db
from kblib.core import monitor
from kblib.core.common import KBError, KBNeed, SYS, current_ctx, uuid_hex

PROTO_VERSION = 1
STATUSES = ('排队', '已派', '运行', '等回答', '完成', '失败', '取消')
ACTIVE = ('已派', '运行', '等回答')
ENDED = ('完成', '失败', '取消')
KINDS = ('开发', 'Bug分析', 'Bug修复', '全栈端', '插件', '运行到手机', '构建安装')
QUOTA_DEFAULT = {'每人': 3, '每项目': 8, '队列每项目': 50}
ACK_SEC = 120
OFFLINE_SEC = 105   # 最后一次心跳后 105 秒（心跳 15 秒一次）：保证停掉后 2 分钟内一定标离线（REQ-014 REQ-033）
MAX_RETRY = 5
WAIT_NOTIFY = 600
CLAIM_MAX = 50
HB_STALE = 40               # 心跳 15 秒一次：超过 40 秒没心跳的执行机，长轮询里不再给它派活
POLL_FRESH = 3              # 执行机多少秒内在 claim 里等着，才算「此刻在等活」
LOCAL_ID = 'local'
LOG_DIR = SYS / 'cache' / '任务日志'
WEIGHTS_DEFAULT = {'空闲容量': 40, '内存': 25, 'CPU': 15, '磁盘': 10, '亲和': 10}
AFFINITY_GIVEUP = 25

_LOCAL_FN = {}              # 任务 id → 本机启动函数（超配额排队的本机任务；进程内）
_POLLING = {}               # 执行机 id → 最近一次在 claim 里等活的时间
_LOCK = threading.Lock()
_HOLDER = f'{os.getpid()}-{uuid_hex()[:6]}'


def now():
    return _db.now_ts()


def _publish(ev):
    """SSE：网页进程里有订阅者就推；跨进程靠 events 表。"""
    try:
        from webapp.shared import _publish as pub
        pub(ev)
    except Exception:  # noqa: BLE001 命令行进程里没有网页
        pass


def _ev_task(t):
    _publish({'kind': 'task', 'type': 'task', 'id': t['id'], 'period': t.get('period') or '', 'project': t.get('project') or '',
              '状态': t.get('status')})


def _ev_runner(rid):
    _publish({'kind': 'runner', 'type': 'runner', 'id': rid, 'project': ''})


# ---------------------------------------------------------------- A2 的接口（还没有时不限制）
def _accounts():
    try:
        from kblib.ai import accounts
        return accounts
    except ImportError:
        return None


def _resources():
    try:
        from kblib.core import resources
        return resources
    except ImportError:
        return None


def _call(mod, fn, *a, default=None, **kw):
    f = getattr(mod, fn, None) if mod else None
    if not f:
        return default
    try:
        return f(*a, **kw)
    except Exception as e:  # noqa: BLE001 资源池 / 账号池出错不拖垮派活
        print(f'queue: {fn} 出错', repr(e), flush=True)
        return default


def _release(tid):
    _call(_accounts(), 'release_task', tid)
    _call(_resources(), 'release_session', tid)
    try:
        from kblib.infra import deploy
        deploy.session_token_revoke(tid)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------- 配额（REQ-019）
def quota():
    v, _ = _db.kv_get('quota', {})
    return {**QUOTA_DEFAULT, **{k: int(x) for k, x in (v or {}).items() if k in QUOTA_DEFAULT and int(x) >= 1}}


def quota_save(patch, by=''):
    q = quota()
    for k in QUOTA_DEFAULT:
        if k in patch and patch[k] is not None:
            try:
                n = int(patch[k])
            except (TypeError, ValueError):
                raise KBError(f'{k} 要是整数')
            if n < 1:
                raise KBError(f'{k} 至少是 1')
            q[k] = n
    _db.kv_set('quota', q, by=by)
    _db.audit('配额', by, 'quota', '', '成功', str(q))
    return f'已保存：每人同时 {q["每人"]} 个、每项目同时 {q["每项目"]} 个、每项目最多排队 {q["队列每项目"]} 个'


def weights():
    v, _ = _db.kv_get('dispatch_weights', {})
    return {**WEIGHTS_DEFAULT, **(v or {})}


# ---------------------------------------------------------------- 执行机
def ensure_local():
    """同进程的本机执行机（单机模式）。"""
    if not _db.get('runners', id=LOCAL_ID):
        _db.upsert('runners', 'id', id=LOCAL_ID, name='本机', status='在线', local=1, capacity=99, created=_db.ts_str(),
                   last_hb=now(), auto_tags=['本机'])
    else:
        _db.update('runners', {'id': LOCAL_ID}, last_hb=now())


def runner(rid):
    return _db.get('runners', id=rid)


def runner_state(r):
    """网页显示的状态：在线 / 离线 / 排空中 / 已排空 / 停用 / 待升级。"""
    if r.get('disabled'):
        return '停用'
    if r.get('status') in ('离线', '待升级'):
        return r['status']
    if r.get('drain') == 1:
        return '排空中'
    if r.get('drain') == 2:
        return '已排空'
    return r.get('status') or '在线'


def remote_online():
    t = now()
    return [r for r in _db.rows('runners', {'local': 0, 'disabled': 0})
            if r.get('status') == '在线' and t - (r.get('last_hb') or 0) <= OFFLINE_SEC]


def running_on(rid):
    return _db.count('tasks', {'runner': rid}, extra=f'status IN ({",".join("?" * len(ACTIVE))})', args=ACTIVE)


def tags_of(r):
    return set(r.get('auto_tags') or []) | set(r.get('custom_tags') or [])


def exclude_reason(r, task):
    """不能派给这台的原因；'' = 合格（REQ-012 REQ-035 REQ-036）。"""
    st = runner_state(r)
    if st != '在线':
        return st
    if now() - (r.get('last_hb') or 0) > OFFLINE_SEC:
        return '离线'
    p = task.get('payload') or {}
    need = set(p.get('标签') or [])
    miss = need - tags_of(r)
    if miss:
        return '缺标签 ' + '、'.join(sorted(miss))
    if running_on(r['id']) >= int(r.get('capacity') or 0):
        return '已满'
    why = monitor.exclude_reason(r['id'], p.get('仓库') or '')
    if why:
        return why
    return ''


def _ratio(v, default=1.0):
    return default if v is None else max(0.0, min(1.0, v))


def score(r, task):
    """负载打分（REQ-036），返回 (总分, 明细)。"""
    w = weights()
    cap = max(int(r.get('capacity') or 0), 1)
    free = (cap - running_on(r['id'])) / cap
    m = monitor.latest(r['id']) or {}
    mem = _ratio(m.get('mem'))
    cpu = 1 - _ratio(m.get('cpu'), 0.0)
    disk = 1 - _ratio(m.get('disk'), 0.0)
    p = task.get('payload') or {}
    aff = 0.0
    for c in (r.get('info') or {}).get('副本') or []:
        if p.get('仓库') and c.get('仓库') == p.get('仓库') or p.get('马甲线') and c.get('马甲线') == p.get('马甲线'):
            aff = 1.0
            break
    d = {'空闲容量': w['空闲容量'] * _ratio(free), '内存': w['内存'] * mem, 'CPU': w['CPU'] * _ratio(cpu),
         '磁盘': w['磁盘'] * _ratio(disk), '亲和': w['亲和'] * aff}
    d = {k: round(v, 2) for k, v in d.items()}
    return round(sum(d.values()), 2), d


def rank(task, runners=None):
    """所有执行机的候选明细 + 选中的那台（只在合格的里选）。"""
    rs = runners if runners is not None else _db.rows('runners', {'local': 0})
    cands, ok = [], []
    for r in rs:
        why = exclude_reason(r, task)
        if why:
            cands.append({'执行机': r.get('name') or r['id'], 'id': r['id'], '总分': 0, '明细': {}, '排除原因': why})
            continue
        s, d = score(r, task)
        c = {'执行机': r.get('name') or r['id'], 'id': r['id'], '总分': s, '明细': d, '排除原因': ''}
        cands.append(c)
        ok.append((s, r, c))
    if not ok:
        return None, cands
    top = max(s for s, _, _ in ok)
    pick = None
    prev = task.get('prev_runner')
    for s, r, c in ok:                                   # 回队列的：原执行机合格且不比最高分低 25 分以上就派回
        if prev and r['id'] == prev and s >= top - AFFINITY_GIVEUP:
            pick = r
    if not pick:
        aff = [(s, r) for s, r, c in ok if c['明细'].get('亲和') and s >= top - AFFINITY_GIVEUP]
        pool = aff or [(s, r) for s, r, _ in ok]
        best = max(s for s, _ in pool)
        pick = min((r for s, r in pool if s == best), key=lambda r: r.get('last_assigned') or 0)
    return pick, cands


# ---------------------------------------------------------------- 任务
def _new_id():
    return 'T' + time.strftime('%m%d%H%M%S') + uuid_hex()[:4]


def _who():
    c = current_ctx() or {}
    if c.get('user'):
        return c['user']
    from kblib.core.users import local_user
    return local_user()


def _project_of(period, project=None):
    if project:
        return project
    if period:
        r = _db.period_row(period)
        if r and r.get('project'):
            return r['project']
    c = current_ctx() or {}
    if c.get('project'):
        return c['project']
    from kblib.core.users import ensure_default_project
    return ensure_default_project()


def task(tid):
    t = _db.get('tasks', id=tid)
    if not t:
        raise KBError('没有这个任务')
    return t


def _active_count(**where):
    return _db.count('tasks', where, extra=f'status IN ({",".join("?" * len(ACTIVE))})', args=ACTIVE)


def _queued(project=None):
    return _db.task_queue_rows(['排队'], project)


def submit(kind, period, payload=None, *, local=None, user=None, project=None):
    """建任务（REQ-013）。返回 {'task': 视图, 'result': 本机直接执行的返回值或 None}。队列满抛 KBNeed（409）。"""
    if kind not in KINDS:
        raise KBError(f'未知的任务类型：{kind}')
    user = user or _who()
    project = _project_of(period, project)
    q = quota()
    if len(_queued(project)) >= q['队列每项目']:
        raise KBNeed(f'队列已满（{q["队列每项目"]}），稍后再试或先停掉一些', {'队列上限': q['队列每项目']})
    p = dict(payload or {})
    remote = bool(remote_online()) and not p.get('只在本机')
    use_local = local is not None and not remote and os.environ.get('KB_NO_LOCAL_RUNNER') != '1'   # 容器里的控制面可关掉本机执行
    p['本机'] = use_local
    tid = _new_id()
    t = now()
    _db.insert('tasks', id=tid, type=kind, period=period or '', project=project, initiator=user, status='排队', payload=p,
               created=t, updated=t, waiting_since=t)
    if not use_local:
        refresh_waits([task(tid)])
        tk = task(tid)
        _ev_task(tk)
        return {'task': view(tk), 'result': None}
    ensure_local()
    why = _quota_block(task(tid))
    if why:                                               # 本机也守配额：超了排队，名额空出来 tick 再起
        _LOCAL_FN[tid] = local
        _db.update('tasks', {'id': tid}, wait_reason=why)
        tk = task(tid)
        _ev_task(tk)
        return {'task': view(tk), 'result': None}
    res = _run_local(tid, local)
    return {'task': view(task(tid)), 'result': res}


def _run_local(tid, fn):
    if not _db.task_cas(tid, ['排队'], status='运行', runner=LOCAL_ID, updated=now(), wait_reason=''):
        return None
    try:
        res = fn()
    except KBError:
        _db.delete('tasks', {'id': tid})               # 没起来（如已有会话在跑）：不留记录，原样报给网页
        raise
    except Exception as e:
        _finish(tid, '失败', {'失败原因': repr(e)})
        raise
    tk = task(tid)
    if not (tk.get('payload') or {}).get('local_check'):
        _finish(tid, '完成', {})                        # 同步执行完的（插件运行等）
    else:
        _ev_task(task(tid))
    return res


def _quota_block(t):
    q = quota()
    n = _active_count(initiator=t['initiator'])
    if n >= q['每人']:
        return f'你已有 {n} 个在跑'
    n = _active_count(project=t['project'])
    if n >= q['每项目']:
        return f'本项目已有 {n} 个在跑'
    return ''


def _finish(tid, result, extra):
    t = task(tid)
    if t['status'] in ENDED:
        return False
    res = {**(t.get('result') or {}), **extra, '结果': result, '结束时间': _db.ts_str()}
    _db.update('tasks', {'id': tid}, status=result, result=res, updated=now(), cancel=0)
    _release(tid)
    _LOCAL_FN.pop(tid, None)
    _ev_task(task(tid))
    return True


def requeue(tid, why, prev=None):
    """回队列（掉线 / 确认超时 / 停用 / 外部原因重试）：放掉资源，记原执行机以便派回。"""
    t = task(tid)
    f = {'status': '排队', 'runner': None, 'updated': now(), 'wait_reason': why, 'waiting_since': now(), 'notified_wait': 0,
         'ack_deadline': 0}
    if prev or t.get('runner'):
        f['prev_runner'] = prev or t['runner']
    if not _db.task_cas(tid, list(ACTIVE) + ['排队'], **f):
        return False
    _release(tid)
    _record(t, f'任务回到队列：{why}')
    _ev_task(task(tid))
    return True


def _record(t, text):
    """写进期的记录（任务相关的切换、重试）。"""
    if not t.get('period'):
        return
    try:
        from kblib.core.periods import log, period_dir
        log(period_dir(t['period']), '任务', f'{t["id"]}（{t["type"]}）{text}')
    except Exception:  # noqa: BLE001 期删了等
        pass


def cancel(tid, ctx=None):
    """停止（REQ-013）：排队中的直接取消；运行中的通知执行机结束会话（下一次心跳 / 回传返回），本机的直接停。"""
    t = task(tid)
    _can_cancel(t, ctx)
    if t['status'] in ENDED:
        return f'任务已经是「{t["status"]}」'
    if t['status'] == '排队':
        _finish(tid, '取消', {'取消人': (ctx or {}).get('user', '')})
        return '已取消'
    if t.get('runner') == LOCAL_ID:
        _local_stop(t)
        _finish(tid, '取消', {'取消人': (ctx or {}).get('user', '')})
        return '已停止'
    _db.update('tasks', {'id': tid}, cancel=1, updated=now())
    return '已通知执行机停止，稍后标为「取消」'


def _can_cancel(t, ctx):
    ctx = ctx if ctx is not None else (current_ctx() or {})
    u = ctx.get('user', '')
    if not ctx or not ctx.get('via') and not u:
        return
    from kblib.core import authz
    if authz.is_admin(u) or t['initiator'] == u:
        return
    r = _db.period_row(t['period']) if t.get('period') else None
    if r and r.get('owner') == u or authz.project_role(u, t['project']) == '负责人':
        return
    raise authz.Denied('只有发起人、期负责人或管理员能停止这个任务')


# ---------------------------------------------------------------- 视图
def _positions():
    return {t['id']: i for i, t in enumerate(_queued(), 1)}


def view(t, pos=None):
    pos = _positions() if pos is None else pos
    p = t.get('payload') or {}
    r = runner(t['runner']) if t.get('runner') else None
    res = t.get('result') or {}
    return {'id': t['id'], '类型': t['type'], '期': t.get('period') or '', '项目': t.get('project') or '',
            '发起人': t.get('initiator') or '', '状态': t['status'], '排队位置': pos.get(t['id']) if t['status'] == '排队' else None,
            '等待原因': t.get('wait_reason') or '' if t['status'] == '排队' else '',
            '执行机': (r or {}).get('name') or t.get('runner') or None, '重试次数': t.get('retries') or 0,
            '创建时间': _db.ts_str(t['created']), '更新时间': _db.ts_str(t['updated']),
            '引擎账号': p.get('引擎账号名') or '', '降级模型': p.get('降级模型') or '', '本机': bool(p.get('本机')),
            '结果': res, '可停止': t['status'] not in ENDED}


def queue_view(ctx, project='', period='', mine=False, status=''):
    from kblib.core import authz
    u = (ctx or {}).get('user', '')
    vis = None if authz.is_admin(u) else set(authz.visible_projects(u))
    out, pos = [], _positions()
    for t in _db.rows('tasks', order='created DESC', limit=500):
        if vis is not None and t.get('project') not in vis:
            continue
        if project and t.get('project') != project or period and t.get('period') != period:
            continue
        if mine and t.get('initiator') != u or status and t['status'] != status:
            continue
        out.append(view(t, pos))
    return out


def period_tasks(period):
    pos = _positions()
    return [view(t, pos) for t in _db.rows('tasks', {'period': period}, order='created DESC', limit=50)]


def score_view(tid):
    t = task(tid)
    sc = t.get('score') or {}
    if not sc or t['status'] == '排队':
        pick, cands = rank(t)
        sc = {'选中': (pick or {}).get('name') or '', '候选': cands, '时间': _db.ts_str(), '预估': True}
    return sc


# ---------------------------------------------------------------- 等待原因（REQ-013 REQ-019 REQ-036 REQ-018 REQ-040）
def _hard_block(t):
    """和哪台执行机无关的阻塞：配额、预算、AI 账号。返回 (原因, 账号, 降级模型)。"""
    why = _quota_block(t)
    if why:
        return why, None, ''
    acc_mod = _accounts()
    cheap = ''
    bs = _call(acc_mod, 'budget_state', t['initiator'], t['project'], default=None)
    if bs and not bs[0]:
        if bs[2]:
            cheap = bs[2]
        else:
            return bs[1] or '本项目今日 AI 预算已用完', None, ''
    if (t.get('payload') or {}).get('不用引擎'):
        return '', None, cheap
    pk = _call(acc_mod, 'pick', t, default=None)
    if pk is not None:
        acc, why = pk
        if not acc:
            return why or '本项目没有可用 AI 账号，请项目负责人在 设置 → AI 账号 配置', None, ''
        return '', acc, cheap
    return '', None, cheap


def _no_runner_reason(t, cands):
    p = t.get('payload') or {}
    need = '、'.join(p.get('标签') or [])
    if not cands:
        return '没有在线的执行机' + (f'（需要 {need}）' if need else '')
    whys = [c['排除原因'] for c in cands]
    if all(w.startswith('缺标签') for w in whys):
        return f'没有 {need} 的执行机'
    if any(w.startswith('内存') for w in whys):
        return f'没有内存充足的{(" " + need) if need else ""}执行机'
    if any(w.startswith('磁盘') for w in whys):
        return f'没有磁盘充足的{(" " + need) if need else ""}执行机'
    if all(w in ('离线', '停用', '排空中', '已排空', '待升级') for w in whys):
        return f'没有在线的{(" " + need) if need else ""}执行机'
    if any(w == '已满' for w in whys):
        return '执行机都满了，等空出来'
    return '没有合格的执行机：' + '；'.join(f'{c["执行机"]} {c["排除原因"]}' for c in cands[:3])


def refresh_waits(ts=None):
    t0 = now()
    for t in ts if ts is not None else _queued():
        p = t.get('payload') or {}
        if p.get('本机'):
            why = _quota_block(t) or t.get('wait_reason') or ''
        else:
            why, _, _ = _hard_block(t)
            if not why:
                pick, cands = rank(t)
                why = '' if pick else _no_runner_reason(t, cands)
                if pick and not _POLLING:
                    why = '等执行机领取'
        if why != (t.get('wait_reason') or ''):
            _db.update('tasks', {'id': t['id']}, wait_reason=why)
        if why and not t.get('notified_wait') and t0 - (t.get('waiting_since') or t['created']) >= WAIT_NOTIFY:
            _db.update('tasks', {'id': t['id']}, notified_wait=1)
            owner = monitor.project_owner(t['project'])
            monitor.notify_people([owner], f'任务排队超过 10 分钟：{t.get("period") or t["type"]}', f'在等：{why}（任务 {t["id"]}）')


# ---------------------------------------------------------------- 执行机协议
def register(reg, body):
    """注册（REQ-011）或 --rebind 换控制面（REQ-034：保留原 ID / 标签，旧令牌作废）。返回 {id, token, 标签}。"""
    from kblib.infra import deploy
    name = (body.get('name') or reg.get('name') or '').strip() or '执行机'
    rid = (body.get('rebind_id') or '').strip()
    f = {'name': name, 'version': str(body.get('version') or ''), 'proto': int(body.get('协议版本') or PROTO_VERSION),
         'auto_tags': [str(x) for x in body.get('自动标签') or []], 'last_hb': now(), 'status': '在线', 'local': 0,
         'disabled': 0, 'drain': 0}
    old = _db.get('runners', id=rid) if rid else None
    if not old:
        rid = rid or ('R' + uuid_hex()[:8])
        f.update(created=_db.ts_str(), capacity=int(body.get('容量') or 2))
    info = dict((old or {}).get('info') or {})
    info['自检'] = body.get('自检') or []
    f['info'] = info
    if f['proto'] != PROTO_VERSION:
        f['status'] = '待升级'
    _db.upsert('runners', 'id', id=rid, **f)
    tok = deploy.runner_token(rid)
    _db.audit('执行机', reg.get('user') or '注册令牌', rid, '', '成功', ('重新绑定' if old else '注册') + f' {name}')
    _ev_runner(rid)
    r = runner(rid)
    return {'id': rid, 'token': tok, '标签': sorted(tags_of(r))}


def heartbeat(rid, body):
    r = runner(rid)
    info = dict(r.get('info') or {})
    for k in ('副本', '手机', '磁盘', 'CPU', '内存', '网络', '会话'):
        if k in body:
            info[k] = body[k]
    if '自检' in body:
        info['自检'] = body['自检']
    info.update(monitor.ingest(rid, body))
    f = {'last_hb': now(), 'info': info}
    if body.get('自动标签') is not None:
        f['auto_tags'] = [str(x) for x in body['自动标签']]
    if r.get('status') == '离线':
        f['status'] = '在线'
    _db.update('runners', {'id': rid}, **f)
    _call(_resources(), 'sync_runner_phones', rid, body.get('手机') or [])
    if r.get('status') == '离线':
        _ev_runner(rid)
    cancel_ids = [t['id'] for t in _db.rows('tasks', {'runner': rid, 'cancel': 1}) if t['status'] in ACTIVE]
    # 执行机说自己在跑、控制面已不认（已回队列 / 已结束）的，也让它停
    mine = {t['id'] for t in _db.rows('tasks', {'runner': rid}) if t['status'] in ACTIVE}
    for s in body.get('会话') or []:
        if isinstance(s, dict) and s.get('task') and s['task'] not in mine:
            cancel_ids.append(s['task'])
    from kblib.infra import webport                 # 网页里改了端口：告诉执行机新端口，它自己改连（经反向代理的不改）
    return {'cancel': sorted(set(cancel_ids)), '状态': runner_state(runner(rid)), **webport.note_runner(rid)}


def _try_assign(rid, t):
    """把 t 派给 rid：占账号名额 / 手机，CAS 成「已派」。成功返回下发内容。"""
    why, acc, cheap = _hard_block(t)
    if why:
        return None
    p = dict(t.get('payload') or {})
    res_mod = _resources()
    holds = []
    if p.get('手机'):
        need = p['手机'] if isinstance(p['手机'], dict) else {'n': 1}
        r = _call(res_mod, 'claim', '手机', need.get('cond') or {}, int(need.get('n') or 1), t['id'], t.get('period') or '',
                  t['initiator'], t['project'], mode='自动', wait=False, runner=rid, default=None)
        if r is not None and not r.get('ok'):
            w = r.get('error') or f'等手机：第 {r.get("位置") or 1} 位'
            _db.update('tasks', {'id': t['id']}, wait_reason=w)
            return None
        holds = (r or {}).get('holds') or []
    if acc:
        ok = _call(_accounts(), 'acquire', t['id'], acc.get('id'), default=True)
        if not ok:
            _call(res_mod, 'release_session', t['id'])
            _db.update('tasks', {'id': t['id']}, wait_reason=f'等 AI 账号 {acc.get("名称") or acc.get("name") or acc.get("id")} 的名额')
            return None
        p['引擎账号id'] = acc.get('id')
        p['引擎账号名'] = acc.get('名称') or acc.get('name') or acc.get('id')
    if cheap:
        p['降级模型'] = cheap
    p['已占资源'] = holds
    pick, cands = rank(t)
    sc = {'选中': (runner(rid) or {}).get('name') or rid, '候选': cands, '时间': _db.ts_str()}
    if not _db.task_cas(t['id'], ['排队'], status='已派', runner=rid, ack_deadline=now() + ACK_SEC, updated=now(),
                        wait_reason='', score=sc, payload=p, account=(acc or {}).get('id')):
        _release(t['id'])
        return None
    _db.update('runners', {'id': rid}, last_assigned=now())
    if p.get('引擎账号名') and t.get('account') and t['account'] != p.get('引擎账号id'):
        _record(t, f'换 AI 账号：{p["引擎账号名"]}')
    tk = task(t['id'])
    _ev_task(tk)
    return _dispatch_body(tk, rid)


def _dispatch_body(t, rid):
    from kblib.infra import deploy, vault
    p = t.get('payload') or {}
    tok = deploy.session_token(t['initiator'], t['project'], t.get('period') or '', t['id'], engine=p.get('引擎') or '')
    acc = None
    if p.get('引擎账号id'):
        blob = _call(_accounts(), 'login_blob', p['引擎账号id'], t['initiator'], default={}) or {}
        acc = {'id': p['引擎账号id'], '名称': p.get('引擎账号名') or '', '登录态': blob}
    try:
        creds = vault.session_creds(t['initiator'], t['project'], session=t['id'])
    except Exception as e:  # noqa: BLE001
        creds = {'git': {'方式': '缺', '缺': f'读凭据出错：{e}'}}
    body = {'id': t['id'], '类型': t['type'], '期': t.get('period') or '', '项目': t['project'], '发起人': t['initiator'],
            '仓库': p.get('仓库') or '', '分支': p.get('分支') or '', '马甲线': p.get('马甲线') or '', '引擎': p.get('引擎') or '',
            '提示词': p.get('提示词') or '', 'sessionToken': tok, '凭据': creds, '确认期限': _db.ts_str(t['ack_deadline']),
            '引擎账号': acc, '已占资源': p.get('已占资源') or [], '标签': p.get('标签') or []}
    for k in ('命令', '模型', '降级模型', '参数', '手机'):
        if p.get(k):
            body[k] = p[k]
    return body


def claim(rid, body, wait=None):
    """长轮询领活（REQ-013 REQ-029）。协议不一致抛 KBNeed 409；超时返回 None。"""
    proto = int(body.get('协议版本') or PROTO_VERSION)
    if proto != PROTO_VERSION:
        _db.update('runners', {'id': rid}, status='待升级', proto=proto)
        _ev_runner(rid)
        from kblib.steward.update import code_version
        raise KBNeed(f'执行机协议版本 {proto} 与控制面 {PROTO_VERSION} 不一致，请升级执行机',
                     {'需要协议版本': PROTO_VERSION, '需要版本': code_version()[:12]})
    r = runner(rid)
    if r.get('status') == '待升级':
        _db.update('runners', {'id': rid}, status='在线', proto=proto)
    _db.update('runners', {'id': rid}, last_hb=now())   # 请求到达算一次活着；长轮询等待中不再刷，否则被杀的执行机挂着的请求会把离线拖后 50 秒
    wait = CLAIM_MAX if wait is None else max(0, min(float(wait), CLAIM_MAX))
    end = time.time() + wait
    while True:
        _POLLING[rid] = time.time()
        if now() - (runner(rid).get('last_hb') or 0) > HB_STALE:   # 心跳停了（进程被杀 / 冻结）还挂着的长轮询不再派活
            _POLLING.pop(rid, None)
            return None
        got = _claim_once(rid)
        if got or time.time() >= end:
            _POLLING.pop(rid, None) if not got else None
            return got
        time.sleep(min(1.0, max(0.05, end - time.time())))


def _polling_ids():
    t = time.time()
    return {k for k, v in list(_POLLING.items()) if t - v <= POLL_FRESH}


def _claim_once(rid):
    r = runner(rid)
    if exclude_reason(r, {'payload': {}}) not in ('',):
        return None
    with _LOCK:
        live = _polling_ids() | {rid}
        cand_runners = [x for x in _db.rows('runners', {'local': 0}) if x['id'] in live]
        for t in _queued():
            p = t.get('payload') or {}
            if p.get('本机'):
                continue
            if exclude_reason(r, t):
                continue
            best, _ = rank(t)                        # 全体合格机里的最高分：它此刻也在等活就只给它
            if best and best['id'] != rid and (best['id'] in live or now() - (t.get('waiting_since') or 0) < POLL_FRESH):
                continue
            pick, _ = rank(t, cand_runners)
            if not pick or pick['id'] != rid:
                continue
            got = _try_assign(rid, t)
            if got:
                return got
    return None


def _mine(rid, tid):
    t = task(tid)
    if t.get('runner') != rid:
        raise KBNeed('这个任务已不归这台执行机（已回队列或改派）', {'状态': t['status']})
    return t


def ack(rid, tid, copy=None):
    t = _mine(rid, tid)
    if t['status'] != '已派':
        raise KBNeed(f'任务状态是「{t["status"]}」，不能确认', {'状态': t['status']})
    p = dict(t.get('payload') or {})
    if copy:
        p['副本'] = copy
    if not _db.task_cas(tid, ['已派'], status='运行', payload=p, updated=now(), ack_deadline=0):
        raise KBNeed('确认超时，任务已回队列', {})
    _ev_task(task(tid))
    return '已确认'


def log_file(tid):
    return LOG_DIR / f'{tid}.log'


def event(rid, tid, body):
    t = _mine(rid, tid)
    if t['status'] in ENDED:
        return {'cancel': True}
    txt = body.get('日志') or ''
    if txt:
        f = log_file(tid)
        f.parent.mkdir(parents=True, exist_ok=True)
        with open(f, 'a', encoding='utf-8') as fh:
            fh.write(txt)
        _db.update('tasks', {'id': tid}, log_size=f.stat().st_size)
    st = body.get('状态')
    if st in ('运行', '等回答') and st != t['status']:
        _db.task_cas(tid, ['已派', '运行', '等回答'], status=st, updated=now(), ack_deadline=0)
    tk = task(tid)
    _publish({'kind': 'task', 'type': 'task', 'id': tid, 'period': tk.get('period') or '', 'project': tk.get('project') or '',
              '状态': tk['status'], '日志': bool(txt)})
    return {'cancel': bool(tk.get('cancel'))}


def log_tail(tid, offset=0, limit=200_000):
    f = log_file(tid)
    if not f.exists():
        return {'日志': '', '偏移': 0}
    size = f.stat().st_size
    offset = max(0, min(int(offset or 0), size))
    if size - offset > limit:
        offset = size - limit
    with open(f, 'rb') as fh:
        fh.seek(offset)
        data = fh.read()
    return {'日志': data.decode('utf-8', 'replace'), '偏移': size}


EXTERNAL_HINTS = ('429', '529', '503', 'rate limit', 'overloaded', 'timeout', 'timed out', '网络', 'connection', '用量', 'quota')
LOGIN_HINTS = ('401', '登录失效', 'unauthorized', 'invalid api key', '账号到期')


def done(rid, tid, body):
    t = _mine(rid, tid)
    result = body.get('结果') or ''
    if result not in ('完成', '失败', '取消'):
        raise KBError('结果要是 完成 / 失败 / 取消')
    if t['status'] in ENDED:
        return f'任务已经是「{t["status"]}」'
    p = t.get('payload') or {}
    usage = body.get('用量') or {}
    if usage:
        _call(_accounts(), 'record_usage', t, usage)
    why = body.get('失败原因') or ''
    low = why.lower()
    if p.get('引擎账号id') and result == '失败':
        code = next((h for h in ('429', '529', '503', '401', '403') if h in low), '')
        if code or any(h in low for h in LOGIN_HINTS):
            _call(_accounts(), 'report_error', p['引擎账号id'], code or '登录失效', why[:500])
    extra = {'退出码': body.get('退出码'), '失败原因': why, '提交': body.get('提交') or [],
             '副本有未推送提交': bool(body.get('副本有未推送提交')), '用量': usage}
    if t.get('cancel') and result != '完成':
        result = '取消'
    external = result == '失败' and (body.get('外部原因') or any(h in low for h in EXTERNAL_HINTS))
    if external:
        if (t.get('retries') or 0) < MAX_RETRY:
            _db.update('tasks', {'id': tid}, retries=(t.get('retries') or 0) + 1, result={**(t.get('result') or {}), **extra})
            requeue(tid, f'外部原因失败，自动重试（第 {(t.get("retries") or 0) + 1} 次）：{why[:80]}', prev=rid)
            return '外部原因失败，已回队列重试'
        extra['失败原因'] = f'外部原因重试 {MAX_RETRY} 次仍失败：{why}'
        _fail_card(t, extra['失败原因'])
    _finish(tid, result, extra)
    _record(t, f'在执行机 {(runner(rid) or {}).get("name") or rid} 上{result}' + (f'：{why[:120]}' if why else ''))
    return f'已记为「{result}」'


def _fail_card(t, why):
    owner = ''
    if t.get('period'):
        owner = (_db.period_row(t['period']) or {}).get('owner') or ''
    owner = owner or monitor.project_owner(t['project'])
    monitor.notify_people([owner, t['initiator']], f'任务失败：{t.get("period") or t["type"]}', f'{t["id"]} {why[:300]}')
    if t.get('period'):
        try:
            from kblib.ai.devices import add_issue
            add_issue(t['period'], f'任务 {t["id"]}（{t["type"]}）重试 {MAX_RETRY} 次仍失败', why[:500], need='看日志后重新发起')
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------- 管理（REQ-020）
def runner_update(rid, body, by=''):
    r = runner(rid)
    if not r:
        raise KBError('没有这台执行机')
    if rid == LOCAL_ID and body.get('动作') in ('停用', '删除'):
        raise KBError('本机执行机不能停用 / 删除')
    f, did = {}, []
    if isinstance(body.get('自定义标签'), list):
        f['custom_tags'] = [str(x).strip() for x in body['自定义标签'] if str(x).strip()]
        did.append('标签')
    if body.get('容量') is not None:
        try:
            cap = int(body['容量'])
        except (TypeError, ValueError):
            raise KBError('容量要是整数')
        if cap < 0:
            raise KBError('容量不能小于 0')
        f['capacity'] = cap
        did.append(f'容量 {cap}')
    act = body.get('动作') or ''
    from kblib.infra import deploy
    if act == '启用':  # noqa: SIM114
        f.update(disabled=0, drain=0)
    elif act == '排空':
        f['drain'] = 1 if running_on(rid) else 2
    elif act == '停用':
        f.update(disabled=1)
        _db.token_revoke(kind='执行机', runner=rid)
    elif act == '删除':
        for t in _db.rows('tasks', {'runner': rid}):
            if t['status'] in ACTIVE:
                requeue(t['id'], '执行机被删除', prev='')
        _db.token_revoke(kind='执行机', runner=rid)
        _call(_resources(), 'release_runner', rid)
        _db.delete('runners', {'id': rid})
        _db.audit('执行机', by, rid, '', '成功', '删除')
        _ev_runner(rid)
        return '已删除'
    elif act:
        raise KBError('动作要是 启用 / 排空 / 停用 / 删除')
    if f:
        _db.update('runners', {'id': rid}, **f)
    if act == '停用':
        for t in _db.rows('tasks', {'runner': rid}):
            if t['status'] in ACTIVE:
                requeue(t['id'], '执行机被停用')
        _call(_resources(), 'release_runner', rid)
    if act:
        did.append(act)
    _db.audit('执行机', by, rid, '', '成功', '、'.join(did))
    _ev_runner(rid)
    return '已' + '、'.join(did) if did else '没有改动'


def runners_view():
    out = []
    pos = _positions()
    for r in _db.rows('runners', order='local DESC, created'):
        info = r.get('info') or {}
        m = monitor.latest(r['id']) or {}
        out.append({'id': r['id'], '名称': r.get('name') or r['id'], '状态': runner_state(r), '本机': bool(r.get('local')),
                    '自动标签': r.get('auto_tags') or [], '自定义标签': r.get('custom_tags') or [], '容量': r.get('capacity') or 0,
                    '运行中': [view(t, pos) for t in _db.rows('tasks', {'runner': r['id']}) if t['status'] in ACTIVE],
                    '副本': info.get('副本') or [], '手机': info.get('手机') or [], '磁盘': info.get('磁盘') or {},
                    '版本': r.get('version') or '', '协议版本': r.get('proto') or PROTO_VERSION,
                    '最后心跳': _db.ts_str(r['last_hb']) if r.get('last_hb') else '', '自检': info.get('自检') or [],
                    '指标': {'CPU': m.get('cpu'), '内存可用比': m.get('mem'), '磁盘使用比': m.get('disk'), '网络': m.get('net') or {}},
                    '网络差': info.get('网络差') or [], '禁止新副本': bool(info.get('禁止新副本')),
                    '告警': len(_db.rows('alerts', {'runner': r['id']}, extra='start > 0 AND end IS NULL'))})
    return out


# ---------------------------------------------------------------- 本机任务（单机模式）
def _local_alive(t):
    """本机任务还在跑吗：True 在跑 / False 结束了 / None 看不出来。"""
    p = t.get('payload') or {}
    c = p.get('local_check') or {}
    k = c.get('kind') or ''
    try:
        import kb
        if k == 'dev':
            s = kb.dev_session(t['period']) or {}
            return s.get('状态') == '运行中'
        if k == 'stack':
            from kblib.stack import dev as sd
            x = ((kb.load_meta(kb.period_dir(t['period'])).get('各端开发') or {}).get(c.get('end') or '') or {})
            from kblib.ai.devsession import _alive
            return x.get('状态') in sd.RUNNING and _alive(x.get('pid', 0))
        if k == 'devrun':
            from kblib.plugins.devrun import DONE
            return kb.devrun_job(c.get('job'))['状态'] not in DONE
        if k == 'install':
            return kb.install_job(c.get('job')).get('状态') not in ('成功', '失败', '已取消')
        if k == 'bug':
            return any(x.get('状态') == '运行中' for x in kb.an_sessions())
    except KBError:
        return False
    except Exception:  # noqa: BLE001
        return None
    return None


def _local_stop(t):
    c = (t.get('payload') or {}).get('local_check') or {}
    k = c.get('kind') or ''
    try:
        import kb
        if k == 'dev':
            kb.dev_stop(t['period'], by='用户（任务队列）')
        elif k == 'stack':
            kb.stack_dev_stop(t['period'], c.get('end') or '')
        elif k == 'devrun':
            kb.devrun_cancel(c.get('job'))
    except Exception as e:  # noqa: BLE001
        print('queue: 停本机任务出错', repr(e), flush=True)


def set_local_check(tid, check):
    """本机启动函数跑完才知道 job 编号的（运行到手机、安装）：补记怎么查活。"""
    t = task(tid)
    p = dict(t.get('payload') or {})
    p['local_check'] = check
    _db.update('tasks', {'id': tid}, payload=p)


def _local_tick():
    t0 = now()
    for t in _db.rows('tasks', {'runner': LOCAL_ID}):
        if t['status'] not in ACTIVE or t0 - (t.get('updated') or 0) < 10:
            continue
        alive = _local_alive(t)
        if alive is False:
            _finish(t['id'], '完成', {})
    if remote_online():
        return
    for t in _queued():                                   # 超配额排队的本机任务：名额空了就起
        fn = _LOCAL_FN.get(t['id'])
        if not fn:
            if (t.get('payload') or {}).get('本机'):
                _finish(t['id'], '失败', {'失败原因': '服务重启过，排队中的本机任务没了，请重新发起'})
            continue
        if _quota_block(t):
            continue
        _LOCAL_FN.pop(t['id'], None)
        try:
            _run_local(t['id'], fn)
        except Exception as e:  # noqa: BLE001
            print('queue: 本机任务启动失败', t['id'], repr(e), flush=True)


def recover():
    """网页服务启动（dev_reattach 调）：按任务表恢复——本机在跑的看会话还在不在；本机排队的（进程内函数已丢）标失败。"""
    ensure_local()
    for t in _db.rows('tasks', {'runner': LOCAL_ID}):
        if t['status'] in ACTIVE and _local_alive(t) is False:
            _finish(t['id'], '完成', {'说明': '服务重启时会话已结束'})
    for t in _queued():
        if (t.get('payload') or {}).get('本机') and t['id'] not in _LOCAL_FN:
            _finish(t['id'], '失败', {'失败原因': '服务重启过，排队中的本机任务没了，请重新发起'})


# ---------------------------------------------------------------- 定时（每 5 秒，租约保证只跑一份，REQ-009）
def _mark_maybe_dirty(r):
    info = dict(r.get('info') or {})
    cs = []
    for c in info.get('副本') or []:
        c = dict(c)
        if c.get('占用会话'):
            c['可能有改动'] = True
        cs.append(c)
    info['副本'] = cs
    return info


def tick():
    t0 = now()
    for r in _db.rows('runners', {'local': 0, 'disabled': 0}):
        if r.get('status') != '离线' and t0 - (r.get('last_hb') or 0) > OFFLINE_SEC:
            _db.update('runners', {'id': r['id']}, status='离线', info=_mark_maybe_dirty(r))
            for t in _db.rows('tasks', {'runner': r['id']}):
                if t['status'] in ACTIVE:
                    requeue(t['id'], f'执行机 {r.get("name") or r["id"]} 掉线', prev=r['id'])
            _call(_resources(), 'release_runner', r['id'])
            _ev_runner(r['id'])
        if r.get('drain') == 1 and not running_on(r['id']):
            _db.update('runners', {'id': r['id']}, drain=2)
            _ev_runner(r['id'])
    for t in _db.task_queue_rows(['已派']):
        if t.get('ack_deadline') and t['ack_deadline'] < t0:
            requeue(t['id'], '执行机 2 分钟内没确认领取', prev=t.get('runner'))
    ensure_local()
    _local_tick()
    refresh_waits()
    _call(_resources(), 'tick')
    _call(monitor, 'tick')


def tick_loop(interval=5):
    while True:
        try:
            if _db.lease_try('queue_tick', interval - 0.5, _HOLDER, 30):
                try:
                    tick()
                    _db.lease_done('queue_tick', _HOLDER, 'ok')
                except Exception as e:
                    _db.lease_done('queue_tick', _HOLDER, repr(e))
                    raise
        except Exception as e:  # noqa: BLE001
            print('queue.tick:', repr(e), flush=True)
        time.sleep(interval)


def task_log_path(tid):
    return Path(log_file(tid))


# ---------------------------------------------------------------- 启动类接口的统一入口（REQ-013 REQ-026）
def engine_command(eng, prompt, title):
    """控制面按引擎设置算好的命令行（执行机可直接用，也可按 引擎 自己拼）。"""
    args = [str(a).replace('{提示词}', prompt).replace('{标题}', title) for a in eng.get('启动参数') or [] if '{会话ID}' not in str(a)]
    return [eng.get('命令') or eng.get('id') or 'claude', *args]


def remote_dev_payload(period, note='', repo='', branch='', kind='开发', end=''):
    """有远程执行机时，开发 / 全栈端任务下发的内容：仓库、分支、引擎、提示词。批准（改状态）仍在控制面做。"""
    import kb
    d = kb.period_dir(period)
    m = kb.load_meta(d)
    if kind == '开发' and m.get('状态') not in kb.DEV_OK:
        kb.set_status(d.name, '已批准开发', note or '网页「开始开发」批准', by_human=True)
    eng = kb.period_engine(m) if kind == '开发' else kb.role_engine('开发', m, end)
    want = branch or m.get('开发分支') or (kb.want_branches(d, m) or [''])[0]
    rp = repo or m.get('远程仓库') or m.get('开发仓库') or ''
    if end:
        e = (m.get('各端') or {}).get(end) or {}
        rp = repo or e.get('仓库') or rp
        want = branch or e.get('分支') or want
    prompt = (f'你是知识库后台开发会话（执行机上运行）：{"期" if kb.load_meta(d).get("类型") != "事项" else "事项"}「{d.name}」'
              + (f'，负责「{end}」端' if end else '') + '。\n'
              f'先调用 kb_dev_package（period={d.name}）拿开发包和开发约定，再按约定开发；要问用户用 kb_ask，拿不准的不要自己定。'
              + (f'\n\n【用户这次的要求】{note}' if note else ''))
    return {'仓库': rp, '分支': want, '马甲线': m.get('马甲线') or '', '引擎': eng.get('id') or '', '提示词': prompt,
            '命令': engine_command(eng, prompt, f'kb·{d.name}'), '标签': [], '端': end}


def launch(kind, period, local, *, check=None, remote=None, payload=None):
    """路由里的启动类接口都走这里：有远程执行机且这类任务能下发（给了 remote）→ 进队列；否则本机执行（和原来一样）。
    返回 {'task': 视图, 'result': 本机返回值}；本机执行跑完才知道 job 编号的，check 可以是 callable(result) → local_check。"""
    p = dict(payload or {})
    if not callable(check):
        p['local_check'] = check
    if remote_online() and remote is not None:
        p.update(remote() if callable(remote) else remote)
    else:
        p['只在本机'] = True
    r = submit(kind, period, p, local=local)
    if callable(check) and r['result'] is not None and r['task']['状态'] == '运行':
        set_local_check(r['task']['id'], check(r['result']))
    return r


def launch_msg(r, fallback=''):
    """给网页的 msg：本机执行返回原文；进了队列说排第几、在等什么。"""
    if isinstance(r.get('result'), str):
        return r['result']
    t = r['task']
    if t['状态'] == '排队':
        return f'已建任务 {t["id"]}，排队第 {t["排队位置"] or 1} 位' + (f'：{t["等待原因"]}' if t['等待原因'] else '')
    return fallback or f'已建任务 {t["id"]}（{t["状态"]}）'


def runner_groups(key):
    """远程执行机心跳里报的 副本 / 手机，按执行机分组（/api/pool、/api/devices 用）。"""
    out = []
    for r in _db.rows('runners', {'local': 0}, order='created'):
        out.append({'执行机': r.get('name') or r['id'], 'id': r['id'], '状态': runner_state(r),
                    key: ((r.get('info') or {}).get(key)) or []})
    return out
