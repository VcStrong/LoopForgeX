"""行为账本：把你在全平台的操作记成结构化事件，管家按类别统计后只出「提议」卡片（逐类放权，从不代点审批）。

事件（_system/管家/行为.jsonl，一行一条）：{时间, 事件, 对象, 子对象, 类别, 主体, 采纳, 耗时秒, ...}
  ask_answer   你回答 AI 的 kb_ask（类别按阶段 / 问题归：改法、定位、方案、需求、物料、仓库、其他；采纳：照建议 / 改了 / 无建议）
  dnd          开 / 关免打扰          archive / unarchive   归档 / 恢复
  push         推动（事项条目、跟这一期说）   bug_review / bug_fix / bug_analyze / plan   Bug 审核、修复、分析、出方案（你点的）
第一次用时从「待确认」、管家记录和记录.md 回填历史（只算你本人的操作，免打扰时 AI 自己定的不算）。

用来做什么（steward.patrol 里出卡片，都要你点一下才生效）：
  - 信任升级：某类动作你在任何页面手动做过 ≥3 次（不只是在管家卡片上点），问要不要以后直接做；
  - 归档：你常在完成后手动归档 → 提议「完成 7 天自动归档」；自动归档后你又恢复了 ≥2 次 → 提议改回去；
  - 接着推动：事项里推动过、会话已结束但还没解决的条目 → 一键「接着推动」（= 你本人批准，和条目上的按钮一样）；
  - kb_ask：问题里带上「这类问题你过去 X 次里 Y 次照建议」，网页显示在问题卡片上（仍由你点）。
硬边界：开始开发、方案拍板、Bug 审核、修复、推动、svn 提交不进任何可被学习放权的集合；「改法确认」按拍板处理。
"""
import json
import re
import threading
from datetime import datetime, timedelta

from kblib.core.common import PERIODS, SYS, load_settings, now

FILE = SYS / '管家' / '行为.jsonl'
_LOCK = threading.Lock()
KINDS = [('改法', r'改法|怎么改|修改方案|修复方案|根因与改法|改动范围'), ('定位', r'定位|根因|原因|复现'),
         ('方案', r'方案|技术规划|取舍|拍板|架构'), ('需求', r'需求|PRD|范围|验收|产品'), ('物料', r'物料|资料|设计稿|链接|日志|下载'),
         ('仓库', r'仓库|分支|工程|切换|push|推送|提交'), ('确认点', r'确认点')]


def enabled():
    return (load_settings().get('管家') or {}).get('学习', True) is not False


def ask_kind(q):
    """kb_ask 的问题类别：先看阶段，再看问题。"""
    for text in ((q or {}).get('阶段') or '', (q or {}).get('问题') or ''):
        for k, rx in KINDS:
            if re.search(rx, text, re.I):
                return k
    return '其他'


def adopted(q):
    """回答是不是照 AI 建议：→ 照建议 / 改了 / 无建议。"""
    a, r = ((q or {}).get('回答') or '').strip(), ((q or {}).get('AI建议') or '').strip()
    if not r:
        return '无建议'
    if not a:
        return '改了'
    if a == r or re.fullmatch(r'(按|照|采用)?\s*(AI\s*)?(你的)?建议(执行|来|做)?|同意|可以|好的?|ok|OK|就这样|行', a):
        return '照建议'
    if len(a) >= 4 and (a in r or r in a):
        return '照建议'
    opt = next((o for o in (q.get('选项') or []) if o and (r.startswith(o) or o.startswith(r[:12]))), '')
    return '照建议' if opt and (a == opt or a.startswith(opt[:12])) else '改了'


def _user(by):
    by = by or ''
    return not re.search(r'AI|管家|自动|Claude|会话结束|免打扰', by)


def emit(event, obj='', sub='', kind='', by='用户', t='', **kv):
    """记一条（失败不影响主流程）。by 是 AI / 管家 / 自动 的不记——只学你本人的操作。"""
    if not _user(by):
        return
    try:
        _backfill()
        x = {'时间': t or now(), '事件': event, '对象': obj, '子对象': sub, '类别': kind, '主体': by, **kv}
        with _LOCK:
            FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(FILE, 'a', encoding='utf-8') as f:
                f.write(json.dumps(x, ensure_ascii=False) + '\n')
    except Exception as e:      # noqa: BLE001
        print('behavior:', repr(e), flush=True)


def events(days=None, event=None):
    _backfill()
    try:
        lines = FILE.read_text('utf-8').splitlines()
    except OSError:
        return []
    cut = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d %H:%M') if days else ''
    out = []
    for ln in lines:
        try:
            x = json.loads(ln)
        except ValueError:
            continue
        if (not cut or x.get('时间', '') >= cut) and (not event or x.get('事件') == event):
            out.append(x)
    return out


