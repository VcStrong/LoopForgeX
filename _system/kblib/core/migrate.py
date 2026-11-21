"""升级迁移（REQ-027）、搬家备份 / 恢复（REQ-034）。

  kb migrate --dry-run   列出要迁移的期、事项、Bug、机密、令牌数量和目标项目，不改任何东西
  kb migrate             元数据整目录备份到 _system/cache/迁移备份/<日期时间> → 建库导入 → 校验 → 不一致自动回滚
  kb doctor              校验：条数一致、每期能打开、机密能读
  单机模式 kb web 启动时自动做一次（ensure_migrated），重复运行结果一致（幂等）。
  kb backup --for-move   数据库 + 资料目录 + 机密主密钥（单独加密、要口令）打成一个归档
  kb restore <归档>      到新机器恢复
"""
import json
import os
import re
import shutil
import subprocess
import tarfile
import time
from pathlib import Path

from kblib.core.common import KBError, PERIODS, ROOT, SETTINGS_FILE, SYS, load_settings, read_text, save_settings
from kblib.core import db as _db
from kblib import lazy

_users = lazy('kblib.core.users')
_periods = lazy('kblib.core.periods')
_baseinfo = lazy('kblib.core.baseinfo')
_vault = lazy('kblib.infra.vault')

MIGRATED_KEY = '迁移'
BACKUP_DIR = SYS / 'cache' / '迁移备份'
REC_RE = re.compile(r'^- (\d{4}-\d\d-\d\d \d\d:\d\d) ｜ ([^｜]*) ｜ (.*)$')


def _period_dirs():
    if not PERIODS.exists():
        return []
    return sorted(p for p in PERIODS.iterdir() if p.is_dir() and (p / '期信息.json').exists())


def _old_dep():
    try:
        s = json.loads(read_text(SETTINGS_FILE)) if SETTINGS_FILE.exists() else {}
    except ValueError:
        s = {}
    return (s.get('部署') or {}) if isinstance(s, dict) else {}


def _base_entries():
    try:
        return _baseinfo.base_load()['条目']
    except Exception:
        return []


def migrate_plan():
    """dry-run：要迁移什么、迁到哪。"""
    ds = _period_dirs()
    tasks = periods = 0
    for d in ds:
        try:
            m = json.loads((d / '期信息.json').read_text('utf-8'))
        except (OSError, ValueError):
            m = {}
        if m.get('类型') == '事项':
            tasks += 1
        else:
            periods += 1
    try:
        from kblib.bugs.index import zt_load
        bugs = len((zt_load() or {}).get('Bug') or {})
    except Exception:
        bugs = 0
    es = _base_entries()
    secrets = sum(1 for e in es for f in e.get('字段') or [] if f.get('机密'))
    dep = _old_dep()
    return {'期': periods, '事项': tasks, 'Bug': bugs, '机密': secrets, '测试账号': sum(1 for e in es if e.get('分类') == '测试账号'),
            '令牌': len(dep.get('令牌') or []), '旧访问密码': bool(dep.get('密码')), '目标项目': '默认项目（default）',
            '本机用户': _local_name_guess(), '已迁移': bool(_db.kv_get(MIGRATED_KEY)[0])}


def _local_name_guess():
    import getpass
    return getpass.getuser()


def migrate_plan_text(p):
    return '\n'.join([
        '要迁移（dry-run，没有改任何东西）：',
        f'- 期 {p["期"]} 个、事项 {p["事项"]} 个 → 「{p["目标项目"]}」，负责人 本机用户 {p["本机用户"]}',
        f'- Bug 索引 {p["Bug"]} 条 → 「{p["目标项目"]}」',
        f'- 机密 {p["机密"]} 个 → 「团队」层级（只有管理员能改层级）',
        f'- 测试账号 {p["测试账号"]} 个 → 测试账号清单（默认项目 · 测试环境）',
        f'- 令牌 {p["令牌"]} 个 → 管理员的个人令牌' + ('；旧访问密码 → 管理员账号 admin 的密码' if p['旧访问密码'] else ''),
        f'- 元数据先备份到 {BACKUP_DIR}/<日期时间>',
        '（之前已迁移过：再运行结果一致）' if p['已迁移'] else '',
    ]).strip()


