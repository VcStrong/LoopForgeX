"""全栈 · 新产品从 0 到发布：只有一句想法、还没有代码的期。

  期信息「新产品」: true 打开这套流程（全栈板块「🌱 这是新产品」，或还没有端时点「出方案」自动打开）。老期不受影响。
  1. 完善需求：后台会话（不碰代码）读这一期资料，用 kb_ask 一条条追问目标用户、核心场景、MVP 范围、不做什么，
     按 /pm 的 PRD 骨架写 需求/PRD.md（kb_write_doc）。有 PRD.md 或点了「跳过」就算完成。
  2. 出方案：plan.py 的「新产品模式」提示词：AI 建议要哪些端、各端技术栈、目录结构、接口契约，你拍板。
  3. 建工程：拍板后方案要做但没仓库的端点「🛠 新建工程」：选父目录 + 目录名 → 建目录、git init、首次提交、登记仓库、填进这一端，
     再起一个后台会话只在这个目录里按方案生成脚手架并提交。期信息「建工程」{端: {状态, 目录, 起点, pid, 日志, …}}。
     选到空目录 / 非仓库目录时网页问「初始化为 git 仓库？」→ stack_git_init。
  4. 开发、审查、验收照 dev.py。
  5. 发布（只对新产品期）：验收后「发布」{状态: 待出清单 / 出清单中 / 待勾选 / 已上线, 清单: […]}；
     后台只读会话按端列发布清单（缺的账号、证书用 kb_issue 记），kb_stack_release 交回；网页逐条勾，勾完这一期才算上线（标已完成）。
后台会话：期信息「新产品会话」{需求 / 发布: {状态, pid, 日志, 开始时间, 引擎}}。
"""
import json
import re
import threading
from datetime import datetime
from pathlib import Path

from kblib.core.common import KBError, ROOT, now
from kblib.core.periods import load_meta, log, period_dir, save_meta, set_status
from kblib.core.repos import add_repo
from kblib.infra import hosts
from kblib.ai.devsession import _NO_BG, _alive, _dev_env, _dev_result, _notify
from kblib.ai.engines import engine_popen, role_engine
from kblib.ai import team
from kblib.stack.plan import _sdir, end_name, period_ends, sort_ends, stack_set_end

PRD_FILE = 'PRD.md'
SESS = '新产品会话'
_SLUG = {'服务端': 'server', 'Web': 'web', 'iOS': 'ios', 'Android': 'android', '鸿蒙': 'harmony', '小程序': 'miniapp', '桌面端': 'desktop'}
_PROCS = {}


def is_new_product(m):
    return bool(m.get('新产品'))


def prd_file(d):
    return d / '需求' / PRD_FILE


def prd_done(d, m):
    return prd_file(d).exists() or bool(m.get('需求已跳过'))


def _ro(eng):
    return ['--disallowedTools', 'Edit', 'Write', 'MultiEdit', 'NotebookEdit'] \
        if eng.get('id') == 'claude' or Path(eng.get('命令') or '').name == 'claude' else []


def _plan(d):
    try:
        return json.loads((_sdir(d) / '方案.json').read_text('utf-8'))
    except (OSError, ValueError):
        return None


def stack_period(m):
    """全栈期：走「各端 / 方案」这套的期（不是事项）。这种期不用单端的「开始开发」，开发进度在「全栈」里看。"""
    return (m or {}).get('类型') != '事项' and bool((m or {}).get('全栈') or (m or {}).get('各端'))


def stack_brief(name):
    """「开发」页签和会话栏用：是不是全栈期、这一期在跑的 AI 会话、各端仓库一览。"""
    d = period_dir(name)
    m = load_meta(d)
    if not stack_period(m):
        return {'全栈期': False}
    run = []
    s = m.get('全栈会话') or {}
    if s.get('状态') == '运行中' and _alive(s.get('pid', 0)):
        run.append('出方案')
    run += [lb for k, lb in (('需求', '完善需求'), ('发布', '出发布清单')) if _running(m, k)]
    run += [f'{e} 建工程' for e, x in (m.get('建工程') or {}).items() if x.get('状态') == '生成脚手架中' and _alive(x.get('pid', 0))]
    for e, x in (m.get('各端开发') or {}).items():
        if x.get('状态') in ('开发中', '审查中') and _alive(x.get('pid', 0)):
            run.append(f'{e} {x["状态"]}')
        elif x.get('状态') == '排队中':
            run.append(f'{e} 排队中')
    ends = period_ends(d, m)
    return {'全栈期': True, '在跑': run, '全栈状态': (m.get('全栈') or {}).get('状态', ''),
            '各端一览': [{'端': e, '仓库': ends[e]['仓库'], '分支': ends[e].get('分支', '')} for e in sort_ends(ends)]}


