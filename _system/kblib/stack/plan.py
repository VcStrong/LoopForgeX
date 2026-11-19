"""全栈：一期挂多个端的仓库（服务端 / Web / iOS / Android / 鸿蒙 / 小程序 / 桌面端，也可以自定义如 Unity），AI 先出「需求规格 + 设计对照 + 影响分析 + 接口契约」，你拍板后各端再开工。

一人全栈的第一道关卡（拍板 1）：
  1. 各端仓库：期信息「各端」{端: {仓库, 分支}}，只放这一期加了的端；仓库从基础信息里的本地路径、登记的仓库、各期用过的开发仓库里选，按仓库里的文件自动认端。
  2. 出方案：后台只读会话读这一期资料和各端代码，用 kb_stack_plan 交回「方案」（涉及哪些端、各端改什么、接口、待确认）
     和接口契约草案（OpenAPI YAML）。存在 期/<名>/全栈/方案.json、全栈/api/openapi.yaml。
  3. 拍板（只能你在网页上点）：契约锁成 api/openapi.v<N>.yaml，之后各端按这个版本开发；要改就提意见重出方案。
各端用什么开发流程仍按「⚙ 设置 → 开发流程」的识别文件自动匹配；服务端自己的 SDD 以后作为一套流程接入，现在先按仓库现有约定。
"""
import json
import re
import shutil
import threading
from datetime import datetime
from pathlib import Path

from kblib.core.common import PERIODS, KBError, now
from kblib.infra import hosts, pool as _pool, timing
from kblib.core.periods import design_previews, load_meta, log, period_dir, period_names, save_meta
from kblib.core.repos import REPO_ENDS, load_repos, repo_pref, save_repo_pref
from kblib.ai.devsession import _alive, _dev_result, _notify, auto_decide_on
from kblib.ai.engines import engine_popen, role_engine
from kblib.ai import team
from kblib.ai.flows import flow_for, flows, get_flow
from kblib.plugins.run import base_repo_paths
from kblib import lazy
_stackdev = lazy('kblib.stack.dev')      # 上层模块：用到时才 import
_fix = lazy('kblib.bugs.fix')
_launch = lazy('kblib.stack.launch')     # 新产品：完善需求、建工程、发布

ENDS = REPO_ENDS               # 常用平台（候选和排序）；端名不限于这些，见 end_name
STACK_DIR = '全栈'
_END_BAD = re.compile(r'[/\\·、，,;；:：|\s]')
_PROCS = {}

_WEB_DEPS = ('react', 'vue', 'next', 'nuxt', 'svelte', '@angular/core', 'vite', 'umi')
_SRV_DEPS = ('express', 'koa', '@nestjs/core', 'egg', 'fastify', 'hapi', 'midway')
_PATS = {'鸿蒙': ['oh-package.json5', 'build-profile.json5', 'AppScope/app.json5'],
         'Android': ['*/src/main/AndroidManifest.xml', 'src/main/AndroidManifest.xml'],
         'iOS': ['*.xcodeproj', '*.xcworkspace', 'Podfile', '*/*.xcodeproj', 'Package.swift'],
         '服务端': ['pom.xml', 'go.mod', 'Cargo.toml', 'composer.json', 'pyproject.toml', 'requirements.txt', 'manage.py',
                  'src/main/java', 'src/main/kotlin', 'src/main/resources/application.yml', 'src/main/resources/application.properties']}


def end_name(end):
    """校验端名：常用平台原样（大小写不同的归一，android → Android）；自定义端去首尾空格、最多 20 字、不含分隔符。"""
    e = (end or '').strip()
    if not e:
        raise KBError('端名不能为空')
    hit = next((x for x in ENDS if x.lower() == e.lower()), None)
    if hit:
        return hit
    if len(e) > 20:
        raise KBError(f'端名太长（最多 20 个字）：{e}')
    if _END_BAD.search(e) or e.startswith('__'):
        raise KBError(f'端名不能含空格和 / · 、 , ; : | 这类分隔符：{e}')
    return e


def end_key(end):
    """端的排序：常用平台按 ENDS 的顺序，自定义端排在后面（按名字）。"""
    return (ENDS.index(end), '') if end in ENDS else (len(ENDS), end or '')


def sort_ends(ends):
    return sorted(ends, key=end_key)


def detect_ends(loc):
    """这个工程是哪个端（可能不止一个，如前后端同仓）：手动指定的排第一，再加上按文件认出的；本机、远程一样。"""
    mine = repo_pref(loc).get('端')
    found = _detect_ends_files(loc)
    return ([mine] if mine else []) + [e for e in found if e != mine]


def _detect_ends_files(loc):
    """按仓库里的文件认端（认不出返回空，不猜）。"""
    try:
        have, files = hosts.probe(loc, [x for v in _PATS.values() for x in v], cat=['package.json'])
    except KBError:
        return []
    try:
        pj = json.loads(files.get('package.json') or '{}')
        deps = {**(pj.get('dependencies') or {}), **(pj.get('devDependencies') or {})}
    except ValueError:
        deps = {}
    hit = lambda e: any(x in have for x in _PATS[e])
    out = [e for e in ('鸿蒙', 'Android', 'iOS') if hit(e)]
    if 'react-native' not in deps and any(k in deps for k in _WEB_DEPS):
        out.append('Web')
    srv = [x for x in _PATS['服务端'] if x in have and not ('Android' in out and x.startswith('src/main/'))]
    if srv or any(k in deps for k in _SRV_DEPS):
        out.append('服务端')
    return out


def stack_repos():
    """可选的本地仓库：基础信息里的本地路径 + 登记的仓库 + 各期用过的开发仓库，附认出的端、当前分支。"""
    seen = {}
    for r in base_repo_paths():
        seen.setdefault(r['路径'], r['名称'])
    for nm, r in load_repos().items():
        seen.setdefault(str(Path(r['路径'] if isinstance(r, dict) else r).expanduser()), nm)
    for n in period_names():
        m = load_meta(PERIODS / n)
        for x in [m.get('开发仓库')] + [v.get('仓库') for v in (m.get('各端') or {}).values()]:
            if x:
                seen.setdefault(_pool.canonical(x), Path(x).name)       # 工作副本池的各份副本合成一个（选仓库，开工时分配）
    out = []
    for pth, nm in seen.items():
        if hosts.is_remote(pth):          # 远程的不在这里逐个连（慢），选了再校验
            out.append({'路径': pth, '名称': nm, '端': [], '分支': '', '远程': True})
            continue
        kind = 'git' if (Path(pth) / '.git').exists() else 'svn' if (Path(pth) / '.svn').exists() else '本地' if Path(pth).is_dir() else ''
        if not kind:
            continue
        out.append({'路径': pth, '名称': nm, '端': detect_ends(pth), '版本库': kind, '分支': '' if kind == '本地' else hosts.branch_of(pth, kind)})
    out = [x for x in out if not _pool.copy_of(x['路径'])]      # 池里的副本不单列，按基础信息的远程仓库条目列
    for e in _pool.remote_entries():
        out.append({'路径': _pool.REMOTE + e['编号'], '名称': _pool.remote_label(e), '端': [end_name(e['平台类型'])] if e.get('平台类型') else [],
                    '版本库': 'git', '分支': e.get('基线分支', ''), '远程仓库': e['编号']})
    return sorted(out, key=lambda x: (end_key(x['端'][0]) if x['端'] else (99, ''), x['名称']))


