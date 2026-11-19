"""改完代码自动推送：会话正常结束、全栈某端审查通过、Bug 修复合回后，由知识库把开发分支推到远端（AI 自己不 push）。

开关：「⚙ 设置 → 常用 → 自动推送」总开关（设置「自动推送」，默认开，关了全局都不推）；
      每一期 meta「自动推送」（默认开，关掉后这一期和以前一样只本地提交）。生效 = 总开关开 且 这一期没关。
安全底线：不强推；HEAD 游离、和远端冲突（rebase 不过）、远端有新提交但工作区有没提交的改动（没法 rebase）、
      push 被拒 / 没权限都不推，在这一期记录里写原因，管家出「推送失败：xx」卡片。
      只推已提交的内容，工作区有没提交的改动（常是同仓库另一会话正在改）不挡推送。
失败后自动补推：管家巡检（retry_failed，按 5→10→20→40→80 分钟退避）和同仓库任一会话结束时，重推最近一次失败的期。svn 不自动提交（只能你点），本地目录 / 没远端跳过。
Gerrit 仓库（ssh 端口 29418 或地址带 gerrit）推 refs/heads/<分支>:refs/for/<分支> 送审。
每次推送在 meta「推送记录」留一条：时间 · 时机 · 仓库 · 分支 · 提交数 · sha · 结果 · 原因。"""
import re
from pathlib import Path
from kblib.core.common import KBError, PERIODS, load_settings, now, save_settings
from kblib.core.periods import load_meta, log, period_dir, period_names, save_meta
from kblib.infra import hosts

KEEP = 30           # 每期留最近几条推送记录


class PushError(KBError):
    """不能推 / 推不上：原因写给人看。"""


# ---------------------------------------------------------------- 开关
def autopush_global():
    return load_settings().get('自动推送', True) is not False


def autopush_set_global(on, by='用户（网页）'):
    """「⚙ 设置 → 常用 → 自动推送」总开关。"""
    s = load_settings()
    s['自动推送'] = bool(on)
    save_settings(s)
    return '自动推送总开关已打开：各期改完代码后知识库自动推到远端（每期可单独关）' if on else \
        '自动推送总开关已关闭：所有期都只在本地提交，和以前一样（全栈验收时照旧推送）'


def autopush_on(m):
    return autopush_global() and m.get('自动推送', True) is not False


def push_tip(m, what='这一期'):
    """给后台会话提示词：不 push，并告诉它自动推送开关现在的状态，回复时照实说（不要只说「没有推送」让人以为没推）。"""
    if autopush_on(m):
        return (f'不 push：{what}的「自动推送」开着，会话结束后知识库自动把提交推到远端；'
                '回复 / 结论里写「已提交 <提交号>，会话结束后自动推送」，不要说成「没有推送」')
    why = '总开关（⚙ 设置）关着' if not autopush_global() else f'{what}的开关关着'
    return (f'不 push：「自动推送」现在关着（{why}），提交只留在本地；'
            '回复 / 结论里写「已提交 <提交号>，只在本地，要推送在网页打开自动推送或手动推」')


def autopush_set(period, on, by='用户（网页）'):
    """每一期的「改完自动推送」开关。"""
    d = period_dir(period)
    m = load_meta(d)
    m['自动推送'] = bool(on)
    save_meta(d, m)
    log(d, '自动推远程', ('打开' if on else '关闭') + f'（{by}）')
    tail = '' if autopush_global() else '；但「⚙ 设置」里的总开关关着，现在不会推'
    return (f'「{d.name}」自动推远程：已打开，改完代码后自动推到远端' if on else f'「{d.name}」自动推远程：已关闭，只在本地提交') + tail


def autopush_info(m):
    """给网页：开关状态 + 最近一条推送记录。"""
    rec = (m.get('推送记录') or [])[-1:] or [None]
    return {'开': m.get('自动推送', True) is not False, '总开关': autopush_global(), '生效': autopush_on(m), '最近': rec[0]}


# ---------------------------------------------------------------- git 推送（只推一个分支，不强推）
def _remote(root):
    rs = hosts.git(root, 'remote').stdout.split()
    return 'origin' if 'origin' in rs else (rs[0] if rs else '')


def is_gerrit(root, remote):
    url = hosts.git(root, 'remote', 'get-url', remote).stdout.strip()
    return bool(re.match(r'ssh://[^/]*:29418/', url)) or 'gerrit' in url.lower()


