# From 大大王.DT
"""AI 引擎（Claude Code 等）的配置和调用。"""
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from kblib.core.common import PROG, KBError, PERIODS, ROOT, SYS, load_settings, read_text, save_settings
from kblib.core.periods import load_meta, log, period_dir, period_names, save_meta
from kblib.core.repos import load_repos
from kblib.ai.flows import BUILTIN_FLOWS, FLOW_FIELDS, FLOW_VARS
from kblib.ai import team
from kblib import lazy
_devsession = lazy('kblib.ai.devsession')      # 上层模块：用到时才 import（不形成循环）


# 后台会话用哪个编程 AI 跑：Claude Code / Codex CLI / Hermes Agent，或自定义命令。
# 开发会话按这一期选的引擎（不选用默认），模糊添加、来源更新、生成流程用默认引擎。
# 引擎要能无人值守运行、并接入知识库 MCP（kb_ask 等工具），否则没法在网页上确认。
# 内置预设只用各引擎的常规权限模式，不默认跳过审批或沙箱；要放宽由用户在设置里自己改参数。
ENGINE_VARS = {'提示词': '本次任务的完整提示词', '标题': '会话标题，如 kb·期名', '会话ID': '续跑时上一轮的会话号'}
ENGINE_FIELDS = [
    ('名称', '名称', 'text', '如：Codex CLI、自研 Agent'),
    ('说明', '说明', 'textarea', '给人看的'),
    ('命令', '可执行命令', 'text', '命令名或绝对路径，如 claude、codex、hermes'),
    ('启动参数', '启动参数（每行一个）', 'lines', '无人值守跑一次任务的参数；{提示词} 单独占一行。权限相关参数决定 AI 能在仓库里做什么，请按团队安全要求填写'),
    ('续跑参数', '续跑参数（每行一个，可空）', 'lines', '接着上一轮会话继续的参数，可用 {会话ID}；留空 = 每次新开会话，用续跑提示接着做'),
    ('输出格式', '输出格式', 'select:Claude stream-json|Codex JSONL|Hermes stream-json|纯文本',
     '用来在网页上显示动作、取最后结果和会话号；不确定选「纯文本」'),
    ('接入命令', '接入知识库的命令（可空）', 'text', '把知识库 MCP 服务注册给这个引擎；可用 {kb} 代表「python kb.py serve」的完整命令'),
    ('接入配置', '手动接入说明（可空）', 'textarea', '没有接入命令时，告诉用户把什么写进哪个配置文件；可用 {python}、{kbpy}'),
]
BUILTIN_ENGINES = {
    'claude': {
        '名称': 'Claude Code', '说明': 'Anthropic 的 Claude Code（claude -p 无人值守，auto 权限模式）',
        '命令': 'claude',
        '启动参数': ['-p', '{提示词}', '--permission-mode', 'auto', '--output-format', 'stream-json', '--verbose', '-n', '{标题}'],
        '续跑参数': ['-p', '{提示词}', '--permission-mode', 'auto', '--output-format', 'stream-json', '--verbose', '-n', '{标题}',
                    '--resume', '{会话ID}'],
        '输出格式': 'Claude stream-json', '接入命令': 'claude mcp add kb -s user -- {kb}', '接入配置': '',
    },
    'codex': {
        '名称': 'Codex CLI', '说明': 'OpenAI 的 Codex CLI（codex exec 无人值守，workspace-write 沙箱）。'
                                    '如果知识库工具调用被审批拦住，按团队安全要求调整启动参数',
        '命令': 'codex',
        '启动参数': ['exec', '--json', '--sandbox', 'workspace-write', '--skip-git-repo-check', '{提示词}'],
        '续跑参数': ['exec', 'resume', '{会话ID}', '--json', '--sandbox', 'workspace-write', '--skip-git-repo-check', '{提示词}'],
        '输出格式': 'Codex JSONL', '接入命令': 'codex mcp add kb -- {kb}', '接入配置': '',
    },
    'hermes': {
        '名称': 'Hermes Agent', '说明': 'Nous Research 的 Hermes Agent（hermes chat --oneshot 无人值守）。'
                                       '危险命令默认要审批，无人值守时会被拒绝；需要放宽时按团队安全要求调整启动参数',
        '命令': 'hermes',
        '启动参数': ['chat', '--oneshot', '--format', 'stream-json', '-q', '{提示词}'],
        '续跑参数': ['chat', '--resume', '{会话ID}', '--oneshot', '--format', 'stream-json', '-q', '{提示词}'],
        '输出格式': 'Hermes stream-json', '接入命令': '',
        '接入配置': '在 ~/.hermes/config.yaml 里加上（已有 mcp_servers: 就只加 kb 这一段），然后重启 hermes：\n'
                    'mcp_servers:\n  kb:\n    command: "{python}"\n    args: ["{kbpy}", "serve"]\n    timeout: 900',
    },
}
DEFAULT_ENGINE = 'claude'