def stack_branches(repo):
    """仓库可选的分支（网页分支框的下拉）：git 本地 + 远程分支（去掉 origin/ 前缀、去重），svn 是 trunk + branches/*。
    → {'当前': 当前分支, '分支': [...]}；读不到（远程连不上、svn 服务器要登录）返回空列表，不报错，手动填就行。"""
    repo = (repo or '').strip()
    if not repo:
        raise KBError('没有仓库路径')
    try:
        kind = hosts.vcs(repo)
        cur = hosts.branch_of(repo, kind) if kind else ''           # 不是 git / svn 的本地目录：没有分支
        out = []
        if kind == 'git':
            p = hosts.git(repo, 'for-each-ref', '--sort=-committerdate', '--format=%(refname)', 'refs/heads', 'refs/remotes', timeout=20)
            for ln in p.stdout.splitlines():
                ln = ln.strip()
                if ln.startswith('refs/heads/'):
                    out.append(ln[len('refs/heads/'):])
                elif ln.startswith('refs/remotes/') and not ln.endswith('/HEAD'):
                    out.append(ln[len('refs/remotes/'):].split('/', 1)[-1])
        elif kind == 'svn':
            out.append('trunk')
            p = hosts.svn(repo, 'ls', '^/branches', timeout=20)
            if p.returncode == 0:
                out += ['branches/' + x.strip().rstrip('/') for x in p.stdout.splitlines() if x.strip()]
    except KBError:
        return {'当前': '', '分支': []}
    seen = []
    for b in ([cur] if cur else []) + out:
        if b and b not in seen:
            seen.append(b)
    return {'当前': cur, '分支': seen[:200]}


def period_ends(d, m=None):
    """这一期的各端；老的单仓库期（只有「开发仓库」）按认出的端补上。"""
    m = m or load_meta(d)
    ends = dict(m.get('各端') or {})
    if not ends and m.get('开发仓库'):
        e = (detect_ends(m['开发仓库']) or [''])[0]        # 认不出端就留空让人在「各端」里选，不再默认当成 Android
        if e:
            ends[e] = {'仓库': m['开发仓库'], '分支': (m.get('开发会话') or {}).get('分支', ''), '来自': '开发仓库'}
    return ends


def stack_set_end(name, end, repo='', branch='', by='用户（网页）'):
    """设这一期某个端的仓库和分支；repo 为空 = 这一期不涉及这个端（从「各端」里移除）。
    端可以是常用平台，也可以是自定义的名字（见 end_name）。新选的仓库没填分支时，自动填它的当前分支。"""
    end = end_name(end)
    d = period_dir(name)
    m = load_meta(d)
    ends = period_ends(d, m)
    repo = (repo or '').strip()
    ent = None
    if _pool.is_remote_choice(repo):          # 基础信息里的远程仓库：从工作副本池分配一份，切到这个端的分支（没有就从基线分支拉）
        repo, ent = _pool.remote_claim(repo, f'{d.name}/{end}', (branch or '').strip())
        _pool.pool_release(repo)
        branch = branch or ''
    if repo:
        kind, rp = hosts.check_dir(repo)      # 路径不存在 / 远程连不上 在这里报；不是 git / svn 的目录 → 本地
        branch = '' if kind == '本地' else (branch or '').strip()
        if kind != '本地' and not branch and (ends.get(end) or {}).get('仓库') != rp:     # 新选的仓库：git 用当前分支，svn 用当前 URL 对应的 trunk / branches/xxx
            try:
                branch = hosts.branch_of(rp, kind)
            except KBError:
                branch = ''
        if kind == 'svn' and branch:
            branch = hosts.svn_branch(rp, branch)        # feat-x → branches/feat-x（服务器上没有就报错）
        ends[end] = {'仓库': rp, '分支': branch, '版本库': kind, **({'流程': ends[end]['流程']} if (ends.get(end) or {}).get('流程') else {}),
                     **({'远程仓库': ent['编号']} if ent else {})}
        if end not in detect_ends(rp):          # 认不出或认成别的端：记住这个工程是这一端，以后选它就知道
            save_repo_pref(rp, 端=end)
        msg = (f'{end}：基础信息「{ent["名称"]}」→ {_pool.copy_label(rp)}' if ent else f'{end}：{hosts.label(rp)}') + ('（svn：AI 只改工作副本，你验收后由知识库提交）' if kind == 'svn' else
                                           '（不是 git / svn 的本地目录：AI 直接改目录里的文件，不提交、不推送）' if kind == '本地' else '') + (f'，分支 {branch}' if branch else '')
    else:
        ends.pop(end, None)
        msg = f'{end}：不涉及'
    for v in ends.values():
        v.pop('来自', None)
    m['各端'] = ends
    save_meta(d, m)
    log(d, '各端', f'{msg}（{by}）')
    dev = m.get('各端开发') or {}
    if repo and dev and end not in dev and end in ((m.get('全栈') or {}).get('涉及') or []):   # 变更后要做、之前缺仓库的端：选好就开工
        msg += '；' + _stackdev.stack_dev_start(d.name, end, by)
    return msg


def _flow_name(fid):
    try:
        return get_flow(fid)['名称'] if fid else ''
    except KBError:
        return ''


EMPTY_TREE = '4b825dc642cb6eb9a060e54bf8d69288fbee4904'      # git 的空树：git diff 空树..HEAD = 全部内容


def refresh_vcs(d, m=None, by='自动'):
    """记成「本地目录」的端，后来变成了 git / svn 仓库（如按你的回答 git init 了）：改过来，审查、验收、推送才按仓库走。→ 改了哪些端"""
    m = m or load_meta(d)
    ends, dev, changed = m.get('各端') or {}, m.get('各端开发') or {}, []
    for e, v in ends.items():
        if v.get('版本库') != '本地' or hosts.is_remote(v.get('仓库', '')):
            continue
        kind = hosts.vcs(v['仓库'])
        if not kind:
            continue
        try:
            br = hosts.branch_of(v['仓库'], kind)
        except KBError:
            br = ''
        v.update(版本库=kind, 分支=v.get('分支') or br)
        if e in dev:
            dev[e].update(版本库=kind, 分支=dev[e].get('分支') or br,          # 本地目录的起点是开工时间：开工后才建的库，提交都算这次的
                          起点=EMPTY_TREE if kind == 'git' else '')
        changed.append(f'{e}（{kind}{"，分支 " + br if br else ""}）')
    if changed:
        save_meta(d, m)
        log(d, '各端', f'目录已经变成仓库，改按仓库处理（审查、验收推送）：{"、".join(changed)}（{by}）')
    return changed


def end_remote(repo):
    """git 端的 origin 地址（没有返回 ''）。"""
    try:
        p = hosts.git(repo, 'remote', 'get-url', 'origin', timeout=10)
    except KBError:
        return ''
    return p.stdout.strip() if p.returncode == 0 else ''


