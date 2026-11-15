"""网页终端：期页面「终端 → ⌨ 我的终端」，用开源的 ttyd（github.com/tsl0922/ttyd，MIT）在这一期各端仓库里开交互式 shell。

AI 会话被拦（自动模式判越权、要登录、要推送）、模型反馈的环境问题，直接在网页上处理，不用切到电脑的命令窗口。
  - 每个终端起一个 ttyd：只监听 UNIX socket（不开端口），-b /term/<编号> 让它挂在知识库网页的 /term/<编号>/ 下；
    网页服务把 /term/<编号>/… 原样转发过去（含 WebSocket，见 webapp/server.py），登录态、访问密码照常生效。
  - shell 外面套一层 script，把输出追加录到 期/<名>/终端/<时间>-<编号>.log，「↩ 发给会话」从里面取最近的输出。
  - 远程仓库（user@主机:/路径）：ttyd 里跑 ssh -tt 进那台机器、cd 过去再起 shell。
  - ttyd 每接一个浏览器连接起一个 shell，断开就结束（刷新页面 = 新 shell，之前的输出在日志里）。
安全：开 / 关只走 page_only 接口（只能本人在知识库网页上操作）；/term/ 的 WebSocket 只接受本页面发起的（Origin 校验）；
监听非本机地址本来就必须设访问密码；「⚙ 设置 → 常用 → 网页终端」可以关掉（设置.json「网页终端」: false）。
没装 ttyd：macOS brew install ttyd；Linux apt install ttyd 或从 GitHub Releases 下载静态二进制。
"""
import os
import re
import secrets
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from kblib.core.common import KBError, load_settings, now, save_settings
from kblib.core.periods import log, period_dir
from kblib.infra import hosts

MAX_PER_PERIOD = 4
_TERMS = {}          # 编号 → {期, 目录, 日志, 套接字, proc, 开始}
_SOCK_DIR = Path(tempfile.gettempdir()) / f'kb-term-{os.getuid()}'
_ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(\x07|\x1b\\)|\x1b[()][0-9A-Za-z]|\x1b[=>]|\r')
TERM_INSTALL = 'brew install ttyd' if sys.platform == 'darwin' else 'apt install ttyd（或从 https://github.com/tsl0922/ttyd/releases 下载静态二进制）'


def term_enabled():
    return load_settings().get('网页终端', True) is not False


def ttyd_path():
    return shutil.which('ttyd') or next((p for p in ('/opt/homebrew/bin/ttyd', '/usr/local/bin/ttyd', '/usr/bin/ttyd') if os.path.exists(p)), '')


def term_set_enabled(on, by='用户（网页）'):
    """「⚙ 设置 → 常用 → 网页终端」开关；关掉时已经开着的终端全部关闭。"""
    s = load_settings()
    s['网页终端'] = bool(on)
    save_settings(s)
    if not on:
        for tid in list(_TERMS):
            term_close(tid, by)
    return '网页终端已' + ('打开' if on else '关闭（开着的终端都关掉了）')


def _alive(t):
    return t['proc'].poll() is None


def _shell_cmd(loc, logf):
    """→ (要在 ttyd 里跑的命令, 工作目录)。外面套 script 录输出（macOS 和 Linux 的 script 参数不一样）。"""
    r = hosts.parse(loc)
    if r['远程']:
        inner = f'cd {shlex.quote(r["路径"])} 2>/dev/null || echo "（目录不存在：{r["路径"]}，停在家目录）"; exec "${{SHELL:-/bin/sh}}" -l'
        prog = ['ssh', '-tt', *(['-p', r['端口']] if r['端口'] else []), r['主机'], inner]
        cwd = str(Path.home())
    else:
        if not os.path.isdir(r['路径']):
            raise KBError(f'目录不存在：{r["路径"]}')
        prog = [os.environ.get('SHELL') or ('/bin/zsh' if sys.platform == 'darwin' else '/bin/bash'), '-l']
        cwd = r['路径']
    if sys.platform == 'darwin':
        return ['script', '-q', '-a', '-F', str(logf), *prog], cwd
    return ['script', '-q', '-a', '-f', '-c', shlex.join(prog), str(logf)], cwd


