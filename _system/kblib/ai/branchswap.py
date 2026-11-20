# From 大大王.DT
"""换分支：一期上线（合进上线分支）后，之后的开发、跟进、复测、Bug 修复改到新分支；老分支不删，和它有关的记录照旧能查。

期信息：
  开发分支 / 各端.<端>.分支  —— 始终是当前分支
  分支历史 —— [{分支, 端, 仓库, 起, 止, 原因}]：换下来的老分支（端为空 = 单仓库的期）
用到分支的地方：开发 / 复测按「开发分支」走；Bug 修复排队时记的是历史分支就改成当前分支（current_branch）；
Bug 归期（want_branches）、Jenkins 构建任务（period_branches）把历史分支也算进去。
"""
import re
from pathlib import Path

from kblib.core.common import KBError, now
from kblib.core.periods import load_meta, log, period_dir, save_meta
from kblib.infra import hosts

_BR_RE = re.compile(r'[A-Za-z0-9][\w./-]*')
_RUNNING = ('运行中', '审查中', '开发中')


def branch_history(m):
    return [x for x in (m.get('分支历史') or []) if x.get('分支')]


def history_branches(m, end=None):
    """历史分支名（end=None 不分端；'' = 单仓库的期）。"""
    return list(dict.fromkeys(x['分支'] for x in branch_history(m) if end is None or (x.get('端') or '') == end))


def now_branch(m, end=''):
    if end:
        return ((m.get('各端') or {}).get(end) or {}).get('分支', '')
    return m.get('开发分支', '')


def current_branch(m, branch, end=''):
    """branch 是这一期换下来的老分支时，换成它那一端现在的分支；否则原样返回。"""
    if not branch:
        return branch
    for x in reversed(branch_history(m)):
        if x['分支'] == branch and (not end or (x.get('端') or '') == end):
            cur = now_branch(m, x.get('端') or '')
            return cur or branch
    return branch


def record_switch(m, old, new, end='', repo='', reason=''):
    """old → new：老分支写进分支历史（同一端最后一条就是它时不重复记）。只改 m，不保存。"""
    if not old or old == new:
        return False
    hs = m.setdefault('分支历史', [])
    mine = [x for x in hs if (x.get('端') or '') == end]
    if mine and mine[-1].get('分支') == old and not mine[-1].get('止'):
        mine[-1].update(止=now(), 原因=reason or mine[-1].get('原因', ''))
        return True
    hs.append({'分支': old, '端': end, '仓库': repo, '起': mine[-1].get('止', '') if mine else '', '止': now(), '原因': reason})
    return True


def stash_for_switch(root, target, d=None, why='切分支'):
    """切分支被未提交改动挡住时不再停下：git stash 收起（说明里写期名、时间、原分支），给了期就记一条非阻断问题
    （stash 名、提交号、原分支、文件数、恢复命令），方便以后恢复。没有改动返回 None；stash 失败抛 KBError。
    → {名称, 提交, 原分支, 文件数, 问题, 文字}"""
    g = lambda *a, t=120: hosts.git(str(root), *a, timeout=t)
    files = [x for x in g('status', '--porcelain', '--untracked-files=no').stdout.splitlines() if x.strip()]
    if not files:
        return None
    old = g('branch', '--show-current').stdout.strip() or g('rev-parse', '--short', 'HEAD').stdout.strip() or '?'
    name = d.name if d is not None else ''
    label = f'kb: {name or "知识库"} {now()} 原分支 {old} → {target}（{why}）'
    r = g('stash', 'push', '-m', label, t=300)
    if r.returncode:
        raise KBError(f'git stash 失败：{(r.stderr or r.stdout).strip()[-300:]}')
    ref = g('rev-parse', '--short=10', 'stash@{0}').stdout.strip()
    rp = Path(str(root)).name
    text = (f'{rp} 原分支 {old} 有 {len(files)} 个未提交改动，已 git stash「{label}」（{ref}）；'
            f'恢复：在 {old} 上 git stash apply {ref}')
    iid = ''
    if d is not None:
        try:
            from kblib.ai.devices import _add_issue
            iid, _ = _add_issue(d, f'{rp}：{why}前收起了 {len(files)} 个未提交改动（原分支 {old}）',
                                f'仓库：{root}\nstash：{label}\n提交号：{ref}\n原分支：{old}\n目标分支：{target}\n文件数：{len(files)}\n'
                                + '\n'.join(files[:30]) + f'\n\n恢复：git -C {root} switch {old} && git stash apply {ref}（确认无用后 git stash drop）',
                                '其他', '确认这些改动还要不要：要就切回原分支 git stash apply 恢复，不要就 git stash drop', source='知识库',
                                key=f'stash:{ref}')
            log(d, '收起改动', text + (f'（待处理 {iid}）' if iid else ''))
        except Exception:      # noqa: BLE001 记录失败不耽误切分支
            pass
    return {'名称': label, '提交': ref, '原分支': old, '文件数': len(files), '问题': iid, '文字': text}


