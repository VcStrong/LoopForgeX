# From 大大王.DT
"""Bug 修复用的独立 git worktree：修复会话不在你的主工作区里改，主工作区有改动、在别的分支上都不挡修复。

每个仓库固定一份 worktree（~/.kb-worktrees/<仓库名>-<hash>，复用省去重新检出和构建缓存），
会话开始时在里面建分支 kb-fix/<会话>（从目标分支最新提交开始），会话在里面改、提交；
结束时：没提交的改动 git stash 收起（stash 是整个仓库共用的，主仓库 git stash list 也看得到），
提交合回目标分支（目标分支检出在主工作区就 merge --ff-only，没检出就直接快进；分支期间前进了先 rebase），
合不回就留在 kb-fix/<会话> 分支上，提示你处理。"""
import hashlib
import shutil
from pathlib import Path
from kblib.core.common import KBError
from kblib.infra import hosts

WT_HOME = Path.home() / '.kb-worktrees'
COPY_MAX = 1 << 20          # 主仓库根目录下被忽略的小配置文件（local.properties 等）拷到 worktree


def wt_path(root):
    r = Path(root).resolve()
    return WT_HOME / f'{r.name}-{hashlib.sha1(str(r).encode()).hexdigest()[:8]}'


def _g(loc, *args, timeout=120):
    return hosts.git(str(loc), *args, timeout=timeout)


def _ok(loc, *args, timeout=120):
    r = _g(loc, *args, timeout=timeout)
    if r.returncode:
        raise KBError(f'git {" ".join(args[:3])} 失败：{(r.stderr or r.stdout).strip()[-300:]}')
    return r.stdout.strip()


def _common(loc):
    r = _g(loc, 'rev-parse', '--path-format=absolute', '--git-common-dir')
    return str(Path(r.stdout.strip()).resolve()) if r.returncode == 0 and r.stdout.strip() else ''


def _base(root, branch):
    """目标分支的起点：本地分支；落后于上游（本地没有没推的提交）就用上游的。"""
    if _g(root, 'rev-parse', '--verify', '--quiet', f'refs/heads/{branch}').returncode:
        up = f'refs/remotes/origin/{branch}'
        if _g(root, 'rev-parse', '--verify', '--quiet', up).returncode:
            raise KBError(f'仓库里没有分支 {branch}')
        return _ok(root, 'rev-parse', up)
    local = _ok(root, 'rev-parse', f'refs/heads/{branch}')
    up = _g(root, 'rev-parse', '--verify', '--quiet', f'{branch}@{{u}}').stdout.strip()
    if up and up != local and _g(root, 'merge-base', '--is-ancestor', local, up).returncode == 0:
        return up
    return local


def wt_shelve(wt, label):
    """worktree 里还没提交的改动（含会话新建的文件）收进 git stash，返回 (说明, stash 提交号)；没改动返回 ('', '')。"""
    if not Path(wt).is_dir():
        return '', ''
    if not _g(wt, 'status', '--porcelain').stdout.strip():
        return '', ''
    r = _g(wt, 'stash', 'push', '--include-untracked', '-m', label, timeout=300)
    if r.returncode:
        raise KBError(f'git stash 失败：{(r.stderr or r.stdout).strip()[-300:]}')
    ref = _ok(wt, 'rev-parse', 'stash@{0}')
    return f'git stash「{label}」（{ref[:10]}，主仓库 git stash list 里也能看到；git stash apply {ref[:10]} 恢复）', ref


def wt_prepare(root, branch, sid):
    """准备修复会话用的 worktree：→ (worktree 路径, 起点提交, 工作分支)。失败抛 KBError（调用方退回主工作区模式）。"""
    rp = Path(root).resolve()
    if not (rp / '.git').exists():
        raise KBError('不是 git 仓库根目录')
    base = _base(rp, branch)
    wt, work = wt_path(rp), f'kb-fix/{sid}'
    mine = _common(rp)
    if wt.is_dir() and _common(wt) == mine:
        wt_shelve(wt, f'kb 修复 worktree 开始前发现的遗留（会话 {sid} 之前）')     # 上次没收起的先收起，收不起就不开始（不丢改动）
        old = _g(wt, 'branch', '--show-current').stdout.strip()
        _ok(wt, 'checkout', '-q', '-f', '-B', work, base, timeout=900)
        _g(wt, 'clean', '-fdq')                      # 未跟踪的删掉，被忽略的（build 目录、本机配置）保留
        if old.startswith('kb-fix/') and old != work and _g(rp, 'merge-base', '--is-ancestor', old, branch).returncode == 0:
            _g(rp, 'branch', '-D', old)              # 已合回的旧工作分支清掉
    else:
        if wt.exists():
            wt = wt.with_name(wt.name + '-' + sid[-4:])      # 同名目录不是这个仓库的 worktree：不碰它，换个名字
        wt.parent.mkdir(parents=True, exist_ok=True)
        _g(rp, 'worktree', 'prune')
        _ok(rp, 'worktree', 'add', '-q', '-B', work, str(wt), base, timeout=1800)
    for f in _g(rp, 'ls-files', '--others', '--ignored', '--exclude-standard', '--directory').stdout.splitlines():
        src = rp / f
        if '/' not in f.rstrip('/') and src.is_file() and src.stat().st_size <= COPY_MAX and not (wt / f).exists():
            try:
                shutil.copy2(src, wt / f)
            except OSError:
                pass
    return str(wt), base, work


