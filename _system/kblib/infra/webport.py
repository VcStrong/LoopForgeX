"""网页（控制面：网页 + HTTP MCP + 执行机协议）端口：启动前改、启动后在平台里改并重启、被占用时自动顺延。

  配置   _system/web.json {"端口": 11666}（文件，启动前就能读；kb port set / 网页「🌐 网页端口」写入）。
  优先级 kb web --port 显式参数 > 环境变量 KB_PORT > web.json > 11555。
  被占   占着的就是这个知识库 → 「已经在运行」；否则往后找空闲端口启动并提示，配置不改，下次仍先试配置的端口。
  实际   本次实际监听的端口和原因写 _system/cache/网页端口.json（kb port、网页顶部提示用）。
  改端口 save() 写配置 + 同步开机自启（LaunchAgent / systemd 不再写死 --port）；网页里改的还会先经心跳告诉执行机新端口，再原地重启。
本机 Claude Code 的 MCP 走 stdio（kb serve），和端口无关；kb token 接入的远程 Claude Code、经反向代理的执行机要自己改地址。
"""
import json
import os
import re
import socket
import threading
import time
import urllib.request
from pathlib import Path

from kblib.core.common import KBError, SYS

DEFAULT = 11555
CONF = SYS / 'web.json'
STATE = SYS / 'cache' / '网页端口.json'
SPAN = 50                   # 被占时往后找多少个
RUNNING = {'port': 0, 'host': ''}     # 本进程实际监听的端口（server.main 填）
MOVE = {'端口': 0, '旧端口': 0, '通知到': set(), '时间': 0}     # 网页里改端口：等执行机心跳拿到新端口再重启
LAUNCH_AGENT = Path.home() / 'Library' / 'LaunchAgents' / 'com.local.kb.web.plist'
SYSTEMD_UNIT = Path.home() / '.config' / 'systemd' / 'user' / 'kb-web.service'


def _valid(port):
    try:
        p = int(port)
    except (TypeError, ValueError):
        raise KBError('端口要是数字') from None
    if not 1024 <= p <= 65535:
        raise KBError('端口要在 1024–65535 之间（1024 以下要 root 权限）')
    return p


def configured():
    """(端口, 来源)：不看命令行参数。"""
    env = (os.environ.get('KB_PORT') or '').strip()
    if env:
        return _valid(env), '环境变量 KB_PORT'
    try:
        p = json.loads(CONF.read_text('utf-8')).get('端口')
        if p:
            return _valid(p), '_system/web.json'
    except (OSError, ValueError, KBError):
        pass
    return DEFAULT, '默认'


def resolve(cli_port=None):
    if cli_port:
        return _valid(cli_port), '命令行 --port'
    return configured()


def in_container():
    return Path('/.dockerenv').exists() or os.environ.get('KB_IN_DOCKER') == '1'


def is_free(port, host='127.0.0.1'):
    for h in {host, '127.0.0.1'} if host not in ('0.0.0.0', '::') else {host}:
        s = socket.socket(socket.AF_INET6 if ':' in h else socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind((h, port))
        except OSError:
            return False
        finally:
            s.close()
    return True


def is_this_kb(port):
    """占着端口的是不是这个知识库（同一个 KB_ROOT）：/api/web/whoami 返回本库标识。"""
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/web/whoami', timeout=2) as r:
            return json.loads(r.read() or b'{}').get('root') == root_id()
    except Exception:       # noqa: BLE001
        return False


def root_id():
    import hashlib
    return hashlib.sha256(str(SYS.resolve()).encode()).hexdigest()[:16]     # 不对外暴露路径


def pick(host, port):
    """返回 (实际端口, 提示)。本库已经在跑（配置端口或上次顺延到的端口上）抛 KBError「已经在运行」。"""
    run = actual()
    if run and not run.get('已停止') and int(run.get('pid') or 0) != os.getpid() and is_this_kb(int(run.get('端口') or 0)):
        raise _running(run['端口'])
    if is_free(port, host):
        return port, ''
    if is_this_kb(port):
        raise _running(port)
    for p in range(port + 1, min(port + SPAN, 65535) + 1):
        if is_free(p, host):
            return p, f'端口 {port} 被别的程序占用，已改用 {p}（配置没改，下次仍先试 {port}；要固定换端口：kb port set <端口>）'
        if is_this_kb(p):
            raise _running(p)
    raise KBError(f'端口 {port}–{port + SPAN} 都被占用了：kb port set <别的端口> 后再启动')


def _running(port):
    return KBError(f'这个知识库已经在 http://127.0.0.1:{port} 运行了（要换端口：kb port set <端口>，或网页「⚙ 设置 → 🌐 网页端口」）')


def record_actual(port, want, source, note=''):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({'端口': port, '配置端口': want, '来源': source, '提示': note, 'pid': os.getpid(),
                                 '时间': time.strftime('%Y-%m-%d %H:%M:%S')}, ensure_ascii=False), 'utf-8')


