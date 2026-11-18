"""程序更新：检查 origin/main 有没有新提交、提示、一键更新并重启（自检失败自动退回）。版本号就是提交号，不另维护。

  检查   管家巡检时顺带看（距上次满 1 小时才 git fetch），算 HEAD 落后 origin/main 几个提交、列出提交标题。
  保护   程序文件有本地改动、分支不是 main、本地有远端没有的提交（分叉）→ 只提示不更新；
         有后台 AI 会话在跑 → 排队，等空闲再更新。期、基础信息、设置都在 .gitignore 白名单外，更新不会动资料。
  更新   git pull --ff-only → requirements.txt 变了就在 venv 里 pip install → 起一个独立的守护小进程 → 网页进程 execv 原地重启
         （pid 不变，launchd / systemd 照常管）→ 新进程启动后自检、写标记 → 守护进程等 :端口/login 通且标记对得上；
         等不到就 git reset --hard 原提交、重装依赖、再重启，结果写回状态。
  设置   设置.json「更新」：模式 = 关 / 只提示（默认）/ 自动（空闲时自动更新）；推送 = 发现新提交时发通知（系统通知 + 群机器人）。
  MCP    新开的 Claude Code 会话自动用新代码；已开着的 stdio MCP 进程发现磁盘上的提交号变了，下一次工具结果末尾提示一句 /mcp 重连。
状态在 _system/管家/更新.json。
"""
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from kblib.core.common import KBError, PERIODS, SYS, load_settings, now, save_settings
from kblib.ai.devsession import _alive, _notify

PROG = Path(os.environ.get('KB_PROG_DIR') or Path(__file__).resolve().parents[3])    # 程序目录（git 仓库）；KB_ROOT 可以是另一个数据目录
STATE = SYS / '管家' / '更新.json'
BRANCH = 'main'
MODES = ('关', '只提示', '自动')
CHECK_HOURS = 1
WAIT_SEC = 90                 # 重启后等新进程自检通过的时间，超过就退回
WEB = {'on': False, 'port': 11555, 'argv': None}     # 只有网页进程能重启自己：server.main 启动时置上
_LOCK = threading.Lock()
_ENV = {**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'LC_ALL': 'C'}
_ENV.setdefault('GIT_SSH_COMMAND', 'ssh -o BatchMode=yes -o ConnectTimeout=15')


def _git(*a, timeout=30, check=True):
    r = subprocess.run(['git', '-C', str(PROG), *a], capture_output=True, text=True, timeout=timeout, env=_ENV)
    if check and r.returncode:
        raise KBError(f'git {" ".join(a)} 失败：{(r.stderr or r.stdout).strip()[:300]}')
    return r.stdout.strip()


def _load():
    try:
        return json.loads(STATE.read_text('utf-8'))
    except (OSError, ValueError):
        return {}


def _save(st):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix('.tmp')
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), 'utf-8')
    tmp.replace(STATE)


def update_settings():
    s = load_settings().get('更新') or {}
    return {'模式': s.get('模式') if s.get('模式') in MODES else '只提示', '推送': s.get('推送', True) is not False}


def update_set(mode=None, push=None):
    s = load_settings()
    cur = update_settings()
    if mode not in (None, ''):
        if mode not in MODES:
            raise KBError(f'更新模式只能是：{"、".join(MODES)}')
        cur['模式'] = mode
    if push is not None:
        cur['推送'] = bool(push)
    s['更新'] = cur
    save_settings(s)
    return {'关': '已关闭更新检查', '只提示': '有更新时只提示，你点「⬆ 更新并重启」才更新',
            '自动': '有更新时等后台会话都结束后自动更新并重启'}[cur['模式']] + ('；发现新版本会推送通知' if cur['推送'] and cur['模式'] != '关' else '')


# ---------------------------------------------------------------- 版本号（只读 .git 文件，MCP 每次工具调用都看，不起子进程）

