"""接口 · 我的、成员、项目、审计、定时任务（REQ-001~REQ-007 REQ-009 REQ-028）。"""
import json as _json
import urllib.request

from webapp.router import get, post  # noqa: F401
from webapp.shared import *  # noqa: F401,F403
from kblib.core import authz as _authz
from kblib.core import db as _kdb


def _me(self):
    return self.ident.get('user', '')


def _admin(self):
    if not _authz.is_admin(_me(self)):
        raise _authz.Denied('只有管理员能操作')


# ---------------------------------------------------------------- 我的
@get('/api/me')
def _get_api_me(self, u, q):
    return self._guard(lambda: kb.me_view(_me(self), self.ident.get('project', ''), bool(self.ident.get('single'))))


@post('/api/me/password', page_only=True)
def _post_api_me_password(self, u, n, body):
    from kblib.infra.deploy import _tok_hash
    ck = self._cookie()
    return self._guard(lambda: {'msg': kb.change_password(_me(self), body.get('old', ''), body.get('new', ''),
                                                         _tok_hash(ck) if ck else '')})


@post('/api/me/project', page_only=True)
def _post_api_me_project(self, u, n, body):
    def run():
        p = body.get('project', '')
        if p not in _authz.visible_projects(_me(self)):
            raise _authz.Denied(_authz.MSG_PROJ)
        kb.profile_save(_me(self), {'当前项目': p})
        return {'msg': f'已切换到「{(_kdb.project_get(p) or {}).get("name", p)}」'}
    return self._guard(run)


@get('/api/me/tokens')
def _get_api_me_tokens(self, u, q):
    return self._guard(lambda: {'items': kb.token_list(_me(self))})


@post('/api/me/token', page_only=True)
def _post_api_me_token(self, u, n, body):
    def run():
        t = kb.token_add(body.get('name', ''), _me(self))
        host = self.headers.get('Host', '127.0.0.1:11555')
        scheme = 'https' if self._https() else 'http'
        return {'msg': '令牌只显示这一次，请马上复制', 'token': t,
                'mcpAdd': f'claude mcp add --transport http kb {scheme}://{host}/mcp --header "Authorization: Bearer {t}"'}
    return self._guard(run)


@post('/api/me/token/delete', page_only=True)
def _post_api_me_token_delete(self, u, n, body):
    return self._guard(lambda: {'msg': kb.token_del(body.get('name', ''), _me(self))})


@post('/api/me/profile', page_only=True)
def _post_api_me_profile(self, u, n, body):
    def run():
        me = _me(self)
        patch = {}
        if isinstance(body.get('平台账号'), dict):
            patch['平台账号'] = {k: str(v).strip() for k, v in body['平台账号'].items() if str(v).strip()}
        if isinstance(body.get('通知'), dict):
            hook = str(body['通知'].get('webhook') or '').strip()
            if hook and not hook.startswith(('http://', 'https://')):
                raise kb.KBError('Webhook 要是 http(s):// 开头的地址')
            patch['通知'] = {'webhook': hook}
        if body.get('AI账号') in ('自己的', '项目共享'):
            patch['AI账号'] = body['AI账号']
        for plat, pw in (body.get('平台密码') or {}).items():          # 个人机密：只写不读
            if pw:
                kb.personal_secret_put(me, f'平台/{plat}', pw)
        g = body.get('gerrit') or {}
        if g.get('用户名'):
            patch['gerrit'] = {'用户名': g['用户名']}
        if g.get('密钥'):
            kb.personal_secret_put(me, 'gerrit', g['密钥'])
        kb.profile_save(me, patch)
        out = {'msg': '已保存'}
        if '平台账号' in patch and patch['平台账号']:
            ok, msg = kb.user_bug_test(me)                              # 保存后立即测试连接（REQ-006）
            out['test'] = {'ok': ok, 'msg': msg}
            out['msg'] = '已保存；' + ('连接正常' if ok else f'连接失败：{msg}')
        return out
    return self._guard(run)


