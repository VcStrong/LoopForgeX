"""编译方式识别与学习：工程是本地编译还是远程编译、用哪个文件哪个命令编——自动认出来并记住，开发中不断观察、越用越准。

- 识别（detect_remote）：工程根目录下的远程编译脚本——文件名带 remote + build（remote_build.sh），或脚本里同时有
  ssh / rsync / sshpass 和 gradlew / xcodebuild / hvigorw。解析出：flavor → 模块映射（case 里 `alpha) GRADLE_TASK=":app-alpha:..."`）、
  支持的 buildType、用法行、引用的工程内配置目录（$LOCAL_PROJECT_DIR/.idea/.sync/... → .idea/.sync，worktree 里没有要拷过去）；
  再看 CLAUDE.md / AGENTS.md / README 有没有写「远程构建 / 推荐」。
- 学习（learn）：增量扫描各期 / 事项「开发会话/*.jsonl」里 AI 跑过的命令，挑出编译命令（remote_build.sh、./gradlew、xcodebuild、hvigorw），
  只算在这个工程（主工作区或它的 worktree）里跑的；按命令累计次数、最后一次、来自哪一期，记进设置.json「工程」{路径: {编译学习}}。
  已读到哪（文件 → 字节数）记在 _system/cache/buildlearn.json，每次只读新增部分。
- 决定（build_plan）：编译方式 自动 / 远程 / 本地。自动 = 识别到远程脚本、配置文件在、模块对得上 flavor 就走远程，
  否则本地；会话里用远程脚本的次数多于本地 gradlew 也算一条依据。
现在「运行到手机」只做 Android；iOS / 鸿蒙的远程脚本照样识别和学习，执行等那两个平台做运行到手机时一起接。
"""
import json
import re
import subprocess
import time
from pathlib import Path

from kblib.core.common import SYS, now
from kblib.core.periods import period_dir, period_names
from kblib.core.repos import repo_pref, save_repo_pref

CACHE = SYS / 'cache' / 'buildlearn.json'
KEEP_CMDS = 20                                  # 每个工程最多记多少条不同的命令
MODES = ('自动', '远程', '本地')
_TOOLS = r'gradlew|xcodebuild|hvigorw'
_BUILD_RX = re.compile(
    r'''((?:[\w./~-]*/)?[\w.-]*remote[\w.-]*build[\w.-]*\.sh(?:[ \t]+(?!>|<|\||&|;|2>)[^\s;&|<>]+)*)'''
    r'''|(\./gradlew(?:[ \t]+(?!>|<|\||&|;|2>)[^\s;&|<>]+)+)'''
    r'''|(\bxcodebuild(?:[ \t]+(?!>|<|\||&|;|2>)[^\s;&|<>]+)+)'''
    r'''|(\bhvigorw(?:[ \t]+(?!>|<|\||&|;|2>)[^\s;&|<>]+)+)''', re.I)


_EXEC_RX = re.compile(r'(?:^|[;&|(\n]|\b(?:then|do|else|time|nohup|exec|bash|zsh|sh|env))\s*(?:[A-Za-z_]\w*=\S*\s+)*$')


def _git(root, *a):
    try:
        r = subprocess.run(['git', '-C', str(root), *a], capture_output=True, text=True, timeout=20)
        return r.stdout.strip() if r.returncode == 0 else ''
    except (OSError, subprocess.TimeoutExpired):
        return ''


def main_root(root):
    """worktree → 它的主工作区（学到的东西按主工作区记，所有 worktree 共用）；不是 git 原样返回。"""
    c = _git(root, 'rev-parse', '--path-format=absolute', '--git-common-dir')
    if c and Path(c).name == '.git':
        return str(Path(c).parent)
    return str(Path(root))


def worktrees(root):
    """主工作区 + 所有 worktree 的路径。"""
    out, base = [], main_root(root)
    for ln in _git(base, 'worktree', 'list', '--porcelain').splitlines():
        if ln.startswith('worktree '):
            out.append(ln[9:])
    return out or [base]


