"""线上问题的基础资料：日志包 / 日志文件夹（往往只要其中某份）、解密程序 / 解密文件 / 解密工程。

  用户给了日志包（.zip / .tar.gz）或整个日志文件夹，里面按日期分了几十上百份日志：
    pack_keep(事项, 包)            → 整份放进事项「其他资料/日志包/」，只回一句概况（多少文件、日期范围、子目录），不列清单
    pack_list(包, find)            → 概况；带 find 时按关键词 / 日期 / 通配找，最多列 30 个
    pack_pick(事项, 包, 挑哪些)    → 挑中的放进「其他资料/日志/」，记一条「要解析的日志」
  用户点名了哪份就直接挑；没点名不追问，推动后由分析会话按现象找出相关日志，再用 kb_ask 跟用户确认。
  日志是加密的、要用指定的程序 / 文件 / 工程解密时：
    decrypt_add(事项, 路径或链接, 用法) → 文件归到 其他资料/解密/；工程目录只登记路径（不整份拷贝）；链接记成链接；
                                         再记一条「解密方式」，处理事项的会话照着解密。
"""
import fnmatch
import re
import shutil
import tarfile
import zipfile
from collections import Counter
from datetime import datetime
from pathlib import Path

from kblib.core.common import KBError, SYS, safe_name, uuid_hex
from kblib.core.periods import add_material, period_dir
from kblib.core.notes import add_note

PACK_EXT = ('.zip', '.tar', '.tar.gz', '.tgz')
MAX_FIND = 30
MAX_PICK = 50
MAX_ENTRY = 500 << 20                   # 单个日志解出来最多 500MB
DATE_RE = re.compile(r'(20\d\d)[-_.]?([01]\d)[-_.]?([0-3]\d)')
PROJECT_MARK = ('.git', '.svn', 'build.gradle', 'build.gradle.kts', 'pom.xml', 'package.json', 'setup.py',
                'pyproject.toml', 'go.mod', 'Cargo.toml', 'Makefile', 'CMakeLists.txt')


def is_pack(p):
    p = Path(p)
    return p.is_dir() or p.name.lower().endswith(PACK_EXT)


def _hidden(name):
    return any(x.startswith('.') or x == '__MACOSX' for x in name.split('/') if x)


def _entries(p):
    """[(相对路径, 大小, 修改时间文字)]，不含目录、隐藏文件、__MACOSX。"""
    p = Path(p)
    out = []
    try:
        if p.is_dir():
            for f in sorted(p.rglob('*')):
                n = f.relative_to(p).as_posix()
                if f.is_file() and not _hidden(n):
                    st = f.stat()
                    out.append((n, st.st_size, datetime.fromtimestamp(st.st_mtime).strftime('%Y-%m-%d %H:%M')))
        elif p.name.lower().endswith('.zip'):
            with zipfile.ZipFile(p) as z:
                for zi in z.infolist():
                    if not zi.is_dir() and not _hidden(zi.filename):
                        out.append((zi.filename, zi.file_size, '%04d-%02d-%02d %02d:%02d' % zi.date_time[:5]))
        else:
            with tarfile.open(p) as t:
                for ti in t.getmembers():
                    if ti.isfile() and not _hidden(ti.name):
                        out.append((ti.name, ti.size, datetime.fromtimestamp(ti.mtime).strftime('%Y-%m-%d %H:%M')))
    except (zipfile.BadZipFile, tarfile.TarError, OSError) as e:
        raise KBError(f'{p.name} 打不开（不是完整的压缩包？）：{e}')
    return out


def _size(n):
    for u in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or u == 'GB':
            return f'{n:.0f}{u}' if u == 'B' else f'{n:.1f}{u}'
        n /= 1024


def _date(name):
    m = DATE_RE.search(name)
    return f'{m.group(1)}-{m.group(2)}-{m.group(3)}' if m else ''


def _src(path):
    p = Path(path).expanduser()
    if not p.exists():
        raise KBError(f'不存在：{p}')
    if not is_pack(p):
        raise KBError(f'{p.name} 不是文件夹或压缩包（支持 {" / ".join(PACK_EXT)}）')
    return p


def _label(p):
    return f'📁 {p.name}' if p.is_dir() else p.name