def _checked_out(root, branch):
    """分支检出在哪个 worktree（路径），没检出返回 ''。"""
    cur = ''
    for line in _g(root, 'worktree', 'list', '--porcelain').stdout.splitlines():
        if line.startswith('worktree '):
            cur = line[9:]
        elif line == f'branch refs/heads/{branch}':
            return cur
    return ''


def wt_integrate(root, branch, wt, base, work):
    """把 worktree 里的提交合回目标分支。→ (结果说明, 是否合回, {旧提交: 新提交}, 修复提交列表「短号 说明」)。"""
    rp = Path(root).resolve()
    if _g(rp, 'rev-parse', '--verify', '--quiet', f'refs/heads/{work}').returncode:
        return '', True, {}, []
    tip = _ok(rp, 'rev-parse', f'refs/heads/{work}')
    if tip == base:
        _drop(rp, wt, work)
        return '', True, {}, []
    n = len(_ok(rp, 'rev-list', f'{base}..{tip}').split())
    since = base
    has = _g(rp, 'rev-parse', '--verify', '--quiet', f'refs/heads/{branch}').stdout.strip()
    remap = {}
    if has and _g(rp, 'merge-base', '--is-ancestor', has, tip).returncode:       # 期间分支前进了：rebase 上去
        old = _ok(rp, 'log', '--format=%H %s', f'{base}..{tip}').splitlines()
        r = _g(wt, 'rebase', '-q', has, timeout=600)
        if r.returncode:
            _g(wt, 'rebase', '--abort')
            return (f'⚠ {n} 个修复提交没合回 {branch}：期间 {branch} 有新提交，rebase 冲突；提交留在分支 {work}，'
                    f'请手动 git cherry-pick 或 merge'), False, {}, []
        tip, since = _ok(wt, 'rev-parse', 'HEAD'), has
        new = {s.split(' ', 1)[1] if ' ' in s else '': s.split(' ', 1)[0] for s in _ok(rp, 'log', '--format=%H %s', f'{has}..{tip}').splitlines()}
        remap = {h.split(' ', 1)[0]: new.get(h.split(' ', 1)[1] if ' ' in h else '') for h in old}
    log = _ok(rp, 'log', '--format=%h %s', f'{since}..{tip}').splitlines()
    where = _checked_out(rp, branch)
    if where and Path(where).resolve() == Path(wt).resolve():
        return f'⚠ {branch} 被检出在修复 worktree 里，没合回', False, remap, log
    if where:
        r = _g(where, 'merge', '--ff-only', '-q', tip, timeout=300)
        if r.returncode:
            return (f'⚠ {n} 个修复提交没合回 {branch}：{where} 里未提交的改动和修复改了同一批文件（{" ".join((r.stderr or r.stdout).split())[-200:]}）；'
                    f'提交留在分支 {work}，处理掉那些改动后 git merge --ff-only {work}'), False, remap, log
    else:
        args = ['update-ref', f'refs/heads/{branch}', tip] + ([has] if has else [])
        r = _g(rp, *args)
        if r.returncode:
            return f'⚠ {n} 个修复提交没合回 {branch}：{(r.stderr or r.stdout).strip()[-200:]}；提交留在分支 {work}', False, remap, log
    _drop(rp, wt, work)
    return f'{n} 个提交已合回 {branch}' + ('' if where else '（分支没检出，直接快进）'), True, remap, log


def _drop(rp, wt, work):
    """worktree 换到游离 HEAD，删掉已合回的工作分支（worktree 本身留着下次复用）。"""
    _g(wt, 'checkout', '-q', '--detach')
    _g(rp, 'branch', '-D', work)
