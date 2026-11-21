"""AI 引擎账号池（REQ-018 REQ-038 REQ-040 REQ-041 REQ-042）。

  账号：项目共享（每个项目 1~2 个，按「备用顺序」排成备用链）/ 个人（只给本人和他发起的会话，可开「优先用我的」）。
        登录态（API Key 或 OAuth 登录态 JSON）存机密库（vault 服务 kb-engine-account），只写不读、不回显。
  选号 pick()：个人优先（开了开关）→ 项目共享按顺序 → 备用引擎（只对允许换引擎的任务类型）；不做轮询绕限流，
        换号只在长期故障时；冷却中的等冷却；同一任务 10 分钟内最多切 1 次。
  名额：派活前经资源池占「引擎名额」（eng:<账号>，容量 = 并发上限）；1 分钟请求数到 RPM 的 80% 暂缓。
  健康：report_error() 只看结构化错误码：长期（登录失效 / 401 / 额度 / 到期）、暂时（429 / 529 / 503 / 网络）、
        模型级（403 / 模型不可用）；长期和模型级连续 3 次同类才改状态，不确定的不计。冷却后用极短自检探测，连续 2 次成功恢复。
  用量 / 预算：会话结束记到发起人、项目、账号；按 token（输入 + 输出）设日 / 月预算，0 = 不限只统计。
"""
import json
import time

from kblib.core import db as _db
from kblib.core.common import KBError, uuid_hex

VAULT_SERVICE = 'kb-engine-account'
KINDS = ('项目共享', '个人')
SWITCH_TYPES = ('Bug分析', '方案')          # 默认允许换引擎的任务类型
SWITCH_GAP = 600                              # 同一任务 10 分钟内最多切 1 次
RPM_SOFT = 0.8
STREAK = 3                                    # 连续 3 次同类失败才改状态
COOL_429, COOL_NET, COOL_MAX = 60, 30, 900
PROBE_DAY, PROBE_NIGHT = 300, 1800
EST_TOKENS_PER_SEC = 60                       # 引擎没报 token 时按时长估算
SESSION_SHARE = 0.3                           # 单会话超项目日预算 30% 暂停
NO_ACCOUNT = '本项目没有可用 AI 账号，请项目负责人在 设置 → AI 账号 配置'
PROBE = None                                  # 测试可替换：PROBE(账号) -> True / (False, 原因)


def now():
    return _db.now_ts()


# ---------------------------------------------------------------- 通知（按人）
def _notify(users, title, text):
    users = [u for u in users if u]
    _db.event_add('通知', '', {'给': users, '标题': title, '内容': text})
    try:
        from kblib.core import monitor
        monitor.notify_people(users, title, text)
    except Exception as e:  # noqa: BLE001 通知失败不影响主流程
        print('通知出错：', repr(e), flush=True)


def _admins():
    return [u['name'] for u in _db.user_list() if u.get('sys_role') == '管理员' and u.get('status') != '停用']


def _project_owner(project):
    p = _db.project_get(project) if project else None
    return (p or {}).get('owner') or ''


def _who_to_tell(a):
    if a.get('kind') == '个人':
        return [a['owner']]
    o = _project_owner(a.get('project'))
    return [o] if o else _admins()


# ---------------------------------------------------------------- 账号登记
def _rid(aid):
    return f'eng:{aid}'


def _sync_res(a):
    from kblib.core import resources
    resources.put_resource(_rid(a['id']), '引擎名额', {'账号': a['id'], '名称': a['name'], '项目': a.get('project') or '',
                                                     '引擎': a['engine'], '类型': a['kind'], '所属人': a.get('owner') or ''},
                           capacity=a.get('conc') or 1, provider='engine')


def get(aid):
    return _db.get('engine_accounts', id=aid)


def all_accounts():
    return _db.rows('engine_accounts', order='ord, created')