def migrate_backup():
    """整目录备份元数据（不拷资料文件本身）。"""
    dst = BACKUP_DIR / time.strftime('%Y%m%d-%H%M%S')
    dst.mkdir(parents=True, exist_ok=True)
    for f in (SETTINGS_FILE, ROOT / '.网页会话.json', ROOT / '代码仓库.json', ROOT / '设备占用.json'):
        if f.exists():
            shutil.copy2(f, dst / f.name)
    if _db.has_db():
        _db.backup_to(dst / 'kb.db')
    for d in _period_dirs():
        o = dst / '期' / d.name
        o.mkdir(parents=True, exist_ok=True)
        for n in ('期信息.json', '记录.md'):
            if (d / n).exists():
                shutil.copy2(d / n, o / n)
    for sub in ('基础', '禅道', 'Bug'):
        p = ROOT / sub
        if p.is_dir():
            shutil.copytree(p, dst / sub, ignore=shutil.ignore_patterns('附件', '*.png', '*.jpg', '*.zip'), dirs_exist_ok=True)
    return dst


def _import():
    local = _users.local_user()
    _users.ensure_default_project(local)
    dep = _old_dep()
    if dep.get('密码') and not (_db.user_get('admin') or {}).get('pw'):
        _db.user_put('admin', pw=dep['密码'], sys_role='管理员', status='正常') if not _db.user_get('admin') else \
            _db.user_put('admin', pw=dep['密码'], sys_role='管理员')
        _db.member_set('default', 'admin', '负责人')
    owner = 'admin' if _db.user_get('admin') else local
    for t in dep.get('令牌') or []:
        if t.get('哈希') and not _db.token_get(t['哈希']):
            _db.token_put(t['哈希'], '个人', user=owner, name=t.get('名称') or '旧令牌')
    n_rec = 0
    for d in _period_dirs():
        _periods.load_meta(d)                                   # 导入期信息（项目 default，负责人本机用户）
        r = _db.period_row(d.name)
        if r and not r['project']:
            _db.period_set_attr(d.name, project='default', owner=r['owner'] or local, by='迁移')
        if _db.record_count(d.name) == 0 and (d / '记录.md').exists():
            for line in (d / '记录.md').read_text('utf-8', errors='ignore').splitlines():
                m = REC_RE.match(line)
                if m:
                    _db.record_add(d.name, m.group(1), m.group(2), m.group(3), '')      # 历史原样保留（REQ-004）
                    n_rec += 1
    for e in _base_entries():
        if not _db.secret_meta(e['编号']) and any(f.get('机密') for f in e.get('字段') or []):
            _db.secret_meta_put(e['编号'], '团队', '', e.get('项目') or 'default')
    try:
        from kblib.core import testaccounts
        testaccounts.import_from_base()
    except Exception:
        pass
    _db.kv_set(MIGRATED_KEY, {'时间': _db.ts_str(), '版本': _db.schema_version()}, by='kb migrate')
    return n_rec


def migrate_check(check_secrets=True):
    """校验：条数一致、每期能打开、机密能读。返回 (问题列表, 统计)。"""
    bad = []
    ds = _period_dirs()
    rows = {r['name'] for r in _db.period_rows()}
    missing = [d.name for d in ds if d.name not in rows]
    if missing:
        bad.append(f'期没进库：{"、".join(missing[:10])}' + (f' 等 {len(missing)} 个' if len(missing) > 10 else ''))
    for d in ds:
        try:
            m = _periods.load_meta(d)
            if not isinstance(m, dict) or not m.get('名称'):
                bad.append(f'期打不开：{d.name}')
        except Exception as e:
            bad.append(f'期打不开：{d.name}（{e}）')
    if check_secrets:
        for e in _base_entries():
            for f in e.get('字段') or []:
                if f.get('机密'):
                    try:
                        if _baseinfo._kc_get(f'{e["编号"]}/{f["名"]}') is None:
                            bad.append(f'机密读不出：{e["编号"]}/{f["名"]}')
                    except Exception as ex:
                        bad.append(f'机密读不出：{e["编号"]}/{f["名"]}（{ex}）')
    if not _db.project_get('default'):
        bad.append('没有默认项目')
    if not any(u.get('sys_role') == '管理员' for u in _db.user_list()):
        bad.append('没有管理员账号')
    return bad, {'期目录': len(ds), '库里的期': len(rows), **_db.table_counts()}


