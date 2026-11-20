# From 大大王.DT
"""插件：挂时机的、按需调用的工具插件、插件入口。"""
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from kblib.core.common import KBError, PERIODS, ROOT, load_settings, now, read_text, safe_name, save_settings
from kblib.core.periods import create_task, is_deleted, is_task, load_meta, log, period_dir, period_names, save_meta, set_status
from kblib.core.notes import add_note
from kblib.core.baseinfo import base_load, base_secret
from kblib.infra import bgjobs, timing
from kblib import lazy
_devprep = lazy('kblib.ai.devprep')      # 上层模块：用到时才 import（不形成循环）
_devsession = lazy('kblib.ai.devsession')      # 上层模块：用到时才 import（不形成循环）
_devices = lazy('kblib.ai.devices')      # 上层模块：用到时才 import（不形成循环）
_bug_index = lazy('kblib.bugs.index')      # 上层模块：用到时才 import（不形成循环）


# 插件不是知识库的一部分：放在知识库根目录「插件/<名称>/」（不进 git），或在设置里添加其他目录。
# 每个插件一个 plugin.json（名称、说明、命令、默认时机、识别文件、超时、配置项、提交说明＝手动「运行一次」后提交用的说明），在网页「⚙ 设置 → 插件」挂载并选时机。
# 运行时 cwd 是插件目录，环境变量带上期名、仓库、分支、文档目录和配置；退出码非 0 算失败（记成非阻断问题，不拦截开发）。
PLUGIN_DIR = ROOT / '插件'
# 能挂插件的时机来自统一的时机清单（infra/timing.py TIMINGS，和代码更新共用）：时机 id → 说明。
# 设置里存新 id；旧配置的中文名（开发开始 / 开发完成）照样认，旧配置里的「开发开始」顺带全栈各端首轮开工。
PLUGIN_EVENTS = {t['id']: f'{t["名称"]}：{t["说明"]}' for t in timing.TIMINGS if t['id'] in timing.PLUGIN_TIMINGS}
PLUGIN_ENV = {'KB_EVENT': '时机名（开发开始 / 开发完成 / 手动运行 / Bug修复前；全栈各端首轮开工也是「开发开始」）',
              'KB_TIMING': '时机 id（dev_start / end_start / bug_fix / done / manual）', 'KB_PERIOD': '期名', 'KB_PERIOD_DIR': '这一期的资料目录',
              'KB_REPO': '代码仓库绝对路径', 'KB_BRANCH': '当前分支', 'KB_DOCS': '开发流程的文档目录（可能为空）',
              'KB_PLUGIN_CONFIG': '设置里填的配置项（JSON）', 'KB_ROOT': '知识库根目录', 'KB_PYTHON': '知识库用的 Python',
              'KB_CLI': '知识库命令行入口 kb.py（用 KB_PYTHON 运行）',
              'KB_PLUGIN_BASE': '「基础信息」类配置项选中的条目（JSON：键 → {字段: 值}，含机密，只在运行时给）'}
PLUGIN_TOOL = '工具'      # plugin.json「类型」：工具 = 不挂时机，由你 / AI 按需调用（kb_plugin_call、kb plugin call）


def _plugin_dirs():
    out = []
    if PLUGIN_DIR.is_dir():
        out += [p for p in sorted(PLUGIN_DIR.iterdir()) if (p / 'plugin.json').is_file()]
    for x in load_settings().get('插件目录', []):
        p = Path(x).expanduser()
        if p not in out:
            out.append(p)
    return out


