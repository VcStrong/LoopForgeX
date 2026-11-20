# From 大大王.DT
"""体检（kb doctor / 网页「⚙ 设置 → 🩺 体检」）：这台机器和团队默认是否一致，不一致的给一键修复。

逐项查：
  规矩        团队默认（_system/团队默认.json）里的管家规矩，本机是不是都在（本机删过的不会自动补回，这里可以一键补回）
  管家        自主度、每日上限等和团队默认是否一致
  kb 工具放行  ~/.claude/settings.json 的 permissions.allow 有没有 mcp__kb（没有的话交互会话里每次调 kb_ 工具都弹「允许吗」）
  auto 模式    后台会话用 claude --permission-mode auto；这台的 claude 不支持时改成 acceptEdits + --allowedTools（见 FALLBACK）
  角色套件残留  绕过知识库装进 ~/.claude 的 worker-agent agent / skill / hook（会设卡弹确认），去「设置 → 角色团 → 🗑 移除」清
  MCP         ~/.claude.json 里有没有接入本知识库的 kb 服务
install.sh 装完会跑 kb doctor --fix allow,auto,mcp。
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

from kblib.core.common import (PROG, KBError, TEAM_FILE, TEAM_STEWARD_KEYS, load_settings, now, read_text, save_settings,
                               team_defaults)
from kblib.ai import engines as _engines, team as _team

ALLOW = 'mcp__kb'
# auto 不可用时的替代（假设）：acceptEdits 自动接受改文件；-p 无人值守下其余工具不在放行名单会被直接拒，
# 所以放行知识库工具和 Bash / 联网读取，让后台会话能跑 git、构建和查资料——效果接近本机的 auto，但没有 auto 的风险分类器。
FALLBACK = ['--permission-mode', 'acceptEdits', '--allowedTools', 'mcp__kb,Bash,WebFetch,WebSearch']


def _claude_dir():
    return Path.home() / '.claude'


def _user_settings():
    p = _claude_dir() / 'settings.json'
    try:
        d = json.loads(read_text(p) or '{}') if p.is_file() else {}
    except (OSError, ValueError):
        d = None
    return d, p


def _allowed(d):
    allow = ((d or {}).get('permissions') or {}).get('allow') or []
    deny = ((d or {}).get('permissions') or {}).get('deny') or []
    ok = any(a in (ALLOW, ALLOW + '__*') for a in allow)
    blocked = [x for x in deny if x == ALLOW or str(x).startswith(ALLOW + '__')]
    return ok, blocked


def allow_kb_tools():
    """往 ~/.claude/settings.json 的 permissions.allow 加 mcp__kb（合并，不动其他配置）。"""
    d, p = _user_settings()
    if d is None:
        raise KBError(f'{p} 不是合法的 JSON，没有改；请先修好这个文件')
    if _allowed(d)[0]:
        return f'{p} 已放行 kb 工具'
    perm = d.setdefault('permissions', {})
    perm['allow'] = list(perm.get('allow') or []) + [ALLOW]
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.is_file():
        shutil.copy2(p, p.with_name('settings.json.bak-kb'))
    tmp = p.with_name('settings.json.kbtmp')
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2) + '\n', 'utf-8')
    tmp.replace(p)
    return f'已在 {p} 放行 kb 工具（permissions.allow 加了 {ALLOW}）'


def _claude_engine():
    try:
        e = _engines.get_engine('claude')
        return e, _engines.engine_bin(e)
    except KBError:
        return None, ''


def _uses_auto(e):
    a = list(e.get('启动参数') or [])
    return any(a[i] == '--permission-mode' and a[i + 1] == 'auto' for i in range(len(a) - 1))


def auto_supported(b):
    """claude --help 的 --permission-mode 选项里有没有 auto（只能看 CLI 版本；账号不支持的情况查不出来）。"""
    try:
        r = subprocess.run([b, '--help'], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r'--permission-mode.*?\(choices:([^)]*)\)', (r.stdout or '') + (r.stderr or ''), re.S)
    return ('"auto"' in m.group(1)) if m else None


def _swap(args):
    a = list(args or [])
    out, i = [], 0
    while i < len(a):
        if a[i] == '--permission-mode' and i + 1 < len(a):
            out += FALLBACK
            i += 2
            continue
        out.append(a[i])
        i += 1
    return out


def use_fallback_mode():
    """claude 引擎的启动 / 续跑参数里 --permission-mode auto 换成 FALLBACK（写进本机 设置.json「引擎」）。"""
    s = load_settings()
    over = s.setdefault('引擎', {})
    cur = dict(_engines.BUILTIN_ENGINES['claude'], **(over.get('claude') or {}))
    over['claude'] = dict(over.get('claude') or {}, 启动参数=_swap(cur.get('启动参数')), 续跑参数=_swap(cur.get('续跑参数')),
                          说明='Claude Code（claude -p 无人值守；这台的 claude 不支持 auto 权限模式，体检改成 acceptEdits + 放行 kb / Bash / 联网工具）')
    save_settings(s)
    return '后台会话的权限模式改成：' + ' '.join(FALLBACK) + '（claude 升级到支持 auto 后，在「设置 · AI 引擎」点恢复默认即可）'


def _mcp_linked():
    try:
        srv = (json.loads(read_text(Path.home() / '.claude.json')).get('mcpServers') or {}).get('kb')
    except (OSError, ValueError):
        return None, ''
    if not srv:
        return None, ''
    if srv.get('type') in ('http', 'sse') or srv.get('url'):
        return True, srv.get('url', '')
    args = [str(x) for x in srv.get('args') or []]
    return str(PROG / 'kb.py') in args, ' '.join([str(srv.get('command', ''))] + args)


def link_mcp():
    _, b = _claude_engine()
    if not b:
        raise KBError('没找到 claude 命令：装好 Claude Code 后再修复')
    py, kbpy = _engines._kb_cmd()
    subprocess.run([b, 'mcp', 'remove', 'kb', '-s', 'user'], capture_output=True, timeout=60)
    r = subprocess.run([b, 'mcp', 'add', 'kb', '-s', 'user', '--', py, kbpy, 'serve'], capture_output=True, text=True, timeout=60)
    if r.returncode:
        raise KBError('claude mcp add 失败：' + (r.stderr or r.stdout).strip()[:300])
    return '已接入 Claude Code（用户级 MCP 服务 kb）'


def _team_rule_ids():
    return [r['编号'] for r in team_defaults().get('管家规矩') or [] if isinstance(r, dict) and r.get('编号') and r.get('内容')]


def restore_team_rules():
    s = load_settings()
    ids = set(_team_rule_ids())
    s['已删团队规矩'] = [x for x in s.get('已删团队规矩') or [] if x not in ids]
    save_settings(s)
    return '已补回团队默认里的规矩：' + str(len(load_settings().get('管家规矩') or [])) + ' 条'


def apply_team_steward():
    t = {k: v for k, v in (team_defaults().get('管家') or {}).items() if k in TEAM_STEWARD_KEYS}
    if not t:
        return '团队默认里没有管家设置'
    s = load_settings()
    s['管家'] = dict(s.get('管家') or {}, **t)
    save_settings(s)
    return '管家设置已改成团队默认：' + '、'.join(f'{k}={v}' for k, v in t.items())


def save_team_defaults(by='用户（网页）'):
    """「⬆ 设为团队默认」：本机的管家规矩和管家设置写进 _system/团队默认.json（进 git，提交后同事 git pull 生效）。"""
    s = load_settings()
    t = team_defaults()
    rules = [{'编号': r['编号'], '内容': r['内容'], '时间': r.get('时间', '')} for r in s.get('管家规矩') or []
             if isinstance(r, dict) and r.get('编号') and r.get('内容')]
    old = set(_team_rule_ids())
    t['管家规矩'] = rules
    t['管家'] = {k: v for k, v in (s.get('管家') or {}).items() if k in TEAM_STEWARD_KEYS}
    t['更新'] = f'{now()} {by}'
    tmp = TEAM_FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps(t, ensure_ascii=False, indent=1) + '\n', 'utf-8')
    tmp.replace(TEAM_FILE)
    gone = old - {r['编号'] for r in rules}
    try:
        d = subprocess.run(['git', '-C', str(PROG.parent), 'status', '--porcelain', '--', str(TEAM_FILE)],
                           capture_output=True, text=True, timeout=20).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        d = ''
    return (f'已写入 {TEAM_FILE.name}：规矩 {len(rules)} 条、管家 {t["管家"].get("自主度", "")} / 每天 {t["管家"].get("每日上限", "")} 件'
            + (f'（去掉了 {len(gone)} 条；同事那边已有的不会自动删）' if gone else '')
            + ('。提交并推送程序仓库后，同事 git pull（或网页「⬆ 更新」）即生效' if d else '。和仓库里的一致，没有变化'))


def doctor(probe=True):
    """逐项体检。probe=False 时不跑 claude --help（快）。返回 [{id, 项目, 状态 ok/warn/bad, 本机, 团队默认, 说明, 修复}]。"""
    out = []
    s = load_settings()
    t = team_defaults()

    def add(i, name, st, mine, want, note, fix=''):
        out.append({'id': i, '项目': name, '状态': st, '本机': mine, '团队默认': want, '说明': note, '修复': fix})

    ids = _team_rule_ids()
    have = {r.get('编号') for r in s.get('管家规矩') or [] if isinstance(r, dict) and r.get('内容')}
    miss = [i for i in ids if i not in have]
    add('rules', '管家规矩', 'bad' if miss else 'ok', f'{len(have)} 条', f'{len(ids)} 条',
        (f'缺团队规矩 {"、".join(miss)}（本机删过的不会自动补回）' if miss else '团队默认的规矩都在') if ids else '团队默认里没有规矩',
        '补回团队规矩' if miss else '')

    tm = {k: v for k, v in (t.get('管家') or {}).items() if k in TEAM_STEWARD_KEYS}
    from kblib.steward.patrol import steward_settings       # 同层
    eff = steward_settings()
    diff = [k for k, v in tm.items() if eff.get(k) != v]
    add('steward', '管家设置', 'bad' if diff else 'ok', f'{eff["自主度"]} / 每天 {eff["每日上限"]} 件',
        f'{tm.get("自主度", "—")} / 每天 {tm.get("每日上限", "—")} 件' if tm else '—',
        ('和团队默认不同：' + '、'.join(f'{k} {eff.get(k)}→{tm[k]}' for k in diff)) if diff else ('和团队默认一致' if tm else '团队默认里没有管家设置'),
        '改成团队默认' if diff else '')

    d, p = _user_settings()
    ok, blocked = _allowed(d)
    try:                                                    # settings.local.json 里放行的也算
        lp = _claude_dir() / 'settings.local.json'
        lo, lb = _allowed(json.loads(read_text(lp) or '{}')) if lp.is_file() else (False, [])
        ok, blocked = ok or lo, blocked + lb
    except (OSError, ValueError):
        pass
    if d is None:
        add('allow', 'kb 工具放行', 'bad', f'{p} 读不了', ALLOW, 'settings.json 不是合法 JSON，先手动修好')
    elif blocked:
        add('allow', 'kb 工具放行', 'bad', '被 deny 拦住：' + '、'.join(blocked), ALLOW, f'{p} 的 permissions.deny 里拦了 kb 工具，请手动删掉')
    else:
        add('allow', 'kb 工具放行', 'ok' if ok else 'bad', '已放行' if ok else '未放行', ALLOW,
            '交互会话调 kb_ 工具不弹确认' if ok else f'交互会话里每次调 kb_ 工具都会弹「允许吗」：{p} 的 permissions.allow 加 {ALLOW}',
            '' if ok else '放行 kb 工具')

    e, b = _claude_engine()
    if not e:
        add('auto', 'auto 权限模式', 'warn', '没找到 claude', 'auto', '没装 Claude Code（或默认不用它）：装好后重新运行 ./install.sh')
    elif not _uses_auto(e):
        modes = [a for i, a in enumerate(e.get('启动参数') or []) if i and (e['启动参数'][i - 1] == '--permission-mode')]
        add('auto', 'auto 权限模式', 'ok', '后台会话用 ' + (' '.join(modes) or '默认模式'), 'auto',
            '引擎参数已改过（不用 auto）；claude 支持 auto 后可在「设置 · AI 引擎」恢复默认')
    else:
        sup = auto_supported(b) if probe else True
        if sup is False:
            add('auto', 'auto 权限模式', 'bad', '这台的 claude 不支持 auto', 'auto',
                '后台会话（claude -p）会退回逐条审批、在无人值守下被直接拒，表现为「claude 不能用」；可升级 Claude Code，或一键改用 '
                + ' '.join(FALLBACK), '改用 acceptEdits')
        else:
            add('auto', 'auto 权限模式', 'ok' if sup else 'warn', '支持' if sup else '没查出来', 'auto',
                '后台会话用 --permission-mode auto' + ('' if sup else '（claude --help 里没读到选项；账号是否开通 auto 只能实际跑一次看）'))

    man = _team._manifest()
    left = [] if man is not None else _team.suite_residue() + _team.adapter_residue()      # 套件移除了，其他引擎的适配也该一并清掉
    hooks = 0
    if man is None and d:
        _, ours = _team._split_hooks(d)
        hooks = sum(map(len, ours.values()))
    add('suite', '角色套件残留', 'bad' if left else 'ok', ('；'.join(left))[:400] if left else ('经知识库安装' if man is not None else '无'), '无',
        ('绕过知识库装进 ~/.claude 的角色套件' + (f'（含 {hooks} 组 hook，会设卡弹确认）' if hooks else '')
         + '：去「⚙ 设置 → 🧑‍🤝‍🧑 角色团」点「🗑 移除」清干净（会先备份），要用再从那里装') if left else '没有绕过知识库装的角色套件')

    lk, desc = _mcp_linked()
    add('mcp', 'MCP 接入', 'ok' if lk else 'bad', ('已接入' if lk else ('kb 指向别处：' + desc[:120] if lk is False else '未接入')), '接入本知识库',
        'Claude Code 能用 kb_ 工具' if lk else '本机 Claude Code 连不上（或连的不是）这个知识库', '' if lk else '接入 Claude Code')
    return out


FIXES = {'rules': restore_team_rules, 'steward': apply_team_steward, 'allow': allow_kb_tools, 'auto': use_fallback_mode, 'mcp': link_mcp}


def doctor_fix(ids='all'):
    """修复体检里标红的项。ids：all 或逗号分隔的 id（rules / steward / allow / auto / mcp）。返回每项的结果文字。"""
    want = set(FIXES) if ids in ('', 'all', None) else {x.strip() for x in str(ids).split(',') if x.strip()}
    bad = [x for x in want if x not in FIXES]
    if bad:
        raise KBError('没有这个修复项：' + '、'.join(bad) + '（可选：' + '、'.join(FIXES) + '）')
    msgs = []
    for it in doctor():
        if it['id'] in want and it['修复']:
            try:
                msgs.append(f'✓ {it["项目"]}：' + FIXES[it['id']]())
            except KBError as x:
                msgs.append(f'✗ {it["项目"]}：{x}')
    return msgs or ['没有要修的']


def doctor_text(items=None):
    mark = {'ok': '✓', 'warn': '!', 'bad': '✗'}
    items = items if items is not None else doctor()
    lines = [f'{mark[x["状态"]]} {x["项目"]}：本机 {x["本机"]} · 团队默认 {x["团队默认"]} — {x["说明"]}'
             + (f'（可修复：{x["修复"]}，kb doctor --fix {x["id"]}）' if x['修复'] else '') for x in items]
    n = sum(x['状态'] == 'bad' for x in items)
    return '\n'.join(lines) + ('\n\n' + (f'{n} 项和团队默认不一致：kb doctor --fix 一键修复（角色套件残留去网页设置里移除）' if n else '全部一致'))
