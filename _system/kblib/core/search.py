# From 大大王.DT
"""全文索引（SQLite FTS）和检索 / 阅读。"""
import re
import sqlite3
from pathlib import Path
from kblib.core.common import (
    BASE,
    CATS,
    CHUNK_CHARS,
    DB,
    DOC_EXT,
    HIST,
    IMG_EXT,
    KBError,
    MAX_CODE_BYTES,
    NOTE_SUFFIX,
    PERIODS,
    ROOT,
    SYS,
    read_text,
)
from kblib.core.textx import extract
from kblib.core import fuzzy as _fuzzy
from kblib.core.periods import load_meta, period_dir, period_names
from kblib.core.repos import code_wanted, load_repos, repo_files
from kblib import lazy
_baseinfo = lazy('kblib.core.baseinfo')      # 上层模块：用到时才 import（不形成循环）


def db():
    SYS.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB, timeout=30)
    c.execute('pragma journal_mode=wal')
    c.executescript('''
    create table if not exists files(path text primary key, mtime real, size integer, kind text,
                                     period text, cat text, repo text);
    create table if not exists chunk(id integer primary key, path text, kind text, period text,
                                     cat text, repo text, start integer, end integer, content text);
    create index if not exists chunk_path on chunk(path);
    create index if not exists chunk_kind on chunk(kind);
    create virtual table if not exists fts using fts5(content, content='chunk', content_rowid='id',
                                                      tokenize='trigram');
    create trigger if not exists chunk_ai after insert on chunk begin
      insert into fts(rowid, content) values (new.id, new.content); end;
    create trigger if not exists chunk_ad after delete on chunk begin
      insert into fts(fts, rowid, content) values('delete', old.id, old.content); end;
    ''')
    return c


def chunks_of(text):
    out, buf, size, start, last = [], [], 0, 1, 1
    for i, line in enumerate(text.splitlines(), 1):
        for piece in [line[j:j + CHUNK_CHARS] for j in range(0, len(line), CHUNK_CHARS)] or ['']:
            if buf and size + len(piece) > CHUNK_CHARS:
                out.append((start, last, '\n'.join(buf)))
                buf, size = [], 0
            if not buf:
                start = i
            buf.append(piece)
            size += len(piece) + 1
            last = i
    if buf:
        out.append((start, last, '\n'.join(buf)))
    return [x for x in out if x[2].strip()]


def _drop(c, path):
    c.execute('delete from chunk where path=?', (path,))
    c.execute('delete from files where path=?', (path,))


def _index_one(c, p: Path, st, kind, period='', cat='', repo=''):
    path = str(p)
    _drop(c, path)
    text = extract(p)
    if text is None:
        text = f'[未解析的文件] {p.name}'
    rows = [(path, kind, period, cat, repo, s, e, body + (f'\n［文件］{p.name}' if n == 0 else ''))
            for n, (s, e, body) in enumerate(chunks_of(text) or [(1, 1, '')])]
    c.executemany('insert into chunk(path,kind,period,cat,repo,start,end,content) values (?,?,?,?,?,?,?,?)', rows)
    c.execute('insert into files values (?,?,?,?,?,?,?)', (path, st.st_mtime, st.st_size, kind, period, cat, repo))


def _sync(c, wanted, kind, repo=None):
    """wanted: {path: (Path, stat, period, cat)}；增量更新，返回 (新增/更新, 删除)。"""
    q = 'select path, mtime, size from files where kind=?' + (' and repo=?' if repo else '')
    known = {r[0]: (r[1], r[2]) for r in c.execute(q, (kind, repo) if repo else (kind,))}
    changed = 0
    for path, (p, st, period, cat) in wanted.items():
        if known.get(path) != (st.st_mtime, st.st_size):
            _index_one(c, p, st, kind, period, cat, repo or '')
            changed += 1
            if changed % 500 == 0:
                c.commit()
    removed = [p for p in known if p not in wanted]
    for p in removed:
        _drop(c, p)
    c.commit()
    return changed, len(removed)