def _secs(a, b):
    try:
        return int((datetime.strptime(b, '%Y-%m-%d %H:%M') - datetime.strptime(a, '%Y-%m-%d %H:%M')).total_seconds())
    except (TypeError, ValueError):
        return None


def ask_event(period, q):
    """你回答了一个 kb_ask。"""
    emit('ask_answer', period, q.get('编号', ''), ask_kind(q), q.get('回答人') or '用户', 采纳=adopted(q),
         耗时秒=_secs(q.get('时间'), q.get('回答时间') or now()), 阶段=q.get('阶段', ''))


def ask_rate(kind, days=180):
    """这类问题你过去回答了几次、几次照建议、最近 3 次是不是都照建议。"""
    xs = [x for x in events(days, 'ask_answer') if x.get('类别') == kind and x.get('采纳') != '无建议']
    n, y = len(xs), sum(1 for x in xs if x.get('采纳') == '照建议')
    return {'类别': kind, '次数': n, '照建议': y, '最近都照': bool(xs) and all(x.get('采纳') == '照建议' for x in xs[-3:])}


# ---------------------------------------------------------------- 回填历史（只在账本还不存在时跑一次）
_FILLED = False


def _backfill():
    global _FILLED
    if _FILLED:
        return
    with _LOCK:
        if _FILLED:
            return
        _FILLED = True
        if FILE.exists():
            return
        rows = []
        for d in sorted(PERIODS.glob('*')) if PERIODS.exists() else []:
            try:
                m = json.loads((d / '期信息.json').read_text('utf-8'))
            except (OSError, ValueError):
                continue
            for q in m.get('待确认') or []:
                if q.get('状态') == '已回答' and _user(q.get('回答人')):
                    rows.append({'时间': q.get('回答时间') or q.get('时间', ''), '事件': 'ask_answer', '对象': d.name, '子对象': q.get('编号', ''),
                                 '类别': ask_kind(q), '主体': q.get('回答人') or '用户', '采纳': adopted(q),
                                 '耗时秒': _secs(q.get('时间'), q.get('回答时间')), '阶段': q.get('阶段', ''), '回填': True})
            try:
                logs = (d / '记录.md').read_text('utf-8', errors='ignore').splitlines()
            except OSError:
                logs = []
            for ln in logs:
                mm = re.search(r'(\d{4}-\d\d-\d\d \d\d:\d\d)\D{0,8}?(归档|免打扰|推动解决|推动|Bug 分析审核|Bug 修复)\s*[｜|:：]\s*(.*)', ln)
                if not mm or not re.search(r'（[^（）]*(用户|网页|本人)[^（）]*）', mm.group(3)):
                    continue
                t, act, body = mm.groups()
                ev = {'归档': 'unarchive' if body.startswith('恢复') else 'archive', '免打扰': 'dnd', '推动解决': 'push', '推动': 'push',
                      'Bug 分析审核': 'bug_review', 'Bug 修复': 'bug_fix'}[act]
                rows.append({'时间': t, '事件': ev, '对象': d.name, '子对象': '', '类别': '', '主体': '用户（回填）', '回填': True,
                             **({'开': '开启' in body} if ev == 'dnd' else {})})
        try:
            for ln in (SYS / '管家' / '记录.jsonl').read_text('utf-8').splitlines():
                r = json.loads(ln)
                if r.get('方式') in ('手动', '回答'):
                    rows.append({'时间': r.get('时间', ''), '事件': 'steward', '对象': str(r.get('对象', ''))[:200], '子对象': '',
                                 '类别': r.get('动作', ''), '主体': r.get('回答人') or '用户', '回填': True})
        except (OSError, ValueError):
            pass
        rows.sort(key=lambda x: x['时间'])
        FILE.parent.mkdir(parents=True, exist_ok=True)
        FILE.write_text(''.join(json.dumps(x, ensure_ascii=False) + '\n' for x in rows), 'utf-8')


# ---------------------------------------------------------------- 学到的提议（patrol 出卡片）
def learned_cards():
    if not enabled():
        return []
    out = []
    try:
        out += _archive_cards()
    except Exception as e:      # noqa: BLE001
        print('behavior archive cards:', repr(e), flush=True)
    try:
        out += _push_again_cards()
    except Exception as e:      # noqa: BLE001
        print('behavior push cards:', repr(e), flush=True)
    return out


ARCHIVE_SUGGEST = 7


