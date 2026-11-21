# From 大大王.DT
"""期 / 事项的目录、元数据、记录、状态。"""
from kblib.infra import devlock
import filecmp
import json
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from kblib.core.common import (
    CATS,
    DOC_EXT,
    HIST,
    IMG_EXT,
    INBOX,
    KBError,
    NOTE_SUFFIX,
    PERIODS,
    REPOS_FILE,
    STATUSES,
    SYS,
    Snap,
    actor_label,
    current_ctx,
    current_project,
    current_user,
    now,
    safe_name,
    snap,
    snap_diff,
)
from kblib.core import db as _db
from kblib import lazy
_notes = lazy('kblib.core.notes')      # 上层模块：用到时才 import（不形成循环）
_search = lazy('kblib.core.search')      # 上层模块：用到时才 import（不形成循环）
_plugins = lazy('kblib.plugins.registry')      # 上层模块：用到时才 import（不形成循环）
_devprep = lazy('kblib.ai.devprep')      # 上层模块：用到时才 import（不形成循环）
_devsession = lazy('kblib.ai.devsession')      # 上层模块：用到时才 import（不形成循环）
_talk = lazy('kblib.ai.talk')      # 上层模块：删期时列出 / 停掉这一期在跑的后台会话
_users = lazy('kblib.core.users')
_bug_index = lazy('kblib.bugs.index')      # 上层模块：用到时才 import（不形成循环）
_anr_store = lazy('kblib.anr.store')      # 上层模块：用到时才 import（不形成循环）


def init():
    for p in (SYS, PERIODS, INBOX):
        p.mkdir(parents=True, exist_ok=True)
    if not REPOS_FILE.exists():
        REPOS_FILE.write_text('{}', 'utf-8')
    _search.db().close()


# 已删除的期（墓碑）：删期后后台任务（插件回写、管家巡检、资料整理）晚到的写入可能把目录又建出来，
# 列表里不再认它，tomb_sweep 把重建出来的目录收走；用同名新建一期时清掉墓碑。
TOMB = SYS / '已删除期.json'


def _tombs():
    try:
        return json.loads(TOMB.read_text('utf-8')) if TOMB.exists() else {}
    except (OSError, ValueError):
        return {}


def _save_tombs(t):
    SYS.mkdir(parents=True, exist_ok=True)
    TOMB.write_text(json.dumps(t, ensure_ascii=False, indent=1), 'utf-8')


def is_deleted(name):
    return bool(name) and name in _tombs()


def tomb_sweep():
    """删掉的期又被后台写回来了（目录重新出现）：移到废纸篓，并记一笔。网页后台循环定时跑。"""
    out = []
    for n, x in _tombs().items():
        d = PERIODS / n
        if d.is_dir():
            dest = Path.home() / '.Trash' / f'{n}（删除后被后台写回 {time.strftime("%Y%m%d-%H%M%S")}）'
            try:
                dest.parent.mkdir(exist_ok=True)
                shutil.move(str(d), str(dest))
            except OSError:
                shutil.rmtree(d, ignore_errors=True)
            out.append(n)
    return out


def period_names(all_projects=False):
    """期 / 事项名（按创建时间）。网页请求、AI 会话里只列当前项目的（REQ-003）；后台线程没有用户上下文，列全部。"""
    if not PERIODS.exists():
        return []
    tomb = _tombs()
    ds = [p for p in PERIODS.iterdir() if p.is_dir() and p.name not in tomb]
    names = [p.name for p in sorted(ds, key=lambda p: load_meta(p).get('创建时间', ''))]
    scope = '' if all_projects else period_scope()
    if scope:
        proj = {r['name']: r['project'] for r in _db.period_rows()}
        names = [n for n in names if (proj.get(n) or 'default') in scope]
    return names


def _check_visible(name):
    """网页登录人 / AI 会话访问不属于自己项目的期：拒绝（REQ-003 REQ-005）。"""
    c = current_ctx()
    if c.get('via') not in ('网页', 'AI') or not c.get('user'):
        return
    from kblib.core import authz
    if not authz.can(c, '看', name)[0]:
        raise authz.Denied(authz.MSG_PROJ)


