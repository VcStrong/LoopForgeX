"""本期对话：期页面最上方的输入框和「最近动作」。

- 有会话在跑（开发 / 复测 / 跟进，Bug 分析，Bug 修复）：把输入补充给指定会话或全部会话；
- 没有会话在跑：按输入的内容推动解决（后续发现的问题、需求扩展），接着这一期上一次的开发会话（保留上下文）；
- 最近动作：这一期所有会话按时间排的动作，不只是开发。

消息都记在期信息「对话」里（「会话」字段标明发给谁：dev = 这一期的开发类会话，an:<编号> = Bug 分析 / 修复会话）。
开发类会话在调用带期名的知识库工具时收到（devsession._take_chat）；Bug 会话没有期名，按环境变量 KB_SESSION 认领（take_session_msgs）。
"""
import os
import re
import shutil
import threading
from datetime import datetime
from pathlib import Path

from kblib.core.common import SYS, KBError, now, sess_env, uuid_hex
from kblib.core.periods import load_meta, log, period_dir, period_names, save_meta, set_status, unarchive_if
from kblib.core.notes import add_note, note_title, notes_of
from kblib.ai.devices import dev_activity
from kblib.ai.engines import engine_popen, period_engine
from kblib.ai import team
from kblib.ai import supervise as _sup
from kblib.ai.devsession import DEV_DIR, PUSH_RULE, _NO_BG, _alive, _deliver_now, _dev_env, _dev_spawn, _head, _notify, _session_id, _session_push, dev_chat, dev_session
from kblib.bugs.index import zt_load
from kblib.infra import timing
from kblib.bugs.analysis import _an_load, _an_save, an_sessions
from kblib import lazy
_stackdev = lazy('kblib.stack.dev')      # 上层模块：用到时才 import

DEV_KIND = {'复测': '复测会话', '跟进': '跟进会话'}


def _bug_owner(data, bid):
    return ((data['Bug'].get(str(bid)) or {}).get('归属') or {}).get('名称', '')


def _an_name(x):
    bs = x.get('Bug') or []
    return f'{"修复" if x.get("类型") == "修复" else "分析"} Bug {"、".join(bs[:3])}{" 等" if len(bs) > 3 else ""}'


def _period_an(name, data=None):
    """和这一期有关的 Bug 分析 / 修复会话（会话里有归到这一期的 Bug）。"""
    data = data or zt_load()
    return [x for x in an_sessions() if any(_bug_owner(data, b) == name for b in x.get('Bug') or [])]


def period_sessions(name):
    """这一期正在跑的会话：[{键, 名称, 开始, 可打断}]。"""
    d = period_dir(name)
    out = []
    s = dev_session(d.name)
    if s and s.get('状态') == '运行中':
        out.append({'键': 'dev', '名称': DEV_KIND.get(s.get('类型'), '开发会话'), '开始': s.get('本轮开始') or s.get('开始时间', ''),
                    '可打断': True})
    for x in _px_list(d):
        out.append({'键': 'px:' + x['编号'], '名称': '新会话：' + x['内容'][:16], '开始': x['开始时间'], '可打断': False})
    out += _stackdev.sessions_of(d)
    for x in _period_an(d.name):
        if x.get('状态') == '运行中':
            out.append({'键': 'an:' + x['编号'], '名称': _an_name(x), '开始': x.get('开始时间', ''), '可打断': False})
    return out


def _stem_time(stem):
    mm = re.search(r'(\d{4})(\d\d)(\d\d)-(\d\d)(\d\d)(\d\d)', stem)
    return f'{mm[1]}-{mm[2]}-{mm[3]} {mm[4]}:{mm[5]}:{mm[6]}' if mm else ''


