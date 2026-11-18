"""构建：每一期（事项）对应构建平台上的产出（安卓就是 Jenkins 上这一期分支的各次构建：版本、提交、apk、mapping）。

知识库本体不写死 Jenkins：挂载了声明「能力: 构建」的工具插件就用它（插件/Jenkins/）。插件子命令（JSON 进出）：
  jobs <关键词…> → [{名称, 地址, 分}]；builds <任务> [数量] → {任务, 地址, 构建: […]}；build <构建地址> → 构建
期信息「构建任务」记这一期对应的任务：[{名称, 地址, 依据: 自动 / 手动}]。没记过就按这一期的分支模糊匹配（分数够高才自动绑）。
"""
import re
import time

from kblib.core.common import KBError, now
from kblib.core.periods import load_meta, log, period_dir, save_meta
from kblib.core.notes import note_title, notes_of
from kblib.plugins.registry import _plugin_json, plugins
from kblib.ai.devsession import dev_session
from kblib.ai.branchswap import history_branches
from kblib.bugs.index import zt_load

AUTO_SCORE = 90           # 自动绑定要的匹配分（分支整段出现在任务名里约 95+）
_CACHE = {}               # (插件, 任务, 数量) → (时间, 结果)
CACHE_SEC = 90


def build_plugin(required=True):
    p = next((x for x in plugins() if '构建' in (x.get('能力') or []) and x.get('启用') and not x.get('错误')), None)
    if not p and required:
        raise KBError('还没有挂载构建插件：把 Jenkins 地址填进「◈ 基础信息」，在「⚙ 设置 → 插件 → Jenkins」挂载并选这个条目')
    return p


def period_branches(d, m=None):
    m = m or load_meta(d)
    out = []
    for n in notes_of(d, m=m):
        if n.get('类型') == '分支':       # 「分支 feature-xxx-stable 工程」这类写法：取像分支名的那段
            t = note_title(n) + ' ' + (n.get('内容') or '')
            out += [x for x in re.findall(r'[A-Za-z0-9][\w./-]*[-/][\w./-]*[A-Za-z0-9]', t) if not x.startswith(('http', '/'))][:2] or [t.strip()]
    s = dev_session(d.name) or {}
    cur = ([m['开发分支']] if m.get('开发分支') else []) + [v['分支'] for v in (m.get('各端') or {}).values() if v.get('分支')]   # 全栈：各端的分支都去对构建任务
    if s.get('分支') and s['分支'] not in history_branches(m):
        cur.append(s['分支'])
    out = cur + out + history_branches(m)          # 当前分支在前；换下来的老分支也算（老构建、Bug 归属、mapping 照旧能查）
    return list(dict.fromkeys(b for b in out if b and b not in ('master', 'main', 'develop')))


def _old(m):
    return set(history_branches(m)) - {m.get('开发分支')} - {v.get('分支') for v in (m.get('各端') or {}).values()}


def _match(p, branches):
    if not branches:
        return []
    js = _plugin_json(p['id'], ['jobs', *branches], '构建匹配') or []
    if not js:
        return []
    top = js[0]['分']
    return [j for j in js if j['分'] >= AUTO_SCORE and j['分'] >= top - 1][:3]


def _tried(cur):
    """已经对过构建任务的分支（老数据 分支 是列表）。"""
    out = set()
    for j in cur or []:
        b = j.get('分支')
        out |= set(b if isinstance(b, list) else [b] if b else [])
    return out


def _mark(m, js):
    """按分支标「历史」：任务对应的分支都换下来了的，是老分支的任务（照旧显示，排在后面）。"""
    old = _old(m)
    for j in js:
        b = j.get('分支')
        bs = b if isinstance(b, list) else [b] if b else []
        j['历史'] = bool(bs) and all(x in old for x in bs)
    return sorted(js, key=lambda j: j['历史'])


