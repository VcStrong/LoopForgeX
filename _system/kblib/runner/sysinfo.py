"""本机信息：自动标签和自检（REQ-012）、CPU / 内存 / 磁盘 / 网络（REQ-035）、手机（REQ-016）。

模拟磁盘（故障演练 disk-full，REQ-033）：环境变量 KB_RUNNER_FAKE_DISK=<使用比 0~1>，或数据目录下 fake_disk 文件写使用比（可动态改）。
"""
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO_CHECK_SEC = 600         # 仓库可达性自检多久做一次
_REPO_CACHE = {'t': 0.0, 'tags': [], 'checks': []}


def os_tag():
    return 'darwin' if sys.platform == 'darwin' else ('linux' if sys.platform.startswith('linux') else sys.platform)


def _which(name):
    return shutil.which(name) or ''


def adb_path():
    try:
        from kblib.infra import devlock
        return devlock.real_adb()
    except Exception:
        return _which('adb')


def phones(adb=None):
    """[{序列号, 型号}]：adb 在线（device 状态）的手机。没有 adb 返回 []。"""
    if os.environ.get('KB_RUNNER_FAKE_PHONES'):          # 测试 / 演练：假手机「序列号:型号,…」
        out = []
        for x in os.environ['KB_RUNNER_FAKE_PHONES'].split(','):
            sn, _, model = x.partition(':')
            if sn:
                out.append({'序列号': sn, '型号': model or '模拟手机'})
        return out
    adb = adb if adb is not None else adb_path()
    if not adb:
        return []
    try:
        from kblib.infra import devlock
        return [{'序列号': s, '型号': m} for s, st, m in devlock.online(adb) if st == 'device']
    except Exception:
        return []


def _repo_checks(stats=None, force=False):
    """各登记仓库能不能访问：git ls-remote（10 分钟一次）。→ (标签, 自检项)。"""
    if not force and time.time() - _REPO_CACHE['t'] < REPO_CHECK_SEC:
        return _REPO_CACHE['tags'], _REPO_CACHE['checks']
    tags, checks = [], []
    try:
        from kblib.infra import pool
        repos = pool.pool_repos()
    except Exception:
        repos = {}
    for name, e in repos.items():
        t0 = time.time()
        try:
            r = subprocess.run(['git', 'ls-remote', '--heads', e['克隆地址']], capture_output=True, text=True, timeout=20,
                               env=dict(os.environ, GIT_TERMINAL_PROMPT='0', GIT_SSH_COMMAND='ssh -o BatchMode=yes -o ConnectTimeout=10'))
            ok, why = r.returncode == 0, (r.stderr or r.stdout).strip()[-200:]
        except (OSError, subprocess.TimeoutExpired) as x:
            ok, why = False, f'超时或出错：{x}'
        if stats:
            stats.add(f'仓库:{name}', (time.time() - t0) * 1000, ok)
        checks.append({'项': f'仓库 {name}', '通过': ok, '原因': '' if ok else (why or '访问不了')})
        if ok:
            tags.append(f'repo:{name}')
    _REPO_CACHE.update(t=time.time(), tags=tags, checks=checks)
    return tags, checks


def tags_and_checks(stats=None, force=False):
    """自动标签 + 自检。自检不通过的仓库不打 repo: 标签，原因写在自检里。"""
    tags, checks = [os_tag()], []
    adb = adb_path()
    if adb or os.environ.get('ANDROID_HOME') or os.environ.get('ANDROID_SDK_ROOT'):
        tags.append('android-sdk')
    checks.append({'项': 'Android SDK（adb）', '通过': bool(adb), '原因': '' if adb else '没找到 adb'})
    if sys.platform == 'darwin':
        x = bool(_which('xcodebuild'))
        if x:
            tags.append('xcode')
        checks.append({'项': 'Xcode', '通过': x, '原因': '' if x else '没装 Xcode 命令行工具'})
    n = len(phones(adb))
    tags.append(f'phones:{n}')
    for eng in ('claude', 'codex'):
        ok = bool(_which(eng))
        if ok:
            tags.append(eng)
        checks.append({'项': f'引擎 {eng}', '通过': ok, '原因': '' if ok else f'没找到 {eng} 命令'})
    git = bool(_which('git'))
    checks.append({'项': 'git', '通过': git, '原因': '' if git else '没装 git'})
    rt, rc = _repo_checks(stats, force)
    return tags + rt, checks + rc


def cpu():
    try:
        l1 = os.getloadavg()[0]
    except OSError:
        l1 = 0.0
    return {'负载1分': round(l1, 2), '核数': os.cpu_count() or 1}


def _mem_linux():
    d = {}
    for line in Path('/proc/meminfo').read_text().splitlines():
        k, _, v = line.partition(':')
        d[k] = int(v.split()[0]) * 1024
    return d.get('MemAvailable', d.get('MemFree', 0)), d.get('MemTotal', 0)


def _mem_darwin():
    total = int(subprocess.run(['sysctl', '-n', 'hw.memsize'], capture_output=True, text=True, timeout=5).stdout.strip() or 0)
    out = subprocess.run(['vm_stat'], capture_output=True, text=True, timeout=5).stdout
    page, pages = 4096, {}
    for line in out.splitlines():
        if 'page size of' in line:
            page = int(''.join(c for c in line.split('page size of')[1] if c.isdigit()) or 4096)
        k, _, v = line.partition(':')
        v = v.strip().rstrip('.')
        if v.isdigit():
            pages[k.strip()] = int(v)
    free = sum(pages.get(k, 0) for k in ('Pages free', 'Pages inactive', 'Pages speculative', 'Pages purgeable'))
    return free * page, total


def mem():
    try:
        avail, total = _mem_linux() if sys.platform.startswith('linux') else _mem_darwin()
    except Exception:
        return {'可用比': 1.0, '总GB': 0}
    return {'可用比': round(avail / total, 3) if total else 1.0, '总GB': round(total / 2 ** 30, 1)}


def fake_disk_ratio(data):
    v = os.environ.get('KB_RUNNER_FAKE_DISK', '')
    f = Path(data) / 'fake_disk'
    if f.exists():
        v = f.read_text().strip() or v
    try:
        r = float(v)
        return r if 0 <= r <= 1 else None
    except ValueError:
        return None


def disk(data):
    """{可用GB, 总GB, 使用比}；模拟磁盘开关打开时按设定的使用比报。"""
    Path(data).mkdir(parents=True, exist_ok=True)
    u = shutil.disk_usage(str(data))
    fake = fake_disk_ratio(data)
    used = fake if fake is not None else (u.total - u.free) / u.total
    return {'可用GB': round(u.total * (1 - used) / 2 ** 30, 2), '总GB': round(u.total / 2 ** 30, 2), '使用比': round(used, 3),
            **({'模拟': True} if fake is not None else {})}


def machine_name():
    return platform.node().split('.')[0] or 'runner'