# ---------------- 识别
def _scripts(root):
    out = []
    for f in sorted(Path(root).glob('*.sh')) + sorted(Path(root).glob('*.py')):
        try:
            if f.stat().st_size > 512 * 1024:
                continue
            t = f.read_text('utf-8', 'replace')
        except OSError:
            continue
        named = re.search(r'remote', f.name, re.I) and re.search(r'build|compile|assemble', f.name, re.I)
        remote = re.search(r'\b(ssh|sshpass|rsync)\b', t) and re.search(_TOOLS, t)
        if named or remote:
            out.append((f, t))
    out.sort(key=lambda x: (not re.search(r'remote', x[0].name, re.I), len(x[0].name)))
    return out


def _flavors(t):
    """case 分支里 `label) ... :模块:assemble` → {label: 模块}。"""
    out = {}
    for m in re.finditer(r'^\s*([\w|-]+)\)\s*\n?((?:(?!;;).)*?);;', t, re.M | re.S):
        mod = re.search(r'["\']?(:[\w:.-]+?):(?:assemble|install|bundle)', m.group(2))
        if mod:
            for lab in m.group(1).split('|'):
                if lab != '*':
                    out[lab] = mod.group(1)
    return out


def _deps(t, root):
    """脚本引用的工程内文件（$LOCAL_PROJECT_DIR/xxx、$(dirname "$0")/xxx）→ 它们所在的被忽略目录（worktree 里通常没有）。"""
    out = []
    for p in re.findall(r'\$\{?(?:LOCAL_PROJECT_DIR|PROJECT_DIR|ROOT_DIR|SCRIPT_DIR)\}?/([\w./-]+)', t):
        parts = Path(p).parts
        top = str(Path(*parts[:2])) if len(parts) > 2 else parts[0] if parts else ''
        if top and top not in out and (Path(root) / top).exists() and top not in ('gradle.properties', 'gradlew'):
            out.append(top)
    return out


def _doc_hint(root):
    for n in ('CLAUDE.md', 'AGENTS.md', 'README.md'):
        try:
            t = (Path(root) / n).read_text('utf-8', 'replace')
        except OSError:
            continue
        m = re.search(r'^.*(?:远程(?:构建|编译)|remote[_ -]?build).*(?:推荐|recommend|默认|优先).*$|^.*(?:推荐|默认|优先).*(?:远程(?:构建|编译)|remote[_ -]?build).*$',
                      t, re.M | re.I)
        if m:
            return f'{n}：{m.group(0).strip()[:80]}'
    return ''


def detect_remote(root):
    """→ {脚本, 平台工具, 映射 {flavor: 模块}, 构建类型, 用法, 依赖目录, 配置在, 依据} 或 None。root 用主工作区（脚本、配置都在那）。"""
    root = Path(main_root(root))
    sc = _scripts(root)
    if not sc:
        return None
    f, t = sc[0]
    tool = (re.search(_TOOLS, t) or [''])[0]
    usage = re.search(r'^#\s*(?:用法|usage)\s*[:：]\s*(.+)$', t, re.M | re.I)
    deps = _deps(t, root)
    cfg = re.findall(r'\$\{?(?:LOCAL_PROJECT_DIR|PROJECT_DIR)\}?/([\w./-]+\.(?:json|properties|conf|cfg|ya?ml))', t)
    cfg_ok = all((root / c).is_file() for c in cfg)
    why = [f'工程里有远程编译脚本 {f.name}']
    doc = _doc_hint(root)
    if doc:
        why.append(doc)
    bt = [x for x in ('debug', 'release') if re.search(rf'^\s*{x}\)', t, re.M)]
    return {'脚本': f.name, '工具': tool, '映射': _flavors(t), '构建类型': bt, '用法': usage.group(1).strip() if usage else '',
            '依赖目录': deps, '配置': cfg, '配置在': cfg_ok, '依据': why}


