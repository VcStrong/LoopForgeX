"""资源池 / 引擎账号 / 用量预算 / 测试账号 / 设备提供方 / 产物证据（REQ-018 REQ-038~REQ-050）。

    cd _system && python3 -m unittest tests.test_resources_api -v

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
import uuid
from pathlib import Path

if 'kb' not in sys.modules:                     # 和别的测试模块一起跑时沿用先导入的那个临时库
    TMP = Path(tempfile.mkdtemp(prefix='kb-res-test-'))
    os.environ['KB_ROOT'] = str(TMP / 'kb')
    os.environ['HOME'] = str(TMP / 'home')
    os.environ['KB_SECRET_BACKEND'] = 'file'
    os.environ['KB_MASTER_KEY'] = 'test-master-key'
    (TMP / 'kb').mkdir()
    (TMP / 'home').mkdir()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import kb  # noqa: E402
from kblib.ai import accounts  # noqa: E402
from kblib.core import db, resources  # noqa: E402
from kblib.devices import providers  # noqa: E402
from kblib.infra import deploy  # noqa: E402
from webapp.server import H  # noqa: E402
from webapp.routes import resources as rroutes  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402

# 和别的测试同进程时 vault 可能已按真钥匙串初始化：强制加密文件后端，绝不弹钥匙串
from kblib.infra import vault as _vault  # noqa: E402
_vault.BACKEND = 'file'
os.environ.setdefault('KB_MASTER_KEY', 'test-master-key')

kb.init()
(kb.SYS / '测试库').mkdir(parents=True, exist_ok=True)       # 假时钟只在测试库启用
SRV = ThreadingHTTPServer(('127.0.0.1', 0), H)
PORT = SRV.server_address[1]
threading.Thread(target=SRV.serve_forever, daemon=True).start()


class C:
    def __init__(self, cookie=''):
        self.cookie = cookie

    def req(self, method, path, body=None, headers=None, raw=None):
        c = http.client.HTTPConnection('127.0.0.1', PORT, timeout=30)
        h = {'Content-Type': 'application/json', 'Origin': f'http://127.0.0.1:{PORT}', 'Host': f'127.0.0.1:{PORT}'}
        if self.cookie:
            h['Cookie'] = f'kb_s={self.cookie}'
        h.update(headers or {})
        from urllib.parse import quote
        c.request(method, quote(path, safe='/?=&%:'), body=raw if raw is not None else
                  (json.dumps(body).encode() if body is not None else None), headers=h)
        r = c.getresponse()
        data = r.read()
        sc = r.getheader('Set-Cookie') or ''
        if sc.startswith('kb_s=') and 'Max-Age=0' not in sc:
            self.cookie = sc.split(';')[0][5:]
        try:
            return r.status, json.loads(data or b'{}')
        except ValueError:
            return r.status, {'raw': data}

    def get(self, path, **kw):
        return self.req('GET', path, **kw)

    def post(self, path, body=None, **kw):
        return self.req('POST', path, body or {}, **kw)


def user(name, sys_role='普通'):
    if not db.user_get(name):
        kb.user_create_strict(name, name + 'pass1234', sys_role)
        db.user_put(name, status='正常')
    c = C()
    st, b = c.post('/api/login', {'username': name, 'password': name + 'pass1234'})
    assert st == 200, b
    return c


def multipart(fields, file=None):
    b = 'kbtest' + uuid.uuid4().hex
    out = b''
    for k, v in fields.items():
        out += f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
    if file:
        fn, data = file
        out += f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="{fn}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode()
        out += data + b'\r\n'
    out += f'--{b}--\r\n'.encode()
    return out, f'multipart/form-data; boundary={b}'


def phones(runner, items):
    resources.sync_runner_phones(runner, items)


class Res(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.boss = user('rsboss', '管理员')
        user('rsown')
        ex = [p for p in db.project_list() if p['name'] == '资源项目']
        if ex:
            cls.P = ex[0]['id']
        else:
            st, b = cls.boss.post('/api/project', {'名称': '资源项目', '负责人': 'rsown'})
            assert st == 200, b
            cls.P = b['id']
        cls.own = user('rsown')
        cls.mem = user('rsmem')
        cls.ro = user('rsro')
        for n, r in (('rsown', '负责人'), ('rsmem', '成员'), ('rsro', '只读')):
            assert cls.boss.post('/api/project/member', {'project': cls.P, 'username': n, '角色': r})[0] == 200
        cls.other = user('rsout')

    def setUp(self):
        os.environ.pop('KB_FAKE_CLOCK', None)

    # ---------------------------------------------------------------- 资源池（REQ-044 REQ-045 REQ-048）
    def test_01_concurrent_claim_one_phone(self):
        phones('rA', [{'序列号': 'ONE1', '型号': 'Pixel'}])
        out = [None] * 10
        go = threading.Event()

        def grab(i):
            go.wait()
            time.sleep(i * 0.02)               # 到达顺序 0..9
            out[i] = resources.claim('手机', {'执行机': 'rA'}, 1, f's{i}', '', f'u{i}', 'pX')
        ts = [threading.Thread(target=grab, args=(i,)) for i in range(10)]
        for t in ts:
            t.start()
        go.set()
        for t in ts:
            t.join()
        ok = [i for i, r in enumerate(out) if r['ok']]
        self.assertEqual(ok, [0])
        pos = {out[i]['ticket']: out[i]['位置'] for i in range(1, 10)}
        self.assertEqual(sorted(pos.values()), list(range(1, 10)))          # 不重复发放，位置各不相同
        by_id = [w['ticket'] for w in db.rows('waits', {'rtype': '手机', 'status': '排队'}, order='id') if w['ticket'] in pos]
        self.assertEqual([pos[t] for t in by_id], list(range(1, 10)))       # 位置 = 到达（入队）顺序
        self.assertEqual(db.count('holds', {'resource': 'phone:rA:ONE1'}), 1)
        # 控制面重启：占用和队列从库里恢复
        db.reset_conn()
        ov = [r for r in resources.overview('手机') if r['id'] == 'phone:rA:ONE1'][0]
        self.assertEqual((len(ov['占用']), len(ov['队列'])), (1, 9))
        resources.release('phone:rA:ONE1', 's0', by='s0')
        head = db.get('waits', ticket=by_id[0])['session']
        self.assertEqual(resources.holds_of('phone:rA:ONE1')[0]['session'], head)   # 发给队首
        r = resources.claim('手机', {'执行机': 'rA'}, 1, head, '', 'u', 'pX')
        self.assertTrue(r['ok'])
        for i in range(1, 10):
            resources.release_session(f's{i}')
        self.assertEqual(db.count('holds', {'resource': 'phone:rA:ONE1'}), 0)

    def test_02_condition_and_whole_order(self):
        phones('rB', [{'序列号': 'X12', '型号': 'Mi 12', '品牌': 'Xiaomi', '版本': '12'},
                      {'序列号': 'X14', '型号': 'Mi 14', '品牌': 'Xiaomi', '版本': '14'},
                      {'序列号': 'X15', '型号': 'Mi 15', '品牌': 'Xiaomi', '版本': '15'},
                      {'序列号': 'S13', '型号': 'Galaxy', '品牌': 'Samsung', '版本': '13'}])
        r = resources.claim('手机', {'执行机': 'rB', '版本范围': ['12', '14'], '品牌': 'Xiaomi'}, 2, 'c1', '', 'u', 'pX')
        self.assertTrue(r['ok'])
        self.assertEqual(sorted(h['资源'] for h in r['holds']), ['phone:rB:X12', 'phone:rB:X14'])
        r = resources.claim('手机', {'执行机': 'rB', '品牌': 'Nokia'}, 1, 'c2', '', 'u', 'pX')
        self.assertEqual((r['ok'], r['error'], r['ticket']), (False, resources.NO_MATCH, ''))     # 无匹配立即返回，不排队
        self.assertEqual(db.count('waits', {'session': 'c2'}), 0)
        r = resources.claim('手机', {'执行机': 'rB', '品牌': 'Xiaomi'}, 2, 'c3', '', 'u', 'pX', wait=False)
        self.assertFalse(r['ok'])
        self.assertEqual(r['holds'], [])                        # 整单：只剩 1 台时 2 台都不拿
        self.assertEqual(db.count('holds', {'session': 'c3'}), 0)
        r = resources.claim('手机', {'执行机': 'rB'}, 1, 'c4', '', 'u', 'pX')
        self.assertTrue(r['ok'])                                # 空条件：任意空闲
        resources.release_session('c1')
        resources.release_session('c4')

    def test_03_fake_clock_12h(self):
        phones('rC', [{'序列号': 'T1', '型号': 'T'}])
        r = resources.claim('手机', {'执行机': 'rC'}, 1, 'long', '', 'u', 'pX', mode='申请')
        self.assertTrue(r['ok'])
        resources.tick()
        self.assertEqual(db.count('holds', {'session': 'long'}), 1)
        resources.set_fake_offset(12 * 3600 + 60)
        try:
            resources.tick()
            self.assertEqual(db.count('holds', {'session': 'long'}), 0)
        finally:
            os.environ.pop('KB_FAKE_CLOCK', None)

    def test_04_phone_wait_limits_and_timeout(self):
        phones('rD', [{'序列号': 'D1', '型号': 'D'}])
        self.assertTrue(resources.claim('手机', {'执行机': 'rD'}, 1, 'h', '', 'x', 'pX')['ok'])
        a = resources.claim('手机', {'执行机': 'rD'}, 1, 'w1', '', 'lim', 'pX')
        b = resources.claim('手机', {'执行机': 'rD', '型号': 'D'}, 1, 'w2', '', 'lim', 'pX')
        c = resources.claim('手机', {'执行机': 'rD', '机型': 'D'}, 1, 'w3', '', 'lim', 'pX')
        self.assertTrue(a['ticket'] and b['ticket'])
        self.assertIn('每人同时最多排 2 个', c['error'])
        self.assertIn('预计', a['error'])
        resources.set_fake_offset(3601)
        try:
            resources.tick()
        finally:
            os.environ.pop('KB_FAKE_CLOCK', None)
        self.assertEqual(resources.wait_status(a['ticket'])['状态'], '超时')
        for s in ('h', 'w1', 'w2'):
            resources.release_session(s)

    def test_05_release_api_and_force(self):
        phones('rE', [{'序列号': 'E1', '型号': 'E'}])
        r = resources.claim('手机', {'执行机': 'rE'}, 1, 'sessE', '期E', 'rsmem', self.P)
        self.assertTrue(r['ok'])
        st, b = self.own.post('/api/resource/release', {'资源': 'phone:rE:E1'})
        self.assertEqual(st, 403)
        st, b = self.own.post('/api/resource/release', {'资源': 'phone:rE:E1', '强制': True})
        self.assertEqual(st, 403)
        st, b = self.boss.post('/api/resource/release', {'资源': 'phone:rE:E1', '强制': True})
        self.assertEqual(st, 200, b)
        self.assertTrue(resources.recalled('phone:rE:E1', session='sessE'))
        total, items = db.audit_query(kind='资源')
        self.assertTrue(any('原占用者 rsmem' in (i['detail'] or '') for i in items))
        st, b = self.boss.post('/api/resource/release', {'资源': 'phone:rE:E1'})
        self.assertEqual(st, 400)
        st, b = self.mem.get('/api/resources?type=手机')
        self.assertEqual(st, 200)
        self.assertTrue(any(x['id'] == 'phone:rE:E1' for x in b['资源']))
        self.assertEqual(self.mem.get('/api/resources?type=电脑')[0], 400)

    def test_06_devlock_adapter(self):
        from kblib.infra import devlock
        with devlock.locked() as data:
            data['LOCAL9'] = {'期': '某期', '方式': '申请', '开始': devlock.stamp(), '分支': 'b', '最后使用': time.time()}
        self.assertEqual(devlock.load()['LOCAL9']['分支'], 'b')
        hs = db.holds_like('phone:local:LOCAL9')
        self.assertEqual((len(hs), hs[0]['period'], hs[0]['mode']), (1, '某期', '申请'))
        resources.release('phone:local:LOCAL9', by='rsboss', force=True)
        self.assertNotIn('LOCAL9', devlock.load())
        self.assertTrue(devlock.recalled('LOCAL9', '某期'))

    # ---------------------------------------------------------------- 测试账号（REQ-046 REQ-047）
    def test_10_test_accounts_crud_and_claim(self):
        st, b = self.ro.post('/api/test-account', {'项目': self.P, '名称': 'ro加'})
        self.assertEqual(st, 403)
        st, b = self.mem.post('/api/test-account', {'项目': self.P, '名称': 'VIP甲', '账号': 'vip1', '密码': 'pw1', '标签': ['VIP']})
        self.assertEqual(st, 200, b)
        t1 = b['id']
        st, b = self.mem.post('/api/test-account', {'项目': self.P, '名称': '坏', '环境': '生产'})
        self.assertEqual(st, 400)
        st, b = self.mem.post('/api/test-account', {'id': t1, '允许同时使用数': 2})
        self.assertEqual(st, 403)                                # 删改限项目负责人
        st, b = self.own.post('/api/test-account', {'项目': self.P, '名称': '三人号', '账号': 'multi', '允许同时使用数': 3, '标签': ['多人']})
        t3 = b['id']
        st, b = self.own.post('/api/test-account', {'项目': self.P, '名称': '线上号', '环境': '线上', '账号': 'prod'})
        self.assertEqual(st, 200)
        st, b = self.mem.get(f'/api/test-accounts?project={self.P}&env=测试')
        self.assertEqual(st, 200)
        v = {x['名称']: x for x in b['账号']}
        self.assertEqual(v['VIP甲']['密码'], '●●●●●●')
        self.assertNotIn('线上号', v)
        self.assertEqual(self.other.get(f'/api/test-accounts?project={self.P}')[0], 403)
        # 允许同时使用数 1：第二个排队
        a = resources.claim('测试账号', {'项目': self.P, '标签': ['VIP'], 'AI': True}, 1, 'ta1', '', 'u', self.P)
        b2 = resources.claim('测试账号', {'项目': self.P, '标签': ['VIP'], 'AI': True}, 1, 'ta2', '', 'u', self.P)
        self.assertTrue(a['ok'])
        self.assertEqual((b2['ok'], b2['位置']), (False, 1))
        resources.release_session('ta1')
        self.assertTrue(resources.claim('测试账号', {'项目': self.P, '标签': ['VIP']}, 1, 'ta2', '', 'u', self.P)['ok'])
        # 允许同时使用数 3：3 个都拿到，第 4 个排队
        rs = [resources.claim('测试账号', {'id': t3}, 1, f'm{i}', '', 'u', self.P) for i in range(4)]
        self.assertEqual([r['ok'] for r in rs], [True, True, True, False])
        # 线上环境 AI 默认拒绝
        r = resources.claim('测试账号', {'项目': self.P, '环境': '线上', 'AI': True}, 1, 'ai', '', 'u', self.P)
        self.assertFalse(r['ok'])
        self.assertIn('线上', r['error'])
        # 空闲 15 分钟自动释放
        resources.set_fake_offset(16 * 60)
        try:
            resources.tick()
        finally:
            os.environ.pop('KB_FAKE_CLOCK', None)
        self.assertEqual(db.count('holds', {'session': 'm0'}), 0)
        # 密码明文走机密规则 + 审计
        from kblib.core.common import with_user
        pw = with_user({'via': '网页', 'user': 'rsmem'}, kb.test_account_secret, t1)
        self.assertEqual(pw, 'pw1')
        with self.assertRaises(kb.KBError):
            with_user({'via': '网页', 'user': 'rsout'}, kb.test_account_secret, t1)
        for s in ('ta2', 'm0', 'm1', 'm2', 'm3'):
            resources.release_session(s)
        st, b = self.own.post('/api/test-account', {'id': t3, '删除': True})
        self.assertEqual(st, 200)

    def test_11_migrate_base_test_accounts(self):
        kb.base_save(category='测试账号', name='老测试号', fields=[{'名': '账号', '值': 'old1'}, {'名': '密码', '值': 'oldpw', '机密': True}],
                     by='测试')
        n = kb.test_accounts_migrate()
        self.assertGreaterEqual(n, 1)
        t = [x for x in kb.test_account_list() if x['名称'] == '老测试号'][0]
        self.assertEqual((t['项目'], t['环境'], t['账号']), ('default', '测试', 'old1'))
        self.assertEqual(kb.test_account_secret(t['id']), 'oldpw')
        self.assertEqual(kb.test_accounts_migrate(), 0)          # 幂等

    # ---------------------------------------------------------------- 引擎账号（REQ-018 REQ-038 REQ-041 REQ-042）
    def test_20_no_accounts_means_local_login(self):
        if db.count('engine_accounts'):
            self.skipTest('已有账号')
        a, why = accounts.pick({'id': 'tx', '类型': '开发', 'project': self.P, 'initiator': 'rsmem', 'payload': {}})
        self.assertEqual((a['名称'], why), ('本机登录态', ''))

    def test_21_engine_account_api(self):
        st, b = self.mem.post('/api/engine-account', {'引擎': 'claude', '名称': '共享1', '类型': '项目共享', '项目': self.P})
        self.assertEqual(st, 403)
        st, b = self.own.post('/api/engine-account', {'引擎': 'claude', '名称': '', '类型': '项目共享', '项目': self.P})
        self.assertEqual(st, 400)
        st, b = self.own.post('/api/engine-account', {'引擎': 'claude', '名称': '共享1', '类型': '项目共享', '项目': self.P,
                                                     '登录态': 'sk-test', '并发上限': 1, 'RPM上限': 10})
        self.assertEqual(st, 200, b)
        a1 = b['id']
        st, b = self.mem.post('/api/engine-account', {'引擎': 'claude', '名称': '我的号', '类型': '个人', '登录态': '{"oauth":"x"}'})
        self.assertEqual(st, 200, b)
        mine = b['id']
        st, b = self.own.get('/api/engine-accounts')
        names = [x['名称'] for x in b['账号']]
        self.assertIn('共享1', names)
        self.assertNotIn('我的号', names)                        # 个人账号只本人可见
        self.assertNotIn('sk-test', json.dumps(b, ensure_ascii=False))
        st, b = self.mem.get('/api/engine-accounts')
        self.assertIn('我的号', [x['名称'] for x in b['账号']])
        with self.assertRaises(kb.KBError):
            accounts.login_blob(mine, 'rsown')                       # 别人的会话不能拿个人登录态
        self.assertEqual(accounts.login_blob(mine, 'rsmem'), {'oauth': 'x'})
        self.assertEqual(accounts.login_blob(a1, 'rsown'), {'apiKey': 'sk-test'})
        accounts.PROBE = lambda a: True
        try:
            st, b = self.own.post('/api/engine-account/probe', {'id': a1})
            self.assertEqual((st, b['状态']), (200, '可用'))
            self.assertEqual(self.other.post('/api/engine-account/probe', {'id': a1})[0], 403)
        finally:
            accounts.PROBE = None
        # 名额：并发 1，占了就排队
        task = {'id': 'q1', '类型': '开发', 'project': self.P, 'initiator': 'rsown', 'payload': {}}
        a, why = accounts.pick(task)
        self.assertEqual((a['id'], why), (a1, ''))
        self.assertTrue(accounts.acquire('q1', a1))
        a, why = accounts.pick(dict(task, id='q2'))
        self.assertIsNone(a)
        self.assertIn('等 AI 账号 共享1 的名额：第 1 位', why)
        accounts.release_task('q1')
        self.assertEqual(accounts.pick(dict(task, id='q2'))[0]['id'], a1)
        # RPM 80% 暂缓
        for _ in range(8):
            accounts.record_call(a1)
        self.assertIn('请求较多', accounts.pick(dict(task, id='q3'))[1])
        db.delete('engine_calls', {'account': a1})
        # 删除
        st, b = self.mem.post('/api/engine-account', {'id': mine, '删除': True})
        self.assertEqual(st, 200)

    def test_22_error_classes(self):
        _, aid = accounts.save({'引擎': 'claude', '名称': '分类号', '类型': '项目共享', '项目': self.P}, 'rsboss')
        g = lambda: accounts.get(aid)
        self.assertIsNone(accounts.report_error(aid, 'weird', 'please check your quota and api key'))   # 不确定不计
        self.assertEqual(g()['status'], '未检测')
        for i in range(2):
            accounts.report_error(aid, '401', 'unauthorized')
        self.assertNotEqual(g()['status'], '不可用')                # 不到 3 次不改
        accounts.report_error(aid, '401', 'unauthorized')
        self.assertEqual(g()['status'], '不可用')
        self.assertTrue(any(e['data'].get('标题') == '知识库 · AI 账号不可用' for e in db.events_after(0)))
        _, b2 = accounts.save({'引擎': 'claude', '名称': '限流号', '类型': '项目共享', '项目': self.P}, 'rsboss')
        self.assertEqual(accounts.report_error(b2, '429'), '暂时')
        x = accounts.get(b2)
        self.assertEqual(x['status'], '冷却')                        # 冷却，不标不可用
        self.assertAlmostEqual(x['cool_until'] - db.now_ts(), 60, delta=3)
        accounts.report_error(b2, '429', retry_after=120)
        self.assertAlmostEqual(accounts.get(b2)['cool_until'] - db.now_ts(), 120, delta=3)
        _, b3 = accounts.save({'引擎': 'claude', '名称': '模型号', '类型': '项目共享', '项目': self.P}, 'rsboss')
        for _ in range(3):
            accounts.report_error(b3, '403', model='opus-x')
        x = accounts.get(b3)
        self.assertEqual((x['bad_models'], x['status']), (['opus-x'], '未检测'))
        # 恢复探测：连续 2 次成功才可用
        accounts.PROBE = lambda a: True
        try:
            db.update('engine_accounts', {'id': b2}, cool_until=db.now_ts() - 1, last_probe=0)
            accounts.probe(b2)
            self.assertEqual(accounts.get(b2)['status'], '冷却')
            accounts.probe(b2)
            self.assertEqual(accounts.get(b2)['status'], '可用')
        finally:
            accounts.PROBE = None
        for x in (aid, b2, b3):
            accounts.save({'id': x, '删除': True}, 'rsboss')

    def test_23_backup_chain_and_switch_limit(self):
        _, p1 = accounts.save({'引擎': 'claude', '名称': '主号', '类型': '项目共享', '项目': self.P, '备用顺序': -2}, 'rsboss')
        _, p2 = accounts.save({'引擎': 'claude', '名称': '备号', '类型': '项目共享', '项目': self.P, '备用顺序': -1}, 'rsboss')
        task = {'id': 'sw', '类型': '开发', 'project': self.P, 'initiator': 'rsown', 'payload': {}, 'account': '', 'switched': 0}
        self.assertEqual(accounts.pick(task)[0]['id'], p1)
        db.update('engine_accounts', {'id': p1}, status='不可用')
        a, _ = accounts.pick(dict(task, account=p1))
        self.assertEqual((a['id'], a['切换']), (p2, True))           # 长期故障：按备用链切
        db.update('engine_accounts', {'id': p1}, status='冷却', cool_until=db.now_ts() + 300)
        a, why = accounts.pick(task)
        self.assertIsNone(a)
        self.assertIn('冷却中', why)                                  # 冷却：等，不换号
        db.update('engine_accounts', {'id': p1}, status='可用', cool_until=0)
        a, why = accounts.pick(dict(task, account=p2, switched=db.now_ts()))
        self.assertIsNone(a)
        self.assertIn('10 分钟内已切换过', why)
        for x in (p1, p2):
            accounts.save({'id': x, '删除': True}, 'rsboss')
        db.delete('engine_accounts', {'project': self.P}) if not db.count('engine_accounts') else None

    # ---------------------------------------------------------------- 用量与预算（REQ-040）
    def test_30_budget(self):
        P = self.P
        self.assertEqual(accounts.budget_state('rsmem', P)[0], True)            # 0 = 不限
        st, b = self.mem.post('/api/budget', {'范围': '项目', '对象': P, '日上限': 1000})
        self.assertEqual(st, 403)
        st, b = self.own.post('/api/budget', {'范围': '项目', '对象': P, '日上限': 1000})
        self.assertEqual(st, 200, b)
        self.assertEqual(self.own.post('/api/budget', {'范围': '人', '对象': 'rsmem', '日上限': 5})[0], 403)
        self.assertEqual(self.own.post('/api/budget', {'范围': '组', '对象': 'x'})[0], 400)
        n0 = len([e for e in db.events_after(0) if e['data'].get('标题') == '知识库 · AI 预算提醒'])
        accounts.record_usage({'id': 'bt1', 'initiator': 'rsmem', 'project': P}, {'时长秒': 10, '输入token': 600, '输出token': 210})
        accounts.record_usage({'id': 'bt2', 'initiator': 'rsmem', 'project': P}, {'时长秒': 10, '输入token': 10, '输出token': 0})
        n1 = len([e for e in db.events_after(0) if e['data'].get('标题') == '知识库 · AI 预算提醒'])
        self.assertEqual(n1 - n0, 1)                                           # 80% 只通知一次
        self.assertTrue(accounts.budget_state('rsmem', P)[0])
        accounts.record_usage({'id': 'bt3', 'initiator': 'rsmem', 'project': P}, {'时长秒': 10, '输入token': 310, '输出token': 0})
        ok, why, _ = accounts.budget_state('rsmem', P)
        self.assertEqual((ok, why), (False, '本项目今日 AI 预算已用完'))
        self.assertTrue(accounts.over_session_budget('bt3'))                  # 单会话超日预算 30%
        st, b = self.mem.post('/api/budget/grant', {'项目': P, '额度': 500})
        self.assertEqual(st, 403)
        st, b = self.own.post('/api/budget/grant', {'项目': P, '额度': 500, '原因': '赶版本'})
        self.assertEqual(st, 200, b)
        self.assertTrue(accounts.budget_state('rsmem', P)[0])
        _, items = db.audit_query(kind='预算')
        self.assertTrue(any('临时加额' in (i['detail'] or '') for i in items))
        self.own.post('/api/budget', {'范围': '项目', '对象': P, '日上限': 100, '超额策略': '降级', '降级模型': 'haiku'})
        ok, why, cheap = accounts.budget_state('rsmem', P)
        self.assertEqual((ok, cheap), (True, 'haiku'))
        st, b = self.own.get(f'/api/usage?by=项目&project={P}')
        self.assertEqual(st, 200)
        self.assertEqual(b['行'][0]['会话数'], 3)
        self.assertEqual(self.own.get('/api/usage?by=天')[0], 400)
        st, b = self.mem.get('/api/usage?by=人')
        self.assertTrue(all(r['对象'] == 'rsmem' for r in b['行']))
        r = accounts.record_usage({'id': 'bt4', 'initiator': 'rsmem', 'project': P}, {'时长秒': 100})
        self.assertTrue(r['估算'])
        self.own.post('/api/budget', {'范围': '项目', '对象': P, '日上限': 0, '超额策略': '排队'})

    # ---------------------------------------------------------------- 设备提供方（REQ-043）
    def test_40_providers(self):
        st, b = self.mem.get('/api/device-providers')
        self.assertEqual(st, 200)
        mock = [x for x in b['提供方'] if x['id'] == 'mock'][0]
        self.assertEqual((mock['启用'], mock['状态'], mock['模拟']), (False, '未启用', True))
        self.assertEqual(self.mem.post('/api/device-provider', {'id': 'mock', '启用': True})[0], 403)
        self.assertEqual(self.boss.post('/api/device-provider', {'id': 'nope'})[0], 400)
        self.assertEqual(self.boss.post('/api/device-provider', {'id': 'mock', '模拟行为': '乱来'})[0], 400)
        st, b = self.boss.post('/api/device-provider', {'id': 'mock', '启用': True})
        self.assertEqual(st, 200, b)
        r = providers.session_run('mock', {}, 1, 'pv1', artifact='a.apk', cmds=['echo hi'])
        self.assertTrue(r['ok'], r)
        self.assertTrue(r['模拟'])
        self.assertEqual([s['步骤'] for s in r['步骤']], ['安装', '执行', '截图日志'])
        self.assertEqual(db.count('holds', {'session': 'pv1'}), 0)
        self.boss.post('/api/device-provider', {'id': 'mock', '模拟行为': '故意失败'})
        r = providers.session_run('mock', {}, 1, 'pv2')
        self.assertEqual((r['ok'], r['错误类型']), (False, '提供方不可用'))
        self.assertEqual(db.count('holds', {'session': 'pv2'}), 0)              # 失败自动释放
        self.boss.post('/api/device-provider', {'id': 'mock', '模拟行为': '故意慢', '配置': {'慢秒数': 0.05}})
        r = providers.session_run('mock', {}, 1, 'pv3')
        self.assertEqual(r['错误类型'], '超时')
        r = providers.session_run('mock', {}, 5, 'pv4')
        self.assertFalse(r['ok'])
        self.assertTrue(r['error'].startswith('无空闲'))
        self.boss.post('/api/device-provider', {'id': 'mock', '启用': False, '模拟行为': '正常'})

    # ---------------------------------------------------------------- 产物与证据（REQ-049 REQ-050）
    def test_50_artifacts_and_evidence(self):
        kb.create_period('产物期', 's')
        rid = 'run-art'
        db.insert('runners', id=rid, name='编译机', created=db.ts_str())
        tok = deploy.runner_token(rid)
        db.insert('tasks', id='tart', type='构建安装', period='产物期', project='default', initiator='rsboss', status='运行', runner=rid,
                  created=db.now_ts(), updated=db.now_ts())
        data = b'APKDATA' * 100
        import hashlib
        sha = hashlib.sha256(data).hexdigest()
        rc = C()
        body, ct = multipart({'meta': json.dumps({'task': 'tart', '版本': '1.0', '分支': 'dev', '提交': 'abc', 'sha256': 'bad'})},
                             ('app.apk', data))
        st, b = rc.req('POST', '/api/runner/artifact', raw=body, headers={'Content-Type': ct, 'Authorization': f'Bearer {tok}'})
        self.assertEqual(st, 400)
        self.assertIn('SHA256', b['error'])
        st, b = rc.req('POST', '/api/runner/artifact', raw=body, headers={'Content-Type': ct, 'Authorization': 'Bearer kbr_bad'})
        self.assertEqual(st, 401)
        body, ct = multipart({'meta': json.dumps({'task': 'tart', '版本': '1.0', '分支': 'dev', '提交': 'abc', 'sha256': sha})},
                             ('app.apk', data))
        st, b = rc.req('POST', '/api/runner/artifact', raw=body, headers={'Content-Type': ct, 'Authorization': f'Bearer {tok}'})
        self.assertEqual(st, 200, b)
        aid = b['id']
        st, b = self.boss.get('/api/artifacts?period=产物期')
        self.assertEqual((st, b['产物'][0]['sha256'], b['产物'][0]['大小']), (200, sha, len(data)))
        self.assertEqual(self.boss.get('/api/artifacts')[0], 400)
        st, b = self.boss.get(f'/api/artifact/download?id={aid}')
        self.assertEqual((st, b.get('raw')), (200, data))
        st, b = C().get(f'/api/artifact/download?id={aid}', headers={'Authorization': f'Bearer {tok}'})
        self.assertEqual(st, 200)
        self.assertEqual(self.boss.get('/api/artifact/download?id=nope')[0], 404)
        self.assertEqual(C().get(f'/api/artifact/download?id={aid}')[0], 401)
        # 证据
        body, ct = multipart({'task': 'tart', '类型': '截图', '步骤': '第 3 步', '模拟': 'true'}, ('s.png', b'PNG'))
        st, b = rc.req('POST', '/api/runner/evidence', raw=body, headers={'Content-Type': ct, 'Authorization': f'Bearer {tok}'})
        self.assertEqual(st, 200, b)
        body, ct = multipart({'task': 'tart', '类型': '录像'}, ('s.mp4', b'x'))
        st, b = rc.req('POST', '/api/runner/evidence', raw=body, headers={'Content-Type': ct, 'Authorization': f'Bearer {tok}'})
        self.assertEqual(st, 400)
        old = rroutes.EVI_MAX
        rroutes.EVI_MAX = 10
        try:
            body, ct = multipart({'task': 'tart', '类型': 'logcat'}, ('l.txt', b'L' * 70000))
            st, b = rc.req('POST', '/api/runner/evidence', raw=body, headers={'Content-Type': ct, 'Authorization': f'Bearer {tok}'})
            self.assertEqual(st, 413)
        finally:
            rroutes.EVI_MAX = old
        st, b = self.boss.get('/api/evidence?task=tart')
        self.assertEqual((st, len(b['证据']), b['模拟数据']), (200, 1, True))
        st, b2 = self.boss.get(f'/api/evidence/file?id={b["证据"][0]["id"]}')
        self.assertEqual((st, b2.get('raw')), (200, b'PNG'))
        # 契约 v2 错误码：400 缺任务 / 没有这个任务，401 未登录，403 无权看该项目，404 没有这条证据
        self.assertEqual(self.boss.get('/api/evidence')[0], 400)
        self.assertEqual(self.boss.get('/api/evidence?task=nope')[0], 400)
        self.assertEqual(C().get('/api/evidence?task=tart')[0], 401)
        self.assertEqual(C().get(f'/api/evidence/file?id={b["证据"][0]["id"]}')[0], 401)
        self.assertEqual(self.boss.get('/api/evidence/file?id=999999')[0], 404)
        self.assertEqual(self.boss.get('/api/evidence/file?id=abc')[0], 404)
        db.insert('tasks', id='tprj', type='构建安装', period='', project=self.P, initiator='rsown', status='运行', runner=rid,
                  created=db.now_ts(), updated=db.now_ts())
        body, ct = multipart({'task': 'tprj', '类型': 'logcat'}, ('l.txt', b'LOG'))
        st, b3 = rc.req('POST', '/api/runner/evidence', raw=body, headers={'Content-Type': ct, 'Authorization': f'Bearer {tok}'})
        self.assertEqual(st, 200, b3)
        self.assertEqual(self.other.get('/api/evidence?task=tprj')[0], 403)
        self.assertEqual(self.other.get(f'/api/evidence/file?id={b3["id"]}')[0], 403)
        st, b4 = self.mem.get('/api/evidence?task=tprj')
        self.assertEqual((st, [e['类型'] for e in b4['证据']], b4['模拟数据']), (200, ['logcat'], False))
        self.assertEqual(self.mem.get(f'/api/evidence/file?id={b3["id"]}')[1].get('raw'), b'LOG')
        db.delete('evidence', {'task': 'tprj'})
        # 没有期也没有项目的任务 / 任务已删：只给发起人和管理员（审查第 1 轮）
        db.insert('tasks', id='tnone', type='构建安装', period='', project='', initiator='rsmem', status='运行', runner=rid,
                  created=db.now_ts(), updated=db.now_ts())
        body, ct = multipart({'task': 'tnone', '类型': 'logcat'}, ('n.txt', b'NONE'))
        st, b5 = rc.req('POST', '/api/runner/evidence', raw=body, headers={'Content-Type': ct, 'Authorization': f'Bearer {tok}'})
        self.assertEqual(st, 200, b5)
        self.assertEqual(self.other.get('/api/evidence?task=tnone')[0], 403)
        self.assertEqual(self.other.get(f'/api/evidence/file?id={b5["id"]}')[0], 403)
        self.assertEqual(self.mem.get(f'/api/evidence/file?id={b5["id"]}')[0], 200)
        db.delete('tasks', {'id': 'tnone'})
        self.assertEqual(self.mem.get(f'/api/evidence/file?id={b5["id"]}')[0], 403)
        self.assertEqual(self.boss.get(f'/api/evidence/file?id={b5["id"]}')[0], 200)
        os.remove(db.get('evidence', id=b5['id'])['path'])                 # 文件被清理 → 404
        self.assertEqual(self.boss.get(f'/api/evidence/file?id={b5["id"]}')[0], 404)
        db.delete('evidence', {'task': 'tnone'})
        # 安装只能装到本会话占用的手机
        phones('rF', [{'序列号': 'F1', '型号': 'F'}, {'序列号': 'F2', '型号': 'F'}])
        resources.claim('手机', {'序列号': 'F1'}, 1, 'tart', '产物期', 'rsboss', 'default')
        resources.claim('手机', {'序列号': 'F2'}, 1, 'other', '别的期', 'rsmem', 'default')
        self.assertEqual(resources.artifact_install_check('tart', 'F1'), (True, ''))
        ok, why = resources.artifact_install_check('tart', 'F2')
        self.assertFalse(ok)
        self.assertIn('占用', why)
        resources.release_session('tart')
        resources.release_session('other')
        # 保留期：超过 7 天且不在最近 20 个里才删
        db.update('artifacts', {'id': aid}, ts=db.now_ts() - 8 * 86400)
        self.assertEqual(resources.artifacts_prune(), 0)
        db.update('evidence', {'task': 'tart'}, ts=db.now_ts() - 15 * 86400)
        self.assertEqual(resources.evidence_prune(), 1)


if __name__ == '__main__':
    unittest.main()
