"""工作副本池：按克隆地址登记、第 1 份克隆、占用时另拉一份（worktree）、上限排队、切到开发分支、闲置清理（未推送的不删）、
Gerrit 识别、各份副本合成一个仓库（工程设置共用）。

    cd _system && .venv/bin/python -m unittest tests.test_pool -v     （没有 .venv 用 python3）

用临时 KB_ROOT、临时 HOME 和本地裸仓库（file://）当远端，不碰真实数据。
"""
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

TMP = Path(tempfile.mkdtemp(prefix='kb-pool-test-'))
os.environ['KB_ROOT'] = str(TMP / 'kb')
os.environ['HOME'] = str(TMP / 'home')
(TMP / 'kb').mkdir()
(TMP / 'home').mkdir()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import kb  # noqa: E402
from kblib.infra import pool  # noqa: E402
from kblib.core import repos  # noqa: E402


def git(root, *a):
    r = subprocess.run(['git', '-C', str(root), *a], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


class Pool(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        kb.init()
        cls.remote = TMP / 'remote.git'
        subprocess.run(['git', 'init', '-q', '--bare', '-b', 'main', str(cls.remote)], check=True)
        src = TMP / 'src'
        subprocess.run(['git', 'init', '-q', '-b', 'main', str(src)], check=True)
        for k, v in (('user.email', 't@t'), ('user.name', 't')):
            git(src, 'config', k, v)
        (src / 'a.txt').write_text('a')
        git(src, 'add', '.')
        git(src, 'commit', '-qm', 'init')
        git(src, 'branch', 'feature/x')
        git(src, 'push', '-q', str(cls.remote), 'main', 'feature/x')
        cls.url = 'file://' + str(cls.remote)
        cls.msg = repos.add_repo(cls.url, 'app', post='touch <dir>/.git/post-ran')

    def setUp(self):
        self.busy = set()
        p = mock.patch.object(pool, 'pool_busy', lambda path, me='': '占着' if str(Path(path).resolve()) in self.busy else '')
        p.start()
        self.addCleanup(p.stop)

    def test_1_register_and_first_clone(self):
        self.assertIn('已登记远程仓库：app', self.msg)
        e = repos.load_repos()['app']
        self.assertEqual(e['克隆地址'], self.url)
        self.assertNotIn('状态', e)
        c1 = pool.POOL / 'app' / '1'
        self.assertTrue((c1 / '.git').exists())
        self.assertTrue((c1 / '.git' / 'post-ran').exists(), '克隆后命令要跑')
        self.assertEqual(pool.copy_of(c1), ('app', 1))
        self.assertEqual(pool.canonical(str(c1)), str(c1))

    def test_2_claim_free_then_new_worktree(self):
        c1 = pool.POOL / 'app' / '1'
        got = pool.pool_claim('app', '期A', 'feature/x')
        self.assertEqual(Path(got), c1)
        self.busy.add(str(c1.resolve()))
        got2 = pool.pool_claim(str(c1), '期B', 'main')
        self.assertEqual(Path(got2), pool.POOL / 'app' / '2')
        self.assertTrue((Path(got2) / '.git').is_file(), '默认用 git worktree')
        self.assertEqual(git(got2, 'branch', '--show-current'), 'main')
        self.assertEqual(repos._repo_key(got2), str(c1), '各份副本按第 1 份记工程设置')
        self.assertEqual(pool.copy_label(got2), 'app 第 2 份（工作区/app/2）')

    def test_3_limit_queue(self):
        with mock.patch.object(pool, 'pool_settings', lambda: dict(pool.DEFAULTS, 上限=2)):
            for p in pool.copies('app'):
                self.busy.add(str(p.resolve()))
            with self.assertRaises(kb.KBError) as cm:
                pool.pool_claim('app', '期C')
            self.assertIn('排队', str(cm.exception))

    def test_4_dirty_copy_of_other_skipped(self):
        c2 = pool.POOL / 'app' / '2'
        (c2 / 'new.txt').write_text('x')                    # 期B 留下的改动
        got = pool.pool_claim('app', '期D')
        self.assertEqual(Path(got), pool.POOL / 'app' / '1')
        self.busy.add(str((pool.POOL / 'app' / '1').resolve()))
        got = pool.pool_claim('app', '期D')
        self.assertEqual(Path(got), pool.POOL / 'app' / '3', '别人留下改动的副本不分给别人')
        self.assertEqual(Path(pool.pool_claim(str(c2), '期B')), c2, '自己上次的副本（有改动）接着用')
        (c2 / 'new.txt').unlink()

    def test_5_cleanup(self):
        c3 = pool.POOL / 'app' / '3'
        git(c3, 'config', 'user.email', 't@t')
        git(c3, 'config', 'user.name', 't')
        git(c3, 'switch', '-q', '-c', 'tmp')
        (c3 / 'b.txt').write_text('b')
        git(c3, 'add', '.')
        git(c3, 'commit', '-qm', 'local only')
        st = pool._state()
        for p in pool.copies('app'):
            st.setdefault(str(p.resolve()), {})['最后使用'] = time.time() - 30 * 86400
        pool._save_state(st)
        r = pool.pool_cleanup()
        self.assertTrue(any('第 2 份' in x for x in r['删除']), r)
        self.assertFalse((pool.POOL / 'app' / '2').exists())
        self.assertTrue(c3.exists(), '有未推送提交的不删')
        self.assertTrue(any('第 3 份' in x and '没推送' in x for x in r['提醒']), r)
        self.assertTrue((pool.POOL / 'app' / '1').exists(), '第 1 份永远留着')

    def test_6_url_detection(self):
        for u in ('ssh://u@h:29418/a/b', 'git@github.com:me/x.git', 'https://h/x.git', 'u@h:group/x'):
            self.assertTrue(pool.is_clone_url(u), u)
        for u in ('/Users/me/app', '~/app', 'u@h:/home/me/app', ''):
            self.assertFalse(pool.is_clone_url(u), u)
        self.assertTrue(pool.is_gerrit('ssh://u@h:29418/a'))
        self.assertTrue(pool.is_gerrit('https://gerrit.corp/a/b'))
        self.assertFalse(pool.is_gerrit('git@github.com:me/x.git'))
        self.assertEqual(pool.gerrit_hook_cmd('ssh://u@h:29418/a'),
                         'scp -O -P 29418 u@h:hooks/commit-msg <dir>/.git/hooks/ && chmod +x <dir>/.git/hooks/commit-msg')

    def test_7_local_path_untouched(self):
        self.assertEqual(pool.pool_claim('/some/local/repo', '期E'), '/some/local/repo')


class Lines(unittest.TestCase):
    """一个仓库多个 master（马甲包）：认出分支属于哪条线、按线挑模块、新副本从那条线起步。"""
    def test_base_line_and_module(self):
        from kblib.plugins import devrun
        r = TMP / 'lines'
        subprocess.run(['git', 'init', '-q', '-b', 'master', str(r)], check=True)
        for k, v in (('user.email', 't@t'), ('user.name', 't')):
            git(r, 'config', k, v)
        (r / 'a').write_text('0')
        git(r, 'add', '.')
        git(r, 'commit', '-qm', 'base')
        for line in ('alpha-master', 'beta-master'):
            git(r, 'switch', '-q', '-c', line, 'master')
            for i in range(3):
                (r / line).write_text(str(i))
                git(r, 'add', '.')
                git(r, 'commit', '-qm', f'{line} {i}')
        git(r, 'switch', '-q', '-c', 'feature-w-main-tab-x', 'beta-master')
        (r / 'f').write_text('f')
        git(r, 'add', '.')
        git(r, 'commit', '-qm', 'feat')
        for b in ('master', 'alpha-master', 'beta-master', 'feature-w-main-tab-x'):
            git(r, 'update-ref', f'refs/remotes/origin/{b}', b)
        self.assertEqual(pool.branch_base_line(r, 'feature-w-main-tab-x'), 'beta-master', '-main- 在中间的不算 master')
        self.assertEqual(pool.branch_base_line(r, 'alpha-master'), 'alpha-master')
        self.assertEqual(pool.branch_base_line(r, 'nope'), '')
        cands = [{'模块': ':app-alpha', '目录': 'app/alpha'}, {'模块': ':app-beta', '目录': 'app/beta'}, {'模块': ':app', '目录': 'app'}]
        g = devrun.guess_module(cands, 'feature-w-main-tab-x', 'beta-master')
        self.assertEqual(g['模块'], ':app-beta')
        self.assertEqual(devrun.guess_module(cands, 'feature-alpha-x', '')['模块'], ':app-alpha')
        self.assertIsNone(devrun.guess_module(cands, 'master', 'master'))


class PoolChoose(unittest.TestCase):       # 名字排在 Pool 之后：要用 Pool 登记的仓库
    """远程仓库在基础信息里按模板登记，各处下拉选条目，开工时从池里分配。"""
    @classmethod
    def setUpClass(cls):
        from kblib.core import baseinfo
        cls.bi = baseinfo
        r = baseinfo.base_save(category='远程仓库', name='示例工程 alpha', scope='alpha',
                               fields=[{'名': '克隆地址', '值': Pool.url}, {'名': '基线分支', '值': 'feature/x'},
                                       {'名': '平台类型', '值': 'Android'}, {'名': '运行模块', '值': ':app-alpha'}])
        cls.bid = r.split('\n')[1].split()[1]

    def test_template_required(self):
        with self.assertRaises(kb.KBError) as cm:
            self.bi.base_save(category='远程仓库', name='缺基线', fields=[{'名': '克隆地址', '值': Pool.url}])
        self.assertIn('基线分支', str(cm.exception))
        with self.assertRaises(kb.KBError):
            self.bi.base_save(category='远程仓库', name='坏地址', fields=[{'名': '克隆地址', '值': '/tmp/x'}, {'名': '基线分支', '值': 'm'}])
        self.assertIn('远程仓库', self.bi.BASE_TEMPLATES)

    def test_pickers_list_entry(self):
        from kblib.ai import devprep
        from kblib.anr import manual
        from kblib.stack import plan
        tok = pool.REMOTE + self.bid
        kb.create_period('选仓库测试')
        d = kb.period_dir('选仓库测试')
        cs = devprep.repo_candidates(d, kb.load_meta(d))
        x = next(c for c in cs if c['路径'] == tok)
        self.assertIn('示例工程 alpha', x['远程仓库'])
        self.assertTrue(x['只供选择'])
        self.assertIsNone(devprep._pick_repo(cs, kb.load_meta(d)))
        self.assertTrue(any(c['路径'] == tok for c in manual.repo_choices()))
        self.assertFalse(any(pool.copy_of(c['路径']) for c in manual.repo_choices()), '池里的副本不单列')
        self.assertTrue(any(c['路径'] == tok for c in plan.stack_repos()))

    def test_claim_entry_new_branch_from_base(self):
        tok = pool.REMOTE + self.bid
        for p in pool.copies('app'):
            pool.pool_release(p)
        with mock.patch.object(pool, 'pool_busy', lambda path, me='': ''):
            path, e = pool.remote_claim(tok, '期Z', 'kb-new-feature')
        self.assertEqual(e['编号'], self.bid)
        self.assertEqual(pool.copy_of(path)[0], 'app', '同一个克隆地址共用已登记的池')
        self.assertEqual(git(path, 'branch', '--show-current'), 'kb-new-feature', '开发分支不存在时从基线分支拉')
        self.assertEqual(git(path, 'rev-parse', 'HEAD'), git(path, 'rev-parse', 'origin/feature/x'))
        git(path, 'switch', '-q', '--detach')

    def test_task_pick(self):
        from kblib.anr import manual
        kb.create_period('事项选仓库')
        with mock.patch.object(pool, 'pool_busy', lambda path, me='': ''):
            out = manual._setup_task('事项选仓库', '', pool.REMOTE + self.bid, '测试')
        m = kb.load_meta(kb.period_dir('事项选仓库'))
        self.assertEqual(m['远程仓库'], self.bid)
        self.assertTrue(pool.copy_of(m['开发仓库']))
        self.assertIn('示例工程 alpha', out[0])


class RealBusy(unittest.TestCase):
    def test_real_busy_and_lease(self):
        c1 = pool.POOL / 'app' / '1'
        for p in pool.copies('app'):
            pool.pool_release(p)
        self.assertEqual(Path(pool.pool_claim(str(c1), '期D', switch=False)), c1)
        self.assertIn('刚分配给', pool.pool_busy(c1, 'x'))          # 分配后会话起来前算预留
        self.assertEqual(pool.pool_busy(c1, '期D'), '')            # 预留给自己的不算
        pool.pool_release(c1)
        self.assertEqual(pool.pool_busy(c1, 'x'), '')              # 真的占用判断（开发 / 修复 / 全栈各端）能跑
        with pool.pool_lease(c1, '插件「测试」'):
            self.assertIn('插件「测试」', pool.pool_busy(c1, 'x'))
        self.assertEqual(pool.pool_busy(c1, 'x'), '')
        st = pool.pool_status()
        self.assertEqual(st['仓库'][0]['名称'], 'app')

    def test_free_text_platform(self):
        """基础信息里平台类型手填成自由文本（如「其他（Python + Web）」）：能认出的归一，认不出留空，不拖垮整个仓库下拉。"""
        from kblib.stack import plan
        self.assertEqual(plan.end_guess('其他（Python + Web）'), ['Web'])
        self.assertEqual(plan.end_guess('android 客户端'), ['Android'])
        self.assertEqual(plan.end_guess('Unity'), ['Unity'])
        self.assertEqual(plan.end_guess('乱 七 八 糟'), [])
        self.assertEqual(plan.end_guess(''), [])
        fake = [{'编号': 'B901', '名称': 'x', '克隆地址': 'file:///nope.git', '基线分支': 'main', '平台类型': '其他（a b c）'},
                {'编号': 'B902', '名称': 'y', '克隆地址': 'file:///nope2.git', '基线分支': 'main', '平台类型': 'Android 客户端'}]
        with mock.patch.object(pool, 'remote_entries', lambda: fake):
            rows = {r.get('远程仓库'): r for r in plan.stack_repos() if r.get('远程仓库')}
        self.assertEqual(rows['B901']['端'], [])
        self.assertEqual(rows['B902']['端'], ['Android'])


if __name__ == '__main__':
    unittest.main()


class PoolZAddRemove(unittest.TestCase):    # 排在最后：自己建远端，不碰 Pool 的仓库
    """kb_add_repo 按克隆地址登记：成功建基础信息「远程仓库」条目；失败不留登记；remove_repo 能移除。"""
    def test_clone_fail_rolls_back(self):
        bad = 'file://' + str(TMP / 'no-such.git')
        with self.assertRaises(kb.KBError) as cm:
            repos.add_repo(bad, 'broken')
        self.assertIn('没有登记', str(cm.exception))
        self.assertNotIn('broken', repos.load_repos())
        self.assertFalse((pool.POOL / 'broken').exists(), '工作区/<名> 不留空目录')
        self.assertFalse(any(e['克隆地址'] == bad for e in pool.remote_entries()))

    def test_clone_fail_background_rolls_back(self):
        bad = 'file://' + str(TMP / 'no-such-bg.git')
        import threading
        ts = []
        real = threading.Thread
        with mock.patch.object(threading, 'Thread', lambda *a, **k: ts.append(real(*a, **k)) or ts[-1]):
            repos.add_repo(bad, 'broken-bg', background=True)
        for t in ts:
            t.join(60)
        self.assertNotIn('broken-bg', repos.load_repos())

    def test_success_creates_entry_then_remove(self):
        from kblib.stack import plan
        remote = TMP / 'second.git'
        subprocess.run(['git', 'init', '-q', '--bare', '-b', 'dev-master', str(remote)], check=True)
        src = TMP / 'src2'
        subprocess.run(['git', 'init', '-q', '-b', 'dev-master', str(src)], check=True)
        for k, v in (('user.email', 't@t'), ('user.name', 't')):
            git(src, 'config', k, v)
        (src / 'b.txt').write_text('b')
        git(src, 'add', '.')
        git(src, 'commit', '-qm', 'init')
        git(src, 'push', '-q', str(remote), 'dev-master')
        url = 'file://' + str(remote)
        msg = repos.add_repo(url, 'second')
        self.assertIn('远程仓库」条目 second', msg)
        e = next(x for x in pool.remote_entries() if x['克隆地址'] == url)
        self.assertEqual(e['基线分支'], 'dev-master')
        self.assertEqual(e['仓库'], 'second')
        row = next(r for r in plan.repo_settings() if r.get('远程仓库') == 'second')
        self.assertTrue(row['有条目'])
        self.assertNotIn('远程仓库」条目', repos.add_repo(url, 'second'), '已有条目不再建')
        self.assertEqual(sum(1 for x in pool.remote_entries() if x['克隆地址'] == url), 1, '再登记不重复建条目')
        with mock.patch.object(pool, 'pool_busy', lambda path, me='': ''):
            (pool.POOL / 'second' / '1' / 'dirty.txt').write_text('x')
            with self.assertRaises(kb.KBError):
                repos.remove_repo('second')
            (pool.POOL / 'second' / '1' / 'dirty.txt').unlink()
            out = repos.remove_repo('second')
        self.assertIn('已移除远程工程「second」', out)
        self.assertIn('条目还在', out)
        self.assertNotIn('second', repos.load_repos())
        self.assertFalse((pool.POOL / 'second').exists())
        self.assertFalse(any(r.get('远程仓库') == 'second' for r in plan.repo_settings()))
        with self.assertRaises(kb.KBError):
            repos.remove_repo('nope')