def plugins():
    """全部插件：plugin.json 的内容 + id、目录、是否外部目录、挂载情况（启用、时机、配置）。"""
    conf = load_settings().get('插件', {})
    out = []
    for p in _plugin_dirs():
        pid = p.name
        base = {'id': pid, '目录': str(p), '外部': p.parent != PLUGIN_DIR}
        try:
            man = json.loads(read_text(p / 'plugin.json'))
            if not isinstance(man.get('命令'), list) or not man['命令']:
                raise ValueError('「命令」要写成数组，如 ["{python}", "main.py"]')
        except (OSError, ValueError) as e:
            out.append(dict(base, 名称=pid, 错误=f'plugin.json 读取失败：{e}', 启用=False, 时机=[], 配置={}))
            continue
        c = conf.get(pid, {})
        tool = man.get('类型') == PLUGIN_TOOL
        bsrc = man.get('类型') == _bug_index.BUG_PLUGIN              # Bug 平台适配器：在「🐞 Bug → 平台」里接入，不挂时机
        dft = {x['键']: (x.get('默认') or (x.get('类型', '').split(':', 1)[1].split('|')[0]
                                            if x.get('类型', '').startswith('select:') else ''))
               for x in man.get('配置项', [])}
        out.append(dict(man, **base, 启用=bool(c.get('启用')) or bsrc, 工具=tool, Bug平台=bsrc,
                        按需=bool(man.get('按需')) and not tool and not bsrc,
                        时机=[] if tool or bsrc else timing.timing_ids(c.get('时机', man.get('默认时机', [])),
                                                                      legacy='时机' not in c or any(e in timing._ALIAS for e in c['时机'])),
                        配置=dict(dft, **c.get('配置', {}))))
    return out


def get_plugin(pid):
    p = next((x for x in plugins() if x['id'] == pid), None)
    if not p:
        raise KBError(f'没有这个插件：{pid}')
    return p


def save_plugin(pid, enabled, events, config=None):
    """网页「⚙ 设置 → 插件」：挂载 / 卸下，选时机，填配置。"""
    p = get_plugin(pid)
    bad = [e for e in events if timing._ALIAS.get(e, e) not in PLUGIN_EVENTS]
    if bad:
        raise KBError(f'这些时机不能挂插件：{"、".join(bad)}；可选 {" / ".join(PLUGIN_EVENTS)}')
    events = timing.timing_ids(events)
    if p.get('工具') or p.get('Bug平台'):
        events = []
    elif enabled and not events:
        raise KBError('挂载插件至少要选一个时机')
    s = load_settings()
    s.setdefault('插件', {})[pid] = {'启用': bool(enabled), '时机': list(events),
                                   '配置': {k: str(v) for k, v in (config or {}).items()
                                            if k in {x['键'] for x in p.get('配置项', [])}}}
    save_settings(s)
    return f'插件「{p["名称"]}」' + ((f'已挂载：{"、".join(timing.timing_get(e)["名称"] for e in events)}' if events else '已挂载（按需调用）') if enabled else '已卸下')


def add_plugin_dir(path):
    p = Path(path).expanduser().resolve()
    if not (p / 'plugin.json').is_file():
        raise KBError(f'{p} 下没有 plugin.json')
    s = load_settings()
    ds = s.setdefault('插件目录', [])
    if p.parent == PLUGIN_DIR or str(p) in ds:
        return f'已经有这个插件：{p.name}'
    if p.name in {x.name for x in _plugin_dirs()}:
        raise KBError(f'已有同名插件「{p.name}」，请改目录名后再添加')
    ds.append(str(p))
    save_settings(s)
    return f'已添加插件目录：{p}（在列表里点「挂载」后才会运行）'


def remove_plugin_dir(pid):
    p = get_plugin(pid)
    if not p['外部']:
        raise KBError(f'「{p["名称"]}」在知识库的插件目录里，删除请直接删文件夹：{p["目录"]}')
    s = load_settings()
    s['插件目录'] = [x for x in s.get('插件目录', []) if Path(x).expanduser() != Path(p['目录'])]
    s.get('插件', {}).pop(pid, None)
    save_settings(s)
    return f'已移除插件「{p["名称"]}」（文件没动：{p["目录"]}）'


