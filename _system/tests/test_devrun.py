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


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for f, n in ((kb.create_task, P), (kb.create_period, OTHER)):
            try:
                f(n)
            except KBError:
                pass                                # 别的测试类已经建过

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
        return Base.real_popen(cmd, *a, **k)

    def start(self, root, **kw):
        jid = kb.devrun_start(P, str(root), by='单测', **kw)
        dr.devrun_worker(jid)
        return kb.devrun_job(jid)


class RunTest(Base):

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



def git(root, *a):
    import subprocess
    return subprocess.run(['git', '-C', str(root), *a], capture_output=True, text=True, check=True).stdout.strip()


def git_project(name):
    """带 git 的 gradle 工程：main 上一次提交，另有本期分支 feature-x（本地已建好），停在 main。"""
    g = gradle_project(TMP / name, OK_GRADLE)
    (g / '.gitignore').write_text('serial.txt\nargs.txt\nlocal.properties\n', 'utf-8')
    (g / 'local.properties').write_text('sdk.dir=/x\n', 'utf-8')
    git(g.parent, 'init', '-q', '-b', 'main', str(g))
    git(g, 'config', 'user.email', 't@t')
    git(g, 'config', 'user.name', 't')
    git(g, 'add', '.')
    git(g, 'commit', '-qm', 'init')
    git(g, 'branch', 'feature-x')
    return g


class BranchTest(Base):
    """运行到手机先切到本期分支：切过去就留在那；未提交改动 stash 并记待处理；分支在别的 worktree 里就用那份；占用时不切。"""
    def setUp(self):
        super().setUp()
        d = kb.period_dir(P)
        m = kb.load_meta(d)
        m['开发分支'] = 'feature-x'
        m.pop('非阻断问题', None)
        kb.save_meta(d, m)

    def test_switch_stash_and_stay(self):
        g = git_project('br-switch')
        (g / 'app' / 'build.gradle').write_text((g / 'app' / 'build.gradle').read_text() + '// dirty\n', 'utf-8')
        o = kb.devrun_options(P, str(g))
        r = next(x for x in o['工程'] if x['路径'] == str(g))
        self.assertEqual(r['切分支']['动作'], '切换')
        self.assertIn('将切到 feature-x 分支', r['切分支']['说明'])
        self.assertIn('git stash', r['切分支']['说明'])
        j = self.start(g)
        self.assertEqual(j['状态'], '成功', j)
        self.assertEqual(git(g, 'branch', '--show-current'), 'feature-x')          # 编完不切回 main
        self.assertEqual(j['分支'], 'feature-x')
        self.assertIn('原分支 main', git(g, 'stash', 'list'))
        self.assertEqual(git(g, 'status', '--porcelain', '-uno'), '')
        q = [x for x in kb.load_meta(kb.period_dir(P)).get('非阻断问题') or [] if '收起了' in x['标题']]
        self.assertEqual(len(q), 1)
        self.assertIn(j['收起']['提交'], q[0]['详情'])
        self.assertIn('原分支：main', q[0]['详情'])
        self.assertIn('文件数：1', q[0]['详情'])
        self.assertIn('已切到 feature-x', (kb.period_dir(P) / '记录.md').read_text('utf-8'))

    def test_branch_in_other_worktree(self):
        g = git_project('br-wt').resolve()
        wt = (TMP / 'br-wt-copy').resolve()
        git(g, 'worktree', 'add', '-q', str(wt), 'feature-x')
        o = kb.devrun_options(P, str(g))
        sw = next(x for x in o['工程'] if x['路径'] == str(g))['切分支']
        self.assertEqual((sw['动作'], sw['路径']), ('副本', str(wt)))
        j = self.start(g)
        self.assertEqual(j['状态'], '成功', j)
        self.assertEqual(j['工程'], str(wt))
        self.assertEqual(j['分支'], 'feature-x')
        self.assertTrue((wt / 'serial.txt').is_file())                             # 在 worktree 里编译
        self.assertTrue((wt / 'local.properties').is_file())                       # 被忽略的本机配置拷过去
        self.assertEqual(git(g, 'branch', '--show-current'), 'main')               # 主工作区不动

    def test_busy_no_switch(self):
        g = git_project('br-busy')
        with mock.patch.object(dr._pool, 'pool_busy', lambda p, me='': '「别的期」的开发会话正在这个仓库里运行'):
            sw = dr.plan_branch(g, 'feature-x')
            self.assertEqual(sw['动作'], '不切')
            self.assertIn('照旧用当前工作区', sw['说明'])
            j = self.start(g)
        self.assertEqual(j['状态'], '成功', j)
        self.assertEqual(git(g, 'branch', '--show-current'), 'main')

    def test_missing_branch_no_switch(self):
        g = git_project('br-miss')
        sw = dr.plan_branch(g, 'feature-none')
        self.assertEqual(sw['动作'], '不切')
        self.assertIn('都没有分支', sw['说明'])
        self.assertEqual(dr.plan_branch(g, 'main')['动作'], '')

    def test_plugin_run_stays_and_stashes(self):
        """插件「运行一次」：有未提交改动自动 stash，运行完留在目标分支，不切回原分支。"""
        from kblib.plugins import run as pr
        g = git_project('br-plugin')
        (g / 'app' / 'build.gradle').write_text('// dirty\n', 'utf-8')
        j = {'步骤': []}
        with mock.patch.object(pr, 'run_plugins', lambda *a, **k: '完成'):
            out = pr._run_locked(j, {'名称': '测试插件', 'id': 'x'}, kb.period_dir(P), g, 'main', 'feature-x', '', True)
        self.assertIn('没有改动', out)
        self.assertEqual(git(g, 'branch', '--show-current'), 'feature-x')
        self.assertIn('原分支 main', git(g, 'stash', 'list'))
        self.assertTrue(any('不切回原来的 main' in t for _, t in j['步骤']))


