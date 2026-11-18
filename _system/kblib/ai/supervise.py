"""监工：后台会话退出时统一分类，外部原因中断的记进台账，到点自动接着做。

  分类（classify）：做完 / 用户停止 / 外部中断（用量限制、网络、引擎崩溃、机器重启）/ 真失败。
    只看最终结果、报错事件和非 JSON 的输出行，不看 AI 的正文（AI 在讨论「rate limit」时不能误判成被限流）。
  外部中断：从引擎输出里解析恢复时间（取不到按 5 → 15 → 60 分钟退避），记进台账「几点接着做」，会话状态改成「中断待续」。
  到点续跑（tick）：管家每 5 分钟巡检时调用；用原会话号 --resume 接着做，续跑提示先核对文档和 git 的实际进度。
    批准针对的是这件事，不是某一个进程，所以不用重新拍板。连续中断超过上限就停下，出卡片问你。
  消息：交给会话时记下交给了哪一轮（送达日志）；那一轮正常结束才算「已处理」；被打断那一轮没处理的，续跑时按编号补发一次。
台账：_system/管家/中断台账.json；设置在 设置.json「管家」：中断续跑（默认开）、续跑上限（默认 5）。
单仓库开发 / 复测 / 跟进 / 事项条目（kblib.ai.devsession）和全栈各端开发、审查（kblib.stack.dev）接入；Bug 分析 / 修复、另起的新会话还没接。
"""
import json
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path

from kblib.core.common import SYS, KBError, load_settings, now, uuid_hex

FILE = SYS / '管家' / '中断台账.json'
BACKOFF = (5, 15, 60)          # 取不到恢复时间时，第 1 / 2 / 3+ 次中断等几分钟
BUFFER_MIN = 2                 # 解析出的恢复时间再多等一会儿
KEEP = 300
_LOCK = threading.RLock()
OPEN = ('待续', '已停下')
REASONS = ('用量限制', '网络', '引擎崩溃', '机器重启')


class KBLater(KBError):
    """现在续不了（仓库被占、名额满），过一会儿再试，不算续跑失败。"""


# ---------------------------------------------------------------- 分类
_LIMIT = re.compile(r'usage limit|rate[ _-]?limit|hit your (usage )?limit|limit (reached|will reset)|(session|weekly|5-hour|daily) limit'
                    r'|too many requests|\b429\b|insufficient[_ ]quota|quota exceeded|out of (extra )?usage|额度|限额|用量(上限|限制)', re.I)
_NET = re.compile(r'econnreset|etimedout|enotfound|econnrefused|eai_again|getaddrinfo|network (error|is unreachable)|connection (error|refused|reset|closed)'
                  r'|socket hang up|fetch failed|could not resolve|request timed out|api error: 5\d\d|overloaded|service unavailable'
                  r'|(status|http|error|code)\W{0,3}(500|502|503|504|529)\b|stream (disconnected|error)|网络', re.I)
_KILLED = (-9, -6, -11, -15, 134, 137, 139, 143)


def _tail(logf, size=400000):
    try:
        with open(logf, 'rb') as fh:
            head = fh.readline().decode('utf-8', 'ignore')
            fh.seek(max(0, Path(logf).stat().st_size - size))
            return head, fh.read().decode('utf-8', 'ignore')
    except (OSError, TypeError):
        return '', ''