def stack_set_remote(name, end, url, by='用户（网页）'):
    """「全栈 → 各端 → 推送到」：给 git 端设 origin（知识库执行 git remote add / set-url，不经过 AI）。验收时知识库推到这里。"""
    end = end_name(end)
    d = period_dir(name)
    m = load_meta(d)
    refresh_vcs(d, m)
    m = load_meta(d)
    v = (m.get('各端') or {}).get(end)
    if not v:
        raise KBError(f'这一期还没有加 {end}')
    if v.get('版本库') not in ('git', '', None):
        raise KBError(f'{end} 不是 git 仓库（{v.get("版本库")}），没有远端可设；本地目录要推送先在各端里选它，按提示初始化成 git 仓库')
    url = (url or '').strip()
    if not url or re.search(r'\s', url):
        raise KBError('填远端仓库地址，如 https://github.com/you/app.git 或 git@github.com:you/app.git')
    old = end_remote(v['仓库'])
    p = hosts.git(v['仓库'], 'remote', 'set-url' if old else 'add', 'origin', url, timeout=15)
    if p.returncode:
        raise KBError(f'设置远端失败：{(p.stderr or p.stdout).strip()[-300:]}')
    log(d, '各端', f'{end} 推送到 {url}' + (f'（原来是 {old}）' if old else '') + f'；验收时知识库推送（{by}）')
    return f'{end} 的远端设为 {url}，验收时知识库推送到这里'


def stack_set_flow(name, end, flow='', by='用户（网页）'):
    """这一期某个端用哪套开发流程（全栈 → 各端）；空 = 自动（这一期统一指定的 > 工程默认 > 识别文件 > 全局默认）。
    全栈各端开发、单端「开始开发」、开发包都按它（见 flows.flow_for）。"""
    end = end_name(end)
    d = period_dir(name)
    m = load_meta(d)
    ends = period_ends(d, m)
    if end not in ends:
        raise KBError(f'这一期还没有加 {end}：先在「全栈 → 各端」给它选仓库')
    f = get_flow(flow) if (flow or '').strip() else None
    if (ends[end].get('流程') or '') == (f['id'] if f else ''):
        return '没有变化'
    if f:
        ends[end]['流程'] = f['id']
    else:
        ends[end].pop('流程', None)
    for v in ends.values():
        v.pop('来自', None)
    m['各端'] = ends
    save_meta(d, m)
    log(d, '开发流程', f'{end}：{f["名称"] if f else "自动"}（{by}）')
    return f'{end} 的开发流程改为「{f["名称"] if f else "自动"}」'


# ---------------------------------------------------------------- 工程设置：端、默认流程（🗂 基础信息 → 工程设置）

def repo_settings():
    """可选的工程，各带认出的端、手动指定的端和默认流程。"""
    out = []
    for r in stack_repos():
        if r.get('远程仓库'):          # 基础信息里的远程仓库条目：设置在基础信息里改，这里不列
            continue
        p = repo_pref(r['路径'])
        out.append({'路径': r['路径'], '名称': r['名称'], '分支': r.get('分支', ''), '远程': bool(r.get('远程')),
                    '识别端': [] if r.get('远程') else _detect_ends_files(r['路径']),
                    '端': p.get('端', ''), '默认流程': p.get('默认流程', ''), '代码更新': p.get('代码更新', ''),
                    '运行模块': p.get('运行模块', ''), '运行变体': p.get('运行变体', ''), '运行参数': p.get('运行参数', '')})
    names = {}
    for e in _pool.remote_entries():
        names.setdefault(e['克隆地址'], []).append(e['名称'])
    for nm, r in _pool.pool_repos().items():     # 远程仓库：各份副本共用一套（按第 1 份记）；运行模块 / 变体在基础信息条目里填
        p = repo_pref(r['路径'])
        out.append({'路径': r['路径'], '名称': '🌐 ' + ('、'.join(names.get(r['克隆地址']) or []) or nm), '分支': '', '远程': False, '远程仓库': nm,
                    '识别端': _detect_ends_files(r['路径']) if Path(r['路径']).is_dir() else [],
                    '端': p.get('端', ''), '默认流程': p.get('默认流程', ''), '代码更新': p.get('代码更新', ''),
                    '运行模块': '', '运行变体': '', '运行参数': p.get('运行参数', '')})
    return out


def repo_set(path, end=None, flow=None, by='用户（网页）', code=None, run=None):
    """改某个工程的端 / 默认流程 / 代码更新 / 运行到手机（run={模块, 变体, 参数}）；None = 不改，'' = 回到自动
    （端按文件识别，流程按识别文件 / 全局默认，代码更新跟随全局「时机」，运行模块自动识别、变体 debug）。"""
    path = (path or '').strip()
    if not path:
        raise KBError('没有工程路径')
    kv, said = {}, []
    if end is not None:
        end = end_name(end) if (end or '').strip() else ''
        kv['端'] = end
        said.append(f'端：{end or "自动识别"}')
    if flow is not None:
        f = get_flow(flow) if flow else None
        kv['默认流程'] = f['id'] if f else ''
        said.append(f'默认流程：{f["名称"] if f else "自动"}')
    if code is not None:
        if code and code not in (timing.PULL, timing.FETCH, timing.KEEP):
            raise KBError(f'代码更新只能选 {timing.PULL} / {timing.FETCH} / {timing.KEEP}，或留空跟随全局')
        kv['代码更新'] = code
        said.append(f'代码更新：{code or "跟随全局"}' + ('（对所有可选的时机生效，锁定的时机不受影响）' if code else ''))
    for k, name in (('模块', '运行模块'), ('变体', '运行变体'), ('参数', '运行参数')):
        if run is None or run.get(k) is None:
            continue
        v = str(run[k]).strip()
        if k == '模块' and v and not v.startswith(':'):
            v = ':' + v
        if k == '变体' and v and not re.fullmatch(r'[A-Za-z0-9_]+', v):
            raise KBError(f'变体只能是字母数字，如 debug、prodDebug：{v}')
        kv[name] = '' if (k == '变体' and v == 'debug') else v
        said.append(f'{name}：{v or ("自动识别" if k == "模块" else "debug" if k == "变体" else "无")}')
    if not kv:
        return '没有变化'
    save_repo_pref(path, **kv)
    return f'{hosts.label(path) if hosts.is_remote(path) else Path(path).name}　' + '，'.join(said) + f'（{by}）'


# ---------------------------------------------------------------- 方案（影响分析 + 接口契约）

def _sdir(d):
    return d / STACK_DIR


def _last_approved(d):
    vs = sorted(_sdir(d).glob('方案.v*.json'), key=lambda p: int(p.stem.split('.v')[-1]) if p.stem.split('.v')[-1].isdigit() else 0)
    return vs[-1] if vs else None


def _incr_rule(d):
    p = _last_approved(d)
    if not p:
        return ''
    return (f'这一期已经拍板过方案（{p}），现在是【增量修改】：在它的基础上改，已有需求的编号不要变，新需求接着往后编号；'
            '改了或新增的需求、各端改动、接口，在描述后面注明（变更 CR-xxx）。原来不涉及、现在因为变更必须做的端，把「涉及」改成 true 并写清原因；'
            f'接口契约在已锁定版本（{_sdir(d) / "api"} 下最新的 openapi.vN.yaml）上改，没变就原样交回。'
            '在 plan 里加 "变更": [{"编号": "CR-001", "处理": "纳入 / 不需要改 / 建议驳回", "说明": "影响了哪些端、哪些需求"}]。')