# ---------------- 学习：开发会话里跑过的编译命令
def _commands(o):
    """stream-json 一行里 AI 执行的命令（Claude Bash 的 input.command、Codex 的 command）。"""
    if isinstance(o, dict):
        for k, v in o.items():
            if k in ('command', 'cmd') and isinstance(v, str):
                yield v
            elif k in ('command', 'cmd') and isinstance(v, list) and all(isinstance(x, str) for x in v):
                yield ' '.join(v)
            else:
                yield from _commands(v)
    elif isinstance(o, list):
        for x in o:
            yield from _commands(x)


def build_commands(cmd):
    """一条 shell 命令里的编译调用（去掉重定向、管道）→ [规整后的命令]。"""
    out = []
    for m in _BUILD_RX.finditer(cmd or ''):
        pre = cmd[:m.start()]
        if not _EXEC_RX.search(pre) or pre.count("'") % 2 or pre.count('"') % 2:
            continue                                     # cat / sed remote_build.sh 是在读文件；引号里的是字符串（python -c、sed 表达式）
        toks = []
        for i, t in enumerate(next(g for g in m.groups() if g).split()):
            if i and (re.search(r'[$\'"#`\\\[\]{}]', t) or (t.startswith('-') and not m.group(1))):
                if re.search(r'[$\'"#`\\\[\]{}]', t):
                    break                                # 变量、引号：后面不可靠
                continue                                 # gradlew 的 -q / --offline 这类开关不区分
            toks.append(t)
        if len(toks) < 2 and not m.group(1):
            continue                                     # 只剩 ./gradlew（任务是变量）认不出编的是什么
        c = ' '.join(toks[:8])
        c = re.sub(r'^(?:[\w./~-]*/)?(?=[\w.-]*remote[\w.-]*build)', './', c)      # /abs/path/remote_build.sh、remote_build.sh → ./remote_build.sh
        if c and c not in out:
            out.append(c)
    return out


def _in_repo(cmd, cwd, paths):
    if any(p in cmd for p in paths):
        return True
    if re.search(r'\bcd\s+/', cmd):                      # cd 到别的绝对路径
        return False
    return any(cwd == p or cwd.startswith(p + '/') for p in paths) if cwd else False


def _load_cache():
    try:
        return json.loads(CACHE.read_text('utf-8'))
    except (OSError, ValueError):
        return {}


def _save_cache(c):
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE.with_suffix('.tmp')
    tmp.write_text(json.dumps(c, ensure_ascii=False), 'utf-8')
    tmp.replace(CACHE)


def learn(root, budget=4.0):
    """接着上次读到的位置扫开发会话日志，把这个工程里跑过的编译命令累计进「编译学习」。返回学到的内容（dict）。
    budget：最多花几秒（弹框里调用，别卡住）；没扫完的下次接着扫。"""
    base = main_root(root)
    paths = [str(Path(p)) for p in worktrees(base)]
    cache = _load_cache()
    seen = cache.setdefault(base, {})
    pf = repo_pref(base)
    st = dict(pf.get('编译学习') or {})
    cmds = {c['命令']: c for c in st.get('命令') or []}
    t0, changed = time.time(), False
    for n in period_names():
        sd = period_dir(n) / '开发会话'
        if not sd.is_dir():
            continue
        for f in sorted(sd.glob('*.jsonl')):
            if time.time() - t0 > budget:
                break
            try:
                size = f.stat().st_size
            except OSError:
                continue
            key = str(f)
            off = seen.get(key, 0)
            if off >= size:
                continue
            cwd = ''
            try:
                with open(f, 'rb') as fh:
                    if off:
                        fh.seek(off)
                    for raw in fh:
                        if b'"cwd"' in raw and not cwd:
                            m = re.search(rb'"cwd"\s*:\s*"([^"]+)"', raw)
                            cwd = m.group(1).decode('utf-8', 'replace') if m else ''
                        if not re.search(rb'remote|gradlew|xcodebuild|hvigorw', raw, re.I):
                            continue
                        try:
                            o = json.loads(raw)
                        except ValueError:
                            continue
                        if o.get('type') in ('user', 'result', 'system'):            # 工具输出、汇总里提到的命令不算
                            continue
                        for sh in _commands(o.get('message') or o.get('item') or o):
                            if not _in_repo(sh, cwd, paths):
                                continue
                            for c in build_commands(sh):
                                x = cmds.setdefault(c, {'命令': c, '次数': 0, '远程': bool(re.search(r'remote', c, re.I)), '期': []})
                                x['次数'] += 1
                                x['最后'] = time.strftime('%Y-%m-%d %H:%M', time.localtime(f.stat().st_mtime))
                                if n not in x['期']:
                                    x['期'] = (x['期'] + [n])[-5:]
                                changed = True
            except OSError:
                continue
            seen[key] = size
    _save_cache(cache)
    if changed:
        lst = sorted(cmds.values(), key=lambda x: (-x['次数'], x.get('最后', '')))[:KEEP_CMDS]
        st = {'命令': lst, '远程次数': sum(x['次数'] for x in lst if x['远程']),
              '本地次数': sum(x['次数'] for x in lst if not x['远程']), '更新': now()}
        save_repo_pref(base, 编译学习=st)
    return st