def index_docs():
    init_dirs = PERIODS.exists()
    if not init_dirs:
        return '索引：无文档'
    wanted = {}
    for p in PERIODS.rglob('*'):
        rel = p.relative_to(PERIODS).parts
        if not p.is_file() or HIST in rel or p.name == '期信息.json' or p.name.startswith('.'):
            continue
        if len(rel) > 2 and rel[1] == '对话附件':      # 期页面对话里附的文件（ai/talk.py）：只给会话看，不进检索
            continue
        if p.suffix.lower() not in DOC_EXT:
            continue
        cat = rel[1] if len(rel) > 2 and rel[1] in CATS + ['来源快照'] else ('记录' if p.name == '记录.md' else '')
        wanted[str(p)] = (p, p.stat(), rel[0], cat)
    for p in BASE.rglob('*') if BASE.exists() else []:
        if p.is_file() and p.suffix.lower() in DOC_EXT and p != _baseinfo.BASE_FILE and not p.name.startswith('.') and HIST not in p.parts:
            wanted[str(p)] = (p, p.stat(), '', '基础')
    zmd = ROOT / 'Bug' / 'Bug索引.md'
    if zmd.exists():
        wanted[str(zmd)] = (zmd, zmd.stat(), '', 'Bug')
    amd = ROOT / 'ANR崩溃' / 'ANR崩溃索引.md'
    if amd.exists():
        wanted[str(amd)] = (amd, amd.stat(), '', 'ANR')
    with db() as c:
        changed, removed = _sync(c, wanted, 'doc')
    return f'索引：文档 {len(wanted)} 个（本次更新 {changed}，移除 {removed}）'


def index_repo(name):
    repos = load_repos()
    if name not in repos:
        raise KBError(f'没有登记这个仓库：{name}')
    root = Path(repos[name]['路径'])
    if not root.is_dir():
        raise KBError(f'仓库目录不存在：{root}')
    wanted = {}
    for p in repo_files(root):
        if not code_wanted(p.relative_to(root)):
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        if p.is_file() and st.st_size <= MAX_CODE_BYTES:
            wanted[str(p)] = (p, st, '', '')
    with db() as c:
        changed, removed = _sync(c, wanted, 'code', name)
    return f'索引：仓库 {name} 文件 {len(wanted)} 个（本次更新 {changed}，移除 {removed}）'


def reindex(target='all'):
    out = []
    if target in ('all', 'docs'):
        out.append(index_docs())
    for name, r in load_repos().items():
        if target in ('all', 'code', name):
            if isinstance(r, dict) and r.get('克隆地址') and not Path(r.get('路径') or '/nonexistent').is_dir():
                out.append(f'{name}：远程仓库还没克隆（开工时自动拉），跳过')      # 工作副本池按需拉
                continue
            out.append(index_repo(name))
    if not out:
        raise KBError(f'不认识的目标：{target}（可用 all / docs / code / 仓库名）')
    return '\n'.join(out)


def _snippet(content, terms, start):
    low = content.lower()
    idx = min([i for i in (low.find(t.lower()) for t in terms) if i >= 0] or [0])
    a = max(0, idx - 120)
    line = start + content[:idx].count('\n')
    s = content[a:idx + 220].replace('\n', ' ⏎ ')
    return line, ('…' if a else '') + s + ('…' if idx + 220 < len(content) else '')


def _query(c, terms, filters, args, limit):
    """terms：每项是一个词，或一组可互换的写法（list，组内 OR、组间 AND）。"""
    groups = [list(t) if isinstance(t, (list, tuple)) else [t] for t in terms]
    long_ = [g for g in groups if all(len(t) >= 3 for t in g)]
    short = [g for g in groups if g not in long_]
    where, params = list(filters), list(args)   # 便宜的过滤条件在前，LIKE 在后
    if long_:
        base = 'select chunk.path, kind, period, cat, repo, start, end, chunk.content from fts join chunk on chunk.id=fts.rowid'
        where.insert(0, 'fts match ?')
        params.insert(0, ' AND '.join('(' + ' OR '.join('"' + t.replace('"', '""') + '"' for t in g) + ')' for g in long_))
        order = "order by (kind='doc') desc, (cat='记录'), bm25(fts)"
    else:
        base = 'select path, kind, period, cat, repo, start, end, content from chunk'
        order = "order by (cat='记录'), chunk.id desc"
    for g in short:
        where.append('(' + ' or '.join('chunk.content like ?' for _ in g) + ')')
        params += [f'%{t}%' for t in g]
    if long_ or 'kind=?' in filters:
        sql = f'{base} where {" and ".join(where)} {order} limit ?'
        return c.execute(sql, params + [limit]).fetchall()
    # 只有短词（走 LIKE 扫表）：先扫文档（小），不够再扫代码，避免为了「文档优先」把十几万个代码块全扫完再排序
    rows = []
    for kind in ('doc', 'code'):
        o = order if kind == 'doc' else 'order by chunk.id desc'     # 代码块 cat 都是空：按 id 倒序可以边扫边停
        sql = f'{base} where {" and ".join(["kind=?"] + where)} {o} limit ?'
        rows += c.execute(sql, [kind] + params + [limit - len(rows)]).fetchall()
        if len(rows) >= limit:
            break
    return rows