def period_jobs(name, rematch=False):
    """这一期对应的构建任务：按分支逐个匹配、追加。已有任务不丢，老分支的任务标「历史」；
    换了新分支（还没对过构建任务的分支）自动补匹配；rematch = 所有分支重新匹配，和手动指定的合在一起。"""
    d = period_dir(name)
    m = load_meta(d)
    cur = m.get('构建任务')
    br = period_branches(d, m)
    done = _tried(cur) | set(m.get('构建已匹配分支') or [])
    todo = br if rematch or cur is None else [b for b in br if b not in done]
    if not todo:
        return _mark(m, cur or [])
    p = build_plugin(False)
    if not p:
        return _mark(m, cur or [])
    keep = [{k: v for k, v in j.items() if k != '历史'} for j in _mark(m, list(cur or []))
            if not rematch or j.get('依据') == '手动' or j.get('历史')]
    have = {j['名称'] for j in keep}
    found, tried = [], []
    for b in todo:
        try:
            hit = _match(p, [b])
        except KBError:
            continue                      # 构建平台连不上：下次再对这个分支
        tried.append(b)
        for j in hit:
            if j['名称'] not in have:
                have.add(j['名称'])
                found.append({'名称': j['名称'], '地址': j['地址'], '依据': '自动', '分支': b})
    js = keep + found
    if found or tried or rematch:
        m = load_meta(d)
        m['构建任务'] = js
        m['构建已匹配分支'] = sorted((set(m.get('构建已匹配分支') or []) | set(tried)) if not rematch else set(tried))
        save_meta(d, m)
        if found:
            log(d, '构建', f'按分支 {"、".join(tried)} 匹配到构建任务 {"、".join(j["名称"] for j in found)}（追加，原有任务保留）')
    return _mark(m, js)


def set_period_jobs(name, jobs, by='用户（网页）'):
    """手动改这一期对应的任务（任务名或地址，可多个；空 = 重新按分支自动匹配）。"""
    d = period_dir(name)
    jobs = [x.strip() for x in (jobs if isinstance(jobs, list) else re.split(r'[\s,，]+', jobs or '')) if x.strip()]
    if not jobs:
        js = period_jobs(d.name, rematch=True)
        return f'已按分支重新匹配：{"、".join(j["名称"] for j in js) or "没匹配到（分支没写，或任务名里没有分支名）"}'
    p = build_plugin()
    out = []
    for x in jobs:
        if x.startswith('http'):
            nm = re.sub(r'/+$', '', x.split('/api/')[0]).rsplit('/job/', 1)[-1]
            out.append({'名称': nm, '地址': x.split('/api/')[0].rstrip('/') + '/', '依据': '手动'})
        else:
            c = _plugin_json(p['id'], ['jobs', x], by) or []
            hit = next((j for j in c if j['名称'] == x), c[0] if c else None)
            if not hit:
                raise KBError(f'构建平台上没找到任务「{x}」')
            out.append({'名称': hit['名称'], '地址': hit['地址'], '依据': '手动'})
    m = load_meta(d)
    br = [b for b in period_branches(d, m) if b not in _old(m)]
    for j in out:
        j['分支'] = br[0] if br else ''
    old = [j for j in _mark(m, m.get('构建任务') or []) if j.get('历史') and j['名称'] not in {x['名称'] for x in out}]
    m['构建任务'] = out + [{k: v for k, v in j.items() if k != '历史'} for j in old]      # 只换当前分支的任务，老分支的保留
    save_meta(d, m)
    log(d, '构建', f'构建任务改为 {"、".join(j["名称"] for j in out)}（{by}）' + (f'；老分支的任务保留：{"、".join(j["名称"] for j in old)}' if old else ''))
    return f'这一期的构建任务：{"、".join(j["名称"] for j in out)}' + (f'（老分支的 {"、".join(j["名称"] for j in old)} 保留）' if old else '')


def _builds(p, job, n):
    k = (p['id'], job, n)
    hit = _CACHE.get(k)
    if hit and time.time() - hit[0] < CACHE_SEC:
        return hit[1]
    r = _plugin_json(p['id'], ['builds', job, str(n)], '构建列表')
    _CACHE[k] = (time.time(), r)
    return r


