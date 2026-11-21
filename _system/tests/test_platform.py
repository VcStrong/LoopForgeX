"""平台化阶段一：账号 / 登录 / 角色权限 / 项目 / 令牌 / MCP / 机密分层 / SQLite / 租约 / 迁移（REQ-001~REQ-010 REQ-026 REQ-027 REQ-030）。

    cd _system && .venv/bin/python -m unittest tests.test_platform -v

临时 KB_ROOT / HOME、机密走加密文件；起一个真网页服务（随机端口），用 HTTP 调契约里的接口。
"""
import http.client
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix='kb-plat-test-'))
os.environ['KB_ROOT'] = str(TMP / 'kb')
os.environ['HOME'] = str(TMP / 'home')
os.environ['KB_SECRET_BACKEND'] = 'file'
os.environ['KB_MASTER_KEY'] = 'test-master-key'
(TMP / 'kb').mkdir()
(TMP / 'home').mkdir()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import kb  # noqa: E402
from kblib.core import db  # noqa: E402
from kblib.infra import deploy  # noqa: E402
from webapp.server import H  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402

# 和别的测试同进程时 vault 可能已按真钥匙串初始化：强制加密文件后端，绝不弹钥匙串
from kblib.infra import vault as _vault  # noqa: E402
_vault.BACKEND = 'file'
os.environ.setdefault('KB_MASTER_KEY', 'test-master-key')

kb.init()
# 老数据：一期、旧访问密码、旧令牌（升级迁移用）
kb.create_period('老的一期', '升级前就有')
_s = kb.load_settings()
_s['部署'] = {'密码': deploy.hash_pw('oldpass123'), '令牌': [{'名称': '旧令牌', '哈希': deploy._tok_hash('kb_oldtoken'), '时间': kb.now()}]}
kb.save_settings(_s)

SRV = ThreadingHTTPServer(('127.0.0.1', 0), H)
PORT = SRV.server_address[1]
threading.Thread(target=SRV.serve_forever, daemon=True).start()
deploy.BIND['host'] = '127.0.0.1'


class C:
    """一个浏览器（带 Cookie）。"""

    def __init__(self, cookie=''):
        self.cookie = cookie

    def req(self, method, path, body=None, headers=None, page=True):
        c = http.client.HTTPConnection('127.0.0.1', PORT, timeout=30)
        h = {'Content-Type': 'application/json'}
        if page:
            h['Origin'] = f'http://127.0.0.1:{PORT}'
            h['Host'] = f'127.0.0.1:{PORT}'
        if self.cookie:
            h['Cookie'] = f'kb_s={self.cookie}'
        h.update(headers or {})
        from urllib.parse import quote
        path = quote(path, safe='/?=&%:')
        c.request(method, path, body=json.dumps(body).encode() if body is not None else None, headers=h)
        r = c.getresponse()
        data = r.read()
        sc = r.getheader('Set-Cookie') or ''
        if sc.startswith('kb_s=') and 'Max-Age=0' not in sc:
            self.cookie = sc.split(';')[0][5:]
        try:
            return r.status, json.loads(data or b'{}')
        except ValueError:
            return r.status, {'raw': data[:200]}

    def get(self, path, **kw):
        return self.req('GET', path, **kw)

    def post(self, path, body=None, **kw):
        return self.req('POST', path, body or {}, **kw)


def login(user, pw):
    c = C()
    st, b = c.post('/api/login', {'username': user, 'password': pw})
    return c, st, b