def actual():
    try:
        st = json.loads(STATE.read_text('utf-8'))
    except (OSError, ValueError):
        return {}
    try:
        os.kill(int(st.get('pid') or 0), 0)
    except (OSError, ValueError):
        st['已停止'] = True
    return st


def status(running_port=None):
    want, source = configured()
    st = actual()
    port = running_port or st.get('端口') or want
    fixed = ''
    if in_container():
        fixed = '在 Docker 里运行：容器内端口固定，对外端口改 .env 的 KB_PUBLISH 后 docker compose up -d'
    elif source == '环境变量 KB_PORT':
        fixed = '端口由环境变量 KB_PORT 固定：改环境变量后重启'
    return {'端口': port, '配置端口': want, '来源': source, '提示': st.get('提示', '') if running_port in (None, st.get('端口')) else '',
            '可在线修改': not fixed, '不能改的原因': fixed, '默认': DEFAULT,
            '开机自启': [str(p) for p in (LAUNCH_AGENT, SYSTEMD_UNIT) if p.exists()]}


def save(port, by=''):
    """写配置 + 同步开机自启配置。返回新端口。"""
    p = _valid(port)
    CONF.parent.mkdir(parents=True, exist_ok=True)
    CONF.write_text(json.dumps({'端口': p, '改于': time.strftime('%Y-%m-%d %H:%M:%S'), '改动人': by}, ensure_ascii=False), 'utf-8')
    sync_autostart()
    return p


def reset():
    CONF.unlink(missing_ok=True)
    sync_autostart()
    return DEFAULT


def sync_autostart():
    """开机自启里写死的 --port 去掉（端口改由 web.json 决定）；只动指向本知识库的那份。"""
    kb_bin = str(SYS.parent / 'kb')
    changed = []
    if LAUNCH_AGENT.exists():
        s = LAUNCH_AGENT.read_text('utf-8')
        if kb_bin in s:
            n = re.sub(r'<string>--port</string>\s*<string>\d+</string>', '', s)
            if n != s:
                LAUNCH_AGENT.write_text(n, 'utf-8')
                changed.append(str(LAUNCH_AGENT))
    if SYSTEMD_UNIT.exists():
        s = SYSTEMD_UNIT.read_text('utf-8')
        if kb_bin in s:
            n = re.sub(r' --port \d+', '', s)
            if n != s:
                SYSTEMD_UNIT.write_text(n, 'utf-8')
                changed.append(str(SYSTEMD_UNIT))
    return changed


def strip_port_args(argv):
    """重启用的命令行：去掉 --port N / --port=N，让新进程按配置走。"""
    out, skip = [], False
    for a in argv:
        if skip:
            skip = False
            continue
        if a == '--port':
            skip = True
            continue
        if a.startswith('--port='):
            continue
        out.append(a)
    return out


def check_new(port, current):
    p = _valid(port)
    if p == current:
        raise KBError(f'已经是 {p} 了')
    if not is_free(p, '0.0.0.0' if os.environ.get('KB_HOST') == '0.0.0.0' else '127.0.0.1'):
        raise KBError(f'端口 {p} 已被别的程序占用，换一个')
    return p


# ---------------------------------------------------------------- 网页里改端口：通知执行机 → 原地重启
def begin_move(new, old, runners_online):
    MOVE.update({'端口': new, '旧端口': old, '通知到': set(), '时间': time.time(), '等': set(runners_online)})


def note_runner(rid):
    """执行机心跳时调：有待切换的端口就返回给它（同一台只要拿到一次）。"""
    if not MOVE['端口']:
        return {}
    MOVE['通知到'].add(rid)
    return {'新端口': MOVE['端口'], '旧端口': MOVE['旧端口']}


def restart_after_notify(argv, wait=20):
    """等在线执行机都拿到新端口（最多 wait 秒）再原地重启网页进程。"""
    def run():
        t0 = time.time()
        while time.time() - t0 < wait and not MOVE.get('等', set()) <= MOVE['通知到']:
            time.sleep(0.5)
        time.sleep(1.0)
        try:
            os.execv(argv[0], argv)
        except OSError as e:
            print('execv 失败，退出等 launchd / systemd 拉起：', repr(e), flush=True)
            os._exit(1)
    threading.Thread(target=run, daemon=True).start()
