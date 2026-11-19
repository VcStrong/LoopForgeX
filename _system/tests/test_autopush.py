"""改完代码自动推送：开关默认开、总开关 / 本期关掉不推、Gerrit 走 refs/for、有冲突 / HEAD 游离不推；
工作区脏（另一会话没提交的改动）不挡推送，只有要 rebase 时才拒；失败后巡检 / 同仓库会话结束补推。

    cd _system && .venv/bin/python -m unittest tests.test_autopush -v     （没有 .venv 用 python3）

用临时 KB_ROOT、临时 HOME 和本地裸仓库当远端，不碰真实数据。
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix='kb-ap-test-'))
os.environ['KB_ROOT'] = str(TMP / 'kb')
os.environ['HOME'] = str(TMP / 'home')
(TMP / 'kb').mkdir()
(TMP / 'home').mkdir()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import kb  # noqa: E402
from kblib.infra import autopush as ap  # noqa: E402
from kblib.ai import devsession  # noqa: E402

N = [0]


def git(root, *a):
    r = subprocess.run(['git', '-C', str(root), *a], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def make_repo(name, remote_name=None):
    """新建本地仓库 + 裸远端（main 已推送）。→ (仓库, 远端)。"""
    remote = TMP / (remote_name or f'{name}.git')
    subprocess.run(['git', 'init', '-q', '--bare', '-b', 'main', str(remote)], check=True)
    repo = TMP / name
    subprocess.run(['git', 'init', '-q', '-b', 'main', str(repo)], check=True)
    git(repo, 'config', 'user.email', 't@t')
    git(repo, 'config', 'user.name', 't')
    (repo / 'a.txt').write_text('1\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'init')
    git(repo, 'remote', 'add', 'origin', str(remote))
    git(repo, 'push', '-q', '-u', 'origin', 'main')
    return repo, remote


def commit(repo, fname='a.txt', text=None):
    N[0] += 1
    (repo / fname).write_text(text or f'{N[0]}\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', f'c{N[0]}')
    return git(repo, 'rev-parse', 'HEAD')


def period(name):
    try:
        kb.create_period(name)
    except kb.KBError:
        pass
    return kb.period_dir(name)


class AutoPush(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        kb.init()

    def setUp(self):
        s = kb.load_settings()
        s.pop('自动推送', None)
        kb.save_settings(s)

    def test_default_on(self):
        self.assertTrue(ap.autopush_global())
        self.assertTrue(ap.autopush_on({}))
        d = period('默认开')
        self.assertTrue(ap.autopush_info(kb.load_meta(d))['生效'])

    def test_push_after_session(self):
        repo, remote = make_repo('s1')
        d = period('会话结束推送')
        start = git(repo, 'rev-parse', 'HEAD')
        sha = commit(repo)
        devsession._session_push(d, {'仓库': str(repo), '分支': 'main', '起点': start}, 0, '开发会话结束')
        self.assertEqual(git(remote, 'rev-parse', 'main'), sha)
        rec = kb.load_meta(d)['推送记录'][-1]
        self.assertEqual((rec['结果'], rec['提交数'], rec['时机']), ('已推送', 1, '开发会话结束'))
        self.assertEqual(ap.push_status(kb.load_meta(d), str(repo), 'main'), '已推送')
        self.assertIn('自动推送', (d / '记录.md').read_text())

    def test_no_push_without_new_commit_or_on_error(self):
        repo, remote = make_repo('s2')
        d = period('没新提交不推')
        before = git(remote, 'rev-parse', 'main')
        commit(repo)                                   # 用户自己的本地提交，会话没提交
        head = git(repo, 'rev-parse', 'HEAD')
        devsession._session_push(d, {'仓库': str(repo), '分支': 'main', '起点': head}, 0, '开发会话结束')
        devsession._session_push(d, {'仓库': str(repo), '分支': 'main', '起点': before}, 1, '开发会话结束')      # 出错退出
        devsession._session_push(d, {'仓库': str(repo), '分支': 'main', '起点': before, '停止中': True}, 0, '开发会话结束')
        self.assertEqual(git(remote, 'rev-parse', 'main'), before)
        self.assertEqual(ap.push_status(kb.load_meta(d), str(repo), 'main'), '本地领先 1 个')

    def test_switch_off(self):
        repo, remote = make_repo('s3')
        d = period('本期关掉')
        before = git(remote, 'rev-parse', 'main')
        commit(repo)
        kb.autopush_set(d.name, False)
        self.assertEqual(ap.autopush(d, str(repo), 'main', '测试', before)['结果'], '没开')
        kb.autopush_set(d.name, True)
        kb.autopush_set_global(False)                  # 总开关关了：每期开着也不推
        self.assertFalse(ap.autopush_on(kb.load_meta(d)))
        self.assertEqual(ap.autopush(d, str(repo), 'main', '测试', before)['结果'], '没开')
        self.assertEqual(git(remote, 'rev-parse', 'main'), before)
        kb.autopush_set_global(True)
        self.assertEqual(ap.autopush(d, str(repo), 'main', '测试', before)['结果'], '已推送')

    def test_gerrit_refs_for(self):
        repo, remote = make_repo('s4', 'gerrit-s4.git')
        d = period('Gerrit')
        before = git(remote, 'rev-parse', 'main')
        sha = commit(repo)
        r = ap.autopush(d, str(repo), 'main', '测试', before)
        self.assertEqual(r['结果'], '已推送')
        self.assertTrue(r.get('Gerrit'))
        self.assertEqual(git(remote, 'rev-parse', 'refs/for/main'), sha)
        self.assertEqual(git(remote, 'rev-parse', 'main'), before)      # 送审，不直接进分支
        self.assertTrue(ap.push_status(kb.load_meta(d), str(repo), 'main').startswith('已推送'))

    def test_conflict_not_pushed(self):
        repo, remote = make_repo('s5')
        other = TMP / 's5-other'
        subprocess.run(['git', 'clone', '-q', str(remote), str(other)], check=True)
        git(other, 'config', 'user.email', 'o@o')
        git(other, 'config', 'user.name', 'o')
        theirs = commit(other, text='theirs\n')
        git(other, 'push', '-q', 'origin', 'main')
        d = period('冲突不推')
        start = git(repo, 'rev-parse', 'HEAD')
        mine = commit(repo, text='mine\n')
        r = ap.autopush(d, str(repo), 'main', '测试', start)
        self.assertEqual(r['结果'], '失败')
        self.assertIn('冲突', r['原因'])
        self.assertEqual(git(remote, 'rev-parse', 'main'), theirs)
        self.assertEqual(git(repo, 'rev-parse', 'HEAD'), mine)             # rebase 已撤销，本地保持原样
        cards = [c for c in ap.fail_cards() if c['id'].startswith(f'push-fail:{d.name}:')]
        self.assertTrue(cards and cards[0]['标题'].startswith('推送失败：'))

    def test_rebase_then_push(self):
        repo, remote = make_repo('s6')
        other = TMP / 's6-other'
        subprocess.run(['git', 'clone', '-q', str(remote), str(other)], check=True)
        git(other, 'config', 'user.email', 'o@o')
        git(other, 'config', 'user.name', 'o')
        commit(other, 'b.txt')
        git(other, 'push', '-q', 'origin', 'main')
        d = period('先合远端再推')
        start = git(repo, 'rev-parse', 'HEAD')
        commit(repo)
        r = ap.autopush(d, str(repo), 'main', '测试', start)
        self.assertEqual(r['结果'], '已推送')
        self.assertEqual(git(remote, 'rev-parse', 'main'), git(repo, 'rev-parse', 'HEAD'))

    def test_dirty_pushed_detached_not(self):
        repo, remote = make_repo('s7')
        d = period('脏工作区照推')
        before = git(remote, 'rev-parse', 'main')
        sha = commit(repo)
        (repo / 'a.txt').write_text('dirty\n')        # 另一会话还没提交的改动
        r = ap.autopush(d, str(repo), 'main', '测试', before)
        self.assertEqual(r['结果'], '已推送')
        self.assertEqual(git(remote, 'rev-parse', 'main'), sha)          # 只推已提交的
        self.assertEqual((repo / 'a.txt').read_text(), 'dirty\n')       # 工作区原样
        git(repo, 'checkout', '-q', '--', 'a.txt')
        git(repo, 'checkout', '-q', '--detach')
        r = ap.autopush(d, str(repo), '', '测试', before)
        self.assertIn('游离', r['原因'])

    def _behind_dirty(self, name):
        """远端有别人的新提交 + 本地有自己的提交 + 工作区脏。→ (仓库, 远端, 别人的克隆)。"""
        repo, remote = make_repo(name)
        other = TMP / f'{name}-other'
        subprocess.run(['git', 'clone', '-q', str(remote), str(other)], check=True)
        git(other, 'config', 'user.email', 'o@o')
        git(other, 'config', 'user.name', 'o')
        commit(other, 'b.txt')
        git(other, 'push', '-q', 'origin', 'main')
        commit(repo, 'c.txt')
        (repo / 'a.txt').write_text('dirty\n')
        return repo, remote, other

    def test_dirty_and_behind_fails_with_files(self):
        repo, remote, _ = self._behind_dirty('s9')
        d = period('脏且落后不推')
        before = git(remote, 'rev-parse', 'main')
        r = ap.autopush(d, str(repo), 'main', '测试')
        self.assertEqual(r['结果'], '失败')
        self.assertIn('未提交的改动', r['原因'])
        self.assertIn('a.txt', r['原因'])                                   # 写清是哪些文件
        self.assertEqual(git(remote, 'rev-parse', 'main'), before)

    def test_retry_after_failure(self):
        repo, remote, _ = self._behind_dirty('s10')
        d = period('失败后补推')
        r = ap.autopush(d, str(repo), 'main', '会话结束')
        self.assertEqual(r['结果'], '失败')
        self.assertTrue([c for c in ap.fail_cards() if c['id'].startswith(f'push-fail:{d.name}:')])
        self.assertEqual(ap.retry_failed(root=str(repo)), [])                # 刚失败：退避中不重推
        res = dict(ap.retry_failed(root=str(repo), force=True))             # 还脏：再失败，只更新原记录
        self.assertIn('补推失败', res[d.name])
        xs = kb.load_meta(d)['推送记录']
        self.assertEqual((len(xs), xs[-1]['重试']), (1, 1))
        git(repo, 'checkout', '-q', '--', 'a.txt')                          # 另一会话收尾了
        res = dict(ap.retry_failed(force=True))
        self.assertIn('补推成功', res[d.name])
        self.assertEqual(git(remote, 'rev-parse', 'main'), git(repo, 'rev-parse', 'HEAD'))
        self.assertEqual(kb.load_meta(d)['推送记录'][-1]['结果'], '已推送')
        self.assertFalse([c for c in ap.fail_cards() if c['id'].startswith(f'push-fail:{d.name}:')])
        self.assertEqual(dict(ap.retry_failed(force=True)).get(d.name), None)   # 成功后不再重推

    def test_session_end_retries_same_repo(self):
        repo, remote, _ = self._behind_dirty('s11')
        a = period('同仓库A')
        self.assertEqual(ap.autopush(a, str(repo), 'main', '会话结束')['结果'], '失败')
        git(repo, 'checkout', '-q', '--', 'a.txt')
        b = period('同仓库B')
        start = git(repo, 'rev-parse', 'HEAD')
        devsession._session_push(b, {'仓库': str(repo), '分支': 'main', '起点': start}, 0, '开发会话结束')   # B 没新提交，但会顺带补推 A
        self.assertEqual(kb.load_meta(a)['推送记录'][-1]['结果'], '已推送')
        self.assertEqual(git(remote, 'rev-parse', 'main'), git(repo, 'rev-parse', 'HEAD'))

    def test_no_remote_and_svn_skip(self):
        repo = TMP / 's8'
        subprocess.run(['git', 'init', '-q', '-b', 'main', str(repo)], check=True)
        git(repo, 'config', 'user.email', 't@t')
        git(repo, 'config', 'user.name', 't')
        commit(repo)
        d = period('没有远端')
        self.assertEqual(ap.autopush(d, str(repo), 'main', '测试')['结果'], '没有远端')
        plain = TMP / 'plain'
        plain.mkdir()
        self.assertEqual(ap.autopush(d, str(plain), '', '测试')['结果'], '不是 git')


if __name__ == '__main__':
    unittest.main()
