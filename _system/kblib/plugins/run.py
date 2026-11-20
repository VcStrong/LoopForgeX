"""插件「运行一次」：选一期或直接输分支，知识库自己准备好仓库再运行，改动提交并推送。

流程（后台线程，网页轮询进度）：
  1. 定仓库：输入的是期名（或某期的分支）→ 用那一期的开发仓库和分支；输入的是新分支 → 用选的仓库（基础信息里带本地路径的条目）。
  2. 检查：路径不存在 / 不是 git 仓库 → 报错停下。
  3. 仓库被占用（开发会话、修复会话、别的插件任务在用）→ 排队，空出来再继续；超过 QUEUE_MAX 秒放弃。
  4. 切分支：git fetch；本地有就 checkout + pull --ff-only；本地没有、远端有就建跟踪分支拉下来；都没有报错。
     要切分支但有未提交改动 → git stash 收起（说明写期名、时间、原分支），在那一期记一条待处理问题，不再停下。
  5. 运行插件；只提交插件改过的文件，推到远端；切过去就留在目标分支（这一期明确要用的分支），不再切回原来的分支。
svn 工作副本同样支持：分支填 trunk、branches/xxx 或分支名（自动找 branches/<名>）；svn switch 切过去、svn update 拉最新；
  运行后新文件 svn add，只把插件改过的文件 svn commit（svn 提交就是推到服务器）；留在目标目录，不再 switch 回去。
"""
import re
import threading
import time
import uuid
from pathlib import Path

from kblib.core.common import PERIODS, KBError, now, sess_env
from kblib.core.periods import load_meta, log, period_dir, period_names
from kblib.core.baseinfo import base_load
from kblib.infra import hosts, pool as _pool
from kblib.plugins.registry import _git_changed, get_plugin, run_plugins
from kblib.ai.devprep import _git

QUEUE_MAX = 2 * 3600
QUEUE_POLL = 15
_JOBS = {}                # 任务号 → {插件, 目标, 状态, 步骤: [(时间, 文字)], 结果}
_REPO_LOCKS = {}          # 仓库 → Lock：同一仓库的插件任务一个一个来
_LOCK = threading.Lock()


def base_repo_paths():
    """基础信息里带本地路径的条目（如「某工程本地路径」）。"""
    out = []
    for e in base_load()['条目']:
        for f in e.get('字段') or []:
            v = (f.get('值') or '').strip()
            if f.get('机密'):
                continue
            if v.startswith(('/', '~')):
                p = Path(v).expanduser()
                kind = 'git' if (p / '.git').exists() else 'svn' if (p / '.svn').exists() else ''
                out.append({'名称': e['名称'], '编号': e['编号'], '路径': str(p), 'git': bool(kind), '版本库': kind})
            elif hosts.is_remote(v) and '路径' in (f.get('名') or ''):   # 远程工程：字段名含「路径」，值 user@host:/path 或 ssh://user@host:port/path
                out.append({'名称': e['名称'], '编号': e['编号'], '路径': v, 'git': True, '远程': True})
    return out


def _period_branch(d, m):
    from kblib.plugins.builds import period_branches
    return (period_branches(d, m) or [''])[0]


def plugin_targets():
    """「运行一次」下拉：各期（分支、仓库，没完成的在前）+ 基础信息里的本地仓库。"""
    ps = []
    for n in period_names():
        d = PERIODS / n
        m = load_meta(d)
        ps.append({'名称': n, '分支': _period_branch(d, m), '仓库': m.get('开发仓库', ''), '状态': m.get('状态', '')})
    ps.sort(key=lambda x: x['状态'] == '已完成')
    return {'期': ps, '仓库': [x for x in base_repo_paths() if not x.get('远程')]}   # 「运行一次」先只在本机跑


def _resolve(target, repo):
    """→ (期目录或 None, 仓库, 分支)。"""
    target = (target or '').strip()
    if not target:
        raise KBError('填一个期名或分支')
    ps = plugin_targets()['期']
    hit = next((p for p in ps if p['名称'] == target), None) or next((p for p in ps if p['分支'] and p['分支'] == target), None)
    if hit:
        d = period_dir(hit['名称'])
        root = (repo or '').strip() or hit['仓库']
        if not root:
            raise KBError(f'「{d.name}」还没有开发仓库，选一个仓库')
        return d, root, hit['分支'] or (target if target != d.name else '')
    if not (repo or '').strip():
        raise KBError(f'「{target}」不是已有的期，当作分支：要选在哪个仓库运行')
    return None, repo.strip(), target


