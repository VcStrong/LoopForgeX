"""时机：知识库在哪些时候更新代码、哪些时候能跑插件——同一份清单（TIMINGS），设置页、插件对话框、调用点共用。

每个时机：
  代码更新  拉最新 = hosts.sync（fetch，干净且有上游就 pull --rebase）；只fetch = 只 fetch（没检出的目标分支照样快进）；
            不动 = 什么都不做；必做 = 锁定，不能改（推送 / svn 提交前必须先合上远端）。
  插件      能挂的时机，插件在「先更新代码、再跑插件」的顺序里运行。
设置：设置.json「时机」{代码更新: {时机id: 选项}}（全局）；「工程」{路径: {代码更新: 选项}}（按工程，对所有可选的时机生效）。
优先级：工程 > 全局 > 内置默认。工程选的值不在某个时机的可选项里（如「各端再开工」不能「拉最新」）就按全局走。
安全保护写死在 hosts.sync，任何设置都绕不过：有未提交改动只 fetch、仓库被别的会话占着只 fetch、冲突撤销、worktree 不动主工作区。
"""
from pathlib import Path

from kblib.core.common import KBError, load_settings, now, save_settings
from kblib.infra import hosts
from kblib import lazy
_fix = lazy('kblib.bugs.fix')            # 上层模块：查仓库占用，用到时才 import
_periods = lazy('kblib.core.periods')
_repos = lazy('kblib.core.repos')

PULL, FETCH, KEEP, MUST = '拉最新', '只fetch', '不动', '必做'
POLICY_TEXT = {PULL: '拉最新（fetch 后 pull --rebase）', FETCH: '只 fetch（不动工作区）', KEEP: '不动（不碰仓库）', MUST: '必做（锁定）'}
_ALL = [PULL, FETCH, KEEP]

TIMINGS = [
    {'id': 'plan', '名称': '出方案前', '说明': '全栈「出方案」启动前，各端仓库（这一端在开发 / 审查中时只 fetch）', '选项': _ALL, '默认': PULL},
    {'id': 'dev_start', '名称': '单仓库开发开始前', '说明': '网页点「开始开发 / 继续开发」后、启动后台 AI 之前（刚切过分支的按「切开发分支」）',
     '选项': _ALL, '默认': PULL, '插件': True, '旧名': ['开发开始'], '事件': '开发开始'},
    {'id': 'switch', '名称': '切开发分支', '说明': '网页确认切到开发分支（或沿用当前分支）时：切换前后各更新一次，冲突就停下',
     '选项': [PULL, FETCH], '默认': PULL, '原因': '切分支时一定要碰仓库，没有「不动」'},
    {'id': 'end_start', '名称': '各端首轮开工前', '说明': '全栈某一端第一次开工（记下审查起点）之前', '选项': _ALL, '默认': PULL,
     '插件': True, '事件': '开发开始'},
    {'id': 'end_round', '名称': '各端再开工', '说明': '全栈某一端按审查意见修改 / 联调再开工时', '选项': [FETCH, KEEP], '默认': KEEP,
     '原因': '不能「拉最新」：别人的提交会混进「起点..HEAD」，审查会看错'},
    {'id': 'bug_analyze', '名称': 'Bug 分析前', '说明': 'Bug 分析会话启动前（有会话在改这个仓库时只 fetch）', '选项': _ALL, '默认': PULL},
    {'id': 'bug_fix', '名称': 'Bug 修复前', '说明': '修复会话开工前（worktree 模式只 fetch 并快进目标分支，不动主工作区）', '选项': _ALL, '默认': PULL,
     '插件': True, '事件': 'Bug修复前'},
    {'id': 'retest', '名称': '复测前', '说明': '网页点「复测」后、启动复测会话之前', '选项': _ALL, '默认': KEEP,
     '插件原因': '复测前挂插件后续再做'},
    {'id': 'run_device', '名称': '运行到手机前', '说明': '「▶ 运行到手机」编译前：用工作区现有代码编译（不切分支）；仓库被别的会话占着时只 fetch',
     '选项': _ALL, '默认': KEEP},
    {'id': 'push_task', '名称': '推动解决前', '说明': '网页「推动解决 / 跟进」新开会话之前', '选项': _ALL, '默认': KEEP},
    {'id': 'publish', '名称': '验收推送 / svn 提交前', '说明': '全栈验收推代码前 rebase 上远端新提交；svn 提交前 svn update，合不上就不推',
     '选项': [MUST], '默认': MUST, '原因': '推上去前必须先合上远端的新提交，否则推不上去 / svn 提交冲突', '插件原因': '推送前挂插件后续再做'},
    {'id': 'bug_svn', '名称': 'Bug 提交 svn 前', '说明': 'Bug 修复验收后 svn 提交前 svn update', '选项': [MUST], '默认': MUST,
     '原因': 'svn 提交前必须先合上服务器的改动'},
    {'id': 'done', '名称': '开发完成后', '说明': '期状态改为「已完成」时（AI 用 kb_set_status 标完成，或在网页 / 终端修改）', '选项': [KEEP], '默认': KEEP,
     '原因': '开发已经结束，不再更新代码；插件改动的文件请检查后提交', '插件': True, '旧名': ['开发完成'], '事件': '开发完成'},
    {'id': 'manual', '名称': '手动运行一次', '说明': '插件「运行一次」（网页、终端 kb plugin run）：切到目标分支、拉到最新再运行，按选择提交推送',
     '选项': [PULL], '默认': PULL, '原因': '手动运行总是先切分支、拉最新，保证插件改动基于最新代码', '插件': '总是', '旧名': ['手动运行'], '事件': '手动运行'},
]
_BY_ID = {t['id']: t for t in TIMINGS}
PLUGIN_TIMINGS = [t['id'] for t in TIMINGS if t.get('插件') is True]     # 能在设置里挂插件的时机（手动运行不用挂）
_ALIAS = {a: t['id'] for t in TIMINGS for a in t.get('旧名', [])}