def view(a, me=''):
    running = _db.count('holds', {'resource': _rid(a['id'])})
    st = a['status']
    if st == '冷却' and a.get('cool_until', 0) and a['cool_until'] > now():
        cool = _db.ts_str(a['cool_until'])
    else:
        cool = ''
    return {'id': a['id'], '引擎': a['engine'], '名称': a['name'], '类型': a['kind'], '项目': a.get('project') or '',
            '所属人': a.get('owner') or '', '并发上限': a['conc'], 'RPM上限': a['rpm'], '运行中': running, '状态': st,
            '冷却到': cool, '原因': a.get('reason') or '', '模型不可用': a.get('bad_models') or [], '备用顺序': a.get('ord') or 0,
            '允许换引擎': bool(a.get('allow_switch')), '优先用我的': bool(a.get('prefer_mine')),
            '已自动下调': bool(a.get('adjusted') and now() - a['adjusted'] < 86400 * 7), '有登录态': _has_login(a['id'])}


def _has_login(aid):
    try:
        from kblib.infra import vault
        return vault.get(VAULT_SERVICE, aid) is not None
    except Exception:  # noqa: BLE001
        return False


def visible(user, project=None):
    """项目负责人看本项目，管理员看全部，个人账号只本人可见。"""
    from kblib.core import authz
    adm = authz.is_admin(user)
    out = []
    for a in all_accounts():
        if a['kind'] == '个人':
            if a.get('owner') == user:
                out.append(a)
            continue
        if project and a.get('project') != project:
            continue
        if adm or authz.project_role(user, a.get('project')):
            out.append(a)
    return out


def save(body, user):
    """新增 / 修改 / 删除。项目共享限项目负责人或管理员，个人账号限本人。返回 (消息, id)。"""
    from kblib.core import authz
    from kblib.infra import vault
    aid = body.get('id') or ''
    old = get(aid) if aid else None
    if aid and not old:
        raise KBError(f'没有这个引擎账号：{aid}')
    kind = body.get('类型') or (old or {}).get('kind') or '项目共享'
    if kind not in KINDS:
        raise KBError(f'类型只能是：{" / ".join(KINDS)}')
    project = body.get('项目') if body.get('项目') is not None else (old or {}).get('project') or ''
    owner = (old or {}).get('owner') or (user if kind == '个人' else '')
    if kind == '个人':
        if owner != user:
            raise authz.Denied('个人账号只能本人修改')
    else:
        if not project:
            raise KBError('项目共享账号要选项目')
        if not authz.is_admin(user) and authz.project_role(user, project) != '负责人':
            raise authz.Denied('项目共享账号只有项目负责人或管理员能改')
    if body.get('删除'):
        if not old:
            raise KBError('要删的账号不存在')
        from kblib.core import resources
        resources.drop_resource(_rid(aid))
        _db.delete('engine_accounts', {'id': aid})
        try:
            vault.delete(VAULT_SERVICE, aid)
        except Exception:  # noqa: BLE001
            pass
        _db.audit('引擎账号', user, aid, project, '成功', '删除')
        return f'已删除 {old["name"]}', aid
    engine = body.get('引擎') or (old or {}).get('engine') or ''
    name = (body.get('名称') or (old or {}).get('name') or '').strip()
    if not engine or not name:
        raise KBError('引擎和名称不能为空')
    f = dict(engine=engine, name=name, kind=kind, project=project, owner=owner,
             conc=max(1, int(body.get('并发上限') or (old or {}).get('conc') or 3)),
             rpm=max(1, int(body.get('RPM上限') or (old or {}).get('rpm') or 30)),
             ord=int(body.get('备用顺序') if body.get('备用顺序') is not None else (old or {}).get('ord') or 0),
             allow_switch=1 if (body.get('允许换引擎') if '允许换引擎' in body else (old or {}).get('allow_switch')) else 0,
             prefer_mine=1 if (body.get('优先用我的') if '优先用我的' in body else (old or {}).get('prefer_mine')) else 0)
    if not old:
        aid = 'EA' + uuid_hex()[:8]
        _db.insert('engine_accounts', id=aid, created=_db.ts_str(), status='未检测', **f)
    else:
        if old.get('adjusted') and (f['conc'] != old['conc'] or f['rpm'] != old['rpm']):
            f['adjusted'] = 0                     # 管理员手动改回：不再标「已自动下调」
        _db.update('engine_accounts', {'id': aid}, **f)
    if body.get('登录态'):
        vault.put(VAULT_SERVICE, aid, str(body['登录态']), f'引擎账号 {name}')
        _db.secret_meta_put(f'EA:{aid}', '个人' if kind == '个人' else '项目', owner, project)
        if (old or {}).get('status') == '不可用':           # 人工修好：进入探测，连续 2 次成功恢复
            _db.update('engine_accounts', {'id': aid}, status='冷却', cool_until=now(), ok_probes=0, reason='重新登录后待探测')
    _sync_res(get(aid))
    _db.audit('引擎账号', user, aid, project, '成功', '新增' if not old else '修改')
    return ('已添加' if not old else '已保存') + f' {name}', aid


