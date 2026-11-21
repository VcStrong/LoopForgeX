"""接口 · 执行机、任务队列、监控告警（REQ-011~REQ-020 REQ-029 REQ-035 REQ-036）。

/api/runner/*：执行机自己调，Bearer 执行机令牌 kbr_（注册用一次性 kbreg_），server.do_POST 绕过 Cookie 直接分发到这里。
"""
import json as _json

from webapp.router import get, post  # noqa: F401
from webapp.shared import *  # noqa: F401,F403
from kblib.core import authz as _authz
from kblib.core import db as _kdb
from kblib.core import monitor as _mon
from kblib.core import queue as _q
from kblib.infra import deploy as _dep


def _me(self):
    return (getattr(self, 'ident', None) or {}).get('user', '')


def _admin(self):
    if not _authz.is_admin(_me(self)):
        raise _authz.Denied('只有管理员能操作')


# ---------------------------------------------------------------- 队列（网页）
@get('/api/queue')
def _get_api_queue(self, u, q):
    return self._guard(lambda: {'items': _q.queue_view(self.ident, q.get('project', ''), q.get('period', ''), q.get('mine') == '1',
                                                       q.get('status', ''))})


@get('/api/task')
def _get_api_task(self, u, q):
    def run():
        t = _q.task(q.get('id', ''))
        if t.get('period') and (kb.PERIODS / t['period']).is_dir():      # 期的任务日志和期资料一样全平台可看（T003）
            _authz.check(self.ident, '看', t['period'])
        elif t.get('project') and not _authz.is_admin(_me(self)) and t['project'] not in _authz.visible_projects(_me(self)):
            raise _authz.Denied(_authz.MSG_PROJ)
        return {**_q.view(t), **_q.log_tail(t['id'], q.get('offset') or 0)}
    return self._guard(run)


@post('/api/queue/cancel', page_only=True)
def _post_api_queue_cancel(self, u, n, body):
    return self._guard(lambda: {'msg': _q.cancel(str(body.get('id', '')), self.ident)})


@post('/api/quota', page_only=True)
def _post_api_quota(self, u, n, body):
    def run():
        _admin(self)
        return {'msg': _q.quota_save(body, _me(self))}
    return self._guard(run)


@get('/api/quota')
def _get_api_quota(self, u, q):
    return self._guard(lambda: _q.quota())


@get('/api/task/score')
def _get_api_task_score(self, u, q):
    return self._guard(lambda: _q.score_view(q.get('id', '')))


# ---------------------------------------------------------------- 执行机管理（管理员）
@get('/api/runners')
def _get_api_runners(self, u, q):
    def run():
        _admin(self)
        host = self.headers.get('Host', '127.0.0.1:11555')
        scheme = 'https' if self._https() else 'http'
        return {'items': _q.runners_view(), 'protocol': _q.PROTO_VERSION,
                'installHelp': f'在要接入的机器上运行：kb runner --server {scheme}://{host} --token <注册令牌>'
                               '（注册令牌在本页「生成注册令牌」拿，24 小时内有效、只能用一次）'}
    return self._guard(run)


@post('/api/runners/token', page_only=True)
def _post_api_runners_token(self, u, n, body):
    def run():
        _admin(self)
        t, exp = _dep.runner_reg_token(str(body.get('name') or ''), _me(self))
        host = self.headers.get('Host', '127.0.0.1:11555')
        scheme = 'https' if self._https() else 'http'
        _kdb.audit('执行机', _me(self), '注册令牌', '', '成功', f'生成（{body.get("name") or "未命名"}）')
        return {'token': t, 'expires': exp, 'command': f'kb runner --server {scheme}://{host} --token {t}',
                'msg': '注册令牌只显示这一次，24 小时内有效、只能用一次'}
    return self._guard(run)


@post('/api/runners/update', page_only=True)
def _post_api_runners_update(self, u, n, body):
    def run():
        _admin(self)
        return {'msg': _q.runner_update(str(body.get('id', '')), body, _me(self))}
    return self._guard(run)


@get('/api/runners/metrics')
def _get_api_runners_metrics(self, u, q):
    def run():
        _admin(self)
        if not _q.runner(q.get('id', '')):
            raise kb.KBError('没有这台执行机')
        return _mon.metrics_view(q.get('id', ''), q.get('hours') or 24)
    return self._guard(run)


@get('/api/alerts')
def _get_api_alerts(self, u, q):
    def run():
        _admin(self)
        return {'告警': _mon.alerts_view(q.get('state') or '进行中')}
    return self._guard(run)


@post('/api/alerts/ack', page_only=True)
def _post_api_alerts_ack(self, u, n, body):
    def run():
        _admin(self)
        return {'msg': _mon.alert_ack(str(body.get('id', '')), _me(self), body.get('静默分钟') or 0)}
    return self._guard(run)


# ---------------------------------------------------------------- 执行机协议（Bearer）
def _read(self, n):
    try:
        b = _json.loads(self.rfile.read(n) or b'{}')
    except ValueError:
        return None
    return b if isinstance(b, dict) else None


def _runner_of(self):
    i = _dep.token_identity(self._bearer())
    if not i or i['kind'] != '执行机' or not i['runner']:
        return None
    r = _q.runner(i['runner'])
    if not r or r.get('disabled'):
        return None
    return r['id']


def _rpc(self, n, fn):
    """执行机接口：鉴权 + 解析 + 统一错误。fn(rid, body) → dict。"""
    rid = _runner_of(self)
    if not rid:
        return self._send(401, {'error': '执行机令牌无效（被停用、删除或已换绑），重新注册：kb runner --server <地址> --token <注册令牌>',
                                'login': True})
    body = _read(self, n)
    if body is None:
        return self._send(400, {'error': '请求体不是 JSON'})
    return self._guard(lambda: fn(rid, body))


@post('/api/runner/register')
def _post_api_runner_register(self, u, n, body):
    reg = _dep.runner_reg_consume(self._bearer())
    if not reg:
        return self._send(401, {'error': '注册令牌无效或已用过，到网页重新生成', 'login': True})
    b = _read(self, n)
    if b is None:
        return self._send(400, {'error': '请求体不是 JSON'})
    if not b.get('name') or not b.get('version'):
        return self._send(400, {'error': '要带 name 和 version'})
    return self._guard(lambda: _q.register(reg, b))


@post('/api/runner/heartbeat')
def _post_api_runner_heartbeat(self, u, n, body):
    return _rpc(self, n, _q.heartbeat)


@post('/api/runner/claim')
def _post_api_runner_claim(self, u, n, body):
    return _rpc(self, n, lambda rid, b: {'task': _q.claim(rid, b, b.get('等待秒'))})


@post('/api/runner/ack')
def _post_api_runner_ack(self, u, n, body):
    return _rpc(self, n, lambda rid, b: {'msg': _q.ack(rid, str(b.get('task', '')), b.get('副本'))})


@post('/api/runner/event')
def _post_api_runner_event(self, u, n, body):
    return _rpc(self, n, lambda rid, b: _q.event(rid, str(b.get('task', '')), b))


@post('/api/runner/done')
def _post_api_runner_done(self, u, n, body):
    return _rpc(self, n, lambda rid, b: {'msg': _q.done(rid, str(b.get('task', '')), b)})