def _expand(c, terms, absent=()):
    """模糊改写：返回 (每个词的候选组, 说明)。同义词总是带上；拼音 / 纠错只给在库里搜不到的词。"""
    out, notes = [], []
    for t in terms:
        alts = _fuzzy.pinyin_words(c, t) + _fuzzy.synonyms(t)
        if (not alts or len(t) >= 3) and (t in absent or not _query(c, [t], [], [], 1)):
            alts = _fuzzy.corrections(c, t) + alts        # 库里没有这个词：纠错候选排在同义改写前面
        alts = list(dict.fromkeys(alts))
        if alts:
            notes.append(f'{t}→{"/".join(alts[:3])}')
        out.append([t] + alts)
    return out, notes


def search_hits(query, period=None, category=None, scope='all', repo=None, limit=10):
    terms = [t for t in re.split(r'\s+', (query or '').strip()) if t]
    if not terms:
        raise KBError('查询词为空')
    index_docs()
    filters, args = [], []
    if scope in ('docs', 'code'):
        filters.append('kind=?')
        args.append('doc' if scope == 'docs' else 'code')
    if period:
        filters.append('period=?')
        args.append(period_dir(period).name)
    if category:
        filters.append('cat=?')
        args.append(category)
    if repo:
        filters.append('repo=?')
        args.append(repo)
    limit = max(1, min(int(limit or 10), 50))
    shown = list(terms)
    with db() as c:
        rows = _query(c, terms, filters, args, limit)
        mode = '全部词'
        seen = {(r[0], r[5]) for r in rows}

        def add(more):
            for r in more:
                if (r[0], r[5]) not in seen and len(rows) < limit:
                    seen.add((r[0], r[5]))
                    rows.append(r)
        if len(rows) < limit:              # 不够数：同义词 / 拼音 / 纠错改写后补在原词结果后面
            try:
                _fuzzy.ensure_vocab(c)
                groups, notes = _expand(c, terms, terms if not rows and len(terms) == 1 and not filters else ())
            except sqlite3.Error:
                groups, notes = [], []
            if notes:
                n0 = len(rows)
                add(_query(c, groups, filters, args, limit))
                if len(rows) > n0:
                    mode = ('全部词＋' if n0 else '') + '模糊：' + '，'.join(notes)
                    shown = [t for g in groups for t in g]
        if not rows and len(terms) > 1:
            for t in terms:
                add(_query(c, [t], filters, args, limit))
            mode = '任一词'
    hits = []
    for path, kind, per, cat, repo_, start, end, content in rows:
        if system_denied(path):                # 系统文件（库、连接串、会话、机密）即使进了索引也不出现在检索结果里
            continue
        line, snip = _snippet(content, shown, start)
        hits.append({'path': path, 'kind': kind, 'period': per, 'cat': cat, 'repo': repo_,
                     'line': line, 'snippet': snip})
    return hits, mode


def search(query, period=None, category=None, scope='all', repo=None, limit=10):
    hits, mode = search_hits(query, period, category, scope, repo, limit)
    if not hits:
        return f'没有找到「{query}」。可换个说法、缩短关键词，或先 kb_reindex。'
    out = [f'找到 {len(hits)} 条（匹配{mode}）：']
    for i, h in enumerate(hits, 1):
        tag = f'代码·{h["repo"]}' if h['kind'] == 'code' else f'{h["period"]}·{h["cat"] or "其他"}'
        out.append(f'\n[{i}] {tag}  {h["path"]}:{h["line"]}\n    {h["snippet"]}')
    return '\n'.join(out)