def _signals(text, res):
    """会说明「为什么停了」的那些文字：最终结果（短的）、带 error 的事件、非 JSON 的输出行。"""
    out = []
    r = (res or {}).get('结果') or ''
    if len(r) <= 1500:
        out.append(r)
    plain = []
    for line in (text or '').splitlines():
        s = line.strip()
        if not s:
            continue
        if not s.startswith('{'):
            plain.append(s)
            continue
        try:
            j = json.loads(s)
        except ValueError:
            plain.append(s)
            continue
        if not isinstance(j, dict):
            continue
        t = j.get('type')
        if t in ('error', 'turn.failed') or j.get('error') or (t == 'system' and 'retry' in str(j.get('subtype', ''))):
            err = j.get('error')
            out.append(json.dumps(err, ensure_ascii=False) if isinstance(err, (dict, list)) else str(err or ''))
            out.append(str(j.get('message') or ''))
            if t == 'assistant':          # Claude 被限流时，带 error 的那条 assistant 消息正文就是限流说明
                for c in ((j.get('message') or {}).get('content') or [])[:3]:
                    if isinstance(c, dict) and c.get('type') == 'text':
                        out.append((c.get('text') or '')[:500])
        elif t == 'result' and j.get('is_error'):
            out.append(str(j.get('result') or '')[:1500])
    out += plain[-40:]
    return '\n'.join(x for x in out if x)


def parse_reset(text, base=None):
    """从限流说明里找「几点恢复」→ datetime；找不到返回 None。认：|epoch、resets 3pm / resets at 15:05、
    try again in 1 hour 5 minutes、try again at 3:05 PM、retry after 120（秒）、ISO 时间。"""
    base = base or datetime.now()
    t = text or ''
    m = re.search(r'(?:\||reset[s_]?(?:_?at)?"?\s*[:=]\s*"?)(1\d{9})\b', t, re.I)
    if m:
        return datetime.fromtimestamp(int(m.group(1)))
    m = re.search(r'reset[s_]?(?:_?at)?"?\s*[:=]?\s*"?(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?)(Z|[+-]\d{2}:?\d{2})?', t, re.I)
    if m:
        try:
            dt = datetime.fromisoformat(m.group(1).replace(' ', 'T') + (m.group(2) or '').replace('Z', '+00:00'))
            return dt.astimezone().replace(tzinfo=None) if dt.tzinfo else dt
        except ValueError:
            pass
    m = re.search(r'(?:try again|retry|resets?|available again)\s+in\s+((?:\d+\s*(?:days?|d|hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)\b[\s,and]*)+)', t, re.I)
    if m:
        sec = 0
        for n, u in re.findall(r'(\d+)\s*([a-z]+)', m.group(1), re.I):
            u = u.lower()
            sec += int(n) * (86400 if u.startswith('d') else 3600 if u.startswith('h') else 60 if u.startswith('m') else 1)
        if sec:
            return base + timedelta(seconds=sec)
    m = re.search(r'retry[- ]after"?\s*[:=]?\s*"?(\d+)\b', t, re.I)
    if m and int(m.group(1)) < 7 * 86400:
        return base + timedelta(seconds=int(m.group(1)))
    m = (re.search(r'(?:resets?|try again|available again)\s+(?:at\s+|after\s+)?(\d{1,2})(?::(\d{2}))?\s*([ap]\.?m\.?)?', t, re.I)
         or re.search(r'(\d{1,2}):(\d{2})()\s*(?:后)?(?:重置|恢复|再试)', t))
    if m:
        h, mi, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or '').lower().replace('.', '')
        if not m.group(2) and not ap:
            return None                   # 「resets 5」这种看不出是几点
        if ap == 'pm' and h < 12:
            h += 12
        elif ap == 'am' and h == 12:
            h = 0
        if h < 24 and mi < 60:
            dt = base.replace(hour=h, minute=mi, second=0, microsecond=0)
            return dt if dt > base else dt + timedelta(days=1)
    return None