def plan_questions(d):
    """当前方案的待确认（步骤条用）。"""
    return [q for q in (_plan(d) or {}).get('待确认') or [] if isinstance(q, dict)]


def stack_new_product(name, on=True, by='用户（网页）'):
    """打开 / 关掉「新产品」流程。打开时这一期不能已经有端（有代码的期照老流程走）。"""
    d = period_dir(name)
    m = load_meta(d)
    if bool(on) == is_new_product(m):
        return '没有变化'
    if on and period_ends(d, m):
        raise KBError('这一期已经有端和仓库了，照现有流程走就行；新产品流程是给还没有代码的期用的')
    if on:
        m['新产品'] = True
    else:
        m.pop('新产品', None)
    save_meta(d, m)
    log(d, '新产品', ('打开：从想法开始，完善需求 → 出方案 → 建工程 → 开发 → 验收 → 发布' if on else '关掉新产品流程') + f'（{by}）')
    return '已按新产品流程走：先完善需求，再出方案、建工程' if on else '已关掉新产品流程'


# ---------------------------------------------------------------- 后台会话（完善需求、发布清单）
def _running(m, key):
    s = (m.get(SESS) or {}).get(key) or {}
    return s.get('状态') == '运行中' and _alive(s.get('pid', 0))


def _spawn(d, key, prompt, readonly=True, add_dirs=(), on_done=None):
    m = load_meta(d)
    if _running(m, key):
        raise KBError(f'{key}会话正在跑，等它结束')
    eng = role_engine('方案', m)
    sd = _sdir(d)
    sd.mkdir(exist_ok=True)
    logf = sd / f'{key}会话-{datetime.now():%Y%m%d-%H%M%S}.jsonl'
    extra = _ro(eng) if readonly else []
    if extra:
        for p in add_dirs:
            if p and not hosts.is_remote(p):
                extra += ['--add-dir', p]
    env = dict(_dev_env(d.name, '', str(d)), KB_SESSION=f'launch:{key}:{d.name}')
    scene = {'需求': '需求', '发布': '发布'}.get(key, '')
    # 只读会话：结束时对比 git 状态。没给 add_dirs（完善需求）就看这一期各端的仓库和知识库自己的仓库
    git0 = team.wc_snapshot(list(add_dirs) or [v['仓库'] for v in period_ends(d, m).values()] + [str(ROOT)]) if readonly else {}
    proc = engine_popen(eng, prompt + _NO_BG, f'kb·{key}·{d.name}', logf, str(d), env_extra=env, extra_args=extra, scene=scene, period=m)
    _PROCS[(d.name, key)] = proc
    m = load_meta(d)
    m.setdefault(SESS, {})[key] = {'状态': '运行中', 'pid': proc.pid, '开始时间': now(), '日志': str(logf), '引擎': eng['名称'],
                                   **({'场景': scene} if scene else {}),
                                   **({'git前': git0} if git0 else {})}
    save_meta(d, m)

    def wait():
        code = proc.wait()
        _PROCS.pop((d.name, key), None)
        _sess_finish(d.name, key, code, on_done)
    threading.Thread(target=wait, daemon=True).start()
    return eng


def _sess_finish(name, key, code=None, on_done=None):
    d = period_dir(name)
    m = load_meta(d)
    s = (m.get(SESS) or {}).get(key) or {}
    if s.get('状态') != '运行中':
        return
    res = _dev_result(s.get('日志', ''))
    s.update(状态='已结束', 结束时间=now(), 退出码=code, 结果=(res.get('结果') or '')[:1500], 费用=res.get('费用'))
    team.tally(s, s.get('日志', ''), m, d, s.get('场景', ''))
    team.wc_alarm(d, s, f'{key}会话')
    m[SESS][key] = s
    save_meta(d, m)
    if on_done:
        on_done(d, s)