def period_scope():
    """当前请求能看的项目集合（空 = 不过滤）：网页看当前项目；会话看它的项目；个人令牌看持有者参与的项目。"""
    c = current_ctx()
    if c.get('via') not in ('网页', 'AI') or c.get('all_projects'):
        return set()
    if c.get('project'):
        return {c['project']}
    if c.get('user'):
        from kblib.core import authz
        return set(authz.visible_projects(c['user'])) or {'-'}
    return set()


def period_project(name):
    r = _db.period_row(name)
    return (r or {}).get('project') or 'default'


def period_dir(name):
    if not name:
        raise KBError('请指定期名')
    d = PERIODS / safe_name(name)
    if d.name in _tombs():
        raise KBError(f'「{d.name}」已删除')
    if d.is_dir():
        _check_visible(d.name)
        return d
    cands = [n for n in period_names() if name in n]
    if len(cands) == 1:
        return PERIODS / cands[0]
    if not cands:
        raise KBError(f'没有这一期：{name}。现有：{"、".join(period_names()) or "（空）"}')
    raise KBError(f'「{name}」匹配到多期：{"、".join(cands)}，请写全名')


_EXPORTED = {}        # 期信息.json 路径 → 本进程最近一次导出后的 mtime（区分「自己导出的」和「有人直接改了文件」）


def _meta_default(d):
    return {'名称': d.name, '说明': '', '状态': '收集中', '创建时间': now(), '关联仓库': [], '状态历史': []}


def load_meta(d):
    """期信息在数据库（periods 表），期信息.json 是导出视图（REQ-008）。
    还没进库的（老数据、别处拷来的期）读文件并导入；有人直接改了文件（比库里新）以文件为准。"""
    f = d / '期信息.json'
    r = _db.period_row(d.name)
    try:
        fm = f.stat().st_mtime if f.exists() else 0
    except OSError:
        fm = 0
    if fm and (r is None or (fm > _EXPORTED.get(str(f), 0) + 1 and fm > (r.get('updated') or 0) + 1)):
        try:
            m = json.loads(f.read_text('utf-8'))
        except (OSError, ValueError):
            m = None
        if isinstance(m, dict):
            if r is None or m != r['meta']:
                proj, owner = (None, None) if r else _default_owner()
                gone = [k for k in r['meta'] if k not in m] if r else []   # 以文件为准：文件里删掉的字段库里也删
                _db.period_save(d.name, m, list(m), gone, by='期信息.json', project=proj, owner=owner)
            _EXPORTED[str(f)] = fm
            return snap(m)
    if r is not None:
        return snap(r['meta'])
    return snap(_meta_default(d))


def _default_owner():
    """新建 / 首次导入的期：归当前项目（没有就默认项目），负责人是当前用户（没有就本机用户）。"""
    try:
        return _users.default_project_for(current_project()), current_user() or _users.local_user()
    except Exception:
        return '', ''


def _export_meta(d, m):
    f = d / '期信息.json'
    tmp = f.with_name('.期信息.json.tmp')
    tmp.write_text(json.dumps(m, ensure_ascii=False, indent=2), 'utf-8')
    tmp.replace(f)
    try:
        _EXPORTED[str(f)] = f.stat().st_mtime
    except OSError:
        pass


def save_meta(d, m, version=None):
    """写入走数据库事务、按字段合并（只改这次改过的键）；version 给了就做乐观锁检查（REQ-030）。写完导出 期信息.json。"""
    m['更新时间'] = now()
    changed, removed = snap_diff(m)
    proj = owner = None
    if _db.period_row(d.name) is None:
        proj, owner = _default_owner()
    merged, _ver = _db.period_save(d.name, m, changed, removed, by=actor_label() or 'AI', version=version,
                                   project=proj, owner=owner)
    if d.is_dir():
        _export_meta(d, merged)
    if isinstance(m, Snap):
        m.clear()
        m.update(merged)
        m._base = json.loads(json.dumps(merged, ensure_ascii=False, default=str))
    try:
        _db.event_add('period', (_db.period_row(d.name) or {}).get('project', ''), {'name': d.name})
    except Exception:
        pass