def _kb_cmd():
    py = PROG / '.venv' / 'bin' / 'python'          # 程序目录（KB_ROOT 指向别处的数据目录时也对）
    return str(py if py.exists() else sys.executable), str(PROG / 'kb.py')


def engines():
    s = load_settings()
    over = s.get('引擎') or {}
    dft = s.get('默认引擎') or DEFAULT_ENGINE
    out = [dict(base, **(over.get(eid) or {}), id=eid, 内置=True, 已修改=eid in over) for eid, base in BUILTIN_ENGINES.items()]
    out += [dict(BUILTIN_ENGINES['claude'], **e, id=eid, 内置=False, 已修改=False) for eid, e in over.items()
            if eid not in BUILTIN_ENGINES]
    for e in out:
        e['默认'] = e['id'] == dft
    return out


def get_engine(eid=''):
    eid = eid or load_settings().get('默认引擎') or DEFAULT_ENGINE
    e = next((x for x in engines() if x['id'] == eid), None)
    if not e:
        raise KBError(f'没有这个 AI 引擎：{eid}')
    return e


def engine_bin(e):
    c = os.path.expanduser(e.get('命令') or '')
    found = shutil.which(c) if c and '/' not in c else (c if c and os.access(c, os.X_OK) else '')
    if not found and c and '/' not in c:
        found = next((x for x in (f'/opt/homebrew/bin/{c}', f'/usr/local/bin/{c}', str(Path.home() / '.local/bin' / c),
                                  str(Path.home() / '.npm-global/bin' / c)) if Path(x).exists()), '')
    if not found:
        raise KBError(f'找不到 {e["名称"]} 的命令「{e.get("命令")}」：请先安装，或在「⚙ 设置 · AI 引擎」里填可执行文件的绝对路径')
    return found


def rules_text():
    """管家规矩（你在「问管家」里说「以后……」记下的）：附在每个后台 AI 会话的提示词后面。"""
    rs = [r.get('内容') for r in load_settings().get('管家规矩') or [] if isinstance(r, dict) and r.get('内容')]
    return ('\n\n用户定的长期规矩（必须遵守）：\n' + '\n'.join(f'- {x}' for x in rs)) if rs else ''


def engine_cmd(e, prompt, title='kb', resume_id='', scene='', period=None, auto=False, end=''):
    """拼出运行命令（参数逐个替换变量，不经过 shell）。
    scene：角色团场景（管家 / 方案 / Bug分析 / 开发·Android …）；end：会话所在端（@端 换算，也可以写在 scene 的 · 后面）；period：期信息或期名（期级覆盖、本期剩余次数）；auto：管家自动发起。
    scene 为空、续跑（上下文里已有）、不挂角色团时，命令行和原来逐字一致。"""
    prompt = prompt + rules_text()
    if scene and not resume_id:
        try:
            prompt += team.team_text(scene, period, end, eng=e, auto=auto)
        except Exception as x:          # noqa: BLE001 — 场景表写坏了也不能让会话起不来：不挂角色团
            print(f'角色团：场景「{scene}」生成提示词出错，不挂角色团：{x!r}', file=sys.stderr, flush=True)
    args = e.get('续跑参数') if resume_id and e.get('续跑参数') else e.get('启动参数')
    v = {'提示词': prompt, '标题': title, '会话ID': resume_id}
    return [engine_bin(e)] + [re.sub(r'\{(提示词|标题|会话ID)\}', lambda mm: v[mm.group(1)], a) for a in args or []]


def engine_status(e):
    """装没装、版本、有没有接入知识库 MCP（读各自的配置文件判断，判断不了为 None）。"""
    try:
        b = engine_bin(e)
    except KBError:
        return {'已安装': False, '版本': '', '已接入': None}
    try:
        r = subprocess.run([b, '--version'], capture_output=True, text=True, timeout=15)
        out = (r.stdout or r.stderr).strip()
        ver = out.splitlines()[0][:80] if out else ''
    except (OSError, subprocess.TimeoutExpired):
        ver = ''
    name = Path(b).name
    linked = None
    try:
        if name == 'claude':
            linked = 'kb' in (json.loads(read_text(Path.home() / '.claude.json')).get('mcpServers') or {})
        elif name == 'codex':
            linked = bool(re.search(r'^\[mcp_servers\.kb\]', read_text(Path.home() / '.codex' / 'config.toml'), re.M))
        elif name == 'hermes':
            t = read_text(Path.home() / '.hermes' / 'config.yaml')
            linked = bool(re.search(r'^mcp_servers:\s*\n(?:[ \t].*\n)*?[ \t]+kb:', t + '\n', re.M))
    except (OSError, ValueError):
        linked = False if name in ('claude', 'codex', 'hermes') else None
    return {'已安装': True, '版本': ver, '已接入': linked}