def _step(j, text):
    j['步骤'].append((time.strftime('%H:%M:%S'), text))


def _commit_msg(rp, p):
    """插件提交说明：plugin.json「提交说明」；仓库提交要带「TAG: xxx」（commit-msg 钩子校验，如 VV Commit Template）时，
    沿用这个分支最近提交里的 TAG 行，不然钩子会拒绝提交。"""
    msg = p.get('提交说明') or f'chore: {p["名称"]}'
    if 'TAG:' in msg:
        return msg
    log_ = _git(rp, 'log', '-30', '--format=%B').stdout
    tag = next((ln.strip() for ln in log_.splitlines() if re.match(r'^TAG:\s*[A-Za-z0-9_\-]+\s*$', ln.strip())), '')
    return f'{msg}\n\n{tag}' if tag else msg


def _wait_repo(j, rp, me='', limit=QUEUE_MAX):
    """排队拿仓库：同一进程里同一仓库的插件任务一个一个来，别的会话（开发 / 修复）在改就等；me 是发起方自己，不算占用。
    拿到返回锁（用完 release），超过 limit 秒抛错。"""
    from kblib.bugs.fix import _repo_busy
    with _LOCK:
        lk = _REPO_LOCKS.setdefault(str(rp), threading.Lock())
    t0, said = time.time(), ''
    while True:
        if lk.acquire(blocking=False):
            busy = _repo_busy(rp, me=me)
            if not busy:
                return lk
            lk.release()
        else:
            busy = '另一个插件任务正在这个仓库里运行'
        if busy != said:
            _step(j, f'排队：{busy}，空出来后自动继续')
            said = busy
        j['状态'] = '排队中'
        if time.time() - t0 > limit:
            raise KBError(f'排队超过 {limit // 60} 分钟，已放弃：{busy}')
        time.sleep(QUEUE_POLL)


def _run(j, p, d, root, branch, push):
    rp = Path(root).expanduser()
    if not rp.exists():
        raise KBError(f'路径错误：{rp} 不存在')
    kind, top = hosts._top(str(rp))
    if not kind:
        raise KBError(f'{rp} 不是 git 仓库，也不是 svn 工作副本')
    rp = Path(top)
    if kind == 'git' and _pool.copy_of(rp):          # 按地址登记的仓库：这份被开发 / 修复会话占着就换一份空闲副本，不排队
        busy = _pool.pool_busy(rp)
        held = branch and (_git(rp, 'branch', '--show-current').stdout.strip() == branch
                           or _pool.branch_holder(rp, branch))      # 分支检出在别的副本里（worktree 共用分支）换了也切不过去：照旧排队
        if busy and not held:
            try:
                alt = Path(_pool.pool_claim(str(rp), f'插件「{p["名称"]}」', switch=False))
                _step(j, f'{busy}，工作副本池改用 {_pool.copy_label(alt)}')
                rp = alt
            except KBError as ex:
                _step(j, str(ex))
    if kind == 'svn':
        cur = hosts.branch_of(str(rp), 'svn')
        branch, remote = _svn_branch(rp, branch) if branch else cur, ''
        _step(j, f'svn 工作副本 {rp}，当前 {cur}，目标 {branch}')
    else:
        cur = _git(rp, 'branch', '--show-current').stdout.strip()
        remote = _git(rp, 'remote').stdout.split()
        remote = 'origin' if 'origin' in remote else (remote[0] if remote else '')
        branch = branch or cur
        _step(j, f'仓库 {rp}，当前分支 {cur or "（游离）"}，目标分支 {branch}' + ('' if remote else '，没有远端'))

    lk = _wait_repo(j, rp)
    j['状态'] = '运行中'
    try:
        with _pool.pool_lease(rp, f'插件「{p["名称"]}」'):
            _pool.pool_release(rp)
            if kind == 'svn':
                return _run_locked_svn(j, p, d, rp, cur, branch, push)
            return _run_locked(j, p, d, rp, cur, branch, remote, push)
    finally:
        lk.release()


