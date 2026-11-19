# From 大大王.DT
"""工作副本池：知识库部署在服务器上、多人 / 多期并行开发时，仓库按「克隆地址」登记，用户只选仓库，不管本地路径。

登记：代码仓库.json 里 {名称: {克隆地址, 克隆后命令, 路径(=第 1 份副本), 加入时间}}。克隆地址是
  ssh://user@host:29418/xxx、git@host:group/xxx.git、https://… 这类；克隆后命令里的 <dir> / {dir} 换成副本目录，
  Gerrit 地址（端口 29418 或主机名带 gerrit）没写克隆后命令时自动装 commit-msg 钩子。ssh 密钥由服务器 ~/.ssh 负责，知识库不生成。
副本：知识库根目录（_system 外）的 工作区/<仓库名>/<n>/，第 1 份是完整克隆（索引、各工程设置都认它），
  其余默认用 git worktree 从第 1 份派生（省时间和磁盘），也可设成 clone --reference 或完整克隆。
分配（pool_claim）：开工时按「仓库」挑一份空闲的——没有开发 / 修复 / 全栈各端 / 插件 / 运行到手机会话占着，
  而且干净（或者上次就是这一方用的，留着它没提交的改动）；优先上次用过的那份；全占了就新拉一份，到上限报「排队」。
  老的本机路径登记（不在 工作区/ 里的）原样返回，行为不变。
清理（pool_cleanup）：N 天没用、没人占、干净、没有未推送提交的副本删掉（第 1 份留着）；有未推送提交的不删、列出来提醒。
设置.json「工作副本池」：{上限: 4, 闲置天数: 7, 新副本方式: worktree | reference | clone}。
"""
import fcntl
import json
import os
import re
import shlex
import shutil
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

from kblib.core.common import KBError, PERIODS, REPOS_FILE, ROOT, load_settings, save_settings
from kblib import lazy
_fix = lazy('kblib.bugs.fix')            # 上层：开发 / 修复会话占用（_repo_busy）
_periods = lazy('kblib.core.periods')
_base = lazy('kblib.core.baseinfo')
_search = lazy('kblib.core.search')

POOL = ROOT / '工作区'
STATE = POOL / '.池.json'
LOCK = POOL / '.池.lock'
DEFAULTS = {'上限': 4, '闲置天数': 7, '新副本方式': 'worktree'}
MODES = ('worktree', 'reference', 'clone')
RESERVE = 180              # 分配后多少秒内算「预留」（会话还没起来，别被别人抢走）
CLONE_TIMEOUT = 3600

_URL = re.compile(r'^(?:(?:ssh|https?|git|file)://\S+|[\w.-]+@[\w.-]+:(?![/~])\S+)$')


# ---------------------------------------------------------------- 设置、登记
def pool_settings():
    s = dict(DEFAULTS)
    s.update({k: v for k, v in (load_settings().get('工作副本池') or {}).items() if k in DEFAULTS and v not in (None, '')})
    try:
        s['上限'] = max(1, int(s['上限']))
        s['闲置天数'] = max(1, int(s['闲置天数']))
    except (TypeError, ValueError):
        s.update(上限=DEFAULTS['上限'], 闲置天数=DEFAULTS['闲置天数'])
    if s['新副本方式'] not in MODES:
        s['新副本方式'] = DEFAULTS['新副本方式']
    return s


def pool_save_settings(limit=None, idle_days=None, mode=None):
    s = load_settings()
    x = s.setdefault('工作副本池', {})
    for k, v in (('上限', limit), ('闲置天数', idle_days), ('新副本方式', mode)):
        if v in (None, ''):
            continue
        if k == '新副本方式':
            if v not in MODES:
                raise KBError(f'新副本方式只能是 {" / ".join(MODES)}')
        else:
            try:
                v = int(v)
            except (TypeError, ValueError):
                raise KBError(f'{k}要填数字')
            if v < 1:
                raise KBError(f'{k}至少是 1')
        x[k] = v
    save_settings(s)
    p = pool_settings()
    return f'工作副本池：每仓库最多 {p["上限"]} 份，闲置 {p["闲置天数"]} 天清理，新副本用 {p["新副本方式"]}'


