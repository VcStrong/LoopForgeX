"""用户、项目、成员（REQ-001 REQ-002 REQ-003 REQ-028）。登录 / 令牌在 infra/deploy.py。

  本机用户：单机模式（只听 127.0.0.1、没有任何带密码的账号）免登录时的身份，系统角色 管理员（REQ-026）。
  默认项目：升级前的老数据都归它，本机用户是负责人（REQ-003）。
"""
import getpass
import re
import secrets

from kblib.core.common import KBError, KBNeed, current_ctx, now
from kblib.core import db as _db
from kblib import lazy

_deploy = lazy('kblib.infra.deploy')
_periods = lazy('kblib.core.periods')
_queue = lazy('kblib.core.queue')
_baseinfo = lazy('kblib.core.baseinfo')

DEFAULT_PROJECT = 'default'
DEFAULT_PROJECT_NAME = '默认项目'
SYS_ROLES = ('管理员', '普通')
PROJ_ROLES = ('负责人', '成员', '只读')
NAME_RE = re.compile(r'^[\w.\-@一-鿿]{2,32}$')


def local_user():
    """本机用户名（单机模式的身份）。第一次用时建账号（管理员、无密码）和默认项目。"""
    for u in _db.user_list():
        if u.get('local'):
            return u['name']
    name = re.sub(r'[^\w.\-@]', '', getpass.getuser() or '') or 'me'
    if _db.user_get(name):
        _db.user_put(name, local=1)
    else:
        _db.user_put(name, sys_role='管理员', status='正常', local=1)
    ensure_default_project(name)
    return name


def ensure_default_project(owner=None):
    if not _db.project_get(DEFAULT_PROJECT):
        _db.project_put(DEFAULT_PROJECT, DEFAULT_PROJECT_NAME, owner or local_user())
    return DEFAULT_PROJECT


def default_project_for(project=''):
    if project and _db.project_get(project):
        return project
    return ensure_default_project()


# ---------------------------------------------------------------- 用户
def user_view(u):
    roles = _db.member_roles(u['name'])
    return {'用户名': u['name'], '系统角色': u.get('sys_role') or '普通', '状态': u.get('status') or '正常',
            '项目': [{'项目': p, '角色': r} for p, r in roles.items()], '最近登录': u.get('last_login') or '',
            '本机用户': bool(u.get('local'))}


def users_list():
    return [user_view(u) for u in _db.user_list()]


def user_save(username, password=None, sys_role=None, by=''):
    """新建（必须带初始密码）或修改（改角色、重置密码）。新建 / 重置后状态是「待改密码」。"""
    username = (username or '').strip()
    if not NAME_RE.match(username):
        raise KBError('用户名 2~32 位：字母、数字、汉字、. - _ @')
    if sys_role and sys_role not in SYS_ROLES:
        raise KBError('系统角色只能是 管理员 / 普通')
    if password is not None and password != '' and len(password) < 8:
        raise KBError('密码至少 8 位')
    u = _db.user_get(username)
    if u is None:
        if not password:
            raise KBError('新建账号要给初始密码（至少 8 位）')
        _db.user_put(username, pw=_deploy.hash_pw(password), sys_role=sys_role or '普通', status='待改密码', pw_ver=1)
        ensure_default_project()
        _db.audit('用户', by or 'kb', username, '', '成功', '新建')
        return f'已新建账号 {username}（首次登录要改密码）'
    if password is not None and password == '' and sys_role is None:
        raise KBError('用户名已存在')
    f = {}
    if sys_role:
        if u.get('sys_role') == '管理员' and sys_role != '管理员' and _admin_count() <= 1:
            raise KBError('至少要留一个管理员')
        f['sys_role'] = sys_role
    if password:
        f.update(pw=_deploy.hash_pw(password), status='待改密码' if u.get('status') != '停用' else '停用',
                 pw_ver=(u.get('pw_ver') or 1) + 1, fails=0, locked_until=0)
    if not f:
        raise KBError('用户名已存在')
    _db.user_put(username, **f)
    if password:
        _db.wsess_del(user=username)               # 改密码：他已登录的浏览器都要重新登录（REQ-001）
    _db.audit('用户', by or 'kb', username, '', '成功', '重置密码' if password else f'系统角色 → {sys_role}')
    return f'已更新账号 {username}' + ('（已重置密码，下次登录要改）' if password else '')


def user_create_strict(username, password, sys_role='普通', by=''):
    if _db.user_get((username or '').strip()):
        raise KBError('用户名已存在')
    return user_save(username, password, sys_role, by)


