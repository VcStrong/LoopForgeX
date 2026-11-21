"""磁盘水位与工作副本清理（REQ-037）。

≥80%：按最近最少使用，删空闲 ≥7 天、没有未提交 / 未推送改动、没被占用的副本（第 1 份是基础克隆不删）和构建缓存，直到 <70%。
有改动 / 可能有改动的副本永不自动删：网页可点「导出补丁到控制面后删除」（export_patch）。
每删一个都记一条（副本、大小、最后谁用、何时），随心跳的「清理」字段上报，控制面写审计。
和同一仓库的 fetch / gc 串行（gitlock）。
"""
import shutil
import subprocess
import time
from pathlib import Path

from kblib.runner import gitlock, sysinfo

HIGH, LOW, IDLE_DAYS = 0.80, 0.70, 7


def _size(p):
    n = 0
    for f in Path(p).rglob('*'):
        try:
            if f.is_file() and not f.is_symlink():
                n += f.stat().st_size
        except OSError:
            pass
    return n


def _git(p, *a):
    return subprocess.run(['git', '-C', str(p), *a], capture_output=True, text=True, timeout=120)


def has_changes(p):
    """未提交改动或未推送提交 → True（有改动的不删）。"""
    if _git(p, 'status', '--porcelain').stdout.strip():
        return True
    r = _git(p, 'rev-list', '--count', 'HEAD', '--not', '--remotes')
    try:
        return r.returncode == 0 and int(r.stdout.strip() or 0) > 0
    except ValueError:
        return True


def candidates(busy=()):
    """可清理的副本：[(最后使用, 仓库, 路径, 最后占用)]，按最近最少使用排序。"""
    from kblib.infra import pool
    st, out, busy = pool._state(), [], {str(Path(b).resolve()) for b in busy}
    for name in pool.pool_repos():
        for p in pool.copies(name):
            if p.name == '1':
                continue
            key = str(p.resolve())
            x = st.get(key) or {}
            last = float(x.get('最后使用') or p.stat().st_mtime)
            out.append((last, name, p, x.get('最后占用', ''), key in busy))
    return sorted(out, key=lambda t: t[0])


def _remove_copy(name, p):
    from kblib.infra import pool
    base = pool.repo_home(name) / '1'
    with gitlock.repo_lock(name):
        if _git(base, 'worktree', 'remove', '--force', str(p)).returncode:
            shutil.rmtree(p, ignore_errors=True)
            _git(base, 'worktree', 'prune')
        with pool._locked() as s:
            s.pop(str(p.resolve()), None)


def cleanup(data, busy=(), ratio_fn=None, now=None, idle_days=IDLE_DAYS):
    """磁盘 ≥80% 时清理。ratio_fn() → 当前使用比（默认看数据目录所在磁盘）。→ {清理: [...], 保留: [...], 使用比}。"""
    ratio_fn = ratio_fn or (lambda: sysinfo.disk(data)['使用比'])
    now = now or time.time()
    r = ratio_fn()
    gone, kept = [], []
    if r < HIGH:
        return {'清理': gone, '保留': kept, '使用比': r}
    cache = Path(data) / 'cache'                      # 构建缓存先删
    if cache.is_dir():
        sz = _size(cache)
        shutil.rmtree(cache, ignore_errors=True)
        gone.append({'副本': '构建缓存', '大小': sz, '最后谁用': '', '时间': time.strftime('%Y-%m-%d %H:%M:%S')})
    for last, name, p, who, is_busy in candidates(busy):
        if ratio_fn() < LOW:
            break
        label = f'{name} 第 {p.name} 份'
        if is_busy:
            continue                                   # 占用中的永不清理
        if now - last < idle_days * 86400:
            continue
        if has_changes(p):
            kept.append({'副本': label, '路径': str(p), '原因': '有未提交 / 未推送改动，可导出补丁后删除'})
            continue
        sz = _size(p)
        _remove_copy(name, p)
        gone.append({'副本': label, '路径': str(p), '大小': sz, '最后谁用': who,
                     '最后使用': time.strftime('%Y-%m-%d %H:%M', time.localtime(last)), '时间': time.strftime('%Y-%m-%d %H:%M:%S')})
    return {'清理': gone, '保留': kept, '使用比': ratio_fn()}


def export_patch(copy, out_dir):
    """把副本里没推送的提交（format-patch）和未提交改动（diff）导出成文件 → 文件列表。"""
    copy, out = Path(copy), Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    files = []
    base = _git(copy, 'merge-base', 'HEAD', '@{u}').stdout.strip()
    rng = [f'{base}..HEAD'] if base else ['--root', 'HEAD']
    r = _git(copy, 'format-patch', '-o', str(out), *rng)
    files += [x for x in r.stdout.splitlines() if x.strip()]
    diff = _git(copy, 'diff', 'HEAD').stdout
    untracked = _git(copy, 'ls-files', '--others', '--exclude-standard').stdout.split()
    if diff or untracked:
        f = out / 'uncommitted.diff'
        f.write_text(diff + ''.join(f'\n# 未跟踪文件：{u}' for u in untracked), 'utf-8')
        files.append(str(f))
    return files