def is_clone_url(s):
    """像不像 git 克隆地址（不是本机路径、也不是 user@host:/绝对路径 这种远程工作目录）。"""
    s = (s or '').strip()
    if not s or s.startswith(('/', '~', '.')):
        return False
    if s.startswith(('http://', 'https://')):
        return True             # 网址登记仓库时就当克隆地址（https 克隆）
    return bool(_URL.match(s))


def _url_parts(url):
    """→ (用户@主机, 主机, 端口)，认不出返回空。"""
    m = re.match(r'^ssh://(?:(?P<u>[^@/]+)@)?(?P<h>[^/:]+)(?::(?P<p>\d+))?/', url)
    if m:
        return (f'{m["u"]}@{m["h"]}' if m['u'] else m['h']), m['h'], m['p'] or ''
    m = re.match(r'^(?:(?P<u>[\w.-]+)@)?(?P<h>[\w.-]+):(?![/~])', url)
    if m and '://' not in url:
        return (f'{m["u"]}@{m["h"]}' if m['u'] else m['h']), m['h'], ''
    m = re.match(r'^https?://(?:[^@/]+@)?(?P<h>[^/:]+)(?::(?P<p>\d+))?', url)
    if m:
        return '', m['h'], m['p'] or ''
    return '', '', ''


def is_gerrit(url):
    _, host, port = _url_parts(url or '')
    return port == '29418' or 'gerrit' in host.lower()


def gerrit_hook_cmd(url):
    """Gerrit 的 commit-msg 钩子（提交说明自动带 Change-Id，不然推不上去）。"""
    who, host, port = _url_parts(url)
    if url.startswith(('http://', 'https://')):
        base = re.match(r'^(https?://[^/]+)', url)[1]
        return f'curl -fsSLo <dir>/.git/hooks/commit-msg {base}/tools/hooks/commit-msg && chmod +x <dir>/.git/hooks/commit-msg'
    return (f'scp -O -P {port or 29418} {who}:hooks/commit-msg <dir>/.git/hooks/ && chmod +x <dir>/.git/hooks/commit-msg')


def _load_repos():
    try:
        return json.loads(REPOS_FILE.read_text('utf-8'))
    except (OSError, ValueError):
        return {}


def pool_repos():
    """按克隆地址登记的仓库：名称 → 登记项。"""
    return {k: v for k, v in _load_repos().items() if isinstance(v, dict) and v.get('克隆地址')}


def repo_home(name):
    return POOL / name


def copy_of(path):
    """路径是不是池里的副本 → (仓库名, 第几份)，不是返回 None。"""
    if not path:
        return None
    try:
        rel = Path(str(path)).expanduser().resolve().relative_to(POOL.resolve())
    except (ValueError, OSError):
        return None
    if len(rel.parts) < 2 or not rel.parts[1].isdigit() or rel.parts[0] not in pool_repos():
        return None
    return rel.parts[0], int(rel.parts[1])


def canonical(path):
    """池里的副本 → 第 1 份的路径（工程设置、索引都按它记）；不是池里的原样返回。"""
    c = copy_of(path)
    return str(repo_home(c[0]) / '1') if c else path


def copies(name):
    h = repo_home(name)
    if not h.is_dir():
        return []
    return sorted((p for p in h.iterdir() if p.name.isdigit() and (p / '.git').exists()), key=lambda p: int(p.name))


# ---------------------------------------------------------------- 状态（最后使用、预留、租用）
def _state():
    try:
        return json.loads(STATE.read_text('utf-8'))
    except (OSError, ValueError):
        return {}