def engine_link(eid):
    """网页「接入知识库」：运行引擎的接入命令，把知识库 MCP 注册给它。"""
    import shlex
    e = get_engine(eid)
    if not e.get('接入命令'):
        raise KBError(f'{e["名称"]} 没有接入命令，请按说明手动接入')
    py, kbpy = _kb_cmd()
    argv = shlex.split(e['接入命令'].replace('{kb}', f'"{py}" "{kbpy}" serve'))
    if argv and argv[0] == e.get('命令'):
        argv[0] = engine_bin(e)
    r = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    if r.returncode and 'already' not in (r.stderr + r.stdout).lower():
        raise KBError(f'接入失败：{(r.stderr or r.stdout).strip()[:500]}')
    return f'已把知识库接入 {e["名称"]}' + (f'：{r.stdout.strip()[:200]}' if r.stdout.strip() else '')


def engine_link_help(e):
    py, kbpy = _kb_cmd()
    return (e.get('接入配置') or '').replace('{python}', py).replace('{kbpy}', kbpy)


def save_engine(data):
    s = load_settings()
    over = s.setdefault('引擎', {})
    e = {k: data[k] for k, *_ in ENGINE_FIELDS if k in data}
    for k in ('启动参数', '续跑参数'):
        if isinstance(e.get(k), str):
            e[k] = [x.strip() for x in e[k].splitlines() if x.strip()]
    if e.get('输出格式') and e['输出格式'] not in ENGINE_FIELDS[5][2][7:].split('|'):
        raise KBError('输出格式不对')
    if not (e.get('名称') or '').strip() or not (e.get('命令') or '').strip():
        raise KBError('名称和命令不能为空')
    if '{提示词}' not in ' '.join(e.get('启动参数') or []):
        raise KBError('启动参数里要有 {提示词}')
    eid = (data.get('id') or '').strip()
    if not eid:
        eid = 'engine-' + uuid.uuid4().hex[:6]
    elif eid not in BUILTIN_ENGINES and eid not in over:
        raise KBError(f'没有这个 AI 引擎：{eid}')
    if any(x['名称'] == e['名称'] and x['id'] != eid for x in engines()):
        raise KBError(f'已经有叫「{e["名称"]}」的引擎了')
    over[eid] = e
    save_settings(s)
    return f'已保存 AI 引擎「{e["名称"]}」', eid


def delete_engine(eid):
    s = load_settings()
    over = s.setdefault('引擎', {})
    if eid not in over:
        raise KBError('内置引擎没有修改过，不用恢复' if eid in BUILTIN_ENGINES else f'没有这个 AI 引擎：{eid}')
    name = get_engine(eid)['名称']
    over.pop(eid)
    if eid not in BUILTIN_ENGINES:
        if s.get('默认引擎') == eid:
            s['默认引擎'] = DEFAULT_ENGINE
        for n in period_names():
            d = PERIODS / n
            m = load_meta(d)
            if m.get('AI引擎') == eid:
                m.pop('AI引擎')
                save_meta(d, m)
    save_settings(s)
    return f'已恢复「{name}」的默认设置' if eid in BUILTIN_ENGINES else f'已删除 AI 引擎「{name}」'


def set_default_engine(eid):
    e = get_engine(eid)
    s = load_settings()
    s['默认引擎'] = e['id']
    save_settings(s)
    return f'默认 AI 引擎改为「{e["名称"]}」'


def set_period_engine(period, eid):
    d = period_dir(period)
    e = get_engine(eid) if eid else None
    m = load_meta(d)
    if (m.get('AI引擎') or '') == (eid or ''):
        return '没有变化'
    if e:
        m['AI引擎'] = e['id']
    else:
        m.pop('AI引擎', None)
    save_meta(d, m)
    log(d, 'AI 引擎', e['名称'] if e else f'默认（{get_engine()["名称"]}）')
    return f'「{d.name}」的 AI 引擎改为「{e["名称"] if e else "默认"}」'