def _run_locked(j, p, d, rp, cur, branch, remote, push):
    def git(*a, timeout=120, msg=''):
        r = _git(rp, *a, timeout=timeout)
        if r.returncode != 0:
            raise KBError(f'{msg or "git " + " ".join(a)} 失败：{(r.stderr or r.stdout).strip()[-800:]}')
        return r.stdout.strip()

    switched = False
    if remote:
        _step(j, f'git fetch {remote} {branch}')
        _git(rp, 'fetch', remote, branch, timeout=300)
    if branch != cur:
        from kblib.ai.branchswap import stash_for_switch
        local = _git(rp, 'rev-parse', '--verify', '--quiet', f'refs/heads/{branch}').returncode == 0
        track = not local and remote and _git(rp, 'rev-parse', '--verify', '--quiet', f'refs/remotes/{remote}/{branch}').returncode == 0
        if not local and not track:
            raise KBError(f'本地和远端都没有分支 {branch}')
        stashed = stash_for_switch(rp, branch, d, f'插件「{p["名称"]}」切分支')
        if stashed:
            _step(j, stashed['文字'] + (f'（已记待处理 {stashed["问题"]}）' if stashed['问题'] else ''))
        try:
            if local:
                git('checkout', branch)
                _step(j, f'切到本地分支 {branch}')
            else:
                git('checkout', '-b', branch, '--track', f'{remote}/{branch}')
                _step(j, f'本地没有 {branch}，已从 {remote} 拉下来')
        except KBError:
            if stashed:
                _git(rp, 'stash', 'pop')
                _step(j, '切分支失败，收起的改动已还原')
            raise
        switched = True
    try:
        if remote and _git(rp, 'rev-parse', '--abbrev-ref', '@{u}').returncode == 0:
            git('pull', '--ff-only', timeout=300, msg='拉取最新代码')
            _step(j, '已拉到最新')
        before = _git_changed(rp)
        _step(j, f'运行插件「{p["名称"]}」…')
        out = run_plugins('manual', d, str(rp), branch, (load_meta(d).get('SDD目录', '') if d else ''), only=p['id'])
        _step(j, out or '没有运行')
        if '失败' in out.split('\n')[0] or '没有运行' in out.split('\n')[0]:
            raise KBError('插件没跑成功，没有提交')
        after = _git_changed(rp)
        changed = sorted(f for f in set(after) if before.get(f) != after.get(f))
        if not changed:
            _step(j, '没有文件改动，不用提交')
            return '完成：没有改动'
        if not push:
            _step(j, f'{len(changed)} 个文件改动，按你的选择没有提交')
            return f'完成：{len(changed)} 个文件改动，未提交'
        git('add', '--', *changed)
        msg = _commit_msg(rp, p)
        git('commit', '-m', msg, '--', *changed, msg='提交')
        h = git('rev-parse', '--short', 'HEAD')
        _step(j, f'已提交 {h}「{msg}」（{len(changed)} 个文件）')
        if remote:
            _step(j, '推送前更新：' + hosts.git_catch_up(str(rp), remote, branch))
            git('push', remote, f'HEAD:refs/heads/{branch}', timeout=300, msg='推送')
            _step(j, f'已推送到 {remote}/{branch}')
        if d:
            log(d, '插件', f'「{p["名称"]}」手动运行：提交 {h} {"并推送" if remote else ""}（{len(changed)} 个文件）')
        return f'完成：提交 {h}' + ('，已推送' if remote else '')
    finally:
        if switched and cur:
            _step(j, f'留在 {branch}（这一期要用的分支），不切回原来的 {cur}')


def _svn_branch(rp, name):
    return hosts.svn_branch(str(rp), name)