def _git_changed(root):
    """仓库里有改动的文件 → 内容摘要（插件运行前后对比，找出插件改了哪些文件）。git 仓库和 svn 工作副本都行。"""
    root = Path(root)
    if (root / '.svn').exists() and not (root / '.git').exists():
        r = subprocess.run(['svn', '--non-interactive', 'status', '--depth', 'infinity'], cwd=root, capture_output=True, text=True, timeout=120)
        names = []
        for ln in r.stdout.splitlines() if r.returncode == 0 else []:
            if len(ln) <= 8 or not (ln[0] in '?AMDR!~C' or ln[1] == 'M'):
                continue
            f = ln[8:].strip()
            if ln[0] == '?' and (root / f).is_dir():      # 没纳入版本控制的新目录：svn 只列目录，这里展开成文件
                names += [str(x.relative_to(root)) for x in (root / f).rglob('*') if x.is_file()]
            elif not (root / f).is_dir():
                names.append(f)
    else:
        r = _devprep._git(root, 'status', '--porcelain', '-uall')
        names = [ln[3:].split(' -> ')[-1].strip('"') for ln in r.stdout.splitlines()] if r.returncode == 0 else []
    out = {}
    for f in names:
        try:
            out[f] = hashlib.md5((Path(root) / f).read_bytes()).hexdigest()
        except OSError:
            out[f] = ''
    return out


def _plugin_base(p, by):
    """配置项里「类型: base」的项填的是基础信息编号（如 B019）：运行时把那条的字段（含钥匙串里的机密）交给插件，插件里不用写账号密码。"""
    out = {}
    for c in p.get('配置项', []):
        if c.get('类型') != 'base':
            continue
        bid = (p['配置'].get(c['键']) or '').strip()
        if not bid:
            continue
        e = next((x for x in base_load()['条目'] if x['编号'] == bid), None)
        if not e:
            raise KBError(f'插件「{p.get("名称") or p["id"]}」的「{c.get("标题") or c["键"]}」填的 {bid} 在基础信息里找不到')
        out[c['键']] = {f['名']: base_secret(bid, f['名'], by=by, purpose=f'插件「{p.get("名称") or p["id"]}」') if f.get('机密') else f.get('值', '')
                        for f in e['字段']}
    return out


def _plugin_env(p, by, **kw):
    env = dict(os.environ, KB_ROOT=str(ROOT), KB_PYTHON=sys.executable, KB_CLI=str(Path(__file__).resolve().parents[2] / 'kb.py'),
               KB_PLUGIN_CONFIG=json.dumps(p['配置'], ensure_ascii=False))
    b = _plugin_base(p, by)
    if b:
        env['KB_PLUGIN_BASE'] = json.dumps(b, ensure_ascii=False)
    env.update({k: str(v) for k, v in kw.items()})
    return env


NOTIFY_CAP = '通知'


def notify_plugins(title, text, kind=''):
    """把通知也发到「通知」插件（飞书 / 钉钉 / 企业微信 / Slack 群机器人等）：命令 + notify <标题>，标准输入是正文。
    插件配置「范围」= 管家 时只收管家的日报和提问（kind='管家'），= 全部 时所有通知都收。失败不影响别的。"""
    for p in plugins():
        if NOTIFY_CAP not in (p.get('能力') or []) or not p.get('启用') or p.get('错误'):
            continue
        if (p['配置'].get('范围') or '全部') == '管家' and kind != '管家':
            continue
        try:
            cmd = [sys.executable if x == '{python}' else x for x in p['命令']] + ['notify', title]
            subprocess.run(cmd, cwd=p['目录'], env=_plugin_env(p, '通知', KB_EVENT='通知'), input=text, text=True,
                           capture_output=True, timeout=int(p.get('超时') or 20))
        except Exception as e:
            print('通知插件出错：', p.get('名称'), repr(e), flush=True)


def tool_plugins():
    """能用 kb_plugin_call 调的插件：「类型: 工具」的，加上写了「按需: true」的时机插件（如多语言拉取：挂时机照跑，开发途中也能调一次）。"""
    return [p for p in plugins() if (p.get('工具') or p.get('按需')) and not p.get('错误')]


I18N_CAP = '多语言'


def i18n_plugin(root=''):
    """已挂载、声明「能力: 多语言」、能按需调用的插件；给了仓库时还要求仓库里有它的识别文件（如 i18n_resource）。没有返回 None。"""
    for p in tool_plugins():
        if not p['启用'] or I18N_CAP not in (p.get('能力') or []):
            continue
        if root and any(not (Path(root) / x).exists() for x in p.get('识别文件', [])):
            continue
        return p
    return None