class Platform(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        print(kb.migrate(check_secrets=False))
        cls.admin, st, b = login('admin', 'oldpass123')
        assert st == 200, b

    # ---- REQ-027 迁移
    def test_01_migrate_old_password_and_token(self):
        self.assertEqual(db.user_get('admin')['sys_role'], '管理员')
        i = deploy.token_identity('kb_oldtoken')
        self.assertEqual(i['user'], 'admin')
        info = kb.period_info('老的一期')
        self.assertEqual(info['项目'], 'default')
        self.assertTrue(db.records_of('老的一期'))
        dry = kb.migrate(dry_run=True)
        self.assertIn('期 1 个', dry)
        before = db.table_counts()
        kb.migrate(check_secrets=False)              # 幂等
        self.assertEqual(before['periods'], db.table_counts()['periods'])
        self.assertEqual(before['records'], db.table_counts()['records'])

    def test_02_migrate_rollback_on_bad_check(self):
        orig = kb.migrate_check if hasattr(kb, 'migrate_check') else None
        from kblib.core import migrate as mg
        real = mg.migrate_check
        mg.migrate_check = lambda check_secrets=True: (['期打不开：假的'], {})
        try:
            with self.assertRaises(kb.KBError) as cm:
                mg.migrate(check_secrets=False)
            self.assertIn('自动回滚', str(cm.exception))
        finally:
            mg.migrate_check = real
        self.assertTrue(db.user_get('admin'))
        del orig

    # ---- REQ-001 账号与登录
    def test_10_create_user_dup_and_first_login(self):
        st, b = self.admin.post('/api/user', {'username': 'zhang', 'password': 'init1234', '系统角色': '普通', '新建': True})
        self.assertEqual(st, 200, b)
        st, b = self.admin.post('/api/user', {'username': 'zhang', 'password': 'init1234', '新建': True})
        self.assertEqual((st, b['error']), (400, '用户名已存在'))
        st, b = self.admin.post('/api/user', {'username': 'short', 'password': '123', '新建': True})
        self.assertEqual(st, 400)
        c, st, b = login('zhang', 'init1234')
        self.assertEqual(st, 200)
        self.assertTrue(b['mustChangePassword'])
        st, b = c.get('/api/overview')
        self.assertEqual((st, b['error']), (403, '先修改初始密码'))
        st, b = c.get('/api/me')
        self.assertEqual(b['状态'], '待改密码')
        st, b = c.post('/api/me/password', {'old': 'init1234', 'new': 'newpass123'})
        self.assertEqual(st, 200, b)
        st, b = c.get('/api/me')
        self.assertEqual((b['用户名'], b['状态']), ('zhang', '正常'))
        self.assertTrue(any(p['id'] == 'default' for p in b['项目']) is False)    # 还没加进任何项目

    def test_11_bad_login_and_lock(self):
        kb.user_create_strict('lockme', 'lockpass1')
        _, st, b = login('nobody', 'x')
        self.assertEqual((st, b['error']), (403, '用户名或密码不对'))
        for _ in range(5):
            _, st, b = login('lockme', 'wrong')
        _, st, b = login('lockme', 'lockpass1')
        self.assertEqual(st, 403)
        self.assertIn('锁定', b['error'])

    def test_12_password_reset_kicks_sessions(self):
        kb.user_create_strict('kick', 'kickpass1')
        c, _, _ = login('kick', 'kickpass1')
        c.post('/api/me/password', {'old': 'kickpass1', 'new': 'kickpass2'})
        self.assertEqual(c.get('/api/me')[0], 200)
        self.admin.post('/api/user', {'username': 'kick', 'password': 'reset1234'})
        st, b = c.get('/api/me')
        self.assertEqual(st, 401)
        self.assertTrue(b.get('login'))

    def test_13_users_admin_only(self):
        kb.user_create_strict('plain', 'plainpass1')
        c, _, _ = login('plain', 'plainpass1')
        c.post('/api/me/password', {'old': 'plainpass1', 'new': 'plainpass2'})
        self.assertEqual(c.get('/api/users')[0], 403)
        self.assertEqual(self.admin.get('/api/users')[0], 200)

    # ---- REQ-002 / REQ-003 角色和项目
    def _user(self, name, project=None, role=None):
        if not db.user_get(name):
            kb.user_create_strict(name, name + 'pass1')
            c, _, _ = login(name, name + 'pass1')
            c.post('/api/me/password', {'old': name + 'pass1', 'new': name + 'pass2'})
        else:
            c, _, _ = login(name, name + 'pass2')
        if project:
            self.assertEqual(self.admin.post('/api/project/member', {'project': project, 'username': name, '角色': role})[0], 200)
        return c

    def test_20_roles_and_owner_actions(self):
        st, b = self.admin.post('/api/project', {'名称': '甲项目', '负责人': 'admin'})
        self.assertEqual(st, 200, b)
        pid = b['id']
        owner = self._user('owner1', pid, '成员')
        mem = self._user('mem1', pid, '成员')
        ro = self._user('ro1', pid, '只读')
        owner.post('/api/me/project', {'project': pid})
        mem.post('/api/me/project', {'project': pid})
        ro.post('/api/me/project', {'project': pid})
        st, b = owner.post('/api/new', {'name': '甲的期', 'summary': 's'})
        self.assertEqual(st, 200, b)
        info = kb.period_info('甲的期')
        self.assertEqual((info['项目'], info['负责人']), (pid, 'owner1'))
        st, b = mem.post('/api/dev/start', {'period': '甲的期', 'confirm': '甲的期'})
        self.assertEqual((st, b['error']), (403, '只有本期负责人或项目负责人能操作'))
        st, b = mem.get('/api/period?name=' + '甲的期')
        self.assertEqual(st, 200)
        self.assertNotIn('开工', b['can'])
        self.assertIn('开工', owner.get('/api/period?name=甲的期')[1]['can'])
        st, b = ro.post('/api/log', {'period': '甲的期', 'note': 'x'})
        self.assertEqual((st, b['error']), (403, '只读账号不能修改'))
        # 改角色立即生效
        self.admin.post('/api/project/member', {'project': pid, 'username': 'ro1', '角色': '成员'})
        st, b = ro.post('/api/log', {'period': '甲的期', 'note': '我现在是成员'})
        self.assertEqual(st, 200, b)
        self.assertTrue(any('ro1（网页）' in (r['actor'] or '') for r in db.records_of('甲的期')))

    def test_21_open_materials_owner_delete(self):
        """用户 2026-11-20 22:03（T003）：期 / 事项资料全平台可看可改（记操作人），只有本人和管理员能删；系统文件谁都不给。"""
        st, b = self.admin.post('/api/project', {'名称': '乙项目', '负责人': 'admin'})
        pid = b['id']
        self.admin.post('/api/me/project', {'project': pid})
        self.admin.post('/api/new', {'name': '乙的期'})
        self.admin.post('/api/me/project', {'project': 'default'})
        out = self._user('outsider', 'default', '成员')
        st, b = out.get('/api/period?name=乙的期')
        self.assertEqual(st, 200, b)
        self.assertNotIn('删除', b['can'])
        self.assertIn('加资料', b['can'])
        st, b = out.get('/fs' + str(kb.PERIODS / '乙的期' / '期信息.json'))
        self.assertEqual(st, 200)
        st, b = out.post('/api/log', {'period': '乙的期', 'note': '别的项目的人也能补充'})
        self.assertEqual(st, 200, b)
        self.assertTrue(any('outsider（网页）' in (r['actor'] or '') for r in db.records_of('乙的期')))
        # 删除：不是本人 → 403；本人 / 管理员可以
        st, b = out.post('/api/period/delete', {'period': '乙的期', 'confirm': '乙的期'})
        self.assertEqual((st, b['error']), (403, '只有本人或管理员能删除'))
        out.post('/api/new', {'name': '外人自己的事项'})
        other = self._user('other2', 'default', '成员')
        st, b = other.post('/api/period/delete', {'period': '外人自己的事项', 'confirm': '外人自己的事项'})
        self.assertEqual(st, 403)
        self.assertIn('删除', out.get('/api/period?name=外人自己的事项')[1]['can'])
        st, b = out.post('/api/period/delete', {'period': '外人自己的事项', 'confirm': '外人自己的事项'})
        self.assertEqual(st, 200, b)
        st, b = self.admin.post('/api/period/delete', {'period': '乙的期', 'confirm': '乙的期'})
        self.assertEqual(st, 200, b)
        # 项目仍是默认视图 / 切换范围
        self.assertEqual(out.post('/api/me/project', {'project': pid})[0], 403)
        # 系统文件：/fs、/file、/api/text、kb_read 都拿不到
        if True:                      # 不新建 / 不改这些文件（db.url 建了会切驱动、store.enc 会坏）：按路径拦，存在与否都 403
            self.assertTrue(db.DB_FILE.exists() or db.driver() != 'sqlite')
            for path in (db.DB_FILE, kb.SYS / 'db.url', kb.ROOT / '.网页会话.json', kb.ROOT / '.机密' / 'store.enc', kb.SYS / 'index.db'):
                for who in (out, self.admin):
                    st, b = who.get('/fs' + str(path))
                    self.assertEqual(st, 403, (path, b))
                    st, b = who.get('/file?path=' + str(path))
                    self.assertEqual(st, 403, (path, b))
                    st, b = who.get('/api/text?path=' + str(path))
                    self.assertNotEqual(st, 200, path)
                with self.assertRaises(kb.KBError):
                    kb.read_file(str(path))

    def test_24_base_private_and_template(self):
        """用户 2026-11-20 22:04 / 22:09：基础信息公共 / 私有（本人和管理员能看），管理员模板复制给新员工只填个人字段。"""
        a = self._user('basea', 'default', '成员')
        b = self._user('baseb', 'default', '成员')
        st, r = a.post('/api/base/save', {'category': '其他', 'name': '公共 Wi-Fi', 'fields': [{'名': 'SSID', '值': 'office'}]})
        self.assertEqual(st, 200, r)
        st, r = a.post('/api/base/save', {'category': '账号密码', 'name': '我的 Jira', 'visibility': '私有',
                                          'fields': [{'名': '账号', '值': 'basea'}, {'名': '密码', '值': 'pw-a', '机密': True}]})
        self.assertEqual(st, 200, r)
        pid = r['id']
        names = lambda c: [e['名称'] for e in c.get('/api/base')[1]['条目']]     # noqa: E731
        self.assertIn('公共 Wi-Fi', names(b))
        self.assertNotIn('我的 Jira', names(b))
        self.assertIn('我的 Jira', names(a))
        self.assertIn('我的 Jira', names(self.admin))
        # 别人改 / 删 / 读机密都不行；本人能读；管理员能看条目但读不到明文（REQ-007 个人层）
        st, r = b.post('/api/base/save', {'id': pid, 'delete': True})
        self.assertEqual(st, 400 if 'error' in r and '没有这条' in r['error'] else 403, r)
        self.assertEqual(b.post('/api/base/secret', {'id': pid, 'field': '密码'})[0], 400)
        self.assertEqual(a.post('/api/base/secret', {'id': pid, 'field': '密码'})[1].get('value'), 'pw-a')
        self.assertNotEqual(self.admin.post('/api/base/secret', {'id': pid, 'field': '密码'})[0], 200)
        # 不进基础信息.md / 检索；基础信息.json 不能经 /fs 下载
        self.assertNotIn('我的 Jira', kb.BASE_MD.read_text('utf-8'))
        self.assertEqual(b.get('/fs' + str(kb.BASE_FILE))[0], 403)
        # 模板：只有管理员能建；人人可见；复制只填个人字段
        st, r = a.post('/api/base/save', {'category': '账号密码', 'name': '公司 GitLab', 'template': True, 'fields': []})
        self.assertEqual(st, 403, r)
        st, r = self.admin.post('/api/base/save', {'category': '账号密码', 'name': '公司 GitLab', 'template': True, 'fields': [
            {'名': '地址', '值': 'https://git.example.com'}, {'名': '账号', '值': '', '个人': True},
            {'名': 'token', '值': '', '机密': True, '个人': True}]})
        self.assertEqual(st, 200, r)
        tid = r['id']
        self.assertIn('公司 GitLab', names(b))
        self.assertEqual(b.post('/api/base/copy', {'id': 'B999'})[0], 400)          # 没有这个基础信息模板
        self.assertEqual(C().get('/api/base')[0], 401)
        self.assertEqual(C().post('/api/base/save', {'category': '其他', 'name': 'x'})[0], 401)
        self.assertEqual(C().post('/api/base/copy', {'id': tid})[0], 401)
        st, r = b.post('/api/base/copy', {'id': tid, 'values': {'账号': 'baseb'}})
        self.assertEqual((st, r.get('error')), (400, '要填「token」'))
        st, r = b.post('/api/base/copy', {'id': tid, 'holder': 'basea', 'values': {'账号': 'x', 'token': 't'}})
        self.assertEqual(st, 403, r)                           # 普通成员不能替别人复制
        st, r = b.post('/api/base/copy', {'id': tid, 'values': {'账号': 'baseb', 'token': 'tok-b'}})
        self.assertEqual(st, 200, r)
        e = next(x for x in b.get('/api/base')[1]['条目'] if x['编号'] == r['id'])
        self.assertEqual((e['可见'], e['所有人'], e['来自模板']), ('私有', 'baseb', tid))
        self.assertEqual({f['名']: f['值'] for f in e['字段'] if not f['机密']}, {'地址': 'https://git.example.com', '账号': 'baseb'})
        self.assertEqual(b.post('/api/base/secret', {'id': r['id'], 'field': 'token'})[1].get('value'), 'tok-b')
        self.assertNotIn('公司 GitLab（baseb）', names(a))
        # 管理员替新同事复制
        st, r = self.admin.post('/api/base/copy', {'id': tid, 'holder': 'basea', 'values': {'账号': 'basea', 'token': 'tok-a'}})
        self.assertEqual(st, 200, r)
        self.assertIn('公司 GitLab（basea）', names(a))
        self.assertNotIn('公司 GitLab（basea）', names(b))
        # MCP / 开发包按会话发起人：b 发起的会话看不到 a 的私有
        from kblib.core.common import set_user_ctx, _USER_CTX
        tok = set_user_ctx({'via': 'AI', 'user': 'baseb', 'project': 'default'})
        try:
            self.assertNotIn('我的 Jira', kb.base_list())
            self.assertIn('公司 GitLab（baseb）', kb.base_list())
            self.assertNotIn('我的 Jira', kb.base_context())
        finally:
            _USER_CTX.reset(tok)
        # MCP kb_base_save private=true（契约 v2）：只有发起人和管理员能看到
        from kblib.api import mcp as _mcp
        tok = set_user_ctx({'via': 'AI', 'user': 'baseb', 'project': 'default'})
        try:
            _mcp.call_tool('kb_base_save', {'category': '账号密码', 'name': 'AI 存的私有', 'private': True,
                                            'fields': [{'名': '账号', '值': 'baseb'}]})
        finally:
            _USER_CTX.reset(tok)
        self.assertIn('AI 存的私有', names(b))
        self.assertIn('AI 存的私有', names(self.admin))
        self.assertNotIn('AI 存的私有', names(a))

    def test_25_base_private_no_escalation(self):
        """审查第 2 轮：改成私有 / 从模板复制都不能拿到本来读不到的机密；私有附件只给本人；保存直接返回编号；老期补负责人。"""
        from kblib.core.common import set_user_ctx, _USER_CTX
        st, b = self.admin.post('/api/project', {'名称': 'X项目', '负责人': 'admin'})
        px = b['id']
        out = self._user('outsider9', 'default', '成员')
        tok = set_user_ctx({'via': '网页', 'user': 'admin'})
        try:
            msg, sid = kb.base_save_id(category='账号密码', name='X 生产库', project=px,
                                       fields=[{'名': '地址', '值': 'db.x'}, {'名': '密码', '值': 'prod-secret', '机密': True}])
            msg, tid = kb.base_save_id(category='账号密码', name='Y 平台模板', project=px, template=True, fields=[
                {'名': '地址', '值': 'https://y.example.com'}, {'名': '共用token', '值': 'shared-Y', '机密': True},
                {'名': '账号', '值': '', '个人': True}])
        finally:
            _USER_CTX.reset(tok)
        self.assertTrue(sid.startswith('B') and tid.startswith('B') and sid != tid)
        self.assertEqual(out.post('/api/base/secret', {'id': sid, 'field': '密码'})[0], 400)
        # ① 不是创建人：不能改可见范围；字段也没被动
        st, r = out.post('/api/base/save', {'id': sid, 'visibility': '私有', 'fields': [{'名': '地址', '值': 'db.x'}, {'名': '密码', '值': '', '机密': True}]})
        self.assertEqual((st, r.get('error')), (403, '只有创建人或管理员能改这条的可见范围'))
        self.assertEqual(out.post('/api/base/secret', {'id': sid, 'field': '密码'})[0], 400)
        self.assertIn('X 生产库', [e['名称'] for e in self.admin.get('/api/base')[1]['条目']])
        # 创建人自己（但已读不到这个项目的机密）也不能借改私有拿明文
        e = next(x for x in kb.base_load()['条目'] if x['编号'] == sid)
        tok = set_user_ctx({'via': '网页', 'user': 'outsider9'})
        try:
            from kblib.core import baseinfo as _bi
            data = kb.base_load()
            next(x for x in data['条目'] if x['编号'] == sid)['创建人'] = 'outsider9'
            _bi._base_save_all(data)
        finally:
            _USER_CTX.reset(tok)
        st, r = out.post('/api/base/save', {'id': sid, 'visibility': '私有'})
        self.assertEqual(st, 403, r)
        self.assertIn('读不到的机密', r['error'])
        self.assertEqual(out.post('/api/base/secret', {'id': sid, 'field': '密码'})[0], 400)
        # 自己建的公共条目改成私有：可以，并记审计
        st, r = out.post('/api/base/save', {'category': '其他', 'name': '我的条目', 'fields': [{'名': 'k', '值': 'v'}, {'名': 'p', '值': 'mine', '机密': True}]})
        mine = r['id']
        self.assertEqual(out.post('/api/base/save', {'id': mine, 'visibility': '私有'})[0], 200)
        self.assertEqual(out.post('/api/base/secret', {'id': mine, 'field': 'p'})[1].get('value'), 'mine')
        # ② 从模板复制：模板里读不到的共用机密 → 拒绝；管理员替他复制 → 可以，读取记机密审计
        st, r = out.post('/api/base/copy', {'id': tid, 'values': {'账号': 'o9'}})
        self.assertEqual(st, 403, r)
        self.assertIn('读不到的机密', r['error'])
        n0 = db.audit_query(kind='机密', obj=f'{tid}/共用token')[0]
        st, r = self.admin.post('/api/base/copy', {'id': tid, 'holder': 'outsider9', 'values': {'账号': 'o9'}})
        self.assertEqual(st, 200, r)
        self.assertEqual(db.audit_query(kind='机密', obj=f'{tid}/共用token')[0], n0 + 1)
        # ③ 私有附件：别人列表里看不到、下载 403；本人可以
        att = kb.ROOT / 'tmp-vpn.ovpn'
        att.write_text('client\nremote vpn.example.com')
        tok = set_user_ctx({'via': '网页', 'user': 'outsider9'})
        try:
            msg, pid_ = kb.base_save_id(category='VPN', name='我的 VPN', visibility='私有', file=str(att),
                                        fields=[{'名': '账号', '值': 'o9'}])
        finally:
            _USER_CTX.reset(tok)
        f = kb.BASE / '附件' / 'tmp-vpn.ovpn'
        other = self._user('other9', 'default', '成员')
        self.assertNotIn(str(f), [x['path'] for x in other.get('/api/base')[1]['files']])
        self.assertEqual(other.get('/fs' + str(f))[0], 403)
        self.assertIn(str(f), [x['path'] for x in out.get('/api/base')[1]['files']])
        self.assertEqual(out.get('/fs' + str(f))[0], 200)
        # ④ 老期没有负责人：启动时补成本机用户，本人能删
        kb.create_period('没负责人的期')
        db.period_set_attr('没负责人的期', owner='', by='测试')
        from kblib.core import migrate as _mg
        self.assertGreaterEqual(_mg.backfill_owners(), 1)
        self.assertEqual(db.period_row('没负责人的期')['owner'], kb.local_user())

    def test_22_optimistic_lock(self):
        self.admin.post('/api/new', {'name': '锁的期'})
        v = kb.period_info('锁的期')['版本号']
        st, b = self.admin.post('/api/period/owner', {'name': '锁的期', '负责人': 'admin', 'version': v})
        self.assertEqual(st, 200, b)
        st, b = self.admin.post('/api/period/owner', {'name': '锁的期', '负责人': 'admin', 'version': v})
        self.assertEqual(st, 409)
        self.assertIn('刷新后再改', b['error'])

    def test_23_field_merge(self):
        kb.create_period('合并的期')
        d = kb.period_dir('合并的期')
        a, b = kb.load_meta(d), kb.load_meta(d)
        a['说明'] = '甲改的'
        kb.save_meta(d, a)
        b['免打扰'] = True
        kb.save_meta(d, b)
        m = kb.load_meta(d)
        self.assertEqual((m['说明'], m['免打扰']), ('甲改的', True))
        self.assertEqual(json.loads((d / '期信息.json').read_text('utf-8'))['说明'], '甲改的')

    # ---- REQ-005 令牌
    def test_26_base_rejected_save_keeps_secrets(self):
        """审查第 1 轮：被拒（403 / 400）的保存不能改动任何字段 / 机密，也不留孤儿钥匙串；复制被拒不读机密、不写审计；stdio 私有归本机用户。"""
        from kblib.core import baseinfo as bi
        from kblib.core.common import set_user_ctx, _USER_CTX
        c = self._user('rejc', 'default', '成员')
        st, r = c.post('/api/base/save', {'category': '其他', 'name': '我的令牌', 'fields': [{'名': 'tok', '值': 'v1', '机密': True}]})
        self.assertEqual(st, 200, r)
        sid = r['id']
        bad = [({'template': True}, 403), ({'visibility': '私有', 'holder': 'admin'}, 403),
               ({'visibility': '半公开'}, 400), ({'visibility': '私有', 'holder': 'nobody-x', '_as': 'admin'}, 400)]
        for extra, code in bad:
            who = self.admin if extra.pop('_as', '') else c
            st, r = who.post('/api/base/save', {'id': sid, 'fields': [{'名': 'tok', '值': 'CHANGED', '机密': True}], **extra})
            self.assertEqual(st, code, (extra, r))
            self.assertEqual(bi._kc_get(f'{sid}/tok'), 'v1', extra)
            st, r = who.post('/api/base/save', {'id': sid, 'fields': [{'名': 'x', '值': '1'}], **extra})   # 删掉机密字段也不能生效
            self.assertEqual(st, code, (extra, r))
            self.assertEqual(bi._kc_get(f'{sid}/tok'), 'v1', extra)
        nxt = f'B{bi.base_load()["序号"] + 1:03d}'
        st, r = c.post('/api/base/save', {'category': '其他', 'name': '模板?', 'template': True, 'fields': [{'名': 'p', '值': 'orphan', '机密': True}]})
        self.assertEqual((st, r.get('error')), (403, '只有管理员能建基础信息模板'))
        self.assertIsNone(bi._kc_get(f'{nxt}/p'))                    # 没有孤儿机密
        # 非管理员改模板：文案和验收标准一致
        st, r = self.admin.post('/api/base/save', {'category': '账号密码', 'name': '共用平台', 'template': True, 'fields': [
            {'名': '共用令牌', '值': 'shared-1', '机密': True}, {'名': '账号', '值': '', '个人': True}]})
        tid = r['id']
        st, r = c.post('/api/base/save', {'id': tid, 'note': 'x'})
        self.assertEqual((st, r.get('error')), (403, '只有管理员能建基础信息模板'))
        # 替别人复制 / 给不存在的人复制：先拒，不读共用机密、不写审计
        n0 = db.q1('SELECT count(*) FROM audit WHERE obj = ?', (f'{tid}/共用令牌',))[0]
        self.assertEqual(c.post('/api/base/copy', {'id': tid, 'holder': 'admin', 'values': {'账号': 'a'}})[0], 403)
        self.assertEqual(self.admin.post('/api/base/copy', {'id': tid, 'holder': 'nobody-x', 'values': {'账号': 'a'}})[0], 400)
        self.assertEqual(db.q1('SELECT count(*) FROM audit WHERE obj = ?', (f'{tid}/共用令牌',))[0], n0)
        st, r = self.admin.post('/api/base/copy', {'id': tid, 'values': {'账号': 'adm'}})
        self.assertEqual(st, 200, r)
        self.assertEqual(db.q1('SELECT count(*) FROM audit WHERE obj = ?', (f'{tid}/共用令牌',))[0], n0 + 1)
        # MCP 经 stdio（没有上下文）存私有：归本机用户，机密是个人层、owner 是本机用户
        from kblib.api import mcp as _mcp
        tok = set_user_ctx({})
        try:
            _mcp.call_tool('kb_base_save', {'category': '账号密码', 'name': 'stdio 私有', 'private': True,
                                            'fields': [{'名': '密码', '值': 'pw-stdio', '机密': True}]})
        finally:
            _USER_CTX.reset(tok)
        e = next(x for x in bi.base_load()['条目'] if x['名称'] == 'stdio 私有')
        me = kb.local_user()
        self.assertEqual(e['所有人'], me)
        self.assertEqual((db.secret_meta(e['编号'])['layer'], db.secret_meta(e['编号'])['owner']), ('个人', me))
        self.assertNotIn('stdio 私有', [x['名称'] for x in c.get('/api/base')[1]['条目']])
        # 审查第 2 轮：stdio 改别人已有的私有条目（只补备注）不转所有人、不改机密 owner
        st, r = c.post('/api/base/save', {'category': '账号密码', 'name': 'rejc 的私有', 'visibility': '私有',
                                          'fields': [{'名': '密码', '值': 'pw-c', '机密': True}]})
        cid = r['id']
        tok = set_user_ctx({})
        try:
            _mcp.call_tool('kb_base_save', {'id': cid, 'note': '补一句', 'private': True})
        finally:
            _USER_CTX.reset(tok)
        e = next(x for x in bi.base_load()['条目'] if x['编号'] == cid)
        self.assertEqual((e['所有人'], e['备注'], db.secret_meta(cid)['owner']), ('rejc', '补一句', 'rejc'))
        self.assertIn('rejc 的私有', [x['名称'] for x in c.get('/api/base')[1]['条目']])
        # 网页发起的本机后台会话（KB_USER=发起人）经 stdio 存私有：归发起人，发起人自己看得到
        os.environ['KB_USER'] = 'rejc'
        tok = set_user_ctx({})
        try:
            _mcp.call_tool('kb_base_save', {'category': '其他', 'name': '会话存的私有', 'private': True, 'fields': [{'名': 'k', '值': 'v'}]})
            os.environ['KB_USER'] = 'nobody-x'            # 账号不存在 → 退回本机用户
            _mcp.call_tool('kb_base_save', {'category': '其他', 'name': '无效发起人', 'private': True, 'fields': [{'名': 'k', '值': 'v'}]})
        finally:
            _USER_CTX.reset(tok)
            os.environ.pop('KB_USER', None)
        own = {x['名称']: x.get('所有人') for x in bi.base_load()['条目']}
        self.assertEqual((own['会话存的私有'], own['无效发起人']), ('rejc', me))
        self.assertIn('会话存的私有', [x['名称'] for x in c.get('/api/base')[1]['条目']])
        # _dev_env 带上发起人：有登录上下文用登录人，否则用记下的，再否则期负责人
        from kblib.ai import devsession as ds
        kb.create_period('发起人期', 's') if not (kb.PERIODS / '发起人期').is_dir() else None
        tok = set_user_ctx({'via': '网页', 'user': 'rejc'})
        try:
            self.assertEqual(ds._dev_env('发起人期', '', '/tmp')['KB_USER'], 'rejc')
        finally:
            _USER_CTX.reset(tok)
        self.assertEqual(ds._initiator('发起人期', 'someone'), 'someone')
        self.assertEqual(ds._initiator('发起人期'), kb.period_info('发起人期')['负责人'])

    def test_30_tokens_and_mcp(self):
        st, b = self.admin.post('/api/me/token', {'name': '笔记本'})
        self.assertEqual(st, 200)
        tok = b['token']
        self.assertTrue(tok.startswith('kb_'))
        items = self.admin.get('/api/me/tokens')[1]['items']
        self.assertTrue(any(x['名称'] == '笔记本' for x in items))
        self.assertNotIn(tok, json.dumps(items))
        # 令牌不能调 page_only
        st, b = C().post('/api/dev/start', {'period': '老的一期'}, headers={'Authorization': f'Bearer {tok}'}, page=False)
        self.assertIn(st, (401, 403))
        # MCP：别的项目的期 → 没有这个项目的权限
        out = self._user('mcpuser', 'default', '成员')
        st, b = out.post('/api/me/token', {'name': 't'})
        t2 = b['token']
        st, b = self.admin.post('/api/project', {'名称': '丙项目', '负责人': 'admin'})
        pid = b['id']
        self.admin.post('/api/me/project', {'project': pid})
        self.admin.post('/api/new', {'name': '丙的期'})
        self.admin.post('/api/me/project', {'project': 'default'})
        call = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': 'kb_iteration', 'arguments': {'name': '丙的期'}}}
        st, b = C().post('/mcp', call, headers={'Authorization': f'Bearer {t2}'}, page=False)
        self.assertEqual(st, 200)
        self.assertIn('# 丙的期', json.dumps(b, ensure_ascii=False))          # 期资料全平台可看（T003）
        self.assertNotIn('没有这个项目的权限', json.dumps(b, ensure_ascii=False))
        # 个人令牌读别的项目的机密：仍然不行（REQ-007）
        from kblib.core import baseinfo
        orig = baseinfo.secret_layer
        baseinfo.secret_layer = lambda i: {'layer': '项目', 'owner': '', 'project': pid}
        try:
            ok, why, _ = baseinfo.secret_allowed('B001', {'via': 'AI', 'user': 'mcpuser'})
            self.assertEqual((ok, why), (False, '不是本项目的机密'))
            self.assertTrue(baseinfo.secret_allowed('B001', {'via': 'AI', 'user': 'admin'})[0])
        finally:
            baseinfo.secret_layer = orig
        # 停用后令牌立即失效
        kb.user_disable('mcpuser', {'新负责人': 'admin'})
        st, b = C().post('/mcp', call, headers={'Authorization': f'Bearer {t2}'}, page=False)
        self.assertEqual(st, 401)
        _, st, b = login('mcpuser', 'mcpuserpass2')
        self.assertEqual(b['error'], '账号已停用，联系管理员')

    def test_31_session_token_revoked(self):
        t = deploy.session_token('admin', 'default', '老的一期', 'sess-1', engine='claude')
        call = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': 'kb_log', 'arguments': {'iteration': '老的一期', 'note': 'AI 写的'}}}
        st, b = C().post('/mcp', call, headers={'Authorization': f'Bearer {t}'}, page=False)
        self.assertEqual(st, 200, b)
        self.assertTrue(any((r['actor'] or '').startswith('AI·claude（发起人 admin）') for r in db.records_of('老的一期')))
        other = dict(call, params={'name': 'kb_log', 'arguments': {'iteration': '合并的期', 'note': '写别的期'}})
        kb.create_period('合并的期') if not (kb.PERIODS / '合并的期').exists() else None
        st, b = C().post('/mcp', other, headers={'Authorization': f'Bearer {t}'}, page=False)
        self.assertIn('会话只能写本期', json.dumps(b, ensure_ascii=False))
        deploy.session_token_revoke('sess-1')
        st, b = C().post('/mcp', call, headers={'Authorization': f'Bearer {t}'}, page=False)
        self.assertEqual(st, 401)

    def test_32_disable_needs_handover(self):
        kb.user_create_strict('leaver', 'leaverpas1')
        kb.create_period('离职的期')
        db.period_set_attr('离职的期', owner='leaver')
        st, b = self.admin.post('/api/user/disable', {'username': 'leaver'})
        self.assertEqual(st, 409)
        self.assertIn('离职的期', b['need']['期'])
        st, b = self.admin.post('/api/user/disable', {'username': 'leaver', 'handover': {'新负责人': 'admin', '在跑任务': '取消'}})
        self.assertEqual(st, 200, b)
        self.assertEqual(kb.period_info('离职的期')['负责人'], 'admin')

    # ---- REQ-007 机密
    def test_40_secret_layers_and_audit(self):
        from kblib.core.common import with_user
        st, b = self.admin.post('/api/base/save', {'category': '账号密码', 'name': '团队的', 'fields': [{'名': '密码', '值': 'p1', '机密': True}]})
        self.assertEqual(st, 200, b)
        es = kb.base_load()['条目']
        bid = es[-1]['编号']
        kb.base_save(id=bid, layer='个人', owner='admin')
        ctx = {'via': 'AI', 'user': 'zhang', 'project': 'default', 'session': 's9'}
        with self.assertRaises(kb.KBError):
            with_user(ctx, kb.base_secret, bid, '密码')
        self.assertEqual(with_user(dict(ctx, user='admin'), kb.base_secret, bid, '密码'), 'p1')
        kb.base_save(id=bid, layer='项目', project='default')
        self.assertEqual(with_user(ctx, kb.base_secret, bid, '密码'), 'p1')
        with self.assertRaises(kb.KBError):
            with_user(dict(ctx, project='other'), kb.base_secret, bid, '密码')
        st, b = self.admin.get('/api/audit?secret=' + f'{bid}/密码')
        self.assertEqual(st, 200)
        self.assertGreaterEqual(b['total'], 4)
        self.assertEqual({x['结果'] for x in b['items']}, {'成功', '拒绝'})

    # ---- REQ-009 租约
    def test_50_lease_only_one(self):
        got = [db.lease_try('同步', 60, 'A', 120), db.lease_try('同步', 60, 'B', 120)]
        self.assertEqual(got, [True, False])
        db.lease_done('同步', 'A', 'ok')
        self.assertFalse(db.lease_try('同步', 60, 'B', 120))       # 周期没到
        db.x('UPDATE schedules SET last_run=0, lease_until=? WHERE name=?', (time.time() - 1, '同步'))
        self.assertTrue(db.lease_try('同步', 60, 'B', 120))        # 租约过期，另一个接手

    # ---- REQ-026 单机模式
    def test_60_single_mode_flags(self):
        self.assertTrue(deploy.auth_required())                    # 有带密码的账号 = 团队模式
        with self.assertRaises(kb.KBError):
            orig = db.user_count
            db.user_count = lambda with_pw=False: 0
            try:
                deploy.check_bind('0.0.0.0')
            finally:
                db.user_count = orig
                deploy.BIND['host'] = '127.0.0.1'

    def test_61_db_busy_is_500(self):
        def boom():
            raise db.DBBusy('x')
        from webapp import router
        router.ROUTES['GET']['/api/__busy'] = router.Route(lambda self, u, q: self._guard(boom))
        st, b = self.admin.get('/api/__busy')
        self.assertEqual((st, b['error']), (500, '数据库忙，稍后重试'))


if __name__ == '__main__':
    unittest.main()
