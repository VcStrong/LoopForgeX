"""执行机主循环（REQ-011~REQ-018 REQ-029 REQ-033 REQ-035 REQ-037 REQ-039 REQ-049 REQ-050）。

    kb runner --server <控制面地址> --token <注册令牌> [--name 名称] [--data ~/.kb-runner] [--ca 证书] [--rebind] [--capacity N]

线程：心跳（15 秒）、领活（长轮询 50 秒）、每个会话一个。只出站，不监听端口。
控制面连不上时：在跑的会话不杀，日志留在本地文件，恢复后从断点继续回传；重连间隔指数退避，最长 10 秒。

会话目录：<数据目录>/sessions/<任务>/home（私有 HOME，结束删除）；日志 <数据目录>/logs/<任务>.log（保留 7 天）。
done 里的扩展字段（契约之外、控制面可选处理）：
  登录态回写 {账号, 登录态: 更新后的完整登录态, 文件: {相对路径: 内容}}   会话里引擎刷新过的（REQ-039，控制面 accounts.writeback_login(账号, 登录态)）
  日志尾       失败时最后 200 行（REQ-049）
心跳里的扩展字段：自检、版本、协议版本、清理（REQ-037 删了哪些副本）、审计（如残留会话清理，REQ-039）。
"""
import argparse
import codecs
import glob
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from kblib.runner import PROTO, data_dir
from kblib.runner.client import ApiError, Client, NetError, NetStats
from kblib.runner.home import SessionHome, cleanup_leftovers
from kblib.runner import sysinfo

HB_SEC = 15
CLAIM_WAIT = 50
EVENT_SEC = 2
MAX_BACKOFF = 10
LOG_KEEP_DAYS = 7
EVIDENCE_MAX = 20 * 1024 * 1024
BAD_TOKEN = '注册令牌无效或已用过，到网页重新生成'
EXTERNAL = re.compile(r'\b(429|529|503)\b|rate.?limit|overloaded|too many requests|ECONNRESET|ETIMEDOUT|ENOTFOUND|'
                      r'network (error|is unreachable)|could not resolve|connection (refused|reset)|timed? ?out', re.I)
FALLBACK_ARGS = ['--permission-mode', 'acceptEdits', '--allowedTools', 'mcp__kb,Bash,WebFetch,WebSearch']

MOCK_ENGINE = r'''
import os, sys, time, subprocess
p = sys.argv[1] if len(sys.argv) > 1 else ''
step = float(os.environ.get('KB_MOCK_STEP', '0.3'))
print('mock 引擎：开始，提示词', len(p), '字', flush=True)
n = int(os.environ.get('KB_MOCK_LINES', '5'))
if 'SLOW' in p:
    n = int(os.environ.get('KB_MOCK_SLOW_LINES', '200'))
for i in range(n):
    print(f'mock 引擎：第 {i + 1} 步', flush=True)
    time.sleep(step)
if 'REFRESH' in p:
    f = os.path.join(os.environ['HOME'], '.claude', '.credentials.json')
    os.makedirs(os.path.dirname(f), exist_ok=True)
    open(f, 'w').write('{"refreshed": true}')
if 'COMMIT' in p and os.path.isdir('.git'):
    open('mock.txt', 'a').write('x\n')
    subprocess.run(['git', 'add', 'mock.txt']); subprocess.run(['git', 'commit', '-qm', 'mock 提交'])
if 'FAIL429' in p:
    print('Error: 429 rate limit exceeded', flush=True); sys.exit(1)
if 'FAIL' in p:
    print('Error: mock 引擎按要求失败', flush=True); sys.exit(2)
print('mock 引擎：完成', flush=True)
'''


def log(*a):
    print(time.strftime('%H:%M:%S'), *a, flush=True)


# ---------------------------------------------------------------- 本机配置（runner.json，权限 600）
def cfg_path(data):
    return Path(data) / 'runner.json'


def load_cfg(data):
    try:
        return json.loads(cfg_path(data).read_text('utf-8'))
    except (OSError, ValueError):
        return {}


def save_cfg(data, cfg):
    p = cfg_path(data)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)
    os.chmod(p, 0o600)


def version():
    try:
        from kblib.steward.update import code_version
        return code_version() or 'dev'
    except Exception:
        return 'dev'