_NEW_RULE = """
【新产品模式】这一期是全新产品，还没有（或只有部分）代码仓库。没有仓库的端，你来做技术选型：
- 先读需求（需求/PRD.md 如果有，以它为准），判断要哪些端（App / Web / 小程序 / 服务端…），能少就少：一个人做，MVP 优先。
- 每个要做的端在「各端」里加一项、涉及 true，并多写三个字段：
  "技术栈": "语言 / 框架 / 主要库 / 数据库 / 部署方式，一行写清，选成熟、文档多、一个人好维护的",
  "目录": "顶层目录结构（多行文本，像 tree 那样，每个目录后面一句用途）",
  "脚手架": "怎么初始化（官方脚手架命令 + 非交互参数），之后知识库会照这个在新目录里建工程"
  「改动」写这一端要做的模块 / 页面（REQ-xxx），基于需求，不用找现有代码。
- 接口契约照常写 OpenAPI 3（服务端怎么鉴权、返回包装、错误码、分页由你定，写在契约里），客户端和服务端都按它开发。
- plan 里加 "工程名": "英文短名（小写、连字符），新建工程的目录名用它，如 todo"。
- 技术选型里拿不准、要用户定的（如要不要做 App、用不用云服务）放进待确认，并给出建议。
"""


def _plan_prompt(d, ends, note, new=False):
    names = '/'.join(sort_ends(ends)) or '/'.join(ENDS[:5])
    common = '、'.join(ENDS)
    others = '、'.join(e for e in ENDS if e not in ends) or '其他平台'
    rows = '\n'.join(f'- {e}：{v["仓库"]}' + (f'（分支 {v["分支"]}）' if v.get('分支') else '')
                     + ('　← 在远程机器上：用 kb_repo_read（repo 填这个地址）列目录、读文件、搜索' if hosts.is_remote(v['仓库']) else '')
                     for e, v in ends.items())
    old = _sdir(d) / '方案.json'
    return f"""你是由知识库网页启动的【后台无人值守、只读】全栈方案会话。终端里没有人，不要提问，不要改任何文件、不要切分支、不要提交。
用户一个人做全栈，所有端都由 AI 开发、用户拍板。现在要出「需求规格 + 设计稿对照 + 影响分析 + 接口契约」，给用户做第一次拍板。

这一期：「{d.name}」。用 kb_period（name=「{d.name}」）看需求、设计图、接口文档等资料，必要时 kb_read 读全文；kb_search 可搜资料和代码。
各端仓库（只读，用来判断现有接口、数据结构、模块位置）：
{rows or '（还没有任何仓库：这一期从零开始）'}
{_NEW_RULE if new else ''}{f'用户的意见 / 这次要处理的变更：{note}' if note else ''}
{_incr_rule(d)}

要做的事（一次做完；方案明确时知识库会自动拍板、接着开工，所以能自己定的就定下来）：
1. 需求规格化：把资料里的需求拆成一条条可验收的需求，编号 REQ-001 起；每条写清验收标准（「当…时，应…」，可测、不含糊），标出涉及哪些端。
   资料里没写、但做的时候必须定的（空态、加载、报错、无网、权限、上限、时区、多语言…）也补成需求或放进待确认。
2. 设计稿对照：看这一期「设计图」里的图（kb_period 里有路径，图片直接读；PSD 已转成同名 .png 预览；蓝湖只有链接的看不到，记进「无法核对」）。
   如果你能用 Figma 的 MCP 工具，也可以直接看资料里的 Figma 链接。逐条需求找对应的画板，列出：需求有、设计没画的；设计画了、需求没写的；
   页面缺的状态（空态 / 加载 / 报错 / 无网 / 权限等）；各端设计不一致的地方。没有设计稿就在「无法核对」里写明。
3. 影响分析：判断每个端是否涉及、为什么、要改什么（落到模块 / 页面 / 类，基于读到的代码，不要泛泛而谈）。上面没列出仓库的端也要判断是否需要
   （需要的话在「各端」里加一项、涉及 true，并在待确认里提出来；端名用上面的名字，常用的还有 {common}，别的平台直接写名字，如 Unity）。
4. 接口契约：需要新增或修改服务端接口时，先在服务端仓库里找现有接口的写法（路由、鉴权、返回包装、错误码、分页），按同样的约定写 OpenAPI 3 YAML。
   客户端已有接口复用的也列出来（标「复用」）。不需要接口就不写 openapi。接口的说明里写上对应的需求编号。
5. 待确认：只放真拿不准的点（需求互相矛盾、设计和需求对不上又看不出该按哪个、要不要多做一个端 / 用不用付费服务这类要用户定的取舍），每条给出你的建议。
   能按资料、现有代码约定或常识定下来的（空态文案、分页大小、错误提示、字段命名…）直接定在方案里，并把按什么假设定的写进「假设」，不要放进待确认。
   方案没有待确认、也不扩大范围（"扩大范围": false：只做资料要求的事）时，知识库会自动拍板并接着各端开工，网页上显示「AI 已自动定」，用户可一键撤回；
   有待确认或扩大范围时停下等用户拍板（用户开了免打扰时一律按建议走）。

最后调用一次 kb_stack_plan 交回（只交一次，交完就结束）：
- iteration=「{d.name}」
- plan：JSON 字符串，格式：
  {{"摘要": "一两句话：做什么、涉及哪几端",
   "需求": [{{"编号": "REQ-001", "标题": "…", "描述": "…", "验收": ["当…时，应…", …], "端": ["Android", "服务端"], "设计": ["对应的画板 / 图片文件名"], "来源": "资料里的出处（文件名或链接）"}}],
   "设计对照": {{"缺设计": ["REQ-003：…"], "设计多出": ["画板 xx 里的 …，需求没写"], "缺状态": [{{"页面": "…", "缺": "空态 / 加载 / 报错 / 无网 / 权限…"}}],
              "端不一致": ["…"], "无法核对": "没有设计稿 / 蓝湖只有链接 等（都核对了就留空）"}},
   "各端": [{{"端": "{names}（这一期加了的端；没加仓库但需要的端也可以写，如 {others}）", "涉及": true, "原因": "…", "改动": ["模块或文件：改什么（REQ-001）", …], "工作量": "小/中/大", "风险": "…"{', "技术栈": "…", "目录": "…", "脚手架": "…"（新产品模式：没有仓库的端要写）' if new else ''}}}],
   "接口": [{{"方法": "GET", "路径": "/api/…", "说明": "…（REQ-001）", "类型": "新增/修改/复用", "调用端": ["iOS", "Android"]}}],
   "数据": ["表 / 字段 / 缓存 / 配置的变化"],
   "假设": ["自己定下来的点：按什么依据 / 假设定的"],
   "扩大范围": false,
   "待确认": [{{"问题": "…", "建议": "…"}}]}}
- openapi：OpenAPI 3 YAML 全文（只含新增和修改的接口；没有就留空）。
"""