def period_info(name):
    """期的平台属性：项目、负责人、版本号（REQ-002 REQ-003 REQ-030）。"""
    r = _db.period_row(name)
    if r is None:
        d = PERIODS / name
        if d.is_dir():
            load_meta(d)
            r = _db.period_row(name)
    return {'项目': (r or {}).get('project', ''), '负责人': (r or {}).get('owner', ''), '版本号': (r or {}).get('version', 0)}


def period_set_owner(name, owner=None, project=None, version=None):
    d = period_dir(name)
    _db.period_set_attr(d.name, project=project, owner=owner, version=version, by=actor_label() or '')
    bits = ([f'负责人 → {owner}'] if owner is not None else []) + ([f'项目 → {project}'] if project is not None else [])
    log(d, '归属', '，'.join(bits))
    return f'{d.name}：' + '，'.join(bits)


def log(d, action, content, actor=None):
    """记录：进数据库 records 表，同时追加到 记录.md（导出视图，格式不变）。操作人取当前用户上下文（REQ-004）。"""
    if not d.is_dir():           # 期已删除：后台晚到的记录不写（写了会把目录又建出来）
        return
    actor = actor or actor_label()
    c = current_ctx()
    if actor and c.get('via', '网页') == '网页' and c.get('user'):
        content = content.replace('用户（网页）', actor)
    t = now()
    try:
        _db.record_add(d.name, t, action, content, actor or '')
    except Exception:
        pass
    f = d / '记录.md'
    if not f.exists():
        f.write_text(f'# {d.name} · 记录\n\n> 知识库自动追加：本期所有材料变动、状态变化、开发中的关键决定。\n\n', 'utf-8')
    with f.open('a', encoding='utf-8') as fp:
        fp.write(f'- {t} ｜ {action} ｜ {content}\n')


def create_period(name, summary=''):
    init()
    d = PERIODS / safe_name(name)
    t = _tombs()
    if d.name in t:              # 同名重新建：先收走删除后被写回的残留目录，再去掉墓碑
        tomb_sweep()
        t.pop(d.name, None)
        _save_tombs(t)
    if d.exists():
        raise KBError(f'已存在：{d.name}')
    _db.period_delete(d.name)            # 库里残留的同名旧行（目录被手工删掉的）不并进来
    for c in CATS:
        (d / c).mkdir(parents=True)
    save_meta(d, {'名称': d.name, '说明': summary, '状态': '收集中', '创建时间': now(), '关联仓库': [],
                  '状态历史': [{'时间': now(), '状态': '收集中', '备注': '创建'}]})
    log(d, '创建', '说明：' + (summary or '无'))
    return f'已创建一期：{d.name}\n目录：{d}\n状态：收集中（开发需你本人运行 kb approve "{d.name}"）'


# 事项：不是产品需求、但要用知识库推动的工作（用户上报 ANR、加埋点、线上问题、优化、调研、其他）。
# 和「期」放在同一个目录（期/<名称>/），资料、文字资料、确认、开发批准、手机、日志都一样用；
# 区别只在期信息里「类型」=「事项」，默认用轻量流程，网页上单独列出。
TASK_KINDS = ['ANR/崩溃', '埋点', '线上问题', '优化', '调研', '其他']
TASK_PRI = ['高', '中', '低']


def is_task(m):
    return (m or {}).get('类型') == '事项'


def _guess_task_kind(text):
    t = (text or '').lower()
    for k, pat in (('ANR/崩溃', r'anr|崩溃|crash|闪退|卡死|无响应|oom|native'), ('埋点', r'埋点|上报|打点|统计|track|event'),
                   ('线上问题', r'线上|用户反馈|用户上报|客诉|生产'), ('优化', r'优化|性能|内存|耗电|启动速度|卡顿|重构'),
                   ('调研', r'调研|评估|预研|可行性')):
        if re.search(pat, t):
            return k
    return '其他'