def _save_state(s):
    POOL.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix('.tmp')
    tmp.write_text(json.dumps(s, ensure_ascii=False, indent=1), 'utf-8')
    tmp.replace(STATE)


@contextmanager
def _locked():
    POOL.mkdir(parents=True, exist_ok=True)
    with open(LOCK, 'a') as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        s = _state()
        yield s
        _save_state(s)


def _pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def pool_touch(path, who=''):
    """记下这份副本刚被用过（清理按它算闲置）。不是池里的副本不记。"""
    if not copy_of(path):
        return
    with _locked() as s:
        x = s.setdefault(str(Path(path).resolve()), {})
        x['最后使用'] = time.time()
        if who:
            x['最后占用'] = who


@contextmanager
def pool_lease(path, who):
    """插件 / 运行到手机这类知识库自己跑的任务：跑的期间把副本标成占用（别的会话分配时跳过它）。"""
    key = str(Path(path).resolve()) if copy_of(path) else ''
    if key:
        with _locked() as s:
            x = s.setdefault(key, {})
            x.setdefault('租用', {})[str(os.getpid())] = {'给': who, '开始': time.time()}
            x['最后使用'] = time.time()
    try:
        yield
    finally:
        if key:
            with _locked() as s:
                x = s.setdefault(key, {})
                (x.get('租用') or {}).pop(str(os.getpid()), None)
                x['最后使用'] = time.time()


def _git(path, *args, timeout=120):
    try:
        return subprocess.run(['git', '-C', str(path), *args], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, 124, '', f'超时（{timeout} 秒）')


def _stack_busy(r, me=''):
    """全栈各端的开发 / 审查会话在不在这个目录里跑。"""
    for n in _periods.period_names():
        try:
            m = _periods.load_meta(PERIODS / n)
        except Exception:
            continue
        for e, x in (m.get('各端开发') or {}).items():
            if (x.get('状态') in ('开发中', '审查中') and _pid_alive(x.get('pid', 0)) and x.get('仓库')
                    and f'{n}/{e}' != me and str(Path(x['仓库']).resolve()) == r):
                return f'「{n}」的 {e} 会话正在这个副本里运行'
    return ''


def pool_busy(path, me=''):
    """这份副本被谁占着（返回说明），空闲返回 ''。me：发起方自己（期名 / 期名/端 / an:编号），不算占用。"""
    r = str(Path(path).resolve())
    why = _fix._repo_busy(r, me=me) or _stack_busy(r, me)
    if why:
        return why
    x = _state().get(r) or {}
    for pid, l in (x.get('租用') or {}).items():
        if _pid_alive(pid) and l.get('给') != me:
            return f'{l.get("给") or "知识库任务"} 正在用这份副本'
    rv = x.get('预留') or {}
    if rv.get('到', 0) > time.time() and rv.get('给') != me:
        return f'刚分配给 {rv.get("给")}，会话正在启动'
    return ''


def _dirty(path):
    return bool(_git(path, 'status', '--porcelain', timeout=60).stdout.strip())


def _unpushed(path):
    """HEAD 上还没推到任何远端分支的提交数（没有远端分支时按全部算）。"""
    r = _git(path, 'rev-list', '--count', 'HEAD', '--not', '--remotes', timeout=60)
    try:
        return int(r.stdout.strip() or 0) if r.returncode == 0 else 0
    except ValueError:
        return 0