def _check_name(b, what):
    if not _BR_RE.fullmatch(b or '') or '..' in b or b.endswith(('/', '.lock')):
        raise KBError(f'{what}不合法：{b}')


def _remote(root):
    rs = hosts.git(root, 'remote').stdout.split()
    return 'origin' if 'origin' in rs else (rs[0] if rs else '')


def _change_id(g, sha):
    m = re.search(r'^Change-Id:\s*(I[0-9a-f]{8,})', g('log', '-1', '--format=%B', sha).stdout, re.M)
    return m.group(1) if m else ''


def _gerrit(root, remote):
    """远端是 Gerrit（ssh://user@host:29418/project）→ (ssh 参数, 项目)；不是返回 None。"""
    url = hosts.git(root, 'remote', 'get-url', remote).stdout.strip()
    m = re.match(r'ssh://(?:([^@/]+)@)?([^:/]+):(\d+)/(.+?)(?:\.git)?/?$', url)
    if not m or m.group(3) != '29418':
        return None
    return ['-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '-p', m.group(3), (m.group(1) + '@' if m.group(1) else '') + m.group(2)], m.group(4)


def _in_review(root, remote, g, shas):
    """Gerrit 仓库：推上去是送审（refs/for），远端分支要合进去才更新。提交的 Change-Id 在 Gerrit 查得到 = 已推送送审。
    → {sha: 'Gerrit 编号 状态'}；查不到的不在里面。"""
    gr = _gerrit(root, remote)
    if not gr or hosts.parse(root)['远程']:
        return {}
    import subprocess
    out = {}
    for sha in shas:
        cid = _change_id(g, sha)
        if not cid:
            continue
        try:
            r = subprocess.run(['ssh', *gr[0], 'gerrit', 'query', '--format=TEXT', f'project:{gr[1]}', cid],
                               capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            continue
        num = re.search(r'^\s*number:\s*(\d+)', r.stdout, re.M) or re.search(r'/\+/(\d+)', r.stdout)
        st = re.search(r'^\s*status:\s*(\w+)', r.stdout, re.M)
        if r.returncode == 0 and st:
            out[sha] = f'Gerrit {num.group(1) if num else cid[:9]} {st.group(1)}'
    return out


def _busy(d, m, end):
    if end:
        x = (m.get('各端开发') or {}).get(end) or {}
        if x.get('状态') in _RUNNING and x.get('pid'):
            from kblib.stack.dev import _alive
            if _alive(x['pid']):
                return f'{end}的开发会话正在运行'
        return ''
    from kblib.ai.devsession import dev_session
    s = dev_session(d.name) or {}
    return '后台会话正在运行' if s.get('状态') == '运行中' else ''


def swap_info(name):
    """网页「⇄ 换分支」弹窗用：各端（单仓库的期 端 = ''）的仓库、当前分支、历史。"""
    d = period_dir(name)
    m = load_meta(d)
    rows = []
    if m.get('各端'):
        for e, v in m['各端'].items():
            if v.get('仓库') and v.get('版本库', 'git') == 'git':
                rows.append({'端': e, '仓库': v['仓库'], '分支': v.get('分支', '')})
    elif m.get('开发仓库'):
        rows.append({'端': '', '仓库': m['开发仓库'], '分支': m.get('开发分支', '')})
    return {'端': rows, '历史': branch_history(m)}


def branch_swap(period, new, base='', reason='', end='', by='用户（网页）', carry=False):
    """网页「⇄ 换分支」：检查（没有会话在跑、老分支已推到远端；未提交改动自动 git stash 并记待处理）→ 建 / 切到新分支 → 老分支写进分支历史。
    base：从哪个分支拉出（默认老分支；可填 release-x 或 origin/release-x）。新分支已存在就直接切过去。
    Gerrit 仓库送审中（还没合进远端老分支）的提交算已推送；carry=True 把它们 cherry-pick 到新分支（不推送）。"""
    d = period_dir(period)
    m = load_meta(d)
    new, base, reason, end = (new or '').strip(), (base or '').strip(), (reason or '').strip(), (end or '').strip()
    _check_name(new, '新分支名')
    if end:
        v = (m.get('各端') or {}).get(end)
        if not v or not v.get('仓库'):
            raise KBError(f'这一期没有「{end}」端')
        if v.get('版本库', 'git') != 'git':
            raise KBError(f'{end}不是 git 仓库（{v.get("版本库")}），换分支只支持 git')
        root = v['仓库']
    else:
        if m.get('各端'):
            raise KBError('这一期走全栈：选一个端换分支')
        root = m.get('开发仓库') or ''
        if not root or not Path(root).is_dir():
            raise KBError('这一期还没确认开发仓库（开始开发后才有），没法换分支')
    busy = _busy(d, m, end)
    if busy:
        raise KBError(f'{busy}，先等它结束或停止后再换分支')
    g = lambda *a, t=60: hosts.git(root, *a, timeout=t)
    cur = g('branch', '--show-current').stdout.strip()
    old = now_branch(m, end) or cur
    if not old:
        raise KBError('认不出现在的分支（仓库处在游离 HEAD）：先切回开发分支')
    if new == old:
        raise KBError(f'新分支和现在的分支一样：{new}')
    remote = _remote(root)
    if not remote:
        raise KBError(f'{Path(root).name} 没有远端，没法确认老分支 {old} 已推送；老分支要先推到远端再换')
    f = g('fetch', remote, '--prune', t=300)
    if f.returncode:
        raise KBError(f'git fetch 失败：{(f.stderr or f.stdout).strip()[-400:]}')
    if g('rev-parse', '--verify', '--quiet', f'refs/heads/{old}').returncode == 0:
        if g('rev-parse', '--verify', '--quiet', f'refs/remotes/{remote}/{old}').returncode:
            raise KBError(f'老分支 {old} 还没推到远端（{remote} 上没有）：先推送再换分支，免得老分支的提交只留在本机')
        ahead = g('rev-list', '--reverse', '--no-merges', f'{remote}/{old}..{old}').stdout.split()
        review = _in_review(root, remote, g, ahead) if ahead else {}
        left = [x for x in ahead if x not in review]
        if left:
            raise KBError(f'老分支 {old} 有 {len(left)} 个提交还没推到 {remote}（' + '、'.join(x[:10] for x in left[:5])
                          + '）：先推送（Gerrit 推 refs/for 送审也行）再换分支')
    else:
        ahead, review = [], {}
    has = lambda ref: g('rev-parse', '--verify', '--quiet', ref).returncode == 0
    stashed = stash_for_switch(root, new, d, '换分支')
    if has(f'refs/heads/{new}'):
        r, how = g('switch', new), f'切到已有的本地分支 {new}'
    elif has(f'refs/remotes/{remote}/{new}'):
        r, how = g('switch', '--track', f'{remote}/{new}'), f'从 {remote}/{new} 拉下来并切过去'
    else:
        b = base or old
        _check_name(b, '起点分支')
        start = next((x for x in ((f'{remote}/{b[len(remote) + 1:]}',) if b.startswith(remote + '/') else (b, f'{remote}/{b}')) if has(x)), '')
        if not start:
            raise KBError(f'起点分支「{b}」本地和 {remote} 都没有')
        if b == old and has(f'refs/remotes/{remote}/{old}'):
            start = f'{remote}/{old}'            # 老分支已核对和远端一致，从远端那份拉
        r, how = g('switch', '--no-track', '-c', new, start), f'从 {start} 新建 {new}'
    if r.returncode:
        if stashed:
            g('stash', 'pop')
        raise KBError(f'切换失败：{(r.stderr or r.stdout).strip()[:500]}' + ('（收起的改动已还原）' if stashed else ''))
    picked, skip, fail = [], [], ''
    if ahead:
        for sha in ahead:
            cid = _change_id(g, sha)
            if cid and g('log', '-1', '--format=%h', f'--grep=Change-Id: {cid}', 'HEAD').stdout.strip():
                skip.append(sha[:10])                       # 新分支上已经有了
            elif carry and not fail:
                c = g('cherry-pick', sha, t=120)
                if c.returncode:
                    g('cherry-pick', '--abort')
                    fail = f'{sha[:10]}（冲突，已撤销这次 cherry-pick：{(c.stderr or c.stdout).strip()[:200]}）'
                else:
                    picked.append(sha[:10])
    pend = [x for x in ahead if x[:10] not in skip and x[:10] not in picked]
    m = load_meta(d)
    reason = reason or f'换到 {new}'
    record_switch(m, old, new, end, root, reason)
    if end:
        m['各端'][end]['分支'] = new
        x = (m.get('各端开发') or {}).get(end)
        if x:
            x['分支'] = new
            for k in ('已推送', '推送时间'):     # 下次验收推新分支
                x.pop(k, None)
    else:
        m['开发分支'] = new
        if m.get('开发会话'):
            m['开发会话']['分支'] = new
    save_meta(d, m)
    who = f'{end}：' if end else ''
    log(d, '换分支', f'{who}{old} → {new}（{how}；原因：{reason}；{by}）。老分支 {old} 保留，Jenkins 构建、Bug、记录继续能查')
    extra = []
    if picked:
        extra.append(f'送审中的提交已 cherry-pick 到 {new}（没推送）：{"、".join(picked)}')
    if fail:
        extra.append(f'cherry-pick 停在 {fail}；没带过来的要手动处理')
    if pend and not carry:
        extra.append(f'老分支上送审中、还没合进去的提交没带过来：' + '、'.join(f'{x[:10]}（{review.get(x, "")}）' for x in pend))
    if stashed:
        extra.append(stashed['文字'] + (f'（已记待处理 {stashed["问题"]}）' if stashed['问题'] else ''))
    if extra:
        log(d, '换分支', '；'.join(extra))
    _rematch_builds(d.name)
    return (f'{who}已从 {old} 换到 {new}（{how}）。之后的开发、跟进、复测、Bug 修复都用 {new}；'
            f'老分支 {old} 没删，它的构建任务和 Bug 照旧保留' + ''.join(f'\n{x}' for x in extra))


def _rematch_builds(name):
    try:
        from kblib.plugins.builds import period_jobs
        period_jobs(name)
    except Exception:
        pass


def bug_branch(e, branch):
    """Bug 修复用的分支：归属的期 / 事项换过分支、branch 是老分支时，改成现在的分支（已修完、已提交的不经过这里）。"""
    o = (e or {}).get('归属') or {}
    if not branch or o.get('类型') not in ('期', '事项') or not o.get('名称'):
        return branch
    try:
        return current_branch(load_meta(period_dir(o['名称'])), branch)
    except (KBError, OSError, ValueError):
        return branch


def _last_commit(root, branch):
    if not root or not branch or hosts.parse(root)['远程'] or not Path(root).is_dir():
        return ''                                   # 远程仓库不在巡检里连 ssh
    r = hosts.git(root, 'log', '-1', '--format=%ci', f'refs/heads/{branch}', '--', timeout=15)
    return r.stdout.strip()[:16] if r.returncode == 0 else ''


def swap_hint(m):
    """管家：一期验收 / 完成（上线分支已合）后，当前分支上又有新提交 → [(端, 分支, 提交时间)]，基准时间。没有返回 ([], '')。"""
    from kblib.core.periods import done_time
    out = []
    st = m.get('全栈') or {}
    if st.get('状态') == '已验收' and st.get('验收时间'):
        t0 = st['验收时间']
        for e, v in (m.get('各端') or {}).items():
            if v.get('版本库', 'git') != 'git':
                continue
            last = max((x.get('止') or '' for x in branch_history(m) if (x.get('端') or '') == e), default='')
            c = _last_commit(v.get('仓库', ''), v.get('分支', ''))
            pushed = str(((m.get('各端开发') or {}).get(e) or {}).get('已推送') or '').rpartition('@')[2]
            if pushed and c and hosts.git(v['仓库'], 'rev-parse', '--short', f'refs/heads/{v["分支"]}', timeout=15).stdout.strip().startswith(pushed[:7]):
                continue                            # 还是验收时推上去的那个提交（推送前 rebase 会改提交时间）
            if c and c > t0 and last < t0:
                out.append((e, v['分支'], c))
        return out, t0
    if m.get('状态') != '已完成' or m.get('各端'):
        return [], ''
    t0 = done_time(m)
    last = max((x.get('止') or '' for x in branch_history(m) if not x.get('端')), default='')
    c = _last_commit(m.get('开发仓库', ''), m.get('开发分支', ''))
    return ([('', m['开发分支'], c)] if t0 and c and c > t0 and last < t0 else []), t0