def _reap(d, m):
    """网页服务重启后，之前起的会话已经没人等：进程没了就收尾。"""
    for key, s in list((m.get(SESS) or {}).items()):
        if s.get('状态') == '运行中' and (d.name, key) not in _PROCS and not _alive(s.get('pid', 0)):
            _sess_finish(d.name, key, None, {'需求': _prd_done, '发布': _release_done}.get(key))
    for e, x in list((m.get('建工程') or {}).items()):
        if x.get('状态') == '生成脚手架中' and (d.name, '建工程:' + e) not in _PROCS and not _alive(x.get('pid', 0)):
            _scaffold_finish(d.name, e, None)


# ---------------------------------------------------------------- 1. 完善需求
def _prd_prompt(d, note):
    return f"""你是由知识库网页启动的【后台无人值守】需求会话。终端里没有人，不要在回复里提问；要问用户就用 kb_ask（iteration=「{d.name}」），再用 kb_wait_answer 等回答。
不要改任何代码或本机文件，只通过知识库工具读写资料。

这一期「{d.name}」是一个全新产品：可能只有一句想法，还没有代码。你的任务：把想法打磨成一份可以出技术方案的 PRD。
1. 用 kb_iteration（name=「{d.name}」）看这一期已有的资料，必要时 kb_read 读全文；kb_search 搜一下有没有相关的历史资料。
2. 资料没说清的，用 kb_ask 一次问一条（问一条、kb_wait_answer 等到回答，再问下一条），每条都给 options 和 recommend：
   目标用户是谁、核心场景（用户在什么情况下用、完成什么事）、MVP 范围（第一版必须有的功能）、明确不做什么、
   要哪些端（App / Web / 小程序 / 服务端…）、账号与数据（要不要登录、数据存哪）、上线目标（时间、平台、是否上架）。
   资料里已经能看出答案的不要再问；问题控制在 8 条以内，次要的直接按你的建议定下来，写进 PRD 的「开放问题」。
   kb_ask 提示免打扰时按你的建议继续，并用 kb_log 记下你替用户定的点。
3. 按下面的骨架写 PRD（对齐 /pm 技能的 11 章，没内容的小节写「无」，不要硬编）：
   # PRD: <产品名>
   ## 1. 文档说明　## 2. 项目概要（2.1 需求背景 2.2 需求目标 2.3 项目范围：目标用户、核心场景、MVP 范围 2.4 名词解释）
   ## 3. 流程图（业务流程、页面流程，用 mermaid）　## 4. 功能列表（表格：编号 / 功能 / 端 / 优先级 P0-P2）
   ## 5. 详细需求（每个页面 / 能力：入口、交互、规则、状态：空态 / 加载 / 报错 / 无网 / 权限）
   ## 6. 服务端与非功能需求（数据、接口要点、性能、安全合规、弱网）　## 7. 多语言　## 8. 数据（埋点、指标）
   ## 9. 验收标准（「当…时，应…」，可测）　## 10. 边界与排除项（10.1 本期明确不做 10.2 已知限制 10.3 外部依赖）
   ## 11. 开放问题
4. 用 kb_write_doc 保存：iteration=「{d.name}」，category=需求，filename={PRD_FILE}，content=PRD 全文，note=一句话简介。
   已经有 {PRD_FILE} 的，在原文基础上改（保留用户确认过的内容），同样用这个文件名保存。
{f'用户的补充 / 意见：{note}' if note else ''}
保存后用一两句话说明 PRD 的要点就结束（出方案由用户在网页上点）。
"""


def _prd_done(d, s):
    ok = prd_file(d).exists()
    log(d, '新产品', '完善需求会话结束：' + (f'PRD 已写好（需求/{PRD_FILE}）' if ok else f'没有写出 PRD（退出码 {s.get("退出码")}）：{(s.get("结果") or "")[:200]}'))
    _notify('知识库 · 完善需求', f'「{d.name}」' + ('PRD 写好了，可以出方案' if ok else 'PRD 没写出来，看记录'))