def summary(p, es=None):
    """一句话概况：多少文件、多大、文件名里的日期范围、主要子目录和类型。不列文件。"""
    p = Path(p)
    es = _entries(p) if es is None else es
    if not es:
        return f'{_label(p)}：里面没有文件'
    dates = sorted({_date(n) for n, _, _ in es} - {''})
    root = {n.split('/')[0] for n, _, _ in es}
    strip = len(root) == 1 and all('/' in n for n, _, _ in es)          # 包里只有一个顶层目录：看下一层
    dirs = Counter(n.split('/')[1 if strip else 0] for n, _, _ in es if n.count('/') > (1 if strip else 0))
    ext = Counter((Path(n).suffix.lower() or '无后缀') for n, _, _ in es)
    parts = [f'{len(es)} 个文件，共 {_size(sum(s for _, s, _ in es))}']
    if dates:
        parts.append(f'文件名里的日期 {dates[0]} ~ {dates[-1]}（{len(dates)} 天）' if len(dates) > 1 else f'日期 {dates[0]}')
    if dirs:
        parts.append('子目录 ' + '、'.join(f'{k}（{v}）' for k, v in dirs.most_common(6)) + (' 等' if len(dirs) > 6 else ''))
    parts.append('类型 ' + '、'.join(f'{k} {v}' for k, v in ext.most_common(4)))
    return f'{_label(p)}：' + '；'.join(parts)


def _find(es, key):
    k = key.strip()
    d = _date(k)
    if any(c in k for c in '*?['):
        return [e for e in es if fnmatch.fnmatch(e[0], k) or fnmatch.fnmatch(e[0].rsplit('/', 1)[-1], k)]
    return [e for e in es if (d and _date(e[0]) == d) or k.lower() in e[0].lower()]


def pack_list(path, find=''):
    """概况；带 find（关键词 / 日期 / 通配）时列出对得上的，最多 30 个。不带 find 不列文件。"""
    p = _src(path)
    es = _entries(p)
    out = [summary(p, es)]
    if find and find.strip():
        hit = _find(es, find)
        out.append(f'对得上「{find.strip()}」的 {len(hit)} 个' + ('：' if hit else ''))
        out += [f'- {n}  {_size(s)}  {t}' for n, s, t in hit[:MAX_FIND]]
        if len(hit) > MAX_FIND:
            out.append(f'……还有 {len(hit) - MAX_FIND} 个，换个更具体的 find')
    return '\n'.join(out)


def pack_keep(iteration, path, find=''):
    """整份放进事项「其他资料/日志包/」（上传的暂存文件用完就删），回一句概况；之后从这份里找、挑。"""
    p = _src(path)
    d = period_dir(iteration)
    dest = d / '其他资料' / '日志包' / p.name
    if d.resolve() not in p.resolve().parents:
        add_material(iteration, p, '其他资料', note='日志' + ('文件夹' if p.is_dir() else '包') + '（还没确认要解析哪份）', sub='日志包')
    return f'已放进「{d.name}」：{dest}\n' + pack_list(dest, find)


def _match(es, picks):
    names = [n for n, _, _ in es]
    out = []
    for raw in picks:
        k = str(raw).strip()
        if not k:
            continue
        hit = [k] if k in names else [n for n, _, _ in _find(es, k)]
        if not hit:
            raise KBError(f'里面没有对得上「{k}」的文件，先用 kb_log_pack 带 find 搜一下')
        out += [h for h in hit if h not in out]
    if not out:
        raise KBError('没说挑哪个日志')
    if len(out) > MAX_PICK:
        raise KBError(f'「{"、".join(map(str, picks))}」对上了 {len(out)} 个文件，太多了，挑具体一点')
    return out


def _extract(p, name, dest_dir, password=''):
    """取出一个文件到 dest_dir（只用文件名，防止路径穿越）；同名的带上上级目录名。"""
    base = safe_name(name.rsplit('/', 1)[-1])
    f = dest_dir / base
    if f.exists():
        f = dest_dir / safe_name(name.replace('/', '-'))
    if p.is_dir():
        src = (p / name).resolve()
        if p.resolve() not in src.parents:
            raise KBError(f'{name} 不在 {p.name} 里')
        shutil.copy2(src, f)
    elif p.name.lower().endswith('.zip'):
        with zipfile.ZipFile(p) as z:
            zi = z.getinfo(name)
            if zi.file_size > MAX_ENTRY:
                raise KBError(f'{name} 太大（{_size(zi.file_size)}），请单独发这一个文件')
            try:
                src = z.open(zi, pwd=password.encode() if password else None)
            except RuntimeError as e:             # 加了密码的 zip
                raise KBError(f'{p.name} 设了密码：{"密码不对" if password else "带上 password 再挑"}（{e}）')
            with src, f.open('wb') as out:
                shutil.copyfileobj(src, out)
    else:
        with tarfile.open(p) as t:
            ti = t.getmember(name)
            if ti.size > MAX_ENTRY:
                raise KBError(f'{name} 太大（{_size(ti.size)}），请单独发这一个文件')
            with t.extractfile(ti) as src, f.open('wb') as out:
                shutil.copyfileobj(src, out)
    return f


