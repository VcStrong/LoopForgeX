"""安装到手机（事项「手机和 Jenkins 构建一键安装（按端推荐）」）的单测。

    cd _system && python3 -m unittest tests.test_install -v

用临时 KB_ROOT，假的构建列表、手机列表和 adb，不碰真实手机和 Jenkins。
"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

TMP = Path(tempfile.mkdtemp(prefix='kb-install-test-'))
os.environ['KB_ROOT'] = str(TMP / 'kb')
(TMP / 'kb').mkdir()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import kb  # noqa: E402
from kblib.core.common import KBError, KBNeed  # noqa: E402
from kblib.core.periods import load_meta, period_dir  # noqa: E402
from kblib.infra import devlock  # noqa: E402
from kblib.plugins import install as ins  # noqa: E402

V = '95.0.0.21'
PATHS = ['build_output/myapp-gp-release-unsigned.apk', f'output/aab/myapp_gp_6000_{V}_signed.aab',
         f'output/release/64-bit/myapp_guanfang_1000.{V}(64).apk', f'output/release/all/myapp_gp_6000.{V}.apk',
         f'output/release/all/myapp_guanfang_1000.{V}.apk', f'output/release/all/myapp_ceshi_9999.{V}.apk']
URL = 'http://j/job/app/21/'
BUILD = {'编号': 21, '地址': URL, '结果': 'SUCCESS', '进行中': False, '版本': V, '时间': '', '分支': 'f',
         '产物': [{'名称': p.rsplit('/', 1)[-1], '路径': p, '地址': URL + 'artifact/' + p, '类型': '安装包'} for p in PATHS]}
P = '安装测试期'
OTHER = '别的期'


def phones():
    hold = devlock.holders()
    out = []
    for sn in ('A', 'B'):
        c = hold.get(sn)
        out.append({'序列号': sn, '型号': 'M' + sn, '状态': '在线', '占用': dict(c, 最后使用=devlock.stamp(c['最后使用'])) if c else None,
                    '本期': bool(c and c.get('期') == P and c.get('方式') != '本人'), '空闲': not c, 'agent-device': None})
    return out


class InstallTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        kb.create_period(P)
        kb.create_period(OTHER)

    def setUp(self):
        devlock.FILE.write_text('{}', 'utf-8')
        devlock.RECENT.write_text('{}', 'utf-8')
        m = load_meta(period_dir(P))
        m.pop('安装渠道', None)
        from kblib.core.periods import save_meta
        save_meta(period_dir(P), m)
        self.adb_calls = []
        self.adb_out = []
        ps = [mock.patch.object(ins._builds, 'period_builds', lambda n, *a, **k: {'任务': [{'名称': 'app', '构建': [BUILD]}]}),
              mock.patch.object(ins._builds, 'build_plugin', lambda required=True: {'id': 'Jenkins', '配置': {'默认渠道匹配': 'guanfang'}}),
              mock.patch.object(ins._dev, 'devices', lambda n='': phones()),
              mock.patch.object(ins._dev, '_adb', lambda: '/x/adb'),
              mock.patch.object(ins, 'period_android', lambda n: None),
              mock.patch.object(ins, '_adb_run', self.fake_adb),
              mock.patch.object(ins, 'CACHE_DIR', TMP / 'cache')]
        for p in ps:
            p.start()
            self.addCleanup(p.stop)
        self.downloads = 0

        def fake_dl(p, url, dest, job):
            if dest.is_file():
                job.update(缓存=True, 进度=100)
                return
            self.downloads += 1
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b'apk')
        dl = mock.patch.object(ins, '_download', fake_dl)
        dl.start()
        self.addCleanup(dl.stop)

    def fake_adb(self, args, timeout=0):
        self.adb_calls.append(args)
        return self.adb_out.pop(0) if self.adb_out else (0, 'Performing Streamed Install\nSuccess')

    def wait(self, jid):
        for _ in range(100):
            j = kb.install_job(jid)
            if j['状态'] not in ('下载中', '安装中'):
                return j
            time.sleep(0.05)
        self.fail('安装任务没结束')

    # 验收 2：默认渠道 上次 → 正则 → 最短路径
    def test_default_channel(self):
        apks, d, why = ins.apk_choices(BUILD, 'output/release/all/', '', '')
        self.assertEqual(len(apks), 3)
        self.assertEqual((d, why), (f'output/release/all/myapp_gp_6000.{V}.apk', '路径最短'))
        _, d, why = ins.apk_choices(BUILD, 'output/release/all/', 'guanfang|official', '')
        self.assertEqual((d, why), (f'output/release/all/myapp_guanfang_1000.{V}.apk', '匹配「guanfang|official」'))
        last = ins.channel_key(f'output/release/all/myapp_ceshi_9999.95.0.0.20.apk')     # 上一次构建选的，版本号不同
        _, d, why = ins.apk_choices(BUILD, 'output/release/all/', 'guanfang', last)
        self.assertEqual((d, why), (f'output/release/all/myapp_ceshi_9999.{V}.apk', '本期上次选的'))
        apks, _, _ = ins.apk_choices(BUILD)
        self.assertFalse(any(a['路径'].endswith('.aab') or 'unsigned' in a['路径'] for a in apks))
        apks, _, _ = ins.apk_choices(BUILD, 'nope/', '[bad', '')          # 前缀对不上不过滤，坏正则忽略
        self.assertEqual(len(apks), 4)

    # 验收 1：非 Android 期不提供
    def test_not_android(self):
        with mock.patch.object(ins, 'period_android', lambda n: False):
            with self.assertRaises(KBError):
                kb.install_options(P)
            self.assertFalse(kb.install_latest(P)['可装'])
        self.assertTrue(kb.install_latest(P)['可装'])
        no_apk = dict(BUILD, 产物=[a for a in BUILD['产物'] if not a['路径'].endswith('.apk')])
        with mock.patch.object(ins._builds, 'period_builds', lambda n, *a, **k: {'任务': [{'名称': 'app', '构建': [no_apk]}]}):
            self.assertFalse(kb.install_latest(P)['可装'])

    def test_options_phone_order(self):
        with devlock.locked() as d:
            d['A'] = {'期': OTHER, '方式': '申请', '最后使用': time.time()}
        o = kb.install_options(P, URL)
        self.assertEqual(o['默认'], f'output/release/all/myapp_guanfang_1000.{V}.apk')   # 没配前缀：匹配正则里最短的
        self.assertEqual(o['默认手机'], 'B')
        a = next(x for x in o['手机'] if x['序列号'] == 'A')
        self.assertFalse(a['可装'])
        self.assertTrue(a['可强制'])
        self.assertIn(OTHER, a['说明'])

    # 验收 3 + 4：他期占用要 force；装成功记为本期占用；改的渠道记到本期
    def test_busy_force_and_claim(self):
        with devlock.locked() as d:
            d['A'] = {'期': OTHER, '方式': '申请', '最后使用': time.time()}
        path = f'output/release/all/myapp_ceshi_9999.{V}.apk'
        with self.assertRaises(KBNeed):
            kb.install_start(P, URL, path, 'A')
        self.assertEqual(self.adb_calls, [])
        j = self.wait(kb.install_start(P, URL, path, 'A', force=True))
        self.assertEqual(j['状态'], '成功')
        self.assertEqual(devlock.load()['A']['期'], P)
        self.assertEqual(load_meta(period_dir(P))['安装渠道'], ins.channel_key(path))

    # 验收 5：签名冲突先停下，确认后才卸载
    def test_signature_conflict(self):
        path = f'output/release/all/myapp_gp_6000.{V}.apk'
        bad = (1, 'Failure [INSTALL_FAILED_UPDATE_INCOMPATIBLE: Existing package com.vv.sing signatures do not match newer version; ignoring!]')
        self.adb_out = [bad]
        j = self.wait(kb.install_start(P, URL, path, 'B'))
        self.assertEqual((j['状态'], j['包名']), ('签名冲突', 'com.vv.sing'))
        self.assertFalse(any('uninstall' in c for c in self.adb_calls))
        self.assertNotIn('B', devlock.load())
        self.adb_out = [bad, (0, 'Success'), (0, 'Success')]
        j = self.wait(kb.install_start(P, URL, path, 'B', uninstall=True))
        self.assertEqual(j['状态'], '成功')
        self.assertIn(['-s', 'B', 'uninstall', 'com.vv.sing'], self.adb_calls)

    def test_downgrade_retry(self):
        self.adb_out = [(1, 'Failure [INSTALL_FAILED_VERSION_DOWNGRADE]'), (0, 'Success')]
        j = self.wait(kb.install_start(P, URL, PATHS[3], 'B'))
        self.assertEqual(j['状态'], '成功')
        self.assertIn('-d', self.adb_calls[-1])

    # 验收 6：同一个包第二次不重新下载
    def test_cache(self):
        self.wait(kb.install_start(P, URL, PATHS[4], 'B'))
        j = self.wait(kb.install_start(P, URL, PATHS[4], 'B'))
        self.assertEqual(self.downloads, 1)
        self.assertTrue(j['缓存'])

    # 验收 7：失败原样显示 adb 输出，不改占用
    def test_failure_keeps_holder(self):
        raw = 'adb: failed to install x.apk: Failure [INSTALL_FAILED_INSUFFICIENT_STORAGE]'
        self.adb_out = [(1, raw)]
        j = self.wait(kb.install_start(P, URL, PATHS[3], 'B'))
        self.assertEqual(j['状态'], '失败')
        self.assertIn(raw, j['输出'])
        self.assertNotIn('B', devlock.load())

    def test_real_download_cache_and_prune(self):
        """真的 _download：缓存命中不起插件；超过 KEEP 个时删最旧的。"""
        cache = TMP / 'cache2'
        cache.mkdir()
        f = cache / 'x.apk'
        f.write_bytes(b'1')
        job = {}
        with mock.patch.object(ins, 'CACHE_DIR', cache), mock.patch('subprocess.Popen') as pop:
            REAL_DL(None, 'u', f, job)
            pop.assert_not_called()
        self.assertTrue(job['缓存'])
        for i in range(ins.KEEP + 2):
            g = cache / f'{i}.apk'
            g.write_bytes(b'1')
            os.utime(g, (i, i))
        with mock.patch.object(ins, 'CACHE_DIR', cache):
            ins._prune()
        self.assertEqual(len(list(cache.glob('*.apk'))), ins.KEEP)
        self.assertFalse((cache / '0.apk').exists())

    def test_adb_parse(self):
        self.assertEqual(ins.adb_failure('Failure [INSTALL_FAILED_VERSION_DOWNGRADE]'), '降级')
        self.assertEqual(ins.adb_failure('INSTALL_FAILED_UPDATE_INCOMPATIBLE: Package a.b signatures do not match'), '签名冲突')
        self.assertEqual(ins.adb_package('Package com.a.b signatures do not match the previously installed version'), 'com.a.b')
        self.assertEqual(ins.adb_failure('Failure [INSTALL_FAILED_INSUFFICIENT_STORAGE]'), '')


REAL_DL = ins._download

if __name__ == '__main__':
    unittest.main()