def period_activity(name, groups=8, per=20):
    """这一期所有会话最近的动作，按会话分组、新的在前：[{时间, 名称, 运行中, 动作}]。"""
    d = period_dir(name)
    s = dev_session(d.name) or {}
    run_log = s.get('日志') if s.get('状态') == '运行中' else ''
    out = []
    files = sorted((d / DEV_DIR).glob('*.jsonl')) if (d / DEV_DIR).exists() else []
    for f in files[-groups * 2:]:
        pre = f.stem.split('-')[0] if not f.stem[:1].isdigit() else ''
        px = next((x for x in _px_all(d) if x['日志'] == str(f)), None)
        out.append({'时间': _stem_time(f.stem), '名称': ('新会话：' + px['内容'][:20]) if px else pre or '开发会话',
                    '运行中': str(f) == run_log or bool(px and px['状态'] == '运行中'), '日志': str(f),
                    '参与': (px or {}).get('参与') or (s.get('参与', '') if str(f) == s.get('日志') else '')})
    for x in _period_an(d.name):
        out.append({'时间': x.get('开始时间', ''), '名称': _an_name(x), '运行中': x.get('状态') == '运行中', '日志': x.get('日志', ''),
                    '参与': x.get('参与', '')})
    out.sort(key=lambda g: (g['运行中'], g['时间']), reverse=True)      # 在跑的放最前面
    res = []
    for g in out:
        acts = dev_activity(g.pop('日志'), per)
        if acts:
            g['动作'] = acts
            res.append(g)
        if len(res) >= groups:
            break
    return res


def period_talk(name):
    d = period_dir(name)
    m = load_meta(d)
    return {'会话': period_sessions(d.name), '动作': period_activity(d.name), '对话': (m.get('对话') or [])[-6:],
            '排队': sum(1 for x in m.get('对话') or [] if x['状态'] == '排队中'),
            '状态': m.get('状态', ''), '事项': m.get('类型') == '事项'}


# ---- 发消息
def _period_root(m, s=None, task=False):
    """推动 / 另起会话在哪个仓库里跑：开发会话的仓库 > 开发仓库 > 「全栈 → 各端」里本机的仓库（按端的顺序）。
    → (仓库, 分支, 提示词里补的各端说明)；都没有返回 ('', '', '')。"""
    s = s or {}
    root = (m.get('开发仓库') or s.get('仓库') or '') if task else (s.get('仓库') or m.get('开发仓库') or '')
    if root and Path(root).is_dir():
        return root, s.get('分支', '') if s.get('仓库') == root else '', ''
    ends = [(e, v) for e, v in (m.get('各端') or {}).items()
            if v.get('仓库') and ':' not in str(v['仓库']).split('/')[0] and Path(v['仓库']).is_dir()]      # 远程的不能当本机工作目录
    if not ends:
        return '', '', ''
    e0, v0 = ends[0]
    note = ''
    if len(m.get('各端') or {}) > 1:
        note = ('\n这一期是全栈，各端仓库：\n' + '\n'.join(f'- {e}：{v.get("仓库")}' + (f'（分支 {v["分支"]}）' if v.get('分支') else '')
                                                    for e, v in (m.get('各端') or {}).items())
                + f'\n会话在 {e0} 的仓库里启动；只改和这次内容有关的端（别的端用绝对路径进去改），按各仓库惯例提交，不要 push（推送由知识库做）。')
    return v0['仓库'], v0.get('分支', ''), note


def _push_prompt(name, root, branch, text, resumed, stack=False):
    """stack：全栈多端（提示词后面补「不要 push」的各端说明），不加推送规则（REQ-024 只改 SDD 跟进）。"""
    rule = extra = ''
    try:                         # 角色团：判断类型后按类型派角色（不挂角色团时提示词不变）
        m = load_meta(period_dir(name))
        eng = period_engine(m)
        rule = team.push_rule(m, eng)
        # 接着上一轮会话续跑时 engine_cmd 不再附加场景段落；上一轮不是跟进（如开发）的，上下文里只有那个场景的规则，这里补上跟进的
        if resumed and rule and (m.get('开发会话') or {}).get('场景') != '跟进':
            extra = team.team_text('跟进', m, eng=eng)
    except KBError:
        pass
    head = '' if resumed else (f'你是由知识库网页启动的【后台无人值守】会话：期「{name}」，仓库 {root}，分支 {branch}。'
                               '终端里没有人，不要在回复里提问，也不要用 AskUserQuestion。\n\n')
    return head + f"""用户在知识库网页给期「{name}」发来跟进内容（{now()}）：
{text}

这一期没有会话在跑，这是后续发现的问题或要扩展的业务功能，请推动解决：
1. 先 kb_dev_package 读开发包（开发约定、流程、分支、文档目录），判断这是：只需回答的问题 / 要改代码的缺陷或需求扩展 / 要补的资料。{rule}
2. 要改代码的：按这一期的开发约定在分支 {branch or '（按开发包）'} 上改、自测、按约定提交；改法或范围有取舍时用 kb_ask 确认后再改（kb_ask 提示免打扰就按你的建议执行）。
3. 和原需求 / 方案文档不一致的，同步更新这一期的流程文档；缺账号、环境等先查 kb_base，查不到再 kb_ask。
4. 做完 kb_log 记一条：做了什么、改了哪些文件、提交号；回复里简短说明结果。
{'' if stack else PUSH_RULE + '。' + chr(10)}不要改这一期的状态，不要做用户没提到的开发。""" + extra + _NO_BG