# ---------------------------------------------------------------- 拉副本
def _run_post(name, d, url, cmd):
    """克隆后命令：<dir> / {dir} 换成副本目录；没写且是 Gerrit 地址时装 commit-msg 钩子。→ 说明。"""
    cmd = (cmd or '').strip() or (gerrit_hook_cmd(url) if is_gerrit(url) else '')
    if not cmd:
        return ''
    q = shlex.quote(str(d))
    full = cmd.replace('<dir>', q).replace('{dir}', q)
    try:
        p = subprocess.run(['sh', '-c', full], cwd=str(d), capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return f'⚠ 克隆后命令超时：{full}'
    if p.returncode:
        return f'⚠ 克隆后命令失败（{p.returncode}）：{full}\n{(p.stderr or p.stdout).strip()[-300:]}'
    return f'已运行克隆后命令：{full}'


def _clone(url, d, ref=''):
    d.parent.mkdir(parents=True, exist_ok=True)
    args = ['git', 'clone', '-q', *(['--reference-if-able', str(ref), '--dissociate'] if ref else []), url, str(d)]
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=CLONE_TIMEOUT,
                           env=dict(os.environ, GIT_TERMINAL_PROMPT='0', GIT_SSH_COMMAND=os.environ.get('GIT_SSH_COMMAND') or 'ssh -o BatchMode=yes'))
    except subprocess.TimeoutExpired:
        shutil.rmtree(d, ignore_errors=True)
        raise KBError(f'克隆超时（{CLONE_TIMEOUT // 60} 分钟）：{url}')
    if p.returncode:
        shutil.rmtree(d, ignore_errors=True)
        err = (p.stderr or p.stdout).strip()[-400:]
        hint = '\n服务器上的 ssh 密钥没配好：在服务器上 ssh -p <端口> <用户>@<主机> 试一次（知识库不替你生成密钥）' \
            if re.search(r'Permission denied|publickey|Host key verification', err) else ''
        raise KBError(f'git clone 失败：{err}{hint}')


def pool_clone_first(name, touch=True):
    """登记后拉第 1 份（完整克隆）。已经有了直接返回。→ 说明。"""
    e = pool_repos().get(name)
    if not e:
        raise KBError(f'没有按克隆地址登记这个仓库：{name}')
    d = repo_home(name) / '1'
    if (d / '.git').exists():
        return f'{name} 第 1 份已存在：{d}'
    if d.exists() and any(d.iterdir()):
        raise KBError(f'{d} 已存在且不是 git 仓库，先挪走再登记')
    _clone(e['克隆地址'], d)
    post = _run_post(name, d, e['克隆地址'], e.get('克隆后命令', ''))
    if touch:
        pool_touch(d)
    return f'已克隆 {e["克隆地址"]} → {d}' + (f'\n{post}' if post else '')


def _new_copy(name, starts=()):
    """新拉一份：→ 路径。调用方持有锁并已检查上限。starts：起点候选（开发分支、它所在的马甲线 master），都没有用 origin/HEAD。"""
    e = pool_repos()[name]
    have = copies(name)
    if not have:
        pool_clone_first(name, touch=False)
        return repo_home(name) / '1'
    used = {int(p.name) for p in repo_home(name).iterdir() if p.name.isdigit()}
    n = next(i for i in range(1, len(used) + 2) if i not in used)
    d = repo_home(name) / str(n)
    base, mode = have[0], pool_settings()['新副本方式']
    if mode == 'worktree':
        _git(base, 'fetch', '-q', 'origin', timeout=600)
        _git(base, 'worktree', 'prune')
        start = next((f'origin/{b}' for b in starts if b and _ref_ok(base, f'refs/remotes/origin/{b}')), '') \
            or ('origin/HEAD' if _ref_ok(base, 'refs/remotes/origin/HEAD') else 'HEAD')      # 从要的马甲线起步，切分支时改动最少
        r = _git(base, 'worktree', 'add', '-q', '--detach', str(d), start, timeout=1800)
        if r.returncode == 0:
            _copy_ignored(base, d)
            return d
        shutil.rmtree(d, ignore_errors=True)           # worktree 不行（如 git 太老）退回 --reference
    _clone(e['克隆地址'], d, ref=base if mode != 'clone' else '')
    _run_post(name, d, e['克隆地址'], e.get('克隆后命令', ''))   # worktree 共用第 1 份的钩子，单独克隆的要再装
    _copy_ignored(base, d)
    return d