def create_task(name, summary='', kind='', source='', priority='', bugs=None, by='Claude'):
    """新建一个事项；bugs 是 Bug 编号，会一并归到这个事项。"""
    kind = kind or _guess_task_kind(f'{name} {summary}')
    if kind not in TASK_KINDS:
        raise KBError(f'事项类型只能是：{"、".join(TASK_KINDS)}')
    if priority and priority not in TASK_PRI:
        raise KBError(f'优先级只能是：{"、".join(TASK_PRI)}')
    create_period(name, summary)
    d = PERIODS / safe_name(name)
    m = load_meta(d)
    m.update({'类型': '事项', '事项类型': kind, '来自': source or '', '优先级': priority or '中'})
    save_meta(d, m)
    log(d, '事项', f'类型 {kind}　优先级 {m["优先级"]}' + (f'　来自 {source}' if source else '') + f'（{by}）')
    out = [f'已创建事项：{d.name}（{kind}，优先级 {m["优先级"]}）', f'目录：{d}',
           '资料照常用 kb_add_material / kb_add_note / kb_write_doc 放进来（日志、截图、用户反馈、埋点口径……）；'
           f'开始处理需要你本人在网页点「开始处理」或运行 kb approve "{d.name}"，默认按轻量流程：定位 → 确认改法 → 修改自测 → 写结论。']
    for b in bugs or []:
        try:
            out.append(_bug_index.bug_assign(str(b), d.name, by=by))
        except KBError as e:
            out.append(f'Bug {b}：{e}')
    return '\n'.join(out)


def delete_period(name, by='用户（网页）'):
    """网页「删除」：把没用的期 / 事项从知识库里整个去掉（只有用户本人在网页上能做，不作为 MCP 工具）。
    目录移到系统废纸篓（误删还能从废纸篓找回），并清掉别处对它的引用：归到它的 Bug 回到待归纳、关联的 ANR 问题取消关联、
    它占着的手机释放、检索索引里它的文档移除。还在跑的后台会话 / 插件任务一并停掉；删后留墓碑，
    后台任务晚到的写入不会再把这一期建回来（见 tomb_sweep）。"""
    d = period_dir(name)
    out = []
    stopped = _talk.period_bg_stop_all(d.name, by=by)
    if stopped:
        out.append(f'停掉了在跑的 {"、".join(stopped)}')
    t = _tombs()                 # 先立墓碑再挪目录：挪的过程中后台写入也不会被当成一期
    t[d.name] = {'时间': now(), '删除人': by}
    _save_tombs(t)
    n = 0
    data = _bug_index.zt_load()                     # 保存时按条目合并，和同步同时进行也不会互相覆盖
    for e in (data.get('Bug') or {}).values():
        o = e.get('归属') or {}
        if o.get('名称') == d.name and o.get('类型') in ('期', '事项'):
            e['归属'] = {'类型': '待归纳', '名称': '', '依据': f'原来归属的「{d.name}」已删除', '时间': now()}
            n += 1
    if n:
        _bug_index.zt_save(data)
        out.append(f'{n} 个 Bug 回到待归纳')
    with _anr_store._AR_LOCK:
        probs = _anr_store._ar_probs()
        k = [pid for pid, e in probs.items() if e.get('事项') == d.name]
        for pid in k:
            probs[pid]['事项'] = ''
        if k:
            _anr_store._ar_save_probs(probs)
            out.append(f'{len(k)} 个 ANR / 崩溃问题取消关联')
    try:
        with devlock.locked() as dv:
            ph = [sn for sn, c in dv.items() if isinstance(c, dict) and c.get('期') == d.name]
            for sn in ph:
                dv.pop(sn)
        if ph:
            out.append(f'释放手机 {"、".join(ph)}')
    except OSError:
        pass
    _devsession._PROCS.pop(d.name, None)
    _devsession._TRACK.pop(d.name, None)
    trash = Path.home() / '.Trash'
    dest = trash / f'{d.name}（知识库已删除 {time.strftime("%Y%m%d-%H%M%S")}）'
    try:
        trash.mkdir(exist_ok=True)
        shutil.move(str(d), str(dest))
        where = f'目录已移到废纸篓：{dest.name}'
    except OSError:
        shutil.rmtree(d)
        where = '目录已删除'
    try:
        _db.period_delete(d.name)
    except Exception:
        pass
    try:
        _search.index_docs()
    except Exception:
        pass
    return f'已删除「{d.name}」：{where}' + (f'；{"；".join(out)}' if out else '')