def _task_prompt(d, m, root, branch, text, resumed):
    """事项：不走开发流程，接着上次的上下文按用户的话继续（如「编译校验一下」）。"""
    sup = [f'- 工程：{root}'] + [f'- {"分支" if n.get("类型") == "分支" else "补充"}：'
                                 f'{note_title(n) if n.get("类型") == "分支" else (n.get("内容") or note_title(n))}' for n in notes_of(d, m=m)]
    head = '' if resumed else (f'你是由知识库网页启动的【后台无人值守】会话，处理事项「{d.name}」。终端里没有人，不要在回复里提问，'
                               '也不要用 AskUserQuestion；要问用户就用 kb_ask（iteration 填事项名），再用 kb_wait_answer 等回答。\n'
                               '事项的补充信息：\n' + '\n'.join(sup) + '\n\n')
    return head + f"""用户在知识库网页上接着说（{now()}）：
{text}

接着上面的上下文继续做（比如上次只改了代码，这次让你编译校验，就去编译、跑相关测试并说明结果）。不走开发流程，不用 SDD；
要再改代码先用 kb_ask 说清改法（提示免打扰就按你的建议做）；提交按仓库惯例，不 push（推送由知识库在会话结束后按这一期的「自动推送」开关做）。和某一条的结果有关的，用 kb_task_item 更新那一条的结论。
回复里简短说明做了什么、结果如何。不要改事项的状态。""" + _NO_BG


def _push(d, m, text, by):
    """没有会话在跑：按内容推动解决（事项：接着上次的上下文继续，不走开发流程）。"""
    from kblib.steward import behavior
    behavior.emit('push', d.name, by=by)
    if m.get('类型') == '事项':
        s = dev_session(d.name) or {}
        root, ebranch, _ = _period_root(m, s, task=True)
        if not root:
            raise KBError('先在「补充」里选好工程（代码仓库），或在「全栈 → 各端」里给端选仓库')
        branch = next((note_title(n) for n in notes_of(d, m=m) if n.get('类型') == '分支'), '') or ebranch or s.get('分支', '')
        m['代码基线'] = timing.code_update('push_task', root, branch=branch, d=d)     # 推动前（默认不动，只记基线）
        sid = _session_id(s['日志']) if s.get('日志') and s.get('仓库') == root else ''
        msg = {'时间': now(), '内容': text, '方式': '接着做' if sid else '推动解决', '状态': '已送达',
               '送达时间': now(), '会话': 'dev', '目标': '接着上次的会话' if sid else '新会话'}
        m.setdefault('对话', []).append(msg)
        save_meta(d, m)
        if m.get('状态') != '开发中':
            set_status(d.name, '开发中', '网页「跟这个事项说」（本人批准）', by_human=True)
        proc = _dev_spawn(d, Path(root), branch, _task_prompt(d, load_meta(d), root, branch, text, bool(sid)), sid,
                          续跑次数=0, 类型='跟进', 仅对话=True)
        _deliver_now(d, [msg], proc)
        log(d, '推动解决', f'{"接着上次的会话" if sid else "新开会话"}（pid {proc.pid}）：{text[:100]}（{by}）')
        return '已' + ('接着上次的会话（保留上下文）' if sid else '新开会话') + '交给 AI，进展看下面的动作'
    if m.get('状态') == '收集中':
        add_note(d.name, text, '需求', '文字', source=by)
        return '这一期还在收集中，已记进「需求」文字资料；开始开发时会一起处理'
    s = dev_session(d.name) or {}
    root, branch, note = _period_root(m, s)
    if not root:
        raise KBError('这一期还没有开发仓库，没法推动：先在「全栈 → 各端」里给端选仓库，或在「开发」里开始开发')
    m['代码基线'] = timing.code_update('push_task', root, branch=branch, d=d)         # 推动前（默认不动，只记基线）
    sid = _session_id(s['日志']) if s.get('日志') and s.get('仓库') == root else ''
    msg = {'时间': now(), '内容': text, '方式': '推动解决', '状态': '已送达', '送达时间': now(), '会话': 'dev', '目标': '新的跟进会话'}
    m.setdefault('对话', []).append(msg)
    save_meta(d, m)
    proc = _dev_spawn(d, Path(root), branch, _push_prompt(d.name, root, branch, text, bool(sid), stack=bool(note)) + note, sid, 续跑次数=0, 类型='跟进',
                      仅对话=True)
    _deliver_now(d, [msg], proc)
    log(d, '推动解决', f'{"接着上次的会话" if sid else "新开会话"}处理（pid {proc.pid}）：{text[:100]}（{by}）')
    return ('没有会话在跑：已' + ('接着这一期上次的会话（保留上下文）' if sid else '新开后台会话')
            + '按你的内容推动解决，进展看「最近动作」，需要确认时会在「开发」里问你')