def _run_locked_svn(j, p, d, rp, cur, branch, push):
    loc = str(rp)

    def svn(*a, timeout=300, msg=''):
        r = hosts.svn(loc, *a, timeout=timeout)
        if r.returncode != 0:
            raise KBError(f'{msg or "svn " + " ".join(a)} 失败：{(r.stderr or r.stdout).strip()[-800:]}')
        return r.stdout.strip()

    switched = False
    if branch != cur:
        ch, _ = hosts.svn_changes(loc)
        if ch:
            raise KBError(f'工作副本有 {len(ch)} 个未提交的改动（在 {cur}），不能切到 {branch}；先提交或处理再运行')
        svn('switch', f'^/{branch}', msg=f'切到 {branch}')
        _step(j, f'已 svn switch 到 {branch}')
        switched = True
    try:
        svn('update', msg='svn update 拉最新')
        _step(j, '已 svn update 到 r' + hosts.head(loc, 'svn'))
        before = _git_changed(rp)
        _step(j, f'运行插件「{p["名称"]}」…')
        out = run_plugins('manual', d, loc, branch, (load_meta(d).get('SDD目录', '') if d else ''), only=p['id'])
        _step(j, out or '没有运行')
        if '失败' in out.split('\n')[0] or '没有运行' in out.split('\n')[0]:
            raise KBError('插件没跑成功，没有提交')
        after = _git_changed(rp)
        changed = sorted(f for f in set(after) | set(before) if before.get(f) != after.get(f))
        if not changed:
            _step(j, '没有文件改动，不用提交')
            return '完成：没有改动'
        if not push:
            _step(j, f'{len(changed)} 个文件改动，按你的选择没有提交')
            return f'完成：{len(changed)} 个文件改动，未提交'
        _, new = hosts.svn_changes(loc)
        newf = [f for f in changed if any(f == n or f.startswith(n.rstrip('/') + '/') for n in new)]
        if newf:
            svn('add', '--parents', *newf, msg='svn add 新文件')
            _step(j, f'svn add {len(newf)} 个新文件')
        gone = [x for st, x in hosts.svn_changes(loc)[0] if st == '!' and x in changed]
        if gone:                                   # 插件直接删掉的文件：告诉 svn 是删除
            svn('delete', *gone, msg='svn delete 删掉的文件')
        msg = p.get('提交说明') or f'chore: {p["名称"]}'
        _step(j, '提交前更新：' + hosts.sync(loc, 'svn', strict=True))
        ch, _ = hosts.svn_changes(loc)
        paths = [x for _, x in ch if x in changed or any(f.startswith(x.rstrip('/') + '/') for f in newf)]
        rev = hosts.svn_commit(loc, paths, msg)
        _step(j, f'已 svn 提交 r{rev}「{msg}」（{len(changed)} 个文件）')
        if d:
            log(d, '插件', f'「{p["名称"]}」手动运行：svn 提交 r{rev}（{len(changed)} 个文件）')
        return f'完成：svn 提交 r{rev}'
    finally:
        if switched and cur:
            _step(j, f'留在 {branch}，不 switch 回原来的 {cur}')


def plugin_run_start(pid, target, repo='', push=True, by='用户（网页）'):
    """网页「运行一次」：先同步检查能确定的（期 / 分支 / 仓库），再在后台跑，返回任务号。"""
    p = get_plugin(pid)
    if p.get('错误'):
        raise KBError(p['错误'])
    d, root, branch = _resolve(target, repo)
    rp = Path(root).expanduser()
    if not rp.exists():
        raise KBError(f'路径错误：{rp} 不存在')
    if not hosts.vcs(str(rp)):
        raise KBError(f'{rp} 不是 git 仓库，也不是 svn 工作副本')
    jid = uuid.uuid4().hex[:10]
    j = {'编号': jid, '插件': p['名称'], '目标': target, '期': d.name if d else '', '仓库': str(rp), '分支': branch,
         '状态': '运行中', '步骤': [], '结果': '', '开始': now(), '发起': by}
    _JOBS[jid] = j

    def go():
        try:
            j['结果'] = _run(j, p, d, str(rp), branch, push)
            j['状态'] = '完成'
        except KBError as e:
            j['状态'], j['结果'] = '失败', str(e)
            _step(j, f'✗ {e}')
        except Exception as e:  # noqa: BLE001 — 后台线程，什么错都要记下给网页看
            j['状态'], j['结果'] = '失败', f'{type(e).__name__}: {e}'
            _step(j, f'✗ {j["结果"]}')
    threading.Thread(target=go, daemon=True).start()
    return jid


def plugin_run_sync(pid, target, repo='', push=False, by='终端'):
    """终端 kb plugin run：和网页「运行一次」同一个流程（时机「手动运行一次」：排队等占用、切分支、拉最新、运行），
    在当前进程里跑完再返回每一步。push：插件改了文件就提交并推送（默认不提交，留给你检查）。"""
    p = get_plugin(pid)
    if p.get('错误'):
        raise KBError(p['错误'])
    d, root, branch = _resolve(target, repo)
    j = {'编号': 'cli', '插件': p['名称'], '目标': target, '状态': '运行中', '步骤': [], '结果': '', '开始': now(), '发起': by}
    try:
        j['结果'] = _run(j, p, d, root, branch, push)
    except KBError as e:
        raise KBError('\n'.join(f'{t} {x}' for t, x in j['步骤']) + f'\n✗ {e}') from None
    return '\n'.join(f'{t} {x}' for t, x in j['步骤']) + f'\n{j["结果"]}'


CALL_QUEUE_MAX = 10 * 60       # 会话里按需调用最多排队 10 分钟（工具调用不能一直挂着）


def _caller():
    """发起 kb_plugin_call 的会话：开发 / 跟进会话 → 期名，Bug 修复会话 → an:<编号>；终端 / 网页 → 空。"""
    s = sess_env('KB_SESSION', '')
    return s if s.startswith('an:') else (sess_env('KB_PERIOD', '') if s == 'dev' else '')