def login_blob(account_id, user):
    """取账号登录态（执行机写进会话私有 HOME）。个人账号只给本人发起的会话（REQ-018）。"""
    from kblib.core import authz
    from kblib.infra import vault
    a = get(account_id)
    if not a:
        raise KBError(f'没有这个引擎账号：{account_id}')
    if a['kind'] == '个人' and a.get('owner') != user:
        _db.audit('机密', user or '?', f'EA:{account_id}', a.get('project') or '', '拒绝', '别人的个人 AI 账号')
        raise authz.Denied('个人 AI 账号只给本人和他发起的会话')
    v = vault.get(VAULT_SERVICE, account_id)
    _db.audit('机密', user or '?', f'EA:{account_id}', a.get('project') or '', '成功', '会话取引擎登录态')
    if v is None:
        return {}
    try:
        d = json.loads(v)
        return d if isinstance(d, dict) else {'apiKey': v}
    except ValueError:
        return {'apiKey': v}


def writeback_login(account_id, blob):
    """会话里引擎刷新了 OAuth 登录态：结束时加密回写账号存储（REQ-039）。"""
    from kblib.infra import vault
    if not get(account_id) or not blob:
        return False
    vault.put(VAULT_SERVICE, account_id, json.dumps(blob, ensure_ascii=False) if isinstance(blob, dict) else str(blob))
    return True


# ---------------------------------------------------------------- 选号
def _usable(a, model=''):
    """(能不能用, 冷却中?)"""
    if a['status'] == '不可用':
        return False, False
    if model and model in (a.get('bad_models') or []):
        return False, False
    if a['status'] == '冷却' and (a.get('cool_until') or 0) > now():
        return False, True
    if a['status'] == '冷却' and a.get('reason', '').startswith('重新登录'):
        return False, True
    return True, False


def _rpm_used(aid):
    return sum(_db.calls_window(aid, now() - 60).values())


def _quota_pos(a):
    return _db.count('tasks', {'status': '排队'}, extra='wait_reason LIKE ?', args=(f'%AI 账号 {a["name"]} 的名额%',)) + 1


def chain(task):
    """这个任务能用的账号顺序：个人（开了优先）→ 本项目共享（按备用顺序）→ 允许的备用引擎。"""
    user, proj = task.get('initiator') or '', task.get('project') or ''
    accs = all_accounts()
    mine = [a for a in accs if a['kind'] == '个人' and a.get('owner') == user]
    shared = sorted([a for a in accs if a['kind'] == '项目共享' and a.get('project') == proj], key=lambda a: (a['ord'] or 0, a['created'] or ''))
    first = [a for a in mine if a.get('prefer_mine')]
    eng = (task.get('payload') or {}).get('引擎') or (first[0]['engine'] if first else shared[0]['engine'] if shared else '')
    same = [a for a in shared if not eng or a['engine'] == eng]
    other = [a for a in shared if eng and a['engine'] != eng and a.get('allow_switch') and task.get('类型', task.get('type')) in SWITCH_TYPES]
    rest_mine = [a for a in mine if not a.get('prefer_mine') and (task.get('payload') or {}).get('用我的')]
    return first, same + other + rest_mine