# ---------------- 决定编译方式
def flavor_for(det, module):
    """模块 → 远程脚本的 flavor 参数（映射里找；找不到按模块名最后一段猜，如 :app-alpha → alpha）。"""
    mp = (det or {}).get('映射') or {}
    hit = [k for k, v in mp.items() if v == module]
    if hit:
        return hit[0]
    w = re.sub(r'^app[-_]', '', module.rstrip(':').rsplit(':', 1)[-1].lower())
    return w if w in mp else ''


def build_type(variant):
    return 'release' if re.search(r'release$', variant or '', re.I) else 'debug'


def build_plan(root, module='', variant='debug', mode='', learn_budget=2.0):
    """→ {方式: 远程 / 本地, 设置: 自动 / 远程 / 本地, 命令: [...], 说明, 依据: [...], 识别, 学习}。
    mode 空 = 用工程设置「运行编译」（没设 = 自动）。"""
    base = main_root(root)
    mode = mode if mode in MODES else (repo_pref(base).get('运行编译') or '自动')
    det = detect_remote(base)
    try:
        st = learn(base, learn_budget)
    except Exception:          # noqa: BLE001 学习失败不耽误运行
        st = repo_pref(base).get('编译学习') or {}
    why = list((det or {}).get('依据') or [])
    if st.get('远程次数'):
        top = next((c for c in st.get('命令') or [] if c['远程']), None)
        why.append(f'开发会话里远程编译用过 {st["远程次数"]} 次' + (f'（最常用 {top["命令"]}）' if top else ''))
    if st.get('本地次数'):
        why.append(f'本地 gradlew 用过 {st["本地次数"]} 次')
    out = {'设置': mode, '识别': det, '学习': st, '依据': why, '命令': [], '方式': '本地', '说明': ''}
    if mode == '本地':
        out['说明'] = '按设置用本地 gradle 编译'
        return out
    if not det:
        out['说明'] = '没认出远程编译脚本，用本地 gradle 编译' if mode == '远程' else ''
        return out
    if not det['配置在']:
        out['说明'] = f'远程编译脚本 {det["脚本"]} 要的配置（{"、".join(det["配置"])}）不在，用本地 gradle 编译'
        return out
    fl = flavor_for(det, module) if det['映射'] else ''
    if det['映射'] and not fl:
        out['说明'] = f'{det["脚本"]} 只认 {"、".join(det["映射"])}，对不上模块 {module}，用本地 gradle 编译'
        return out
    cmd = ['./' + det['脚本']] + ([fl] if fl else []) + ([build_type(variant)] if det['构建类型'] else [])
    out.update(方式='远程', 命令=cmd, 说明=f'远程编译：{" ".join(cmd)}' + ('（按设置）' if mode == '远程' else '（自动识别）'))
    return out