def _to_other(d, m, key, text, name):
    if key.startswith(('px:', 'end:')):          # 新会话、全栈各端会话：记进这一期「对话」，它下次调用知识库工具时收到（_take_px）
        m.setdefault('对话', []).append({'时间': now(), '内容': text, '方式': '排队', '状态': '待送达', '会话': key, '目标': name})
        return
    sid = key[3:]
    items = _an_load()
    x = next((i for i in items if i['编号'] == sid), None)
    if not x or x.get('状态') != '运行中':
        raise KBError(f'{name} 已经结束了')
    x.setdefault('留言期', [])
    if d.name not in x['留言期']:
        x['留言期'].append(d.name)
    _an_save(items)
    m.setdefault('对话', []).append({'时间': now(), '内容': text, '方式': '排队', '状态': '待送达', '会话': key, '目标': name})


ATTACH_DIR = '对话附件'
STAGE = SYS / 'tmp' / 'chat-stage'           # 网页上传的暂存区（和交给管家共用 /api/chat/stage）


def talk_attach(d, ids):
    """把网页暂存的文件（暂存号）复制进这一期的「对话附件/<时间>/」，返回复制后的路径。暂存区是临时的，会话晚点才读也不会丢。"""
    srcs = [STAGE / i for i in ids or [] if re.fullmatch(r'[0-9a-f]{32}', i or '') and (STAGE / i).is_dir()]
    if not srcs:
        return []
    dst = d / ATTACH_DIR / datetime.now().strftime('%Y%m%d-%H%M%S')
    dst.mkdir(parents=True, exist_ok=True)
    out = []
    for s in srcs:
        for f in s.iterdir():                       # 一个暂存号一个顶层文件 / 文件夹
            to = dst / f.name
            n = 1
            while to.exists():
                to = dst / f'{f.stem}-{n}{f.suffix}'
                n += 1
            (shutil.copytree if f.is_dir() else shutil.copy2)(f, to)
            out.append(to)
    return out


def _with_files(text, files):
    if not files:
        return text
    block = '附件（用 Read 查看；文件夹用 ls 看里面的文件）：\n' + '\n'.join(f'- {f}{"/" if f.is_dir() else ""}' for f in files)
    return f'{text}\n\n{block}' if text else block