def migrate_rollback(src):
    """把 migrate_backup 的备份拷回去（迁移失败自动回滚）。库用在线恢复，不删文件（别的线程的连接还在用）。"""
    src = Path(src)
    if (src / 'kb.db').exists():
        _db.restore_from(src / 'kb.db')
    for f in src.iterdir():
        if f.is_file() and f.name != 'kb.db':
            shutil.copy2(f, ROOT / f.name)
    if (src / '期').is_dir():
        for d in (src / '期').iterdir():
            if (PERIODS / d.name).is_dir():
                for f in d.iterdir():
                    shutil.copy2(f, PERIODS / d.name / f.name)


def migrate(dry_run=False, check_secrets=True):
    if dry_run:
        return migrate_plan_text(migrate_plan())
    bk = migrate_backup()
    try:
        n_rec = _import()
        bad, stats = migrate_check(check_secrets)
    except Exception as e:
        bad, stats, n_rec = [f'导入出错：{e!r}'], {}, 0
    if bad:
        migrate_rollback(bk)
        raise KBError('迁移校验不通过，已自动回滚到备份 ' + str(bk) + '：\n- ' + '\n- '.join(bad))
    return (f'迁移完成（备份在 {bk}）：期 {stats["库里的期"]} 个、用户 {stats["users"]} 个、项目 {stats["projects"]} 个、'
            f'令牌 {stats["tokens"]} 个、机密登记 {stats["secrets_meta"]} 个、导入历史记录 {n_rec} 条；kb doctor 校验通过')


def backfill_owners():
    """没有负责人的期 / 事项（老库、期信息.json 直接放进来的）补上负责人：期信息里记了创建人就用他，否则本机用户
    （平台化之前一切都是本机用户建的）。不补的话「删除只给本人和管理员」里的本人就删不了（审查第 2 轮）。返回补了几个。"""
    local = _users.local_user()
    n = 0
    for r in _db.period_rows():
        if r.get('owner'):
            continue
        try:
            m = _periods.load_meta(PERIODS / r['name'])
        except Exception:      # noqa: BLE001
            m = {}
        who = m.get('创建人') if m.get('创建人') and _db.user_get(m.get('创建人')) else local
        if who:
            _db.period_set_attr(r['name'], owner=who, by='迁移补负责人')
            n += 1
    return n


def ensure_migrated():
    """kb web 启动时：还没迁移过就自动迁移一次（不读机密，免得钥匙串弹窗）；迁移过只补新出现的期、补没有负责人的。"""
    if _db.kv_get(MIGRATED_KEY)[0]:
        _users.local_user()
        n = backfill_owners()
        return f'给 {n} 个没有负责人的期 / 事项补上了负责人（本人可删除）' if n else ''
    try:
        msg = migrate(check_secrets=False)
        backfill_owners()
        return msg
    except KBError as e:
        return str(e)