def _hint(err):
    low = err.lower()
    if any(k in low for k in ('rejected', 'fetch first', 'non-fast-forward')):
        return '远端有别人的新提交，和本地冲突：在仓库里 git pull --rebase 解决后再推'
    if any(k in low for k in ('denied', 'authentication', 'could not read', 'permission', 'unauthorized', '403')):
        return '没有推送权限或要登录：先在那台机器上手动 git push 一次'
    return ''


def _same_repo(a, b):
    try:
        return bool(a and b) and Path(str(a)).resolve() == Path(str(b)).resolve()
    except OSError:
        return False


def _dirty_note(root, me=''):
    """工作区没提交的改动是谁的：列前几个文件 + 同仓库正在运行的其他会话（me = 自己这一期，不算）。"""
    from kblib.infra.devlock import session_alive
    st = hosts.git(root, 'status', '--porcelain', '--untracked-files=no').stdout.splitlines()
    files = [ln[3:].strip() for ln in st if ln.strip()]
    if not files:
        return ''
    txt = '未提交：' + '、'.join(files[:5]) + (f' 等 {len(files)} 个文件' if len(files) > 5 else '')
    who = []
    for n in period_names():
        if n == me:
            continue
        try:
            m = load_meta(PERIODS / n)
        except Exception:                 # noqa: BLE001
            continue
        s = m.get('开发会话') or {}
        if _same_repo(s.get('仓库') or m.get('开发仓库'), root) and session_alive(n):
            who.append(n)
    if who:
        txt += '（可能是另一会话「' + '」「'.join(who[:3]) + '」正在改，等它提交后会自动补推）'
    return txt


def push_git(root, branch='', since='', me=''):
    """把本地分支 branch（空 = 当前分支）推到远端：先 git_catch_up（远端有新提交先 rebase），Gerrit 推 refs/for。
    → {结果: 已推送 / 无新提交 / 没有远端, 远端, 分支, 提交数, sha, 推前, Gerrit}；不能推、推不上抛 PushError。
    只推 refs/heads/<分支> 上已提交的内容，工作区有没提交的改动不挡；只有远端有新提交要 rebase 时由 git_catch_up 拒绝。
    me：发起推送的期名（失败原因里认「另一会话」时排除自己）。"""
    g = lambda *a, t=60: hosts.git(root, *a, timeout=t)
    cur = g('branch', '--show-current').stdout.strip()
    b = branch or cur
    if not b:
        raise PushError('HEAD 游离（不在任何分支上），不知道推哪个分支')
    if g('rev-parse', '--verify', '--quiet', f'refs/heads/{b}').returncode:
        raise PushError(f'本地没有分支 {b}')
    remote = _remote(root)
    out = {'远端': remote, '分支': b, '提交数': 0, 'sha': '', '推前': '', 'Gerrit': False}
    if not remote:
        return dict(out, 结果='没有远端')
    try:
        out['推前'] = hosts.git_catch_up(root, remote, b)
    except KBError as e:
        msg = str(e).removeprefix('推送前 ')
        if '未提交的改动' in msg:
            note = _dirty_note(root, me)
            msg += ('；' + note) if note else ''
        raise PushError(msg) from None
    rb = f'refs/remotes/{remote}/{b}'
    if not g('rev-parse', '--verify', '--quiet', rb).returncode:
        rng = [f'{rb}..refs/heads/{b}']
    elif since:
        rng = [f'{since}..refs/heads/{b}']
    else:
        rng = [f'refs/heads/{b}', '--not', f'--remotes={remote}']
    n = int(g('rev-list', '--count', *rng).stdout.strip() or 0)
    out.update(提交数=n, sha=g('rev-parse', '--short', f'refs/heads/{b}').stdout.strip())
    if not n:
        return dict(out, 结果='无新提交')
    gerrit = is_gerrit(root, remote)
    out['Gerrit'] = gerrit
    dst = f'refs/for/{b}' if gerrit else f'refs/heads/{b}'
    r = hosts.run(root, f'GIT_TERMINAL_PROMPT=0 git push {"" if gerrit else "--follow-tags -u "}{remote} refs/heads/{b}:{dst}', timeout=300)
    if r.returncode:
        err = (r.stderr or r.stdout).strip()
        if gerrit and 'no new changes' in err.lower():
            return dict(out, 结果='已推送', 说明='Gerrit 上已有这些提交（之前送审过）')
        h = _hint(err)
        raise PushError('git push 失败' + (f'（{h}）' if h else '') + f'：{err[-400:]}')
    return dict(out, 结果='已推送')


# ---------------------------------------------------------------- 时机：会话结束 / 审查通过 / Bug 修复合回
def _record(d, m, rec):
    xs = m.setdefault('推送记录', [])
    xs.append(rec)
    m['推送记录'] = xs[-KEEP:]