def stack_prd_start(name, note='', skip=False, by='用户（网页）'):
    """「💬 完善需求」：AI 追问、写 PRD。skip=True：需求已经够清楚，跳过这一步。"""
    d = period_dir(name)
    m = load_meta(d)
    if skip:
        m['需求已跳过'] = True
        save_meta(d, m)
        log(d, '新产品', f'跳过完善需求（{by}）')
        return '已跳过完善需求，可以直接出方案'
    if not is_new_product(m):
        m['新产品'] = True
        save_meta(d, m)
    eng = _spawn(d, '需求', _prd_prompt(d, (note or '').strip()), readonly=True, on_done=_prd_done)
    log(d, '新产品', f'完善需求：AI 追问并写 PRD' + (f'；意见：{note.strip()}' if (note or '').strip() else '') + f'（{by}，{eng["名称"]}）')
    return f'已交给 {eng["名称"]} 完善需求：它会在「开发 → 待确认」里一条条问你，问完写 需求/{PRD_FILE}'


# ---------------------------------------------------------------- 3. 建工程
def _git(path, *a, t=60):
    return hosts.git(str(path), *a, timeout=t)


def _commit(path, msg, allow_empty=False):
    who = [] if _git(path, 'config', 'user.name').stdout.strip() else ['-c', 'user.name=知识库', '-c', 'user.email=kb@localhost']
    _git(path, 'add', '-A')
    r = _git(path, *who, 'commit', '-q', '-m', msg, *(['--allow-empty'] if allow_empty else []))
    if r.returncode:
        raise KBError(f'首次提交失败：{(r.stderr or r.stdout).strip()[-300:]}')
    return _git(path, 'rev-parse', '--short', 'HEAD').stdout.strip()


def stack_git_init(path, by='用户（网页）'):
    """把一个本机目录（空的或已有文件但不是仓库）初始化成 git 仓库并首次提交。已经是仓库的原样返回。"""
    path = (path or '').strip()
    if not path:
        raise KBError('没有目录')
    if hosts.is_remote(path):
        raise KBError('远程目录请到那台机器上 git init')
    p = Path(path).expanduser()
    if not p.is_dir():
        raise KBError(f'目录不存在：{p}')
    if hosts.vcs(str(p)):
        return f'{p} 已经是仓库'
    r = _git(p, 'init', '-q', '-b', 'main')
    if r.returncode:
        raise KBError(f'git init 失败：{(r.stderr or r.stdout).strip()[-300:]}')
    sha = _commit(p, '初始化仓库（知识库）', allow_empty=True)
    return f'已初始化为 git 仓库并首次提交（{sha}）：{p}'


def stack_dir_kind(path):
    """网页选目录后问一句：这个本机目录在不在 git / svn 仓库里（子目录也算）→ {路径, 版本库: git / svn / ''}。"""
    p = Path((path or '').strip()).expanduser()
    if not p.is_dir():
        raise KBError(f'目录不存在：{p}')
    return {'路径': str(p.resolve()), '版本库': hosts.vcs(str(p))}


def default_dir(name, end):
    """新建工程的默认目录名：方案里的「工程名」（英文短名）+ 端，如 todo-server。"""
    d = period_dir(name)
    base = ((_plan(d) or {}).get('工程名') or '').strip() or d.name
    base = re.sub(r'[^\w.-]+', '-', base).strip('-') or 'app'
    return f'{base}-{_SLUG.get(end) or re.sub(r"[^\w.-]+", "-", end).strip("-").lower() or "app"}'


def _end_plan(plan, end):
    return next((x for x in (plan or {}).get('各端') or [] if x.get('端') == end), {}) or {}