def code_version(prog=None):
    """当前检出的提交号；读不到返回 ''。"""
    g = Path(prog or PROG) / '.git'
    try:
        if g.is_file():                                   # worktree：.git 是指向真实目录的文件
            g = (g.parent / g.read_text('utf-8').split(':', 1)[1].strip()).resolve()
        head = (g / 'HEAD').read_text('utf-8').strip()
        if not head.startswith('ref:'):
            return head
        ref = head[4:].strip()
        common = g
        if (g / 'commondir').exists():
            common = (g / (g / 'commondir').read_text('utf-8').strip()).resolve()
        for base in (g, common):
            if (base / ref).exists():
                return (base / ref).read_text('utf-8').strip()
        for line in (common / 'packed-refs').read_text('utf-8').splitlines():
            if line.endswith(' ' + ref):
                return line.split()[0]
    except (OSError, IndexError):
        pass
    return ''


_BOOT = code_version()


def update_mcp_notice():
    """给已开着的 MCP 进程用：程序在它启动后更新过，返回一句提示（每个进程只提示一次）；否则 ''。"""
    if not _BOOT or WEB['on'] or _BOOT_SAID[0]:
        return ''
    cur = code_version()
    if cur and cur != _BOOT:
        _BOOT_SAID[0] = True
        return (f'\n\n【知识库程序已更新】{_BOOT[:7]} → {cur[:7]}：这个 Claude Code 会话用的还是旧的知识库工具，'
                '在 Claude Code 里输入 /mcp 重连 kb 后用新功能（新开的会话自动用新代码）。')
    return ''


_BOOT_SAID = [False]


# ---------------------------------------------------------------- 检查

def _kind(subject):
    s = subject.lower()
    return '新功能' if s.startswith('feat') else '修复' if s.startswith('fix') else '其他'


def _busy():
    """还在跑的后台 AI 会话（各期 / 事项里 pid 还活着的、Bug 分析 / 修复、模糊添加）。"""
    out = []

    def walk(o, where):
        if isinstance(o, dict):
            pid = o.get('pid')
            if isinstance(pid, int) and pid > 0 and '中' in str(o.get('状态', '')) and _alive(pid):
                out.append(where)
            for v in o.values():
                walk(v, where)
        elif isinstance(o, list):
            for v in o:
                walk(v, where)
    try:
        metas = sorted(PERIODS.glob('*/期信息.json'))
    except OSError:
        metas = []
    for f in metas:
        try:
            walk(json.loads(f.read_text('utf-8')), f.parent.name)
        except (OSError, ValueError):
            pass
    try:
        from kblib.bugs.analysis import an_sessions
        for x in an_sessions():
            if x.get('状态') == '运行中' and _alive(x.get('pid', 0)):
                out.append(f'Bug {x.get("类型") or "分析"}')
    except Exception:                     # noqa: BLE001
        pass
    try:
        walk(json.loads((SYS / '模糊添加.json').read_text('utf-8')), '模糊添加')
    except (OSError, ValueError):
        pass
    return sorted(set(out))


def _blockers(branch, ahead):
    """不能更新的原因（只提示）。"""
    why = []
    if branch != BRANCH:
        why.append(f'程序目录在分支「{branch}」上，不是 {BRANCH}')
    dirty = [x[3:] for x in _git('status', '--porcelain', '--untracked-files=no').splitlines() if x.strip()]
    if dirty:
        why.append(f'程序文件有 {len(dirty)} 处本地改动（{"、".join(dirty[:3])}{"…" if len(dirty) > 3 else ""}）')
    if ahead:
        why.append(f'本地有 {ahead} 个提交远端没有（和远端分叉）')
    return why