def classify(logf, code, res=None, stopped=False):
    """→ {类别: 做完 / 用户停止 / 外部中断 / 真失败, 原因, 详情, 恢复时间('' = 没解析出)}。
    code：退出码；None = 不是本进程盯着的（服务重启过），拿不到。"""
    if stopped:
        return {'类别': '用户停止', '原因': '', '详情': '', '恢复时间': ''}
    head, text = _tail(logf)
    if res is None:
        from kblib.ai.devsession import _result_of
        try:
            fmt = json.loads(head).get('format', '') if head.startswith('{"type": "kb.engine"') else ''
        except ValueError:
            fmt = ''
        res = _result_of(text, fmt)
    failed = code not in (0, None) or res.get('出错') or (code is None and not res)
    if not failed:
        return {'类别': '做完', '原因': '', '详情': '', '恢复时间': ''}
    blob = _signals(text, res)
    line = lambda rx: next((ln.strip()[:200] for ln in blob.splitlines() if rx.search(ln)), '')
    if _LIMIT.search(blob):
        at = parse_reset(blob)
        return {'类别': '外部中断', '原因': '用量限制', '详情': line(_LIMIT),
                '恢复时间': (at + timedelta(minutes=BUFFER_MIN)).strftime('%Y-%m-%d %H:%M') if at else ''}
    if _NET.search(blob):
        return {'类别': '外部中断', '原因': '网络', '详情': line(_NET), '恢复时间': ''}
    if not res and code is None:
        return {'类别': '外部中断', '原因': '机器重启', '详情': '会话进程不在了，也没有留下结果（机器或知识库服务重启过）', '恢复时间': ''}
    if code in _KILLED or (code not in (0, None) and not res):
        return {'类别': '外部中断', '原因': '引擎崩溃', '详情': f'退出码 {code}，没有留下结果' + (f'：{blob.splitlines()[-1][:160]}' if blob else ''),
                '恢复时间': ''}
    return {'类别': '真失败', '原因': '', '详情': (res.get('结果') or '')[:200] or f'退出码 {code}', '恢复时间': ''}


# ---------------------------------------------------------------- 设置、台账
def settings():
    s = load_settings().get('管家') or {}
    try:
        n = int(s.get('续跑上限') or 5)
    except (TypeError, ValueError):
        n = 5
    return {'开启': s.get('中断续跑', True) is not False, '上限': max(1, min(n, 50))}


def _load():
    try:
        return json.loads(FILE.read_text('utf-8'))
    except (OSError, ValueError):
        return []


def _save(xs):
    FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps(xs[-KEEP:], ensure_ascii=False, indent=1), 'utf-8')
    tmp.replace(FILE)


def entries(period='', kind='', end='', states=None):
    return [x for x in _load() if (not period or x['期'] == period) and (not kind or x['类型'] == kind)
            and (not end or x.get('端', '') == end) and (states is None or x['状态'] in states)]


def get(eid):
    return next((x for x in _load() if x['编号'] == eid), None)


def _update(eid, **kv):
    with _LOCK:
        xs = _load()
        for x in xs:
            if x['编号'] == eid:
                x.update(kv)
                _save(xs)
                return x
    return None


def backoff(count):
    return BACKOFF[min(max(count, 1), len(BACKOFF)) - 1]


def record(period, kind, info, count=1, end='', name='', log='', phase=''):
    """记一次外部中断 → 台账条目。count：这个会话连续第几次中断（中间正常做完一轮就重新算）。"""
    cfg = settings()
    at = info.get('恢复时间') or (datetime.now() + timedelta(minutes=backoff(count))).strftime('%Y-%m-%d %H:%M')
    x = {'编号': datetime.now().strftime('%m%d%H%M%S') + uuid_hex()[:4], '期': period, '类型': kind, '端': end, '阶段': phase,
         '会话': name or ('开发' if kind == 'dev' else f'{end} 开发'), '原因': info.get('原因', ''), '详情': info.get('详情', ''),
         '时间': now(), '恢复时间': at, '解析出恢复时间': bool(info.get('恢复时间')), '次数': count, '日志': str(log or ''),
         '状态': '待续' if count <= cfg['上限'] else '已停下'}
    if x['状态'] == '已停下':
        x['说明'] = f'连续中断 {count} 次（上限 {cfg["上限"]}），不再自动续跑'
    with _LOCK:
        xs = _load()
        for o in xs:
            if o['期'] == period and o['类型'] == kind and o.get('端', '') == end and o['状态'] in OPEN:
                o.update(状态='已取代', 结束时间=now())
        xs.append(x)
        _save(xs)
    return x