def update_task(name, kind=None, source=None, priority=None, summary=None, by='用户（网页）'):
    d = period_dir(name)
    m = load_meta(d)
    if not is_task(m):
        raise KBError(f'「{d.name}」不是事项')
    ch = []
    for key, v, allowed in (('事项类型', kind, TASK_KINDS), ('来自', source, None), ('优先级', priority, TASK_PRI), ('说明', summary, None)):
        if v is None or v == m.get(key, ''):
            continue
        if allowed and v not in allowed:
            raise KBError(f'{key}只能是：{"、".join(allowed)}')
        m[key] = v
        ch.append(f'{key} → {v or "空"}')
    if ch:
        save_meta(d, m)
        log(d, '事项', '；'.join(ch) + f'（{by}）')
    return f'已更新「{d.name}」：' + ('；'.join(ch) or '没有变化')


def guess_category(p: Path):
    n = p.name.lower()
    ext = p.suffix.lower()
    word = lambda w: re.search(rf'(^|[^a-z]){w}([^a-z]|$)', n)
    if re.search(r'接口|swagger|openapi|postman|协议|字段|错误码', n) or word('api'):
        return '接口文档'
    if ext == '.rp' or re.search(r'原型|axure|交互稿|线框', n):
        return '需求'  # 原型属于需求，设计稿才进设计图
    if p.is_dir() and ((p / 'resources').exists() or (p / 'start.html').exists()):
        return '需求'  # Axure 导出目录
    if (ext in IMG_EXT or ext in {'.fig', '.sketch', '.xd', '.psd'}
            or re.search(r'设计|视觉|切图|标注|高保真|figma|蓝湖|lanhu', n) or word('ui')):
        return '设计图'
    if re.search(r'需求|prd|功能|产品|story', n):
        return '需求'
    return '其他资料'


def _same(a: Path, b: Path):
    if a.is_file() and b.is_file():
        return filecmp.cmp(a, b, shallow=False)
    return False


PREVIEW_EXT = {'.psd', '.psb', '.tif', '.tiff', '.heic'}      # 网页和 AI 看不了的设计稿格式：放进来时转一张 PNG 预览


def design_preview(path):
    """PSD 等转 PNG 预览（同目录 <原名>.png，macOS 自带 sips，合并图层后的效果图）。转不了返回空。"""
    p = Path(path)
    if p.suffix.lower() not in PREVIEW_EXT:
        return ''
    out = p.with_name(p.name + '.png')
    if out.exists() and out.stat().st_mtime >= p.stat().st_mtime:
        return str(out)
    import subprocess
    if shutil.which('sips'):                      # macOS 自带
        cmd = ['sips', '-s', 'format', 'png', str(p), '--out', str(out)]
    elif shutil.which('magick') or shutil.which('convert'):     # Linux 服务器：ImageMagick（[0] = 合并后的效果图）
        cmd = [shutil.which('magick') or shutil.which('convert'), f'{p}[0]', str(out)]
    else:
        return ''
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    return str(out) if r.returncode == 0 and out.exists() else ''


def design_previews(d):
    """这一期设计图里还没预览的 PSD 等补转一遍。返回新生成的。"""
    out = []
    for p in (Path(d) / '设计图').rglob('*'):
        if p.is_file() and p.suffix.lower() in PREVIEW_EXT and HIST not in p.parts and not p.with_name(p.name + '.png').exists():
            if design_preview(p):
                out.append(p.name)
    return out