def period_say(name, text, target='', interrupt=True, by='用户（网页）', mode='insert', files=None):
    """期页面最上方的输入框。没有会话在跑：推动解决。有会话在跑时 mode：
    insert 插入当前会话（target：会话的键 dev / an:… / px:…，或 all；开发会话默认打断当前这一步、带着补充立即接着做）；
    new 另起一个新会话并行执行；queue 排队，这一期的会话都结束后自动开始处理。
    files：网页暂存的文件（暂存号），复制进这一期后把路径附在消息末尾。"""
    d = period_dir(name)
    text = (text or '').strip()
    if not text and not files:
        raise KBError('消息不能为空')
    text = _with_files(text, talk_attach(d, files))
    if not text:
        raise KBError('附件已经失效（暂存区被清理了），重新选一下文件')
    unarchive_if(d.name, '你在这一期的页面里说了话')
    m = load_meta(d)
    sess = period_sessions(d.name)
    if not sess and (m.get('开发会话') or {}).get('状态') == '中断待续':
        return dev_chat(d.name, text)        # 被用量限制等打断、等着到点续的：现在就接着那个会话做，消息一起送到
    if not sess:
        return _push(d, m, text, by)
    if mode == 'new':
        return _px_start(d, m, text, by, sess)
    if mode == 'queue':
        m.setdefault('对话', []).append({'时间': now(), '内容': text, '方式': '排队处理', '状态': '排队中', '会话': 'queue',
                                        '目标': '会话都结束后处理'})
        save_meta(d, m)
        log(d, '对话', f'排队，会话都结束后处理：{text[:100]}（{by}）')
        n = sum(1 for x in m['对话'] if x['状态'] == '排队中')
        return f'已排队（共 {n} 条）：这一期正在跑的会话都结束后，自动接着上次的会话按顺序处理'
    if target == 'all':
        keys = [x['键'] for x in sess]
    elif target:
        keys = [target]
    elif len(sess) == 1:
        keys = [sess[0]['键']]
    else:
        raise KBError('有多个会话在跑，选一下发给哪个会话（或全部会话）')
    names = {x['键']: x['名称'] for x in sess}
    if any(k not in names for k in keys):
        raise KBError('选的会话已经结束了，刷新后重新选')
    res = []
    other = [k for k in keys if k != 'dev']
    for k in other:
        _to_other(d, m, k, text, names[k])
    if other:
        save_meta(d, m)
        res.append(f'{"、".join(names[k] for k in other)}：下次调用知识库工具时收到（这类会话不能打断）')
        log(d, '对话', f'发给 {"、".join(names[k] for k in other)}：{text[:100]}')
    if 'dev' in keys:
        res.append(f'{names["dev"]}：' + dev_chat(d.name, text, interrupt))
    return '；'.join(res)


# ---- 新会话：和正在跑的会话并行（记在期信息「并行会话」）
def _px_all(d):
    return (load_meta(d).get('并行会话') or [])[-30:]


def _px_list(d):
    """在跑的并行会话；进程没了的补记为已结束。"""
    m = load_meta(d)
    xs = m.get('并行会话') or []
    dead = [x for x in xs if x['状态'] == '运行中' and not _alive(x.get('pid', 0))]
    if dead:
        for x in dead:
            x.update(状态='已结束', 结束时间=now())
            team.tally(x, x.get('日志', ''), m, d, '跟进')
        save_meta(d, m)
    return [x for x in xs if x['状态'] == '运行中']


def _px_start(d, m, text, by, sess):
    s = dev_session(d.name) or {}
    root, branch, note = _period_root(m, s)
    if not root:
        raise KBError('这一期还没有开发仓库，没法另起会话：先在「全栈 → 各端」里给端选仓库')
    pid_ = datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid_hex()[:4]
    (d / DEV_DIR).mkdir(exist_ok=True)
    logf = d / DEV_DIR / f'新会话-{pid_}.jsonl'
    others = '、'.join(x['名称'] for x in sess)
    prompt = _push_prompt(d.name, root, branch, text, False, stack=bool(note)).replace(
        '这一期没有会话在跑，这是后续发现的问题或要扩展的业务功能，请推动解决：',
        f'这一期还有别的会话在跑（{others}），可能同时在改同一个仓库：改之前先 git status 看清楚，只改、只提交你这次需要的文件，'
        '不要 reset / checkout / stash 别人的改动；和正在做的事冲突时用 kb_ask 问。这是用户另起的任务，请推动解决：') + note
    eng = period_engine(m)
    start = _head(root)
    proc = engine_popen(eng, prompt, f'kb·{d.name}·新会话', logf, root,
                        env_extra=dict(_dev_env(d.name, branch, root), KB_SESSION='px:' + pid_), scene='跟进', period=m)
    m = load_meta(d)
    m.setdefault('并行会话', []).append({'编号': pid_, 'pid': proc.pid, '状态': '运行中', '开始时间': now(), '日志': str(logf),
                                       '内容': text, '引擎': eng['名称'], '发起': by})
    m.setdefault('对话', []).append({'时间': now(), '内容': text, '方式': '新会话执行', '状态': '已送达', '送达时间': now(),
                                    '会话': 'px:' + pid_, '目标': '新会话'})
    save_meta(d, m)
    log(d, '新会话', f'另起会话处理（pid {proc.pid}）：{text[:100]}（{by}）')

    def wait():
        code = proc.wait()
        mm = load_meta(d)
        for x in mm.get('并行会话') or []:
            if x['编号'] == pid_:
                x.update(状态='已结束', 结束时间=now(), 退出码=code)
                team.tally(x, x.get('日志', ''), mm, d, '跟进')
        save_meta(d, mm)
        log(d, '新会话结束', f'退出码 {code}：{text[:60]}')
        _session_push(d, {'仓库': root, '分支': branch, '起点': start}, code, '新会话结束')
        _notify(f'知识库 · {d.name}', '另起的会话已结束' + ('（有错误）' if code else ''))
        try:
            talk_queue_tick(d.name)
        except Exception:
            pass
    threading.Thread(target=wait, daemon=True).start()
    return f'已另起一个新会话执行（和 {others} 并行）；进展看「最近动作」'