def stack_new_project(name, end, parent, dirname='', by='用户（网页）'):
    """「🛠 新建工程」：父目录下建 dirname（dirname 空 = 直接用 parent，要求是空目录），git init、写 README / .gitignore、首次提交、
    登记仓库、填进这一端，再起后台会话按方案生成脚手架。"""
    end = end_name(end)
    d = period_dir(name)
    m = load_meta(d)
    plan = _plan(d)
    if not plan:
        raise KBError('还没有方案：先出方案、拍板，知道这一端用什么技术栈再建工程')
    if (m.get('全栈') or {}).get('状态') not in ('已拍板', '开发中', '各端完成'):
        raise KBError('方案还没拍板：拍板后再建工程（技术栈、目录结构以拍板的方案为准）')
    if end in period_ends(d, m):
        raise KBError(f'{end} 已经有仓库了：要换就先在各端里移除')
    parent = (parent or '').strip()
    if not parent:
        raise KBError('选一个父目录')
    if hosts.is_remote(parent):
        raise KBError('新建工程只支持本机目录；远程机器上先建好仓库再选它')
    pp = Path(parent).expanduser()
    target = pp / dirname.strip() if (dirname or '').strip() else pp
    if (dirname or '').strip() and re.search(r'[/\\]|^\.+$', dirname.strip()):
        raise KBError('目录名不能含 / 或 \\')
    if target.exists():
        if not target.is_dir():
            raise KBError(f'{target} 是文件')
        if any(x for x in target.iterdir() if x.name not in ('.DS_Store',)):
            raise KBError(f'{target} 不是空目录：换个名字，或者直接在各端里选它（会问你要不要初始化成 git 仓库）')
    elif not pp.is_dir():
        raise KBError(f'父目录不存在：{pp}')
    target.mkdir(parents=True, exist_ok=True)
    target = target.resolve()
    x = _end_plan(plan, end)
    (target / 'README.md').write_text(
        f'# {target.name}\n\n{d.name} · {end}\n\n{plan.get("摘要", "")}\n\n'
        + (f'技术栈：{x["技术栈"]}\n\n' if x.get('技术栈') else '') + '由知识库「新建工程」创建，脚手架按拍板的方案生成。\n', 'utf-8')
    (target / '.gitignore').write_text('.DS_Store\n.idea/\n.vscode/\n*.log\n.env\n.env.*\n!.env.example\n', 'utf-8')
    r = _git(target, 'init', '-q', '-b', 'main')
    if r.returncode:
        raise KBError(f'git init 失败：{(r.stderr or r.stdout).strip()[-300:]}')
    sha = _commit(target, f'初始化工程：{d.name} · {end}（知识库）')
    try:
        add_repo(str(target), target.name)
    except KBError:
        pass
    msg = stack_set_end(d.name, end, str(target), 'main', by=by)
    m = load_meta(d)
    m.setdefault('建工程', {})[end] = {'状态': '待生成', '目录': str(target), '起点': sha, '创建时间': now()}
    save_meta(d, m)
    log(d, '建工程', f'{end}：新建 {target}（git init，首次提交 {sha}，已登记仓库）（{by}）')
    try:
        nxt = stack_scaffold_start(d.name, end, by=by)
    except KBError as e:
        m = load_meta(d)
        m['建工程'][end].update(状态='失败', 结果=str(e)[:800])
        save_meta(d, m)
        nxt = f'脚手架会话没起来：{e}（可以在各端那一行点「重试脚手架」）'
    return f'已新建 {target} 并填进 {end}；{nxt}（{msg}）'


def _scaffold_prompt(d, m, end, root):
    plan = _plan(d) or {}
    x = _end_plan(plan, end)
    ver = (m.get('全栈') or {}).get('契约版本')
    api = _sdir(d) / 'api' / f'openapi.{ver}.yaml' if ver else None
    reqs = [r['编号'] + ' ' + r.get('标题', '') for r in plan.get('需求') or [] if end in (r.get('端') or [])]
    return f"""你是由知识库网页启动的【后台无人值守】建工程会话。终端里没有人，不要在回复里提问；必须问用户时用 kb_ask（iteration=「{d.name}」）再 kb_wait_answer。
这一期「{d.name}」是新产品，方案已经拍板。现在只做一件事：在 {root}（刚 git init、只有 README 和 .gitignore）里给「{end}」搭好脚手架。

方案对这一端的要求：
- 技术栈：{x.get('技术栈') or '（方案没写：按下面的改动和需求选最常见、最省事的方案，并在 README 里写明）'}
- 目录结构：
{x.get('目录') or '（方案没写：按技术栈的惯例）'}
- 脚手架怎么建：{x.get('脚手架') or '（方案没写：优先用官方脚手架命令）'}
- 这一端要做的改动：{'；'.join(x.get('改动') or []) or '—'}
- 这一端的需求：{'、'.join(reqs) or '—'}（需求规格：{_sdir(d) / 'spec.md'}）
{f'- 接口契约：{api}（服务端按它建路由骨架，客户端按它建接口层 / mock）' if api and api.exists() else ''}

要求：
1. 只在 {root} 里改，不要动别的目录；不要实现业务功能（那是之后各端开发会话的事），只搭能跑起来的空架子：
   依赖配置、目录结构、入口、一个能跑的首页 / 健康检查接口、基础的 lint / 测试配置、README 里写清怎么安装、运行、测试。
2. 用官方脚手架命令时加非交互参数（如 --yes / --template），装依赖失败（没网、没装工具链）不要卡住：把要用户装的东西写进 README，
   并用 kb_issue（iteration=「{d.name}」）记下缺什么，然后继续把能写的文件写好。
3. 能跑的话跑一次构建或测试确认架子是好的。
4. 完成后 git add -A && git commit -m "脚手架：{end}（{x.get('技术栈') or '按方案'}）"（不要 push）。不要提交 node_modules、build 产物这类文件（补进 .gitignore）。
最后用一两句话说明搭了什么、怎么运行。
"""


