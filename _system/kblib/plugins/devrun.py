"""运行到手机：把某一期 / 事项的工程（当前工作区代码）编译、装到手机并拉起启动页（网页「▶ 运行到手机」、kb_run_on_device）。

和「安装最新构建」（install.py，装 Jenkins 上的包）不同：这里用本机工程的代码编译；拉不拉代码按时机「运行到手机前」
（默认不动，可在「⚙ 设置 → ⏱ 时机」或按工程改）。
- 分支（plan_branch）：工程不在本期分支（全栈该端分支 / 开发会话分支 / 开发分支）上时先切过去再编译，切过去就留在那：
  本期分支检出在同一仓库的别的 worktree 里（git 不让同一分支检出两处）→ 改用那份 worktree 编译，主工作区不动；
  否则有未提交改动先 git stash（说明写期名、时间、原分支）并记一条待处理问题，再 git switch（本地没有从 origin 拉）。
  工程被别的会话占着、分支本地和远端都没有、切换失败（失败时把收起的改动还原）→ 不切，照旧用当前工作区，弹框和记录里说明。

- 平台：现在只做 Android（gradle：./gradlew <模块>:install<变体>，ANDROID_SERIAL 指定只装到选的那台 → adb 拉起启动页）；
  iOS（xcodebuild + devicectl）、鸿蒙（hvigor + hdc）先只识别出来，PLATFORMS 里留了位置，后续再做。
- 编译方式（buildlearn.py）：自动 / 远程 / 本地。工程里有远程编译脚本（如 remote_build.sh：rsync 到编译机、远程 gradlew、拉回 apk）
  就自动走远程，依据和开发会话里学到的编译命令显示在弹框；远程脚本安装步骤遇到多台手机会卡交互，这里用假 adb 让 `adb devices`
  只列选中的那台，没装上就自己 adb -s 装；编译顺带改动的已跟踪文件（编译前是干净的）还原。
- 模块 / 变体：默认自动识别 application 模块（build.gradle 里有 com.android.application，多个时 app 优先）、变体 debug；
  「🗂 基础信息 → 工程设置」或弹框里可指定（设置.json「工程」{路径: {运行模块, 运行变体, 运行参数}}）。
- 手机：走现有占用（devlock）：没点名 → 本期占着的优先，再挑空闲的（最近最少用）；别的期 / 本人在用的不装。开工时记为本期占用，
  失败时如果是这次新占的就释放。
- 任务是独立的后台进程（start_new_session），状态、日志写在 _system/cache/devrun/<任务号>.json / .log：
  网页重启、发起的会话（管家、MCP）结束都不会打断编译；网页和 MCP 看的是同一份。
- 排队：工程被开发 / 修复会话占着（_repo_busy）或别的「运行到手机」在编译同一个工程（文件锁）时等，空出来再继续。
- 装好后在这一期 / 事项的记录里写一条：工程@分支@提交 → 型号（序列号）。
"""
import fcntl
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

from kblib.core.common import SYS, KBError, now, sess_env
from kblib.core.periods import load_meta, log, period_dir, save_meta
from kblib.core.repos import repo_pref, save_repo_pref
from kblib.infra import devlock, hosts, pool as _pool, timing
from kblib.ai import devices as _dev
from kblib.plugins import install as _ins
from kblib.plugins import buildlearn as _bl

DIR = SYS / 'cache' / 'devrun'
KEEP = 30                      # 保留最近几个任务的状态和日志
QUEUE_MAX = 2 * 3600
QUEUE_POLL = 10
BUILD_TIMEOUT = 60 * 60
MCP_WAIT = 40 * 60          # kb_run_on_device 最多等多久（任务本身不受影响）
TAIL = 200                     # 网页上显示日志的最后几行
DONE = ('成功', '失败', '已取消')
SKIP = {'build', '.git', '.gradle', '.idea', 'node_modules', '.cxx', 'Pods', 'DerivedData', '.svn', 'oh_modules'}
APP_RX = re.compile(r'''com\.android\.application|plugins\.android\.application|id\s*\(?\s*["']android\.application''')


# ---------------- 识别平台、模块
def _android_root(root):
    """gradle 工程根：仓库根，或 Flutter / RN 这类仓库里的 android/。"""
    for p in (root, root / 'android'):
        if (p / 'gradlew').is_file() and any((p / f).is_file() for f in ('settings.gradle', 'settings.gradle.kts')):
            return p
    return None


def detect_platform(root):
    """→ 'Android' / 'iOS' / '鸿蒙' / ''。"""
    root = Path(root)
    if _android_root(root):
        return 'Android'
    for p in (root, root / 'ios'):
        if p.is_dir() and (any(p.glob('*.xcworkspace')) or any(p.glob('*.xcodeproj'))):
            return 'iOS'
    if (root / 'hvigorfile.ts').is_file() or (root / 'build-profile.json5').is_file():
        return '鸿蒙'
    return ''


def _settings_text(g):
    for f in ('settings.gradle', 'settings.gradle.kts'):
        try:
            return (g / f).read_text('utf-8', 'replace')
        except OSError:
            pass
    return ''


def _gradle_path(rel, settings):
    """模块目录（相对 gradle 根）→ gradle 路径。默认规则是目录层级换成「:」；settings 里写了
    project(':x').projectDir = file('目录') 就用 :x；用了自定义的 include 函数（如 includeModule('app', 'app') 把 app/feature
    注册成 :app-feature）时用「-」连接。认不准的在弹框 / 设置里改。"""
    colon = ':' + rel.replace('/', ':')
    if '/' not in rel or re.search(r'''["']%s["']''' % re.escape(colon), settings):
        return colon
    m = re.search(r'''project\(\s*["'](:[\w.-]+)["']\s*\)\.projectDir\s*=\s*(?:new\s+File\([^,]+,\s*|file\()\s*["']%s/?["']'''
                  % re.escape(rel), settings)
    if m:
        return m.group(1)
    if re.search(r'^\s*(def|fun)\s+\w*include\w*\s*\(', settings, re.M | re.I):
        return ':' + rel.replace('/', '-')
    return colon