def period_engine(m):
    """开发会话用的引擎：会话里记的 > 这一期选的 > 「开发」角色指定的 > 默认。"""
    for eid in ((m.get('开发会话') or {}).get('引擎'), m.get('AI引擎'), (load_settings().get('角色引擎') or {}).get('开发')):
        if eid:
            try:
                return get_engine(eid)
            except KBError:
                pass
    return get_engine()


# ---------------------------------------------------------------- 按角色分配 agent：人只下发任务和拍板，每类工作交给指定的 AI
ROLES = [('方案', '全栈出方案：需求规格、设计对照、影响分析、接口契约'),
         ('开发', '期的开发会话、各端开发（可以按端单独指定）'),
         ('审查', '各端代码审查。建议和开发用不同的引擎，审查才独立'),
         ('Bug分析', 'Bug 只读分析'),
         ('Bug修复', 'Bug 修复'),
         ('整理', '模糊添加、来源更新后的资料整理'),
         ('管家', '回答你在网页上「问管家」的问题，帮你查资料、改配置')]
ROLE_ENDS = ('服务端', 'Web', 'iOS', 'Android', '鸿蒙')


def role_engine(role, m=None, end=''):
    """这类工作用哪个引擎。
    开发：开发·<端> > 这一期选的 > 开发 > 默认；方案：方案 > 这一期选的 > 默认；审查：审查·<端> > 审查 > 默认（不跟这一期走，保持独立）；其他：角色 > 默认。"""
    r = load_settings().get('角色引擎') or {}
    chain = []
    if end:
        chain.append(r.get(f'{role}·{end}'))
    if role == '开发':
        chain += [(m or {}).get('AI引擎'), r.get('开发')]
    elif role == '方案':
        chain += [r.get('方案'), (m or {}).get('AI引擎')]
    else:
        chain.append(r.get(role))
    for eid in chain:
        if eid:
            try:
                return get_engine(eid)
            except KBError:
                pass
    return get_engine()


def role_engines():
    r = load_settings().get('角色引擎') or {}
    return {'角色': [{'角色': k, '说明': v, '引擎': r.get(k, '')} for k, v in ROLES],
            '按端': {f'{k}·{e}': r.get(f'{k}·{e}', '') for k in ('开发', '审查') for e in ROLE_ENDS}}


def set_role_engine(role, eid):
    ok = {k for k, _ in ROLES} | {f'{k}·{e}' for k in ('开发', '审查') for e in ROLE_ENDS}
    if role not in ok:
        raise KBError(f'没有这个角色：{role}')
    if eid:
        get_engine(eid)
    s = load_settings()
    r = s.setdefault('角色引擎', {})
    if eid:
        r[role] = eid
    else:
        r.pop(role, None)
    save_settings(s)
    return f'「{role}」改用 {get_engine(eid)["名称"] if eid else "默认（跟随上一级）"}'


_CLEAN_ENV = ('CLAUDECODE', 'CLAUDE_CODE_ENTRYPOINT', 'CLAUDE_CODE_SSE_PORT')


def engine_popen(e, prompt, title, logf, cwd, resume_id='', env_extra=None, extra_args=None, scene='', period=None, auto=False, end=''):
    """后台启动引擎，输出写进日志文件（第一行记下用的引擎和输出格式，解析日志时用）。"""
    cmd = engine_cmd(e, prompt, title, resume_id, scene, period, auto, end) + list(extra_args or [])
    env = {k: v for k, v in os.environ.items() if k not in _CLEAN_ENV}
    env.update(env_extra or {})
    with open(logf, 'ab') as fh:
        fh.write((json.dumps({'type': 'kb.engine', 'engine': e['id'], 'format': e.get('输出格式')}, ensure_ascii=False)
                  + '\n').encode())
        fh.flush()
        proc = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT,
                                start_new_session=True, env=env)
    _watch_health(e, proc, logf)
    return proc