def stack_scaffold_start(name, end, by='用户（网页）'):
    """按方案在新工程里生成脚手架（新建工程后自动起；失败了可以在网页重试）。"""
    end = end_name(end)
    d = period_dir(name)
    m = load_meta(d)
    x = (m.get('建工程') or {}).get(end)
    if not x:
        raise KBError(f'{end} 不是知识库新建的工程')
    if x.get('状态') == '生成脚手架中' and _alive(x.get('pid', 0)):
        raise KBError(f'{end} 的脚手架正在生成')
    root = x['目录']
    eng = role_engine('开发', m, end)
    logf = _sdir(d) / f'建工程-{end}-{datetime.now():%Y%m%d-%H%M%S}.jsonl'
    _sdir(d).mkdir(exist_ok=True)
    env = dict(_dev_env(d.name, 'main', root), KB_SESSION=f'scaffold:{end}')
    proc = engine_popen(eng, _scaffold_prompt(d, m, end, root) + _NO_BG, f'kb·建工程·{d.name}·{end}', logf, root, env_extra=env)
    _PROCS[(d.name, '建工程:' + end)] = proc
    m = load_meta(d)
    m['建工程'][end].update(状态='生成脚手架中', pid=proc.pid, 日志=str(logf), 开始时间=now(), 引擎=eng['名称'], 结果='')
    save_meta(d, m)
    log(d, '建工程', f'{end}：按方案生成脚手架（{by}，{eng["名称"]}）')

    def wait():
        code = proc.wait()
        _PROCS.pop((d.name, '建工程:' + end), None)
        _scaffold_finish(d.name, end, code)
    threading.Thread(target=wait, daemon=True).start()
    return f'{eng["名称"]} 正在按方案生成脚手架，好了会通知你'


def _scaffold_finish(name, end, code=None):
    d = period_dir(name)
    m = load_meta(d)
    x = (m.get('建工程') or {}).get(end) or {}
    if x.get('状态') != '生成脚手架中':
        return
    res = _dev_result(x.get('日志', ''))
    root = x['目录']
    try:
        n = int(_git(root, 'rev-list', '--count', f'{x["起点"]}..HEAD').stdout.strip() or 0)
    except (KBError, ValueError):
        n = 0
    x.update(状态='已完成' if n else '失败', 结束时间=now(), 退出码=code, 结果=(res.get('结果') or '')[:800], 提交数=n)
    m['建工程'][end] = x
    save_meta(d, m)
    log(d, '建工程', f'{end}：脚手架' + (f'已生成（{n} 个提交）' if n else f'没有生成出提交（退出码 {code}）：{x["结果"][:200]}'))
    _notify('知识库 · 建工程', f'「{d.name}」{end} ' + ('脚手架好了' if n else '脚手架没生成出来，看记录'))


def scaffold_busy(m, end):
    """各端开工前看一眼：这一端的脚手架还在生成就先不开工。"""
    x = (m.get('建工程') or {}).get(end) or {}
    return x.get('状态') in ('待生成', '生成脚手架中') and (x.get('状态') == '待生成' or _alive(x.get('pid', 0)))