def pick(task):
    """→ (账号视图 | None, 等待原因)。整个库一个账号都没登记：老用户单机模式，用本机登录态不阻塞。"""
    if not _db.count('engine_accounts'):
        return {'id': '', '名称': '本机登录态', '引擎': (task.get('payload') or {}).get('引擎') or ''}, ''
    model = (task.get('payload') or {}).get('模型') or ''
    first, rest = chain(task)
    prev = task.get('account') or ''
    cand = []
    for a in first:
        ok, cooling = _usable(a, model)
        if ok:
            cand.append(a)
        elif cooling:
            return None, f'你的 AI 账号 {a["name"]} 冷却中，约 {_mins(a)} 分钟后恢复'
        else:
            prof = (_db.user_get(task.get('initiator') or '') or {}).get('profile') or {}
            if not prof.get('AI回落同意'):
                _ask_fallback(task, a)
                return None, f'你的 AI 账号 {a["name"]} 不可用：回落到项目共享账号前需要你同意（我的 → AI 账号）'
    if not cand:
        for a in rest:
            ok, cooling = _usable(a, model)
            if ok:
                cand.append(a)
                break
            if cooling:                           # 冷却只等，不换号（REQ-042）
                return None, f'AI 账号 {a["name"]} 冷却中，约 {_mins(a)} 分钟后恢复'
    if not cand:
        _tell_no_account(task)
        return None, NO_ACCOUNT
    a = cand[0]
    if prev and prev != a['id'] and now() - (task.get('switched') or 0) < SWITCH_GAP:
        p = get(prev)
        if p:
            return None, f'10 分钟内已切换过账号，等 AI 账号 {p["name"]} 恢复'
    if _db.count('holds', {'resource': _rid(a['id'])}) >= (a['conc'] or 1):
        return None, f'等 AI 账号 {a["name"]} 的名额：第 {_quota_pos(a)} 位'
    if _rpm_used(a['id']) >= RPM_SOFT * (a['rpm'] or 1):
        return None, f'AI 账号 {a["name"]} 最近 1 分钟请求较多，稍后启动'
    v = view(a)
    v['切换'] = bool(prev and prev != a['id'])
    v['说明'] = f'用 {"个人账号" if a["kind"] == "个人" else "项目共享账号"} {a["name"]}'
    return v, ''


def _mins(a):
    return max(1, int(round(((a.get('cool_until') or now()) - now()) / 60)))


def _ask_fallback(task, a):
    key = f'回落提示:{task.get("initiator")}:{a["id"]}'
    if _db.kv_get(key)[0]:
        return
    _db.kv_set(key, True)
    _notify([task.get('initiator')], '知识库 · 你的 AI 账号不可用',
            f'个人 AI 账号 {a["name"]} 不可用（{a.get("reason") or "故障"}）。同意回落到项目共享账号请到「我的 → AI 账号」点同意（只问这一次）。')


def _tell_no_account(task):
    proj = task.get('project') or ''
    key = f'无账号提示:{proj}'
    last = _db.kv_get(key)[0] or 0
    if now() - float(last) < 3600:
        return
    _db.kv_set(key, now())
    o = _project_owner(proj)
    _notify([o] if o else _admins(), '知识库 · 项目没有可用 AI 账号', f'项目 {proj}：{NO_ACCOUNT}')


def acquire(task_id, account_id):
    """占一个引擎名额（资源池，REQ-038）。账号为空（本机登录态）直接 True。"""
    if not account_id:
        return True
    from kblib.core import resources
    t = _db.get('tasks', id=task_id) or {}
    r = resources.claim('引擎名额', {'账号': account_id}, 1, task_id, t.get('period') or '', t.get('initiator') or '',
                        t.get('project') or '', mode='自动', wait=False)
    return bool(r['ok'])


def release_task(task_id):
    """会话结束 / 崩溃 / 取消：归还名额（下一次 5 秒调度前就能被派）。"""
    from kblib.core import resources
    return resources.release_type(task_id, '引擎名额')


# ---------------------------------------------------------------- 故障分类与健康（REQ-041）
LONG = {'401', 'auth', 'unauthorized', 'login', 'not_logged_in', 'invalid_api_key', 'quota', 'insufficient_quota',
        'billing', 'expired', 'account_expired', 'credit'}
TEMP = {'429', '529', '503', 'rate_limit', 'overloaded', 'network', 'timeout', 'econnreset', 'unavailable'}
MODEL = {'403', 'forbidden', 'model_unavailable', 'model_not_found', 'permission_denied'}
OK = {'0', 'ok', '200', 'success'}