def engine_run(e, prompt, cwd, timeout=300, extra_args=None, title='kb·单次', scene='', period=None, env_extra=None, auto=False, end=''):
    """前台跑一次，返回 (退出码, 解析出的结果)；结果里带「角色」（这次派过的角色团专家，没派不带）。"""
    env = {k: v for k, v in os.environ.items() if k not in _CLEAN_ENV}
    env.update(env_extra or {})
    try:
        r = subprocess.run(engine_cmd(e, prompt, title, '', scene, period, auto, end) + list(extra_args or []), cwd=cwd, capture_output=True, text=True, errors='replace', timeout=timeout,
                           env=env, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise KBError(f'{e["名称"]} {timeout // 60} 分钟没有返回')
    res = _devsession._result_of(r.stdout, e.get('输出格式'), limit=200000)
    if not res.get('结果'):
        res['结果'] = (r.stdout.strip() or r.stderr.strip())[-2000:]
    if scene:
        got = team.team_count_text(r.stdout)
        if got:
            res['角色'] = got
    health_from_run(e, r.returncode, res, (r.stdout or '') + '\n' + (r.stderr or ''), '会话')
    return r.returncode, res


# ---------------------------------------------------------------- 引擎能不能用（网页常驻显示）
# 不花额外费用的来源：每个后台会话结束时看结果（engine_popen / engine_run 都会记）；
# 没有真实会话时，网页每 6 小时对在用的引擎跑一次极短的自检（只回复 OK，不调工具）。
HEALTH_FILE = SYS / 'cache' / '引擎状态.json'
PROBE_HOURS = 6
_HEALTH_LOCK = threading.Lock()
_BAD = [(r'invalid api key|invalid x-api-key|api key|apikey|authentication_error|unauthori[sz]ed|\b401\b|not logged in|please (run )?/?login|login required|oauth token|token (has )?expired|credentials',
         '没登录或登录失效 / API key 不对'),
        (r'credit balance|insufficient[_ ]quota|quota|billing|payment|usage limit|limit reached|out of credits|额度', '额度用完或账单问题'),
        (r'rate[_ ]limit|\b429\b|too many requests', '被限流（请求太多）'),
        (r'overloaded|\b529\b|\b503\b|service unavailable', '服务端过载 / 暂时不可用'),
        (r'enotfound|econnrefused|econnreset|etimedout|getaddrinfo|network (is )?unreachable|could not resolve|connection (error|refused|reset)|fetch failed|ssl',
         '网络连不上'),
        (r'forbidden|\b403\b|permission denied for model|model .* not (found|available)|not_found_error', '没有权限 / 模型不可用'),
        (r'command not found|no such file or directory|找不到 .* 的命令', '命令找不到（没安装或路径不对）')]


def _health_load():
    try:
        return json.loads(HEALTH_FILE.read_text('utf-8'))
    except (OSError, ValueError):
        return {}


def _health_set(eid, ok, why='', src='会话', name=''):
    """记一次结果；从可用 / 未检测 变成不可用时发一次通知。"""
    with _HEALTH_LOCK:
        h = _health_load()
        old = h.get(eid) or {}
        st = '可用' if ok else '不可用'
        x = {'状态': st, '原因': '' if ok else (why or '')[:300], '时间': time.strftime('%Y-%m-%d %H:%M:%S'), '来源': src,
             'ts': time.time(), '名称': name or old.get('名称', eid)}
        if ok:
            x['上次失败'] = old.get('原因') if old.get('状态') == '不可用' else old.get('上次失败', '')
        h[eid] = x
        HEALTH_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = HEALTH_FILE.with_suffix('.tmp')
        tmp.write_text(json.dumps(h, ensure_ascii=False, indent=1), 'utf-8')
        tmp.replace(HEALTH_FILE)
    if not ok and old.get('状态') != '不可用':
        try:
            _devsession._notify('知识库 · AI 引擎不可用', f'{x["名称"]}：{x["原因"]}（网页右上角「AI」看详情）')
        except Exception:
            pass
    elif ok and old.get('状态') == '不可用':
        try:
            _devsession._notify('知识库 · AI 引擎恢复', f'{x["名称"]} 又能用了')
        except Exception:
            pass


def _classify(code, res, text):
    """→ True 可用 / (False, 原因) 不可用 / None 看不出（任务本身没做完之类，不算引擎问题）。"""
    if code in (-15, -9, 143, 137, -2, 130):           # 被停掉的（网页「停止」、重启）
        return None
    out = ((res or {}).get('结果') or '')
    plain = [ln for ln in (text or '').splitlines()[-200:] if ln.strip() and not ln.lstrip().startswith(('{', '['))]
    blob = (out if len(out) <= 600 else '') + '\n' + '\n'.join(plain)[-3000:]   # 只看简短的最终结果和非 JSON 的报错行，任务内容不算
    failed = code not in (0, None) or (res or {}).get('出错')
    if failed:
        low = blob.lower()
        for pat, why in _BAD:
            if re.search(pat, low):
                line = next((ln.strip() for ln in blob.splitlines() if re.search(pat, ln.lower())), '')
                return False, why + (f'：{line[:200]}' if line else '')
        if code not in (0, None) and not (res or {}).get('轮数') and len([ln for ln in (text or '').splitlines() if ln.strip()]) <= 3:
            return False, f'启动就退出了（退出码 {code}）：' + ((text or '').strip()[-200:] or '没有输出')
        return None
    return True


def health_from_run(e, code, res, text='', src='会话'):
    try:
        r = _classify(code, res, text)
        if r is True:
            _health_set(e['id'], True, src=src, name=e.get('名称'))
        elif r:
            _health_set(e['id'], False, r[1], src=src, name=e.get('名称'))
    except Exception as x:              # 记状态出错不能影响会话本身
        print('engine health:', repr(x), flush=True)


def _watch_health(e, proc, logf):
    def run():
        code = proc.wait()
        try:
            with open(logf, 'rb') as fh:
                fh.seek(max(0, Path(logf).stat().st_size - 400000))
                tail = fh.read().decode('utf-8', 'ignore')
        except OSError:
            tail = ''
        health_from_run(e, code, _devsession._result_of(tail, e.get('输出格式')), tail)
    threading.Thread(target=run, daemon=True).start()


def engines_in_use():
    """在用的引擎：默认引擎 + 角色里指定的 + 各期选的。"""
    s = load_settings()
    ids = [s.get('默认引擎') or DEFAULT_ENGINE] + [v for v in (s.get('角色引擎') or {}).values() if v]
    for n in period_names():
        eid = load_meta(PERIODS / n).get('AI引擎')
        if eid:
            ids.append(eid)
    known = {e['id'] for e in engines()}
    return [x for i, x in enumerate(ids) if x in known and x not in ids[:i]]


def engine_probe(eid, src='自检'):
    """极短的自检：只回复 OK，不调工具。→ 这次的状态。"""
    e = get_engine(eid)
    t0 = time.time()
    try:
        engine_bin(e)
        env = {k: v for k, v in os.environ.items() if k not in _CLEAN_ENV}
        r = subprocess.run(engine_cmd(e, '这是连通性自检。不要调用任何工具，只回复两个字母：OK', 'kb·自检'), cwd=str(ROOT),
                           capture_output=True, text=True, errors='replace', timeout=120, env=env, stdin=subprocess.DEVNULL)
        res = _devsession._result_of(r.stdout, e.get('输出格式'))
        txt = (r.stdout or '') + '\n' + (r.stderr or '')
        c = _classify(r.returncode, res, txt)
        if c is None:
            c = (False, f'自检没通过（退出码 {r.returncode}）：' + ((res.get('结果') or txt).strip()[-200:] or '没有输出'))
        if c is True and 'ok' not in (res.get('结果') or txt).lower():
            c = (False, '自检没有按要求回复：' + ((res.get('结果') or txt).strip()[-200:] or '没有输出'))
    except KBError as x:
        c = (False, str(x))
    except subprocess.TimeoutExpired:
        c = (False, '2 分钟没有回复（网络慢或卡住）')
    _health_set(e['id'], c is True, '' if c is True else c[1], src=src, name=e['名称'])
    return dict(engine_health_one(e), 耗时=round(time.time() - t0))


def engine_health_one(e, h=None):
    h = _health_load() if h is None else h
    x = dict(h.get(e['id']) or {})
    x.pop('ts', None)
    return {'id': e['id'], '名称': e['名称'], '默认': e.get('默认', False), '状态': x.get('状态') or '未检测', **{k: v for k, v in x.items() if k != '状态'}}


def engine_health():
    """网页常驻显示：在用的引擎各自能不能用；总体 = 默认引擎的状态（有在用的不可用时提示）。"""
    h = _health_load()
    use = engines_in_use()
    items = [dict(engine_health_one(e, h), 在用=e['id'] in use) for e in engines()]
    dft = next((x for x in items if x['默认']), items[0] if items else {})
    bad = [x for x in items if x['在用'] and x['状态'] == '不可用']
    return {'总体': '不可用' if dft.get('状态') == '不可用' else '部分不可用' if bad else dft.get('状态', '未检测'),
            '默认': dft.get('名称', ''), '引擎': items}


def health_tick(force=False):
    """网页启动时和每 6 小时：在用的引擎，这段时间没有真实会话的结果就自检一次。"""
    h = _health_load()
    for eid in engines_in_use():
        x = h.get(eid) or {}
        if force or time.time() - (x.get('ts') or 0) > PROBE_HOURS * 3600:
            try:
                engine_probe(eid)
            except Exception as e:
                print('engine probe:', repr(e), flush=True)


def engine_test(eid):
    """网页「测试」：让引擎调用一次 kb_overview 并回复，验证能运行、能用知识库工具。"""
    e = get_engine(eid)
    t0 = time.time()
    code, res = engine_run(e, '这是知识库的连通性测试。请调用知识库 MCP 工具 kb_overview（在你的工具列表里可能叫 '
                              'mcp__kb__kb_overview、mcp_kb_kb_overview 之类），然后只回复一行：「测试成功：共 N 期」。'
                              '调用不到就回复「测试失败：」加原因。', ROOT)
    ok = code == 0 and '测试成功' in (res.get('结果') or '')
    if ok:
        _health_set(e['id'], True, src='测试', name=e['名称'])
    return {'成功': ok, '退出码': code,
            '结果': (res.get('结果') or '').strip()[:1500], '耗时': round(time.time() - t0)}


def _front_desc(text):
    """SKILL.md / 命令 md 头部的 description（支持 >- 多行）。"""
    m = re.match(r'^---\n(.*?)\n---', text, re.S)
    if not m:
        line = next((x.strip('# ').strip() for x in text.splitlines() if x.strip()), '')
        return line[:200]
    fm = m.group(1)
    d = re.search(r'^description:\s*(.*?)(?=^\S[^\n]*:|\Z)', fm, re.S | re.M)
    if not d:
        return ''
    v = re.sub(r'^[>|]-?\s*', '', d.group(1).strip())
    return re.sub(r'\s+', ' ', v).strip(' "\'')[:300]


def list_commands():
    """本机可用的技能和斜杠命令（个人的 + 各期开发仓库、已登记仓库里的），给「从命令 / 技能添加流程」选。"""
    home = Path.home() / '.claude'
    roots = {}
    for n in period_names():
        r = load_meta(PERIODS / n).get('开发仓库')
        if r and Path(r).is_dir():
            roots[r] = Path(r).name
    for k, v in load_repos().items():
        if Path(v['路径']).is_dir():
            roots.setdefault(v['路径'], k)
    out, seen = [], set()

    def add(p, kind, base=None):
        name = '/' + (p.parent.name if p.name == 'SKILL.md' else p.stem)
        key = (name, str(base or ''))
        if key in seen:
            return
        seen.add(key)
        try:
            desc = _front_desc(read_text(p))
        except OSError:
            desc = ''
        out.append({'名称': name, '类型': kind, '路径': str(p), '说明': desc,
                    '仓库': str(base) if base else '', '相对路径': str(p.relative_to(base)) if base else ''})
    for p in sorted((home / 'skills').glob('*/SKILL.md')):
        add(p, '个人技能')
    for p in sorted((home / 'commands').glob('*.md')):
        add(p, '个人命令')
    for r, nm in roots.items():
        base = Path(r)
        for p in sorted((base / '.claude' / 'skills').glob('*/SKILL.md')):
            add(p, f'仓库技能（{nm}）', base)
        for p in sorted((base / '.claude' / 'commands').glob('*.md')):
            add(p, f'仓库命令（{nm}）', base)
    return out


def _quick_flow(c):
    name = c['名称']
    return dict(
        BUILTIN_FLOWS['generic'], 名称=name, 说明=(c.get('说明') or '')[:200] or f'按 {name} 开发',
        识别文件=[c['相对路径']] if c.get('相对路径') else [],
        文档目录方式='模板', 文档目录模板='claude_project_memory/{分支目录}', 准备方式='通用', 流程配置文件='',
        开发步骤=f"""1. kb_dev_prepare 准备资料（本期需求、设计稿、接口文档的本地副本在 {{资料目录}}）。
2. 用 {name} 推进：反复执行 {name}，直到它报告全部完成。{name} 需要的物料优先填 {{资料目录}} 里的本地文件路径和本期登记的链接。
3. {name} 停下来等人填写 / 回答 / 确认文档时，不要干等：把要确认的内容用 kb_ask 发到网页，拿到回答后写回对应文档，再继续执行 {name}。""",
        确认点=f'{name} 停下来等人填写或确认的地方（待确认问题、方案确认等）、方案取舍、缺少账号或环境、操作被权限拦截等',
        续跑提示=f'先看 {name} 的产物文档确认实际进度，然后接着执行 {name}',
        开发约定=f'- 开发流程用 {name}：反复执行 {name} 推进，需要人确认时用 kb_ask 发到网页',
        需求变更提示=f'先 kb_dev_prepare 同步资料副本，再按 {name} 的规则补充物料、更新方案和任务；影响已写代码的用 kb_ask 确认，并 kb_log 记录',
        进度类型='无', 任务清单文件='')


def flow_draft(path, use_ai=False):
    """从技能 / 命令生成开发流程草稿（不保存）：快速模板，或让 Claude 读说明后填写。"""
    c = next((x for x in list_commands() if x['路径'] == path), None)
    if not c:
        raise KBError(f'没有找到这个技能 / 命令：{path}')
    draft = _quick_flow(c)
    if not use_ai:
        return draft
    p = Path(path)
    files = []
    if p.name == 'SKILL.md':
        files = [str(x.relative_to(p.parent)) for x in sorted(p.parent.rglob('*'))
                 if x.is_file() and '__pycache__' not in x.parts][:80]
    fields = '\n'.join(f'- {k}（{typ}）：{help}' for k, _, typ, help in FLOW_FIELDS)
    example = {k: v for k, v in BUILTIN_FLOWS['generic'].items()}
    prompt = f"""请为「本地知识库」生成一套开发流程配置。知识库网页点「开始开发」后，会在代码仓库里后台无人值守地启动 Claude Code，按这套配置开发；
人不在终端前，需要人确认的地方必须用 kb_ask 发到网页、kb_wait_answer 等回答（原流程里「等用户填写 / 回答文档再执行下一步」都要改成这样）。
开发前 kb_dev_prepare 会把本期资料（需求、设计稿、接口文档、文字资料）拷到 {{资料目录}}，可以直接当物料用。

要接入的流程是 {c['名称']}（{c['类型']}），它的说明文件 {path} 内容如下：
<<<
{read_text(p)[:40000]}
>>>
{('同目录文件：' + '、'.join(files)) if files else ''}

配置项（类型 select:A|B 表示只能取其中之一；lines 用 JSON 数组）：
{fields}
可用变量：{json.dumps(FLOW_VARS, ensure_ascii=False)}；文档目录模板 / 命令里还可用 {{分支目录}}（分支名的 / 换成 _）。

参考：通用模板的配置是
{json.dumps(example, ensure_ascii=False, indent=1)}

要求：
- 文档目录要和该流程实际产物目录一致（从说明里找，例如工作目录按分支名生成就用 {{分支目录}}），能用模板就用模板，不要编造脚本。
- 个人技能 / 个人命令任何仓库都能用，识别文件留空数组；仓库命令填它在仓库里的相对路径：{c['相对路径'] or '（不是仓库命令）'}。
- 进度类型：流程有逐条勾选的任务清单（- [ ] / - [x]）就用「任务清单」，任务清单文件填相对文档目录的文件名；否则「无」。准备方式用「通用」，除非流程本身就是 SDD（有 sdd-start.md 模板）。
- 开发步骤写成编号步骤，写清用什么命令推进、推进到什么状态算完成、停下等人时怎么用 kb_ask。
- 只输出一个 JSON 对象，键为上面的配置项名称，不要输出其他文字，不要调用任何工具。"""
    eng = get_engine()
    if team.team_on(None, eng) and team.scene_roles('流程设计')[1]:     # 角色团：请产品经理设计开发步骤和确认点（子 agent 一次可能超过 5 分钟）
        prompt = prompt.replace('不要输出其他文字，不要调用任何工具。',
                                '不要输出其他文字。只可调用 Agent 工具请产品经理（subagent_type=product-manager）设计开发步骤和确认点，'
                                '不要调用其他任何工具；最后只输出 JSON。')
        code, res = engine_run(eng, prompt, ROOT, timeout=900, scene='流程设计', env_extra={'KB_SESSION': 'flow'})
    else:
        code, res = engine_run(eng, prompt, ROOT, timeout=900, env_extra={'KB_SESSION': 'flow'})
    try:
        got = json.loads(re.search(r'\{.*\}', res.get('结果') or '', re.S).group(0))
    except (ValueError, AttributeError):
        raise KBError(f'{eng["名称"]} 没有返回可用的配置，可以先用「快速生成」再手动改：{(res.get("结果") or "")[:300]}')
    for k, _, typ, _ in FLOW_FIELDS:
        v = got.get(k)
        if v is None:
            continue
        if typ == 'lines':
            v = [str(x) for x in v] if isinstance(v, list) else [x.strip() for x in str(v).splitlines() if x.strip()]
        elif typ.startswith('select:') and v not in typ[7:].split('|'):
            continue
        else:
            v = str(v)
        draft[k] = v
    draft['名称'] = draft.get('名称') or c['名称']
    return draft