def build_done(d, m):
    """「建工程」这一步完成：拍板方案里涉及的端都有仓库，且没有在生成的脚手架。"""
    plan = _plan(d) or {}
    ends = period_ends(d, m)
    need = [x['端'] for x in plan.get('各端') or [] if x.get('涉及')]
    return bool(need) and all(e in ends for e in need) and not any(scaffold_busy(m, e) for e in need)


# ---------------------------------------------------------------- 5. 发布
def release_mark(d, m):
    """新产品期验收后：进入发布阶段（不直接标已完成）。dev.stack_accept 调用，m 由调用方保存。"""
    m['发布'] = {'状态': '待出清单', '时间': now()}


def _release_prompt(d, m):
    plan = _plan(d) or {}
    ends = period_ends(d, m)
    rows = '\n'.join(f'- {e}：{v["仓库"]}' + (f'（技术栈：{_end_plan(plan, e).get("技术栈")}）' if _end_plan(plan, e).get('技术栈') else '')
                     for e, v in ends.items())
    return f"""你是由知识库网页启动的【后台无人值守、只读】发布清单会话。终端里没有人，不要提问，不要改任何文件、不要提交、不要真的去部署。
这一期「{d.name}」是新产品，各端已经开发完、用户已验收。现在要列出「上线前还要做的事」，让用户逐条勾完。
资料：kb_iteration（name=「{d.name}」）；需求规格 {_sdir(d) / 'spec.md'}；方案 {_sdir(d) / '方案.json'}。
各端仓库（只读，看技术栈、配置、有没有 Dockerfile / CI / 签名配置 / 环境变量）：
{rows}

按端列清单，每项要具体到「做什么、在哪做、做完怎么确认」，按端的类型覆盖这些（用不到的不列）：
- 服务端：生产环境（服务器 / 云函数 / 容器）、数据库与迁移、环境变量和密钥、域名与 DNS、HTTPS 证书、日志与监控、备份。
- Web：构建与部署（静态托管 / CDN）、域名与 HTTPS、接口地址切到生产、备案（国内）。
- App（iOS / Android / 鸿蒙）：正式包名与版本号、签名证书 / 描述文件（iOS 开发者账号、Android keystore）、打包、
  商店上架资料（图标、截图、描述、隐私政策、用户协议、分级、测试账号）、各商店审核。
- 小程序：小程序账号与 AppID、服务器域名白名单、隐私协议、提交审核。
- 通用：隐私政策 / 用户协议页面、生产配置检查、上线后冒烟测试、回滚方案。
要用户提供、而资料和代码里看不到的账号、证书、域名、费用（如「需要 Apple 开发者账号」「需要已备案的域名」），
每一类用 kb_issue（iteration=「{d.name}」, kind 不传, need 写要什么）记一条，清单里对应项的「需要」写上同样的内容。

最后调用一次 kb_stack_release 交回（只交一次，交完就结束）：
- iteration=「{d.name}」
- items：JSON 字符串，列表：[{{"端": "服务端", "项": "一句话：做什么", "说明": "在哪做、怎么做、做完怎么确认", "需要": "要用户提供的账号 / 证书 / 域名，没有留空"}}]
"""


def _release_done(d, s):
    m = load_meta(d)
    r = m.get('发布') or {}
    if r.get('状态') == '出清单中':
        r['状态'] = '待出清单'
        m['发布'] = r
        save_meta(d, m)
        log(d, '发布', f'发布清单会话结束但没交回清单（退出码 {s.get("退出码")}）：{(s.get("结果") or "")[:200]}')
    _notify('知识库 · 发布', f'「{d.name}」' + ('发布清单出好了，逐条做完勾上' if r.get('状态') == '待勾选' else '发布清单没出来，看记录'))


def stack_release_start(name, by='用户（网页）'):
    """「📦 出发布清单」：只读会话按端列发布清单。"""
    d = period_dir(name)
    m = load_meta(d)
    r = m.get('发布') or {}
    if not r:
        raise KBError('验收后才能出发布清单')
    if r.get('状态') == '已上线':
        raise KBError('这一期已经上线了')
    eng = _spawn(d, '发布', _release_prompt(d, m), readonly=True, add_dirs=[v['仓库'] for v in period_ends(d, m).values()],
                 on_done=_release_done)
    m = load_meta(d)
    m['发布']['状态'] = '出清单中'
    save_meta(d, m)
    log(d, '发布', f'出发布清单（{by}，{eng["名称"]}）')
    return f'已交给 {eng["名称"]} 出发布清单（只读），几分钟后在这里看'