def summary(x):
    """记进会话信息里的那一份（网页开发面板 / 各端表显示用）。"""
    return {k: x.get(k) for k in ('编号', '原因', '详情', '时间', '恢复时间', '次数', '状态', '说明')}


def settle(period, kind, end='', why='已接着做'):
    """这个会话又起了新的一轮（续跑、你发了消息、重新开工）：台账里等着的条目就算处理了。"""
    with _LOCK:
        xs = _load()
        hit = False
        for o in xs:
            if o['期'] == period and o['类型'] == kind and o.get('端', '') == end and o['状态'] in OPEN:
                o.update(状态=why, 结束时间=now())
                hit = True
        if hit:
            _save(xs)


def label(x):
    """「因用量限制暂停，预计 15:05 自动接着做」"""
    t = (x.get('恢复时间') or '')[11:16]
    if x['状态'] == '已停下':
        return f'因{x["原因"]}中断，{x.get("说明") or "已停下"}'
    if not settings()['开启']:
        return f'因{x["原因"]}暂停（自动续跑已关闭）'
    return f'因{x["原因"]}暂停，预计 {t} 自动接着做'


# ---------------------------------------------------------------- 续跑 / 不用了
def resume(eid, by='自动'):
    x = get(eid)
    if not x:
        raise KBError('没有这条中断记录')
    if x['状态'] not in OPEN:
        raise KBError(f'这条已经{x["状态"]}了')
    try:
        if x['类型'] == 'dev':
            from kblib.ai.devsession import dev_resume_interrupted
            msg = dev_resume_interrupted(x['期'], x)
        elif x['类型'] == 'end':
            from kblib.stack.dev import stack_resume_interrupted
            msg = stack_resume_interrupted(x['期'], x['端'], x)
        else:
            raise KBError(f'不认识的会话类型：{x["类型"]}')
    except KBLater as e:
        _update(eid, 恢复时间=(datetime.now() + timedelta(minutes=5)).strftime('%Y-%m-%d %H:%M'), 等待=str(e))
        raise
    except Exception as e:
        bad = int(x.get('续跑失败') or 0) + 1
        kv = {'续跑失败': bad, '错误': str(e)[:300], '恢复时间': (datetime.now() + timedelta(minutes=15)).strftime('%Y-%m-%d %H:%M')}
        if bad >= 3:
            kv.update(状态='已停下', 说明=f'续跑失败 {bad} 次：{str(e)[:120]}')
        _update(eid, **kv)
        raise
    _update(eid, 状态='已续', 续跑时间=now(), 续跑人=by, 结束时间=now())
    _log(x['期'], '中断续跑', f'{x["会话"]}：{"到点自动" if by == "自动" else by}接着做（上次因{x["原因"]}中断，第 {x["次数"]} 次）')
    return msg


def dismiss(eid, by='用户（网页）'):
    x = get(eid)
    if not x:
        raise KBError('没有这条中断记录')
    if x['状态'] not in OPEN:
        return f'这条已经{x["状态"]}了'
    _update(eid, 状态='不用了', 结束时间=now(), 处理人=by)
    try:
        if x['类型'] == 'dev':
            from kblib.ai.devsession import dev_drop_interrupted
            dev_drop_interrupted(x['期'])
        elif x['类型'] == 'end':
            from kblib.stack.dev import stack_drop_interrupted
            stack_drop_interrupted(x['期'], x['端'])
    except KBError:
        pass
    _log(x['期'], '中断续跑', f'{x["会话"]}：不再接着做（{by}）')
    return f'好，「{x["期"]}」的{x["会话"]}不再自动接着做'


def tick():
    """管家每轮巡检调用：到点的接着做。返回 [(编号, 结果)]。"""
    if not settings()['开启']:
        return []
    out, t = [], now()
    for x in entries(states=('待续',)):
        if (x.get('恢复时间') or '') > t:
            continue
        try:
            out.append((x['编号'], resume(x['编号'])))
        except KBLater as e:
            out.append((x['编号'], f'稍后再试：{e}'))
        except Exception as e:      # noqa: BLE001 — 一条续不上不影响别的
            out.append((x['编号'], f'续跑失败：{e}'))
    return out