def register(server, token, name, data, ca='', rebind=False):
    """注册（或 --rebind 换绑到新控制面）→ 保存执行机令牌。注册令牌无效 → 退出码 2。"""
    cfg = load_cfg(data)
    tags, checks = sysinfo.tags_and_checks(force=True)
    body = {'name': name or cfg.get('name') or sysinfo.machine_name(), 'version': version(), '协议版本': PROTO,
            '自检': checks, '自动标签': tags}
    if rebind and cfg.get('id'):
        body['rebind_id'] = cfg['id']
        token = token or (cfg.get('tokens') or {}).get(server) or cfg.get('token', '')
    try:
        r = Client(server, token, ca).post('/api/runner/register', body)
    except ApiError as e:
        if e.status in (400, 401, 403, 404, 409):
            print(BAD_TOKEN + (f'（{e.body.get("error")}）' if e.body.get('error') else ''), file=sys.stderr)
            sys.exit(2)
        raise
    if not r.get('token') or not r.get('id'):
        print(BAD_TOKEN, file=sys.stderr)
        sys.exit(2)
    toks = dict(cfg.get('tokens') or {})
    toks[server] = r['token']                            # 每个控制面一个令牌：切回旧控制面时还能用（REQ-034）
    cfg.update(server=server, id=r['id'], token=r['token'], name=body['name'], ca=ca or cfg.get('ca', ''), tokens=toks,
               标签=r.get('标签') or tags, 注册时间=time.strftime('%Y-%m-%d %H:%M:%S'))
    save_cfg(data, cfg)
    log(f'已注册为执行机 {r["id"]}（{body["name"]}），令牌存在 {cfg_path(data)}')
    return cfg