def i18n_hint(period='', root='', repo_arg=''):
    """仓库里有 i18n_resource 且挂了多语言拉取插件时，给开发 / 跟进 / Bug 修复会话的一句话：缺 key 先调插件拉，别停下等人在 AS 里拉。
    repo_arg：会话不在本期开发仓库里（如 Bug 修复的 worktree）时，调用要带 仓库=<路径>。没挂 / 仓库不认返回空串。"""
    if not root:
        return ''
    try:
        p = i18n_plugin(root)
    except Exception:          # noqa: BLE001 — 提示词附加项，出错不影响会话启动
        return ''
    if not p:
        return ''
    args = [f'仓库={repo_arg}'] if repo_arg else []
    call = f'kb_plugin_call(plugin="{p["id"]}", args={json.dumps(args, ensure_ascii=False)}' + (f', iteration="{period}")' if period else ')')
    return (f'- 缺多语言 key（代码 / 布局里用到的 i18n_ / 文案 key 在 i18n_resource 里还没有，编译报找不到资源）时，'
            f'不要停下来记「需要在 AS 里拉多语言」：先 {call} 用「{p["名称"]}」插件在当前分支上拉一次'
            '（它会排队等仓库空出来，不切分支、不 push，只把插件改的文件原样单独提交「chore(i18n): 拉取多语言」）；'
            '拉到的文件不手改，拉完再编译。翻译平台上确实还没有这个 key（拉完仍缺）才用 kb_issue 记下（写清缺哪些 key），然后继续')


_I18N_WORDS = ('多语言', 'i18n', 'I18N', 'strings_auto', '翻译平台', '拉取翻译')


def i18n_issue_tip(period, text):
    """kb_issue 记的是「要拉多语言 / 缺 key」这类问题且插件能用时，回给会话的提示：直接调插件，不要停。"""
    if not any(w in (text or '') for w in _I18N_WORDS):
        return ''
    try:
        m = load_meta(period_dir(period))
        p = i18n_plugin(m.get('开发仓库') or '')
    except Exception:          # noqa: BLE001
        return ''
    if not p:
        return ''
    return (f'\n\n【多语言可以自己拉】这一期挂了「{p["名称"]}」插件：现在就调用 kb_plugin_call(plugin="{p["id"]}", iteration="{period}")'
            '（Bug 修复的 worktree 里加 args=["仓库=<worktree 路径>"]）在当前分支上拉一次，它会把改动单独提交 chore(i18n)；'
            '拉到后编译继续开发，不要停下等人在 AS 里拉。拉完 key 仍缺（翻译平台上还没有）才需要用户处理。')


QA_CAP = 'QA用例'


def qa_case_hint(period=''):
    """挂了声明「能力: QA用例」的工具插件（如 case 生成平台）时，给开发会话的一句话：缺 QA 用例就用它生成并拉回。没挂返回空串。"""
    p = next((x for x in tool_plugins() if x['启用'] and QA_CAP in (x.get('能力') or [])), None)
    if not p:
        return ''
    name = period or '<期名>'
    return (f'- SDD 流程提示缺少 QA 用例（QA Case / 黑盒用例 tests/blackbox/blackbox-test.md / QA PRD / agent-test.json）时，'
            f'不要停下等人手工补：用 kb_plugin_call(plugin="{p["id"]}", args=["自动", "{name}"], iteration="{name}") '
            f'在「{p["名称"]}」上给本期生成并拉回（平台上已有这期的需求会直接用，产物进「其他资料/QA用例/」并拷进 SDD 文档目录）；'
            '还在生成就过几分钟再调一次「自动」，提示 PRD 开放问题时按本期资料用「回答」写回，'
            '卡在要人处理的地方（如平台缺 Confluence 凭据）用 kb_issue 记下后继续开发')


QA_ASK = re.compile(r'(case|用例|agent-?test|agent-?json)', re.I)
QA_DO = re.compile(r'(生成|拉回|拉取|补齐|跑一下|跑一遍)')
_QA_RUNNING = set()          # 正在后台生成 QA 用例的期
QA_ROUNDS = 6                # 「自动」一次最多等十几分钟；平台还在生成就再调，最多这么多轮（约 1 小时）