def timing_get(tid):
    t = _BY_ID.get(_ALIAS.get(tid, tid))
    if not t:
        raise KBError(f'没有这个时机：{tid}；可选 {" / ".join(_BY_ID)}')
    return t


def timing_ids(events, legacy=False):
    """插件配置里的时机 → 新 id（认旧中文名）；legacy：旧配置里的「开发开始」顺带全栈各端首轮开工（拍板 2）。不认识的丢掉。"""
    out = []
    for e in events or []:
        tid = _ALIAS.get(e, e)
        for x in ([tid, 'end_start'] if legacy and e == '开发开始' else [tid]):
            if x in PLUGIN_TIMINGS and x not in out:
                out.append(x)
    return out


def _conf():
    return (load_settings().get('时机') or {}).get('代码更新') or {}


def timing_policy(tid, repo=''):
    """→ (选项, 来源：工程 / 全局 / 默认)。锁定的时机只有一个选项。"""
    t = timing_get(tid)
    if len(t['选项']) == 1:
        return t['选项'][0], '锁定'
    if repo:
        v = _repos.repo_pref(repo).get('代码更新')
        if v in t['选项']:
            return v, '工程'
    v = _conf().get(t['id'])
    if v in t['选项']:
        return v, '全局'
    return t['默认'], '默认'


def timings():
    """设置页 / 插件对话框 / 接口用的完整清单：每个时机的可选项、当前全局选择、是否锁定、能不能挂插件及原因。"""
    out = []
    for t in TIMINGS:
        cur, src = timing_policy(t['id'])
        plug = t.get('插件')
        out.append({'id': t['id'], '名称': t['名称'], '说明': t['说明'], '选项': list(t['选项']), '默认': t['默认'], '当前': cur,
                    '来源': src, '锁定': len(t['选项']) == 1, '原因': t.get('原因', ''),
                    '插件': plug is True, '插件总是': plug == '总是',
                    '插件原因': '' if plug else t.get('插件原因') or '这个时机不挂插件', '旧名': t.get('旧名', []),
                    '事件': t.get('事件') or t['名称']})
    return out


def save_timing(tid, policy):
    """设置页改某个时机的代码更新（全局）。锁定的、不在可选项里的拒绝。"""
    t = timing_get(tid)
    if len(t['选项']) == 1:
        raise KBError(f'「{t["名称"]}」锁定为「{t["选项"][0]}」，不能改：{t.get("原因", "")}')
    if policy not in t['选项']:
        raise KBError(f'「{t["名称"]}」的代码更新只能选 {" / ".join(t["选项"])}' + (f'（{t["原因"]}）' if t.get('原因') else ''))
    s = load_settings()
    c = s.setdefault('时机', {}).setdefault('代码更新', {})
    if policy == t['默认']:
        c.pop(t['id'], None)
    else:
        c[t['id']] = policy
    save_settings(s)
    return f'「{t["名称"]}」代码更新：{policy}' + ('（默认）' if policy == t['默认'] else '')