def _copy_ignored(base, d, limit=1 << 20):
    """第 1 份根目录下被忽略的小配置文件（local.properties 等）拷过去，新副本能直接编译。"""
    for f in _git(base, 'ls-files', '--others', '--ignored', '--exclude-standard', '--directory').stdout.splitlines():
        src = Path(base) / f
        if '/' not in f.rstrip('/') and src.is_file() and src.stat().st_size <= limit and not (Path(d) / f).exists():
            try:
                shutil.copy2(src, Path(d) / f)
            except OSError:
                pass


def _switch(d, branch, base=''):
    """干净的副本切到要的分支（本地有就切，没有从 origin 拉；两边都没有、给了基线分支（基础信息「远程仓库」里填的马甲 master）
    就从 origin/<基线> 新建）；切不了不报错（开工时照旧提示切分支）。→ 是否在这个分支上。"""
    if not branch or _git(d, 'branch', '--show-current').stdout.strip() == branch:
        return bool(branch)
    if _dirty(d):
        return False
    if _git(d, 'rev-parse', '--verify', '--quiet', f'refs/heads/{branch}').returncode == 0:
        return _git(d, 'switch', '-q', branch, timeout=600).returncode == 0
    _git(d, 'fetch', '-q', 'origin', f'{branch}:refs/remotes/origin/{branch}', timeout=600)
    if _git(d, 'rev-parse', '--verify', '--quiet', f'refs/remotes/origin/{branch}').returncode:
        if not base or base == branch:
            return False
        _git(d, 'fetch', '-q', 'origin', f'{base}:refs/remotes/origin/{base}', timeout=600)
        if not _ref_ok(d, f'refs/remotes/origin/{base}'):
            return False
        return _git(d, 'switch', '-q', '--no-track', '-c', branch, f'origin/{base}', timeout=600).returncode == 0
    return _git(d, 'switch', '-q', '--track', f'origin/{branch}', timeout=600).returncode == 0


def branch_holder(path, branch):
    """worktree 共用分支：branch 已经检出在同一仓库的另一份副本里 → 那份的路径（换副本也切不过去），没有返回 ''。"""
    me, cur = str(Path(path).resolve()), ''
    for line in _git(path, 'worktree', 'list', '--porcelain').stdout.splitlines():
        if line.startswith('worktree '):
            cur = line[9:]
        elif line == f'branch refs/heads/{branch}' and str(Path(cur).resolve()) != me:
            return cur
    return ''


# ---------------------------------------------------------------- 马甲线：一个仓库多个 master（alpha-master、beta-master…）
_MASTER = re.compile(r'^(?:master|main)(?:[-_].*)?$|[-_/](?:master|main)$')      # master、alpha-master、master-google-play；不认 feature-w-main-tab
_LINE_MEMO = {}


def _ref_ok(path, ref):
    return _git(path, 'rev-parse', '--verify', '--quiet', ref + '^{commit}', timeout=30).returncode == 0


def branch_base_line(path, branch):
    """分支是从哪条 master 拉出来的（马甲线）：在 origin 的各个 *master* / *main* 分支里，找分支独有提交最少的那个。
    → 'alpha-master' 这样的分支名（不带 origin/）；分支不存在 / 没有 master 返回 ''。分支本身就是 master 时返回它自己。"""
    if not branch:
        return ''
    ref = next((r for r in (f'refs/remotes/origin/{branch}', f'refs/heads/{branch}', branch) if _ref_ok(path, r)), '')
    if not ref:
        return ''
    sha = _git(path, 'rev-parse', ref, timeout=30).stdout.strip()
    key = (str(Path(path).resolve()), sha)
    if key in _LINE_MEMO:
        return _LINE_MEMO[key]
    masters = [x[len('origin/'):] for x in _git(path, 'for-each-ref', '--format=%(refname:short)', 'refs/remotes/origin', timeout=30).stdout.split()
               if x.startswith('origin/') and x != 'origin/HEAD' and _MASTER.search(x[len('origin/'):])]
    if branch in masters:
        best = branch
    else:
        scored = []
        for mname in masters:
            r = _git(path, 'rev-list', '--left-right', '--count', f'origin/{mname}...{sha}', timeout=120)
            try:
                behind, ahead = (int(v) for v in r.stdout.split())
            except ValueError:
                continue
            scored.append((ahead, behind, mname))
        best = min(scored)[2] if scored else ''
    if len(_LINE_MEMO) > 500:
        _LINE_MEMO.clear()
    _LINE_MEMO[key] = best
    return best