def classify(code):
    """结构化错误码 → 长期 / 暂时 / 模型级 / 正常 / None（不确定，不计入健康）。"""
    c = str(code if code is not None else '').strip().lower()
    if c in OK:
        return '正常'
    if c in LONG:
        return '长期'
    if c in TEMP:
        return '暂时'
    if c in MODEL:
        return '模型级'
    return None


def record_call(account_id, code='ok'):
    """记一次请求（RPM 和 429 比例用）。"""
    if account_id:
        _db.insert('engine_calls', account=account_id, ts=now(), code=str(code))


def report_error(account_id, code, text='', model='', retry_after=None):
    """报一次结果：只看结构化错误码；返回分类。text 只用来写原因，不参与判断（避免把任务文本里的 quota / api key 误判）。"""
    a = get(account_id) if account_id else None
    if not a:
        return None
    cls = classify(code)
    record_call(account_id, code)
    if cls is None:
        return None
    streak = dict(a.get('streak') or {})
    if cls == '正常':
        _db.update('engine_accounts', {'id': account_id}, streak={}, backoff=0)
        if a['status'] == '未检测':
            _db.update('engine_accounts', {'id': account_id}, status='可用')
        return cls
    key = f'{cls}:{model}' if cls == '模型级' else cls
    n = (streak.get(key) or 0) + 1
    streak = {key: n}                              # 只记连续的同类
    why = f'{code}' + (f'：{text.strip()[:200]}' if text and text.strip() else '')
    f = {'streak': streak}
    if cls == '暂时':
        base = COOL_NET if str(code).lower() in ('network', 'timeout', 'econnreset') else COOL_429
        cool = float(retry_after) if retry_after else min(COOL_MAX, max(base, (a.get('backoff') or 0) * 2 or base))
        f.update(status='冷却', cool_until=now() + cool, backoff=min(COOL_MAX, cool), reason=f'暂时故障，冷却 {int(cool)} 秒（{why}）',
                 ok_probes=0)
        _check_429(a)
    elif n >= STREAK:
        if cls == '长期':
            f.update(status='不可用', reason=f'长期故障：{why}', ok_probes=0)
            if a['status'] != '不可用':
                _notify(_who_to_tell(a), '知识库 · AI 账号不可用',
                        f'{a["name"]}：{why}。请重新登录或续费后到「设置 → AI 账号」更新登录态。')
        elif cls == '模型级' and model:
            bad = sorted(set(a.get('bad_models') or []) | {model})
            f.update(bad_models=bad)
    _db.update('engine_accounts', {'id': account_id}, **f)
    _db.event_add('引擎账号', a.get('project') or '', {'id': account_id, '分类': cls})
    return cls


def _check_429(a):
    """最近 1 小时 429 比例 >2%：并发和 RPM 自动下调 20%，通知管理员（每小时最多一次）。"""
    w = _db.calls_window(a['id'], now() - 3600)
    total = sum(w.values())
    n429 = w.get('429', 0)
    if total < 20 or n429 / total <= 0.02 or now() - (a.get('adjusted') or 0) < 3600:
        return False
    conc, rpm = max(1, int(a['conc'] * 0.8)), max(1, int(a['rpm'] * 0.8))
    _db.update('engine_accounts', {'id': a['id']}, conc=conc, rpm=rpm, adjusted=now())
    _sync_res(get(a['id']))
    _notify(_admins(), '知识库 · AI 账号已自动下调', f'{a["name"]} 最近 1 小时 429 比例 {n429 * 100 // total}%：并发 {a["conc"]}→{conc}，'
            f'RPM {a["rpm"]}→{rpm}。可在「设置 → AI 账号」手动改回。')
    return True


def _probe(a):
    if PROBE:
        return PROBE(a)
    from kblib.ai import engines
    try:
        h = engines.engine_probe(a['engine'], src='账号探测', account=a['id'])
    except Exception as e:  # noqa: BLE001
        return False, str(e)
    return True if h.get('状态') == '可用' else (False, h.get('原因') or '自检没通过')