def _admin_count():
    return sum(1 for u in _db.user_list() if u.get('sys_role') == '管理员' and u.get('status') != '停用')


def change_password(username, old, new, keep_session=''):
    u = _db.user_get(username)
    if not u:
        raise KBError('没有这个账号')
    if len(new or '') < 8:
        raise KBError('新密码至少 8 位')
    if u.get('pw') and not _deploy.check_pw(old or '', u['pw']):
        raise KBError('原密码不对')
    if old == new:
        raise KBError('新密码不能和原密码一样')
    ver = (u.get('pw_ver') or 1) + 1
    _db.user_put(username, pw=_deploy.hash_pw(new), status='正常' if u.get('status') != '停用' else '停用', pw_ver=ver)
    if keep_session:                       # 自己改密码：当前浏览器留着，别处登录的失效
        _db.wsess_keep(keep_session, ver)
    _db.audit('用户', username, username, '', '成功', '改密码')
    return '密码已修改'


def handover_need(username):
    """停用前要交接的：他负责的期 / 事项、在跑的任务。"""
    owned = [r['name'] for r in _db.period_rows() if r['owner'] == username]
    tasks = [t['id'] for t in _db.task_queue_rows(('排队', '已派', '运行', '等回答')) if t['initiator'] == username]
    projs = [p['id'] for p in _db.project_list() if p['owner'] == username]
    return {'期': owned, '在跑任务': tasks, '负责项目': projs}


def user_disable(username, handover=None, by=''):
    """停用并交接（REQ-028）：令牌 / 会话令牌作废、个人机密删除、负责的期转交、在跑任务取消或转交。"""
    u = _db.user_get(username)
    if not u:
        raise KBError('没有这个账号')
    if u.get('sys_role') == '管理员' and _admin_count() <= 1:
        raise KBError('至少要留一个管理员')
    need = handover_need(username)
    if not handover or not handover.get('新负责人'):
        raise KBNeed(f'停用 {username} 前要选新负责人：负责 {len(need["期"])} 个期 / 事项，{len(need["在跑任务"])} 个在跑任务', need)
    new = handover['新负责人']
    nu = _db.user_get(new)
    if not nu or nu.get('status') == '停用' or new == username:
        raise KBError('新负责人要是另一个正常的账号')
    with _db.tx():
        _db.user_put(username, status='停用')
        _db.wsess_del(user=username)
        _db.token_revoke(user=username)
        for n in need['期']:
            _db.period_set_attr(n, owner=new, by=by)
        for pid in need['负责项目']:
            p = _db.project_get(pid)
            _db.project_put(pid, p['name'], new)
    for s in _db.secret_meta_by_owner(username):
        try:
            if s['id'].startswith('U:'):
                personal_secret_delete(username, s['id'].split('/', 1)[1])
            else:
                _baseinfo.base_secret_entry_delete(s['id'])
        except Exception:
            pass
        _db.secret_meta_del(s['id'])
    mode = handover.get('在跑任务') or '取消'
    for tid in need['在跑任务']:
        try:
            if mode == '转交':
                _db.update('tasks', {'id': tid}, initiator=new)
            else:
                _queue.task_cancel(tid, by=by or 'kb')
        except Exception:
            pass
    _db.audit('用户', by or 'kb', username, '', '成功', f'停用，交接给 {new}，在跑任务{mode}')
    return f'已停用 {username}：{len(need["期"])} 个期 / 事项转给 {new}，{len(need["在跑任务"])} 个在跑任务已{mode}'


def user_enable(username, by=''):
    u = _db.user_get(username)
    if not u:
        raise KBError('没有这个账号')
    _db.user_put(username, status='待改密码' if u.get('pw') else '正常', fails=0, locked_until=0)
    _db.audit('用户', by or 'kb', username, '', '成功', '启用')
    return f'已启用 {username}'


PERSONAL_SERVICE = 'kb-user'


def personal_secret_put(user, key, value):
    """个人机密（平台密码、Gerrit 密钥、AI 登录态）：存钥匙串 / 加密文件，登记为「个人」层（REQ-007 REQ-017）。"""
    from kblib.infra import vault
    vault.put(PERSONAL_SERVICE, f'{user}/{key}', value)
    _db.secret_meta_put(f'U:{user}/{key}', '个人', user, '')