# ---------------------------------------------------------------- 工作副本（本机池）
def _git(path, *a, timeout=120):
    try:
        return subprocess.run(['git', '-C', str(path), *a], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(a, 124, '', '超时')


def ensure_repo(name, url='', post=''):
    """控制面派来的仓库在本机池里登记（同名、同地址）。→ 仓库名；没有地址又没登记过返回 ''。"""
    from kblib.infra import pool
    if name in pool.pool_repos():
        return name
    url = url or (name if pool.is_clone_url(name) else '')
    if not url:
        return ''
    if pool.is_clone_url(name):
        return pool._register(url, post)
    repos = pool._load_repos()
    repos[name] = {'克隆地址': url, '克隆后命令': post or '', '路径': str(pool.repo_home(name) / '1'),
                   '加入时间': time.strftime('%Y-%m-%d %H:%M'), '来自': '执行机'}
    pool.REPOS_FILE.parent.mkdir(parents=True, exist_ok=True)
    pool.REPOS_FILE.write_text(json.dumps(repos, ensure_ascii=False, indent=2), 'utf-8')
    return name


def copy_info(path, task=''):
    from kblib.infra import pool
    c = pool.copy_of(path)
    if not c:
        return {'仓库': '', '序号': 0, '路径': str(path), '分支': '', '马甲线': '', '占用会话': task or None, '可能有改动': False}
    x = pool._state().get(str(Path(path).resolve())) or {}
    return {'仓库': c[0], '序号': c[1], '路径': str(path), '分支': _git(path, 'branch', '--show-current').stdout.strip(),
            '马甲线': x.get('马甲线', ''), '占用会话': task or None, '可能有改动': False}


# ---------------------------------------------------------------- 会话
class Session(threading.Thread):
    def __init__(self, agent, task):
        super().__init__(daemon=True, name=f'session-{task.get("id")}')
        self.a, self.t, self.id = agent, task, str(task['id'])
        self.proc = None
        self.cancel = threading.Event()
        self.state = '已派'
        self.path = ''
        self.home = None
        self.logf = Path(agent.data) / 'logs' / f'{self.id}.log'
        self.sent = 0
        self.start_ts = time.time()

    # ---- 回传
    def _event(self, final=False):
        """把日志增量发给控制面（失败不前进偏移，下次重发）。→ 是否要求取消。"""
        try:
            data = self.logf.read_bytes()[self.sent:] if self.logf.exists() else b''
        except OSError:
            data = b''
        chunk = data[:64 * 1024]
        dec = codecs.getincrementaldecoder('utf-8')('replace')
        text = dec.decode(chunk, final=final)              # 末尾半个汉字留到下次
        chunk = chunk[:len(chunk) - len(dec.getstate()[0])]
        body = {'task': self.id, '状态': '运行', '日志': text, '偏移': self.sent}
        try:
            r = self.a.client.post('/api/runner/event', body)
        except (NetError, ApiError):
            return False
        self.sent += len(chunk)
        self.a.reported[self.id] = self.state
        return bool(r.get('cancel'))

    def _flush(self):
        for _ in range(200):                      # 剩下的日志都发完（控制面断着就等它回来）
            if not self.logf.exists() or self.sent >= self.logf.stat().st_size:
                return
            before = self.sent
            self._event(final=True)
            if self.sent == before:
                time.sleep(min(MAX_BACKOFF, 2))

    def _done(self, body):
        """完成回报：控制面断着就一直重试（指数退避），4xx 表示任务已经不归这台了，放弃。"""
        n = 0
        while True:
            try:
                self.a.client.post('/api/runner/done', body)
                return True
            except ApiError as e:
                log(f'任务 {self.id} 完成回报被拒：{e}')
                return False
            except NetError:
                n += 1
                if n > 2000:
                    return False
                time.sleep(min(MAX_BACKOFF, 2 ** min(n, 4)))

    # ---- 准备
    def _prepare(self):
        t = self.t
        repo = str(t.get('仓库') or '')
        name = ensure_repo(repo, t.get('克隆地址', ''), t.get('克隆后命令', '')) if repo else ''
        if name:
            from kblib.infra import pool
            from kblib.runner import gitlock
            with gitlock.repo_lock(name):               # 同一仓库的 fetch / clone / 切分支串行（REQ-015）
                self.path = pool.pool_claim(name, self.id, t.get('分支', ''), base=t.get('马甲线', ''))
        else:
            d = Path(self.a.data) / 'sessions' / self.id / 'work'
            d.mkdir(parents=True, exist_ok=True)
            self.path = str(d)
        return copy_info(self.path, self.id)

    def _ack(self, copy):
        deadline = time.time() + 110
        while time.time() < deadline:
            try:
                self.a.client.post('/api/runner/ack', {'task': self.id, '副本': copy})
                return True
            except ApiError as e:
                log(f'任务 {self.id} 确认被拒（已被收回？）：{e}')
                return False
            except NetError:
                time.sleep(3)
        return False

    def _command(self):
        t = self.t
        if isinstance(t.get('命令'), list) and t['命令']:
            return [str(x) for x in t['命令']]
        if self.t.get('类型') == '构建安装' and t.get('构建命令'):
            return ['sh', '-c', t['构建命令']]
        eng = str(t.get('引擎') or 'mock').lower()
        prompt = t.get('提示词') or ''
        if 'mock' in eng:
            return [sys.executable, '-c', MOCK_ENGINE, prompt]
        if 'codex' in eng:
            return ['codex', 'exec', prompt]
        cmd = ['claude', '-p', prompt, '--output-format', 'stream-json', '--verbose', *FALLBACK_ARGS]
        if t.get('sessionToken'):
            cfg = {'mcpServers': {'kb': {'type': 'http', 'url': self.a.server + '/mcp',
                                         'headers': {'Authorization': f'Bearer {t["sessionToken"]}'}}}}
            cmd += ['--mcp-config', json.dumps(cfg, ensure_ascii=False)]
        return cmd

    def _env(self):
        env = {k: v for k, v in os.environ.items() if k not in ('HOME', 'KB_ROOT', 'KB_RUNNER_DATA', 'PYTHONPATH')}
        env.update(self.home.env())
        env.update(KB_SESSION=self.id, KB_PERIOD=str(self.t.get('期') or ''), KB_MCP_URL=self.a.server + '/mcp',
                   KB_MCP_TOKEN=str(self.t.get('sessionToken') or ''), KB_TASK_TYPE=str(self.t.get('类型') or ''))
        serials = self.serials()
        if serials:
            env['ANDROID_SERIAL'] = serials[0]
            env['KB_DEVICES'] = ','.join(serials)
        return env

    def serials(self):
        out = []
        for h in self.t.get('已占资源') or []:
            if h.get('类型') == '手机':
                rid = str(h.get('资源') or '')
                out.append(h.get('序列号') or rid.split(':')[-1])
        return out

    def mock_device(self):
        return bool(os.environ.get('KB_RUNNER_FAKE_PHONES')) or any(
            'mock' in str(h.get('提供方') or h.get('资源') or '') for h in self.t.get('已占资源') or [] if h.get('类型') == '手机')

    def kill(self):
        self.cancel.set()
        p = self.proc
        if p and p.poll() is None:
            try:
                os.killpg(p.pid, signal.SIGTERM)
            except OSError:
                pass

    # ---- 主体
    def run(self):
        self.a.add(self)
        res = {'task': self.id, '结果': '失败', '退出码': -1}
        acked = False
        try:
            try:
                copy = self._prepare()
            except Exception as e:                      # 拉不到副本：先确认再报失败，控制面按失败处理 / 重试
                copy = {'仓库': self.t.get('仓库', ''), '路径': '', '占用会话': self.id}
                if self._ack(copy):
                    acked = True
                    res.update(失败原因=f'执行机准备工作副本失败：{e}', 外部原因=bool(EXTERNAL.search(str(e))))
                return
            if not self._ack(copy):
                return
            acked = True
            res = self._execute()
        except Exception as e:                          # noqa: BLE001 执行机自己的错也要回报
            res.update(结果='失败', 失败原因=f'执行机出错：{e!r}')
        finally:
            try:
                if acked:
                    if self.home:
                        blob = self.home.changed_blob()
                        acc = (self.t.get('引擎账号') or {}).get('id')
                        if blob and acc:
                            res['登录态回写'] = {'账号': acc, '登录态': blob, '文件': self.home.changed_login()}
                    self._flush()
                    self._done(res)
            finally:
                self._cleanup()
                self.a.remove(self)

    def _execute(self):
        from kblib.infra import pool
        self.home = SessionHome(self.a.data, self.id).create()
        self.home.write_login((self.t.get('引擎账号') or {}).get('登录态'), str(self.t.get('引擎') or ''))
        self.home.write_git(self.t.get('凭据'))
        head0 = _git(self.path, 'rev-parse', 'HEAD').stdout.strip() if (Path(self.path) / '.git').exists() else ''
        self.logf.parent.mkdir(parents=True, exist_ok=True)
        cmd = self._command()
        self.state = '运行'
        with open(self.logf, 'ab') as lf, (pool.pool_lease(self.path, self.id) if pool.copy_of(self.path) else _nullctx()):
            if self.cancel.is_set():
                return {'task': self.id, '结果': '取消', '退出码': -15}
            try:
                self.proc = subprocess.Popen(cmd, cwd=self.path, env=self._env(), stdout=lf, stderr=subprocess.STDOUT,
                                             stdin=subprocess.DEVNULL, start_new_session=True)
            except OSError as e:
                lf.write(f'起不来：{e}\n'.encode())
                return {'task': self.id, '结果': '失败', '退出码': 127, '失败原因': f'命令起不来：{cmd[0]}（{e}）', '外部原因': False}
            killed_at = 0.0
            while self.proc.poll() is None:
                time.sleep(EVENT_SEC)
                if self._event():
                    self.kill()
                if self.cancel.is_set() and not killed_at:
                    killed_at = time.time()
                if killed_at and time.time() - killed_at > 10 and self.proc.poll() is None:
                    try:
                        os.killpg(self.proc.pid, signal.SIGKILL)
                    except OSError:
                        pass
            code = self.proc.returncode
        self._event()
        tail = self.tail(200)
        res = {'task': self.id, '退出码': code, '用量': {'时长秒': int(time.time() - self.start_ts)}}
        if self.cancel.is_set():
            res.update(结果='取消', 失败原因='已取消')
        elif code == 0:
            res['结果'] = '完成'
        else:
            last = next((x for x in reversed(tail.splitlines()) if x.strip()), '')
            res.update(结果='失败', 失败原因=f'退出码 {code}：{last[-300:]}', 外部原因=bool(EXTERNAL.search(tail[-4000:])), 日志尾=tail)
        if head0:
            res['提交'] = [x for x in _git(self.path, 'rev-list', '--reverse', f'{head0}..HEAD').stdout.split() if x]
            res['副本有未推送提交'] = pool._unpushed(self.path) > 0
        if res['结果'] == '完成' and self.t.get('类型') in ('构建安装', '运行到手机'):
            err = self._devices_step()
            if err:
                res.update(结果='失败', 失败原因=err, 外部原因=False, 日志尾=self.tail(200))
        elif res['结果'] == '失败' and self.serials():
            self._evidence('失败')                       # 失败那一步的截图（REQ-050）
        return res

    def tail(self, n):
        try:
            return '\n'.join(self.logf.read_text('utf-8', 'replace').splitlines()[-n:])
        except OSError:
            return ''

    # ---- 产物、安装、证据（REQ-049 REQ-050）
    def _say(self, s):
        with open(self.logf, 'a', encoding='utf-8') as f:
            f.write(s.rstrip() + '\n')

    def _upload_artifacts(self):
        pat = self.t.get('产物') or self.t.get('产物路径') or ''
        if not pat:
            return []
        files = [f for f in glob.glob(str(Path(self.path) / pat), recursive=True) if Path(f).is_file()]
        ids = []
        for f in files:
            meta = {'task': self.id, '版本': self.t.get('版本', ''), '分支': self.t.get('分支', ''),
                    '提交': _git(self.path, 'rev-parse', 'HEAD').stdout.strip(), 'sha256': sha256(f)}
            for i in range(3):
                try:
                    r = self.a.client.post_multipart('/api/runner/artifact', {'meta': json.dumps(meta, ensure_ascii=False)}, f)
                    ids.append((r.get('id'), f))
                    self._say(f'已登记产物 {r.get("id")}：{Path(f).name}（sha256 {meta["sha256"][:12]}…）')
                    break
                except (NetError, ApiError) as e:
                    self._say(f'上传产物失败（第 {i + 1} 次）：{e}')
                    time.sleep(2)
        return ids

    def _fetch_artifact(self, aid, want_sha=''):
        """编译机和手机不在同一台：从控制面拉产物并校验 SHA256，失败重试 2 次。"""
        dst = Path(self.a.data) / 'sessions' / self.id / f'artifact-{aid}.apk'
        for i in range(3):
            try:
                self.a.client.download(f'/api/artifact/download?id={aid}', dst)
                if not want_sha or sha256(dst) == want_sha:
                    return str(dst)
                self._say(f'产物 {aid} 校验 SHA256 不一致（第 {i + 1} 次）')
            except (NetError, ApiError) as e:
                self._say(f'拉取产物 {aid} 失败（第 {i + 1} 次）：{e}')
            time.sleep(2)
        return ''

    def _adb(self, serial, *a, timeout=300, out=None):
        adb = sysinfo.adb_path() or 'adb'
        return subprocess.run([adb, '-s', serial, *a], capture_output=out is None, stdout=out, timeout=timeout)

    def _devices_step(self):
        """构建产物上传 → 装到会话占用的手机 → 留证据。→ 错误说明（成功返回 ''）。"""
        apks = [f for _, f in self._upload_artifacts()]
        if not apks and self.t.get('产物id'):
            f = self._fetch_artifact(self.t['产物id'], self.t.get('sha256', ''))
            if not f:
                return f'拉取产物 {self.t["产物id"]} 失败（已重试 2 次）'
            apks = [f]
        serials = self.serials()
        if not serials:
            return ''
        for sn in serials:
            for apk in [a for a in apks if a.endswith('.apk')][:1]:
                if self.mock_device():
                    self._say(f'【模拟数据】安装 {Path(apk).name} 到 {sn}')
                    continue
                r = self._adb(sn, 'install', '-r', '-t', apk)
                self._say(f'安装到 {sn}：{(r.stdout or b"").decode(errors="replace")[-300:]}')
                if r.returncode:
                    self._evidence('安装失败')
                    return f'安装到 {sn} 失败：{(r.stderr or r.stdout or b"").decode(errors="replace")[-300:]}'
        self._evidence('运行结束')
        return ''

    def _evidence(self, step):
        mock = self.mock_device()
        d = Path(self.a.data) / 'sessions' / self.id / 'evidence'
        d.mkdir(parents=True, exist_ok=True)
        for sn in self.serials():
            items = []
            try:
                if mock:
                    (d / f'{sn}.png').write_bytes(MOCK_PNG)
                    (d / f'{sn}.logcat.txt').write_text('【模拟数据】logcat\n', 'utf-8')
                    (d / f'{sn}.info.json').write_text(json.dumps({'序列号': sn, '模拟': True}, ensure_ascii=False), 'utf-8')
                else:
                    with open(d / f'{sn}.png', 'wb') as f:
                        self._adb(sn, 'exec-out', 'screencap', '-p', timeout=60, out=f)
                    with open(d / f'{sn}.logcat.txt', 'wb') as f:
                        self._adb(sn, 'logcat', '-d', timeout=120, out=f)
                    if (d / f'{sn}.logcat.txt').stat().st_size > EVIDENCE_MAX:        # 单次上限 20MB：留最后的
                        b = (d / f'{sn}.logcat.txt').read_bytes()[-EVIDENCE_MAX:]
                        (d / f'{sn}.logcat.txt').write_bytes(b)
                    props = self._adb(sn, 'shell', 'getprop', timeout=60).stdout.decode(errors='replace')
                    info = {k: v for k, v in re.findall(r'\[(ro\.(?:product\.model|product\.brand|build\.version\.release|build\.version\.sdk))\]: \[(.*?)\]', props)}
                    (d / f'{sn}.info.json').write_text(json.dumps({'序列号': sn, **info}, ensure_ascii=False), 'utf-8')
                items = [('截图', d / f'{sn}.png'), ('logcat', d / f'{sn}.logcat.txt'), ('设备信息', d / f'{sn}.info.json')]
            except (OSError, subprocess.TimeoutExpired) as e:
                self._say(f'取证据失败（{sn}）：{e}')
            for kind, f in items:
                if not f.exists() or f.stat().st_size == 0:
                    continue
                try:
                    self.a.client.post_multipart('/api/runner/evidence', {'task': self.id, '类型': kind, '步骤': f'{step}（{sn}）',
                                                                           '模拟': 'true' if mock else 'false'}, f)
                except (NetError, ApiError) as e:
                    self._say(f'上传证据失败：{e}')

    def _cleanup(self):
        if self.home:
            self.home.destroy()                         # 凭据、登录态随会话删除（REQ-017 REQ-039）
        else:
            import shutil
            shutil.rmtree(Path(self.a.data) / 'sessions' / self.id, ignore_errors=True)
        if self.path:
            try:
                from kblib.infra import pool
                pool.pool_release(self.path, self.id)
            except Exception:
                pass


class _nullctx:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


MOCK_PNG = bytes.fromhex('89504e470d0a1a0a0000000d4948445200000001000000010806000000'
                         '1f15c4890000000d49444154789c6360000002000001e221bc330000000049454e44ae426082')


def sha256(f):
    h = hashlib.sha256()
    with open(f, 'rb') as fh:
        for b in iter(lambda: fh.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


# ---------------------------------------------------------------- 执行机
class Agent:
    def __init__(self, cfg, data, capacity=0):
        self.cfg, self.data = cfg, str(data)
        self.server = cfg['server'].rstrip('/')
        self.stats = NetStats()
        self.client = Client(self.server, cfg['token'], cfg.get('ca', ''), self.stats)
        self.capacity = capacity or int(cfg.get('容量') or 8)   # 本机上限；真正的容量由控制面派活时控制（网页可改）
        self.fixed_cap = bool(capacity)
        self.sessions = {}
        self.reported = {}
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.blocked = ''                 # 协议版本不一致：不领活（REQ-029）
        self.unauthorized = False         # 执行机令牌失效（停用 / 换绑）
        self.pending = {'清理': [], '审计': []}
        self.tags, self.checks = [], []
        self._tags_at = 0.0
        self._copies, self._copies_at = [], 0.0
        self._cleaning = False
        self.online = False

    def add(self, s):
        with self.lock:
            self.sessions[s.id] = s

    def remove(self, s):
        with self.lock:
            self.sessions.pop(s.id, None)
            self.reported.pop(s.id, None)

    def free(self):
        with self.lock:
            return max(0, self.capacity - len(self.sessions))

    # ---- 心跳
    def _copies_report(self):
        if time.time() - self._copies_at < 60 and self._copies:
            busy = {s.path: s.id for s in list(self.sessions.values())}
            return [dict(c, 占用会话=busy.get(c['路径'])) for c in self._copies]
        out = []
        try:
            from kblib.infra import pool
            from kblib.runner import disk
            busy = {str(Path(s.path).resolve()): s.id for s in list(self.sessions.values()) if s.path}
            for name in pool.pool_repos():
                for p in pool.copies(name):
                    c = copy_info(p, busy.get(str(p.resolve()), ''))
                    c['可能有改动'] = disk.has_changes(p)
                    out.append(c)
        except Exception as e:      # noqa: BLE001
            log(f'读副本失败：{e}')
        self._copies, self._copies_at = out, time.time()
        return out

    def hb_body(self):
        if time.time() - self._tags_at > 60 or not self.tags:
            self.tags, self.checks = sysinfo.tags_and_checks(self.stats)
            self._tags_at = time.time()
        held = {}
        for s in list(self.sessions.values()):
            for sn in s.serials():
                held[sn] = s.id
        phones = [dict(p, 占用会话=held.get(p['序列号'])) for p in sysinfo.phones()]
        dk = sysinfo.disk(self.data)
        body = {'自动标签': self.tags, '空闲容量': self.free(), '副本': self._copies_report(), '手机': phones,
                '磁盘': {'可用GB': dk['可用GB'], '总GB': dk['总GB']}, 'CPU': sysinfo.cpu(), '内存': sysinfo.mem(),
                '网络': self.stats.report(),
                '会话': [{'task': s.id, '状态': s.state} for s in list(self.sessions.values())],
                '自检': self.checks, '版本': version(), '协议版本': PROTO}
        for k in ('清理', '审计'):
            if self.pending[k]:
                body[k] = list(self.pending[k])
        return body, dk

    def heartbeat_once(self):
        body, dk = self.hb_body()
        r = self.client.post('/api/runner/heartbeat', body)
        for k in ('清理', '审计'):
            self.pending[k] = self.pending[k][len(body.get(k) or []):]
        for tid in r.get('cancel') or []:
            s = self.sessions.get(str(tid))
            if s:
                log(f'控制面要求停止任务 {tid}')
                s.kill()
        if r.get('容量') and not self.fixed_cap:
            self.capacity = int(r['容量'])
        if r.get('新端口'):
            self.follow_port(int(r['新端口']), int(r.get('旧端口') or 0))
        self.unauthorized = False
        if not self.online:
            log('已连上控制面')
        self.online = True
        if dk['使用比'] >= 0.80 and not self._cleaning:
            threading.Thread(target=self._clean, daemon=True).start()
        return r

    def follow_port(self, new, old):
        """控制面在网页里改了端口：本机直连的是旧端口就改连新端口并存盘（经反向代理、端口不同的不动）。"""
        from urllib.parse import urlparse
        u = urlparse(self.client.server)
        if not u.port or u.port != old or new == u.port:
            return
        new_url = self.client.server.replace(f'{u.hostname}:{u.port}', f'{u.hostname}:{new}', 1)
        tok = self.client.token
        self.cfg.setdefault('tokens', {})[new_url] = tok
        self.cfg.setdefault('搬到', {})[self.client.server] = new_url    # 之后再用旧地址启动（开机自启里写死的 --server）也跟到新端口
        self.cfg['server'] = new_url
        save_cfg(self.data, self.cfg)
        self.client.server = self.server = new_url
        log(f'控制面端口改成 {new}：改连 {new_url}')

    def _clean(self):
        self._cleaning = True
        try:
            from kblib.runner import disk
            busy = [s.path for s in list(self.sessions.values()) if s.path]
            res = disk.cleanup(self.data, busy)
            self.pending['清理'] += res['清理']
            if res['清理']:
                log('磁盘水位清理：' + '、'.join(x['副本'] for x in res['清理']))
        except Exception as e:      # noqa: BLE001
            log(f'磁盘清理出错：{e}')
        finally:
            self._cleaning = False

    def heartbeat_loop(self):
        fails = 0
        while not self.stop.is_set():
            try:
                self.heartbeat_once()
                fails = 0
                wait = HB_SEC
            except ApiError as e:
                if e.status == 401:
                    if not self.unauthorized:
                        log('执行机令牌已失效（被停用或换绑）：到网页重新生成注册令牌，kb runner --token <新令牌> --rebind')
                    self.unauthorized = True
                    wait = HB_SEC
                else:
                    fails += 1
                    wait = min(MAX_BACKOFF, 2 ** fails)
            except NetError:
                if self.online:
                    log('连不上控制面，重连中（在跑的会话不受影响）')
                self.online = False
                fails += 1
                wait = min(MAX_BACKOFF, 2 ** fails)
            except Exception as e:     # noqa: BLE001
                log(f'心跳出错：{e!r}')
                wait = HB_SEC
            self.stop.wait(wait)

    # ---- 领活
    def claim_once(self):
        r = self.client.post('/api/runner/claim', {'协议版本': PROTO, '空闲容量': self.free()}, timeout=CLAIM_WAIT + 20)
        t = r.get('task')
        if t:
            log(f'领到任务 {t.get("id")}（{t.get("类型", "")} · {t.get("期", "")}）')
            Session(self, t).start()
        return t

    def claim_loop(self):
        fails = 0
        while not self.stop.is_set():
            if self.unauthorized or self.free() <= 0:
                self.stop.wait(2)
                continue
            if self.blocked:
                if not self.sessions and os.environ.get('KB_RUNNER_AUTO_UPGRADE') == '1':
                    self_upgrade()
                self.stop.wait(60)
            try:
                self.claim_once()
                if self.blocked:
                    log('协议版本已一致，恢复领活')
                self.blocked = ''
                fails = 0
            except ApiError as e:
                if e.status == 409:
                    need = (e.body.get('need') or {})
                    want = need.get('需要协议版本') or need.get('协议版本') or need.get('version') or '?'
                    if not self.blocked:
                        log(f'控制面要求协议版本 {want}，本机是 {PROTO}：请升级（kb update），升级前不领活')
                    self.blocked = str(want)
                elif e.status == 401:
                    self.unauthorized = True
                else:
                    fails += 1
                    self.stop.wait(min(MAX_BACKOFF, 2 ** fails))
            except NetError:
                fails += 1
                self.stop.wait(min(MAX_BACKOFF, 2 ** fails))

    def run(self):
        gone = cleanup_leftovers(self.data, [])
        if gone:
            log(f'清理了上次残留的会话目录：{"、".join(gone)}')
            self.pending['审计'].append({'动作': '清理残留会话目录', '任务': gone, '时间': time.strftime('%Y-%m-%d %H:%M:%S')})
        prune_logs(self.data)
        threading.Thread(target=self.heartbeat_loop, daemon=True, name='heartbeat').start()
        log(f'执行机 {self.cfg.get("name")}（{self.cfg.get("id")}）连接 {self.server}，容量 {self.capacity}')
        try:
            self.claim_loop()
        except KeyboardInterrupt:
            self.stop.set()


def prune_logs(data):
    d = Path(data) / 'logs'
    if d.is_dir():
        for f in d.iterdir():
            if f.is_file() and time.time() - f.stat().st_mtime > LOG_KEEP_DAYS * 86400:
                f.unlink(missing_ok=True)


def self_upgrade():
    """协议版本不一致且开了自动升级：拉程序最新代码后重启自己（REQ-029）。"""
    from kblib.runner import SYS_DIR
    prog = SYS_DIR.parent
    r = subprocess.run(['git', '-C', str(prog), 'pull', '-q', '--ff-only'], capture_output=True, text=True, timeout=300)
    if r.returncode:
        log(f'自动升级失败：{(r.stderr or r.stdout).strip()[-200:]}')
        return
    log('已升级，重启执行机')
    os.execv(sys.executable, [sys.executable, '-m', 'kblib.runner.agent', *sys.argv[1:]])


def _ensure_root(data):
    """执行机进程必须用自己的 KB_ROOT（在 import kblib.core 前）；不对就重新 exec。"""
    want = str(Path(data) / 'root')
    if os.environ.get('KB_ROOT') != want:
        from kblib.runner import launch
        args = sys.argv[1:]
        if not any(x == '--data' or x.startswith('--data=') for x in args):
            args += ['--data', str(data)]
        launch(args)


def main(argv=None):
    ap = argparse.ArgumentParser(prog='kb runner', description='把这台机器登记成执行机，只出站连接控制面领活')
    ap.add_argument('--server', default=os.environ.get('KB_SERVER', ''), help='控制面地址，如 https://kb.example.com')
    ap.add_argument('--token', default=os.environ.get('KB_RUNNER_TOKEN', ''), help='注册令牌（kbreg_，网页「🖥 执行机」生成，24 小时内用一次）')
    ap.add_argument('--name', default=os.environ.get('KB_RUNNER_NAME', ''), help='执行机名称（或环境变量 KB_RUNNER_NAME；容器里用 KB_SERVER / KB_RUNNER_TOKEN 同理）')
    ap.add_argument('--data', default='', help='执行机数据目录，默认 ~/.kb-runner')
    ap.add_argument('--ca', default='', help='控制面是自签证书时，指定 CA 证书文件')
    ap.add_argument('--rebind', action='store_true', help='换到新控制面：保留执行机 ID、标签和副本池')
    ap.add_argument('--capacity', type=int, default=0, help='本机最多同时跑几个会话（默认按网页设置）')
    ap.add_argument('--register-only', action='store_true', help='只注册不常驻（安装脚本用）')
    a = ap.parse_args(argv)
    os.environ.pop('KB_DB_URL', None)          # 执行机绝不直连控制面的数据库
    data = data_dir(a.data)
    _ensure_root(data)
    data.mkdir(parents=True, exist_ok=True)
    try:
        from kblib.infra import pool
        pool._index_later = lambda name: None          # 执行机不建全文索引
    except Exception:
        pass
    cfg = load_cfg(data)
    server = (a.server or cfg.get('server') or '').rstrip('/')
    for _ in range(5):                               # 控制面在网页里改过端口：旧地址跟到新地址
        server = (cfg.get('搬到') or {}).get(server, server)
    if not server:
        print('要指定控制面地址：kb runner --server <地址> --token <注册令牌>', file=sys.stderr)
        return 2
    saved = (cfg.get('tokens') or {}).get(server) or (cfg.get('token') if (cfg.get('server') or '').rstrip('/') == server else '')
    if a.rebind or not saved:
        if not a.token and not a.rebind:
            print('第一次连这个控制面要注册令牌：kb runner --server <地址> --token <注册令牌>', file=sys.stderr)
            return 2
        cfg = register(server, a.token, a.name, data, a.ca, a.rebind)
    else:
        if a.token:
            log('本机已经注册过这个控制面，忽略 --token（要换绑用 --rebind）')
        if cfg.get('server') != server:              # 指回旧控制面：用当时在那边的令牌（REQ-034）
            cfg.update(server=server, token=saved)
            save_cfg(data, cfg)
    if a.register_only:
        return 0
    if a.ca:
        cfg['ca'] = a.ca
    Agent(cfg, data, a.capacity).run()
    return 0


if __name__ == '__main__':
    sys.exit(main() or 0)