def _bug_builds():
    """Bug 平台上记的提测包（构建地址）→ 这个包上的 Bug 编号。"""
    out = {}
    try:
        for e in zt_load()['Bug'].values():
            u = (e.get('Jenkins') or '').split('/api/')[0].rstrip('/')
            if re.search(r'/\d+$', u):
                out.setdefault(u + '/', []).append(e['编号'])
    except (KBError, OSError):
        pass
    return out


def period_builds(name, n=10, fresh=False):
    """期页面「构建」板块：{插件, 分支, 任务: [{名称, 地址, 依据, 构建: […], 错误}]}；每次构建附上这个包上的 Bug。"""
    d = period_dir(name)
    p = build_plugin(False)
    m = load_meta(d)
    res = {'插件': p['名称'] if p else '', '分支': period_branches(d, m), '历史分支': sorted(_old(m)), '任务': [], '时间': now()}
    if not p:
        return res
    if fresh:
        _CACHE.clear()
    bugs = _bug_builds()
    for j in period_jobs(d.name):
        x = dict(j, 构建=[])
        try:
            r = _builds(p, j['地址'] or j['名称'], n)
            for b in r.get('构建') or []:
                b['Bug'] = bugs.get(b['地址'].rstrip('/') + '/', [])
            x['构建'] = r.get('构建') or []
        except KBError as e:
            x['错误'] = str(e)
        res['任务'].append(x)
    return res


def builds_text(name, n=5):
    """给 AI 看：这一期的构建（找测试包、对版本、取 mapping 时用）。"""
    r = period_builds(name, n)
    if not r['插件']:
        return '还没有挂载构建插件（如 Jenkins）'
    if not r['任务']:
        return f'这一期没匹配到构建任务（分支：{"、".join(r["分支"]) or "未写"}）；在网页期页面「构建」里手动指定'
    out = []
    for j in r['任务']:
        out.append(f'## {j["名称"]}（{j["依据"]}' + (f' · 分支 {j["分支"]}' if isinstance(j.get('分支'), str) and j['分支'] else '')
                   + ('，老分支：已换到新分支，只供查历史构建 / mapping' if j.get('历史') else '') + f'）{j["地址"]}' + (f'\n{j["错误"]}' if j.get('错误') else ''))
        for b in j['构建']:
            apk = [a for a in b['产物'] if a['类型'] == '安装包']
            mp = next((a['地址'] for a in b['产物'] if a['类型'] == 'mapping'), '')
            out.append(f'- #{b["编号"]} {b["结果"]} {b["时间"]} 版本 {b["版本"] or "?"} 提交 {(b["提交"] or "?")[:10]} {b["地址"]}'
                       + (f'；这个包上的 Bug：{"、".join(b["Bug"])}' if b.get('Bug') else '')
                       + (f'\n  安装包（{len(apk)} 个）：{apk[0]["地址"]}' + ('' if len(apk) == 1 else ' 等') if apk else '')
                       + (f'\n  mapping：{mp}' if mp else ''))
    return '\n'.join(out)


def build_mapping(url, version, by='用户（网页）'):
    """把某次构建的 mapping 归档到 ANR / 崩溃（版本用构建的完整版本），之后这个版本的崩溃日志自动还原。"""
    from kblib.anr.manual import anr_mapping_url
    if not version:
        raise KBError('这次构建认不出版本号，在 ANR / 崩溃页「＋ 归档 mapping」里手动填版本')
    return anr_mapping_url(version, url, by)


def anr_auto_mapping(version, by='自动'):
    """ANR / 崩溃样本的版本没有 mapping：在各期构建里找同版本的构建，取它的 mapping 归档。找不到返回空。"""
    p = build_plugin(False)
    if not p or not version:
        return ''
    from kblib.core.periods import period_names
    for n in period_names():
        try:
            jobs = period_jobs(n)
        except KBError:
            continue
        for j in jobs:
            try:
                r = _builds(p, j['地址'] or j['名称'], 30)
            except KBError:
                continue
            for b in r.get('构建') or []:
                mp = next((a['地址'] for a in b['产物'] if a['类型'] == 'mapping'), '')
                if mp and b.get('版本') == version:
                    return build_mapping(mp, version, by) + f'\n（来自 {j["名称"]} #{b["编号"]}）'
    return ''