def spec_md(name, plan):
    """方案里的需求写成 spec.md：各端开发、审查、验收都按它（需求编号贯穿到提交和测试）。"""
    out = [f'# {name} · 需求规格', '', f'> 由知识库全栈方案生成（{plan.get("时间", "")}），拍板后作为各端开发和验收的依据。', '',
           plan.get('摘要', ''), '']
    for r in plan.get('需求') or []:
        out += [f'## {r["编号"]} {r.get("标题", "")}', '', r.get('描述', ''), '',
                f'- 端：{"、".join(r.get("端") or []) or "—"}', f'- 设计：{"、".join(r.get("设计") or []) or "—"}',
                f'- 来源：{r.get("来源") or "—"}', '', '验收标准：', ''] + [f'- [ ] {a}' for a in r.get('验收') or []] + ['']
    dc = plan.get('设计对照') or {}
    if any(dc.get(k) for k in ('缺设计', '设计多出', '缺状态', '端不一致', '无法核对')):
        out += ['## 设计对照', '']
        for k in ('缺设计', '设计多出', '端不一致'):
            out += [f'- {k}：{x}' for x in dc.get(k) or []]
        out += [f'- 缺状态：{x.get("页面", "")} 缺 {x.get("缺", "")}' for x in dc.get('缺状态') or []]
        if dc.get('无法核对'):
            out.append(f'- 无法核对：{dc["无法核对"]}')
        out.append('')
    qs = [q for q in plan.get('待确认') or [] if isinstance(q, dict) and q.get('结论')]
    if qs:
        out += ['## 已确认的问题', '', '> 拍板时定下来的，各端开发、审查都按这里执行。', '']
        out += [f'{i}. {q.get("问题", "")}\n   - 结论：{q["结论"]}' + ('（用户的意见）' if q.get('结论来源') == '用户' else '（按 AI 建议）')
                for i, q in enumerate(qs, 1)]
        out.append('')
    return '\n'.join(out)


def _answer_text(q):
    a = q.get('回答') or {}
    return a.get('内容', '') if a.get('方式') == '自定义' else q.get('建议', '') if a.get('方式') == '按建议' else ''


def stack_answer(name, idx, mode='按建议', text='', by='用户（网页）'):
    """待拍板时回答方案里的一条待确认：mode 按建议 / 自定义（text 是你的意见）/ 空 = 撤回回答。存进 方案.json 这一条的「回答」。"""
    d = period_dir(name)
    m = load_meta(d)
    if (m.get('全栈') or {}).get('状态') != '待拍板':
        raise KBError('现在没有待拍板的方案')
    f = _sdir(d) / '方案.json'
    plan = json.loads(f.read_text('utf-8'))
    qs = plan.get('待确认') or []
    try:
        q = qs[int(idx)]
    except (ValueError, IndexError, TypeError):
        raise KBError(f'没有第 {idx} 条待确认')
    mode, text = (mode or '').strip(), (text or '').strip()
    if mode == '自定义' and not text:
        raise KBError('写一下你的意见')
    if mode not in ('按建议', '自定义', ''):
        raise KBError('回答方式只能是 按建议 / 自定义')
    if mode:
        q['回答'] = {'方式': mode, '内容': text if mode == '自定义' else '', '时间': now(), '人': by}
    else:
        q.pop('回答', None)
    f.write_text(json.dumps(plan, ensure_ascii=False, indent=1), 'utf-8')
    n = sum(1 for x in qs if x.get('回答'))
    log(d, '全栈', f'待确认 {int(idx) + 1}「{q.get("问题", "")[:40]}」：' + (f'{mode}' + (f'：{text}' if text else '') if mode else '撤回回答') + f'（{by}）')
    return f'已保存（已确认 {n} / {len(qs)}）'


def _answered_note(d):
    """重出方案时带上：上一版方案待确认里用户已经回答的。"""
    try:
        qs = json.loads((_sdir(d) / '方案.json').read_text('utf-8')).get('待确认') or []
    except (OSError, ValueError):
        return ''
    rows = [f'- {q.get("问题", "")} → {"用户的意见：" + _answer_text(q) if (q.get("回答") or {}).get("方式") == "自定义" else "按你的建议：" + _answer_text(q)}'
            for q in qs if isinstance(q, dict) and q.get('回答')]
    return ('用户对上一版方案「待确认」的回答（照这些改方案，写进对应的需求 / 各端 / 接口；这些问题已经定了，不要再放进待确认）：\n' + '\n'.join(rows)) if rows else ''


def stack_plan_start(name, note='', by='用户（网页）', auto=False):
    """网页「出方案」：起一个只读后台会话，读资料和各端代码，交回影响分析和接口契约。"""
    d = period_dir(name)
    m = load_meta(d)
    s = m.get('全栈会话') or {}
    if s.get('状态') == '运行中' and _alive(s.get('pid', 0)):
        raise KBError('方案正在出，等它结束')
    if not auto:
        from kblib.steward import behavior
        behavior.emit('plan', d.name, by=by)
    ends = period_ends(d, m)
    if not ends and not m.get('新产品'):          # 还没有任何端：当成新产品，让 AI 建议要哪些端和技术栈
        m['新产品'] = True
        save_meta(d, m)
        log(d, '新产品', f'还没有端就出方案：按新产品流程，AI 建议要哪些端、技术栈和目录结构（{by}）')
    for e, v in ends.items():
        hosts.check_dir(v['仓库'])
    ans = _answered_note(d) if (m.get('全栈') or {}).get('状态') == '待拍板' else ''
    if ans:
        note = ((note or '').strip() + '\n' if (note or '').strip() else '') + ans
    crs = [x for x in m.get('全栈变更') or [] if x['状态'] in ('待评估', '评估中')]
    if crs:
        note = ((note or '').strip() + '\n' if (note or '').strip() else '') + '\n'.join(
            f'- {x["编号"]}（{x["来源"]}，{x["时间"]}）{x["标题"]}：{x.get("说明", "")}' + (f'（可能影响：{"、".join(x["影响端"])}）' if x.get('影响端') else '')
            for x in crs)
        for x in crs:
            x['状态'] = '评估中'
        save_meta(d, m)
    dev = m.get('各端开发') or {}
    for e, v in ends.items():             # 分析代码前更新：这一端有会话在改就只 fetch，不动它的工作区
        if v.get('版本库') == '本地':
            continue
        busy = dev.get(e, {}).get('状态') in ('开发中', '审查中') or (not hosts.is_remote(v['仓库']) and _fix._repo_busy(v['仓库']))
        if busy is True:
            busy = f'{e} 正在{dev[e]["状态"]}'
        timing.code_update('plan', v['仓库'], v.get('版本库', ''), branch=v.get('分支', ''), d=d, label=e, busy=busy or '')
    design_previews(d)                    # PSD 等先转好 PNG 预览，AI 才看得到
    eng = role_engine('方案', m)
    _sdir(d).mkdir(exist_ok=True)
    logf = _sdir(d) / f'方案会话-{datetime.now():%Y%m%d-%H%M%S}.jsonl'
    extra = []
    if eng.get('id') == 'claude' or Path(eng.get('命令') or '').name == 'claude':
        extra = ['--disallowedTools', 'Edit', 'Write', 'MultiEdit', 'NotebookEdit']
        for v in ends.values():
            if not hosts.is_remote(v['仓库']):
                extra += ['--add-dir', v['仓库']]
    git0 = team.wc_snapshot([v['仓库'] for v in ends.values()])      # 只读会话：结束时对比
    proc = engine_popen(eng, _plan_prompt(d, ends, (note or '').strip(), new=bool(m.get('新产品'))), f'kb·全栈方案·{d.name}', logf, str(d),
                        env_extra={'KB_SESSION': 'stack:' + d.name}, extra_args=extra, scene='方案', period=m, auto=auto)
    _PROCS[d.name] = proc
    m = load_meta(d)
    m['全栈会话'] = {'状态': '运行中', 'pid': proc.pid, '开始时间': now(), '日志': str(logf), '引擎': eng['名称'], '意见': (note or '').strip(),
                   '场景': '方案', 'git前': git0, '发起': by, '自动发起': bool(auto)}
    st = m.setdefault('全栈', {})
    st['状态'] = '出方案中'
    save_meta(d, m)
    log(d, '全栈', f'出方案：{"、".join(ends)}' + (f'；意见：{note.strip()}' if (note or '').strip() else '') + f'（{by}，{eng["名称"]}）')

    def wait():
        code = proc.wait()
        _PROCS.pop(d.name, None)
        _plan_finish(d.name, code)
    threading.Thread(target=wait, daemon=True).start()
    return f'已交给 {eng["名称"]} 出方案（只读），几分钟后在这里看结果'


