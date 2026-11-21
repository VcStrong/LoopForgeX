"""部署、登录、令牌（REQ-001 REQ-005 REQ-026）。

  账号：users 表（core/users.py 管理），密码存 PBKDF2 哈希。单机模式（只听 127.0.0.1、没有带密码的账号）免登录，
        以本机用户（管理员）身份打开；要监听别的地址必须先有账号（kb user add）。
  登录：web_login(用户名, 密码) → Cookie kb_s 绑定用户（只存哈希，30 天）；连续 5 次失败锁 15 分钟；
        改密码 / 停用后该用户已登录的浏览器在下一次请求就失效（会话里记了密码版本）。
  令牌：kb_ 个人令牌（绑用户，我的 → 令牌，或 kb token add 绑管理员）、kbs_ 会话令牌（绑发起人 + 项目 + 期 + 会话）、
        kbr_ 执行机令牌、kbreg_ 一次性注册令牌（24 小时）。都只存哈希。
放在公网时建议前面加 HTTPS 反向代理（nginx / Caddy），反向代理要保留 Host 头。
"""
import hashlib
import hmac
import secrets
import time

from kblib.core.common import KBError, load_settings, now
from kblib.core import db as _db
from kblib import lazy

_users = lazy('kblib.core.users')

SESS_DAYS = 30
LOOPBACK = ('127.0.0.1', 'localhost', '::1')
LOCK_AFTER = 5
LOCK_SEC = 15 * 60
BIND = {'host': '127.0.0.1'}          # 网页实际监听的地址（server.main 设置）：决定是不是单机模式
BAD_LOGIN = '用户名或密码不对'
MUST_CHANGE = '先修改初始密码'


def hash_pw(pw, salt=None, it=200_000):
    salt = salt or secrets.token_hex(16)
    return f'pbkdf2_sha256${it}${salt}${hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), it).hex()}'


def check_pw(pw, h):
    try:
        _, it, salt, _hex = h.split('$')
    except (ValueError, AttributeError):
        return False
    return hmac.compare_digest(hash_pw(pw, salt, int(it)), h)


_hash_pw, _check_pw = hash_pw, check_pw


def _tok_hash(t):
    return hashlib.sha256(t.encode()).hexdigest()


# ---------------------------------------------------------------- 模式
def multi_user_mode():
    """团队模式：有带密码的正常账号（要登录）。没有时是单机模式（免登录，本机用户身份）。"""
    return _db.user_count(with_pw=True) > 0


def auth_required():
    return multi_user_mode() or BIND['host'] not in LOOPBACK


def single_mode():
    return not auth_required()


def check_bind(host):
    """监听非本机地址必须先有账号（REQ-026）。"""
    BIND['host'] = host
    if host not in LOOPBACK and not multi_user_mode():
        raise KBError(f'要监听 {host}（别的机器能访问），先运行 kb user add 建管理员账号')


def set_password(pw):
    """兼容旧的 kb passwd：设 / 改管理员账号 admin 的密码（没有就建）。"""
    if not pw:
        raise KBError('访问密码改成账号了：取消登录请到「⚙ 设置 → 👥 成员」停用带密码的账号')
    if len(pw) < 8:
        raise KBError('访问密码至少 8 位')
    if _db.user_get('admin'):
        u = _db.user_get('admin')
        _db.user_put('admin', pw=hash_pw(pw), status='正常', pw_ver=(u.get('pw_ver') or 1) + 1, sys_role='管理员')
        _db.wsess_del(user='admin')
    else:
        _db.user_put('admin', pw=hash_pw(pw), sys_role='管理员', status='正常')
        _users.ensure_default_project()
        _db.member_set('default', 'admin', '负责人')
    return '已设置管理员账号 admin 的密码：之后打开网页用 admin 登录（kb user add 可以再建别的账号）'