def line_tokens(*names):
    """分支名 / master 名拆成词（去掉 master、main、feature、stable 这类通用词），用来对应 app 模块（:app:alpha ↔ alpha-master）。"""
    common = {'master', 'main', 'feature', 'release', 'stable', 'dev', 'develop', 'fix', 'bugfix', 'hotfix', 'new', 'top', 'old', 'origin', 'kb'}
    out = []
    for n in names:
        for w in re.split(r'[-_/.]+', (n or '').lower()):
            if w and not w.isdigit() and w not in common and w not in out:
                out.append(w)
    return out


# ---------------------------------------------------------------- 分配
def _resolve_name(x):
    """仓库名 / 池里任一份副本的路径 → 仓库名；都不是返回 ''。"""
    x = str(x or '').strip()
    if x in pool_repos():
        return x
    c = copy_of(x)
    return c[0] if c else ''


def pool_claim(x, me, branch='', switch=True, base=''):
    """开工前拿一份副本：x 是仓库名或某份副本的路径（一般是这一期上次用的）。
    → 实际要用的路径（字符串）。不是池里的仓库原样返回（老的本机路径登记照旧）。全占满且到上限抛 KBError（排队）。
    switch：换了副本 / 新拉的副本是干净的就切到 branch（开工的会话不用再停下来切分支）。"""
    name = _resolve_name(x)
    if not name:
        return x
    prefer = str(Path(x).resolve()) if copy_of(x) else ''
    with _locked() as s:
        have = copies(name)
        if not have:
            pick, fresh = repo_home(name) / '1', True
            pool_clone_first(name, touch=False)
            _index_later(name)
        else:
            pick, fresh = None, False
            line = (branch_base_line(have[0], branch) if branch else '') or base        # 一个仓库多个 master（马甲包）：优先同一条线上的副本，切分支、编译缓存都省
            order = sorted(have, key=lambda p: (str(p.resolve()) != prefer,       # 上次用的那份 → 同马甲线 → 没人用过 / 自己用过的 → 别人用过的
                                                 bool(line) and (s.get(str(p.resolve())) or {}).get('马甲线', '') != line,
                                                 (s.get(str(p.resolve())) or {}).get('最后占用', '') not in ('', me), int(p.name)))
            why = {}
            for p in order:
                b = pool_busy(p, me)
                if b:
                    why[p.name] = b
                    continue
                last = (s.get(str(p.resolve())) or {}).get('最后占用', '')
                if str(p.resolve()) != prefer and last not in ('', me) and _dirty(p):
                    why[p.name] = f'有 {last} 留下的未提交改动'
                    continue
                pick = p
                break
            if pick is None:
                lim = pool_settings()['上限']
                if len(have) >= lim:
                    raise KBError(f'{name} 的 {len(have)} 份工作副本都在用（上限 {lim}，可在「⚙ 设置 → 工作副本池」调大），排队等空出来：'
                                  + '；'.join(f'第 {k} 份 {v}' for k, v in why.items()))
                pick, fresh = _new_copy(name, (branch, line)), True
        key = str(Path(pick).resolve())
        x2 = s.setdefault(key, {})
        x2.update(最后使用=time.time(), 最后占用=me, 预留={'给': me, '到': time.time() + RESERVE})
        if branch:
            x2['马甲线'] = line if have else (branch_base_line(pick, branch) or base)
    if switch and branch and (fresh or key != prefer):
        _switch(pick, branch, base)
    return str(pick)