def _plan_finish(name, code=None):
    d = period_dir(name)
    m = load_meta(d)
    s = m.get('全栈会话') or {}
    if s.get('状态') != '运行中':
        return
    res = _dev_result(s.get('日志', ''))
    s.update(状态='已结束', 结束时间=now(), 退出码=code, 结果=(res.get('结果') or '')[:1500], 费用=res.get('费用'))
    team.tally(s, s.get('日志', ''), m, d, '方案')
    team.wc_alarm(d, s, '方案会话')
    st = m.setdefault('全栈', {})
    if st.get('状态') == '出方案中':          # 没交方案就结束了
        st['状态'] = '出方案失败'
        for x in m.get('全栈变更') or []:
            if x['状态'] == '评估中':
                x['状态'] = '待评估'
        log(d, '全栈', f'方案会话结束但没有交回方案（退出码 {code}）：{s["结果"][:300]}')
    m['全栈会话'] = s
    save_meta(d, m)
    _notify('知识库 · 全栈方案', f'「{d.name}」' + ('方案出好了，等你拍板' if st.get('状态') == '待拍板' else
                                              'AI 已自动拍板' + ('并开工' if (st.get('自动拍板') or {}).get('开工') else '') + '，可在网页撤回'
                                              if st.get('自动拍板') else '方案没出来，看记录'))


def stack_plan_submit(name, plan, openapi='', by='Claude'):
    """kb_stack_plan：方案会话交回影响分析和接口契约草案。"""
    d = period_dir(name)
    if isinstance(plan, str):
        try:
            plan = json.loads(plan)
        except ValueError as e:
            raise KBError(f'plan 不是合法 JSON：{e}')
    if not isinstance(plan, dict) or not isinstance(plan.get('各端'), list):
        raise KBError('plan 至少要有「各端」列表')
    for x in plan['各端']:
        if not isinstance(x, dict):
            raise KBError('「各端」每一项要是 {"端": …, "涉及": …}')
        x['端'] = end_name(x.get('端'))
    openapi = (openapi or '').strip()
    if openapi and not re.search(r'^\s*(openapi|swagger)\s*:', openapi, re.M):
        raise KBError('openapi 要是 OpenAPI 3 的 YAML（开头 openapi: 3.x）')
    sd = _sdir(d)
    (sd / 'api').mkdir(parents=True, exist_ok=True)
    plan['时间'], plan['提交人'] = now(), by
    reqs = plan.get('需求') or []
    ids = [x.get('编号') for x in reqs]
    if any(not re.fullmatch(r'REQ-\d{3,}', str(i or '')) for i in ids) or len(set(ids)) != len(ids):
        raise KBError('需求编号要是 REQ-001 这样、且不重复')
    try:                                           # 重出方案时，问题原样没变的，保留你之前的回答
        prev = {q.get('问题'): q.get('回答') for q in json.loads((sd / '方案.json').read_text('utf-8')).get('待确认') or []
                if isinstance(q, dict) and q.get('回答')}
    except (OSError, ValueError):
        prev = {}
    for q in plan.get('待确认') or []:
        if isinstance(q, dict) and prev.get(q.get('问题')) and not q.get('回答'):
            q['回答'] = prev[q['问题']]
    (sd / '方案.json').write_text(json.dumps(plan, ensure_ascii=False, indent=1), 'utf-8')
    (sd / 'spec.md').write_text(spec_md(d.name, plan), 'utf-8')
    api = sd / 'api' / 'openapi.yaml'
    if openapi:
        api.write_text(openapi + '\n', 'utf-8')
    elif api.exists():
        api.unlink()
    m = load_meta(d)
    st = m.setdefault('全栈', {})
    st.update(状态='待拍板', 方案时间=now())
    for x in m.get('全栈变更') or []:
        if x['状态'] == '评估中':
            x['状态'] = '待拍板'
    save_meta(d, m)
    ends = [x['端'] for x in plan['各端'] if x.get('涉及')]
    dc = plan.get('设计对照') or {}
    gaps = sum(len(dc.get(k) or []) for k in ('缺设计', '设计多出', '缺状态', '端不一致'))
    log(d, '全栈', f'方案交回：需求 {len(reqs)} 条，设计问题 {gaps} 处，涉及 {"、".join(ends) or "（无）"}，接口 {len(plan.get("接口") or [])} 个，待确认 {len(plan.get("待确认") or [])} 条（{by}）')
    try:
        auto = _auto_approve(d, plan)
    except KBError as e:                             # 自动拍板没成：照旧等用户拍板
        log(d, '全栈', f'自动拍板没成，等你拍板：{e}')
        auto = ''
    if auto:
        return f'已收到方案：涉及 {"、".join(ends)}；{auto}。方案会话到这里结束。'
    return f'已收到方案：涉及 {"、".join(ends)}；等用户在网页上拍板'


def plan_needs_user(m, plan):
    """方案要不要用户拍板：返回理由，空 = 明确，AI 自动拍板。免打扰开着时一律不问（连拿不准的也按建议定）。"""
    if m.get('免打扰'):
        return ''
    if not auto_decide_on():
        return '设置里关了「明确的事自动定」'
    qs = [q for q in plan.get('待确认') or [] if isinstance(q, dict) and not q.get('回答')]
    if qs:
        return f'有 {len(qs)} 条待确认'
    if plan.get('扩大范围') is True or str(plan.get('扩大范围')).strip() in ('是', 'true', 'True'):
        return '方案扩大了范围'
    crs = [x['编号'] for x in m.get('全栈变更') or [] if x['状态'] == '待拍板' and not str(x.get('来源', '')).startswith('用户')]
    if crs:
        return f'有 AI 会话提的变更 {"、".join(crs)}（可能扩大范围）'
    if m.get('新产品') and not period_ends(None, m):
        return '新产品还没建工程（要不要做哪些端、技术栈由你定）'
    return ''


