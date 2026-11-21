"""接口 · 资源池、AI 引擎账号、用量预算、测试账号、设备提供方、产物与证据（REQ-018 REQ-038~REQ-050）。"""
import hashlib
import json as _json
import mmap
import os
import shutil
import tempfile
import uuid
from pathlib import Path

from webapp.router import get, post  # noqa: F401
from webapp.shared import *  # noqa: F401,F403
from kblib.ai import accounts as _acc
from kblib.core import authz as _authz
from kblib.core import db as _kdb
from kblib.core import resources as _res
from kblib.devices import providers as _prov
from kblib.infra import deploy as _dep

EVI_MAX = 20 * 1024 * 1024


def _me(self):
    return (getattr(self, 'ident', None) or {}).get('user', '')


def _admin(self):
    if not _authz.is_admin(_me(self)):
        raise _authz.Denied('只有管理员能操作')


def _owner_or_admin(self, project):
    me = _me(self)
    if not (_authz.is_admin(me) or _authz.project_role(me, project) == '负责人'):
        raise _authz.Denied('只有项目负责人或管理员能操作')


def _projects(self):
    """当前登录人能看的项目；管理员 None（不限）。"""
    me = _me(self)
    return None if _authz.is_admin(me) else set(_authz.visible_projects(me))


def _see_project(self, project):
    ps = _projects(self)
    if ps is not None and project and project not in ps:
        raise _authz.Denied(_authz.MSG_PROJ)


def _runner_of(self):
    """Bearer 执行机令牌 kbr_ → 执行机编号；无效返回空。"""
    i = _dep.token_identity(self._bearer())
    if not i or i['kind'] != '执行机' or not i['runner']:
        return ''
    r = _kdb.get('runners', id=i['runner'])
    return r['id'] if r and not r.get('disabled') else ''


# ---------------------------------------------------------------- AI 引擎账号（REQ-018 REQ-038 REQ-041 REQ-042）
@get('/api/engine-accounts')
def _get_engine_accounts(self, u, q):
    return self._guard(lambda: {'账号': [_acc.view(a) for a in _acc.visible(_me(self), q.get('project') or None)]})


@post('/api/engine-account')
def _post_engine_account(self, u, n, body):
    def run():
        msg, aid = _acc.save(body or {}, _me(self))
        return {'msg': msg, 'id': aid}
    return self._guard(run)


@post('/api/engine-account/probe')
def _post_engine_account_probe(self, u, n, body):
    def run():
        aid = str(body.get('id') or '')
        if aid not in {a['id'] for a in _acc.visible(_me(self))}:
            raise _authz.Denied('看不到这个账号')
        return _acc.probe(aid)
    return self._guard(run)


@post('/api/engine-account/fallback', page_only=True)
def _post_engine_account_fallback(self, u, n, body):
    """「我的 → AI 账号」：个人账号故障时同意回落到项目共享账号（只问一次，同意后记住）。"""
    def run():
        kb.profile_save(_me(self), {'AI回落同意': bool(body.get('同意', True))})
        return {'msg': '已记住：个人账号不可用时用项目共享账号' if body.get('同意', True) else '已取消回落同意'}
    return self._guard(run)


# ---------------------------------------------------------------- 用量与预算（REQ-040）
@get('/api/usage')
def _get_usage(self, u, q):
    def run():
        by = q.get('by') or '项目'
        proj = q.get('project') or None
        if proj:
            _see_project(self, proj)
        rows = _acc.usage_report(by, int(q.get('days') or 30), proj)
        ps = _projects(self)
        if ps is not None:
            me = _me(self)
            if by == '人':
                rows = [r for r in rows if r['对象'] == me]
            elif by == '项目':
                rows = [r for r in rows if r['对象'] in ps]
            else:
                names = {a['name'] for a in _acc.visible(me)}
                rows = [r for r in rows if r['对象'] in names]
        return {'行': rows}
    return self._guard(run)


@post('/api/budget')
def _post_budget(self, u, n, body):
    def run():
        scope, obj = body.get('范围'), str(body.get('对象') or '')
        if scope == '人':
            _admin(self)
        elif scope == '项目':
            _owner_or_admin(self, obj)
        else:
            raise kb.KBError('范围只能是 人 / 项目')
        return {'msg': _acc.budget_set(scope, obj, body.get('日上限'), body.get('月上限'), body.get('超额策略'), body.get('降级模型'), _me(self))}
    return self._guard(run)


@get('/api/budget')
def _get_budget(self, u, q):
    def run():
        b = _acc.budget_get(q.get('范围') or q.get('scope') or '项目', q.get('对象') or q.get('obj') or '') or {}
        return {'日上限': b.get('day') or 0, '月上限': b.get('month') or 0, '超额策略': b.get('policy') or '排队', '降级模型': b.get('cheap') or ''}
    return self._guard(run)