def personal_secret_get(user, key, ctx=None):
    """只给本人和他发起的会话；每次读写审计。"""
    from kblib.infra import vault
    c = current_ctx() if ctx is None else ctx
    sid = f'U:{user}/{key}'
    if not _db.secret_meta(sid):
        return None
    ok = not c.get('via') or (c.get('user') == user and c.get('via') != '管家')
    subj = c.get('user') or '系统' if c.get('via') != 'AI' else f'会话 {c.get("session", "?")}（发起人 {c.get("user", "?")}）'
    _db.audit('机密', subj, sid, '', '成功' if ok else '拒绝', '' if ok else '这是别人的个人机密')
    if not ok:
        raise KBError('不能读别人的个人机密')
    return vault.get(PERSONAL_SERVICE, f'{user}/{key}')


def personal_secret_delete(user, key):
    from kblib.infra import vault
    try:
        vault.delete(PERSONAL_SERVICE, f'{user}/{key}')
    finally:
        _db.secret_meta_del(f'U:{user}/{key}')


def profile_get(username):
    return (_db.user_get(username) or {}).get('profile') or {}


def profile_save(username, patch):
    u = _db.user_get(username)
    if not u:
        raise KBError('没有这个账号')
    p = dict(u.get('profile') or {})
    for k, v in patch.items():
        if v is None:
            p.pop(k, None)
        else:
            p[k] = v
    _db.user_put(username, profile=p)
    return p


# ---------------------------------------------------------------- 项目
def project_view(p, user=''):
    from kblib.core import authz
    ms = _db.members_of(p['id'])
    return {'id': p['id'], '名称': p['name'], '负责人': p['owner'] or '',
            '我的角色': authz.project_role(user, p['id']) if user else '',
            '成员': [{'用户名': m['user'], '角色': m['role']} for m in ms]}


def projects_for(user):
    from kblib.core import authz
    vis = set(authz.visible_projects(user))
    return [project_view(p, user) for p in _db.project_list() if p['id'] in vis]


def project_save(name, pid=None, owner=None, by=''):
    name = (name or '').strip()
    if not name:
        raise KBError('项目名称不能为空')
    if owner and not _db.user_get(owner):
        raise KBError(f'没有账号 {owner}')
    if pid:
        p = _db.project_get(pid)
        if not p:
            raise KBError('没有这个项目')
        other = _db.project_get(name)
        if other and other['id'] != p['id']:
            raise KBError('项目名称已存在')
        _db.project_put(p['id'], name, owner if owner is not None else p['owner'])
        _db.audit('项目', by, p['id'], p['id'], '成功', f'修改：{name}')
        return {'msg': f'已修改项目 {name}', 'id': p['id']}
    if _db.project_get(name):
        raise KBError('项目名称已存在')
    pid = 'p' + secrets.token_hex(4)
    _db.project_put(pid, name, owner or by or local_user())
    _db.audit('项目', by, pid, pid, '成功', f'新建：{name}')
    return {'msg': f'已新建项目 {name}', 'id': pid}


def project_member(project, username, role=None, by=''):
    p = _db.project_get(project)
    if not p:
        raise KBError('没有这个项目')
    if not _db.user_get(username):
        raise KBError(f'没有账号 {username}')
    if role and role not in PROJ_ROLES:
        raise KBError('角色只能是 负责人 / 成员 / 只读')
    if not role and p['owner'] == username:
        raise KBError('项目负责人不能移出：先把负责人改成别人')
    _db.member_set(p['id'], username, role or None)
    _db.audit('项目', by, username, p['id'], '成功', f'角色 → {role or "移出"}')
    return f'{username} 在「{p["name"]}」：' + (role or '已移出')


def current_project_for(user, wanted=''):
    """当前项目：要的那个有权限就用它，否则用他参与的第一个。"""
    from kblib.core import authz
    vis = authz.visible_projects(user)
    if wanted and wanted in vis:
        return wanted
    if DEFAULT_PROJECT in vis:
        return DEFAULT_PROJECT
    return vis[0] if vis else ''


def me_view(user, project='', single=False):
    u = _db.user_get(user) or {'name': user}
    prof = u.get('profile') or {}
    accts = {k: v for k, v in (prof.get('平台账号') or {}).items()}
    ai = '自己的' if prof.get('AI账号') == '自己的' else ('项目共享' if prof.get('AI账号') == '项目共享' else '未绑定')
    return {'用户名': user, '系统角色': u.get('sys_role') or '普通', '状态': u.get('status') or '正常', '单机模式': bool(single),
            '当前项目': current_project_for(user, project), '项目': projects_for(user), '平台账号': accts,
            '通知': {'webhook': (prof.get('通知') or {}).get('webhook', '')}, 'AI账号': ai}


def ctx_user():
    return current_ctx().get('user', '')


def now_str():
    return now()