def _auto_approve(d, plan):
    """方案明确（没有待确认、不扩大范围）就自动拍板；第一次拍板、且是你点的「出方案」或已批准开发的，接着各端开工。"""
    m = load_meta(d)
    st = m.get('全栈') or {}
    why = plan_needs_user(m, plan)
    if why:
        st['等拍板理由'] = why
        m['全栈'] = st
        save_meta(d, m)
        log(d, '全栈', f'方案等你拍板：{why}')
        return ''
    reason = '免打扰已开启，按 AI 建议定' if m.get('免打扰') else '没有待确认、不扩大范围、可撤回'
    sess = m.get('全栈会话') or {}
    first = not m.get('各端开发')
    old_ver, old_status = st.get('契约版本', ''), m.get('状态', '')
    out = stack_approve(d.name, by='AI（自动判断）')
    m = load_meta(d)
    st = m.get('全栈') or {}
    pv = st.get('方案版本', '')
    rec = {'时间': now(), '理由': reason, '方案版本': pv, '假设': [str(x) for x in plan.get('假设') or []][:20],
           '旧契约版本': old_ver, '新契约': st.get('契约版本', '') if st.get('契约版本') != old_ver else '', '原状态': old_status}
    st.pop('等拍板理由', None)
    st['自动拍板'] = rec
    m['全栈'] = st
    save_meta(d, m)
    log(d, 'AI决策（自动判断）', f'自动拍板方案 {pv}：{reason}' + (f'；假设：{"；".join(rec["假设"])[:300]}' if rec['假设'] else '') + '（网页可撤回）')
    by = str(sess.get('发起') or '')
    if first and (m.get('状态') in ('已批准开发', '开发中') or not (sess.get('自动发起') or by.startswith(('自动', '管家')))):
        try:
            out += '；' + _stackdev.stack_dev_start(d.name, by='AI（自动拍板后接着开工）')
            m = load_meta(d)
            m['全栈']['自动拍板']['开工'] = True
            save_meta(d, m)
        except KBError as e:
            out += f'；没能自动开工（{e}），等你在网页点「▶ 各端开工」'
    elif first:
        out += '；这次方案是管家自动出的，开工等你点「▶ 各端开工」'
    return 'AI 已自动拍板（' + reason + '）：' + out


def stack_auto_revert(name, by='用户（网页）'):
    """撤回 AI 自动拍板：停掉自动开工的端，方案回到「待拍板」，自动锁的契约版本和方案版本删掉；已写的代码留在仓库里（没推送，按需自己回退）。"""
    d = period_dir(name)
    m = load_meta(d)
    st = m.get('全栈') or {}
    rec = st.get('自动拍板')
    if not rec:
        raise KBError('这一期的方案不是 AI 自动拍板的')
    stopped = []
    if rec.get('开工'):
        for e, x in list((m.get('各端开发') or {}).items()):
            if x.get('状态') in _stackdev.RUNNING or x.get('状态') == '排队中':
                try:
                    _stackdev.stack_dev_stop(d.name, e, by=by)
                    stopped.append(e)
                except KBError:
                    pass
        m = load_meta(d)
        m.pop('各端开发', None)
        if rec.get('原状态') and m.get('状态') != rec['原状态']:
            m['状态'] = rec['原状态']
            m.setdefault('状态历史', []).append({'时间': now(), '状态': rec['原状态'], '备注': '撤回 AI 自动拍板', '操作人': '用户'})
    st = m.get('全栈') or {}
    sd = _sdir(d)
    pv = rec.get('方案版本') or ''
    if pv and (sd / f'方案.{pv}.json').exists():
        (sd / f'方案.{pv}.json').unlink()
    if rec.get('新契约') and (sd / 'api' / f'openapi.{rec["新契约"]}.yaml').exists():
        (sd / 'api' / f'openapi.{rec["新契约"]}.yaml').unlink()
    for x in m.get('全栈变更') or []:
        if x.get('状态') == '已纳入' and x.get('纳入方案') == pv:
            x['状态'] = '待拍板'
            x.pop('纳入方案', None)
            x.pop('纳入时间', None)
    for k in ('自动拍板', '拍板时间', '拍板人', '方案版本', '涉及'):
        st.pop(k, None)
    st.update(状态='待拍板', 契约版本=rec.get('旧契约版本', ''), 撤回自动拍板=now())
    m['全栈'] = st
    save_meta(d, m)
    log(d, '全栈', f'撤回 AI 自动拍板（方案 {pv}）' + (f'，停掉 {"、".join(stopped)}' if stopped else '') + f'：方案回到「待拍板」（{by}）')
    return ('已撤回自动拍板，方案回到「待拍板」：确认问题后点「✓ 拍板」，或写意见「重出方案」'
            + (f'；已停掉 {"、".join(stopped)}（已经写的代码还在仓库里，没推送，需要的话自己回退）' if stopped else ''))


def stack_approve(name, by='用户（网页）'):
    """拍板 1（只能本人在网页上点）：方案定下来，接口契约锁成一个版本。"""
    d = period_dir(name)
    m = load_meta(d)
    st = m.get('全栈') or {}
    if st.get('状态') != '待拍板':
        raise KBError('现在没有待拍板的方案')
    sd = _sdir(d)
    plan = json.loads((sd / '方案.json').read_text('utf-8'))
    qs = [q for q in plan.get('待确认') or [] if isinstance(q, dict)]
    for q in qs:                                   # 拍板 = 每条待确认定下结论：你写了意见的按你的，其余按 AI 建议
        mine = (q.get('回答') or {}).get('方式') == '自定义'
        q['结论'] = (q['回答'].get('内容') if mine else q.get('建议')) or q.get('建议') or ''
        q['结论来源'] = '用户' if mine else 'AI 建议'
    if qs:
        (sd / '方案.json').write_text(json.dumps(plan, ensure_ascii=False, indent=1), 'utf-8')
        (sd / 'spec.md').write_text(spec_md(d.name, plan), 'utf-8')
    prev = _last_approved(d)
    old = json.loads(prev.read_text('utf-8')) if prev else None
    api = sd / 'api' / 'openapi.yaml'
    ver, newver = st.get('契约版本', ''), False
    if api.exists():
        locked = sd / 'api' / f'openapi.{ver}.yaml' if ver else None
        if not (locked and locked.exists() and locked.read_text('utf-8') == api.read_text('utf-8')):   # 契约没变就不升版本
            n = 1 + max([int(x.stem.split('.v')[-1]) for x in (sd / 'api').glob('openapi.v*.yaml') if x.stem.split('.v')[-1].isdigit()] + [0])
            ver, newver = f'v{n}', True
            shutil.copy2(api, sd / 'api' / f'openapi.{ver}.yaml')
    pn = 1 + (int(prev.stem.split('.v')[-1]) if prev and prev.stem.split('.v')[-1].isdigit() else 0)
    shutil.copy2(sd / '方案.json', sd / f'方案.v{pn}.json')
    crs = [x for x in m.get('全栈变更') or [] if x['状态'] == '待拍板']
    for x in crs:
        x.update(状态='已纳入', 纳入方案=f'v{pn}', 纳入时间=now())
    st.pop('自动拍板', None)                       # 重新拍板：旧的「AI 已自动定」不再显示（自动拍板时由 _auto_approve 重新记）
    st.pop('等拍板理由', None)
    st.update(状态='已拍板', 拍板时间=now(), 拍板人=by, 契约版本=ver, 方案版本=f'v{pn}',
              涉及=[x['端'] for x in plan['各端'] if x.get('涉及')])
    m['全栈'] = st
    save_meta(d, m)
    mine = sum(1 for q in qs if q.get('结论来源') == '用户')
    log(d, '全栈', (f'待确认 {len(qs)} 条定下结论：{mine} 条按你的意见，其余按 AI 建议；' if qs else '') + f'拍板方案 v{pn}：涉及 {"、".join(st["涉及"])}' + (f'，接口契约 {ver}{"（新版本）" if newver else ""}' if ver else '，不涉及接口')
        + (f'；纳入变更 {"、".join(x["编号"] for x in crs)}' if crs else '') + f'（{by}）')
    out = '已拍板' + (f'，接口契约{"锁定为" if newver else "仍是"} {ver}' if ver else '')
    if m.get('各端开发') and old:           # 开发中又拍板（变更 / 重出方案）：算出各端要补什么
        out += '；' + _stackdev.plan_changed(d, old, plan, ver if newver else '')
    if any(x['状态'] == '待评估' for x in load_meta(d).get('全栈变更') or []):
        stack_plan_start(d.name, by='自动（还有没评估的变更）')
    return out