def pool_release(path, me=''):
    """会话已经起来 / 不用了：去掉预留（占用由会话本身判断）。"""
    if not copy_of(path):
        return
    with _locked() as s:
        x = s.get(str(Path(path).resolve())) or {}
        if not me or (x.get('预留') or {}).get('给') == me:
            x.pop('预留', None)


def copy_label(path):
    """「📍 代码」里显示用的哪份副本：仓库名 #n（工作区/仓库名/n）；不是池里的返回 ''。"""
    c = copy_of(path)
    return f'{c[0]} 第 {c[1]} 份（工作区/{c[0]}/{c[1]}）' if c else ''


# ---------------------------------------------------------------- 基础信息里的「远程仓库」条目
REMOTE = 'remote:'          # 各处下拉里远程仓库条目的值：remote:<基础信息编号>


def _index_later(name):
    import threading

    def run():
        try:
            _search.index_repo(name)
        except Exception as e:          # noqa: BLE001 建索引失败不影响开工
            print('建索引失败：', name, repr(e), flush=True)
    threading.Thread(target=run, daemon=True).start()


def remote_entries():
    """基础信息里分类「远程仓库」的条目 → [{编号, 名称, 适用, 克隆地址, 基线分支, 平台类型, 运行模块, 运行变体, 包名, 克隆后命令, 仓库}]。
    仓库 = 工作副本池里的仓库名（同一个克隆地址的几条共用）。"""
    try:
        es = _base.base_load()['条目']
    except Exception:
        return []
    by_url = {v.get('克隆地址'): k for k, v in pool_repos().items()}
    out = []
    for e in es:
        if e.get('分类') != '远程仓库':
            continue
        v = {fd['名']: str(fd.get('值') or '').strip() for fd in e.get('字段') or [] if not fd.get('机密')}
        if not v.get('克隆地址'):
            continue
        out.append({'编号': e['编号'], '名称': e.get('名称', ''), '适用': e.get('适用', ''), **v,
                    '仓库': by_url.get(v['克隆地址'], '')})
    return out


def remote_entry(x):
    """remote:B029 / B029 → 条目；不是返回 None。"""
    x = str(x or '').strip()
    bid = x[len(REMOTE):] if x.startswith(REMOTE) else x
    return next((e for e in remote_entries() if e['编号'] == bid), None)


def is_remote_choice(x):
    return str(x or '').startswith(REMOTE)


def remote_label(e):
    """下拉里显示：示例工程 alpha（alpha-master · :app-alpha）。"""
    bits = [b for b in (e.get('基线分支'), e.get('运行模块'), e.get('平台类型')) if b]
    return f'🌐 {e["名称"]}' + (f'（{" · ".join(bits)}）' if bits else '')


def _register(url, post=''):
    """克隆地址登记进工作副本池（代码仓库.json），只登记不克隆（开工时按需拉）。→ 仓库名。同地址已登记直接返回。"""
    repos = _load_repos()
    for k, v in repos.items():
        if isinstance(v, dict) and v.get('克隆地址') == url:
            if post and not v.get('克隆后命令'):
                v['克隆后命令'] = post
                REPOS_FILE.write_text(json.dumps(repos, ensure_ascii=False, indent=2), 'utf-8')
            return k
    base = re.sub(r'\.git$', '', url.rstrip('/')).rsplit('/', 1)[-1].rsplit(':', 1)[-1]
    name = re.sub(r'[\\/:*?"<>|\s]+', '_', base).strip('._') or 'repo'
    while name in repos:
        name += '_'
    repos[name] = {'克隆地址': url, '克隆后命令': post or '', '路径': str(repo_home(name) / '1'),
                   '加入时间': time.strftime('%Y-%m-%d %H:%M'), '来自': '基础信息'}
    REPOS_FILE.write_text(json.dumps(repos, ensure_ascii=False, indent=2), 'utf-8')
    return name