def autopush(d, root, branch, when, since='', m=None):
    """按这一期的开关自动推送。m 由调用方传入时只改 m 不保存（调用方保存），否则自己读写。
    → 推送结果 dict（没开 / 不是 git / 无新提交返回的 结果 不是「已推送」）；不抛错，失败写记录。"""
    own = m is None
    if own:
        m = load_meta(d)
    if not autopush_on(m):
        return {'结果': '没开'}
    if not root or hosts.vcs(str(root)) != 'git':
        return {'结果': '不是 git'}        # svn 只能你点提交；本地目录不推
    rec = {'时间': now(), '时机': when, '仓库': str(root), '分支': branch or ''}
    try:
        r = push_git(str(root), branch, since, me=d.name)
    except (KBError, OSError) as e:
        rec.update(结果='失败', 原因=str(e)[:500])
        _record(d, m, rec)
        log(d, '自动推送', f'推送失败：{when} · {Path(str(root)).name}@{branch or "当前分支"} · {rec["原因"][:300]}')
        if own:
            save_meta(d, m)
        return rec
    rec.update(分支=r['分支'], 提交数=r['提交数'], sha=r['sha'], 结果=r['结果'], 远端=r['远端'],
               **({'Gerrit': True} if r['Gerrit'] else {}), **({'说明': r['说明']} if r.get('说明') else {}))
    if r['结果'] == '已推送':
        _record(d, m, rec)
        how = f'{r["远端"]} refs/for/{r["分支"]}（Gerrit 送审）' if r['Gerrit'] else f'{r["远端"]}/{r["分支"]}'
        log(d, '自动推送', f'{when} · {Path(str(root)).name}@{r["分支"]} · {r["提交数"]} 个提交 · {r["sha"]}（推到 {how}'
            + (f'；推送前：{r["推前"]}' if r['推前'] and r['推前'] != '远端没有新提交' else '') + '）')
        if own:
            save_meta(d, m)
    elif r['结果'] == '没有远端':
        log(d, '自动推送', f'{when} · {Path(str(root)).name}@{r["分支"]}：仓库没有远端，没推送（提交留在本地）')
    return rec


def push_status(m, root, branch=''):
    """「📍 代码」后面的一句：已推送 / 本地领先 N 个（不 fetch，按本地记得的远端分支算；Gerrit 送审的按推送记录算）。"""
    try:
        if not root or not Path(str(root)).is_dir() or hosts.vcs(str(root)) != 'git':
            return ''
        g = lambda *a: hosts.git(str(root), *a, timeout=10)
        b = branch or g('branch', '--show-current').stdout.strip()
        remote = _remote(str(root))
        if not b or not remote:
            return ''
        tip = g('rev-parse', '--short', f'refs/heads/{b}').stdout.strip()
        last = next((x for x in reversed(m.get('推送记录') or []) if x.get('仓库') == str(root) and x.get('分支') == b), None)
        if last and last.get('结果') == '已推送' and tip and str(last.get('sha', '')).startswith(tip[:7]):
            return '已推送' + ('（Gerrit 送审）' if last.get('Gerrit') else '')
        rb = f'refs/remotes/{remote}/{b}'
        if g('rev-parse', '--verify', '--quiet', rb).returncode:
            return '远端没有这个分支'
        n = int(g('rev-list', '--count', f'{rb}..refs/heads/{b}').stdout.strip() or 0)
        return f'本地领先 {n} 个' if n else '已推送'
    except (OSError, ValueError, KBError):
        return ''


def fail_cards():
    """管家卡片：各期最近一次自动推送失败（之后又推成功了就不再提）。"""
    out = []
    for n in period_names():
        try:
            m = load_meta(PERIODS / n)
        except Exception:                 # noqa: BLE001
            continue
        xs = m.get('推送记录') or []
        if not xs or xs[-1].get('结果') != '失败' or not autopush_on(m):
            continue
        x = xs[-1]
        why = (x.get('原因') or '').split('：')[0][:40]
        out.append({'id': f'push-fail:{n}:{x["时间"]}', '类型': '待你', '标题': f'推送失败：{why}',
                    '说明': f'「{n}」{x.get("时机", "")} · {Path(x.get("仓库", "")).name}@{x.get("分支", "")}：{(x.get("原因") or "")[:200]}',
                    '为什么': '知识库不强推，会定时自动补推（推成功这张卡片就消失）；一直推不上就处理好（解决冲突 / 登录）后在仓库里手动 git push；不想自动推就在这一期关掉「自动推送」。'
                    + (f'（已自动重推 {x["重试"]} 次，最近 {x.get("重试时间", "")}）' if x.get('重试') else ''),
                    '按钮': '去看看', '链接': '#/p/' + n})
    return out