def _archive_cards():
    from kblib.core.periods import archive_days
    days = archive_days()
    xs = events(30)
    manual = [x for x in xs if x['事件'] == 'archive']
    back = [x for x in xs if x['事件'] == 'unarchive' and x.get('时间', '') >= (load_settings().get('归档天数时间') or '9')]
    if (days == 0 or days > ARCHIVE_SUGGEST) and len(manual) >= 3:
        return [{'id': f'learn:archive:{ARCHIVE_SUGGEST}', '类型': '建议', '标题': f'做完的期 / 事项，以后完成 {ARCHIVE_SUGGEST} 天就自动归档？',
                 '说明': f'最近 30 天你手动归档了 {len(manual)} 次（' + '、'.join(dict.fromkeys(x['对象'] for x in manual[-4:])) + '）；现在是'
                         + (f'完成 {days} 天才自动归档' if days else '不自动归档'),
                 '为什么': '归档只是不出现在列表里，资料和检索都不动；有新动静会自动回来，也能在「📦 归档」页恢复。之后你要是又恢复了 2 次以上，我会提议改回去。',
                 '按钮': f'以后 {ARCHIVE_SUGGEST} 天自动归档', '动作': 'learn', '对象': {'归档天数': ARCHIVE_SUGGEST}, '链接': '#/archive'}]
    if days and days <= ARCHIVE_SUGGEST and load_settings().get('归档天数来源') == '管家提议' and len(back) >= 2:
        return [{'id': f'learn:archive-back:{len(back)}', '类型': '建议', '标题': f'自动归档后你又恢复了 {len(back)} 次，改回 14 天？',
                 '说明': '、'.join(dict.fromkeys(x['对象'] for x in back[-4:])), '为什么': '看起来 7 天太早了，按你的实际操作收回这项放权。',
                 '按钮': '改回 14 天', '动作': 'learn', '对象': {'归档天数': 14}, '链接': '#/archive'}]
    return []


def _push_again_cards(limit=5):
    """事项里推动过、会话已经结束、但还没解决的条目：一键接着推动（点了 = 你本人批准）。"""
    from kblib.core.periods import is_archived, is_task, load_meta, period_names
    from kblib.ai import supervise as _sup
    cut = (datetime.now() - timedelta(days=14)).strftime('%Y-%m-%d %H:%M')
    out = []
    for n in period_names():
        try:
            m = load_meta(PERIODS / n)
        except Exception:      # noqa: BLE001
            continue
        if not is_task(m) or m.get('状态') == '已完成' or is_archived(m):
            continue
        s = m.get('开发会话') or {}
        if s.get('状态') in ('运行中', '中断待续') or _sup.entries(n, states=_sup.OPEN):
            continue
        for k, st in (m.get('条目状态') or {}).items():
            if st.get('状态') not in ('推动中', '没解决') or (st.get('推动时间') or '') < cut:
                continue
            if st.get('状态') == '推动中' and (s.get('结束时间') or '') < (st.get('推动时间') or ''):
                continue                    # 推动后会话还没结束过
            why = st.get('结论') or ('会话结束了，但没记下这一条的结论' if st.get('状态') == '推动中' else '')
            out.append({'id': f'learn:push:{n}:{k}:{st.get("推动时间", "")}', '类型': '建议', '标题': f'「{n}」{k} 推动过，还没解决，接着推动？',
                        '说明': f'上次推动 {st.get("推动时间", "")}，现在「{st.get("状态")}」' + (f'：{why[:100]}' if why else ''),
                        '为什么': '你推动过的条目没解决时一般会接着推（之前「接着上次的会话」推动了几十次）。点了就等于你本人批准，接着上次的会话做。',
                        '按钮': '接着推动', '动作': 'push-item', '对象': {'事项': n, '条目': k}, '链接': '#/p/' + n})
            if len(out) >= limit:
                return out
    return out


def apply_learn(obj, by='用户（网页）'):
    """点了学习卡片：改对应的设置（只有能撤销的设置项）。"""
    from kblib.core.common import save_settings
    s = load_settings()
    if '归档天数' in obj:
        s['归档天数'] = int(obj['归档天数'])
        s['归档天数来源'] = '管家提议' if int(obj['归档天数']) <= ARCHIVE_SUGGEST else '你改回'
        s['归档天数时间'] = now()
        save_settings(s)
        return f'好，以后完成 {s["归档天数"]} 天自动归档（「⚙ 设置」里随时能改）'
    raise ValueError('不认识的设置')


def stats_text(days=14):
    """给日报 / 设置页：最近学到了什么。"""
    xs = events(days)
    k = {}
    for x in xs:
        if x['事件'] == 'ask_answer':
            k.setdefault(x['类别'], [0, 0])
            k[x['类别']][0] += 1
            k[x['类别']][1] += x.get('采纳') == '照建议'
    return '；'.join(f'{c} {y}/{n} 照建议' for c, (n, y) in sorted(k.items(), key=lambda kv: -kv[1][0]))