def remote_claim(x, me, branch=''):
    """选了基础信息里的远程仓库条目（remote:B029）：登记进池（首次）→ 分配一份副本，切到 branch（没有就从条目的基线分支拉）。
    → (副本路径, 条目)。"""
    e = remote_entry(x)
    if not e:
        raise KBError(f'基础信息里没有这条远程仓库：{x}（在「🗂 基础信息」里按「远程仓库」模板新增）')
    name = _register(e['克隆地址'], e.get('克隆后命令', ''))
    path = pool_claim(name, me, branch or e.get('基线分支', ''), base=e.get('基线分支', ''))
    return path, e


# ---------------------------------------------------------------- 清理、状态
def pool_cleanup(days=None, dry=False):
    """删掉闲置的副本：N 天没用、没人占、干净、没有未推送提交（第 1 份永远留着）。→ {删除: [...], 提醒: [...], 说明}。"""
    days = days or pool_settings()['闲置天数']
    st, now_ = _state(), time.time()
    gone, warn = [], []
    for name in pool_repos():
        have = copies(name)
        for p in have[1:] if have and have[0].name == '1' else have:
            key = str(p.resolve())
            x = st.get(key) or {}
            last = float(x.get('最后使用') or 0) or p.stat().st_mtime
            if now_ - last < days * 86400 or pool_busy(p):
                continue
            n = _unpushed(p)
            if n:
                warn.append(f'{name} 第 {p.name} 份 {n} 个提交没推送（{_git(p, "branch", "--show-current").stdout.strip() or "游离"}），没删：推送或确认不要后再清理')
                continue
            if _dirty(p):
                warn.append(f'{name} 第 {p.name} 份有未提交改动，没删')
                continue
            if not dry:
                base = have[0]
                if _git(base, 'worktree', 'remove', '--force', str(p)).returncode:
                    shutil.rmtree(p, ignore_errors=True)
                    _git(base, 'worktree', 'prune')
                with _locked() as s:
                    s.pop(key, None)
            gone.append(f'{name} 第 {p.name} 份（闲置 {int((now_ - last) // 86400)} 天）')
    msg = ('已清理 ' + '、'.join(gone) if gone else '没有要清理的闲置副本') + (('\n⚠ ' + '\n⚠ '.join(warn)) if warn else '')
    return {'删除': gone, '提醒': warn, '说明': msg}


_LAST_CLEAN = [0.0]


def pool_tick():
    """定时调用（管家巡检）：一天最多清理一次。→ 提醒列表（有未推送提交没删的）。"""
    if time.time() - _LAST_CLEAN[0] < 86400 or not pool_repos():
        return []
    _LAST_CLEAN[0] = time.time()
    try:
        return pool_cleanup()['提醒']
    except Exception as e:
        return [f'清理工作副本失败：{e}']


def pool_status():
    """网页设置用：每个按地址登记的仓库、各份副本的状态。"""
    st, out = _state(), []
    for name, e in pool_repos().items():
        cs = []
        for p in copies(name):
            x = st.get(str(p.resolve())) or {}
            cs.append({'序号': int(p.name), '路径': str(p), '分支': _git(p, 'branch', '--show-current').stdout.strip(),
                       '占用': pool_busy(p), '改动': len(_git(p, 'status', '--porcelain').stdout.splitlines()),
                       '未推送': _unpushed(p), '最后占用': x.get('最后占用', ''), '马甲线': x.get('马甲线', ''),
                       '最后使用': time.strftime('%Y-%m-%d %H:%M', time.localtime(float(x.get('最后使用') or p.stat().st_mtime)))})
        out.append({'名称': name, '克隆地址': e['克隆地址'], '克隆后命令': e.get('克隆后命令', ''),
                    'Gerrit': is_gerrit(e['克隆地址']), '状态': e.get('状态', ''), '副本': cs})
    return {'设置': pool_settings(), '仓库': out, '目录': str(POOL)}