def _log(period, kind, text):
    try:
        from kblib.core.periods import log, period_dir
        log(period_dir(period), kind, text)
    except Exception:       # noqa: BLE001
        pass


# ---------------------------------------------------------------- 管家卡片、日报
def cards():
    out = []
    for x in entries(states=OPEN):
        stop = x['状态'] == '已停下'
        out.append({'id': 'intr:' + x['编号'], '类型': '问你' if stop else '建议',
                    '标题': f'「{x["期"]}」{x["会话"]}：{label(x)}',
                    '说明': (x.get('详情') or '')[:160] + (f'（续跑出错：{x["错误"][:80]}）' if x.get('错误') else ''),
                    '为什么': ('连续中断太多次，我先停下了：看看是额度、网络还是别的问题，处理好再点「现在就续」。' if stop else
                             '这是外部原因（不是做失败了），到点我用原会话接着做，先核对文档和 git 的实际进度，不会倒退；也可以现在就续。'),
                    '按钮': '现在就续', '动作': 'resume', '对象': x['编号'],
                    '链接': '#/p/' + x['期']})
    return out


def day_stats(day):
    xs = [x for x in _load() if (x.get('时间') or '').startswith(day)]
    return len(xs), sum(1 for x in xs if x['状态'] == '已续' and x.get('续跑人') == '自动')


# ---------------------------------------------------------------- 消息：已交给会话 / 已处理
def deliver(xs, logf):
    """消息交给某一轮会话：记编号和交给了哪一轮（还不算处理）。"""
    for x in xs:
        x.setdefault('编号', 'M' + uuid_hex()[:8])
        if x.get('送达日志') or x.get('状态') != '已送达':       # 先占住（日志为空）再记到新一轮的，只算一次
            x['送达次数'] = int(x.get('送达次数') or 0) + 1
        x.update(状态='已送达', 送达时间=now(), 送达日志=str(logf or ''))


def _mine(x, key):
    return x.get('会话', 'dev') == key


def processed(m, key, logf):
    """这一轮正常结束：交给它的消息算已处理。m 由调用方保存。返回处理了几条。"""
    n = 0
    for x in m.get('对话') or []:
        if _mine(x, key) and x.get('状态') == '已送达' and x.get('送达日志') == str(logf or ''):
            x.update(状态='已处理', 处理时间=now())
            n += 1
    return n


def unprocessed(m, key, logf):
    """被打断的那一轮里交给它、还没处理的消息，加上还排着没送到的（按编号去重）。"""
    seen, out = set(), []
    for x in m.get('对话') or []:
        if not _mine(x, key):
            continue
        if (x.get('状态') == '已送达' and x.get('送达日志') == str(logf or '')) or x.get('状态') == '待送达':
            k = x.get('编号') or id(x)
            if k not in seen:
                seen.add(k)
                out.append(x)
    return out


def resend_text(xs):
    if not xs:
        return ''
    for x in xs:
        x.setdefault('编号', 'M' + uuid_hex()[:8])
    return ('\n\n【被打断前用户发来、还没处理完的消息】（按编号；你确认已经处理过的就跳过，不要做两遍）\n'
            + '\n'.join(f'- #{x["编号"]}（{x["时间"]}）{x["内容"]}' for x in xs)
            + '\n要回答的直接在回复里回答，要做的就去做，然后接着原来的工作。')


def resume_note(x):
    return (f'【接着做】上一轮在 {x.get("时间", "")} 因{x.get("原因", "外部原因")}中断了（{(x.get("详情") or "")[:120]}），这不是你做错了。'
            '你结束前在跑的后台命令、子 agent 都已经没了。先核对实际进度：看文档目录里的进度记录、git status / git log 看已经改了和提交了什么，'
            '做完的步骤不要重做、不要回退已有的改动，从断开的地方接着做。\n')
