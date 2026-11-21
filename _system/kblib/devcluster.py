"""kb dev-cluster：没有服务器也能在这台机器上部署和验证多执行机（REQ-032），以及故障演练（REQ-033）。

    kb dev-cluster up [--runners 2] [--port 11655] [--docker]
    kb dev-cluster status
    kb dev-cluster chaos kill-runner | kill-control | net-drop [秒] | disk-full | all [--period 期名]
    kb dev-cluster down [--purge]

独立测试库：~/.kb-devcluster/root（环境变量 KB_DEVCLUSTER_HOME 可改），不碰正在用的 kb web 端口和数据。
  控制面：kb web --port 11655（默认，被占就往后找），只听 127.0.0.1 → 单机模式免登录（浏览器直接打开看「🖥 执行机」）；
          --docker 时容器要连进来，改听 0.0.0.0 → 团队模式，自动建管理员 admin + 随机密码（打印出来，也记在 state.json，权限 600）。
  执行机：dev-1..N，各自 --data ~/.kb-devcluster/runner-<i>（KB_ROOT、HOME、工作副本池都在里面，互不相同）。
  测试库标记 root/_system/测试库：允许 KB_FAKE_CLOCK 假时钟（REQ-048）。
演练直接读测试库 sqlite（runners / tasks / alerts 表）观测；报告写「故障演练报告-<日期>.md」：
  带 --period 写到主知识库这一期的「其他资料」，否则写在 ~/.kb-devcluster/。
"""
import argparse
import json
import os
import secrets
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

PROG = Path(__file__).resolve().parents[1]          # _system
BASE = Path(os.environ.get('KB_DEVCLUSTER_HOME') or Path.home() / '.kb-devcluster').expanduser()
ROOT = BASE / 'root'
STATE = BASE / 'state.json'
DB = ROOT / '_system' / 'kb.db'
IMAGE = 'kb-runner-dev'
DRILL_PERIOD = '故障演练'


def say(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------- 状态
def load_state():
    try:
        return json.loads(STATE.read_text('utf-8'))
    except (OSError, ValueError):
        return {}


def save_state(st):
    BASE.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(STATE), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(st, f, ensure_ascii=False, indent=1)


def alive(pid):
    try:
        os.kill(int(pid), 0)
    except (OSError, TypeError, ValueError):
        return False
    try:                                                # 僵尸进程也算死了
        out = subprocess.run(['ps', '-o', 'stat=', '-p', str(pid)], capture_output=True, text=True, timeout=5).stdout.strip()
        return bool(out) and not out.startswith('Z')
    except (OSError, subprocess.TimeoutExpired):
        return True


def kill(pid, sig=signal.SIGTERM, wait=8):
    if not alive(pid):
        return
    try:
        os.killpg(int(pid), sig)
    except OSError:
        try:
            os.kill(int(pid), sig)
        except OSError:
            return
    t = time.time()
    while alive(pid) and time.time() - t < wait:
        time.sleep(0.2)
    if alive(pid):
        try:
            os.killpg(int(pid), signal.SIGKILL)
        except OSError:
            pass


# ---------------------------------------------------------------- 测试库
def root_env(extra=None):
    env = dict(os.environ, KB_ROOT=str(ROOT), KB_SECRET_BACKEND='file', KB_MASTER_KEY='kb-devcluster-test-key',
               KB_DEVCLUSTER='1', HOME=str(BASE / 'home-control'), PYTHONPATH=str(PROG))
    env.pop('KB_RUNNER_DATA', None)
    env.pop('KB_DB_URL', None)            # 演练用独立 SQLite 测试库，不碰你配置的 PostgreSQL / MySQL
    env.update(extra or {})
    return env


def in_root(code):
    """在测试库里跑一段 Python（本进程已按主库 import 过 kblib，所以起子进程）→ 标准输出。"""
    r = subprocess.run([sys.executable, '-c', 'import sys; sys.path.insert(0, %r); import kb\n' % str(PROG) + code],
                       capture_output=True, text=True, env=root_env(), timeout=120, cwd=str(PROG))
    if r.returncode:
        raise RuntimeError(f'测试库操作失败：{(r.stderr or r.stdout).strip()[-600:]}')
    return r.stdout.strip()


def sql(query, args=()):
    if not DB.exists():
        return []
    c = sqlite3.connect(f'file:{DB}?mode=ro', uri=True, timeout=5)
    c.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in c.execute(query, args)]
    except sqlite3.OperationalError:
        return []
    finally:
        c.close()