def plugin_call_now(p, d, args=(), by='Claude'):
    """开发 / 跟进 / Bug 修复会话里按需调用时机插件（如缺多语言 key 时拉一次多语言）：和「运行一次」同一套排队，
    但就在仓库当前分支上运行——不切分支、不 pull、不 push（会话正在这个分支上干活）；只把插件改过的文件单独提交（git），
    提交说明用 plugin.json 的「提交说明」。参数：仓库=<路径>（默认本期开发仓库 / 会话的仓库）、提交=否（只运行不提交）。"""
    kw = dict(str(a).split('=', 1) for a in args if '=' in str(a))
    m = load_meta(d) if d else {}
    root = (kw.get('仓库') or m.get('开发仓库') or sess_env('KB_REPO', '')).strip()
    if not root:
        raise KBError('不知道在哪个仓库运行：带上 iteration（期名），或 args=["仓库=<路径>"]')
    rp = Path(root).expanduser()
    if not rp.is_dir():
        raise KBError(f'路径错误：{rp} 不存在')
    kind, top = hosts._top(str(rp))
    if not kind:
        raise KBError(f'{rp} 不是 git 仓库，也不是 svn 工作副本')
    rp = Path(top)
    commit = kw.get('提交', '是') not in ('否', '不', 'no', 'false', '0')
    j = {'编号': 'call', '插件': p['名称'], '状态': '运行中', '步骤': [], '结果': ''}
    branch = hosts.branch_of(str(rp), 'svn') if kind == 'svn' else _git(rp, 'branch', '--show-current').stdout.strip()
    _step(j, f'仓库 {rp}，在当前分支 {branch or "（游离）"} 上运行（不切分支、不推送）')
    lk = _wait_repo(j, rp, me=_caller(), limit=CALL_QUEUE_MAX)
    try:
        before = _git_changed(rp)
        out = run_plugins('manual', d, str(rp), branch, m.get('SDD目录', ''), only=p['id'])
        _step(j, out or '没有运行')
        first = (out or '没有运行').split('\n')[0]
        if '失败' in first or '没有运行' in first:
            raise KBError('\n'.join(x for _, x in j['步骤']) + '\n✗ 插件没跑成功，没有提交')
        after = _git_changed(rp)
        changed = sorted(f for f in set(after) | set(before) if before.get(f) != after.get(f))
        if not changed:
            j['结果'] = '完成：没有文件改动（仓库里已是平台最新的多语言 / 插件没改东西），不用提交'
        elif not commit or kind == 'svn':
            j['结果'] = (f'完成：{len(changed)} 个文件改动，' + ('svn 工作副本不自动提交' if kind == 'svn' else '按 提交=否 没有提交')
                        + '：\n' + '\n'.join(f'  {f}' for f in changed[:50]))
        else:
            msg = _commit_msg(rp, p)
            gone = [f for f in changed if not (rp / f).exists()]
            for f in [f for f in changed if f not in gone]:
                _git(rp, 'add', '--', f)
            if gone:
                _git(rp, 'rm', '--cached', '--quiet', '--', *gone)
            r = _git(rp, 'commit', '-m', msg, '--', *changed)
            if r.returncode != 0:          # 多半是仓库的提交规范（钩子）不认这个说明：拉到的文件已暂存，交给会话按规范提交，别停
                j['结果'] = (f'已拉到，{len(changed)} 个文件已 git add 暂存，但自动提交被仓库拒绝：{(r.stderr or r.stdout).strip()[-300:]}\n'
                            f'请按仓库提交规范只提交这些文件（git commit -- <这些文件>，说明用「{msg.splitlines()[0]}」并补上规范要求的内容），然后继续：\n'
                            + '\n'.join(f'  {f}' for f in changed[:50]))
                return '\n'.join(f'{t} {x}' for t, x in j['步骤']) + f'\n{j["结果"]}'
            h = _git(rp, 'rev-parse', '--short', 'HEAD').stdout.strip()
            j['结果'] = f'完成：已单独提交 {h}「{msg.splitlines()[0]}」（{len(changed)} 个文件，没有推送）'
            if d:
                log(d, '插件', f'「{p["名称"]}」按需调用（{by}）：分支 {branch} 提交 {h}（{len(changed)} 个文件）')
    finally:
        lk.release()
    return '\n'.join(f'{t} {x}' for t, x in j['步骤']) + f'\n{j["结果"]}'


def plugin_job(jid):
    j = _JOBS.get(jid)
    if not j:
        raise KBError('没有这个任务（网页服务重启过？）')
    return j