# 不管在哪个白名单根下面都不给的文件：元数据库、连接串（含数据库密码）、网页会话、机密库、主密钥
SECRET_NAMES = {'kb.db', 'kb.db-wal', 'kb.db-shm', 'index.db', 'index.db-wal', 'index.db-shm', 'db.url', 'web.json',
                '.网页会话.json', 'store.enc', 'master.key', '.env'}


PROGRAM_DIRS = {'kblib', 'webapp', 'ui', 'tests', 'bin', 'docs', '示例仓库'}
PROGRAM_EXT = {'.py', '.md', '.txt', '.html', '.js', '.css', '.sh', '.yml', '.yaml', '.dockerignore'}


def system_denied(p: Path):
    """知识库自己的系统文件（REQ-003 REQ-007）：期 / 事项资料全平台开放，但这些任何人都不能经 /fs/、/file、kb_read 拿到。
    返回原因，空 = 不拦。产物、证据、任务日志在 _system/cache 里，走各自的接口（已按期鉴权），不经这里。"""
    try:
        p = Path(p).expanduser().resolve()
        root, sysd = ROOT.resolve(), SYS.resolve()
    except (OSError, RuntimeError):
        return '路径不对'
    why = '这是知识库的系统文件，不能查看或下载'
    if p.name in SECRET_NAMES or '.机密' in p.parts:
        return why
    try:
        if p == Path(_baseinfo.BASE_FILE).resolve():   # 基础信息.json 里有私有条目（只给本人和管理员），走 /api/base 按人过滤
            return why
        att = _baseinfo.attachment_denied(p)           # 私有条目的附件只给本人和管理员（按当前请求 / 会话的人）
        if att:
            return att
    except (OSError, RuntimeError):
        pass
    if p == sysd or sysd in p.parents:
        rel = p.relative_to(sysd).parts if p != sysd else ()
        program = rel and (rel[0] in PROGRAM_DIRS or (len(rel) == 1 and (p.suffix.lower() in PROGRAM_EXT or p.name.startswith('Dockerfile'))))
        if not program:                                 # 程序源码照常可看（知识库自己登记成代码仓库时）；数据、缓存、日志、记录不给
            return why
    if root in p.parents:
        rel = p.relative_to(root).parts
        if rel[0].startswith('.') or (len(rel) == 1 and p.suffix.lower() in ('.json', '.db', '.url', '.key', '.lock', '.jsonl')):
            return why                                    # 根目录下的 .网页会话.json、设置 / 代码仓库 / 设备占用 .json 等
    return ''


def _allowed(p: Path):
    p = Path(p)
    roots = [ROOT.resolve()] + [Path(v['路径']).resolve() for v in load_repos().values()]
    roots += [Path(x).resolve() for x in (load_meta(PERIODS / n).get('SDD目录') for n in period_names()) if x]
    return any(p == r or r in p.parents for r in roots) and not system_denied(p)


def read_file(path, start=1, max_lines=300):
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        raise KBError(f'文件不存在：{p}')
    if not _allowed(p):
        raise KBError('只能读取知识库和已登记仓库里的文件')
    if p.suffix.lower() in IMG_EXT:
        note = p.with_name(p.name + NOTE_SUFFIX)
        desc = read_text(note) if note.exists() else '（暂无说明）'
        return f'这是图片，请用 Read 工具直接查看：{p}\n\n{desc}'
    text = extract(p)
    if text is None:
        return f'不支持解析这种文件：{p.name}'
    lines = text.splitlines()
    start = max(1, int(start or 1))
    end = min(len(lines), start + max(1, int(max_lines or 300)) - 1)
    body = '\n'.join(f'{i:>5}  {lines[i - 1]}' for i in range(start, end + 1))
    more = f'\n…（共 {len(lines)} 行，继续读请用 start={end + 1}）' if end < len(lines) else ''
    return f'{p}（第 {start}-{end} 行）\n{body}{more}'