def probe(account_id):
    """探测一次：成功计数，连续 2 次成功恢复可用（REQ-042）。返回账号视图。"""
    a = get(account_id)
    if not a:
        raise KBError(f'没有这个引擎账号：{account_id}')
    r = _probe(a)
    f = {'last_probe': now()}
    if r is True:
        n = (a.get('ok_probes') or 0) + 1
        f['ok_probes'] = n
        if a['status'] == '未检测' or n >= 2:
            f.update(status='可用', reason='', cool_until=0, backoff=0, streak={}, ok_probes=0)
            if a['status'] in ('不可用', '冷却'):
                _notify(_who_to_tell(a), '知识库 · AI 账号已恢复', f'{a["name"]} 又能用了')
        elif a['status'] == '不可用':
            f.update(status='冷却', cool_until=now(), reason='人工修好后待第 2 次探测')
    else:
        f['ok_probes'] = 0
        if a['status'] == '未检测':
            f.update(status='不可用', reason=r[1][:300] if isinstance(r, tuple) else '自检没通过')
    _db.update('engine_accounts', {'id': account_id}, **f)
    return view(get(account_id))


def probe_tick():
    """冷却结束后每 5 分钟（夜间 0–8 点每 30 分钟）探测；长期不可用的不自动探测（人工修好后才进探测）。"""
    gap = PROBE_NIGHT if time.localtime(now()).tm_hour < 8 else PROBE_DAY
    n = 0
    for a in all_accounts():
        if a['status'] != '冷却' or (a.get('cool_until') or 0) > now():
            continue
        if now() - (a.get('last_probe') or 0) < gap and a.get('ok_probes'):
            continue
        probe(a['id'])
        n += 1
    _db.calls_prune(2)
    return n


# ---------------------------------------------------------------- 用量与预算（REQ-040）
def _day0():
    t = time.localtime(now())
    return time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))


def _month0():
    t = time.localtime(now())
    return time.mktime((t.tm_year, t.tm_mon, 1, 0, 0, 0, 0, 0, -1))


def record_usage(task, usage):
    """会话结束记用量：同时记到发起人、项目、账号；引擎没报 token 按时长估算（标「估算」）。"""
    usage = usage or {}
    sec = int(usage.get('时长秒') or 0)
    tin, tout = usage.get('输入token'), usage.get('输出token')
    est = 0
    if not tin and not tout:
        tin, tout, est = sec * EST_TOKENS_PER_SEC * 3 // 4, sec * EST_TOKENS_PER_SEC // 4, 1
    tid = task.get('id') or ''
    _db.insert('usage', ts=now(), user=task.get('initiator') or '', project=task.get('project') or '', account=task.get('account') or '',
               task=tid, seconds=sec, tin=int(tin or 0), tout=int(tout or 0), estimated=est)
    check_budget_notice(task.get('initiator') or '', task.get('project') or '')
    return {'输入token': int(tin or 0), '输出token': int(tout or 0), '估算': bool(est)}


def budget_get(scope, obj):
    return _db.get('budgets', scope=scope, obj=obj)


def budget_set(scope, obj, day=None, month=None, policy=None, cheap=None, by=''):
    if scope not in ('人', '项目'):
        raise KBError('范围只能是 人 / 项目')
    if not obj:
        raise KBError('要指定对象')
    if policy and policy not in ('排队', '降级'):
        raise KBError('超额策略只能是 排队 / 降级')
    old = budget_get(scope, obj) or {}
    f = dict(day=float(day if day is not None else old.get('day') or 0), month=float(month if month is not None else old.get('month') or 0),
             policy=policy or old.get('policy') or '排队', cheap=cheap if cheap is not None else old.get('cheap') or '')
    if f['day'] < 0 or f['month'] < 0:
        raise KBError('预算不能是负数')
    if f['policy'] == '降级' and not f['cheap']:
        raise KBError('超额降级要填降级模型')
    with _db.tx():
        _db.delete('budgets', {'scope': scope, 'obj': obj})
        _db.insert('budgets', scope=scope, obj=obj, notified=old.get('notified') or '', **f)
    _db.audit('预算', by, f'{scope}:{obj}', obj if scope == '项目' else '', '成功', json.dumps(f, ensure_ascii=False))
    return f'已保存{scope}「{obj}」的预算'