def pack_pick(iteration, path, picks, note='', password='', keep_pack=True):
    """从日志包里挑要解析的日志放进事项；整包也留一份，以后要看别的日期还在。"""
    period_dir(iteration)
    p = _src(path)
    if isinstance(picks, str):
        picks = [x for x in re.split(r'[,，、\n]+', picks) if x.strip()]
    names = _match(_entries(p), picks or [])
    tmp = SYS / 'tmp' / 'logpack' / uuid_hex()[:12]
    tmp.mkdir(parents=True)
    try:
        got = [(n, _extract(p, n, tmp, password)) for n in names]
        out = []
        for n, f in got:
            r = add_material(iteration, f, '其他资料', note=f'从 {p.name} 挑出：{n}', sub='日志', reindex=False)
            out.append(f'- {n} → {r.splitlines()[0]}')
        if keep_pack and period_dir(iteration).resolve() not in p.resolve().parents:
            out.append('- 整份：' + add_material(iteration, p, '其他资料', note='日志包（挑中的已放到 其他资料/日志/）',
                                                 sub='日志包').splitlines()[0])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    d = period_dir(iteration)
    body = (f'来自：{_label(p)}\n只解析这些（已放到 其他资料/日志/）：\n'
            + '\n'.join(f'- {n} → 其他资料/日志/{f.name}' for n, f in got) + (f'\n说明：{note.strip()}' if note.strip() else ''))
    add_note(iteration, body, '其他资料', '其他', f'要解析的日志：{"、".join(n.rsplit("/", 1)[-1] for n in names)[:60]}')
    return f'从 {p.name} 挑出 {len(names)} 个日志放进「{d.name}」：\n' + '\n'.join(out) + '\n已记一条「要解析的日志」，处理事项时只看这些。'


def _is_project(d):
    return any((d / m).exists() for m in PROJECT_MARK) or sum(1 for _ in zip(range(51), d.rglob('*'))) > 50


def decrypt_add(iteration, path='', url='', how='', kind='', source=''):
    """登记解密程序 / 解密文件 / 解密工程，并记一条「解密方式」。"""
    period_dir(iteration)
    path, url, how = (path or '').strip(), (url or '').strip(), (how or '').strip()
    if not path and not url:
        raise KBError('解密程序 / 文件 / 工程：给本地路径（path）或地址（url）')
    lines, done = [], []
    if path:
        p = Path(path).expanduser()
        if not p.exists():
            raise KBError(f'路径不存在：{p}')
        if p.is_dir() and (kind == '工程' or _is_project(p)):
            kind = kind or '工程'
            lines.append(f'解密{kind}（本机目录，没有拷贝）：{p.resolve()}')
            done.append(f'登记了解密{kind}目录 {p.resolve()}（工程不整份拷贝，处理时直接在那里用）')
        else:
            kind = kind or ('程序' if p.suffix.lower() in ('.jar', '.py', '.sh', '.exe', '.js', '.bin', '') and p.is_file() else '文件')
            r = add_material(iteration, p, '其他资料', note=f'解密{kind}', sub='解密')
            at = next((x[3:].strip() for x in r.splitlines() if x.startswith('路径：')), '')
            lines.append(f'解密{kind}：其他资料/解密/{p.name}' + (f'（{at}）' if at else ''))
            done.append(r.splitlines()[0])
    if url:
        kind = kind or '工程'
        lines.append(f'解密{kind}地址：{url}')
    if how:
        lines.append(f'用法：{how}')
    else:
        lines.append('用法：用户没说，处理时先看程序 / 工程自带的说明（README、--help）')
    title = f'解密方式：{Path(path).name if path else url.rstrip("/").rsplit("/", 1)[-1]}'[:60]
    r = add_note(iteration, '\n'.join(lines), '其他资料', '链接' if url and not path else '其他', title, url, source)
    done.append(r.splitlines()[0])
    return '\n'.join(done) + '\n处理这个事项的会话会先按「解密方式」解密日志再分析。'