def runners_db(names=None):
    rs = sql('SELECT * FROM runners')
    return [r for r in rs if not names or r['name'] in names]


def is_online(r, fresh=45):
    return r and r.get('status') in ('在线', '排空中') and time.time() - float(r.get('last_hb') or 0) < fresh


def free_port(start):
    for p in range(start, start + 200):
        with socket.socket() as s:
            try:
                s.bind(('127.0.0.1', p))
                return p
            except OSError:
                continue
    raise RuntimeError('找不到空闲端口')


def http_ok(url, timeout=3):
    try:
        with urllib.request.urlopen(url + '/login', timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


# ---------------------------------------------------------------- 起停
def start_control(st):
    log = BASE / 'control.log'
    p = subprocess.Popen([sys.executable, str(PROG / 'kb.py'), 'web', '--port', str(st['port']), '--host', st['host']],
                         env=root_env(), stdout=open(log, 'ab'), stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                         start_new_session=True, cwd=str(PROG))
    st['control'] = {'pid': p.pid, 'log': str(log), '启动': time.time()}
    for _ in range(150):
        if http_ok(st['url']):
            return True
        if p.poll() is not None:
            break
        time.sleep(0.2)
    raise RuntimeError(f'控制面没起来，看日志：{log}\n' + log.read_text('utf-8', 'replace')[-800:])


def reg_token(name):
    return in_root(f'from kblib.infra import deploy\nt, _ = deploy.runner_reg_token({name!r}, "dev-cluster")\nprint(t)')


def start_runner(st, i, token=None):
    name = f'dev-{i}'
    data = BASE / f'runner-{i}'
    (data / 'home').mkdir(parents=True, exist_ok=True)
    (data / 'fake_disk').unlink(missing_ok=True)     # 上次演练被打断时留下的模拟磁盘满，别带进新一轮
    log = data / 'runner.log'
    args = [sys.executable, '-m', 'kblib.runner.agent', '--server', st['url'], '--name', name, '--data', str(data)]
    if token is None:      # down 会作废执行机令牌：再 up 时总是领新注册令牌，已注册过的用 --rebind 保留原 ID
        token = reg_token(name)
    if token:
        args += ['--token', token] + (['--rebind'] if (data / 'runner.json').exists() else [])
    env = dict(os.environ, KB_ROOT=str(data / 'root'), KB_RUNNER_DATA=str(data), HOME=str(data / 'home'),
               PYTHONPATH=str(PROG), KB_SECRET_BACKEND='file')
    p = subprocess.Popen(args, env=env, stdout=open(log, 'ab'), stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                         start_new_session=True, cwd=str(PROG))
    rs = [r for r in st.setdefault('runners', []) if r['name'] != name]
    rs.append({'name': name, 'pid': p.pid, 'data': str(data), 'log': str(log), 'i': i})
    st['runners'] = sorted(rs, key=lambda r: r['i'])
    return p.pid


def docker_bin():
    """docker 命令：PATH 里没有时找 Docker Desktop / OrbStack 的默认位置（网页起的进程 PATH 常常不带它们）。"""
    for c in (shutil.which('docker'), Path.home() / '.docker/bin/docker', Path.home() / '.orbstack/bin/docker',
              '/usr/local/bin/docker', '/Applications/Docker.app/Contents/Resources/bin/docker'):
        if c and Path(c).exists():
            d = str(Path(c).parent)              # 凭据助手 docker-credential-* 也在这个目录，要在 PATH 里
            if d not in os.environ.get('PATH', '').split(':'):
                os.environ['PATH'] = d + ':' + os.environ.get('PATH', '')
            return str(c)
    return ''


def start_docker(st, i):
    d = docker_bin()
    if not d:
        say('！没装 docker（OrbStack / colima / Docker Desktop 任一个都行），跳过容器执行机')
        return ''
    if subprocess.run([d, 'image', 'inspect', IMAGE], capture_output=True).returncode:
        say('构建执行机镜像（第一次要几分钟）…')
        extra = [x for k, v in (('BASE_IMAGE', 'KB_BASE_IMAGE'), ('NODE_IMAGE', 'KB_NODE_IMAGE'), ('APT_MIRROR', 'KB_APT_MIRROR'), ('PIP_INDEX', 'KB_PIP_INDEX'))
                 if os.environ.get(v) for x in ('--build-arg', f'{k}={os.environ[v]}')]   # 国内网络用镜像源，同 docker.env.example
        r = subprocess.run([d, 'build', '--build-arg', 'WITH_ENGINES=0', *extra, '-f', str(PROG / 'Dockerfile.runner'), '-t', IMAGE, str(PROG.parent)])   # 演练用假引擎，不装 Claude / Codex
        if r.returncode:
            say('✗ 镜像构建失败，跳过容器执行机')
            return ''
    name = f'kb-dev-docker-{i}'
    subprocess.run([d, 'rm', '-f', name], capture_output=True)
    tok = reg_token(f'docker-{i}')
    r = subprocess.run([d, 'run', '-d', '--name', name, '--add-host', 'host.docker.internal:host-gateway', IMAGE,
                        'runner', '--server', f'http://host.docker.internal:{st["port"]}', '--token', tok, '--name', f'docker-{i}'],
                       capture_output=True, text=True)
    if r.returncode:
        say(f'✗ 容器起不来：{r.stderr.strip()[-300:]}')
        return ''
    st.setdefault('docker', []).append(name)
    return name


def wait_online(names, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        rs = {r['name']: r for r in runners_db(names)}
        if all(is_online(rs.get(n)) for n in names):
            return time.time() - t0
        time.sleep(1)
    return None


def up(n=2, port=0, docker=False):
    st = load_state()
    if st.get('control') and alive(st['control']['pid']):
        say(f'已经在跑：{st["url"]}（先 kb dev-cluster down）')
        return status()
    (ROOT / '_system').mkdir(parents=True, exist_ok=True)
    (ROOT / '_system' / '测试库').write_text('dev-cluster 测试库：允许 KB_FAKE_CLOCK 假时钟\n', 'utf-8')
    (BASE / 'home-control').mkdir(parents=True, exist_ok=True)
    port = port or free_port(11655)
    host = '0.0.0.0' if docker else '127.0.0.1'
    st = {'port': port, 'host': host, 'url': f'http://127.0.0.1:{port}', 'runners': [], 'docker': [], '时间': time.strftime('%Y-%m-%d %H:%M:%S')}
    in_root('kb.init()')
    if docker:
        pw = secrets.token_urlsafe(9)
        in_root('from kblib.core import users, db\n'
                f'u = db.user_get("admin")\n'
                f'users.user_save("admin", {pw!r}, "管理员", by="dev-cluster")')
        st['admin'] = {'用户名': 'admin', '密码': pw}
    start_control(st)
    save_state(st)
    say(f'控制面：{st["url"]}（测试库 {ROOT}，日志 {st["control"]["log"]}）')
    for i in range(1, n + 1):
        start_runner(st, i)
    if docker:
        start_docker(st, 1)
    save_state(st)
    names = [r['name'] for r in st['runners']]
    took = wait_online(names, 60)
    if took is None:
        say(f'✗ 60 秒内没有全部在线：看各执行机日志 {BASE}/runner-*/runner.log')
        status()
        return 1
    say(f'✓ {len(names)} 台本机执行机 {took:.0f} 秒内全部在线：' + '、'.join(names))
    if st.get('docker'):
        ok = wait_online([f'docker-{i}' for i in range(1, len(st['docker']) + 1)], 120)
        say('✓ 容器执行机在线（自动标签含 linux）' if ok is not None else '✗ 容器执行机 2 分钟内没上线：docker logs kb-dev-docker-1')
    if st.get('admin'):
        say(f'网页要登录：admin / {st["admin"]["密码"]}（首次登录要改密码；也记在 {STATE}）')
    say(f'打开 {st["url"]} →「⚙ 设置 → 🖥 执行机」查看；停掉：kb dev-cluster down')
    return 0


def deregister(names):
    if not names or not DB.exists():
        return
    try:
        in_root('from kblib.core import db\n'
                f'for r in db.rows("runners"):\n'
                f'    if r["name"] in {list(names)!r}:\n'
                f'        db.update("runners", {{"id": r["id"]}}, status="停用", disabled=1)\n'
                f'        db.token_revoke(kind="执行机", runner=r["id"])')
    except RuntimeError as e:
        say(f'！注销执行机失败：{e}')


def down(purge=False):
    st = load_state()
    names = [r['name'] for r in st.get('runners') or []]
    for r in st.get('runners') or []:
        kill(r['pid'])
    d = docker_bin()
    for c in st.get('docker') or []:
        if d:
            subprocess.run([d, 'rm', '-f', c], capture_output=True)
        names.append(c.replace('kb-dev-', ''))
    deregister(names)
    if st.get('control'):
        kill(st['control']['pid'])
    if purge:
        shutil.rmtree(BASE, ignore_errors=True)
        say(f'已停止并删除测试库 {BASE}')
    else:
        st.update(control=None, runners=[], docker=[])
        save_state(st)
        say(f'已停止、执行机已注销；测试库保留在 {ROOT}（复查用，kb dev-cluster down --purge 删除）')
    return 0


def status():
    st = load_state()
    c = st.get('control') or {}
    say(f'控制面：{st.get("url", "未启动")} ' + ('运行中' if c and alive(c.get('pid')) else '未运行') + f'（测试库 {ROOT}）')
    pids = {r['name']: r['pid'] for r in st.get('runners') or []}
    for r in runners_db():
        run = sql("SELECT count(*) n FROM tasks WHERE runner=? AND status IN ('已派','运行','等回答')", (r['id'],))
        hb = time.strftime('%H:%M:%S', time.localtime(float(r.get('last_hb') or 0))) if r.get('last_hb') else '无'
        say(f'- {r["name"]}（{r["id"]}）{r["status"]} · 最后心跳 {hb} · 在跑 {run[0]["n"] if run else 0} · '
            f'进程 {"活" if alive(pids.get(r["name"])) else "无"}')
    return 0


# ---------------------------------------------------------------- 演练
def submit_task(prompt='SLOW 故障演练', kind='插件'):
    """建一个 mock 引擎任务：优先走控制面的队列（core.queue.submit），没有就直接写任务表。→ 任务编号。"""
    code = f'''
import json, time, uuid
from kblib.core import db
try:
    kb.create_period({DRILL_PERIOD!r}, 'dev-cluster 故障演练用')
except Exception:
    pass
payload = {{'引擎': 'mock', '提示词': {prompt!r}}}
tid = ''
try:
    from kblib.core import queue as q
    t = q.submit({kind!r}, {DRILL_PERIOD!r}, payload)
    tid = (t.get('task') or t).get('id') if isinstance(t, dict) else str(t)
except Exception as e:
    tid = ''
if not tid:
    tid = 't' + uuid.uuid4().hex[:10]
    db.insert('tasks', id=tid, type={kind!r}, period={DRILL_PERIOD!r}, project='default', initiator='dev-cluster',
              status='排队', payload=payload, created=time.time(), updated=time.time())
print(tid)
'''
    return in_root(code).splitlines()[-1]


def task_row(tid):
    r = sql('SELECT * FROM tasks WHERE id=?', (tid,))
    return r[0] if r else {}


def wait_for(fn, timeout, step=1.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = fn()
        if v:
            return v, time.time() - t0
        time.sleep(step)
    return None, time.time() - t0


def _runner_by_id(rid):
    r = sql('SELECT * FROM runners WHERE id=?', (rid,))
    return r[0] if r else {}


def _proc_of(st, rid):
    name = _runner_by_id(rid).get('name')
    return next((r for r in st.get('runners') or [] if r['name'] == name), None)


def _running_task(prompt='SLOW 故障演练'):
    tid = submit_task(prompt)
    t, took = wait_for(lambda: (lambda r: r if r.get('status') in ('运行', '已派') and r.get('runner') else None)(task_row(tid)), 120)
    return tid, t


def chaos_kill_runner(st):
    ev = []
    tid, t = _running_task()
    if not t:
        return False, [f'任务 {tid} 2 分钟内没被领走（{task_row(tid).get("status")} · {task_row(tid).get("wait_reason", "")}）']
    rid = t['runner']
    pr = _proc_of(st, rid)
    if not pr:
        return False, [f'任务派给了 {rid}，但它不是 dev-cluster 起的进程']
    ev.append(f'任务 {tid} 在 {pr["name"]}（{rid}）上{t["status"]}；kill -9 执行机进程 {pr["pid"]}')
    kill(pr['pid'], signal.SIGKILL, wait=3)
    t0 = time.time()
    off, a = wait_for(lambda: not is_online(_runner_by_id(rid), fresh=10 ** 9) or None, 150, 2)
    ev.append(f'{"✓" if off else "✗"} 执行机标离线：{a:.0f} 秒（要求 ≤120）')
    moved, b = wait_for(lambda: (lambda r: r if r.get('runner') and r['runner'] != rid and r.get('status') in ('已派', '运行', '完成') else None)(task_row(tid)), 180, 2)
    if moved:
        ev.append(f'✓ 任务回队列后被 {_runner_by_id(moved["runner"]).get("name")} 领走：杀进程后 {time.time() - t0:.0f} 秒')
    else:
        r = task_row(tid)
        ev.append(f'✗ 任务没被另一台领走：状态 {r.get("status")}，执行机 {r.get("runner")}，等待原因 {r.get("wait_reason", "")}')
    cps = sql('SELECT info FROM runners WHERE id=?', (rid,))
    ev.append('副本「可能有改动」标记：' + ('见执行机 info.副本' if cps else '无副本（演练任务不带仓库）'))
    start_runner(st, pr['i'])
    save_state(st)
    back = wait_online([pr['name']], 60)
    ev.append(f'已重启 {pr["name"]}：' + (f'{back:.0f} 秒恢复在线' if back is not None else '60 秒内没恢复'))
    return bool(off and a <= 125 and moved), ev


def chaos_kill_control(st):
    ev = []
    tid, t = _running_task('SLOW 控制面重启演练')
    if not t:
        return False, [f'任务 {tid} 没被领走']
    rid = t['runner']
    pr = _proc_of(st, rid)
    logf = Path(pr['data']) / 'logs' / f'{tid}.log' if pr else None
    ev.append(f'任务 {tid} 在 {_runner_by_id(rid).get("name")} 上运行；杀控制面 {st["control"]["pid"]}')
    kill(st['control']['pid'], signal.SIGKILL, wait=3)
    time.sleep(5)
    start_control(st)
    save_state(st)
    t0 = time.time()
    ev.append('控制面已重启')
    names = [r['name'] for r in st.get('runners') or []]
    ok, a = wait_for(lambda: all(float(r.get('last_hb') or 0) > t0 for r in runners_db(names)) or None, 45, 1)
    ev.append(f'{"✓" if ok and a <= 30 else "✗"} 全部执行机重连：{a:.0f} 秒（要求 ≤30）')
    size0 = logf.stat().st_size if logf and logf.exists() else 0
    time.sleep(6)
    r = task_row(tid)
    grew = logf and logf.exists() and logf.stat().st_size > size0
    alive_ok = r.get('status') in ('运行', '已派', '完成')
    ev.append(f'{"✓" if alive_ok else "✗"} 会话没被杀：任务状态 {r.get("status")}，本地日志' + ('在增长' if grew else '没增长（可能已跑完）')
              + f'，控制面收到日志 {r.get("log_size", 0)} 字节')
    return bool(ok and a <= 30 and alive_ok), ev


def chaos_net_drop(st, secs=130):
    ev = []
    pr = (st.get('runners') or [None])[-1]
    if not pr or not alive(pr['pid']):
        return False, ['没有在跑的执行机']
    rid = next((r['id'] for r in runners_db([pr['name']])), '')
    ev.append(f'冻结 {pr["name"]}（SIGSTOP 进程 {pr["pid"]}）{secs} 秒，模拟断网')
    os.kill(pr['pid'], signal.SIGSTOP)
    went_off = None
    t0 = time.time()
    while time.time() - t0 < secs:
        if went_off is None and not is_online(_runner_by_id(rid), fresh=10 ** 9):
            went_off = time.time() - t0
        time.sleep(2)
    os.kill(pr['pid'], signal.SIGCONT)
    ev.append(f'{"✓" if went_off is not None else "✗"} 断网期间标离线：' + (f'{went_off:.0f} 秒' if went_off is not None else f'{secs} 秒内没标'))
    t1 = time.time()
    back, b = wait_for(lambda: (lambda r: r if r.get('status') == '在线' and float(r.get('last_hb') or 0) > t1 else None)(_runner_by_id(rid)), 60, 1)
    ev.append(f'{"✓" if back else "✗"} 恢复网络后重新上线：{b:.0f} 秒')
    tid = submit_task('演练：恢复后领活')
    done, c = wait_for(lambda: (lambda r: r if r.get('runner') else None)(task_row(tid)), 90, 2)
    ev.append(f'{"✓" if done else "✗"} 恢复后集群继续领活：任务 {tid} → ' + (_runner_by_id(done['runner']).get('name', '') if done else '没被领走'))
    return bool(went_off is not None and back and done), ev


def chaos_disk_full(st, wait_alert=360):
    ev = []
    pr = (st.get('runners') or [None])[0]
    if not pr:
        return False, ['没有执行机']
    rid = next((r['id'] for r in runners_db([pr['name']])), '')
    f = Path(pr['data']) / 'fake_disk'
    f.write_text('0.95')
    ev.append(f'{pr["name"]} 打开模拟磁盘：使用 95%（{f}）；等心跳上报')
    time.sleep(20)
    tids = [submit_task(f'演练：磁盘满时派活 {i}') for i in range(3)]
    time.sleep(30)
    to_full = [t for t in tids if task_row(t).get('runner') == rid]
    ev.append(f'{"✓" if not to_full else "✗"} 磁盘满的执行机不再接新活：3 个新任务派到 '
              + '、'.join(_runner_by_id(task_row(t).get('runner', '')).get('name', '未派') for t in tids))
    al, a = wait_for(lambda: sql("SELECT * FROM alerts WHERE runner=? AND kind='磁盘' AND (end IS NULL OR end=0)", (rid,)) or None,
                     wait_alert, 5)
    ev.append(f'{"✓" if al else "✗"} 磁盘告警：' + (f'{al[0].get("level")}，{a:.0f} 秒出现' if al else f'{wait_alert} 秒内没有'))
    f.unlink(missing_ok=True)
    ev.append('已关掉模拟磁盘')
    return bool(not to_full and al), ev


CHAOS = {'kill-runner': chaos_kill_runner, 'kill-control': chaos_kill_control, 'net-drop': chaos_net_drop, 'disk-full': chaos_disk_full}


def chaos(items, secs=130, period=''):
    st = load_state()
    if not st.get('control') or not alive(st['control']['pid']):
        say('先 kb dev-cluster up')
        return 1
    if len([r for r in runners_db([x['name'] for x in st.get('runners') or []]) if is_online(r)]) < 2 and 'kill-runner' in items:
        say('！kill-runner 要至少 2 台在线执行机（kb dev-cluster up --runners 2）')
    results = []
    for it in items:
        say(f'== 演练 {it} …')
        t0 = time.time()
        try:
            ok, ev = CHAOS[it](st, secs) if it == 'net-drop' else CHAOS[it](st)
        except Exception as e:      # noqa: BLE001
            ok, ev = False, [f'演练出错：{e!r}']
        results.append((it, ok, time.time() - t0, ev))
        say(('✓ 通过' if ok else '✗ 不通过') + '\n  ' + '\n  '.join(ev))
    path = write_report(results, period)
    say(f'报告：{path}')
    return 0 if all(ok for _, ok, _, _ in results) else 1


def write_report(results, period=''):
    day = time.strftime('%Y-%m-%d')
    lines = [f'# 故障演练报告 {day}', '',
             f'- 时间：{time.strftime("%Y-%m-%d %H:%M:%S")}', f'- 环境：kb dev-cluster（测试库 {ROOT}，控制面 {load_state().get("url", "")}）',
             f'- 执行机：' + '、'.join(f'{r["name"]}（{r["status"]}）' for r in runners_db()), '',
             '| 演练 | 结果 | 用时 |', '|---|---|---|']
    names = {'kill-runner': '杀执行机（REQ-033 ①）', 'kill-control': '杀控制面后重启（②）', 'net-drop': '断网 120 秒以上（③）', 'disk-full': '磁盘满（④）'}
    for it, ok, took, _ in results:
        lines.append(f'| {names.get(it, it)} | {"✅ 通过" if ok else "❌ 不通过"} | {took:.0f} 秒 |')
    lines.append('')
    for it, ok, took, ev in results:
        lines += [f'## {names.get(it, it)}：{"通过" if ok else "不通过"}', ''] + [f'- {e}' for e in ev] + ['']
    lines.append(f'结论：{"全部通过" if all(ok for _, ok, _, _ in results) else "有不通过的项，阶段二不能上线"}')
    text = '\n'.join(lines) + '\n'
    fname = f'故障演练报告-{day}.md'
    if period:
        try:
            from kblib.core.periods import write_doc
            write_doc(period, '其他资料', fname, text, 'kb dev-cluster chaos 生成')
            return f'已写进「{period}」的其他资料：{fname}'
        except Exception as e:      # noqa: BLE001
            say(f'！写进期失败（{e}），改写到本地')
    f = BASE / fname
    BASE.mkdir(parents=True, exist_ok=True)
    f.write_text(text, 'utf-8')
    return str(f)


def main(argv=None):
    ap = argparse.ArgumentParser(prog='kb dev-cluster', description='本机验证多执行机与故障演练（独立测试库）')
    sp = ap.add_subparsers(dest='op')
    x = sp.add_parser('up')
    x.add_argument('--runners', type=int, default=2)
    x.add_argument('--port', type=int, default=0)
    x.add_argument('--docker', action='store_true', help='另起一个 Linux 容器执行机（要装 docker / OrbStack / colima）')
    x = sp.add_parser('down')
    x.add_argument('--purge', action='store_true', help='连测试库一起删')
    sp.add_parser('status')
    x = sp.add_parser('chaos')
    x.add_argument('item', choices=[*CHAOS, 'all'])
    x.add_argument('secs', nargs='?', type=int, default=130, help='net-drop 断网秒数')
    x.add_argument('--period', default='', help='报告写到主知识库这一期的「其他资料」')
    a = ap.parse_args(argv)
    try:
        if a.op == 'up':
            return up(a.runners, a.port, a.docker)
        if a.op == 'down':
            return down(a.purge)
        if a.op == 'chaos':
            return chaos(list(CHAOS) if a.item == 'all' else [a.item], a.secs, a.period)
        return status()
    except RuntimeError as e:
        say(f'✗ {e}')
        return 1