def notify_test(hook):
    data = _json.dumps({'msg_type': 'text', 'content': {'text': '知识库测试消息：通知地址可用'},
                        'msgtype': 'text', 'text': {'content': '知识库测试消息：通知地址可用'}}).encode()
    req = urllib.request.Request(hook, data=data, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return True, f'已发送（HTTP {r.status}）'
    except urllib.error.HTTPError as e:
        return False, f'HTTP {e.code}'
    except Exception as e:
        return False, f'发不出去：{e}'


@post('/api/me/test', page_only=True)
def _post_api_me_test(self, u, n, body):
    def run():
        me = _me(self)
        if body.get('kind') == 'notify':
            hook = (kb.profile_get(me).get('通知') or {}).get('webhook', '')
            if not hook:
                return {'ok': False, 'msg': '还没有填通知地址'}
            ok, msg = notify_test(hook)
            return {'ok': ok, 'msg': msg}
        ok, msg = kb.user_bug_test(me)
        return {'ok': ok, 'msg': msg}
    return self._guard(run)


# ---------------------------------------------------------------- 成员（管理员）
@get('/api/users')
def _get_api_users(self, u, q):
    def run():
        _admin(self)
        return {'items': kb.users_list()}
    return self._guard(run)


@post('/api/user', page_only=True)
def _post_api_user(self, u, n, body):
    def run():
        _admin(self)
        if body.get('新建'):
            return {'msg': kb.user_create_strict(body.get('username', ''), body.get('password', ''), body.get('系统角色') or '普通', _me(self))}
        return {'msg': kb.user_save(body.get('username', ''), body.get('password'), body.get('系统角色'), _me(self))}
    return self._guard(run)


@post('/api/user/disable', page_only=True)
def _post_api_user_disable(self, u, n, body):
    def run():
        _admin(self)
        return {'msg': kb.user_disable(body.get('username', ''), body.get('handover'), _me(self))}
    return self._guard(run)


@post('/api/user/enable', page_only=True)
def _post_api_user_enable(self, u, n, body):
    def run():
        _admin(self)
        return {'msg': kb.user_enable(body.get('username', ''), _me(self))}
    return self._guard(run)


# ---------------------------------------------------------------- 项目
@get('/api/projects')
def _get_api_projects(self, u, q):
    return self._guard(lambda: {'items': kb.projects_for(_me(self))})


@post('/api/project', page_only=True)
def _post_api_project(self, u, n, body):
    def run():
        _admin(self)
        return kb.project_save(body.get('名称', ''), body.get('id') or None, body.get('负责人'), _me(self))
    return self._guard(run)


@post('/api/project/member', page_only=True)
def _post_api_project_member(self, u, n, body):
    def run():
        _authz.check(self.ident, '项目管理', project=body.get('project', ''))
        return {'msg': kb.project_member(body.get('project', ''), body.get('username', ''), body.get('角色') or None, _me(self))}
    return self._guard(run)


@post('/api/period/owner', page_only=True)
def _post_api_period_owner(self, u, n, body):
    """改期 / 事项负责人或所属项目：期负责人、项目负责人或管理员；换项目要两边项目负责人或管理员（REQ-003 REQ-030）。"""
    def run():
        name = body.get('name', '')
        info = kb.period_info(name)
        me = _me(self)
        if not _authz.is_admin(me):
            if _authz.project_role(me, info['项目']) != '负责人' and not (body.get('project') is None and info['负责人'] == me):
                raise _authz.Denied('只有本期负责人或项目负责人能操作')
            if body.get('project') and _authz.project_role(me, body['project']) != '负责人':
                raise _authz.Denied('改归属要两边的项目负责人或管理员')
        if body.get('负责人') and not _kdb.user_get(body['负责人']):
            raise kb.KBError(f'没有账号 {body["负责人"]}')
        if body.get('project') and not _kdb.project_get(body['project']):
            raise kb.KBError('没有这个项目')
        return {'msg': kb.period_set_owner(name, body.get('负责人'), body.get('project'), body.get('version'))}
    return self._guard(run)


# ---------------------------------------------------------------- 审计（管理员 / 项目负责人）
@get('/api/audit')
def _get_api_audit(self, u, q):
    def run():
        me = _me(self)
        projects = None
        if not _authz.is_admin(me):
            projects = [p for p, r in _kdb.member_roles(me).items() if r == '负责人']
            if not projects:
                raise _authz.Denied('只有管理员或项目负责人能看审计')
        import time as _t
        since = q.get('since', '')
        floor = _kdb.now_ts() - 90 * 86400
        try:
            ts = _t.mktime(_t.strptime(since, '%Y-%m-%d')) if since else _kdb.now_ts() - 30 * 86400
        except ValueError:
            raise kb.KBError('since 要是 YYYY-MM-DD')
        size = min(max(int(q.get('size') or 50), 1), 200)
        total, rows = _kdb.audit_query(kind=q.get('kind') or '机密', project=q.get('project') or None, subject=q.get('user') or None,
                                        obj=q.get('secret') or None, since=max(ts, floor), page=max(int(q.get('page') or 1), 1),
                                        size=size, projects=projects)
        return {'total': total, 'items': [{'时间': _kdb.ts_str(r['ts']), '主体': r['subject'], '机密': r['obj'], '项目': r['project'],
                                           '结果': r['result'], '说明': r['detail']} for r in rows]}
    return self._guard(run)


@get('/api/schedules')
def _get_api_schedules(self, u, q):
    return self._guard(lambda: {'items': [{'名称': r['name'], '周期秒': r['period'], '上次运行': _kdb.ts_str(r['last_run']) if r['last_run'] else '',
                                           '结果': r['last_result'] or '', '持有者': r['holder'] or '',
                                           '租约到': _kdb.ts_str(r['lease_until']) if r['lease_until'] else '', '次数': r['runs']}
                                          for r in _kdb.schedules()]})