def qa_case_wanted(text):
    """期页面输入框的话是不是在要 QA 用例（如「用 case 生成平台生成本期 case」）。"""
    t = text or ''
    return bool(QA_ASK.search(t) and QA_DO.search(t))


def qa_case_start(d, text='', by='用户（网页）'):
    """不等开始开发，直接在后台用挂了「能力: QA用例」的插件给这一期跑「自动」（建需求 → 推进 → 等 → 拉回）。
    话里带「本期资料」/ 资料=… 时透传 资料= 参数。没挂这类插件返回空串（调用方按原来的方式处理）。"""
    p = next((x for x in tool_plugins() if x['启用'] and QA_CAP in (x.get('能力') or [])), None)
    if not p:
        return ''
    if d.name in _QA_RUNNING:
        return f'「{p["名称"]}」正在给这一期生成 QA 用例，进展看「最近动作」'
    extra = []
    m = re.search(r'资料\s*[=＝:：]\s*(\S+)', text or '')
    if m:
        extra.append(f'资料={m.group(1)}')
    elif re.search(r'本期(现有|已有)?资料', text or ''):
        extra.append('资料=本期')
    _QA_RUNNING.add(d.name)
    log(d, '插件', f'「{p["名称"]}」开始给本期生成 QA 用例（后台，{" ".join(["自动", d.name] + extra)}；{by}）')
    args = ['自动', d.name] + extra

    def go():
        # 登记进后台任务表：开发面板「会话 / AI 动作」能看到、能停；删期时一并停掉
        jid = bgjobs.job_start(d.name, by, f'插件「{p["名称"]}」生成 QA 用例', ' '.join(args))
        status, res = '已结束', ''
        try:
            out = ''
            for i in range(QA_ROUNDS):
                if bgjobs.job_cancelled(jid) or not _period_alive(d):
                    status, res = '已停止', '已停止' if bgjobs.job_cancelled(jid) else '这一期已删除'
                    return
                bgjobs.job_progress(jid, f'第 {i + 1}/{QA_ROUNDS} 轮：在平台上建需求 / 推进 / 等待生成')
                try:
                    out = call_plugin(p['id'], args, period=d.name, by=by)
                except KBError as e:
                    status, res = ('已停止', str(e)) if bgjobs.job_cancelled(jid) else ('失败', str(e)[-1500:])
                    if status == '失败':
                        _safe_log(d, '插件', f'「{p["名称"]}」生成 QA 用例没成：{res}')
                    return
                if '平台还在生成' not in out:
                    break
                _safe_log(d, '插件', f'「{p["名称"]}」第 {i + 1} 轮：平台还在生成，接着推进')
            tail = '\n'.join(x for x in out.strip().splitlines()[-12:])
            res = tail[-1500:]
            _safe_log(d, '插件', f'「{p["名称"]}」QA 用例：{res}')
        except Exception as e:  # noqa: BLE001 — 后台线程，什么错都记下给网页看
            status, res = '失败', f'{type(e).__name__}: {e}'
            _safe_log(d, '插件', f'「{p["名称"]}」生成 QA 用例出错：{res}')
        finally:
            _QA_RUNNING.discard(d.name)
            bgjobs.job_end(jid, status, res)
            if status == '已停止':
                _safe_log(d, '插件', f'「{p["名称"]}」生成 QA 用例已停止')
    import threading
    threading.Thread(target=go, daemon=True, name=f'qa-case-{d.name}').start()
    return (f'已在后台用「{p["名称"]}」给本期生成 QA 用例（不用等开始开发；有需求文档链接按链接，没有就按本期资料），'
            '要十几分钟到几十分钟，进展和结果记在「最近动作」，产物拉回「其他资料/QA用例/」')


