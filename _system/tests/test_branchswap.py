"""换分支：老分支进分支历史、新分支成当前分支；Bug 修复排队改到当前分支；构建任务追加、老分支标历史；管家提示。

    cd _system && .venv/bin/python -m unittest tests.test_branchswap -v     （没有 .venv 用 python3）

用临时 KB_ROOT、临时 HOME 和本地裸仓库当远端，不碰真实数据。
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix='kb-bs-test-'))
os.environ['KB_ROOT'] = str(TMP / 'kb')
os.environ['HOME'] = str(TMP / 'home')
(TMP / 'kb').mkdir()
(TMP / 'home').mkdir()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import kb  # noqa: E402
from kblib.ai import branchswap as bs  # noqa: E402
from kblib.plugins import builds  # noqa: E402


def git(root, *a):
    r = subprocess.run(['git', '-C', str(root), *a], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


class BranchSwap(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        kb.init()
        cls.remote = TMP / 'remote.git'
        subprocess.run(['git', 'init', '-q', '--bare', str(cls.remote)], check=True)
        cls.repo = TMP / 'app'
        subprocess.run(['git', 'init', '-q', '-b', 'main', str(cls.repo)], check=True)
        git(cls.repo, 'config', 'user.email', 't@t')
        git(cls.repo, 'config', 'user.name', 't')
        (cls.repo / 'a.txt').write_text('1')
        git(cls.repo, 'add', '.')
        git(cls.repo, 'commit', '-qm', 'init')
        git(cls.repo, 'remote', 'add', 'origin', str(cls.remote))
        git(cls.repo, 'switch', '-qc', 'feature-x-v1')
        git(cls.repo, 'push', '-q', '-u', 'origin', 'main', 'feature-x-v1')
        kb.create_period('换分支测试')
        d = kb.period_dir('换分支测试')
        m = kb.load_meta(d)
        m.update(开发仓库=str(cls.repo), 开发分支='feature-x-v1', 开发会话={'分支': 'feature-x-v1', '状态': '已结束'},
                 构建任务=[{'名称': 'app_feature-x-v1', '地址': 'http://j/job/app_feature-x-v1/', '依据': '自动', '分支': ['feature-x-v1']}])
        kb.save_meta(d, m)
        cls.d = d

    def test_1_refuse_unpushed(self):
        (self.repo / 'a.txt').write_text('2')
        git(self.repo, 'commit', '-qam', 'local only')
        with self.assertRaisesRegex(kb.KBError, '还没推到'):
            bs.branch_swap('换分支测试', 'feature-x-v2')
        git(self.repo, 'push', '-q')

    def test_2_swap(self):
        (self.repo / 'a.txt').write_text('dirty')                 # 未提交改动不再挡住：自动 stash 并记待处理
        msg = bs.branch_swap('换分支测试', 'feature-x-v2', reason='已合入 release-1')
        self.assertIn('feature-x-v2', msg)
        self.assertIn('git stash', msg)
        self.assertEqual(git(self.repo, 'branch', '--show-current'), 'feature-x-v2')
        self.assertIn('原分支 feature-x-v1', git(self.repo, 'stash', 'list'))
        m = kb.load_meta(self.d)
        q = [x for x in m.get('非阻断问题') or [] if '收起了' in x['标题']]
        self.assertEqual(len(q), 1)
        self.assertIn('git stash apply', q[0]['详情'])
        self.assertEqual(m['开发分支'], 'feature-x-v2')
        self.assertEqual(m['开发会话']['分支'], 'feature-x-v2')
        self.assertEqual([x['分支'] for x in m['分支历史']], ['feature-x-v1'])
        self.assertEqual(m['分支历史'][0]['原因'], '已合入 release-1')
        self.assertIn('换分支', (self.d / '记录.md').read_text())
        # Bug 归期两个分支都认；当前分支排最前
        wb = kb.want_branches(self.d, m)
        self.assertEqual(wb[:2], ['feature-x-v2', 'feature-x-v1'])
        # 修复：老分支改到当前分支；别的分支不动
        e = {'归属': {'类型': '期', '名称': '换分支测试'}}
        self.assertEqual(bs.bug_branch(e, 'feature-x-v1'), 'feature-x-v2')
        self.assertEqual(bs.bug_branch(e, 'other'), 'other')
        # 构建：分支里新老都有；老任务标历史
        self.assertEqual(builds.period_branches(self.d, m)[:2], ['feature-x-v2', 'feature-x-v1'])
        js = builds._mark(m, list(m['构建任务']))
        self.assertTrue(js[0]['历史'])

    def test_3_build_append(self):
        calls = []

        def fake(pid, args, by):
            calls.append(args)
            return [{'名称': f'app_{args[1]}', '地址': f'http://j/job/app_{args[1]}/', '分': 99}]
        old_p, old_j = builds.build_plugin, builds._plugin_json
        builds.build_plugin = lambda required=True: {'id': 'jk', '名称': 'Jenkins'}
        builds._plugin_json = fake
        try:
            js = builds.period_jobs('换分支测试')
            names = [j['名称'] for j in js]
            self.assertEqual(names, ['app_feature-x-v2', 'app_feature-x-v1'])      # 追加，当前分支在前，老的保留
            self.assertEqual([j['历史'] for j in js], [False, True])
            n = len(calls)
            builds.period_jobs('换分支测试')
            self.assertEqual(len(calls), n)                                          # 对过的分支不再重复匹配
            js = builds.period_jobs('换分支测试', rematch=True)
            self.assertEqual(sorted(j['名称'] for j in js), ['app_feature-x-v1', 'app_feature-x-v2'])
        finally:
            builds.build_plugin, builds._plugin_json = old_p, old_j

    def test_4_hint(self):
        m = kb.load_meta(self.d)
        m['状态'] = '已完成'
        m['状态历史'] = [{'时间': '2000-01-01 00:00', '状态': '已完成'}]
        m['分支历史'][0]['止'] = '1999-01-01 00:00'
        hs, t0 = bs.swap_hint(m)
        self.assertEqual(hs[0][1], 'feature-x-v2')
        m['分支历史'][0]['止'] = '2001-01-01 00:00'                                 # 完成之后换过了：不再提示
        self.assertEqual(bs.swap_hint(m)[0], [])


if __name__ == '__main__':
    unittest.main()