def add_material(period, src, category=None, note='', sub='', reindex=True):
    d = period_dir(period)
    src = Path(src).expanduser().resolve()
    if not src.exists():
        raise KBError(f'文件不存在：{src}')
    if src == d or d in src.parents:
        raise KBError(f'{src} 已经在这一期里了')
    if src.name == _notes.NOTES_FILE:
        raise KBError(f'「{_notes.NOTES_FILE}」是文字资料自动生成的文件名，请改个文件名再放入')
    cat = category or guess_category(src)
    if cat not in CATS:
        raise KBError(f'分类只能是：{" / ".join(CATS)}')
    folder = d / cat / sub if sub else d / cat
    folder.mkdir(parents=True, exist_ok=True)
    rel = f'{cat}/{sub}/' if sub else f'{cat}/'
    dest = folder / src.name
    move = INBOX.resolve() in src.parents or SYS.resolve() in src.parents
    extra = ''
    if dest.exists():
        if _same(src, dest):
            if move:
                src.unlink()
            return f'{rel}{src.name} 内容相同，未变动'
        h = folder / HIST
        h.mkdir(exist_ok=True)
        old = h / f'{dest.stem}.{datetime.now():%Y%m%d-%H%M%S}{dest.suffix}'
        shutil.move(str(dest), str(old))
        extra = f'，旧版移至 {rel}{HIST}/{old.name}'
    if move:
        shutil.move(str(src), str(dest))
    elif src.is_dir():
        shutil.copytree(src, dest)
    else:
        shutil.copy2(src, dest)
    action = '更新材料' if extra else '新增材料'
    log(d, action, f'{rel}{src.name}{extra}' + (f'（{note}）' if note else ''))
    pv = design_preview(dest) if dest.is_file() else ''
    if pv:
        extra += f'，已生成预览图 {Path(pv).name}'
    stats = _search.index_docs() if reindex else ''
    hint = ''
    if cat == '设计图' and not sub and dest.suffix.lower() in IMG_EXT:
        hint = f'\n提示：图片还没有文字说明，可用 Read 看图后 kb_annotate_image 补说明，便于检索。'
    return f'{action}：{d.name}/{rel}{src.name}{extra}\n路径：{dest}\n{stats}{hint}'


def write_doc(period, category, filename, content, note=''):
    period_dir(period)
    if category not in CATS:
        raise KBError(f'分类只能是：{" / ".join(CATS)}')
    name = safe_name(filename)
    if Path(name).suffix.lower() not in DOC_EXT:  # 「4.32」「v1.0」这类不是扩展名
        name += '.md'
    tmp = SYS / 'tmp'
    tmp.mkdir(exist_ok=True)
    f = tmp / name
    f.write_text(content, 'utf-8')
    return add_material(period, f, category, note)


def annotate_image(path, description):
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        raise KBError(f'文件不存在：{p}')
    if p.suffix.lower() not in IMG_EXT:
        raise KBError('只能给图片写说明')
    side = p.with_name(p.name + NOTE_SUFFIX)
    side.write_text(f'# 图片说明：{p.name}\n\n{description.strip()}\n', 'utf-8')
    try:
        rel = p.relative_to(PERIODS.resolve())
        log(PERIODS / rel.parts[0], '图片说明', '/'.join(rel.parts[1:]))
    except ValueError:
        pass
    _search.index_docs()
    return f'已保存说明：{side}'


def set_status(period, status, note='', by_human=False):
    d = period_dir(period)
    m = load_meta(d)
    cur = m.get('状态', '收集中')
    if status not in STATUSES:
        raise KBError(f'状态只能是：{" / ".join(STATUSES)}')
    if not by_human:
        if status == '已批准开发':
            raise KBError('「已批准开发」只能由用户本人在终端运行 kb approve 设置，AI 不能代为批准。')
        if status == '已完成' and not is_task(m) and (m.get('全栈') or m.get('各端')):
            raise KBError('全栈期的「已完成」由用户在网页上验收（新产品是发布清单勾完）决定，AI 不能改；做完用 kb_stack_report 汇报就行')
        allowed = {'开发中': ('已批准开发', '开发中'), '已完成': ('开发中',)}
        if status in allowed and cur not in allowed[status]:
            raise KBError(f'当前状态是「{cur}」，不能直接改为「{status}」。')
    m['状态'] = status
    m.setdefault('状态历史', []).append({'时间': now(), '状态': status, '备注': note,
                                       '操作人': '用户' if by_human else 'AI'})
    save_meta(d, m)
    log(d, '状态', f'{cur} → {status}' + (f'（{note}）' if note else '') + ('' if by_human else '［AI］'))
    msg = f'{d.name}：{cur} → {status}'
    if status == '已完成' and cur != '已完成':
        root = m.get('开发仓库') or ''
        branch = _devprep._git(Path(root), 'branch', '--show-current').stdout.strip() if root and Path(root).is_dir() else ''
        plug = _plugins.run_plugins('done', d, root, branch, m.get('SDD目录', ''))
        if plug:
            msg += f'\n\n【开发完成时已运行的插件】\n{plug}'
            if '个文件改动' in plug:
                msg += '\n插件改动了仓库里的文件：请检查后按仓库惯例提交（如单独一个提交）。'
    return msg