@post('/api/budget/grant', page_only=True)
def _post_budget_grant(self, u, n, body):
    def run():
        proj = str(body.get('项目') or '')
        _owner_or_admin(self, proj)
        return {'msg': _acc.grant(proj, body.get('额度'), body.get('原因') or '', _me(self))}
    return self._guard(run)


# ---------------------------------------------------------------- 资源池（REQ-045 REQ-047 REQ-048）
@get('/api/resources')
def _get_resources(self, u, q):
    def run():
        t = q.get('type') or None
        if t and t not in _res.TYPES:
            raise kb.KBError(f'type 只能是：{" / ".join(_res.TYPES)}')
        proj = q.get('project') or None
        if proj:
            _see_project(self, proj)
        items = _res.overview(t, proj)
        ps = _projects(self)
        if ps is not None:
            items = [r for r in items if r['类型'] == '手机' or (r['属性'].get('项目') or '') in ps or
                     (r['属性'].get('所属人') == _me(self))]
        return {'资源': items}
    return self._guard(run)


@post('/api/resource/release')
def _post_resource_release(self, u, n, body):
    def run():
        rid, sess, force = str(body.get('资源') or ''), body.get('会话') or None, bool(body.get('强制'))
        hs = [h for h in _res.holds_of(rid) if sess is None or h['session'] == sess]
        if not hs:
            raise kb.KBError('没有这个占用（可能已经释放了）')
        me = _me(self)
        if force:
            _admin(self)
        elif not _authz.is_admin(me) and any(h.get('user') != me for h in hs):
            raise _authz.Denied('只有占用者或管理员能释放；管理员可「强制释放」')
        return {'msg': _res.release(rid, sess, by=me, force=force)}
    return self._guard(run)


# ---------------------------------------------------------------- 测试账号（REQ-046）
@get('/api/test-accounts')
def _get_test_accounts(self, u, q):
    def run():
        proj = q.get('project') or None
        if proj:
            _see_project(self, proj)
        env = q.get('env') or None
        if env and env not in kb.TA_ENVS:
            raise kb.KBError(f'环境只能是：{" / ".join(kb.TA_ENVS)}')
        ps = _projects(self)
        return {'账号': kb.test_account_list(proj, env, ps)}
    return self._guard(run)


@post('/api/test-account')
def _post_test_account(self, u, n, body):
    def run():
        me = _me(self)
        old = kb.test_account_get(body.get('id')) if body.get('id') else None
        proj = (old or {}).get('project') or body.get('项目') or self.ident.get('project') or 'default'
        if old or body.get('删除'):
            _owner_or_admin(self, proj)                      # 删改限项目负责人
            if body.get('项目') and body['项目'] != proj:
                _owner_or_admin(self, body['项目'])
        else:
            role = _authz.project_role(me, proj)
            if not _authz.is_admin(me) and role not in ('负责人', '成员'):
                raise _authz.Denied(_authz.MSG_RO if role == '只读' else _authz.MSG_PROJ)
            if body.get('环境') == '线上' and body.get('允许AI') and role != '负责人' and not _authz.is_admin(me):
                raise _authz.Denied('线上账号放开给 AI 要项目负责人操作')
        b = dict(body, 项目=body.get('项目') or proj)
        msg, tid = kb.test_account_save(b, by=me)
        return {'msg': msg, 'id': tid}
    return self._guard(run)


# ---------------------------------------------------------------- 设备提供方（REQ-043）
@get('/api/device-providers')
def _get_device_providers(self, u, q):
    return self._guard(lambda: {'提供方': _prov.listing()})


@post('/api/device-provider')
def _post_device_provider(self, u, n, body):
    def run():
        _admin(self)
        if not body.get('id'):
            raise kb.KBError('要指定提供方 id')
        return {'msg': _prov.configure(str(body['id']), body.get('启用'), body.get('配置'), body.get('模拟行为'), _me(self))}
    return self._guard(run)


@post('/api/device-provider/test', page_only=True)
def _post_device_provider_test(self, u, n, body):
    """走一遍 申请→安装→执行→截图 / 日志→释放（模拟平台验收用）。"""
    def run():
        _admin(self)
        return _prov.session_run(str(body.get('id') or 'mock'), body.get('条件') or {}, int(body.get('台数') or 1),
                                 f'test:{uuid.uuid4().hex[:8]}', user=_me(self), artifact=body.get('产物') or 'demo.apk',
                                 cmds=body.get('命令') or ['echo hello'])
    return self._guard(run)


# ---------------------------------------------------------------- 产物（REQ-049）
def _task(tid):
    return _kdb.get('tasks', id=tid) if tid else None