def call_plugin(pid='', args=(), period='', by='Claude'):
    """按需调用「工具」插件（如按会话编号拉取外部系统的会话）。不带插件名时列出已挂载的工具插件。"""
    ts = tool_plugins()
    if not pid:
        on = [p for p in ts if p['启用']]
        if not on:
            return '没有挂载的工具插件（网页「⚙ 设置 → 插件」挂载）' + (
                f'；未挂载的：{"、".join(p["名称"] for p in ts)}' if ts else '')
        return '可调用的工具插件：\n' + '\n'.join(
            f'- {p["id"]}：{p.get("说明") or ""}' + ('（时机插件，也可按需调用：在仓库当前分支上运行一次）' if p.get('按需') else '')
            + (f'\n  参数：{p["参数"]}' if p.get('参数') else '') for p in on)
    p = next((x for x in ts if pid in (x['id'], x.get('名称'))), None)
    if not p:
        raise KBError(f'没有这个工具插件：{pid}（{"、".join(x["id"] for x in ts) or "还没有工具插件"}）')
    if not p['启用']:
        raise KBError(f'插件「{p["名称"]}」还没挂载：在网页「⚙ 设置 → 插件」点「挂载」并填好配置')
    d = period_dir(period) if period else None
    if p.get('按需'):            # 时机插件按需调用：走「运行一次」的流程（排队、在当前分支运行、只提交插件改的文件），不切分支、不 push
        from kblib.plugins.run import plugin_call_now
        return plugin_call_now(p, d, args, by=by)
    env = _plugin_env(p, by, KB_EVENT='调用', KB_PERIOD=d.name if d else '', KB_PERIOD_DIR=str(d) if d else '')
    cmd = [sys.executable if x == '{python}' else x for x in p['命令']] + [str(a) for a in args]
    # 带期名的调用登记成这一期的后台任务（已在某个任务里，如「自动」生成 QA 用例，就挂到那个任务上）：面板能看到、能停
    own = bool(d) and not bgjobs.job_current()
    jid = bgjobs.job_start(d.name, by, f'调用插件「{p["名称"]}」', ' '.join(map(str, args))) if own else bgjobs.job_current()
    code, out, status = None, '', '失败'
    try:
        if jid and bgjobs.job_cancelled(jid):
            raise KBError(f'插件「{p["名称"]}」已停止')
        try:
            proc = subprocess.Popen(cmd, cwd=p['目录'], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                    start_new_session=True)
        except OSError as e:
            raise KBError(f'插件「{p["名称"]}」启动失败：{e}')
        bgjobs.job_proc(proc.pid, jid)
        if jid and bgjobs.job_cancelled(jid):     # 登记子进程前刚好点了停止：自己结束掉
            bgjobs._killpg(proc.pid, 15)
        try:
            so, se = proc.communicate(timeout=int(p.get('超时') or 300))
        except subprocess.TimeoutExpired:
            bgjobs._killpg(proc.pid, 9)
            proc.communicate()
            raise KBError(f'插件「{p["名称"]}」超时（{p.get("超时") or 300} 秒）')
        finally:
            bgjobs.job_proc(proc.pid, jid, add=False)
        code = proc.returncode
        out = (so + ('\n' + se if se.strip() and code else '')).strip()
        if jid and bgjobs.job_cancelled(jid):
            raise KBError(f'插件「{p["名称"]}」已停止')
        if d:
            _safe_log(d, '插件', f'调用插件「{p["名称"]}」{" ".join(map(str, args))}：{"完成" if code == 0 else "失败"}')
        if code:
            raise KBError(f'插件「{p["名称"]}」失败（退出码 {code}）：{out[-3000:]}')
        status = '已结束'
        return out
    finally:
        if own:
            bgjobs.job_end(jid, status, out.strip().splitlines()[-1][-300:] if out.strip() else '')


def _period_alive(d):
    """期还在（没被删）：删期后后台任务不再往里写，免得把目录又建回来。"""
    return (d / '期信息.json').exists() and not is_deleted(d.name)


def _safe_log(d, action, content):
    if _period_alive(d):
        log(d, action, content)


# ---- 工具插件的网页入口（plugin.json 写了「入口」）：输入 → 候选列表 → 加入事项；事项页实时展示原始内容
# 约定：命令后加 `resolve <输入>` 输出候选 JSON 列表（编号、标题、摘要、来源、提问人、时间、匹配）；
#       `content <编号>` 输出原始内容 JSON（标题、地址、会话[{…, 消息[{角色, 时间, 内容, 工具[{工具, 参数, 结果}]}]}]）。
_PC = {}                 # (插件, 编号) → (时间, 内容)：事项页反复打开时少拉几次