def update_check(fetch=True, by='管家'):
    """检查一次（fetch=False 只看本地已知的 origin/main）。返回状态。"""
    with _LOCK:
        st = _load()
        if not (PROG / '.git').exists():
            st.update(检查时间=now(), 错误='程序目录不是 git 仓库（不是 git clone 安装的），没法自动更新')
            _save(st)
            return update_status()
        err = ''
        if fetch:
            try:
                _git('fetch', '-q', 'origin', BRANCH, timeout=90)
            except (KBError, subprocess.TimeoutExpired, OSError) as e:
                err = f'连不上远端：{str(e)[:200]}'
        try:
            head = _git('rev-parse', 'HEAD')
            remote = _git('rev-parse', f'origin/{BRANCH}')
            branch = _git('rev-parse', '--abbrev-ref', 'HEAD')
            behind = int(_git('rev-list', '--count', f'HEAD..origin/{BRANCH}'))
            ahead = int(_git('rev-list', '--count', f'origin/{BRANCH}..HEAD'))
            log = _git('log', '--format=%h%x09%s%x09%ci', '-n', '50', f'HEAD..origin/{BRANCH}').splitlines() if behind else []
            why = _blockers(branch, ahead) if behind else []
        except (KBError, ValueError, subprocess.TimeoutExpired, OSError) as e:
            st.update(检查时间=now(), 错误=f'读不出版本：{str(e)[:200]}')
            _save(st)
            return update_status()
        commits = []
        for x in log:
            h, s, t = (x.split('\t') + ['', ''])[:3]
            commits.append({'提交': h, '标题': s, '时间': t[:16], '类型': _kind(s)})
        st.update(检查时间=now(), 错误=err, 当前=head, 分支=branch, 远端=remote, 落后=behind, 领先=ahead, 提交=commits, 不能更新=why, 检查人=by)
        if behind and remote != st.get('已通知'):
            cfg = update_settings()
            if cfg['推送'] and cfg['模式'] != '关':
                st['已通知'] = remote
                feats = [c['标题'] for c in commits if c['类型'] != '其他'] or [c['标题'] for c in commits]
                _notify(f'知识库 · 有 {behind} 项更新', '\n'.join(feats[:8]) + ('\n（' + '；'.join(why) + '，只提示不更新）' if why
                                                                       else '\n到总览点「⬆ 更新并重启」'), '管家')
        _save(st)
    return update_status()


def update_status():
    st = _load()
    cfg = update_settings()
    head = code_version() or st.get('当前', '')
    behind = int(st.get('落后') or 0) if st.get('当前') == head else 0       # 检查之后已经更新过（或手动 pull 过）：落后数作废
    busy = _busy() if behind else []
    return {'设置': cfg, '模式选项': list(MODES), '当前': head[:7], '当前完整': head, '分支': st.get('分支', ''),
            '远端': (st.get('远端') or '')[:7], '落后': behind, '提交': st.get('提交') if behind else [],
            '不能更新': st.get('不能更新') if behind else [], '在跑': busy, '排队': bool(st.get('排队')) and behind > 0,
            '检查时间': st.get('检查时间', ''), '错误': st.get('错误', ''), '上次更新': st.get('上次更新') or {},
            '能重启': WEB['on'], 'mcp提示': '已开着的 Claude Code 会话要在 Claude Code 里输入 /mcp 重连 kb 才用新代码；新开的会话自动用新代码。'}


def update_cards():
    """管家卡片：有更新时一张。"""
    if update_settings()['模式'] == '关':
        return []
    v = update_status()
    out = []
    last = v['上次更新']
    if last.get('结果') == '已退回' and last.get('时间', '') >= (datetime.now() - timedelta(days=2)).strftime('%Y-%m-%d %H:%M'):
        out.append({'id': f'update-fail:{last.get("新", "")}', '类型': '待你', '标题': '上次更新自检没通过，已退回原来的版本',
                    '说明': last.get('说明', ''), '为什么': '程序照旧能用；把 web.log 里的报错发给发布的同事。', '按钮': '看看', '链接': '#/settings'})
    if not v['落后']:
        return out
    feats = [c['标题'] for c in v['提交'] if c['类型'] != '其他'] or [c['标题'] for c in v['提交']]
    blocked = v['不能更新']
    out.append({'id': f'update:{v["远端"]}', '类型': '建议', '标题': f'知识库有 {v["落后"]} 项更新',
                '说明': '；'.join(feats[:4]) + ('…' if len(feats) > 4 else ''),
                '为什么': ('只提示不更新：' + '；'.join(blocked)) if blocked
                else (f'有 {len(v["在跑"])} 个后台会话在跑，更新会排队等它们结束。' if v['在跑'] else '')
                + '更新只动程序文件，不动期、基础信息、设置；重启后自检，失败自动退回。' + v['mcp提示'],
                '按钮': '看看' if blocked else '⬆ 更新并重启', '动作': '' if blocked else 'update', '链接': '#/settings'})
    return out