def term_open(period, cwd='', by='用户（网页）'):
    """开一个终端。cwd：本机目录或 user@主机:/路径（空 = 期目录）。→ {id, 目录, 地址}"""
    if not term_enabled():
        raise KBError('网页终端已在「⚙ 设置 → 常用」里关掉')
    exe = ttyd_path()
    if not exe:
        raise KBError(f'网页终端要用 ttyd（开源的网页终端），这台机器还没装：{TERM_INSTALL}')
    d = period_dir(period)
    cwd = (cwd or '').strip() or str(d)
    mine = [t for t in _TERMS.values() if t['期'] == d.name and _alive(t)]
    if len(mine) >= MAX_PER_PERIOD:
        raise KBError(f'这一期已经开了 {len(mine)} 个终端，先关掉不用的')
    tid = secrets.token_hex(6)
    (d / '终端').mkdir(exist_ok=True)
    logf = d / '终端' / f'{datetime.now():%Y%m%d-%H%M%S}-{tid}.log'
    prog, wd = _shell_cmd(cwd, logf)
    _SOCK_DIR.mkdir(mode=0o700, exist_ok=True)
    sock = _SOCK_DIR / f'{tid}.sock'
    env = {k: v for k, v in os.environ.items() if k not in ('CLAUDECODE', 'CLAUDE_CODE_ENTRYPOINT', 'KB_SESSION')}
    env.update(KB_PERIOD=d.name, LANG=os.environ.get('LANG') or 'zh_CN.UTF-8')
    argv = [exe, '-W', '-i', str(sock), '-b', f'/term/{tid}', '-w', wd, '-t', 'fontSize=13', '-t', 'disableLeaveAlert=true',
            '-t', f'titleFixed={d.name} · {hosts.label(cwd)}', *prog]
    errf = _SOCK_DIR / f'{tid}.err'
    with open(errf, 'wb') as eh:                      # ttyd 每个连接都打日志：写文件，不用管道（管道满了 ttyd 会卡住）
        proc = subprocess.Popen(argv, cwd=wd, env=env, stdin=subprocess.DEVNULL, stdout=eh, stderr=subprocess.STDOUT,
                                start_new_session=True)
    for _ in range(50):                               # 等 socket 出来（ttyd 起得很快）
        if sock.exists() or proc.poll() is not None:
            break
        time.sleep(0.05)
    if proc.poll() is not None or not sock.exists():
        err = errf.read_text('utf-8', 'replace')[-300:] if errf.exists() else ''
        _kill(proc)
        raise KBError(f'ttyd 没起来：{err or "等了 2.5 秒还没就绪"}')
    _TERMS[tid] = {'期': d.name, '目录': cwd, '日志': logf, '套接字': str(sock), 'proc': proc, '开始': now()}
    log(d, '终端', f'打开 {hosts.label(cwd)}（{by}；输出录在 终端/{logf.name}）')
    return {'id': tid, '目录': hosts.label(cwd), '地址': f'/term/{tid}/'}


def _kill(proc):
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        pass
    try:
        proc.wait(2)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass


def term_socket(tid):
    """网页服务转发 /term/<编号>/ 用：→ UNIX socket 路径；终端不在了返回 ''。"""
    t = _TERMS.get(tid or '')
    return t['套接字'] if t and _alive(t) else ''


def term_close(tid, by='用户（网页）'):
    t = _TERMS.pop(tid or '', None)
    if not t:
        raise KBError('终端不在了（网页服务重启过，或已经关掉）')
    _kill(t['proc'])
    for f in (t['套接字'], t['套接字'][:-5] + '.err'):
        try:
            os.unlink(f)
        except OSError:
            pass
    try:
        log(period_dir(t['期']), '终端', f'关闭 {hosts.label(t["目录"])}（{by}）')
    except KBError:
        pass
    return '终端已关闭'


def term_list(period):
    """这一期开着的终端。"""
    d = period_dir(period)
    for tid in [k for k, t in _TERMS.items() if not _alive(t)]:
        _TERMS.pop(tid, None)
    return [{'id': k, '目录': hosts.label(t['目录']), '开始': t['开始'], '地址': f'/term/{k}/'}
            for k, t in _TERMS.items() if t['期'] == d.name]


def term_tail(tid, chars=3000):
    """最近的输出（去掉颜色和控制字符），「↩ 发给会话」用。"""
    t = _TERMS.get(tid or '')
    if not t:
        raise KBError('终端不在了')
    try:
        with open(t['日志'], 'rb') as fh:
            fh.seek(0, 2)
            fh.seek(max(0, fh.tell() - chars * 6))
            raw = fh.read().decode('utf-8', 'replace')
    except OSError:
        return ''
    txt = _ANSI.sub('', raw)
    txt = re.sub(r'[\x00-\x08\x0b-\x1f\x7f]', '', txt)
    return txt[-chars:]


def term_dirs(period, ends=None, extra=()):
    """终端可选的目录：各端仓库（调用方传 {端: 仓库}）、开发仓库、期目录。"""
    d = period_dir(period)
    out, seen = [], set()
    for e, p in list((ends or {}).items()) + list(extra):
        if p and p not in seen:
            seen.add(p)
            out.append({'名称': f'{e}：{hosts.label(p)}', '路径': p})
    out.append({'名称': f'期目录：{d}', '路径': str(d)})
    return out