def android_modules(root, depth=3):
    """application 模块：[{'模块': ':app', '目录': 'app', '有渠道': bool}]，app 优先，其次层级浅的。"""
    g = _android_root(Path(root))
    if not g:
        return []
    settings, out = _settings_text(g), []

    def walk(p, n):
        try:
            kids = sorted(x for x in p.iterdir() if x.is_dir() and x.name not in SKIP and not x.name.startswith('.'))
        except OSError:
            return
        for x in kids:
            for f in ('build.gradle', 'build.gradle.kts'):
                try:
                    t = (x / f).read_text('utf-8', 'replace')
                except OSError:
                    continue
                code = re.sub(r'//[^\n]*|/\*.*?\*/', '', t, flags=re.S)
                if APP_RX.search(code):
                    rel = x.relative_to(g).as_posix()
                    out.append({'模块': _gradle_path(rel, settings), '目录': rel,
                                '有渠道': bool(re.search(r'\bproductFlavors\s*\{', code))})
                break
            if n > 1:
                walk(x, n - 1)
    walk(g, depth)
    out.sort(key=lambda m: (m['目录'] != 'app', m['目录'].count('/'), m['目录']))
    return out


def gradle_install_task(module, variant):
    v = (variant or 'debug').strip()
    return f'{module}:install{v[:1].upper()}{v[1:]}'


# ---------------- 工程、手机候选
def period_repos(name):
    """这一期 / 事项能运行的工程：开发仓库 + 全栈各端的仓库（本机目录），各带平台。"""
    m = load_meta(period_dir(name))
    seen, out = set(), []
    for end, p in [('', m.get('开发仓库'))] + [(e, (v or {}).get('仓库')) for e, v in (m.get('各端') or {}).items()]:
        if not p or hosts.is_remote(p):
            continue
        rp = str(Path(p).expanduser())
        if rp in seen or not Path(rp).is_dir():
            continue
        seen.add(rp)
        out.append({'路径': rp, '名称': Path(rp).name, '端': end, '平台': detect_platform(rp)})
    out.sort(key=lambda r: r['平台'] != 'Android')
    return out


def _git_info(root):
    def g(*a):
        try:
            r = subprocess.run(['git', '-C', str(root), *a], capture_output=True, text=True, timeout=20)
            return r.stdout.strip() if r.returncode == 0 else ''
        except (OSError, subprocess.TimeoutExpired):
            return ''
    st = g('status', '--porcelain', '-uno')
    return {'分支': g('rev-parse', '--abbrev-ref', 'HEAD'), '提交': g('rev-parse', '--short=8', 'HEAD'),
            '未提交': len([x for x in st.splitlines() if x.strip()])}


def _module_word(c):
    """模块对应的马甲名：:app-alpha / :app:alpha / app/alpha → alpha；:app → ''。"""
    w = (c['模块'].rstrip(':').rsplit(':', 1)[-1] or c['目录'].rsplit('/', 1)[-1]).lower()
    w = re.sub(r'^app[-_]', '', w)
    return '' if w == 'app' else w


def guess_module(cands, branch, line=''):
    """按分支名和它所在的马甲线 master 认出要跑哪个 app 模块：分支 feature-alpha-xxx / 马甲线 beta-master → :app:alpha / :app:beta。
    → {模块, 马甲线, 依据}；认不出（只有 master、没有对得上的模块）返回 None。"""
    for src, words in (('分支 ' + (branch or ''), _pool.line_tokens(branch)), ('马甲线 ' + (line or ''), _pool.line_tokens(line))):
        hit = [c for c in cands if _module_word(c) in words]
        if len(hit) == 1:
            return {'模块': hit[0]['模块'], '马甲线': line, '依据': src.strip()}
    return None


def period_branch(m, end=''):
    """这一期在这个工程上要用的分支：全栈该端的分支，否则开发会话分支 / 开发分支。"""
    if end:
        b = ((m.get('各端') or {}).get(end) or {}).get('分支', '')
        if b:
            return b
    return (m.get('开发会话') or {}).get('分支') or m.get('开发分支', '')