def update_tick():
    """管家巡检时调：满 1 小时 fetch 一次；排队的、或设成自动的，空闲时更新。返回一句说明（没事返回 ''）。"""
    cfg = update_settings()
    if cfg['模式'] == '关':
        return ''
    st = _load()
    last = st.get('检查时间', '')
    if not last or last <= (datetime.now() - timedelta(hours=CHECK_HOURS)).strftime('%Y-%m-%d %H:%M'):
        update_check(True)
    v = update_status()
    if v['落后'] and WEB['on'] and not v['不能更新'] and not v['在跑'] and (v['排队'] or cfg['模式'] == '自动'):
        return update_apply(by='管家（空闲时自动）' if cfg['模式'] == '自动' else '管家（排队的更新）')
    return ''


# ---------------------------------------------------------------- 更新、重启、自检、退回

_WATCH = r'''
import json, os, signal, subprocess, sys, time, urllib.request
a = json.loads(sys.argv[1])
def st_load():
    try:
        return json.load(open(a["state"], encoding="utf-8"))
    except Exception:
        return {}
def st_save(st):
    tmp = a["state"] + ".tmp"
    json.dump(st, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, a["state"])
def log(s):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), "[更新守护]", s, flush=True)
def up():
    try:
        return urllib.request.urlopen("http://127.0.0.1:%d/login" % a["port"], timeout=5).status == 200
    except Exception:
        return False
def ok(sha):
    s = st_load().get("启动") or {}
    return s.get("提交") == sha and s.get("自检") == "通过" and up()
def finish(res, msg):
    st = st_load()
    lu = st.get("上次更新") or {}
    lu.update(结果=res, 说明=msg, 完成=time.strftime("%Y-%m-%d %H:%M"))
    st["上次更新"] = lu
    st.pop("阶段", None)
    st_save(st)
    log(res + "：" + msg)
def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
t0 = time.time()
while time.time() - t0 < a["wait"]:
    if ok(a["new"]):
        finish("已更新", "%s → %s，自检通过" % (a["old"][:7], a["new"][:7]))
        sys.exit(0)
    if (st_load().get("启动") or {}).get("自检") == "失败":
        break
    if not a["managed"] and not alive(a["pid"]) and not up():     # 没人管的进程起不来就是起不来了，不用干等
        time.sleep(3)
        if not up():
            break
    time.sleep(2)
why = ((st_load().get("启动") or {}).get("错误") or ("新版本没起来（看 web.log）" if not alive(a["pid"]) else "新版本 %d 秒内没起来" % a["wait"]))[:300]
log("自检没通过，退回 " + a["old"][:7] + "：" + why)
subprocess.run(["git", "-C", a["prog"], "reset", "-q", "--hard", a["old"]])
if a["req"]:
    subprocess.run([a["py"], "-m", "pip", "install", "-q", "-r", a["prog"] + "/_system/requirements.txt"], timeout=900)
if alive(a["pid"]):
    os.kill(a["pid"], signal.SIGTERM)
    for _ in range(20):
        if not alive(a["pid"]):
            break
        time.sleep(0.5)
    if alive(a["pid"]):
        os.kill(a["pid"], signal.SIGKILL)
time.sleep(1)
if a["managed"] == "launchd":
    subprocess.run(["launchctl", "kickstart", "-k", "gui/%d/%s" % (os.getuid(), a["label"])])
elif a["managed"] == "systemd":
    subprocess.run(["systemctl", "--user", "restart", a["label"]])
else:
    subprocess.Popen(a["argv"], stdout=open(a["log"], "a"), stderr=subprocess.STDOUT, start_new_session=True, cwd=a["prog"])
t0 = time.time()
while time.time() - t0 < 120 and not up():
    time.sleep(2)
finish("已退回", "新版本 %s 自检没通过（%s），已退回 %s%s" % (a["new"][:7], why, a["old"][:7], "" if up() else "，但原版本也没起来，看 web.log"))
'''


