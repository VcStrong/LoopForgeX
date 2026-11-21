"""平台化阶段二 · 执行机 / 任务队列 / 监控（REQ-011~REQ-020 REQ-026 REQ-029 REQ-035 REQ-036）。

    cd _system && python3 -m unittest tests.test_queue_api -v

临时 KB_ROOT / HOME，起一个真网页服务（随机端口），执行机协议按契约用 Bearer 调。
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

# 只在临时目录里跑：环境里的 KB_ROOT 可能指向真知识库，绝不能用它（和 test_platform 同进程时沿用它的临时库）
if not os.environ.get('KB_ROOT', '').startswith(tempfile.gettempdir().rstrip('/')) or os.environ.get('KB_SECRET_BACKEND') != 'file':
    TMP = Path(tempfile.mkdtemp(prefix='kb-queue-test-'))
    os.environ['KB_ROOT'] = str(TMP / 'kb')
    os.environ['HOME'] = str(TMP / 'home')
    os.environ['KB_SECRET_BACKEND'] = 'file'
    os.environ['KB_MASTER_KEY'] = 'test-master-key'
    (TMP / 'kb').mkdir()
    (TMP / 'home').mkdir()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import kb  # noqa: E402
from kblib.core import db, monitor, queue  # noqa: E402
from kblib.infra import deploy, vault  # noqa: E402
from webapp.server import H  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402

# 和别的测试同进程时 vault 可能已按真钥匙串初始化：强制加密文件后端，绝不弹钥匙串
from kblib.infra import vault as _vault  # noqa: E402
_vault.BACKEND = 'file'
os.environ.setdefault('KB_MASTER_KEY', 'test-master-key')

kb.init()
SRV = ThreadingHTTPServer(('127.0.0.1', 0), H)
PORT = SRV.server_address[1]
threading.Thread(target=SRV.serve_forever, daemon=True).start()
deploy.BIND['host'] = '127.0.0.1'


def req(method, path, body=None, cookie='', bearer='', page=True):
    c = http.client.HTTPConnection('127.0.0.1', PORT, timeout=60)
    h = {'Content-Type': 'application/json'}
    if page:
        h['Origin'] = f'http://127.0.0.1:{PORT}'
        h['Host'] = f'127.0.0.1:{PORT}'
    if cookie:
        h['Cookie'] = f'kb_s={cookie}'
    if bearer:
        h['Authorization'] = f'Bearer {bearer}'
    from urllib.parse import quote
    c.request(method, quote(path, safe='/?=&%:'), body=json.dumps(body).encode() if body is not None else None, headers=h)
    r = c.getresponse()
    data = r.read()
    sc = r.getheader('Set-Cookie') or ''
    try:
        out = json.loads(data or b'{}')
    except ValueError:
        out = {'raw': data[:200]}
    return r.status, out, (sc.split(';')[0][5:] if sc.startswith('kb_s=') else '')


def user(name, admin=False):
    """建一个已改过初始密码的账号并登录，返回 cookie。"""
    if not db.user_get(name):
        kb.user_create_strict(name, name + 'pass1')
        if admin:
            db.user_put(name, sys_role='管理员')
        _, _, ck = req('POST', '/api/login', {'username': name, 'password': name + 'pass1'})
        req('POST', '/api/me/password', {'old': name + 'pass1', 'new': name + 'pass2'}, cookie=ck)
    _, _, ck = req('POST', '/api/login', {'username': name, 'password': name + 'pass2'})
    return ck


def rpc(path, tok, body=None):
    st, b, _ = req('POST', path, body or {}, bearer=tok, page=False)
    return st, b


class Queue(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.adm = user('qadmin', admin=True)
        cls.bob = user('qbob')
        kb.ensure_default_project('qadmin')
        db.member_set('default', 'qbob', '成员')
        for r in db.rows('runners'):
            db.delete('runners', {'id': r['id']})
        db.delete('tasks')
        if not (kb.PERIODS / '队列期').is_dir():
            kb.create_period('队列期', '队列测试')

    @classmethod
    def tearDownClass(cls):
        kb.delete_period('队列期', by='测试')              # 同进程跑 test_platform 时别让它多数出一期
        db.kv_set('quota', {}, by='test')

    def setUp(self):
        db.kv_set('quota', {}, by='test')
        for r in db.rows('runners', {'local': 0}):              # 上一个用例的执行机不参与本用例的派活
            db.update('runners', {'id': r['id']}, disabled=1)
        queue._POLLING.clear()
        for t in db.rows('tasks'):
            if t['status'] not in queue.ENDED:
                db.update('tasks', {'id': t['id']}, status='取消')

    def _register(self, name, tags=(), rebind=''):
        st, b, _ = req('POST', '/api/runners/token', {'name': name}, cookie=self.adm)
        self.assertEqual(st, 200, b)
        body = {'name': name, 'version': 'test', '协议版本': queue.PROTO_VERSION, '自动标签': list(tags)}
        if rebind:
            body['rebind_id'] = rebind
        st, r = rpc('/api/runner/register', b['token'], body)
        self.assertEqual(st, 200, r)
        st2, r2 = rpc('/api/runner/register', b['token'], body)       # 一次性
        self.assertEqual(st2, 401)
        self.assertIn('注册令牌无效或已用过', r2['error'])
        return r['id'], r['token']

    def _hb(self, tok, **kw):
        body = {'自动标签': kw.pop('tags', None), 'CPU': {'负载1分': 0.2, '核数': 8}, '内存': {'可用比': 0.6, '总GB': 16},
                '磁盘': {'可用GB': 80, '总GB': 100}}
        body.update(kw)
        if body['自动标签'] is None:
            body.pop('自动标签')
        return rpc('/api/runner/heartbeat', tok, body)

    def _submit(self, kind='开发', who='qbob', **p):
        return queue.submit(kind, '队列期', {'提示词': 'hi', '仓库': 'demo', **p}, user=who)['task']

    # ---- REQ-011 注册 / REQ-020 权限
    def test_01_runners_admin_only_and_register(self):
        st, b, _ = req('GET', '/api/runners')
        self.assertEqual(st, 401)
        self.assertEqual(req('GET', '/api/runners', cookie=self.bob)[0], 403)
        self.assertEqual(req('POST', '/api/runners/token', {}, cookie=self.bob)[0], 403)
        st, b, _ = req('GET', '/api/runners', cookie=self.adm)
        self.assertEqual(st, 200, b)
        self.assertIn('kb runner --server', b['installHelp'])
        st, r = rpc('/api/runner/register', 'kbreg_bad', {'name': 'x', 'version': '1'})
        self.assertEqual(st, 401)
        rid, tok = self._register('甲机', ['mac', 'android'])
        st, b = self._hb(tok)
        self.assertEqual((st, b['状态']), (200, '在线'))
        self.assertEqual(rpc('/api/runner/heartbeat', 'kbr_bad')[0], 401)
        st, b, _ = req('GET', '/api/runners', cookie=self.adm)
        me = next(x for x in b['items'] if x['id'] == rid)
        self.assertEqual((me['状态'], me['自动标签']), ('在线', ['mac', 'android']))

    # ---- REQ-013 领活全流程 / REQ-014 日志回传
    def test_02_claim_ack_event_done(self):
        rid, tok = self._register('乙机')
        self._hb(tok)
        t = self._submit()
        self.assertEqual(t['状态'], '排队')
        st, b = rpc('/api/runner/claim', tok, {'协议版本': queue.PROTO_VERSION, '等待秒': 3})
        self.assertEqual(st, 200, b)
        got = b['task']
        self.assertEqual(got['id'], t['id'])
        self.assertTrue(got['sessionToken'].startswith('kbs_'))
        self.assertIn('git', got['凭据'])
        self.assertEqual(rpc('/api/runner/ack', tok, {'task': t['id'], '副本': {'仓库': 'demo', '序号': 1}})[0], 200)
        st, b = rpc('/api/runner/ack', tok, {'task': t['id']})
        self.assertEqual(st, 409)
        st, b = rpc('/api/runner/event', tok, {'task': t['id'], '状态': '运行', '日志': 'hello\n'})
        self.assertEqual((st, b['cancel']), (200, False))
        st, b, _ = req('GET', f'/api/task?id={t["id"]}', cookie=self.bob)
        self.assertEqual((st, b['日志'], b['状态']), (200, 'hello\n', '运行'))
        self.assertEqual(rpc('/api/runner/done', tok, {'task': t['id'], '结果': '完成', '退出码': 0})[0], 200)
        self.assertEqual(queue.task(t['id'])['status'], '完成')
        self.assertIsNone(deploy.token_identity(got['sessionToken']))          # 会话令牌随任务结束作废
        st, b = rpc('/api/runner/claim', tok, {'协议版本': queue.PROTO_VERSION, '等待秒': 0})
        self.assertEqual((st, b['task']), (200, None))

    # ---- REQ-029 协议版本
    def test_03_proto_mismatch(self):
        rid, tok = self._register('旧版机')
        st, b = rpc('/api/runner/claim', tok, {'协议版本': 99, '等待秒': 0})
        self.assertEqual(st, 409)
        self.assertEqual(b['need']['需要协议版本'], queue.PROTO_VERSION)
        self.assertEqual(queue.runner_state(queue.runner(rid)), '待升级')
        rpc('/api/runner/claim', tok, {'协议版本': queue.PROTO_VERSION, '等待秒': 0})
        self.assertEqual(queue.runner_state(queue.runner(rid)), '在线')

    # ---- REQ-036 打分 / REQ-012 容量
    def test_04_score_prefers_free_capacity(self):
        a, ta = self._register('A')
        b_, tb = self._register('B')
        self._hb(ta)
        self._hb(tb)
        busy = self._submit(who='qadmin')
        db.update('tasks', {'id': busy['id']}, status='运行', runner=b_)
        t = self._submit()
        now = time.time()
        queue._POLLING.update({a: now, b_: now})
        self.assertIsNone(queue._claim_once(b_))
        got = queue._claim_once(a)
        self.assertEqual(got['id'], t['id'])
        st, sc, _ = req('GET', f'/api/task/score?id={t["id"]}', cookie=self.bob)
        self.assertEqual(st, 200)
        self.assertEqual(sc['选中'], 'A')
        ca = next(c for c in sc['候选'] if c['执行机'] == 'A')
        cb = next(c for c in sc['候选'] if c['执行机'] == 'B')
        self.assertGreater(ca['总分'], cb['总分'])
        db.update('runners', {'id': a}, capacity=1)                     # 满了不再派
        self.assertEqual(queue.exclude_reason(queue.runner(a), {'payload': {}}), '已满')
        queue._POLLING.clear()

    def test_05_exclude_disk_and_tags(self):
        rid, tok = self._register('满盘机', ['linux'])
        self._hb(tok, 磁盘={'可用GB': 5, '总GB': 100})
        r = queue.runner(rid)
        self.assertTrue(queue.exclude_reason(r, {'payload': {}}).startswith('磁盘'))
        self._hb(tok)
        self.assertEqual(queue.exclude_reason(queue.runner(rid), {'payload': {'标签': ['ios']}}), '缺标签 ios')
        st, b, _ = req('POST', '/api/runners/update', {'id': rid, '自定义标签': ['ios']}, cookie=self.adm)
        self.assertEqual(st, 200, b)
        self.assertEqual(queue.exclude_reason(queue.runner(rid), {'payload': {'标签': ['ios']}}), '')

    # ---- REQ-013 确认超时 / REQ-014 掉线回队列、派回原执行机
    def test_06_ack_timeout_offline_and_affinity(self):
        rid, tok = self._register('掉线机')
        self._hb(tok, 副本=[{'仓库': 'demo', '序号': 1, '占用会话': ''}])
        t = self._submit()
        got = queue.claim(rid, {'协议版本': 1}, wait=2)
        self.assertEqual(got['id'], t['id'])
        db.update('tasks', {'id': t['id']}, ack_deadline=queue.now() - 1)
        queue.tick()
        t2 = queue.task(t['id'])
        self.assertEqual((t2['status'], t2['prev_runner']), ('排队', rid))
        got = queue.claim(rid, {'协议版本': 1}, wait=2)
        queue.ack(rid, t['id'], {'仓库': 'demo', '序号': 1, '占用会话': t['id']})
        self._hb(tok, 副本=[{'仓库': 'demo', '序号': 1, '占用会话': t['id']}])
        db.update('runners', {'id': rid}, last_hb=queue.now() - 200)
        queue.tick()
        r = queue.runner(rid)
        self.assertEqual(queue.runner_state(r), '离线')
        self.assertTrue(r['info']['副本'][0]['可能有改动'])
        self.assertEqual(queue.task(t['id'])['status'], '排队')
        self._hb(tok)
        self.assertEqual(queue.runner_state(queue.runner(rid)), '在线')
        got = queue.claim(rid, {'协议版本': 1}, wait=2)
        self.assertEqual(got['id'], t['id'])

    # ---- REQ-013 外部原因自动重试
    def test_07_external_retry(self):
        rid, tok = self._register('重试机')
        self._hb(tok)
        t = self._submit()
        queue.claim(rid, {'协议版本': 1}, wait=2)
        queue.ack(rid, t['id'])
        st, b = rpc('/api/runner/done', tok, {'task': t['id'], '结果': '失败', '失败原因': '网络断了', '外部原因': True})
        self.assertEqual(st, 200, b)
        t2 = queue.task(t['id'])
        self.assertEqual((t2['status'], t2['retries']), ('排队', 1))
        db.update('tasks', {'id': t['id']}, retries=5)
        queue.claim(rid, {'协议版本': 1}, wait=2)
        queue.ack(rid, t['id'])
        rpc('/api/runner/done', tok, {'task': t['id'], '结果': '失败', '失败原因': '网络断了', '外部原因': True})
        self.assertEqual(queue.task(t['id'])['status'], '失败')

    # ---- REQ-019 配额 / 有界队列
    def test_08_quota_and_queue_full(self):
        self.assertEqual(req('POST', '/api/quota', {'每人': 1}, cookie=self.bob)[0], 403)
        st, b, _ = req('POST', '/api/quota', {'每人': 1, '队列每项目': 2}, cookie=self.adm)
        self.assertEqual(st, 200, b)
        rid, tok = self._register('配额机')
        self._hb(tok)
        a = self._submit()
        db.update('tasks', {'id': a['id']}, status='运行', runner=rid)
        b1 = self._submit()
        queue.refresh_waits()
        self.assertEqual(queue.task(b1['id'])['wait_reason'], '你已有 1 个在跑')
        self.assertIsNone(queue.claim(rid, {'协议版本': 1}, wait=0))
        self._submit()
        with self.assertRaises(kb.KBNeed):
            self._submit()
        st, b, _ = req('POST', '/api/plugin/run', {'id': '没有的插件', 'period': '队列期'}, cookie=self.adm)
        self.assertEqual(st, 409)
        self.assertIn('队列已满（2）', b['error'])
        st, b, _ = req('GET', '/api/queue?mine=1', cookie=self.bob)
        self.assertEqual(st, 200)
        self.assertTrue(all(x['发起人'] == 'qbob' for x in b['items']))
        pos = [x for x in b['items'] if x['状态'] == '排队']
        self.assertTrue(pos and pos[-1]['排队位置'] >= 1)

    # ---- REQ-013 停止
    def test_09_cancel(self):
        rid, tok = self._register('停止机')
        self._hb(tok)
        t = self._submit()
        other = user('qcarol')
        self.assertEqual(req('POST', '/api/queue/cancel', {'id': t['id']}, cookie=other)[0], 403)
        st, b, _ = req('POST', '/api/queue/cancel', {'id': t['id']}, cookie=self.bob)
        self.assertEqual((st, queue.task(t['id'])['status']), (200, '取消'))
        t = self._submit()
        queue.claim(rid, {'协议版本': 1}, wait=2)
        queue.ack(rid, t['id'])
        req('POST', '/api/queue/cancel', {'id': t['id']}, cookie=self.bob)
        st, b = self._hb(tok, 会话=[{'task': t['id'], '状态': '运行'}])
        self.assertIn(t['id'], b['cancel'])
        self.assertTrue(rpc('/api/runner/event', tok, {'task': t['id']})[1]['cancel'])
        rpc('/api/runner/done', tok, {'task': t['id'], '结果': '失败'})
        self.assertEqual(queue.task(t['id'])['status'], '取消')

    # ---- REQ-020 排空 / 停用 / REQ-034 rebind
    def test_10_drain_disable_rebind(self):
        rid, tok = self._register('管理机')
        self._hb(tok)
        self.assertEqual(req('POST', '/api/runners/update', {'id': rid, '动作': '排空'}, cookie=self.bob)[0], 403)
        st, b, _ = req('POST', '/api/runners/update', {'id': rid, '动作': '排空'}, cookie=self.adm)
        self.assertEqual(st, 200, b)
        self.assertEqual(queue.runner_state(queue.runner(rid)), '已排空')
        req('POST', '/api/runners/update', {'id': rid, '动作': '启用'}, cookie=self.adm)
        rid2, tok2 = self._register('管理机', rebind=rid)
        self.assertEqual(rid2, rid)
        self.assertEqual(self._hb(tok)[0], 401)                  # 旧令牌作废
        self.assertEqual(self._hb(tok2)[0], 200)
        t = self._submit()
        queue.claim(rid, {'协议版本': 1}, wait=2)
        req('POST', '/api/runners/update', {'id': rid, '动作': '停用'}, cookie=self.adm)
        self.assertEqual(self._hb(tok2)[0], 401)
        self.assertEqual(queue.task(t['id'])['status'], '排队')
        st, b, _ = req('POST', '/api/runners/update', {'id': rid, '动作': '删除'}, cookie=self.adm)
        self.assertEqual(st, 200)
        self.assertIsNone(queue.runner(rid))

    # ---- REQ-035 指标 / 告警
    def test_11_metrics_and_alerts(self):
        rid, tok = self._register('告警机')
        old = dict(monitor.SUSTAIN)
        monitor.SUSTAIN.update({'磁盘': 0})
        try:
            self._hb(tok, 磁盘={'可用GB': 15, '总GB': 100})
            self._hb(tok, 磁盘={'可用GB': 15, '总GB': 100})
            st, b, _ = req('GET', '/api/alerts', cookie=self.adm)
            self.assertEqual(st, 200)
            a = next(x for x in b['告警'] if x['执行机id'] == rid)
            self.assertEqual((a['类型'], a['级别']), ('磁盘', '警告'))
            self.assertEqual(len([x for x in b['告警'] if x['执行机id'] == rid]), 1)      # 去重
            self.assertEqual(req('GET', '/api/alerts', cookie=self.bob)[0], 403)
            self.assertEqual(req('POST', '/api/alerts/ack', {'id': a['id'], '静默分钟': 30}, cookie=self.bob)[0], 403)
            st, b, _ = req('POST', '/api/alerts/ack', {'id': a['id'], '静默分钟': 30}, cookie=self.adm)
            self.assertEqual(st, 200, b)
            self._hb(tok)
            st, b, _ = req('GET', '/api/alerts?state=已恢复', cookie=self.adm)
            self.assertTrue(any(x['id'] == a['id'] and x['恢复'] for x in b['告警']))
        finally:
            monitor.SUSTAIN.update(old)
        st, b, _ = req('GET', f'/api/runners/metrics?id={rid}', cookie=self.adm)
        self.assertEqual(st, 200)
        self.assertTrue(b['点'])
        self.assertEqual(req('GET', f'/api/runners/metrics?id={rid}', cookie=self.bob)[0], 403)

    # ---- REQ-026 单机模式：本机执行机立即执行
    def test_12_local_runner(self):
        for r in db.rows('runners', {'local': 0}):
            db.update('runners', {'id': r['id']}, status='离线')
        r = queue.submit('插件', '队列期', {}, local=lambda: '本机跑完了', user='qbob')
        self.assertEqual((r['result'], r['task']['状态'], r['task']['执行机']), ('本机跑完了', '完成', '本机'))
        with self.assertRaises(kb.KBError):
            queue.submit('插件', '队列期', {}, local=lambda: (_ for _ in ()).throw(kb.KBError('已经在跑')), user='qbob')
        alive = {'v': True}
        orig = queue._local_alive
        queue._local_alive = lambda t: alive['v']
        try:
            r = queue.submit('开发', '队列期', {'local_check': {'kind': 'dev'}}, local=lambda: 'started', user='qbob')
            self.assertEqual(r['task']['状态'], '运行')
            db.update('tasks', {'id': r['task']['id']}, updated=queue.now() - 20)
            alive['v'] = False
            queue.tick()
            self.assertEqual(queue.task(r['task']['id'])['status'], '完成')
        finally:
            queue._local_alive = orig

    # ---- REQ-015 REQ-016 按执行机分组 / REQ-017 凭据
    def test_13_groups_and_creds(self):
        rid, tok = self._register('分组机')
        self._hb(tok, 副本=[{'仓库': 'demo', '序号': 2}], 手机=[{'序列号': 'SN1', '型号': 'Pixel'}])
        st, b, _ = req('GET', '/api/pool', cookie=self.adm)
        self.assertEqual(st, 200, b)
        g = next(x for x in b['items'] if x['id'] == rid)
        self.assertEqual(g['副本'][0]['序号'], 2)
        st, b, _ = req('GET', '/api/devices', cookie=self.adm)
        g = next(x for x in b['items'] if x['id'] == rid)
        self.assertEqual(g['手机'][0]['序列号'], 'SN1')
        c = vault.session_creds('qbob', 'default', 'T1')
        self.assertEqual(c['git']['方式'], '缺')
        vault.bot_save('default', 'kb-bot', 'bot-secret', by='qadmin')
        c = vault.session_creds('qbob', 'default', 'T1')
        self.assertEqual((c['git']['方式'], c['git']['coauthor']), ('bot', 'qbob'))


if __name__ == '__main__':
    unittest.main()


class AdminOverview(unittest.TestCase):
    """管理员「📊 总览」看板（T002）：只给管理员；各块数据来自真实的执行机 / 任务 / 告警 / 审计。"""

    def test_overview(self):
        adm, bob = user('qadmin', admin=True), user('qbob')
        st, b, _ = req('POST', '/api/runners/token', {'name': '看板机'}, cookie=adm)
        st, r = rpc('/api/runner/register', b['token'], {'name': '看板机', 'version': 'test', '协议版本': queue.PROTO_VERSION})
        rpc('/api/runner/heartbeat', r['token'], {'CPU': {'负载1分': 4, '核数': 8}, '内存': {'可用比': 0.5, '总GB': 16},
                                                  '磁盘': {'可用GB': 50, '总GB': 100}})
        if not (kb.PERIODS / '看板期').is_dir():
            kb.create_period('看板期', '看板测试')
        t = queue.submit('开发', '看板期', {'提示词': 'x', '只在本机': False}, user='qbob')['task']
        db.audit('机密', 'qbob', 'B001/密码', 'default', '拒绝', '测试')
        self.assertEqual(req('GET', '/api/admin/overview')[0], 401)
        st, b, _ = req('GET', '/api/admin/overview', cookie=bob)
        self.assertEqual((st, b.get('error')), (403, '只有管理员能看总览'))
        st, d, _ = req('GET', '/api/admin/overview', cookie=adm)
        self.assertEqual(st, 200, d)
        for k in ('系统', '人', '项目', '执行机', '队列', '资源', 'AI', '审计', '定时任务', '概要'):
            self.assertIn(k, d)
            self.assertFalse(isinstance(d[k], dict) and d[k].get('error'), (k, d[k]))
        me = next(p for p in d['人'] if p['用户'] == 'qbob')
        self.assertGreaterEqual(me['排队'] + me['在跑'], 1)
        self.assertTrue(any(x['任务'] == t['id'] for x in me['进行中']))
        self.assertTrue(any(p['名称'] == '看板期' for p in d['项目']['期']))
        self.assertEqual(len(d['项目']['推进趋势']), 14)
        rr = next(x for x in d['执行机']['列表'] if x['名称'] == '看板机')
        self.assertEqual((rr['状态'], rr['CPU']), ('在线', 0.5))
        self.assertGreaterEqual(d['审计']['机密拒绝'], 1)
        self.assertEqual(len(d['AI']['趋势']), 30)
        json.dumps(d)                                     # 能序列化（没有 Decimal 之类）
        # 任务表查询出错：只有「人」「队列」两块报错，其余照常（审查第 1 轮）
        from kblib.api import admin_overview as ao
        orig = ao._db.rows
        ao._db.rows = lambda tb, *a, **k: (_ for _ in ()).throw(RuntimeError('库坏了')) if tb == 'tasks' else orig(tb, *a, **k)
        try:
            st, d2, _ = req('GET', '/api/admin/overview', cookie=adm)
        finally:
            ao._db.rows = orig
        self.assertEqual(st, 200, d2)
        self.assertIn('库坏了', d2['人']['error'])
        self.assertIn('库坏了', d2['队列']['error'])
        self.assertFalse(isinstance(d2['系统'], dict) and d2['系统'].get('error'))
        queue.cancel(t['id'], {'via': '网页', 'user': 'qadmin'})
        kb.delete_period('看板期', by='测试')