def grant(project, amount, reason='', by=''):
    """临时加额：一次性加到今日 / 本月额度上，24 小时有效，写审计。"""
    amount = float(amount or 0)
    if amount <= 0:
        raise KBError('加额要大于 0')
    _db.insert('grants', project=project, amount=amount, until=now() + 86400, by=by, reason=reason)
    _db.audit('预算', by, f'项目:{project}', project, '成功', f'临时加额 {amount:g}（24 小时）：{reason}')
    return f'已给项目临时加额 {amount:g}（24 小时有效）'


def _used(scope, obj):
    col = 'user' if scope == '人' else 'project'
    return _db.usage_tokens(col, obj, _day0()), _db.usage_tokens(col, obj, _month0())


def _ratio(scope, obj):
    """(已用比 = 日、月里较高的那个, 是哪个)。没设预算 → (0, '')。"""
    b = budget_get(scope, obj)
    if not b:
        return 0.0, ''
    d, m = _used(scope, obj)
    extra = _db.grants_active(obj) if scope == '项目' else 0
    rs = []
    if b['day']:
        rs.append((d / (b['day'] + extra), '今日'))
    if b['month']:
        rs.append((m / (b['month'] + extra), '本月'))
    return max(rs) if rs else (0.0, '')


def budget_state(user, project):
    """→ (能不能新开会话, 原因, 降级模型)。0 = 不限只统计。"""
    for scope, obj, who in (('项目', project, '本项目'), ('人', user, '你')):
        if not obj:
            continue
        r, which = _ratio(scope, obj)
        if r >= 1:
            b = budget_get(scope, obj)
            if scope == '项目' and b.get('policy') == '降级' and b.get('cheap'):
                return True, f'{who}{which} AI 预算已用完，超额降级：改用 {b["cheap"]}', b['cheap']
            return False, f'{who}{which} AI 预算已用完', ''
    return True, '', ''


def check_budget_notice(user, project):
    """达 80% 通知本人和项目负责人一次（每个日 / 月周期一次）。"""
    for scope, obj in (('项目', project), ('人', user)):
        if not obj:
            continue
        r, which = _ratio(scope, obj)
        if r < 0.8:
            continue
        key = time.strftime('%Y-%m-%d' if which == '今日' else '%Y-%m', time.localtime(now())) + ('满' if r >= 1 else '')
        b = budget_get(scope, obj)
        if key in (b.get('notified') or '').split(','):
            continue
        _db.update('budgets', {'scope': scope, 'obj': obj}, notified=((b.get('notified') or '') + ',' + key).strip(','))
        to = [obj, _project_owner(project)] if scope == '人' else [_project_owner(obj), user]
        _notify(to, '知识库 · AI 预算提醒', f'{"项目" if scope == "项目" else "成员"} {obj} {which} AI 用量已达预算 {int(r * 100)}%')


def over_session_budget(task_id):
    """单个会话用量超过项目日预算 30%：暂停并挂问题让发起人选（A1 在回传用量时调）。"""
    t = _db.get('tasks', id=task_id) or _db.get('usage', task=task_id) or {}
    b = budget_get('项目', t.get('project') or '')
    if not b or not b.get('day'):
        return False
    return _db.usage_of_task(task_id) > SESSION_SHARE * b['day']


def usage_report(by='项目', days=30, project=None, user=''):
    col = {'人': 'user', '项目': 'project', '账号': 'account'}.get(by)
    if not col:
        raise KBError('by 只能是 人 / 项目 / 账号')
    days = max(1, min(int(days or 30), 90))
    out = []
    names = {a['id']: a['name'] for a in all_accounts()}
    for r in _db.usage_sum(col, now() - days * 86400, project=project):
        obj = r['k'] or ''
        scope = '人' if by == '人' else '项目' if by == '项目' else ''
        b = budget_get(scope, obj) if scope else None
        out.append({'对象': names.get(obj, obj) if by == '账号' else obj, '会话数': r['n'] or 0, '时长秒': r['s'] or 0,
                    '输入token': r['i'] or 0, '输出token': r['o'] or 0, '估算': bool(r['e']),
                    '预算日': (b or {}).get('day') or 0, '预算月': (b or {}).get('month') or 0,
                    '已用比': round(_ratio(scope, obj)[0], 3) if scope else 0})
    return out