def _managed():
    if os.environ.get('XPC_SERVICE_NAME', '').startswith('com.local.kb'):
        return 'launchd', os.environ['XPC_SERVICE_NAME']
    if os.environ.get('INVOCATION_ID') and sys.platform.startswith('linux'):
        return 'systemd', 'kb-web.service'
    return '', ''


def update_apply(by='用户（网页）'):
    """一键更新：保护检查 → pull → 依赖 → 守护进程 → 重启。有后台会话在跑时排队。"""
    if not WEB['on']:
        raise KBError('只能在知识库网页上更新（网页进程才能重启自己）')
    v = update_check(True, by=by)
    if not v['落后']:
        return '已经是最新版本（' + v['当前'] + '）' + ('；' + v['错误'] if v['错误'] else '')
    if v['不能更新']:
        raise KBError('只提示不更新：' + '；'.join(v['不能更新']))
    if v['在跑']:
        st = _load()
        st['排队'] = now()
        _save(st)
        return f'有 {len(v["在跑"])} 个后台会话在跑（{"、".join(v["在跑"][:3])}），等它们结束后自动更新并重启'
    with _LOCK:
        old = _git('rev-parse', 'HEAD')
        try:
            _git('pull', '-q', '--ff-only', 'origin', BRANCH, timeout=180)
        except (KBError, subprocess.TimeoutExpired) as e:
            raise KBError(f'拉取失败，没有改动：{e}')
        new = _git('rev-parse', 'HEAD')
        req = bool(_git('diff', '--name-only', old, new, '--', '_system/requirements.txt'))
        if req:
            r = subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', '-r', str(PROG / '_system' / 'requirements.txt')],
                               capture_output=True, text=True, timeout=900)
            if r.returncode:
                _git('reset', '-q', '--hard', old)
                raise KBError(f'依赖装不上，已退回原来的版本：{(r.stderr or r.stdout).strip()[-300:]}')
        st = _load()
        st.pop('排队', None)
        st.pop('启动', None)
        st.update(阶段='重启中', 上次更新={'时间': now(), '旧': old, '新': new, '发起': by, '结果': '重启中', '依赖': req})
        _save(st)
    managed, label = _managed()
    argv = WEB['argv'] or [sys.executable, *sys.argv]
    args = {'state': str(STATE), 'prog': str(PROG), 'old': old, 'new': new, 'port': WEB['port'], 'pid': os.getpid(), 'wait': WAIT_SEC,
            'req': req, 'py': sys.executable, 'managed': managed, 'label': label, 'argv': argv, 'log': str(PROG / '_system' / 'web.log')}
    with open(PROG / '_system' / 'web.log', 'a') as lf:
        subprocess.Popen([sys.executable, '-c', _WATCH, json.dumps(args, ensure_ascii=False)], stdout=lf, stderr=subprocess.STDOUT,
                         start_new_session=True, cwd=str(PROG))
    print(f'{now()} 更新：{old[:7]} → {new[:7]}（{by}），重启网页', flush=True)

    def restart():
        time.sleep(1.5)                    # 先把这次请求的回答发出去
        try:
            os.execv(argv[0], argv)        # 原地重启：pid 不变，launchd / systemd 照常管
        except OSError as e:
            print('execv 失败，退出等 launchd / systemd 拉起：', repr(e), flush=True)
            os._exit(1)
    threading.Thread(target=restart, daemon=True).start()
    return f'正在更新 {old[:7]} → {new[:7]} 并重启网页，十几秒后自动刷新；自检没通过会自动退回。已开着的 Claude Code 会话在 Claude Code 里 /mcp 重连。'


def update_startup(port, argv, check=None):
    """网页进程启动时调：记下能重启自己；刚更新完的，做一次自检并写标记（守护进程看它决定要不要退回）。"""
    WEB.update(on=True, port=port, argv=argv)
    st = _load()
    if st.get('阶段') != '重启中':
        return
    head = code_version()
    res = {'提交': head, 'pid': os.getpid(), '时间': now(), '自检': '通过', '错误': ''}
    try:
        if check:
            check()
    except Exception as e:                # noqa: BLE001
        res.update(自检='失败', 错误=repr(e)[:300])
    st['启动'] = res
    _save(st)