REMOTE_SH = r"""#!/bin/zsh
# 远程构建脚本
# 用法: ./remote_build.sh [flavor] [buildType]
FLAVOR="${1:-demo}"
case "$2" in
    debug) T=Debug ;;
    release) T=Release ;;
esac
case "$FLAVOR" in
    demo)
        GRADLE_TASK=":app:assemble${T}"
        ;;
    *)
        exit 1
        ;;
esac
LOCAL_PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_FILE="$LOCAL_PROJECT_DIR/.idea/.sync/service/config.json"
[ -f "$CONFIG_FILE" ] || { echo "❌ 配置文件不存在"; exit 1; }
echo "sshpass rsync ssh ./gradlew $GRADLE_TASK"
echo "$@" > remote_args.txt
adb devices > devs.txt
mkdir -p app/build/outputs/apk/debug && echo apk > app/build/outputs/apk/debug/app-debug.apk
echo "<!-- regenerated -->" >> app/build.gradle
echo "BUILD SUCCESSFUL in 3s"
echo "✅ [步骤3] 远程构建成功"
echo "📱 [步骤6] 安装APK到手机..."
echo "✅ [步骤6] APK安装成功"
"""


class RemoteBuildTest(Base):
    """远程编译：识别工程里的 remote_build.sh、从开发会话学编译命令、运行到手机走远程脚本。"""
    def setUp(self):
        super().setUp()
        d = kb.period_dir(P)
        m = kb.load_meta(d)
        m['开发分支'] = 'feature-x'
        kb.save_meta(d, m)

    def remote_project(self, name):
        g = git_project(name)
        (g / 'remote_build.sh').write_text(REMOTE_SH, 'utf-8')
        (g / 'CLAUDE.md').write_text('**推荐构建方式**: 直接使用远程构建脚本 ./remote_build.sh demo\n', 'utf-8')
        with open(g / '.gitignore', 'a') as f:
            f.write('.idea/\nbuild/\nremote_args.txt\ndevs.txt\n')
        git(g, 'add', '.')
        git(g, 'commit', '-qm', 'remote build')
        git(g, 'branch', '-f', 'feature-x')
        c = g / '.idea' / '.sync' / 'service'
        c.mkdir(parents=True)
        (c / 'config.json').write_text('{"remoteServers": []}', 'utf-8')
        return g.resolve()

    def test_detect_and_plan(self):
        from kblib.plugins import buildlearn as bl
        g = self.remote_project('rb-detect')
        det = bl.detect_remote(g)
        self.assertEqual(det['脚本'], 'remote_build.sh')
        self.assertEqual(det['映射'], {'demo': ':app'})
        self.assertEqual(det['构建类型'], ['debug', 'release'])
        self.assertEqual(det['依赖目录'], ['.idea/.sync'])
        self.assertTrue(det['配置在'])
        self.assertTrue(any('CLAUDE.md' in x for x in det['依据']))
        p = bl.build_plan(g, ':app', 'debug')
        self.assertEqual((p['方式'], p['命令']), ('远程', ['./remote_build.sh', 'demo', 'debug']))
        self.assertEqual(bl.build_plan(g, ':app', 'release')['命令'][-1], 'release')
        self.assertEqual(bl.build_plan(g, ':app', 'debug', mode='本地')['方式'], '本地')
        self.assertEqual(bl.build_plan(g, ':other', 'debug')['方式'], '本地')            # 对不上 flavor
        self.assertIsNone(bl.detect_remote(gradle_project(TMP / 'rb-none', OK_GRADLE)))

    def test_build_commands(self):
        from kblib.plugins import buildlearn as bl
        self.assertEqual(bl.build_commands('cd /x && ./remote_build.sh alpha < /dev/null > /tmp/a 2>&1'), ['./remote_build.sh alpha'])
        self.assertEqual(bl.build_commands('REMOTE_JDK_HOME=/opt/j /x/y/remote_build.sh w --no-install'), ['./remote_build.sh w --no-install'])
        self.assertEqual(bl.build_commands('./gradlew :m:compileDebugJavaWithJavac --offline -q'), ['./gradlew :m:compileDebugJavaWithJavac'])
        self.assertEqual(bl.build_commands('cat remote_build.sh; sed -n 1p remote_build.sh'), [])
        self.assertEqual(bl.build_commands('python3 -c "x=\'cd /a; ./remote_build.sh demo\'"'), [])

    def test_learn_from_sessions(self):
        import json
        from kblib.plugins import buildlearn as bl
        g = self.remote_project('rb-learn')
        sd = kb.period_dir(P) / '开发会话'
        sd.mkdir(exist_ok=True)
        use = lambda c: json.dumps({'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'name': 'Bash', 'input': {'command': c}}]}})
        lines = [json.dumps({'type': 'system', 'subtype': 'init', 'cwd': '/elsewhere'}),
                 use(f'cd {g} && ./remote_build.sh demo < /dev/null > /tmp/b.out 2>&1'),
                 use(f'cd {g} && ./remote_build.sh demo'),
                 use(f'cat {g}/remote_build.sh'),
                 use('cd /other/repo && ./gradlew :app:assembleDebug'),                 # 别的工程不算
                 json.dumps({'type': 'user', 'message': {'content': [{'type': 'tool_result', 'content': f'cd {g} && ./remote_build.sh x'}]}})]
        f = sd / 'learn-1.jsonl'
        f.write_text('\n'.join(lines) + '\n', 'utf-8')
        st = bl.learn(g, 30)
        self.assertEqual([(c['命令'], c['次数']) for c in st['命令']], [('./remote_build.sh demo', 2)])
        self.assertEqual(st['命令'][0]['期'], [P])
        with open(f, 'a') as fh:                                                      # 增量：只读新增的
            fh.write(use(f'cd {g} && ./gradlew :app:installDebug') + '\n')
        st = bl.learn(g, 30)
        self.assertEqual({c['命令']: c['次数'] for c in st['命令']}, {'./remote_build.sh demo': 2, './gradlew :app:installDebug': 1})
        self.assertEqual((st['远程次数'], st['本地次数']), (2, 1))
        p = bl.build_plan(g, ':app', 'debug')
        self.assertTrue(any('远程编译用过 2 次' in x for x in p['依据']))
        f.unlink()

    def test_run_remote_in_worktree(self):
        g = self.remote_project('rb-run')
        wt = (TMP / 'rb-run-wt').resolve()
        git(g, 'worktree', 'add', '-q', str(wt), 'feature-x')
        o = kb.devrun_options(P, str(g))
        r = next(x for x in o['工程'] if x['路径'] == str(g))
        self.assertEqual(r['切分支']['动作'], '副本')
        self.assertEqual(r['编译']['方式'], '远程')
        j = self.start(g)
        self.assertEqual(j['状态'], '成功', j)
        self.assertEqual(j['工程'], str(wt))
        self.assertEqual(j['编译']['命令'], ['./remote_build.sh', 'demo', 'debug'])
        self.assertTrue((wt / '.idea' / '.sync' / 'service' / 'config.json').is_file())   # 远程配置从主工作区拷过来
        self.assertEqual((wt / 'remote_args.txt').read_text().strip(), 'demo debug')
        self.assertEqual((wt / 'devs.txt').read_text().split(), ['List', 'of', 'devices', 'attached', 'A', 'device'])  # 只列选中的那台
        self.assertEqual(git(wt, 'status', '--porcelain', '-uno'), '')                    # 编译顺带改的 build.gradle 已还原
        self.assertEqual(j['还原'], ['app/build.gradle'])
        self.assertIn('远程编译 ./remote_build.sh demo debug', dr.devrun_text(j))
        self.assertEqual(self.launched, ['A'])
        self.assertFalse((TMP / 'cache' / f'adbshim-{j["编号"]}').exists())

if __name__ == '__main__':
    unittest.main()