# ---- 排队：这一期的会话都结束后，接着上次的会话处理
def talk_queue_tick(only=''):
    out = []
    for n in [only] if only else period_names():
        try:
            d = period_dir(n)
        except KBError:
            continue
        m = load_meta(d)
        q = [x for x in m.get('对话') or [] if x['状态'] == '排队中']
        if not q or period_sessions(d.name) or _sup.entries(d.name, states=_sup.OPEN):
            continue                    # 有会话在跑、或被打断等着接着做的：等它做完再处理排队的
        for x in q:
            x.update(状态='已送达', 送达时间=now(), 方式='排队处理（已开始）')
        save_meta(d, m)
        text = '\n'.join(f'{i + 1}. （{x["时间"]} 排队）{x["内容"]}' for i, x in enumerate(q)) if len(q) > 1 else q[0]['内容']
        try:
            out.append(_push(d, load_meta(d), text, '排队处理'))
        except KBError as e:
            log(d, '排队处理', f'没能开始：{e}')
    return out


def take_session_msgs():
    """Bug 分析 / 修复会话认领发给自己的消息（按环境变量 KB_SESSION），附在它调用知识库工具的结果后面。"""
    key = sess_env('KB_SESSION', '')
    if key.startswith(('px:', 'end:')):
        return _take_px(key)
    if not key.startswith('an:'):
        return ''
    items = _an_load()
    x = next((i for i in items if i['编号'] == key[3:]), None)
    if not x or not x.get('留言期'):
        return ''
    got = []
    for n in x.pop('留言期'):
        try:
            d = period_dir(n)
        except KBError:
            continue
        m = load_meta(d)
        xs = [i for i in m.get('对话') or [] if i.get('会话') == key and i['状态'] == '待送达']
        for i in xs:
            i.update(状态='已送达', 送达时间=now())
        if xs:
            save_meta(d, m)
            got += xs
    _an_save(items)
    if not got:
        return ''
    return ('\n\n【用户在网页发来消息】\n' + '\n'.join(f'- （{i["时间"]}）{i["内容"]}' for i in got)
            + '\n请先按消息调整（仍然遵守这个会话原来的规则，比如只读分析不改代码），然后接着原来的工作。')


def _take_px(key):
    try:
        d = period_dir(sess_env('KB_PERIOD', ''))
    except KBError:
        return ''
    m = load_meta(d)
    xs = [i for i in m.get('对话') or [] if i.get('会话') == key and i['状态'] == '待送达']
    if not xs:
        return ''
    if key.startswith('end:'):          # 记下交给了哪一轮：那一轮被用量限制等打断，续跑时补发
        _sup.deliver(xs, ((m.get('各端开发') or {}).get(key[4:]) or {}).get('日志', ''))
    else:
        for i in xs:
            i.update(状态='已送达', 送达时间=now())
    save_meta(d, m)
    return ('\n\n【用户在网页发来消息】\n' + '\n'.join(f'- （{i["时间"]}）{i["内容"]}' for i in xs)
            + '\n请先按消息调整，然后接着原来的工作。')