def stack_release_submit(name, items, by='Claude'):
    """kb_stack_release：发布清单会话交回清单。重出时已勾过的同名项保留勾选。"""
    d = period_dir(name)
    if isinstance(items, str):
        try:
            items = json.loads(items)
        except ValueError as e:
            raise KBError(f'items 不是合法 JSON：{e}')
    if not isinstance(items, list) or not items:
        raise KBError('items 要是非空列表：[{"端", "项", "说明", "需要"}]')
    m = load_meta(d)
    r = m.get('发布')
    if not r:
        raise KBError('这一期还没验收，不能交发布清单')
    old = {(x.get('端'), x.get('项')): x for x in r.get('清单') or []}
    out = []
    for i, x in enumerate(items, 1):
        if not isinstance(x, dict) or not (x.get('项') or '').strip():
            raise KBError('每一项要是 {"端": …, "项": …}')
        o = old.get((x.get('端', ''), x['项'].strip())) or {}
        out.append({'编号': f'R{i:02d}', '端': (x.get('端') or '通用').strip(), '项': x['项'].strip(), '说明': (x.get('说明') or '').strip(),
                    '需要': (x.get('需要') or '').strip(), '完成': bool(o.get('完成')), **({'完成时间': o['完成时间']} if o.get('完成时间') else {})})
    r.update(状态='待勾选', 清单=out, 清单时间=now(), 提交人=by)
    m['发布'] = r
    save_meta(d, m)
    need = [x for x in out if x['需要']]
    log(d, '发布', f'发布清单交回：{len(out)} 项，要你提供的 {len(need)} 项（{by}）')
    return f'已收到发布清单 {len(out)} 项；用户在网页逐条勾完这一期才算上线'


def stack_release_check(name, rid, done=True, by='用户（网页）'):
    """网页勾 / 取消勾发布清单的一项；全勾完 → 已上线，这一期标为已完成。"""
    d = period_dir(name)
    m = load_meta(d)
    r = m.get('发布') or {}
    x = next((x for x in r.get('清单') or [] if x['编号'] == rid), None)
    if not x:
        raise KBError(f'发布清单里没有 {rid}')
    if r.get('状态') == '已上线' and not done:
        raise KBError('已经上线了，不能再取消勾选')
    x['完成'] = bool(done)
    x['完成时间'] = now() if done else ''
    left = [y for y in r['清单'] if not y['完成']]
    out = f'{rid} ' + ('已完成' if done else '改回未完成')
    if not left and r.get('状态') != '已上线':
        r.update(状态='已上线', 上线时间=now(), 上线人=by)
        out += '；发布清单全部完成，这一期已上线 🎉'
    m['发布'] = r
    save_meta(d, m)
    log(d, '发布', f'{rid} {x["项"]}：{"完成" if done else "取消完成"}（{by}）')
    if r.get('状态') == '已上线' and not left and m.get('状态') != '已完成':
        log(d, '发布', f'发布清单 {len(r["清单"])} 项全部完成，上线（{by}）')
        set_status(d.name, '已完成', '发布清单全部完成，已上线（本人）', by_human=True)
    return out


# ---------------------------------------------------------------- 网页
def launch_view(d, m):
    """stack_get 里新产品相关的部分。"""
    _reap(d, m)
    m = load_meta(d)
    sess = {k: {kk: s.get(kk) for kk in ('状态', '开始时间', '结束时间', '引擎', '结果', '参与', '警告')} for k, s in (m.get(SESS) or {}).items()}
    for k, s in sess.items():
        s['在跑'] = _running(m, k)
    bld = {}
    for e, x in (m.get('建工程') or {}).items():
        bld[e] = {k: x.get(k) for k in ('状态', '目录', '引擎', '结果', '提交数', '开始时间', '结束时间')}
        bld[e]['在跑'] = scaffold_busy(m, e)
    p = prd_file(d)
    return {'免打扰': bool(m.get('免打扰')), '新产品': is_new_product(m), 'PRD': str(p) if p.exists() else '', '需求已跳过': bool(m.get('需求已跳过')),
            '新产品会话': sess, '建工程': bld, '发布': m.get('发布') or None}