# ---------------------------------------------------------------- 失败后补推：管家巡检 / 同仓库会话结束
RETRY_BASE = 5      # 分钟：第 n 次重试距上次至少 5·2^(n-1) 分钟，最多 80 分钟一次


def _minutes_since(t):
    from datetime import datetime
    try:
        return (datetime.now() - datetime.strptime(t[:16], '%Y-%m-%d %H:%M')).total_seconds() / 60
    except (TypeError, ValueError):
        return 1e9


def retry_failed(root='', force=False, skip=''):
    """最近一次自动推送失败、开着自动推送的期：重推一次。root 只补推这个仓库的（会话结束时顺带）；force 不看退避；skip 跳过这一期（刚自己推过）。
    推成功追加一条「已推送」记录（失败卡片随之消失）；还失败只更新那条失败记录的 重试 / 原因 / 重试时间。→ [(期名, 一句结果)]。"""
    if not autopush_global():
        return []
    out = []
    for n in period_names():
        try:
            d = PERIODS / n
            m = load_meta(d)
        except Exception:                 # noqa: BLE001
            continue
        xs = m.get('推送记录') or []
        if n == skip or not xs or xs[-1].get('结果') != '失败' or not autopush_on(m):
            continue
        x = xs[-1]
        repo, branch = x.get('仓库') or '', x.get('分支') or ''
        if not repo or not branch or (root and not _same_repo(repo, root)):
            continue                      # 没记分支（HEAD 游离时）不猜，免得推错分支
        k = int(x.get('重试') or 0)
        if not force and _minutes_since(x.get('重试时间') or x.get('时间')) < RETRY_BASE * 2 ** min(k, 4):
            continue
        if not Path(repo).is_dir() or hosts.vcs(repo) != 'git':
            continue
        when = '补推（' + (x.get('时机') or '') + '）'
        try:
            r = push_git(repo, branch, me=n)
        except (KBError, OSError) as e:
            m = load_meta(d)              # 推送可能花了一会儿：重读再改，免得盖掉别人的写入
            ys = m.get('推送记录') or []
            if ys and ys[-1].get('时间') == x.get('时间') and ys[-1].get('结果') == '失败':
                ys[-1].update(重试=k + 1, 重试时间=now(), 原因=str(e)[:500])
                save_meta(d, m)
            out.append((n, f'补推失败（第 {k + 1} 次）：{str(e)[:120]}'))
            continue
        m = load_meta(d)
        rec = {'时间': now(), '时机': when, '仓库': repo, '分支': r['分支'], '提交数': r['提交数'], 'sha': r['sha'],
               '结果': r['结果'] if r['结果'] != '无新提交' else '已推送', '远端': r['远端'],
               **({'Gerrit': True} if r['Gerrit'] else {}),
               **({'说明': r.get('说明') or '远端已有这些提交'} if r['结果'] == '无新提交' or r.get('说明') else {})}
        _record(d, m, rec)
        save_meta(d, m)
        how = f'{r["远端"]} refs/for/{r["分支"]}（Gerrit 送审）' if r['Gerrit'] else f'{r["远端"]}/{r["分支"]}'
        log(d, '自动推送', f'{when} · {Path(repo).name}@{r["分支"]} · {r["提交数"]} 个提交 · {r["sha"]}（推到 {how}）'
            if r['结果'] == '已推送' else f'{when} · {Path(repo).name}@{r["分支"]}：远端已有这些提交，不用再推')
        out.append((n, f'补推成功：{Path(repo).name}@{r["分支"]} {r["提交数"]} 个提交'))
    return out


def autopush_view(name):
    """网页（开发面板 / 全栈 / 事项）：开关 + 「📍 代码」后面的推送状态（单仓库一句，全栈按端）。"""
    d = period_dir(name)
    m = load_meta(d)
    s = m.get('开发会话') or {}
    root = m.get('开发仓库') or s.get('仓库') or ''
    branch = s.get('分支', '') if s.get('仓库') == root else ''
    ends = {e: push_status(m, x.get('仓库'), x.get('分支') or '') for e, x in (m.get('各端开发') or {}).items()
            if x.get('仓库') and x.get('版本库') not in ('svn', '本地')}
    return {'自动推送': autopush_info(m), '推送状态': push_status(m, root, branch) if root else '', '各端推送状态': ends}