def _g(root, *a, timeout=120):
    try:
        return subprocess.run(['git', '-C', str(root), *a], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return subprocess.CompletedProcess(a, 124, '', str(e))


def plan_branch(root, want, me=''):
    """要不要 / 怎么切到本期分支 → {目标, 动作, 路径, 说明}。动作：
    '' 已在这个分支或没登记分支；'切换' 在这份工作区 git switch；'副本' 分支检出在别的 worktree，改用那份编译；'不切' 照旧用当前工作区。
    占用一律按「有会话在用就不切」（含本期自己的会话：会话正在这个分支上干活，不能从它脚下换分支）。"""
    root = str(root)
    out = {'目标': want or '', '动作': '', '路径': root, '说明': ''}
    if not want or hosts.is_remote(root) or not (Path(root) / '.git').exists():
        return out
    cur = _g(root, 'branch', '--show-current').stdout.strip()
    if cur == want:
        return out
    holder = _pool.branch_holder(root, want)
    if holder and Path(holder).is_dir():
        busy = _pool.pool_busy(holder)
        if busy:
            return dict(out, 动作='不切', 说明=f'本期分支 {want} 检出在 {holder}，{busy}：不切，照旧用当前工作区（{cur or "游离"}）编译')
        return dict(out, 动作='副本', 路径=str(holder), 说明=f'本期分支 {want} 检出在 worktree {holder}，将用那份编译（主工作区不动）')
    busy = _pool.pool_busy(root)
    if busy:
        return dict(out, 动作='不切', 说明=f'{busy}：不切分支，照旧用当前工作区（{cur or "游离"}）编译')
    has = lambda ref: _g(root, 'rev-parse', '--verify', '--quiet', ref, timeout=30).returncode == 0
    if not (has(f'refs/heads/{want}') or has(f'refs/remotes/origin/{want}')):
        return dict(out, 动作='不切', 说明=f'本地和 origin 都没有分支 {want}（没 fetch 到？）：不切，照旧用当前工作区（{cur or "游离"}）编译')
    st = [x for x in _g(root, 'status', '--porcelain', '--untracked-files=no').stdout.splitlines() if x.strip()]
    return dict(out, 动作='切换', 说明=f'将切到 {want} 分支（现在在 {cur or "游离"}）' +
                (f'；{len(st)} 个未提交改动先 git stash 收起，并记一条待处理' if st else '') + '，编完留在这个分支')


def _copy_ignored(base, d):
    try:
        _pool._copy_ignored(base, d)
    except Exception:      # noqa: BLE001 拷不过去就让 gradle 自己报
        pass


def switch_to_period_branch(j, d):
    """编译前（已拿到工程锁）按 plan_branch 切分支；改 j 的 工程 / 分支说明，返回一句说明。"""
    want = j.get('期分支') or ''
    pl = plan_branch(j['工程'], want, j.get('me', ''))
    act, root = pl['动作'], j['工程']
    if act == '副本':
        _copy_ignored(root, pl['路径'])
        j['原工程'], j['工程'] = root, pl['路径']
    elif act == '切换':
        if not _g(root, 'rev-parse', '--verify', '--quiet', f'refs/heads/{want}').returncode == 0:
            _g(root, 'fetch', 'origin', f'{want}:refs/remotes/origin/{want}', timeout=300)
        from kblib.ai.branchswap import stash_for_switch
        try:
            stashed = stash_for_switch(root, want, d, '运行到手机切分支')
        except KBError as e:
            pl = dict(pl, 动作='不切', 说明=f'{e}：不切分支，照旧用当前工作区编译')
            stashed = None
        if pl['动作'] == '切换':
            local = _g(root, 'rev-parse', '--verify', '--quiet', f'refs/heads/{want}').returncode == 0
            r = _g(root, 'switch', want) if local else _g(root, 'switch', '--track', f'origin/{want}')
            if r.returncode:
                if stashed:
                    _g(root, 'stash', 'pop')
                pl = dict(pl, 动作='不切', 说明=f'切到 {want} 失败（{(r.stderr or r.stdout).strip()[-200:]}）'
                          + ('，收起的改动已还原' if stashed else '') + '：照旧用当前工作区编译')
            else:
                pl['说明'] = f'已切到 {want}' + ('' if local else '（从 origin 拉下来）') + '，编完留在这个分支' + (
                    f'；{stashed["文字"]}' + (f'（已记待处理 {stashed["问题"]}）' if stashed['问题'] else '') if stashed else '')
                if stashed:
                    j['收起'] = stashed
    j['切分支'] = pl
    _save(j)
    if pl['说明']:
        _say(j, pl['说明'])
        log(d, '运行到手机', pl['说明'])
    return pl['说明']


def devrun_options(name, repo=''):
    """「▶ 运行到手机」弹框：工程（各带平台、模块候选、记住的模块 / 变体）、手机、当前分支、时机设置、这一期在跑的任务。"""
    d = period_dir(name)
    m = load_meta(d)
    repos = period_repos(d.name)
    if repo and str(Path(repo).expanduser()) not in {r['路径'] for r in repos}:
        rp = str(Path(repo).expanduser())
        if not Path(rp).is_dir():
            raise KBError(f'工程目录不存在：{repo}')
        repos.insert(0, {'路径': rp, '名称': Path(rp).name, '端': '', '平台': detect_platform(rp)})
    if not repos:
        raise KBError('这一期还没有工程：先在开发面板选开发仓库，或在事项「补充」里选工程')
    cur = str(Path(repo).expanduser()) if repo else repos[0]['路径']
    want = (m.get('开发会话') or {}).get('分支') or m.get('开发分支', '')
    mine = m.get('运行到手机') or {}               # 这一期自己记住的模块 / 变体（按工程，池里各份副本算一个）
    ents = {x: _pool.remote_entry(x) for x in [m.get('远程仓库')] + [(v or {}).get('远程仓库') for v in (m.get('各端') or {}).values()] if x}
    for r in repos:
        pf = repo_pref(r['路径'])
        pm = mine.get(_pool.canonical(r['路径'])) or {}
        c = _pool.copy_of(r['路径'])
        ent = next((e for e in ents.values() if e and c and e.get('仓库') == c[0]), None)
        if ent and not pm.get('模块'):             # 基础信息「远程仓库」条目里填的运行模块 / 变体
            pm = {'模块': ent.get('运行模块', ''), '变体': pm.get('变体') or ent.get('运行变体', '')}
        elif ent and not pm.get('变体'):
            pm = dict(pm, 变体=ent.get('运行变体', ''))
        pool_ = bool(_pool.copy_of(r['路径']))
        # 一个远程仓库多个马甲（:app:alpha / :app:beta…）：工程级记住的模块是所有期共用的，不能当成这一期的；
        # 优先级：这一期记住的 > 本机工程的设置 > 按分支所在的马甲线认出来的 > 远程仓库的工程设置 > 第一个候选
        r.update(记住模块=pm.get('模块') or ('' if pool_ else pf.get('运行模块', '')),
                 记住变体=pm.get('变体') or ('' if pool_ else pf.get('运行变体', '')), 运行参数=pf.get('运行参数', ''))
        r['期分支'] = period_branch(m, r['端']) or want
        if r['路径'] == cur:
            r['模块候选'] = android_modules(r['路径']) if r['平台'] == 'Android' else []
            r.update(_git_info(r['路径']))
            r['切分支'] = plan_branch(r['路径'], r['期分支'])
            r['代码更新'] = timing.timing_policy('run_device', r['路径'])[0]
            if not r['记住模块'] and r['模块候选']:
                br = r['期分支'] if r['切分支']['动作'] in ('切换', '副本') else r.get('分支') if r.get('分支') not in ('', 'HEAD') else r['期分支']
                g = guess_module(r['模块候选'], br, _pool.branch_base_line(r['路径'], br))
                if g:
                    r.update(记住模块=g['模块'], 马甲线=g['马甲线'], 模块依据=g['依据'])
            if not r['记住模块'] and pool_:
                r['记住模块'] = pf.get('运行模块', '')
            if not r['记住变体'] and pool_:
                r['记住变体'] = pf.get('运行变体', '')
            if r['平台'] == 'Android':
                mod = r['记住模块'] or (r['模块候选'][0]['模块'] if r['模块候选'] else '')
                try:
                    r['编译'] = _bl.build_plan(r['切分支']['路径'] if r['切分支']['动作'] == '副本' else r['路径'], mod,
                                             r['记住变体'] or 'debug')
                except Exception as e:      # noqa: BLE001 识别失败照旧本地编译
                    r['编译'] = {'方式': '本地', '设置': '自动', '说明': f'识别编译方式失败：{e}', '依据': [], '命令': []}
    want = next((r['期分支'] for r in repos if r['路径'] == cur), want)
    return {'期': d.name, '工程': repos, '当前': cur, '期分支': want, '手机': _ins._phones(d.name) if _dev._adb() else [],
            'adb': bool(_dev._adb()), '在跑': next((j for j in devrun_jobs(d.name) if j['状态'] not in DONE), None),
            '上次': next((j for j in devrun_jobs(d.name) if j['状态'] in DONE), None), '平台支持': list(PLATFORMS)}


# ---------------- 任务状态文件
def _f(jid, ext='json'):
    if not re.fullmatch(r'[0-9a-f]{10}', jid or ''):
        raise KBError('任务号不对')
    return DIR / f'{jid}.{ext}'


def _load(jid):
    try:
        return json.loads(_f(jid).read_text('utf-8'))
    except (OSError, ValueError):
        return None


def _save(j):
    DIR.mkdir(parents=True, exist_ok=True)
    tmp = _f(j['编号']).with_suffix('.tmp')
    tmp.write_text(json.dumps(j, ensure_ascii=False, indent=1), 'utf-8')
    tmp.replace(_f(j['编号']))


def _alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def _fix_dead(j):
    """进程没了但状态还停在进行中（机器重启、被杀）→ 记为失败。"""
    if j and j['状态'] not in DONE and j.get('pid') and not _alive(j['pid']):
        j.update(状态='失败', 摘要=j.get('摘要') or '后台任务进程意外退出（机器重启或被结束）', 结束=now())
        _save(j)
    return j


def _tail(jid, n=TAIL):
    try:
        with open(_f(jid, 'log'), 'rb') as fh:
            fh.seek(0, 2)
            size = fh.seek(max(0, fh.tell() - 64 * 1024))
            lines = fh.read().decode('utf-8', 'replace').splitlines()
            return lines[-n:] if size == 0 else lines[1:][-n:]
    except OSError:
        return []


def devrun_job(jid, tail=TAIL):
    j = _fix_dead(_load(jid))
    if not j:
        raise KBError('没有这个运行任务（已被清理？）')
    return dict(j, 日志=_tail(jid, tail))


def devrun_jobs(name=''):
    """最近的任务（新的在前），name 给了只看这一期。"""
    out = []
    if not DIR.is_dir():
        return out
    for f in sorted(DIR.glob('*.json'), key=lambda f: f.stat().st_mtime, reverse=True):
        j = _fix_dead(_load(f.stem))
        if j and (not name or j.get('期') == name):
            out.append(j)
    return out


def _prune():
    fs = sorted(DIR.glob('*.json'), key=lambda f: f.stat().st_mtime, reverse=True)
    for f in fs[KEEP:]:
        j = _load(f.stem) or {}
        if j.get('状态') in DONE or not _alive(j.get('pid')):
            for x in (f, f.with_suffix('.log')):
                try:
                    x.unlink()
                except OSError:
                    pass


# ---------------- 开始 / 取消
def devrun_start(name, repo='', serial='', module='', variant='', remember=False, by='用户（网页）', me='', build=''):
    """开一个「运行到手机」后台任务，返回任务号。me：发起方自己的期名（开发会话里调用时，自己的会话不算占着工程）。"""
    d = period_dir(name)
    o = devrun_options(d.name, repo)
    r = next(x for x in o['工程'] if x['路径'] == o['当前'])
    plat = r['平台']
    if plat not in PLATFORMS:
        if plat:
            raise KBError(f'{r["名称"]} 是 {plat} 工程：{plat} 运行到手机还没做（现在只支持 Android），后续再加')
        raise KBError(f'{r["名称"]} 认不出是什么工程（没有 gradlew + settings.gradle）：现在只支持 Android gradle 工程')
    run = o['在跑']
    if run and run.get('工程') == r['路径']:
        raise KBError(f'这一期已经有一个运行任务（{run["编号"]}，{run["状态"]}）在跑这个工程，等它结束或先取消')
    module = (module or r['记住模块'] or (r['模块候选'][0]['模块'] if r['模块候选'] else '')).strip()
    if not module:
        raise KBError(f'{r["名称"]} 里没找到 application 模块（build.gradle 里有 com.android.application 的）：'
                      '在弹框或「🗂 基础信息 → 工程设置」里填模块，如 :app')
    if not module.startswith(':'):
        module = ':' + module
    variant = (variant or r['记住变体'] or 'debug').strip()
    if not re.fullmatch(r'[A-Za-z0-9_]+', variant):
        raise KBError(f'变体只能是字母数字，如 debug、prodDebug：{variant}')
    if not o['adb']:
        raise KBError('本机找不到 adb：装好 Android SDK platform-tools 后再试')
    ph = {p['序列号']: p for p in o['手机']}
    if serial:
        p = ph.get(serial)
        if not p:
            raise KBError(f'手机 {serial} 没连着')
        if not p['可装']:
            raise KBError(f'{serial} 现在不能用：{p["说明"]}（别的期 / 本人在用的手机不装，免得覆盖对方的包）')
    else:
        serial = next((p['序列号'] for p in o['手机'] if p['可装']), '')
        if not serial:
            raise KBError('没有能用的手机：' + ('没连着手机' if not ph else '；'.join(f'{p["序列号"]} {p["说明"]}' for p in ph.values())))
    if remember and not _pool.copy_of(r['路径']):          # 本机工程：照旧记到工程设置（一个目录一个马甲）
        save_repo_pref(r['路径'], 运行模块=module, 运行变体='' if variant == 'debug' else variant)
    build = build if build in _bl.MODES else ''
    if remember and build:                                # 编译方式记在主工作区（它的 worktree 共用）
        save_repo_pref(_bl.main_root(r['路径']), 运行编译='' if build == '自动' else build)
    if remember or _pool.copy_of(r['路径']):
        m = load_meta(d)                                  # 这一期记住自己跑的模块 / 变体：同一个远程仓库的别的期（别的马甲）不受影响
        m.setdefault('运行到手机', {})[_pool.canonical(r['路径'])] = {'模块': module, '变体': variant}
        save_meta(d, m)
    jid = uuid.uuid4().hex[:10]
    j = {'编号': jid, '期': d.name, '工程': r['路径'], '工程名': r['名称'], '平台': plat, '模块': module, '变体': variant,
         '参数': r.get('运行参数', ''), '序列号': serial, '型号': ph[serial]['型号'], '发起': by, 'me': me,
         '期分支': r.get('期分支', ''), '编译设置': build, '状态': '排队中', '步骤': '准备', '开始': now(), '结束': '', '摘要': '', '可选变体': [], '提交': '', '分支': '', 'pid': 0}
    # 先记为本期占用：编译要几分钟，别让别的期这时候把手机拿走
    with devlock.locked() as data:
        old = data.get(serial)
        j['新占'] = not (old and old.get('期') == d.name and devlock.valid(old))
        data[serial] = {'期': d.name, '方式': '申请', '开始': (old or {}).get('开始') if not j['新占'] else devlock.stamp(),
                        '分支': r.get('分支') or o['期分支'], '仓库': r['路径'], '型号': j['型号'],
                        '用途': f'运行到手机（{r["名称"]}，编译中）', '最后使用': time.time()}
        devlock.touch_recent([serial])
    _save(j)
    _f(jid, 'log').write_text(f'[{now()}] 运行到手机：{r["名称"]} {gradle_install_task(module, variant)} → {j["型号"]}（{serial}）\n', 'utf-8')
    with open(_f(jid, 'log'), 'ab') as lf:
        pr = subprocess.Popen([sys.executable, '-c', 'import sys; from kblib.plugins import devrun; devrun.devrun_worker(sys.argv[1])', jid],
                              cwd=str(SYS), stdin=subprocess.DEVNULL, stdout=lf, stderr=lf, start_new_session=True,
                              env={**os.environ, 'PYTHONPATH': str(SYS)})
    j['pid'] = pr.pid
    _save(j)
    _prune()
    return jid


def _release_if_new(j):
    if not j.get('新占'):
        return
    with devlock.locked() as data:
        c = data.get(j['序列号'])
        if c and c.get('期') == j['期'] and c.get('方式') == '申请' and '运行到手机' in (c.get('用途') or ''):
            data.pop(j['序列号'])


def devrun_cancel(jid, by='用户（网页）'):
    j = _load(jid)
    if not j:
        raise KBError('没有这个运行任务')
    if j['状态'] in DONE:
        return f'任务已经{j["状态"]}'
    for pid in (j.get('gradle'), j.get('pid')):
        try:
            os.killpg(int(pid), signal.SIGTERM)
        except (OSError, ValueError, TypeError):
            pass
    j = _load(jid) or j
    j.update(状态='已取消', 摘要=f'被取消（{by}）', 结束=now())
    _save(j)
    _release_if_new(j)
    log(period_dir(j['期']), '运行到手机', f'{j["工程名"]} → {j["型号"]}（{j["序列号"]}）已取消（{by}）')
    return '已取消'


def devrun_wait(jid, timeout=BUILD_TIMEOUT + QUEUE_MAX):
    """等任务结束（MCP 用），返回结果文字。"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = devrun_job(jid, 40)
        if j['状态'] in DONE:
            return devrun_text(j)
        time.sleep(3)
    return (f'等了 {timeout // 60} 分钟还没结束，任务 {jid} 仍在后台跑；网页开发面板「📱 手机」里能看进度，'
            f'或 kb_run_on_device(job="{jid}") 接着等。')


def devrun_mcp(name, repo='', serial='', module='', variant='', wait=True, jid=''):
    """kb_run_on_device：开任务；wait 时等结束（最多 MCP_WAIT 秒，超过就让它在后台继续跑）。jid：查 / 接着等已有的任务。"""
    if jid:
        return devrun_wait(jid, MCP_WAIT) if wait else devrun_text(devrun_job(jid, 40))
    d = period_dir(name)
    me = d.name if sess_env('KB_PERIOD') == d.name else ''      # 这一期自己的开发会话调用：自己不算占着工程
    by = 'Claude（' + ('开发会话' if me else '对话 / 问管家') + '）'
    jid = devrun_start(d.name, repo, serial, module, variant, by=by, me=me)
    j = devrun_job(jid, 0)
    head = f'已开始运行到手机（任务 {jid}）：{j["工程名"]} {gradle_install_task(j["模块"], j["变体"])} → {j["型号"]}（{j["序列号"]}）'
    if not wait:
        return head + f'\n独立后台进程在跑，网页开发面板「📱 手机」里看实时日志；要结果时 kb_run_on_device(iteration, job="{jid}")。'
    return head + '\n' + devrun_wait(jid, MCP_WAIT)


def _ref(j):
    """工程@分支@提交（不是 git 仓库时只有工程名）。"""
    return j['工程名'] + ''.join(f'@{x}' for x in (j.get('分支'), j.get('提交')) if x)


def _how(j):
    """这次怎么编的：远程编译写实际跑的脚本命令，本地写 gradle 安装任务。"""
    b = j.get('编译') or {}
    if b.get('方式') == '远程' and b.get('命令'):
        return '远程编译 ' + ' '.join(b['命令'])
    return gradle_install_task(j['模块'], j['变体'])


def devrun_text(j):
    head = f'{_ref(j)} {_how(j)} → {j["型号"]}（{j["序列号"]}）'
    if j['状态'] == '成功':
        return f'✓ 已装好并启动：{head}' + (f'\n{j["摘要"]}' if j.get('摘要') else '')
    if j['状态'] == '已取消':
        return f'已取消：{head}'
    if j['状态'] not in DONE:
        return f'进行中（{j["状态"]} · {j.get("步骤", "")}）：{head}'
    out = f'✗ 失败：{head}\n{j.get("摘要") or ""}'
    if j.get('可选变体'):
        out += '\n可选变体：' + '、'.join(j['可选变体'])
    return out + '\n日志最后几行：\n' + '\n'.join(j.get('日志', [])[-15:])


# ---------------- 后台进程
def _login_env():
    """网页服务由 launchd 启动，环境里常常没有 JAVA_HOME、ANDROID_HOME：从登录 shell 补齐（已有的不覆盖）。"""
    env = dict(os.environ)
    try:
        out = subprocess.run(['zsh', '-lic', 'env -0'], capture_output=True, timeout=20, stdin=subprocess.DEVNULL).stdout
        for kv in out.decode('utf-8', 'replace').split('\0'):
            k, _, v = kv.partition('=')
            if k in ('JAVA_HOME', 'ANDROID_HOME', 'ANDROID_SDK_ROOT', 'GRADLE_USER_HOME', 'PATH') and v and not env.get(k):
                env[k] = v
            elif k == 'PATH' and v:
                env['PATH'] = env.get('PATH', '') + os.pathsep + v
    except (OSError, subprocess.TimeoutExpired):
        pass
    jh = env.get('JAVA_HOME', '')
    if not (jh and Path(jh, 'bin', 'java').is_file()):      # 没设，或指向已删掉的 JDK（gradlew 会直接报错）：换成本机能用的
        good = _find_java()
        if good:
            env['JAVA_HOME'] = good
            print(f'JAVA_HOME {"无效（" + jh + "）" if jh else "没设"}，改用 {good}', flush=True)
    for k in ('KB_PERIOD', 'ANDROID_SERIAL'):
        env.pop(k, None)
    adb = _dev._adb()
    if adb:     # 不走知识库 adb 守卫（占用由这里自己管）
        env['PATH'] = str(Path(adb).parent) + os.pathsep + env.get('PATH', '')
    return env


def _find_java():
    """本机能用的 JDK：/usr/libexec/java_home → Android Studio 自带的 JBR。"""
    try:
        jh = subprocess.run(['/usr/libexec/java_home'], capture_output=True, text=True, timeout=10).stdout.strip()
        if jh and Path(jh, 'bin', 'java').is_file():
            return jh
    except (OSError, subprocess.TimeoutExpired):
        pass
    for app in sorted(Path('/Applications').glob('Android Studio*.app')):
        for sub in ('Contents/jbr/Contents/Home', 'Contents/jre/Contents/Home'):
            if (app / sub / 'bin' / 'java').is_file():
                return str(app / sub)
    return ''


def _repo_lock(root):
    DIR.mkdir(parents=True, exist_ok=True)
    return open(DIR / f'.{hashlib.md5(str(root).encode()).hexdigest()[:12]}.lock', 'a')


def _wait_repo(j):
    from kblib.bugs.fix import _repo_busy
    fh, t0, said = _repo_lock(j['工程']), time.time(), ''
    while True:
        busy = ''
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            busy = _repo_busy(j['工程'], me=j.get('me', ''))
            if not busy:
                return fh
            fcntl.flock(fh, fcntl.LOCK_UN)
        except BlockingIOError:
            busy = '另一个「运行到手机」正在编译这个工程'
        if busy != said:
            _say(j, f'排队：{busy}，空出来后自动继续')
            said = busy
        if time.time() - t0 > QUEUE_MAX:
            raise KBError(f'排队超过 {QUEUE_MAX // 60} 分钟，已放弃：{busy}')
        time.sleep(QUEUE_POLL)


def _say(j, text, state=None):
    j['步骤'] = text
    if state:
        j['状态'] = state
    _save(j)
    print(f'[{time.strftime("%H:%M:%S")}] {text}', flush=True)


def devrun_summary(text):
    """gradle / adb 输出 → (错误摘要, 可选变体)。"""
    lines, out, seen = text.splitlines(), [], set()

    def add(s):
        s = s.rstrip()
        if s.strip() and s not in seen and len(out) < 20:
            seen.add(s)
            out.append(s)
    for i, ln in enumerate(lines):
        if ln.startswith('* What went wrong'):
            for x in lines[i + 1:i + 12]:
                if x.startswith('* ') or x.startswith('BUILD FAILED'):
                    break
                add(x)
        elif re.match(r'^(e: |w: .*error)|.*\.(java|kt|xml|cpp|c|h):\d+.*\berror\b|^\s*error:|.*AAPT: error|.*INSTALL_FAILED|'
                      r'.*No connected devices|.*Unable to locate a Java Runtime|.*JAVA_HOME is not set|^ERROR:', ln):
            add(ln)
    cand = []
    m = re.search(r"Task '[^']*install\w*' is ambiguous[^\n]*Candidates are: ([^\n]+)", text)
    if m:
        cand = [re.sub(r'^install', '', c)[:1].lower() + re.sub(r'^install', '', c)[1:]
                for c in re.findall(r"'(install\w+)'", m.group(1))]
    hint = []
    if cand:
        hint.append('这个模块有多个渠道（productFlavors），要指定变体，如 ' + '、'.join(cand[:4]))
    elif re.search(r"Task '[^']*install\w*' not found|Cannot locate tasks that match '[^']*install", text):
        hint.append('没有这个安装任务：检查变体名（有渠道时要写全，如 xxxDebug），或在「🗂 基础信息 → 工程设置」里指定')
    if re.search(r"Project '[^']+' not found", text):
        hint.append('模块路径不对：在弹框或「🗂 基础信息 → 工程设置」里填对 application 模块（如 :app）')
    if re.search(r'Unable to locate a Java Runtime|JAVA_HOME is not set|Could not find java', text):
        hint.append('找不到 Java：在登录 shell（~/.zshrc）里设好 JAVA_HOME')
    if 'INSTALL_FAILED_UPDATE_INCOMPATIBLE' in text or re.search(r'signatures do not match', text, re.I):
        hint.append('手机上已装的包签名不一致：先在手机上卸载这个应用（会清空数据）再运行')
    if 'INSTALL_FAILED_VERSION_DOWNGRADE' in text:
        hint.append('手机上的版本比这次编的高：先卸载手机上的应用再运行')
    if 'INSTALL_FAILED_USER_RESTRICTED' in text or 'INSTALL_FAILED_ABORTED' in text:
        hint.append('手机上拒绝了安装：在手机上打开「USB 安装」/ 点允许后再试')
    if not out:
        out = [x for x in lines if x.strip()][-8:]
    return '\n'.join((['提示：' + h for h in hint] if hint else []) + out), cand


def _gradle(j, g, env):
    cmd = ['./gradlew', gradle_install_task(j['模块'], j['变体'])] + (j.get('参数') or '').split()
    _say(j, '编译安装：' + ' '.join(cmd), '编译中')
    return _stream(j, cmd, g, dict(env, ANDROID_SERIAL=j['序列号']))


def _stream(j, cmd, g, env):
    """跑编译命令，输出写进任务日志，按 gradle 的「> Task」更新步骤；超时结束。→ (退出码, 输出)。"""
    buf, t0, last = [], time.time(), 0
    pr = subprocess.Popen(cmd, cwd=str(g), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          start_new_session=True)
    j['gradle'] = pr.pid
    _save(j)
    for raw in pr.stdout:
        ln = raw.decode('utf-8', 'replace').rstrip('\n')
        sys.stdout.write(ln + '\n')
        buf.append(ln)
        if len(buf) > 5000:
            del buf[:1000]
        m = re.match(r'^> Task (\S+)', ln)
        if m and time.time() - last > 2:
            last = time.time()
            j['步骤'] = f'编译中 {m.group(1)}'
            _save(j)
        if time.time() - t0 > BUILD_TIMEOUT:
            os.killpg(pr.pid, signal.SIGTERM)
            buf.append(f'编译超过 {BUILD_TIMEOUT // 60} 分钟，已结束')
            break
    sys.stdout.flush()
    pr.stdout.close()
    rc = pr.wait()
    return rc, '\n'.join(buf)


def _apks(root):
    """工程里所有 build/outputs/apk 下的 apk（远程编译拉回来的产物；脚本编译前会删旧 apk）。"""
    try:
        out = subprocess.run(['find', str(root), '-path', '*/build/outputs/apk/*', '-name', '*.apk', '-not', '-path', '*/.git/*'],
                             capture_output=True, text=True, timeout=120).stdout.split('\n')
    except (OSError, subprocess.TimeoutExpired):
        return []
    fs = [Path(x) for x in out if x.strip()]
    return sorted((f for f in fs if f.is_file()), key=lambda f: f.stat().st_mtime, reverse=True)


def _newest_apk(g, j, t0):
    mod = next((m for m in android_modules(g) if m['模块'] == j['模块']), None)
    dirs = [g / mod['目录']] if mod else [g]
    apks = []
    for d in dirs:
        for f in (d / 'build' / 'outputs' / 'apk').rglob('*.apk') if (d / 'build' / 'outputs' / 'apk').is_dir() else []:
            try:
                if f.stat().st_mtime >= t0 - 5:
                    apks.append(f)
            except OSError:
                pass
    return max(apks, key=lambda f: f.stat().st_mtime) if apks else None


def _badging(apk):
    """aapt dump badging → (包名, 启动 Activity)。"""
    adb = _dev._adb()
    if not adb or not apk:
        return '', ''
    sdk = Path(adb).resolve().parent.parent
    for tool in sorted(sdk.glob('build-tools/*/aapt2'), reverse=True)[:1] + sorted(sdk.glob('build-tools/*/aapt'), reverse=True)[:1]:
        try:
            r = subprocess.run([str(tool), 'dump', 'badging', str(apk)], capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            continue
        pkg = re.search(r"package: name='([^']+)'", r.stdout)
        act = re.search(r"launchable-activity: name='([^']+)'", r.stdout)
        if pkg:
            return pkg.group(1), act.group(1) if act else ''
    return '', ''


def _launch(j, apk, text):
    pkg, act = _badging(apk)
    if not pkg:     # 从 gradle 输出认包名兜底：Installing APK 'x.apk' 认不出包名，就看模块 build.gradle 的 applicationId
        m = re.search(r'''applicationId\s*=?\s*["']([\w.]+)["']''', text)
        pkg = m.group(1) if m else ''
    if not pkg:
        return False, '装好了，但认不出包名，没能自动拉起（手机上手动打开）'
    adb = _dev._adb()
    args = (['shell', 'am', 'start', '-n', f'{pkg}/{act}'] if act else
            ['shell', 'monkey', '-p', pkg, '-c', 'android.intent.category.LAUNCHER', '1'])
    try:
        r = subprocess.run([adb, '-s', j['序列号'], *args], capture_output=True, text=True, timeout=60)
        out = (r.stdout + r.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f'拉起 {pkg} 失败：{e}'
    print(out, flush=True)
    j['包名'] = pkg
    if r.returncode or re.search(r'Error|No activities found|monkey aborted', out):
        return False, f'装好了，拉起 {pkg} 失败：{out[-300:]}'
    return True, f'已拉起 {pkg}' + (f'/{act}' if act else '')


def _run_android(j):
    root = Path(j['工程'])
    g = _android_root(root)
    if not g:
        raise KBError(f'{root} 不是 gradle 工程（没有 gradlew + settings.gradle）')
    env = _login_env()
    plan = _bl.build_plan(j['工程'], j['模块'], j['变体'], j.get('编译设置', ''))
    j['编译'] = {k: plan[k] for k in ('方式', '设置', '说明', '命令', '依据')}
    _save(j)
    if plan['说明']:
        print(f'编译方式：{plan["说明"]}' + (f'（依据：{"；".join(plan["依据"])}）' if plan['依据'] else ''), flush=True)
    if plan['方式'] == '远程':
        return _run_remote(j, root, plan, env)
    t0 = time.time()
    rc, text = _gradle(j, g, env)
    if _load(j['编号']).get('状态') == '已取消':
        return
    if rc:
        j['摘要'], j['可选变体'] = devrun_summary(text)
        raise KBError('编译 / 安装失败')
    if not re.search(r'Installed on \d+ device|Installing APK', text):
        j['摘要'], _ = devrun_summary(text)
        raise KBError('gradle 跑完了，但没看到安装到手机（Installed on … device）')
    _say(j, '拉起启动页', '启动中')
    ok, msg = _launch(j, _newest_apk(g, j, t0), text)
    j['摘要'] = msg
    return ok


def _adb_shim(j, real):
    """假 adb：`adb devices` 只列这次选的手机（远程脚本的安装步骤遇到多台会 read 等输入），其余原样转给真 adb（ANDROID_SERIAL 定到这台）。"""
    d = DIR / f'adbshim-{j["编号"]}'
    d.mkdir(parents=True, exist_ok=True)
    f = d / 'adb'
    f.write_text('#!/bin/sh\n'
                 f'if [ "$1" = devices ]; then printf "List of devices attached\\n{j["序列号"]}\\tdevice\\n\\n"; exit 0; fi\n'
                 f'exec "{real}" "$@"\n', 'utf-8')
    f.chmod(0o755)
    return d


def _tracked_dirty(root):
    st = _g(root, 'status', '--porcelain', '--untracked-files=no').stdout
    return {ln[3:].split(' -> ')[-1].strip().strip('"') for ln in st.splitlines() if ln.strip()}


def _run_remote(j, root, plan, env):
    """远程编译：跑工程自己的远程脚本（同步代码 → 编译机 gradlew → 拉回 apk → 安装），装不上就自己 adb -s 装，再拉起。"""
    import shutil
    det, base = plan['识别'], Path(_bl.main_root(root))
    for dep in det.get('依赖目录') or []:          # worktree 里没有被忽略的远程配置（.idea/.sync）：从主工作区拷过来
        if not (root / dep).exists() and (base / dep).exists():
            try:
                (shutil.copytree if (base / dep).is_dir() else shutil.copy2)(base / dep, root / dep)
                print(f'从主工作区拷来 {dep}（远程编译配置）', flush=True)
            except OSError as e:
                print(f'拷 {dep} 失败：{e}', flush=True)
    if not (root / det['脚本']).is_file():
        raise KBError(f'{root} 里没有 {det["脚本"]}')
    adb = _dev._adb()
    env = dict(env, ANDROID_SERIAL=j['序列号'], PATH=str(_adb_shim(j, adb)) + os.pathsep + env.get('PATH', ''))
    before = _tracked_dirty(root)
    _say(j, '远程编译：' + ' '.join(plan['命令']), '编译中')
    try:
        rc, text = _stream(j, ['/bin/zsh' if det['脚本'].endswith('.sh') else sys.executable] + plan['命令'], root, env)
    finally:
        shutil.rmtree(DIR / f'adbshim-{j["编号"]}', ignore_errors=True)
        side = sorted(_tracked_dirty(root) - before)
        if side:                                   # 编译顺带改的已跟踪文件（如自动生成的多语言 strings）：编译前是干净的，还原
            _g(root, 'checkout', '--', *side)
            j['还原'] = side
            print(f'编译顺带改动了 {len(side)} 个已跟踪文件，已还原：' + '、'.join(side[:5]) + ('…' if len(side) > 5 else ''), flush=True)
    if _load(j['编号']).get('状态') == '已取消':
        return
    built = re.search(r'BUILD SUCCESSFUL|远程构建成功|构建成功', text) and not re.search(r'BUILD FAILED|构建失败', text)
    if not built:
        j['摘要'], j['可选变体'] = devrun_summary(text)
        if (root / '.build.log').is_file():
            j['摘要'] += f'\n（远程编译日志：{root / ".build.log"}）'
        raise KBError('远程编译失败')
    apks = _apks(root)
    mod = next((m for m in android_modules(root) if m['模块'] == j['模块']), None)
    mine = [f for f in apks if mod and f'/{mod["目录"]}/' in str(f)] or apks
    apk = mine[0] if mine else None
    tail = text[text.rfind('步骤6'):] if '步骤6' in text else text[-3000:]
    if not re.search(r'APK安装成功|Success', tail):
        if not apk:
            raise KBError('远程编译成功，但本地没找到拉回来的 apk')
        _say(j, f'安装 {apk.name}', '安装中')
        r = subprocess.run([adb, '-s', j['序列号'], 'install', '-r', '-t', '-d', str(apk)], capture_output=True, text=True, timeout=900)
        out = (r.stdout + r.stderr).strip()
        print(out[-2000:], flush=True)
        if r.returncode or 'Success' not in out:
            j['摘要'], _ = devrun_summary(out)
            raise KBError('远程编译成功，安装到手机失败')
    _say(j, '拉起启动页', '启动中')
    ok, msg = _launch(j, apk, text)
    j['摘要'] = msg + (f'；编译顺带改的 {len(j["还原"])} 个文件已还原' if j.get('还原') else '')
    return ok


def devrun_worker(jid):
    j = _load(jid)
    if not j:
        return
    j['pid'] = os.getpid()
    _save(j)
    d = period_dir(j['期'])
    fh, lease = None, None
    try:
        fh = _wait_repo(j)
        switch_to_period_branch(j, d)                                    # 先切到本期分支（或改用检出它的 worktree），切过去就留在那
        lease = _pool.pool_lease(j['工程'], f'「{j["期"]}」运行到手机')      # 编译期间这份副本算占用（工作副本池分配时跳过）
        lease.__enter__()
        _say(j, '按时机「运行到手机前」更新代码', '准备中')
        base = timing.code_update('run_device', j['工程'], d=d)
        print(f'代码：{timing.baseline_text(base)} · {base.get("结果", "")}', flush=True)
        info = _git_info(j['工程'])
        j.update(分支=info['分支'], 提交=info['提交'], 未提交=info['未提交'])
        _save(j)
        PLATFORMS[j['平台']](j)
        if (_load(jid) or {}).get('状态') == '已取消':
            return
        j.update(状态='成功', 结束=now())
        _save(j)
        with devlock.locked() as data:
            c = data.get(j['序列号']) or {}
            if c.get('期') == j['期']:
                c.update(用途=f'运行到手机：{_ref(j)}', 最后使用=time.time())
        dirty = f'（含 {j["未提交"]} 个未提交改动）' if j.get('未提交') else ''
        log(d, '运行到手机', f'{_ref(j)}{dirty} {_how(j)} → '
                         f'{j["型号"] or "未知型号"}（{j["序列号"]}）· {j["摘要"]}（{j["发起"]}）')
        print(f'[{time.strftime("%H:%M:%S")}] ✓ 完成：{j["摘要"]}', flush=True)
    except Exception as e:      # noqa: BLE001 后台进程里的意外也要让网页看到
        cur = _load(jid) or {}
        if cur.get('状态') == '已取消':
            return
        j.update(状态='失败', 结束=now(), 摘要=j.get('摘要') or str(e))
        if not isinstance(e, KBError):
            j['摘要'] = repr(e)
        _save(j)
        _release_if_new(j)
        print(f'[{time.strftime("%H:%M:%S")}] ✗ 失败：{e}', flush=True)
        log(d, '运行到手机', f'{_ref(j)} → {j["型号"]}（{j["序列号"]}）失败：'
                         f'{(j["摘要"] or str(e)).splitlines()[0][:200]}（{j["发起"]}）')
    finally:
        if lease:
            lease.__exit__(None, None, None)
        if fh:
            fh.close()


# 平台 → 编译安装启动。iOS（xcodebuild + xcrun devicectl）、鸿蒙（hvigorw assembleHap + hdc install / aa start）后续在这里加
PLATFORMS = {'Android': _run_android}