def reset_timings():
    s = load_settings()
    (s.get('时机') or {}).pop('代码更新', None)
    save_settings(s)
    return '时机的代码更新已恢复默认（按工程单独选的不变）'


def _short(loc, kind):
    try:
        h = hosts.head(loc, kind)
    except KBError:
        return ''
    return h[:8] if kind != 'svn' else (f'r{h}' if h else '')


def _behind(loc, kind):
    """当前分支落后远端几个提交（按本地的远端引用算，不额外联网）；svn / 没有上游 → None。"""
    if kind != 'git':
        return None
    try:
        r = hosts.git(loc, 'rev-list', '--count', 'HEAD..@{u}')
    except KBError:
        return None
    n = r.stdout.strip()
    return int(n) if r.returncode == 0 and n.isdigit() else None


def code_update(tid, loc, kind='', branch='', d=None, label='', busy=None, worktree=False, done=''):
    """在某个时机按设置更新代码，写一条统一格式的「更新代码」记录，返回代码基线（dict，网页基线条用）。
    busy：调用方已知的占用原因；None = 本机仓库自己查（开发会话 / 修复会话在改就只 fetch）。
    worktree：Bug 修复在独立 worktree 里修，主工作区只 fetch（目标分支没检出就快进）。
    d：期目录，给了就写进那一期的记录；label：记录里的前缀（如全栈的端名）。
    done：调用方已经按这个时机更新过（如切分支时前后各拉一次），只记结果和基线，不再更新。"""
    t = timing_get(tid)
    kind = kind or hosts.vcs(loc)
    pol, src = timing_policy(t['id'], loc)
    why = ''
    if done:
        res = done
    elif kind not in ('git', 'svn'):
        res = '不是 git / svn 仓库，不更新'
    elif pol == KEEP:
        res = '按设置不更新'
    else:
        if busy is None and pol != MUST and not hosts.is_remote(loc):
            try:
                busy = _fix._repo_busy(loc)
            except Exception:      # noqa: BLE001 — 查占用失败不耽误更新，按没占用处理
                busy = ''
        fetch_only = pol == FETCH or bool(busy) or worktree
        why = (f'设置为只 fetch' if pol == FETCH else f'仓库被占用：{busy}' if busy else '修复在 worktree 里做，不动主工作区' if worktree else '')
        res = hosts.sync(loc, kind, branch=branch, fetch_only=fetch_only, strict=pol == MUST)
    cur = ''
    try:
        cur = hosts.branch_of(loc, kind) if kind in ('git', 'svn') else ''
    except KBError:
        pass
    sha = _short(loc, kind) if kind in ('git', 'svn') else ''
    behind = _behind(loc, kind)
    name = Path(str(loc).rstrip('/')).name if not hosts.is_remote(loc) else str(loc)
    line = (f'{t["名称"]} · {name}@{cur or "?"}{" " + sha if sha else ""} · 设置 {pol}'
            + ('' if src in ('默认', '锁定') else f'（{src}）') + f' · {res}' + (f'（{why}）' if why else '')
            + (f' · 落后远端 {behind}' if behind else ''))
    rec = {'时机': t['名称'], '时机id': t['id'], '仓库': str(loc), '分支': cur, '提交': sha, '时间': now(), '设置': pol,
           '来源': src, '结果': res, '原因': why, '落后': behind, '记录': (label + ' ' if label else '') + line}
    if d is not None:
        try:
            _periods.log(d, '更新代码', rec['记录'])
        except Exception:      # noqa: BLE001 — 记录失败不耽误开发
            pass
    return rec


def baseline_text(rec):
    """代码基线一句话：分支 @sha · 时间 · 哪个时机 · 落后 N。"""
    if not rec:
        return ''
    return (f'{rec.get("分支") or "?"} @{rec.get("提交") or "?"} · {(rec.get("时间") or "")[5:16]} · {rec.get("时机", "")}（{rec.get("设置", "")}）'
            + (f' · 落后远端 {rec["落后"]}' if rec.get('落后') else ''))