def _art_view(a):
    return {'id': a['id'], '版本': a.get('version') or '', '分支': a.get('branch') or '', '提交': a.get('commit_id') or '',
            'sha256': a.get('sha256') or '', '大小': a.get('size') or 0, '执行机': a.get('runner') or '', '时间': _kdb.ts_str(a['ts']),
            '任务': a.get('task') or '', '可下载': bool(a.get('path') and Path(a['path']).is_file())}


@get('/api/artifacts')
def _get_artifacts(self, u, q):
    def run():
        p = q.get('period') or ''
        if not p:
            raise kb.KBError('要指定 period')
        return {'产物': [_art_view(a) for a in _kdb.rows('artifacts', {'period': p}, order='ts DESC')]}
    return self._guard(run)


@get('/api/artifact/download')
def _get_artifact_download(self, u, q):
    a = _kdb.get('artifacts', id=q.get('id', ''))
    if self._bearer().startswith('kbr_'):
        if not _runner_of(self):
            return self._send(401, {'error': '执行机令牌无效', 'login': True})
    elif a and a.get('period') and not _authz.can(getattr(self, 'ident', None), '看', a['period'])[0]:
        return self._send(403, {'error': _authz.MSG_PROJ})
    if not a or not a.get('path') or not Path(a['path']).is_file():
        return self._send(404, {'error': '没有这个产物（可能已过保留期被清理，或只登记在编译机上）'})
    return send_file(self, a['path'], {'dl': '1'}, trusted=True)


def _spool(self, n):
    """把请求体落到临时文件（产物可能很大，不进内存）。"""
    fd, path = tempfile.mkstemp(prefix='kb-up-', dir=str(kb.SYS / 'tmp') if (kb.SYS / 'tmp').is_dir() else None)
    left = n
    with os.fdopen(fd, 'wb') as fp:
        while left > 0:
            chunk = self.rfile.read(min(left, 1 << 20))
            if not chunk:
                break
            fp.write(chunk)
            left -= len(chunk)
    return path


def _multipart(path, ctype):
    """解析 multipart/form-data（落盘后用 mmap 找分隔线）→ {字段: 字符串}, {字段: (文件名, 起, 止)}。"""
    b = next((x.split('=', 1)[1].strip('"') for x in ctype.split(';') if x.strip().startswith('boundary=')), '')
    if not b:
        raise kb.KBError('不是 multipart/form-data')
    sep = b'--' + b.encode()
    fields, files = {}, {}
    size = os.path.getsize(path)
    if not size:
        return fields, files
    with open(path, 'rb') as fh, mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as m:
        pos = m.find(sep)
        while pos >= 0:
            start = pos + len(sep)
            if m[start:start + 2] == b'--':
                break
            hdr_end = m.find(b'\r\n\r\n', start)
            if hdr_end < 0:
                break
            hdr = m[start:hdr_end].decode('utf-8', 'replace')
            nxt = m.find(b'\r\n' + sep, hdr_end + 4)
            if nxt < 0:
                break
            name = _hdr_param(hdr, 'name')
            fn = _hdr_param(hdr, 'filename')
            if fn is not None:
                files[name] = (fn, hdr_end + 4, nxt)
            else:
                fields[name] = m[hdr_end + 4:nxt].decode('utf-8', 'replace')
            pos = nxt + 2
    return fields, files


def _hdr_param(hdr, key):
    import re as _re
    mm = _re.search(rf'{key}="([^"]*)"', hdr)
    return mm.group(1) if mm else None


def _copy_range(src, a, b, dst):
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256()
    with open(src, 'rb') as fi, open(dst, 'wb') as fo:
        fi.seek(a)
        left = b - a
        while left > 0:
            chunk = fi.read(min(left, 1 << 20))
            if not chunk:
                break
            h.update(chunk)
            fo.write(chunk)
            left -= len(chunk)
    return h.hexdigest(), b - a


@post('/api/runner/artifact', raw=True)
def _post_runner_artifact(self, u, n, body):
    rid = _runner_of(self)
    if not rid:
        return self._send(401, {'error': '执行机令牌无效', 'login': True})
    tmp = _spool(self, n)

    def run():
        try:
            fields, files = _multipart(tmp, self.headers.get('Content-Type', ''))
            try:
                meta = _json.loads(fields.get('meta') or '{}')
            except ValueError:
                raise kb.KBError('meta 不是 JSON')
            t = _task(meta.get('task'))
            if not t:
                raise kb.KBError('没有这个任务')
            if t.get('runner') and t['runner'] != rid:
                raise _authz.Denied('这个任务不是派给本执行机的')
            aid = 'AR' + uuid.uuid4().hex[:10]
            path, sha, size = '', meta.get('sha256') or '', int(meta.get('大小') or 0)
            if 'file' in files:
                fn, a, b = files['file']
                dst = _res.ART_DIR / aid / (kb.safe_name(fn) or 'app.apk')
                got, size = _copy_range(tmp, a, b, dst)
                if sha and sha.lower() != got:
                    shutil.rmtree(dst.parent, ignore_errors=True)
                    raise kb.KBError(f'SHA256 不一致：声明 {sha[:12]}…，收到 {got[:12]}…')
                sha, path = got, str(dst)
            _kdb.insert('artifacts', id=aid, task=t['id'], period=t.get('period') or '', project=t.get('project') or '',
                        version=meta.get('版本') or '', branch=meta.get('分支') or '', commit_id=meta.get('提交') or '', sha256=sha,
                        size=size, runner=rid, path=path, ts=_kdb.now_ts())
            _kdb.event_add('产物', t.get('project') or '', {'期': t.get('period'), 'id': aid})
            return {'id': aid}
        finally:
            os.remove(tmp)
    return self._guard(run)