def add_log(period, note):
    d = period_dir(period)
    log(d, '记录', note)
    return f'已记录到 {d.name}/记录.md'


# ---------------------------------------------------------------- 归档：做完一段时间的期 / 事项不再出现在列表里（目录、资料、记录都不动，检索照常）
ARCHIVE_DAYS = 14          # 默认：已完成满 14 天自动归档；设置.json「归档天数」改，0 = 不自动


def archive_days():
    from kblib.core.common import load_settings
    v = load_settings().get('归档天数', ARCHIVE_DAYS)
    try:
        return max(0, int(v))
    except (TypeError, ValueError):
        return ARCHIVE_DAYS


def done_time(m):
    """最后一次标为已完成的时间（没有状态历史就用更新时间）。"""
    for x in reversed(m.get('状态历史') or []):
        if x.get('状态') == '已完成':
            return x.get('时间', '')
    return m.get('更新时间', '') if m.get('状态') == '已完成' else ''


def is_archived(m, days=None):
    """归档了吗：只有已完成的才会归档；手动归档的一直算；否则已完成（或上次恢复）满 N 天自动算。"""
    if (m or {}).get('状态') != '已完成':
        return False
    if m.get('归档') == '手动':
        return True
    days = archive_days() if days is None else days
    since = max(done_time(m), m.get('恢复时间') or '')
    if not days or not since:
        return False
    from datetime import timedelta
    return since < (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d %H:%M')


def period_archive(name, on=True, by='用户（网页）'):
    """手动归档 / 恢复。恢复 = 重新出现在列表里，再满 N 天才会自动归档。"""
    d = period_dir(name)
    m = load_meta(d)
    if on:
        if m.get('状态') != '已完成':
            raise KBError('只有已完成的期 / 事项才能归档；还在推进的要先把状态改成已完成')
        m['归档'] = '手动'
    else:
        if not is_archived(m):
            return '没有归档，不用恢复'
        m.pop('归档', None)
        m['恢复时间'] = now()
    save_meta(d, m)
    log(d, '归档', ('归档：不再出现在列表里，在「📦 归档」页能找到' if on else '恢复：重新出现在列表里') + f'（{by}）')
    from kblib.steward import behavior          # 上层模块：用到时才 import
    behavior.emit('archive' if on else 'unarchive', d.name, by=by)
    return f'「{d.name}」已' + ('归档' if on else '恢复')


def unarchive_if(name, why):
    """归档的期 / 事项又有动静（新 Bug / ANR、你在它的页面里说话）：自动恢复到列表里。→ 是否恢复了"""
    try:
        d = period_dir(name)
    except KBError:
        return False
    m = load_meta(d)
    if not is_archived(m):
        return False
    m.pop('归档', None)
    m['恢复时间'] = now()
    save_meta(d, m)
    log(d, '归档', f'自动恢复到列表：{why}')
    return True


def archive_list(q='', kind=''):
    """「📦 归档」页：归档的期 / 事项，按完成时间倒序。q 搜名字、简介、结论；kind 期 / 事项。"""
    q = (q or '').strip().lower()
    days = archive_days()
    out = []
    for n in period_names():
        d = PERIODS / n
        m = load_meta(d)
        if not is_archived(m, days):
            continue
        k = '事项' if is_task(m) else '期'
        if kind and kind != k:
            continue
        concl = next((x for x in (d / '其他资料' / '结论.md', d / '全栈' / 'spec.md') if x.exists()), None)
        if q:
            hay = f'{n}\n{m.get("说明", "")}\n{m.get("事项类型", "")}'.lower()
            if q not in hay and not (concl and q in concl.read_text('utf-8', 'replace')[:20000].lower()):
                continue
        out.append({'名称': n, '类型': k, '事项类型': m.get('事项类型', ''), '说明': m.get('说明', ''), '完成时间': done_time(m),
                    '手动': m.get('归档') == '手动', '结论': str(concl) if concl else ''})
    return sorted(out, key=lambda x: x['完成时间'], reverse=True)