# ---------------------------------------------------------------- 登录
def web_login(username, pw=None):
    """校验用户名 + 密码，成功返回 (会话令牌, 是否要先改密码)。只给一个参数时按旧用法当 admin 的密码。"""
    if pw is None:
        username, pw = 'admin', username
    username = (username or '').strip()
    u = _db.user_get(username)
    if u and u.get('status') == '停用':
        raise KBError('账号已停用，联系管理员')
    if u and (u.get('locked_until') or 0) > _db.now_ts():
        mins = int(((u['locked_until'] - _db.now_ts()) + 59) // 60)
        raise KBError(f'连续输错 {LOCK_AFTER} 次，已锁定，{mins} 分钟后再试')
    if not u or not u.get('pw') or not check_pw(pw or '', u['pw']):
        locked = _db.user_fail(username, LOCK_AFTER, LOCK_SEC) if u else False
        time.sleep(0.5)                # 拖慢暴力猜密码
        _db.audit('登录', username or '?', username, '', '拒绝', '锁定' if locked else '')
        raise KBError(BAD_LOGIN)
    t = secrets.token_urlsafe(32)
    _db.wsess_add(_tok_hash(t), username, u.get('pw_ver') or 1, _db.now_ts() + SESS_DAYS * 86400)
    _db.user_put(username, fails=0, locked_until=0, last_login=now())
    return t, u.get('status') == '待改密码'


def web_logout(t):
    if t:
        _db.wsess_del(h=_tok_hash(t))


def check_session(t):
    """Cookie → 用户字典（密码版本不对、账号停用都算失效）；无效返回 None。"""
    if not t:
        return None
    s = _db.wsess_get(_tok_hash(t))
    if not s:
        return None
    u = _db.user_get(s['user'])
    if not u or u.get('status') == '停用' or (u.get('pw_ver') or 1) != s['pw_ver']:
        _db.wsess_del(h=_tok_hash(t))
        return None
    u['_project'] = s.get('project') or ''
    return u


def session_set_project(t, project):
    _db.wsess_project(_tok_hash(t), project)


# ---------------------------------------------------------------- 个人令牌（kb_）
def token_add(name, user=None):
    """生成个人 MCP 令牌，明文只返回这一次。终端里 kb token add 不带用户 = 绑本机管理员（兼容旧用法）。"""
    name = (name or '').strip()
    if not name:
        raise KBError('给令牌起个名字，如「家里的 Mac」')
    user = user or _users.local_user()
    if any(x['name'] == name for x in _db.token_find(kind='个人', user=user)):
        raise KBError(f'已经有叫「{name}」的令牌；先删掉再建')
    t = 'kb_' + secrets.token_urlsafe(32)
    _db.token_put(_tok_hash(t), '个人', user=user, name=name)
    return t


def token_list(user=None):
    user = user or _users.local_user()
    return [{'名称': x['name'], '时间': x['created'], '最近使用': x['last_used'] or ''} for x in _db.token_find(kind='个人', user=user)]


def token_del(name, user=None):
    user = user or _users.local_user()
    xs = [x for x in _db.token_find(kind='个人', user=user) if x['name'] == name]
    if not xs:
        raise KBError(f'没有叫「{name}」的令牌')
    for x in xs:
        _db.token_delete(x['h'])
    return f'已删除令牌「{name}」，用它连接的地方会断开'


def _touch(r):
    if (r.get('last_used') or '')[:13] != _db.ts_str()[:13]:      # 一小时最多记一次
        _db.token_touch(r['h'])


def token_identity(t):
    """Bearer 令牌 → 身份字典 {kind, user, project, period, session, runner, engine}；无效返回 None。
    令牌的主人被停用、会话令牌已作废（会话结束 / 停止）都返回 None（REQ-005）。"""
    if not t:
        return None
    r = _db.token_get(_tok_hash(t))
    if not r:
        return None
    if r['user']:
        u = _db.user_get(r['user'])
        if not u or u.get('status') == '停用':
            return None
    _touch(r)
    return {'kind': r['kind'], 'user': r['user'] or '', 'project': r['project'] or '', 'period': r['period'] or '',
            'session': r['session'] or '', 'runner': r['runner'] or '', 'engine': r['engine'] or '', 'name': r['name'] or ''}


def check_token(t):
    i = token_identity(t)
    return bool(i) and i['kind'] == '个人'


# ---------------------------------------------------------------- 会话令牌（kbs_）
def session_token(user, project, period, session, engine='', ttl=0):
    """给后台 / 远程会话发令牌：绑发起人 + 项目 + 期 + 会话，会话结束 session_token_revoke。"""
    t = 'kbs_' + secrets.token_urlsafe(32)
    _db.token_put(_tok_hash(t), '会话', user=user, project=project, period=period, session=session,
                 expires=(_db.now_ts() + ttl) if ttl else 0, engine=engine)
    return t


def session_token_revoke(session):
    return _db.token_revoke(kind='会话', session=session)


# ---------------------------------------------------------------- 执行机令牌（kbr_ / kbreg_）
REG_HOURS = 24


def runner_reg_token(name='', by=''):
    t = 'kbreg_' + secrets.token_urlsafe(24)
    exp = _db.now_ts() + REG_HOURS * 3600
    _db.token_put(_tok_hash(t), '注册', user=by, name=name or '', expires=exp)
    return t, _db.ts_str(exp)


def runner_reg_consume(t):
    """一次性：用过就作废。无效返回 None。"""
    if not t or not t.startswith('kbreg_'):
        return None
    r = _db.token_get(_tok_hash(t))
    if not r or r['kind'] != '注册':
        return None
    _db.token_revoke(h=r['h'])
    return r


def runner_token(runner_id):
    _db.token_revoke(kind='执行机', runner=runner_id)
    t = 'kbr_' + secrets.token_urlsafe(32)
    _db.token_put(_tok_hash(t), '执行机', runner=runner_id, name=runner_id)
    return t


def legacy_settings_password():
    """旧版 设置.json 部署.密码（迁移用）。"""
    return (load_settings().get('部署') or {}).get('密码') or ''
