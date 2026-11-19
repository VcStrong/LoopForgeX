"""运行到手机（事项「支持把工程运行到手机上」）的单测。

    cd _system && python3 -m unittest tests.test_devrun -v

用临时 KB_ROOT、假的手机列表、假的 gradlew（shell 脚本），不碰真实手机；后台进程改成在本进程里直接跑 devrun_worker。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TMP = Path(tempfile.mkdtemp(prefix='kb-devrun-test-'))
os.environ['KB_ROOT'] = str(TMP / 'kb')
(TMP / 'kb').mkdir()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import kb  # noqa: E402
from kblib.core.common import KBError  # noqa: E402
from kblib.infra import devlock  # noqa: E402
from kblib.plugins import devrun as dr  # noqa: E402

P = '运行测试事项'
OTHER = '运行测试别的期'


def phones():
    hold = devlock.holders()
    out = []
    for sn in ('A', 'B'):
        c = hold.get(sn)
        out.append({'序列号': sn, '型号': 'M' + sn, '状态': '在线', '占用': dict(c, 最后使用=devlock.stamp(c['最后使用'])) if c else None,
                    '本期': bool(c and c.get('期') == P and c.get('方式') != '本人'), '空闲': not c, 'agent-device': None})
    return out


def gradle_project(root, gradlew_body, flavors=False):
    root.mkdir(parents=True, exist_ok=True)
    (root / 'settings.gradle').write_text("include ':app', ':lib'\n", 'utf-8')
    (root / 'gradlew').write_text('#!/bin/sh\n' + gradlew_body, 'utf-8')
    (root / 'gradlew').chmod(0o755)
    (root / 'app').mkdir(exist_ok=True)
    (root / 'app' / 'build.gradle').write_text("plugins { id 'com.android.application' }\nandroid { defaultConfig { applicationId \"com.x.demo\" }"
                                               + ('\n productFlavors { a {} }' if flavors else '') + '\n}\n', 'utf-8')
    (root / 'lib').mkdir(exist_ok=True)
    (root / 'lib' / 'build.gradle').write_text("plugins { id 'com.android.library' }\n// id 'com.android.application'\n", 'utf-8')
    return root


OK_GRADLE = 'echo "> Task :app:compileDebugKotlin"\necho "$ANDROID_SERIAL" > serial.txt\necho "$@" > args.txt\necho "Installing APK \'app-debug.apk\' on \'MA\'"\necho "Installed on 1 device."\n'
BAD_GRADLE = ('echo "> Task :app:compileDebugKotlin FAILED"\necho "e: file:///x/Main.kt:12:5 Unresolved reference: foo"\n'
              'echo "* What went wrong:"\necho "Execution failed for task \':app:compileDebugKotlin\'."\necho ""\necho "* Try:"\nexit 1\n')


class DetectTest(unittest.TestCase):
    def test_modules_and_platform(self):
        g = gradle_project(TMP / 'det', 'exit 0\n')
        self.assertEqual(dr.detect_platform(g), 'Android')
        self.assertEqual([m['模块'] for m in dr.android_modules(g)], [':app'])      # 注释里的 application 不算
        x = TMP / 'ios'
        (x / 'Demo.xcodeproj').mkdir(parents=True)
        self.assertEqual(dr.detect_platform(x), 'iOS')
        h = TMP / 'hm'
        h.mkdir()
        (h / 'hvigorfile.ts').write_text('', 'utf-8')
        self.assertEqual(dr.detect_platform(h), '鸿蒙')
        (TMP / 'empty').mkdir(exist_ok=True)
        self.assertEqual(dr.detect_platform(TMP / 'empty'), '')

    def test_flutter_android_subdir(self):
        g = gradle_project(TMP / 'flutter' / 'android', 'exit 0\n')
        self.assertEqual(dr.detect_platform(g.parent), 'Android')
        self.assertEqual(dr.android_modules(g.parent)[0]['模块'], ':app')

    def test_gradle_path(self):
        self.assertEqual(dr._gradle_path('app', ''), ':app')
        self.assertEqual(dr._gradle_path('a/b', "include ':a:b'"), ':a:b')
        self.assertEqual(dr._gradle_path('libs/foo', "include ':foo'\nproject(':foo').projectDir = file('libs/foo')"), ':foo')
        custom = "def includeModule(String dir, String prev) {}\ninclude ':feature'\nincludeModule('app', 'app')"
        self.assertEqual(dr._gradle_path('app/feature', custom), ':app-feature')     # 根目录另有 :feature，不能认成它

    def test_task_name(self):
        self.assertEqual(dr.gradle_install_task(':app', 'debug'), ':app:installDebug')
        self.assertEqual(dr.gradle_install_task(':app', 'prodDebug'), ':app:installProdDebug')

    def test_summary(self):
        s, c = dr.devrun_summary("FAILURE: Build failed with an exception.\n\n* What went wrong:\n"
                                 "Task 'installDebug' is ambiguous in project ':app'. Candidates are: 'installGpDebug', 'installProdDebug'.\n\n* Try:\n")
        self.assertEqual(c, ['gpDebug', 'prodDebug'])
        self.assertIn('要指定变体', s)
        s, _ = dr.devrun_summary('ERROR: JAVA_HOME is set to an invalid directory: /x\n')
        self.assertIn('JAVA_HOME', s)
        s, _ = dr.devrun_summary(BAD_GRADLE.replace('echo ', '').replace('"', '').replace('exit 1', ''))
        self.assertIn('Unresolved reference: foo', s)
        self.assertIn("Execution failed for task ':app:compileDebugKotlin'.", s)


class RunTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        kb.create_task(P)
        kb.create_period(OTHER)

    def setUp(self):
        devlock.FILE.write_text('{}', 'utf-8')
        devlock.RECENT.write_text('{}', 'utf-8')
        self.launched = []
        ps = [mock.patch.object(dr._dev, 'devices', lambda n='': phones()),
              mock.patch.object(dr._dev, '_adb', lambda: '/x/adb'),
              mock.patch.object(dr, 'DIR', TMP / 'cache'),
              mock.patch.object(dr, '_login_env', lambda: dict(os.environ)),
              mock.patch.object(dr, '_launch', lambda j, apk, text: (self.launched.append(j['序列号']) or True, '已拉起 com.x.demo')),
              mock.patch.object(dr.subprocess, 'Popen', self.fake_popen)]
        for p in ps:
            p.start()
            self.addCleanup(p.stop)

    real_popen = dr.subprocess.Popen

    def fake_popen(self, cmd, *a, **k):
        """后台进程（python -c … devrun_worker）不真开，记下来由测试在本进程里跑；gradlew 照常真跑。"""
        if cmd[0] == sys.executable:
            return mock.Mock(pid=os.getpid())
        return RunTest.real_popen(cmd, *a, **k)

    def start(self, root, **kw):
        jid = kb.devrun_start(P, str(root), by='单测', **kw)
        dr.devrun_worker(jid)
        return kb.devrun_job(jid)

    def test_success_claims_phone_and_logs(self):
        g = gradle_project(TMP / 'ok', OK_GRADLE)
        j = self.start(g)
        self.assertEqual(j['状态'], '成功', j)
        self.assertEqual(j['序列号'], 'A')
        self.assertEqual((g / 'serial.txt').read_text().strip(), 'A')          # 只装到选的那台
        self.assertEqual((g / 'args.txt').read_text().strip(), ':app:installDebug')
        self.assertEqual(self.launched, ['A'])
        self.assertEqual(devlock.holders()['A']['期'], P)
        log = (kb.period_dir(P) / '记录.md').read_text('utf-8')
        self.assertIn('运行到手机', log)
        self.assertIn('MA（A）', log)
        self.assertIn('运行到手机前', log)                                        # 时机记录

    def test_failure_summary_and_release(self):
        g = gradle_project(TMP / 'bad', BAD_GRADLE)
        j = self.start(g)
        self.assertEqual(j['状态'], '失败')
        self.assertIn('Unresolved reference: foo', j['摘要'])
        self.assertNotIn('A', devlock.holders())                                 # 这次新占的手机失败后释放

    def test_phone_held_by_other_period(self):
        g = gradle_project(TMP / 'held', OK_GRADLE)
        kb.device_claim(OTHER, serials=['A'], strict=True)
        with mock.patch.object(devlock, 'session_alive', lambda p: True):
            with self.assertRaises(KBError):
                kb.devrun_start(P, str(g), serial='A')                           # 点名别的期占着的：不装
            j = self.start(g)                                                    # 不点名：自动挑空闲的 B
        self.assertEqual(j['序列号'], 'B')
        self.assertEqual(j['状态'], '成功')

    def test_variant_and_remember(self):
        g = gradle_project(TMP / 'var', OK_GRADLE, flavors=True)
        j = self.start(g, variant='gpDebug', remember=True)
        self.assertEqual((g / 'args.txt').read_text().strip(), ':app:installGpDebug')
        self.assertEqual(kb.repo_pref(str(g)).get('运行变体'), 'gpDebug')
        self.assertEqual(j['状态'], '成功')
        j = self.start(g)                                                        # 记住的变体下次默认用
        self.assertEqual(j['变体'], 'gpDebug')

    def test_unsupported_platform(self):
        x = TMP / 'ios2'
        (x / 'Demo.xcworkspace').mkdir(parents=True)
        with self.assertRaisesRegex(KBError, 'iOS'):
            kb.devrun_start(P, str(x))

    def test_cancel(self):
        g = gradle_project(TMP / 'cancel', OK_GRADLE)
        jid = kb.devrun_start(P, str(g))
        with mock.patch.object(dr.os, 'killpg', lambda *a: None):
            self.assertEqual(kb.devrun_cancel(jid), '已取消')
        self.assertEqual(kb.devrun_job(jid)['状态'], '已取消')
        self.assertNotIn('A', devlock.holders())


if __name__ == '__main__':
    unittest.main()