def _plugin_json(pid, args, by):
    out = call_plugin(pid, args, by=by)
    try:
        return json.loads(out.strip().splitlines()[-1] if out.strip() else 'null')
    except ValueError:
        raise KBError(f'插件输出不是 JSON：{out[-500:]}')


def plugin_entries():
    return [{'id': p['id'], '名称': p['入口'].get('名称') or p['名称'], '图标': p['入口'].get('图标') or '🔌',
             '提示': p['入口'].get('提示', ''), '说明': p['入口'].get('说明', '')}
            for p in tool_plugins() if p['启用'] and isinstance(p.get('入口'), dict)]


def plugin_resolve(pid, text, by='用户（网页）'):
    if not text.strip():
        raise KBError('先输入内容')
    items = _plugin_json(pid, ['resolve', text], by) or []
    linked = {}
    for n in period_names():
        for x in load_meta(PERIODS / n).get('外部来源') or []:
            if x.get('插件') == pid:
                linked.setdefault(x.get('编号'), []).append(n)
    for it in items:
        it['事项'] = linked.get(it.get('编号'), [])
    return items


def plugin_content(pid, item, fresh=False, by='用户（网页）'):
    k = (pid, item)
    if not fresh and k in _PC and time.time() - _PC[k][0] < 120:
        return _PC[k][1]
    data = _plugin_json(pid, ['content', item], by)
    _PC[k] = (time.time(), data)
    return data


def plugin_to_task(pid, item, task='', name='', title='', url='', by='用户（网页）'):
    """把插件找到的条目（如一段 Hermes 会话）加入事项：task 空 / ＋新建 = 新建事项（名称默认用条目标题），否则归到已有事项。
    知识库只记来源指针（插件、编号、标题、地址），原始内容每次实时拉取。"""
    p = next((x for x in tool_plugins() if x['id'] == pid), None)
    if not p:
        raise KBError(f'没有这个工具插件：{pid}')
    label = (p.get('入口') or {}).get('名称') or p['名称']
    if not title or not url:
        c = plugin_content(pid, item, by=by)
        title, url = title or c.get('标题') or item, url or c.get('地址') or ''
    out = []
    if not task or task == '＋新建':
        name = safe_name(name or title)
        if (PERIODS / name).exists():
            raise KBError(f'已经有「{name}」，换个名称，或选择归到这个事项')
        out.append(create_task(name, f'{label}：{title}', '', f'{label} {item}', '', by=by).splitlines()[0])
        task = name
    d = period_dir(task)
    m = load_meta(d)
    if not is_task(m):
        raise KBError(f'「{d.name}」不是事项')
    src = m.setdefault('外部来源', [])
    if any(x.get('插件') == pid and x.get('编号') == item for x in src):
        return {'msg': f'「{d.name}」里已经有 {label} {item}', 'name': d.name}
    src.append({'插件': pid, '名称': label, '编号': item, '标题': title, '地址': url, '加入时间': now(), '加入人': by})
    save_meta(d, m)
    add_note(d.name, content=f'{label} {item}：{title}\n原始内容不存知识库，处理时用 kb_plugin_call {pid} {item} 实时拉取。',
             category='其他资料', kind='链接', title=f'{label}：{title}', url=url, source=label)
    log(d, '加入来源', f'{label} {item}「{title}」（{by}）')
    out.append(f'已把 {label}「{title}」加入事项「{d.name}」')
    return {'msg': '；'.join(out), 'name': d.name}


def _sources_prompt(m):
    src = m.get('外部来源') or []
    if not src:
        return ''
    return ('\n\n【本事项的原始内容在外部系统，不在知识库里】先逐个用 kb_plugin_call 实时拉取，以拉到的内容为准'
            '（工具结果太多时，输出里有完整内容的临时文件路径，用 Read / grep 看）：\n'
            + '\n'.join(f'- kb_plugin_call(plugin="{x["插件"]}", args=["{x["编号"]}"])　{x.get("名称", "")}「{x.get("标题", "")}」{x.get("地址", "")}'
                         for x in src)
            + '\n拉到的内容里别人已经做过的分析可以参考，但结论要在本地代码里核实（代码位置、提交、分支），不要照搬；不要把整段原文存进知识库。')