# ---------------------------------------------------------------- 证据（REQ-050）
EVI_KINDS = ('截图', 'logcat', '设备信息')


@post('/api/runner/evidence', raw=True)
def _post_runner_evidence(self, u, n, body):
    rid = _runner_of(self)
    if not rid:
        return self._send(401, {'error': '执行机令牌无效', 'login': True})
    if n > EVI_MAX + 64 * 1024:
        self.close_connection = True
        return self._send(413, {'error': f'证据单次上限 20MB（这次 {n // 1024 // 1024}MB）：logcat 先截最后一段再传'})
    tmp = _spool(self, n)

    def run():
        try:
            fields, files = _multipart(tmp, self.headers.get('Content-Type', ''))
            t = _task(fields.get('task'))
            if not t:
                raise kb.KBError('没有这个任务')
            if t.get('runner') and t['runner'] != rid:
                raise _authz.Denied('这个任务不是派给本执行机的')
            kind = fields.get('类型') or ''
            if kind not in EVI_KINDS:
                raise kb.KBError(f'类型只能是：{" / ".join(EVI_KINDS)}')
            if 'file' not in files:
                raise kb.KBError('缺少 file')
            fn, a, b = files['file']
            if b - a > EVI_MAX:
                raise kb.KBNeed('证据单次上限 20MB', {'上限MB': 20})
            dst = _res.EVI_DIR / kb.safe_name(t['id']) / f'{uuid.uuid4().hex[:8]}-{kb.safe_name(fn) or kind}'
            _, size = _copy_range(tmp, a, b, dst)
            mock = str(fields.get('模拟') or '').lower() in ('1', 'true', 'yes')
            eid = _kdb.insert('evidence', task=t['id'], kind=kind, step=fields.get('步骤') or '', mock=1 if mock else 0, path=str(dst),
                              size=size, ts=_kdb.now_ts(), pinned=0)
            return {'msg': f'已保存证据 {eid}' + ('（模拟数据）' if mock else ''), 'id': eid}
        finally:
            os.remove(tmp)
    return self._guard(run)


def _evi_view(e):
    return {'id': e['id'], '任务': e['task'], '类型': e['kind'], '步骤': e.get('step') or '', '模拟': bool(e.get('mock')),
            '大小': e.get('size') or 0, '时间': _kdb.ts_str(e['ts']), '保留': bool(e.get('pinned'))}


def _task_visible(self, t):
    """有期按期「看」，有项目按项目；既没有期也没有项目（或任务已删）的只给发起人和管理员（审查第 1 轮：原来直接放行）。"""
    if t.get('period') and (kb.PERIODS / t['period']).is_dir():
        if not _authz.can(self.ident, '看', t['period'])[0]:
            raise _authz.Denied(_authz.MSG_PROJ)
    elif t.get('project'):
        _see_project(self, t['project'])
    else:
        u = (self.ident or {}).get('user', '')
        if not (_authz.is_admin(u) or (u and t.get('initiator') == u)):
            raise _authz.Denied('这条证据的任务已不在（或不属于任何期 / 项目）：只有发起人和管理员能看')


@get('/api/evidence')
def _get_evidence(self, u, q):
    def run():
        t = _task(q.get('task', ''))
        if not t:
            raise kb.KBError('没有这个任务')
        _task_visible(self, t)
        es = [_evi_view(e) for e in _kdb.rows('evidence', {'task': t['id']}, order='ts')]
        return {'证据': es, '模拟数据': any(e['模拟'] for e in es)}
    return self._guard(run)


@get('/api/evidence/file')
def _get_evidence_file(self, u, q):
    try:
        e = _kdb.get('evidence', id=int(q.get('id') or 0))
    except ValueError:
        e = None
    if not e:
        return self._send(404, {'error': '没有这条证据'})
    t = _task(e['task']) or {}
    try:
        _task_visible(self, t)
    except _authz.Denied as x:
        return self._send(403, {'error': str(x)})
    return send_file(self, e['path'], q, trusted=True)