# ---------------------------------------------------------------- 变更：做的过程中发现要改需求、要别的端配合、要改契约
def stack_change_add(name, title, detail='', ends=None, by='用户（网页）'):
    """提一个变更（你在网页上，或某一端的开发会话用 kb_stack_change）。已经有方案的，自动起增量方案会话评估，评估完你拍板。"""
    d = period_dir(name)
    title = (title or '').strip()
    if not title:
        raise KBError('变更要写一句是什么')
    ends = [end_name(e) for e in (ends if isinstance(ends, list) else [x for x in re.split(r'[、,，\s]+', ends or '') if x]) if (e or '').strip()]
    m = load_meta(d)
    xs = m.setdefault('全栈变更', [])
    cid = f'CR-{len(xs) + 1:03d}'
    xs.append({'编号': cid, '标题': title, '说明': (detail or '').strip(), '影响端': ends, '来源': by, '时间': now(), '状态': '待评估'})
    save_meta(d, m)
    log(d, '全栈变更', f'{cid} {title}' + (f'（可能影响 {"、".join(ends)}）' if ends else '') + f'（{by}）')
    s = m.get('全栈会话') or {}
    if (m.get('全栈') or {}).get('状态') in ('已拍板', '开发中', '各端完成') and not (s.get('状态') == '运行中' and _alive(s.get('pid', 0))):
        try:
            stack_plan_start(d.name, by=f'自动（变更 {cid}）')
            return f'已记下 {cid}，正在出增量方案，出好后等用户拍板'
        except KBError as e:
            return f'已记下 {cid}；自动评估没起来：{e}'
    return f'已记下 {cid}，下次出方案时一起评估'


def stack_change_reject(name, cid, by='用户（网页）'):
    d = period_dir(name)
    m = load_meta(d)
    x = next((x for x in m.get('全栈变更') or [] if x['编号'] == cid), None)
    if not x:
        raise KBError(f'没有变更 {cid}')
    if x['状态'] in ('已纳入',):
        raise KBError(f'{cid} 已经纳入方案了；要撤回就再提一个变更')
    x.update(状态='已驳回', 驳回时间=now(), 驳回人=by)
    save_meta(d, m)
    log(d, '全栈变更', f'{cid} 驳回（{by}）')
    return f'已驳回 {cid}'


def stack_get(name):
    """网页「全栈」板块。"""
    d = period_dir(name)
    m = load_meta(d)
    s = m.get('全栈会话') or {}
    if s.get('状态') == '运行中' and d.name not in _PROCS and not _alive(s.get('pid', 0)):
        _plan_finish(d.name)
        m = load_meta(d)
        s = m.get('全栈会话') or {}
    sd = _sdir(d)
    plan = None
    spec = sd / 'spec.md'
    if (sd / '方案.json').exists():
        try:
            plan = json.loads((sd / '方案.json').read_text('utf-8'))
        except ValueError:
            plan = None
    api = sd / 'api' / 'openapi.yaml'
    if refresh_vcs(d, m):
        m = load_meta(d)
    ends = period_ends(d, m)
    need = list((m.get('全栈') or {}).get('涉及') or []) + [x.get('端') for x in (plan or {}).get('各端') or [] if isinstance(x, dict) and x.get('涉及')]
    rows = []
    for e in sort_ends(set(ends) | {x for x in need if x}):      # 只列加了的端 + 方案要做但还没加仓库的端
        v = ends.get(e)
        if v:
            remote = hosts.is_remote(v['仓库'])
            local = not remote and Path(v['仓库']).is_dir()
            f = flow_for(m, v['仓库']) if local else None
            auto = flow_for(m, v['仓库'], by_end=False) if local else None
            kind = v.get('版本库') or 'git'
            cur = '' if remote or kind == '本地' else hosts.branch_of(v['仓库'], kind) if Path(v['仓库']).is_dir() else ''
            rows.append({'端': e, '仓库': v['仓库'], '显示': hosts.label(v['仓库']), '远程': remote, '分支': v.get('分支', ''), '当前分支': cur,
                         '版本库': kind, '远端': end_remote(v['仓库']) if kind == 'git' else '',
                         '流程': ('（远程：开工时在那台机器上匹配）' if not v.get('流程') else _flow_name(v['流程']) + '（远程）') if remote
                         else f['名称'] if f else '（没有匹配的流程：按仓库现有约定）',
                         '流程id': v.get('流程', ''), '自动流程': auto['名称'] if auto else '没有匹配的流程：按仓库现有约定', '来自': v.get('来自', '')})
        else:
            rows.append({'端': e, '待添加': True})
    return {'各端': rows, '常用平台': list(ENDS), '流程列表': [{'id': x['id'], '名称': x['名称']} for x in flows()],
            '统一流程': _flow_name(m.get('开发流程')), '状态': (m.get('全栈') or {}).get('状态', ''), '全栈': m.get('全栈') or {},
            '会话': {k: s.get(k) for k in ('状态', '开始时间', '结束时间', '引擎', '结果', '意见', '参与', '警告')} if s else None,
            '方案': plan, '契约': api.read_text('utf-8') if api.exists() else '', '契约路径': str(api) if api.exists() else '',
            '规格路径': str(spec) if spec.exists() else '', '开发': _stackdev.stack_dev_view(d, m),
            '变更': m.get('全栈变更') or [], '证据': _stackdev.evidence(d, m, plan),
            '并行上限': _stackdev.MAX_PAR, '审查轮数': _stackdev.MAX_ROUNDS, **_launch.launch_view(d, m)}