def task_push(period, text='', by='用户（网页）'):
    """事项页「分析推动」：你本人点按钮 = 批准处理。会话在跑就把输入发给它；没在跑就带着输入启动（已完成的先重新打开）。"""
    d = period_dir(period)
    m = load_meta(d)
    text = (text or '').strip()
    s = _devsession.dev_session(d.name)
    if s and s.get('状态') == '运行中':
        if not text:
            raise KBError('后台会话正在处理；要补充什么写在输入框里再点')
        return _devsession.dev_chat(d.name, text)
    log(d, '分析推动', (text or '（没有补充说明）') + f'（{by}）')
    if m.get('状态') == '已完成':
        set_status(d.name, '已暂停', '网页「分析推动」重新打开', by_human=True)
    return _devsession.dev_start(d.name, note=text or '网页「分析推动」')


def run_plugins(event, d, root='', branch='', docs='', only=''):
    """在某个时机运行挂载的插件（only：手动运行指定插件，不看挂载和时机）。返回给人 / AI 看的结果，没有插件运行时为空。
    event：时机 id（dev_start …）或旧中文名；插件拿到的 KB_EVENT 仍是旧名（开发开始 / 开发完成 / 手动运行），KB_TIMING 是 id。"""
    t = timing.timing_get(event)
    tid, event = t['id'], t.get('事件') or t['名称']
    res = []
    for p in plugins():
        if only and p['id'] != only:
            continue
        if not only and not (p['启用'] and tid in p['时机']) or p.get('工具') or p.get('Bug平台'):
            continue
        name = p.get('名称') or p['id']
        if p.get('错误'):
            res.append(f'插件「{name}」没有运行：{p["错误"]}')
            continue
        if not root or not Path(root).is_dir():
            res.append(f'插件「{name}」没有运行：{"这一期" if d else ""}还没有确定代码仓库')
            continue
        root = Path(root)
        miss = [x for x in p.get('识别文件', []) if not (root / x).exists()]
        if miss:
            if only:
                res.append(f'插件「{name}」没有运行：仓库 {root.name} 里没有 {"、".join(miss)}')
            continue
        before = _git_changed(root)
        try:
            env = _plugin_env(p, f'插件 {event}', KB_EVENT=event, KB_TIMING=tid, KB_PERIOD=d.name if d else '', KB_PERIOD_DIR=str(d or ''), KB_REPO=str(root),
                              KB_BRANCH=branch or '', KB_DOCS=str(docs or ''))
        except KBError as e:
            res.append(f'插件「{name}」没有运行：{e}')
            continue
        cmd = [sys.executable if x == '{python}' else x for x in p['命令']]
        t0 = time.time()
        try:
            r = subprocess.run(cmd, cwd=p['目录'], env=env, capture_output=True, text=True,
                               timeout=int(p.get('超时') or 300))
            ok, out = r.returncode == 0, (r.stdout + ('\n' + r.stderr if r.stderr.strip() else '')).strip()
        except subprocess.TimeoutExpired:
            ok, out = False, f'超时（{p.get("超时") or 300} 秒）'
        except OSError as e:
            ok, out = False, f'启动失败：{e}'
        after = _git_changed(root)
        changed = sorted(f for f in set(before) | set(after) if before.get(f) != after.get(f))
        head = (f'插件「{name}」（{t["名称"]}）{"完成" if ok else "失败"}，用时 {time.time() - t0:.0f} 秒，'
                + (f'仓库 {root.name} 有 {len(changed)} 个文件改动' if changed else '没有文件改动'))
        if d:
            log(d, '插件', f'{head}：{out[-1500:]}')
        if not ok and d:
            _devices._add_issue(d, f'插件「{name}」运行失败（{event}）', out[-2000:], kind='工具链', source='插件',
                       key=f'插件:{p["id"]}:{event}')
        res.append(head + (f'\n{out[-3000:]}' if out else ''))
    return '\n\n'.join(res)