# ---------------------------------------------------------------- 搬家（REQ-034）
def backup_for_move(out='', passphrase=''):
    """数据库 + 资料目录 + 机密主密钥（单独用口令加密）打成一个 tar.gz。"""
    if not passphrase or len(passphrase) < 8:
        raise KBError('要给一个至少 8 位的口令（加密机密主密钥用，恢复时要输入）')
    ts = time.strftime('%Y%m%d-%H%M%S')
    out = Path(out or (Path.home() / f'kb-搬家-{ts}.tar.gz')).expanduser()
    tmp = SYS / 'tmp' / f'move-{ts}'
    tmp.mkdir(parents=True, exist_ok=True)
    _db.backup_to(tmp / 'kb.db')
    key = b''
    try:
        key = _vault._master().encode()
    except Exception:
        pass
    store = ROOT / '.机密' / 'store.enc'
    if key:
        r = subprocess.run(['openssl', 'enc', '-aes-256-cbc', '-pbkdf2', '-iter', '200000', '-salt', '-pass', 'env:KB_MOVE_PASS'],
                           input=key, capture_output=True, env=dict(os.environ, KB_MOVE_PASS=passphrase), timeout=30)
        if r.returncode:
            raise KBError('加密主密钥失败：' + r.stderr.decode()[:200])
        (tmp / 'master.key.enc').write_bytes(r.stdout)
    skip = {'_system', '.git', '.venv', '.Trash'}
    with tarfile.open(out, 'w:gz') as tf:
        tf.add(tmp / 'kb.db', 'kb.db')
        if (tmp / 'master.key.enc').exists():
            tf.add(tmp / 'master.key.enc', 'master.key.enc')
        for p in ROOT.iterdir():
            if p.name in skip or p.name.startswith('.') and p.name != '.机密':
                continue
            tf.add(p, f'资料/{p.name}')
        if store.exists():
            tf.add(store, '资料/.机密/store.enc')
    shutil.rmtree(tmp, ignore_errors=True)
    note = '' if key or _vault.BACKEND == 'file' else '\n注意：这台机器的机密在钥匙串里，归档里没有钥匙串内容；新机器上要用 kb base 重新录入机密，或先 KB_SECRET_BACKEND=file 迁到加密文件。'
    return (f'已生成搬家归档：{out}\n到新机器上运行：\n  kb restore {out}\n  kb doctor\n'
            f'执行机改连新地址：kb runner --server <新地址> --rebind{note}')


def kb_restore(archive, passphrase=''):
    archive = Path(archive).expanduser()
    if not archive.is_file():
        raise KBError(f'没有这个归档：{archive}')
    tmp = SYS / 'tmp' / f'restore-{int(time.time())}'
    tmp.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as tf:
        for m in tf.getmembers():
            if m.name.startswith('/') or '..' in Path(m.name).parts:
                raise KBError(f'归档里有不安全的路径：{m.name}')
        tf.extractall(tmp)
    if (tmp / '资料').is_dir():
        for p in (tmp / '资料').iterdir():
            dst = ROOT / p.name
            if p.is_dir():
                shutil.copytree(p, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(p, dst)
    if (tmp / 'master.key.enc').exists():
        if not passphrase:
            raise KBError('归档里的机密主密钥是加密的：带上 --passphrase（备份时给的口令）')
        r = subprocess.run(['openssl', 'enc', '-d', '-aes-256-cbc', '-pbkdf2', '-iter', '200000', '-pass', 'env:KB_MOVE_PASS'],
                           input=(tmp / 'master.key.enc').read_bytes(), capture_output=True,
                           env=dict(os.environ, KB_MOVE_PASS=passphrase), timeout=30)
        if r.returncode:
            raise KBError('口令不对，主密钥解不开')
        kf = _vault.KEYFILE
        kf.parent.mkdir(parents=True, exist_ok=True)
        if kf.exists() and kf.read_bytes().strip() != r.stdout.strip():
            shutil.copy2(kf, kf.with_suffix('.key.bak'))
        kf.write_bytes(r.stdout)
        os.chmod(kf, 0o600)
    _db.restore_from(tmp / 'kb.db')
    shutil.rmtree(tmp, ignore_errors=True)
    s = load_settings()
    s.setdefault('部署', {})['恢复自'] = str(archive)
    save_settings(s)
    return f'已恢复：{archive}\n下一步运行 kb doctor 校验'
