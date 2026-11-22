"""元数据库：SQLite（WAL，默认 / 单机零配置）、PostgreSQL、MySQL 三种驱动。所有 SQL 集中在这里（REQ-008 REQ-021）；
业务模块只调本文件的仓储函数。

  选驱动：连接串 KB_DB_URL（环境变量）或 _system/db.url（kb db use 写入，权限 600）：
          sqlite:///<文件>（不配置时就是 _system/kb.db）、postgresql://用户:密码@主机:5432/库、mysql://用户:密码@主机:3306/库。
          Docker / 服务器部署默认 PostgreSQL（docker-compose.yml 自带）；本机单机不配置仍是 SQLite。
  方言：SQL 按 SQLite 写，执行前在 _tr 里翻译：占位符、upsert（ON CONFLICT → ON DUPLICATE KEY）、保留字列名加引号、
        建表类型（自增 / REAL / INTEGER / MySQL 的 TEXT）。驱动：PostgreSQL 用 psycopg 3，MySQL 用 PyMySQL（requirements-db.txt）。
  连接：每线程一个连接；SQLite 用 WAL、busy_timeout 5 秒；锁超过 5 秒抛 DBBusy（网页返回 500「数据库忙，稍后重试」）。
  事务：with tx() as c: ...——全库写锁语义（SQLite BEGIN IMMEDIATE / PG advisory 锁 / MySQL GET_LOCK），出错回滚，
        资源池等「查完再写」的并发安全在三种库上一致（REQ-048）。
  迁移：schema_version 表记版本，MIGRATIONS 按序执行，只加不改。
  乐观锁：期（periods.version）、设置（kv.version）写入时带版本号，不一致抛 KBNeed（409）。
  备份：backup_to 一律输出 SQLite 文件，restore_from 从 SQLite 文件导入当前驱动——Mac 上的 SQLite 可直接搬进服务器的 PostgreSQL。
  时钟：now_ts() —— 测试库（_system/测试库 存在）可设环境变量 KB_FAKE_CLOCK=<偏移秒> 加速（REQ-048）。
"""
import json
import os
import re
import sqlite3
import threading
import time
import weakref
from contextlib import contextmanager
from decimal import Decimal
from functools import lru_cache
from urllib.parse import unquote, urlparse

from kblib.core.common import KBError, KBNeed, SYS

DB_FILE = SYS / 'kb.db'
URL_FILE = SYS / 'db.url'
_LOCAL = threading.local()
_INIT_LOCK = threading.Lock()
_READY = set()
_CONNS = {}                 # id(conn) -> (线程弱引用, conn)
_CONNS_LOCK = threading.Lock()


class DBBusy(Exception):
    """数据库被锁超过 5 秒。"""


def now_ts():
    off = os.environ.get('KB_FAKE_CLOCK')
    if off and (SYS / '测试库').exists():
        try:
            return time.time() + float(off)
        except ValueError:
            pass
    return time.time()


def ts_str(t=None):
    return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now_ts() if t is None else t))


def _j(v):
    return json.dumps(v, ensure_ascii=False)


def _l(s, default=None):
    if s is None or s == '':
        return default
    try:
        return json.loads(s)
    except ValueError:
        return default


MIGRATIONS = [
    # 1：阶段一（用户 / 项目 / 令牌 / 期 / 记录 / 设置 / 审计 / 定时任务 / 事件）
    """
    CREATE TABLE kv (k TEXT PRIMARY KEY, v TEXT, version INTEGER DEFAULT 0, updated REAL, by TEXT);
    CREATE TABLE users (name TEXT PRIMARY KEY, pw TEXT, sys_role TEXT DEFAULT '普通', status TEXT DEFAULT '正常',
        pw_ver INTEGER DEFAULT 1, created TEXT, last_login TEXT, profile TEXT DEFAULT '{}',
        fails INTEGER DEFAULT 0, locked_until REAL DEFAULT 0, local INTEGER DEFAULT 0);
    CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT UNIQUE, owner TEXT, created TEXT);
    CREATE TABLE members (project TEXT, user TEXT, role TEXT, PRIMARY KEY (project, user));
    CREATE TABLE web_sessions (h TEXT PRIMARY KEY, user TEXT, pw_ver INTEGER, expires REAL, project TEXT);
    CREATE TABLE tokens (h TEXT PRIMARY KEY, kind TEXT, user TEXT, name TEXT, project TEXT, period TEXT, session TEXT,
        runner TEXT, created TEXT, last_used TEXT, expires REAL DEFAULT 0, revoked INTEGER DEFAULT 0, engine TEXT);
    CREATE INDEX tokens_user ON tokens(user);
    CREATE TABLE periods (name TEXT PRIMARY KEY, project TEXT, owner TEXT, version INTEGER DEFAULT 1, meta TEXT,
        updated REAL, updated_by TEXT);
    CREATE TABLE records (id INTEGER PRIMARY KEY AUTOINCREMENT, period TEXT, t TEXT, action TEXT, content TEXT, actor TEXT);
    CREATE INDEX records_period ON records(period);
    CREATE TABLE audit (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, kind TEXT, subject TEXT, obj TEXT, project TEXT,
        result TEXT, detail TEXT);
    CREATE INDEX audit_ts ON audit(kind, ts);
    CREATE TABLE schedules (name TEXT PRIMARY KEY, period REAL, last_run REAL DEFAULT 0, last_result TEXT,
        holder TEXT, lease_until REAL DEFAULT 0, runs INTEGER DEFAULT 0);
    CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, kind TEXT, project TEXT, data TEXT);
    CREATE TABLE secrets_meta (id TEXT PRIMARY KEY, layer TEXT DEFAULT '团队', owner TEXT, project TEXT);
    CREATE TABLE bug_owners (key TEXT PRIMARY KEY, project TEXT, owner TEXT, updated TEXT);
    """,
    # 2：阶段二（执行机 / 任务队列 / 指标告警 / 资源池 / 引擎账号 / 用量预算 / 测试账号 / 产物证据）
    """
    CREATE TABLE runners (id TEXT PRIMARY KEY, name TEXT, status TEXT DEFAULT '在线', auto_tags TEXT DEFAULT '[]',
        custom_tags TEXT DEFAULT '[]', capacity INTEGER DEFAULT 2, version TEXT, proto INTEGER, last_hb REAL DEFAULT 0,
        info TEXT DEFAULT '{}', drain INTEGER DEFAULT 0, disabled INTEGER DEFAULT 0, created TEXT, last_assigned REAL DEFAULT 0,
        local INTEGER DEFAULT 0);
    CREATE TABLE tasks (id TEXT PRIMARY KEY, type TEXT, period TEXT, project TEXT, initiator TEXT, status TEXT,
        payload TEXT, runner TEXT, retries INTEGER DEFAULT 0, created REAL, updated REAL, ack_deadline REAL DEFAULT 0,
        wait_reason TEXT DEFAULT '', score TEXT, result TEXT, prev_runner TEXT, account TEXT, switched REAL DEFAULT 0,
        cancel INTEGER DEFAULT 0, waiting_since REAL DEFAULT 0, notified_wait INTEGER DEFAULT 0, log_size INTEGER DEFAULT 0);
    CREATE INDEX tasks_status ON tasks(status, created);
    CREATE TABLE metrics (runner TEXT, ts REAL, cpu REAL, mem REAL, disk REAL, net TEXT);
    CREATE INDEX metrics_rt ON metrics(runner, ts);
    CREATE TABLE alerts (id TEXT PRIMARY KEY, runner TEXT, kind TEXT, level TEXT, since REAL, start REAL, end REAL,
        notified REAL DEFAULT 0, ack_by TEXT, silence_until REAL DEFAULT 0, detail TEXT);
    CREATE TABLE resources (id TEXT PRIMARY KEY, type TEXT, provider TEXT, runner TEXT, attrs TEXT DEFAULT '{}',
        capacity INTEGER DEFAULT 1, enabled INTEGER DEFAULT 1, last_used REAL DEFAULT 0);
    CREATE TABLE holds (id INTEGER PRIMARY KEY AUTOINCREMENT, resource TEXT, session TEXT, period TEXT, user TEXT,
        mode TEXT, start REAL, expires REAL, last_active REAL, idle_limit REAL DEFAULT 0, ticket TEXT);
    CREATE TABLE waits (id INTEGER PRIMARY KEY AUTOINCREMENT, rtype TEXT, cond TEXT, n INTEGER, session TEXT,
        period TEXT, user TEXT, project TEXT, created REAL, deadline REAL, status TEXT DEFAULT '排队', granted TEXT,
        ticket TEXT UNIQUE, mode TEXT);
    CREATE TABLE engine_accounts (id TEXT PRIMARY KEY, engine TEXT, name TEXT, kind TEXT, project TEXT, owner TEXT,
        conc INTEGER DEFAULT 3, rpm INTEGER DEFAULT 30, status TEXT DEFAULT '未检测', cool_until REAL DEFAULT 0,
        reason TEXT DEFAULT '', bad_models TEXT DEFAULT '[]', ord INTEGER DEFAULT 0, allow_switch INTEGER DEFAULT 0,
        prefer_mine INTEGER DEFAULT 0, streak TEXT DEFAULT '{}', ok_probes INTEGER DEFAULT 0, last_probe REAL DEFAULT 0,
        backoff REAL DEFAULT 0, created TEXT, adjusted REAL DEFAULT 0);
    CREATE TABLE engine_calls (account TEXT, ts REAL, code TEXT);
    CREATE INDEX engine_calls_at ON engine_calls(account, ts);
    CREATE TABLE usage (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, user TEXT, project TEXT, account TEXT, task TEXT,
        seconds INTEGER, tin INTEGER, tout INTEGER, estimated INTEGER DEFAULT 0);
    CREATE TABLE budgets (scope TEXT, obj TEXT, day REAL DEFAULT 0, month REAL DEFAULT 0, policy TEXT DEFAULT '排队',
        cheap TEXT DEFAULT '', notified TEXT DEFAULT '', PRIMARY KEY (scope, obj));
    CREATE TABLE grants (id INTEGER PRIMARY KEY AUTOINCREMENT, project TEXT, amount REAL, until REAL, by TEXT, reason TEXT);
    CREATE TABLE test_accounts (id TEXT PRIMARY KEY, project TEXT, env TEXT, name TEXT, account TEXT, tags TEXT DEFAULT '[]',
        conc INTEGER DEFAULT 1, allow_ai INTEGER DEFAULT 1, secret TEXT, created TEXT);
    CREATE TABLE artifacts (id TEXT PRIMARY KEY, task TEXT, period TEXT, project TEXT, version TEXT, branch TEXT,
        commit_id TEXT, sha256 TEXT, size INTEGER, runner TEXT, path TEXT, ts REAL);
    CREATE TABLE evidence (id INTEGER PRIMARY KEY AUTOINCREMENT, task TEXT, kind TEXT, step TEXT, mock INTEGER DEFAULT 0,
        path TEXT, size INTEGER, ts REAL, pinned INTEGER DEFAULT 0);
    CREATE TABLE providers (id TEXT PRIMARY KEY, kind TEXT, name TEXT, enabled INTEGER DEFAULT 0, config TEXT DEFAULT '{}',
        behavior TEXT DEFAULT '正常');
    """,
    # 3：资源池占用带附加信息（分支 / 仓库 / 型号 / 用途）和项目；资源被收回标记（REQ-045 REQ-048）
    """
    ALTER TABLE holds ADD COLUMN info TEXT DEFAULT '{}';
    ALTER TABLE holds ADD COLUMN project TEXT DEFAULT '';
    CREATE INDEX holds_res ON holds(resource);
    CREATE INDEX waits_st ON waits(rtype, status, id);
    CREATE TABLE recalls (resource TEXT, session TEXT, period TEXT, ts REAL, by TEXT);
    """,
]


# ================================================================ 驱动层
def db_url():
    """当前连接串：KB_DB_URL > _system/db.url > 默认 SQLite 文件。"""
    u = (os.environ.get('KB_DB_URL') or '').strip()
    if not u:
        try:
            u = URL_FILE.read_text('utf-8').strip()
        except OSError:
            u = ''
    if u.lower() in ('sqlite', 'sqlite:', 'sqlite://', 'sqlite:///'):
        u = ''
    return u or f'sqlite:///{DB_FILE}'


def dialect_of(url):
    s = (url or '').split(':', 1)[0].lower()
    if s == 'sqlite':
        return 'sqlite'
    if s in ('postgres', 'postgresql', 'pg'):
        return 'pg'
    if s in ('mysql', 'mariadb'):
        return 'mysql'
    raise KBError(f'不认识的数据库连接串：{s}://…（支持 sqlite:// postgresql:// mysql://）')


def driver():
    """当前驱动名：sqlite / pg / mysql。"""
    return dialect_of(db_url())


def safe_url(url=None):
    """去掉密码的连接串（显示 / 日志用）。"""
    url = url or db_url()
    return re.sub(r'(://[^:/@]+):[^@]*@', r'\1:***@', url)


_RESV = ('user', 'end', 'key', 'local', 'usage', 'by')       # 在 PG / MySQL 里是保留字的列名 / 表名，执行前加引号
_RESV_RE = re.compile(r'\b(' + '|'.join(_RESV) + r')\b')
_SERIAL = {'records', 'audit', 'events', 'holds', 'waits', 'usage', 'grants', 'evidence'}   # 自增 id 的表


@lru_cache(maxsize=4096)
def _tr(sql, d):
    """SQLite 写法 → PG / MySQL。SQL 关键字一律大写、列名小写（本文件的约定），保留字按小写整词加引号。"""
    if d == 'sqlite':
        return sql
    qt = '"' if d == 'pg' else '`'
    s = sql.replace('%', '%%')
    s = _RESV_RE.sub(lambda m: qt + m.group(1) + qt, s)
    s = s.replace('?', '%s')
    if d == 'mysql':
        m = re.search(r'ON CONFLICT\s*\([^)]*\)\s*DO UPDATE SET\s+(.*)$', s, re.S)
        if m:
            sets = re.sub(r'excluded\.(`?\w+`?)', r'VALUES(\1)', m.group(1))
            s = s[:m.start()] + 'ON DUPLICATE KEY UPDATE ' + sets
    return s


def _index_cols():
    """MIGRATIONS 里建过索引 / 主键 / 唯一的列（MySQL 这些 TEXT 列要用 VARCHAR 才能建索引）。"""
    out = {}
    for sql in MIGRATIONS:
        for t, cols in re.findall(r'CREATE (?:UNIQUE )?INDEX \w+ ON (\w+)\s*\(([^)]*)\)', sql):
            out.setdefault(t, set()).update(c.strip() for c in cols.split(','))
        for t, body in re.findall(r'CREATE TABLE (?:IF NOT EXISTS )?(\w+)\s*\((.*?)\);', sql + ';', re.S):
            for cols in re.findall(r'PRIMARY KEY\s*\(([^)]*)\)', body):
                out.setdefault(t, set()).update(c.strip() for c in cols.split(','))
    return out


def _split_top(body):
    parts, depth, cur = [], 0, ''
    for ch in body:
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
        if ch == ',' and depth == 0:
            parts.append(cur)
            cur = ''
        else:
            cur += ch
    parts.append(cur)
    return [p.strip() for p in parts if p.strip()]


def _mysql_col(table, coldef, keyed):
    m = re.match(r'(\w+)\s+TEXT\b(.*)$', coldef, re.S)
    if not m:
        return coldef
    name, rest = m.groups()
    if name in keyed.get(table, ()) or re.search(r'PRIMARY KEY|UNIQUE', rest):
        return f'{name} VARCHAR(255){rest}'
    rest = re.sub(r"DEFAULT\s+('(?:[^']|'')*')", r'DEFAULT (\1)', rest)     # LONGTEXT 的默认值要写成表达式（MySQL 8.0.13+）
    return f'{name} LONGTEXT{rest}'


def _ddl(stmt, d):
    """建表 / 加列语句翻译（只在迁移里用）。"""
    if d == 'sqlite':
        return stmt
    s = stmt
    if d == 'pg':
        s = s.replace('INTEGER PRIMARY KEY AUTOINCREMENT', 'BIGSERIAL PRIMARY KEY')
        s = re.sub(r'\bREAL\b', 'DOUBLE PRECISION', s)
    else:
        s = s.replace('INTEGER PRIMARY KEY AUTOINCREMENT', 'BIGINT AUTO_INCREMENT PRIMARY KEY')
        s = re.sub(r'\bREAL\b', 'DOUBLE', s)
        keyed = _index_cols()
        m = re.match(r'CREATE TABLE (IF NOT EXISTS )?(\w+)\s*\((.*)\)\s*$', s, re.S)
        if m:
            cols = [_mysql_col(m.group(2), c, keyed) for c in _split_top(m.group(3))]
            s = f'CREATE TABLE {m.group(1) or ""}{m.group(2)} ({", ".join(cols)}) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin'
        m = re.match(r'ALTER TABLE (\w+) ADD COLUMN (.*)$', s, re.S)
        if m:
            s = f'ALTER TABLE {m.group(1)} ADD COLUMN {_mysql_col(m.group(1), m.group(2).strip(), keyed)}'
    s = re.sub(r'\bINTEGER\b', 'BIGINT', s)
    return _tr(s, d)


def _cv(v):
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    return v


class Row(dict):
    """PG / MySQL 的行：像 sqlite3.Row 一样既能 r['列'] 也能 r[0]，dict(r) 得到普通字典。"""
    __slots__ = ('_v',)

    def __init__(self, cols, vals):
        vals = [_cv(v) for v in vals]
        super().__init__(zip(cols, vals))
        self._v = vals

    def __getitem__(self, k):
        return self._v[k] if isinstance(k, int) else dict.__getitem__(self, k)


class _Cur:
    def __init__(self, rows, lastrowid=None, rowcount=-1):
        self._rows, self.lastrowid, self.rowcount = rows, lastrowid, rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)


class _Conn:
    """统一的连接：execute / begin / commit / rollback / in_transaction。SQLite 直接透传原生连接。"""

    def __init__(self, url):
        self.url, self.d, self._tx = url, dialect_of(url), False
        self.raw = getattr(self, '_open_' + self.d)()

    # ---- 打开
    def _open_sqlite(self):
        path = self.url[len('sqlite:///'):] if self.url.startswith('sqlite:///') else self.url.split(':', 1)[1]
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        c = sqlite3.connect(path, timeout=5, isolation_level=None, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA journal_mode=WAL')
        c.execute('PRAGMA busy_timeout=5000')
        c.execute('PRAGMA synchronous=NORMAL')
        return c

    def _open_pg(self):
        try:
            import psycopg
        except ImportError:
            raise KBError('要用 PostgreSQL 先装驱动：pip install "psycopg[binary]"（或 pip install -r _system/requirements-db.txt）') from None
        u = re.sub(r'^(postgres|pg)://', 'postgresql://', self.url)
        try:
            c = psycopg.connect(u, autocommit=True, connect_timeout=10)
        except psycopg.Error as e:
            raise KBError(f'连不上 PostgreSQL（{safe_url(self.url)}）：{str(e).strip()[:200]}') from None
        c.execute("SET lock_timeout = '5s'")
        return c

    def _open_mysql(self):
        try:
            import pymysql
            from pymysql.constants import CLIENT
        except ImportError:
            raise KBError('要用 MySQL 先装驱动：pip install PyMySQL（或 pip install -r _system/requirements-db.txt）') from None
        p = urlparse(self.url)
        try:
            c = pymysql.connect(host=p.hostname or '127.0.0.1', port=p.port or 3306, user=unquote(p.username or ''),
                                password=unquote(p.password or ''), database=(p.path or '/').lstrip('/') or 'kb',
                                charset='utf8mb4', autocommit=True, connect_timeout=10, client_flag=CLIENT.FOUND_ROWS)
        except pymysql.MySQLError as e:
            raise KBError(f'连不上 MySQL（{safe_url(self.url)}）：{str(e).strip()[:200]}') from None
        with c.cursor() as k:
            k.execute("SET SESSION sql_mode = 'NO_ENGINE_SUBSTITUTION'")
            k.execute('SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED')
            k.execute("SET time_zone = '+00:00'")
        return c

    # ---- 执行
    def execute(self, sql, args=(), ddl=False):
        if self.d == 'sqlite':
            return self.raw.execute(sql, args)
        s = _ddl(sql, self.d) if ddl else _tr(sql, self.d)
        a = tuple(int(v) if isinstance(v, bool) else v for v in args)
        ret = False
        if self.d == 'pg' and not ddl:
            m = re.match(r'\s*INSERT INTO (\w+)', sql)
            if m and m.group(1) in _SERIAL and 'ON CONFLICT' not in sql and 'RETURNING' not in sql:
                s, ret = s + ' RETURNING id', True
        try:
            if self.d == 'pg':
                cur = self.raw.execute(s, a)
                rows = [Row([c.name for c in cur.description], r) for r in cur.fetchall()] if cur.description else []
                last = rows[0]['id'] if ret and rows else None
                return _Cur([] if ret else rows, last, cur.rowcount)
            with self.raw.cursor() as cur:
                cur.execute(s, a)
                rows = [Row([c[0] for c in cur.description], r) for r in cur.fetchall()] if cur.description else []
                return _Cur(rows, cur.lastrowid, cur.rowcount)
        except Exception as e:
            if _is_lock_timeout(e):
                raise DBBusy('数据库忙，稍后重试') from e
            raise

    # ---- 事务：全库写锁语义
    @property
    def in_transaction(self):
        return self.raw.in_transaction if self.d == 'sqlite' else self._tx

    def begin(self):
        try:
            if self.d == 'sqlite':
                self.raw.execute('BEGIN IMMEDIATE')
            elif self.d == 'pg':
                self.raw.execute('BEGIN')
                self._tx = True
                self.raw.execute('SELECT pg_advisory_xact_lock(8121)')
            else:
                with self.raw.cursor() as k:
                    k.execute("SELECT GET_LOCK('kb_tx', 5)")
                    if not (k.fetchone() or [0])[0]:
                        raise DBBusy('数据库忙，稍后重试')
                    k.execute('START TRANSACTION')
                self._tx = True
        except sqlite3.OperationalError as e:
            raise DBBusy('数据库忙，稍后重试') from e
        except DBBusy:
            raise
        except Exception as e:
            if self.d == 'pg':
                self._tx = False
                self._quiet('ROLLBACK')
            if _is_lock_timeout(e):
                raise DBBusy('数据库忙，稍后重试') from e
            raise

    def _end(self, word):
        if self.d == 'sqlite':
            self.raw.execute(word)
            return
        try:
            if self.d == 'pg':
                self.raw.execute(word)
            else:
                (self.raw.commit if word == 'COMMIT' else self.raw.rollback)()
        finally:
            self._tx = False
            if self.d == 'mysql':
                self._quiet("SELECT RELEASE_LOCK('kb_tx')")

    def commit(self):
        self._end('COMMIT')

    def rollback(self):
        self._end('ROLLBACK')

    def _quiet(self, sql):
        try:
            if self.d == 'pg':
                self.raw.execute(sql)
            else:
                with self.raw.cursor() as k:
                    k.execute(sql)
        except Exception:
            pass

    def tables(self):
        if self.d == 'sqlite':
            return [r[0] for r in self.raw.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        if self.d == 'pg':
            return [r[0] for r in self.execute("SELECT tablename FROM pg_tables WHERE schemaname = current_schema()")]
        return [r[0] for r in self.execute('SHOW TABLES')]

    def close(self):
        try:
            self.raw.close()
        except Exception:
            pass


def _is_lock_timeout(e):
    n = type(e).__name__
    return n in ('LockNotAvailable', 'QueryCanceled') or 'lock timeout' in str(e).lower() or \
        (isinstance(e, sqlite3.OperationalError) and ('locked' in str(e) or 'busy' in str(e)))


def _is_conn_lost(e):
    n = type(e).__name__
    s = str(e).lower()
    return n in ('OperationalError', 'InterfaceError') and not isinstance(e, sqlite3.Error) and \
        any(w in s for w in ('closed', 'lost connection', 'server closed', 'gone away', 'terminat', 'broken pipe', 'connection'))


def _open(url):
    return _Conn(url)


def _sweep_dead():
    """关掉已结束线程留下的连接。sqlite 连接自带引用环，线程结束后要等垃圾回收才释放，
    网页每个请求一个线程，攒多了会把文件句柄用光（launchd 默认上限 256）。"""
    with _CONNS_LOCK:
        dead = [k for k, (t, _) in _CONNS.items() if not (t() and t().is_alive())]
        olds = [_CONNS.pop(k)[1] for k in dead]
    for c in olds:
        c.close()


def conn():
    key = db_url()
    c = getattr(_LOCAL, 'c', None)
    if c is None or getattr(_LOCAL, 'key', None) != key:
        _sweep_dead()
        if c is not None:
            with _CONNS_LOCK:
                _CONNS.pop(id(c), None)
            c.close()
        c = _open(key)
        _LOCAL.c, _LOCAL.key = c, key
        with _CONNS_LOCK:
            _CONNS[id(c)] = (weakref.ref(threading.current_thread()), c)
    if key not in _READY:
        with _INIT_LOCK:
            if key not in _READY:
                migrate_schema(c)
                _READY.add(key)
    return c


def reset_conn():
    """KB_ROOT / 数据库换了（测试、kb restore、kb db use）：丢掉本线程连接。"""
    c = getattr(_LOCAL, 'c', None)
    if c is not None:
        with _CONNS_LOCK:
            _CONNS.pop(id(c), None)
        c.close()
    _LOCAL.c = None
    _READY.discard(db_url())


def has_db():
    """库里已经有东西（SQLite 看文件在不在；PG / MySQL 能连上就算）。"""
    if driver() == 'sqlite':
        return DB_FILE.exists() if db_url() == f'sqlite:///{DB_FILE}' else True
    return True


def schema_version(c=None):
    c = c or conn()
    return c.execute('SELECT max(v) FROM schema_version').fetchone()[0] or 0


def migrate_schema(c):
    c.execute('CREATE TABLE IF NOT EXISTS schema_version (v INTEGER PRIMARY KEY, t TEXT)', ddl=True) if c.d != 'sqlite' else \
        c.execute('CREATE TABLE IF NOT EXISTS schema_version (v INTEGER PRIMARY KEY, t TEXT)')
    if (c.execute('SELECT max(v) FROM schema_version').fetchone()[0] or 0) >= len(MIGRATIONS):
        return
    c.begin()                         # 写锁下再读一次：多个进程同时启动只有一个执行迁移
    try:
        cur = c.execute('SELECT max(v) FROM schema_version').fetchone()[0] or 0
        for i, sql in enumerate(MIGRATIONS, 1):
            if i <= cur:
                continue
            for stmt in [s.strip() for s in sql.split(';') if s.strip()]:
                c.execute(stmt, ddl=True) if c.d != 'sqlite' else c.execute(stmt)
            c.execute('INSERT INTO schema_version VALUES (?, ?)', (i, ts_str()))
        c.commit()
    except Exception:
        c.rollback()
        raise


@contextmanager
def tx():
    c = conn()
    if c.in_transaction:                 # 嵌套：并到外层事务
        yield c
        return
    c.begin()
    try:
        yield c
        c.commit()
    except sqlite3.OperationalError as e:
        c.rollback()
        if 'locked' in str(e) or 'busy' in str(e):
            raise DBBusy('数据库忙，稍后重试') from e
        raise
    except BaseException:
        c.rollback()
        raise


def q(sql, args=()):
    for attempt in (0, 1):
        try:
            return conn().execute(sql, args).fetchall()
        except sqlite3.OperationalError as e:
            if 'locked' in str(e) or 'busy' in str(e):
                raise DBBusy('数据库忙，稍后重试') from e
            raise
        except Exception as e:
            if attempt == 0 and _is_conn_lost(e) and not conn().in_transaction:   # 数据库重启过：重连一次
                reset_conn()
                continue
            raise


def q1(sql, args=()):
    r = q(sql, args)
    return r[0] if r else None


def x(sql, args=()):
    with tx() as c:
        return c.execute(sql, args)


def row(r):
    return dict(r) if r is not None else None


# ================================================================ kv（设置等整块 JSON）
def kv_get(k, default=None):
    r = q1('SELECT v, version FROM kv WHERE k=?', (k,))
    return (_l(r['v'], default), r['version']) if r else (default, 0)


def kv_set(k, v, by='', version=None):
    with tx() as c:
        r = c.execute('SELECT version FROM kv WHERE k=?', (k,)).fetchone()
        cur = r['version'] if r else 0
        if version is not None and version != cur:
            raise KBNeed('设置刚被别人改过，刷新后再改', {'version': cur})
        c.execute('INSERT INTO kv (k, v, version, updated, by) VALUES (?, ?, ?, ?, ?) '
                  'ON CONFLICT(k) DO UPDATE SET v=excluded.v, version=excluded.version, updated=excluded.updated, by=excluded.by',
                  (k, _j(v), cur + 1, now_ts(), by))
        return cur + 1


# ================================================================ 用户 / 项目 / 成员
def user_get(name):
    r = row(q1('SELECT * FROM users WHERE name=?', (name,)))
    if r:
        r['profile'] = _l(r['profile'], {}) or {}
    return r


def user_list():
    out = []
    for r in q('SELECT * FROM users ORDER BY created, name'):
        d = dict(r)
        d['profile'] = _l(d['profile'], {}) or {}
        out.append(d)
    return out


def user_count(with_pw=False):
    return q1('SELECT count(*) FROM users WHERE status != ?' + (' AND pw IS NOT NULL' if with_pw else ''), ('停用',))[0]


def user_put(name, **f):
    with tx() as c:
        if c.execute('SELECT 1 FROM users WHERE name=?', (name,)).fetchone():
            if f:
                if 'profile' in f:
                    f['profile'] = _j(f['profile'])
                c.execute(f'UPDATE users SET {", ".join(k + "=?" for k in f)} WHERE name=?', (*f.values(), name))
        else:
            f = dict(f)
            f.setdefault('created', ts_str())
            if 'profile' in f:
                f['profile'] = _j(f['profile'])
            ks = ['name', *f]
            c.execute(f'INSERT INTO users ({",".join(ks)}) VALUES ({",".join("?" * len(ks))})', (name, *f.values()))


def user_fail(name, lock_after, lock_sec):
    """记一次登录失败；连续 lock_after 次锁 lock_sec 秒。返回是否已锁。"""
    with tx() as c:
        r = c.execute('SELECT fails FROM users WHERE name=?', (name,)).fetchone()
        if not r:
            return False
        n = r['fails'] + 1
        until = now_ts() + lock_sec if n >= lock_after else 0
        c.execute('UPDATE users SET fails=?, locked_until=? WHERE name=?', (0 if until else n, until, name))
        return bool(until)


def project_list():
    return [dict(r) for r in q('SELECT * FROM projects ORDER BY created, name')]


def project_get(pid):
    return row(q1('SELECT * FROM projects WHERE id=? OR name=?', (pid, pid)))


def project_put(pid, name, owner):
    with tx() as c:
        if c.execute('SELECT 1 FROM projects WHERE id=?', (pid,)).fetchone():
            c.execute('UPDATE projects SET name=?, owner=? WHERE id=?', (name, owner, pid))
        else:
            c.execute('INSERT INTO projects VALUES (?, ?, ?, ?)', (pid, name, owner, ts_str()))
        if owner:
            c.execute('INSERT INTO members VALUES (?, ?, ?) ON CONFLICT(project, user) DO UPDATE SET role=excluded.role',
                      (pid, owner, '负责人'))


def members_of(pid):
    return [dict(r) for r in q('SELECT user, role FROM members WHERE project=? ORDER BY role, user', (pid,))]


def member_roles(user):
    return {r['project']: r['role'] for r in q('SELECT project, role FROM members WHERE user=?', (user,))}


def member_set(pid, user, role):
    with tx() as c:
        if role:
            c.execute('INSERT INTO members VALUES (?, ?, ?) ON CONFLICT(project, user) DO UPDATE SET role=excluded.role',
                      (pid, user, role))
        else:
            c.execute('DELETE FROM members WHERE project=? AND user=?', (pid, user))


# ================================================================ 网页登录会话 / 令牌
def wsess_add(h, user, pw_ver, expires, project=''):
    with tx() as c:
        c.execute('DELETE FROM web_sessions WHERE expires < ?', (now_ts(),))
        c.execute('INSERT INTO web_sessions VALUES (?, ?, ?, ?, ?)', (h, user, pw_ver, expires, project))


def wsess_get(h):
    return row(q1('SELECT * FROM web_sessions WHERE h=? AND expires > ?', (h, now_ts())))


def wsess_del(h=None, user=None):
    if h:
        x('DELETE FROM web_sessions WHERE h=?', (h,))
    elif user:
        x('DELETE FROM web_sessions WHERE user=?', (user,))
    else:
        x('DELETE FROM web_sessions')


def wsess_keep(h, pw_ver):
    x('UPDATE web_sessions SET pw_ver=? WHERE h=?', (pw_ver, h))


def wsess_project(h, project):
    x('UPDATE web_sessions SET project=? WHERE h=?', (project, h))


def token_put(h, kind, user='', name='', project='', period='', session='', runner='', expires=0, engine=''):
    x('INSERT INTO tokens (h, kind, user, name, project, period, session, runner, created, expires, engine, revoked, last_used) '
      'VALUES (?,?,?,?,?,?,?,?,?,?,?,0,NULL) ON CONFLICT(h) DO UPDATE SET kind=excluded.kind, user=excluded.user, '
      'name=excluded.name, project=excluded.project, period=excluded.period, session=excluded.session, runner=excluded.runner, '
      'created=excluded.created, expires=excluded.expires, engine=excluded.engine, revoked=0, last_used=NULL',
      (h, kind, user, name, project, period, session, runner, ts_str(), expires, engine))


def token_get(h):
    r = row(q1('SELECT * FROM tokens WHERE h=? AND revoked=0', (h,)))
    if r and r['expires'] and r['expires'] < now_ts():
        return None
    return r


def token_touch(h):
    x('UPDATE tokens SET last_used=? WHERE h=?', (ts_str(), h))


def token_find(kind=None, user=None, name=None, session=None, runner=None, alive=True):
    sql, a = 'SELECT * FROM tokens WHERE 1=1', []
    for col, v in (('kind', kind), ('user', user), ('name', name), ('session', session), ('runner', runner)):
        if v is not None:
            sql += f' AND {col}=?'
            a.append(v)
    if alive:
        sql += ' AND revoked=0'
    return [dict(r) for r in q(sql + ' ORDER BY created', a)]


def token_revoke(h=None, user=None, kind=None, session=None, runner=None):
    sql, a = 'UPDATE tokens SET revoked=1 WHERE 1=1', []
    for col, v in (('h', h), ('user', user), ('kind', kind), ('session', session), ('runner', runner)):
        if v is not None:
            sql += f' AND {col}=?'
            a.append(v)
    if len(a) == 0:
        raise KBError('token_revoke 要带条件')
    return x(sql, a).rowcount


def token_delete(h):
    x('DELETE FROM tokens WHERE h=?', (h,))


# ================================================================ 期（元数据 + 乐观锁）、记录
def period_row(name):
    r = row(q1('SELECT * FROM periods WHERE name=?', (name,)))
    if r:
        r['meta'] = _l(r['meta'], {}) or {}
    return r


def period_rows(project=None):
    sql, a = 'SELECT name, project, owner, version FROM periods', ()
    if project:
        sql, a = sql + ' WHERE project=?', (project,)
    return [dict(r) for r in q(sql, a)]


def period_save(name, meta, changed, removed, by='', version=None, project=None, owner=None):
    """按字段合并写入：只改 changed 里的键、删 removed 里的键，别的请求同时改的其他字段保留（REQ-008）。
    version 不为空且和库里不一致时抛 KBNeed（REQ-030）。返回合并后的 (meta, version)。"""
    with tx() as c:
        r = c.execute('SELECT meta, version, updated_by, project, owner FROM periods WHERE name=?', (name,)).fetchone()
        if r is None:
            cur, ver = dict(meta), 0
            proj, own = project or '', owner or ''
        else:
            if version is not None and version != r['version']:
                who = r['updated_by'] or '别人'
                raise KBNeed(f'这一期刚被 {who} 改过，刷新后再改', {'version': r['version'], 'by': who})
            cur, ver = _l(r['meta'], {}) or {}, r['version']
            for k in changed:
                cur[k] = meta[k]
            for k in removed or ():
                cur.pop(k, None)
            proj = r['project'] if project is None else project
            own = r['owner'] if owner is None else owner
        c.execute('INSERT INTO periods (name, project, owner, version, meta, updated, updated_by) VALUES (?,?,?,?,?,?,?) '
                  'ON CONFLICT(name) DO UPDATE SET project=excluded.project, owner=excluded.owner, version=excluded.version, '
                  'meta=excluded.meta, updated=excluded.updated, updated_by=excluded.updated_by',
                  (name, proj, own, ver + 1, _j(cur), now_ts(), by))
        return cur, ver + 1


def period_set_attr(name, project=None, owner=None, version=None, by=''):
    with tx() as c:
        r = c.execute('SELECT version, updated_by FROM periods WHERE name=?', (name,)).fetchone()
        if r is None:
            raise KBError(f'没有这一期：{name}')
        if version is not None and version != r['version']:
            who = r['updated_by'] or '别人'
            raise KBNeed(f'这一期刚被 {who} 改过，刷新后再改', {'version': r['version'], 'by': who})
        sets, a = ['version=version+1', 'updated=?', 'updated_by=?'], [now_ts(), by]
        if project is not None:
            sets.append('project=?')
            a.append(project)
        if owner is not None:
            sets.append('owner=?')
            a.append(owner)
        c.execute(f'UPDATE periods SET {", ".join(sets)} WHERE name=?', (*a, name))


def period_delete(name):
    x('DELETE FROM periods WHERE name=?', (name,))


def record_add(period, t, action, content, actor):
    x('INSERT INTO records (period, t, action, content, actor) VALUES (?,?,?,?,?)', (period, t, action, content, actor))


def records_of(period, limit=1000):
    return [dict(r) for r in q('SELECT t, action, content, actor FROM records WHERE period=? ORDER BY id DESC LIMIT ?',
                               (period, limit))][::-1]


def record_count(period=None):
    if period:
        return q1('SELECT count(*) FROM records WHERE period=?', (period,))[0]
    return q1('SELECT count(*) FROM records')[0]


# ================================================================ 审计
def audit(kind, subject, obj='', project='', result='成功', detail=''):
    x('INSERT INTO audit (ts, kind, subject, obj, project, result, detail) VALUES (?,?,?,?,?,?,?)',
      (now_ts(), kind, subject, obj, project, result, detail if isinstance(detail, str) else _j(detail)))


def audit_query(kind=None, project=None, subject=None, obj=None, since=0, page=1, size=50, projects=None):
    where, a = ['ts >= ?'], [since]
    for col, v in (('kind', kind), ('project', project), ('subject', subject), ('obj', obj)):
        if v:
            where.append(f'{col}=?' if col != 'subject' else 'subject LIKE ?')
            a.append(v if col != 'subject' else f'%{v}%')
    if projects is not None:
        where.append(f'project IN ({",".join("?" * len(projects)) or "NULL"})')
        a.extend(projects)
    w = ' AND '.join(where)
    total = q1(f'SELECT count(*) FROM audit WHERE {w}', a)[0]
    rows = q(f'SELECT * FROM audit WHERE {w} ORDER BY id DESC LIMIT ? OFFSET ?', (*a, size, (page - 1) * size))
    return total, [dict(r) for r in rows]


def audit_prune(days=90):
    x('DELETE FROM audit WHERE ts < ?', (now_ts() - days * 86400,))


# ================================================================ 定时任务租约（REQ-009）
def lease_try(name, period, holder, lease_sec):
    """到点且租约空闲（或自己持有、或已过期）就拿到租约并返回 True。同一周期只有一个进程拿到。"""
    t = now_ts()
    with tx() as c:
        r = c.execute('SELECT * FROM schedules WHERE name=?', (name,)).fetchone()
        if r is None:
            c.execute('INSERT INTO schedules (name, period, holder, lease_until) VALUES (?, ?, ?, ?)', (name, period, holder, t + lease_sec))
            return True
        if r['lease_until'] > t and r['holder'] != holder:
            return False
        if t - (r['last_run'] or 0) < period:
            return False
        c.execute('UPDATE schedules SET holder=?, lease_until=?, period=? WHERE name=?', (holder, t + lease_sec, period, name))
        return True


def lease_done(name, holder, result):
    x('UPDATE schedules SET last_run=?, last_result=?, runs=runs+1, lease_until=0 WHERE name=? AND holder=?',
      (now_ts(), result[:500], name, holder))


def schedules():
    return [dict(r) for r in q('SELECT * FROM schedules ORDER BY name')]


# ================================================================ 事件（跨进程广播给 SSE）
def event_add(kind, project='', data=None):
    with tx() as c:
        cur = c.execute('INSERT INTO events (ts, kind, project, data) VALUES (?,?,?,?)', (now_ts(), kind, project, _j(data or {})))
        if cur.lastrowid % 500 == 0:
            c.execute('DELETE FROM events WHERE ts < ?', (now_ts() - 3600,))
        return cur.lastrowid


def events_after(eid):
    return [dict(r, data=_l(r['data'], {})) for r in q('SELECT * FROM events WHERE id > ? ORDER BY id LIMIT 500', (eid,))]


def event_last_id():
    return q1('SELECT max(id) FROM events')[0] or 0


# ================================================================ 机密元数据（层级 / 所属）
def secret_meta(sid):
    return row(q1('SELECT * FROM secrets_meta WHERE id=?', (sid,)))


def secret_meta_put(sid, layer, owner='', project=''):
    x('INSERT INTO secrets_meta VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET layer=excluded.layer, owner=excluded.owner, '
      'project=excluded.project', (sid, layer, owner, project))


def secret_meta_del(sid):
    x('DELETE FROM secrets_meta WHERE id=?', (sid,))


def secret_meta_by_owner(owner):
    return [dict(r) for r in q('SELECT * FROM secrets_meta WHERE layer=? AND owner=?', ('个人', owner))]


def secret_meta_all():
    return [dict(r) for r in q('SELECT * FROM secrets_meta')]


# ================================================================ Bug 所属（平台 + 编号去重，按项目、所属人）
def bug_owner_put(key, project, owner):
    x('INSERT INTO bug_owners VALUES (?,?,?,?) ON CONFLICT(key) DO UPDATE SET project=excluded.project, owner=excluded.owner, '
      'updated=excluded.updated', (key, project, owner, ts_str()))


def bug_owners():
    return {r['key']: dict(r) for r in q('SELECT * FROM bug_owners')}


# ================================================================ 通用：按表的 JSON 列读写（阶段二各模块用）
_JSON_COLS = {'auto_tags', 'custom_tags', 'info', 'payload', 'score', 'result', 'attrs', 'cond', 'granted', 'bad_models',
              'streak', 'tags', 'net', 'config'}
_TABLES = {'runners', 'tasks', 'metrics', 'alerts', 'resources', 'holds', 'waits', 'engine_accounts', 'engine_calls',
           'usage', 'budgets', 'grants', 'test_accounts', 'artifacts', 'evidence', 'providers', 'recalls'}


def _dec(r):
    if r is None:
        return None
    d = dict(r)
    for k in _JSON_COLS & d.keys():
        d[k] = _l(d[k], d[k])
    return d


def _enc(f):
    return {k: (_j(v) if k in _JSON_COLS and not isinstance(v, str) else v) for k, v in f.items()}


def _t(table):
    if table not in _TABLES:
        raise KBError(f'未知的表：{table}')
    return table


def _where(where):
    ks = list(where)
    w = ' AND '.join(f'{k}=?' for k in ks) or '1=1'
    return w, [where[k] for k in ks]


def rows(table, where=None, order='', limit=0, extra='', args=()):
    """按等值条件取若干行（extra 是附加条件片段，只在本文件和仓储函数里写）。"""
    w, a = _where(where or {})
    sql = f'SELECT * FROM {_t(table)} WHERE {w}' + (f' AND {extra}' if extra else '') + (f' ORDER BY {order}' if order else '') + \
          (f' LIMIT {int(limit)}' if limit else '')
    return [_dec(r) for r in q(sql, (*a, *args))]


def get(table, **where):
    r = rows(table, where, limit=1)
    return r[0] if r else None


def insert(table, **f):
    f = _enc(f)
    with tx() as c:
        cur = c.execute(f'INSERT INTO {_t(table)} ({",".join(f)}) VALUES ({",".join("?" * len(f))})', tuple(f.values()))
        return cur.lastrowid


def upsert(table, key, **f):
    f = _enc(f)
    with tx() as c:
        if c.execute(f'SELECT 1 FROM {_t(table)} WHERE {key}=?', (f[key],)).fetchone():
            sets = [k for k in f if k != key]
            if sets:
                c.execute(f'UPDATE {table} SET {", ".join(k + "=?" for k in sets)} WHERE {key}=?', (*[f[k] for k in sets], f[key]))
        else:
            c.execute(f'INSERT INTO {table} ({",".join(f)}) VALUES ({",".join("?" * len(f))})', tuple(f.values()))


def update(table, where, **f):
    f = _enc(f)
    w, a = _where(where)
    with tx() as c:
        return c.execute(f'UPDATE {_t(table)} SET {", ".join(k + "=?" for k in f)} WHERE {w}', (*f.values(), *a)).rowcount


def delete(table, where=None, extra='', args=()):
    w, a = _where(where or {})
    with tx() as c:
        return c.execute(f'DELETE FROM {_t(table)} WHERE {w}' + (f' AND {extra}' if extra else ''), (*a, *args)).rowcount


def count(table, where=None, extra='', args=()):
    w, a = _where(where or {})
    return q1(f'SELECT count(*) FROM {_t(table)} WHERE {w}' + (f' AND {extra}' if extra else ''), (*a, *args))[0]


# ---- 专用查询（阶段二）
def task_queue_rows(status_in, project=None):
    sql = f'SELECT * FROM tasks WHERE status IN ({",".join("?" * len(status_in))})'
    a = list(status_in)
    if project:
        sql += ' AND project=?'
        a.append(project)
    return [_dec(r) for r in q(sql + ' ORDER BY created', a)]


def task_cas(tid, from_status, **f):
    """状态机转换：只有当前状态在 from_status 里才改，返回是否改成。"""
    f = _enc(f)
    with tx() as c:
        r = c.execute('SELECT status FROM tasks WHERE id=?', (tid,)).fetchone()
        if not r or r['status'] not in from_status:
            return False
        c.execute(f'UPDATE tasks SET {", ".join(k + "=?" for k in f)} WHERE id=?', (*f.values(), tid))
        return True


def metrics_agg(runner, since, bucket=60):
    """按 bucket 秒聚合（平均值、网络取最大）；在 Python 里分桶，三种库结果一致。"""
    acc = {}
    for r in q('SELECT ts, cpu, mem, disk, net FROM metrics WHERE runner=? AND ts >= ? ORDER BY ts', (runner, since)):
        b = int(r['ts'] // bucket) * bucket
        a = acc.setdefault(b, {'b': b, 'cpu': [], 'mem': [], 'disk': [], 'net': None})
        for k in ('cpu', 'mem', 'disk'):
            if r[k] is not None:
                a[k].append(r[k])
        if r['net'] is not None and (a['net'] is None or r['net'] > a['net']):
            a['net'] = r['net']
    out = []
    for b in sorted(acc):
        a = acc[b]
        out.append({'b': b, **{k: (sum(a[k]) / len(a[k]) if a[k] else None) for k in ('cpu', 'mem', 'disk')}, 'net': _l(a['net'], {})})
    return out


def metrics_window(runner, since):
    return [dict(r, net=_l(r['net'], {})) for r in q('SELECT * FROM metrics WHERE runner=? AND ts >= ? ORDER BY ts', (runner, since))]


def metrics_prune(days=7):
    x('DELETE FROM metrics WHERE ts < ?', (now_ts() - days * 86400,))


def usage_sum(col, since, value=None, project=None):
    sql = f'SELECT {col} AS k, count(DISTINCT task) n, sum(seconds) s, sum(tin) i, sum(tout) o, max(estimated) e FROM usage WHERE ts >= ?'
    a = [since]
    if value is not None:
        sql += f' AND {col}=?'
        a.append(value)
    if project:
        sql += ' AND project=?'
        a.append(project)
    return [dict(r) for r in q(sql + f' GROUP BY {col}', a)]


def usage_tokens(col, value, since):
    r = q1(f'SELECT coalesce(sum(tin), 0) + coalesce(sum(tout), 0) FROM usage WHERE {col}=? AND ts >= ?', (value, since))
    return r[0] or 0


def calls_window(account, since):
    rs = q('SELECT code, count(*) n FROM engine_calls WHERE account=? AND ts >= ? GROUP BY code', (account, since))
    return {r['code']: r['n'] for r in rs}


def calls_prune(hours=2):
    x('DELETE FROM engine_calls WHERE ts < ?', (now_ts() - hours * 3600,))


def grants_active(project):
    return q1('SELECT coalesce(sum(amount), 0) FROM grants WHERE project=? AND until > ?', (project, now_ts()))[0]


def holds_active(resource=None, session=None):
    sql, a = 'SELECT * FROM holds WHERE 1=1', []
    if resource:
        sql += ' AND resource=?'
        a.append(resource)
    if session:
        sql += ' AND session=?'
        a.append(session)
    return [_dec(r) for r in q(sql + ' ORDER BY start', a)]


def table_counts():
    out = {}
    for t in ('users', 'projects', 'periods', 'records', 'tokens', 'secrets_meta', 'tasks', 'runners', 'resources'):
        out[t] = q1(f'SELECT count(*) FROM {t}')[0]
    return out


def all_tables():
    """MIGRATIONS 里建的全部表（不含 schema_version）。"""
    out = []
    for sql in MIGRATIONS:
        out += [t for t in re.findall(r'CREATE TABLE (?:IF NOT EXISTS )?(\w+)', sql) if t not in out]
    return out


def copy_rows(src, dst, tables=None):
    """把 src 连接里各表的行整表复制到 dst（dst 先清空这些表），一个事务。两边可以是不同驱动。返回 {表: 行数}。"""
    tables = tables or all_tables()
    have = set(src.tables())
    dst_cols = {}
    out = {}
    dst.begin()
    try:
        for t in tables:
            if t not in have:
                continue
            rs = src.execute(f'SELECT * FROM {t}').fetchall()
            dst.execute(f'DELETE FROM {t}')
            if rs:
                if t not in dst_cols:
                    dst_cols[t] = set(_cols_of(dst, t))
                cols = [k for k in rs[0].keys() if k in dst_cols[t]]
                ins = f'INSERT INTO {t} ({", ".join(cols)}) VALUES ({", ".join("?" * len(cols))})'
                for r in rs:
                    d = dict(r)
                    dst.execute(ins, tuple(d[k] for k in cols))
                if dst.d == 'pg' and t in _SERIAL:
                    dst.execute(f"SELECT setval(pg_get_serial_sequence('{t}', 'id'), (SELECT coalesce(max(id), 1) FROM {t}))")
            out[t] = len(rs)
        dst.commit()
    except BaseException:
        dst.rollback()
        raise
    return out


def _cols_of(c, t):
    if c.d == 'sqlite':
        return [r[1] for r in c.raw.execute(f'PRAGMA table_info({t})')]
    if c.d == 'pg':
        return [r[0] for r in c.execute('SELECT column_name FROM information_schema.columns WHERE table_name = ? '
                                        'AND table_schema = current_schema()', (t,))]
    return [r[0] for r in c.execute('SELECT column_name FROM information_schema.columns WHERE table_name = ? '
                                    'AND table_schema = DATABASE()', (t,))]


def backup_to(path):
    """备份成一个 SQLite 文件（不管当前是哪种库），可以直接拿去 restore_from / 搬家。"""
    path = str(path)
    if driver() == 'sqlite':
        dst = sqlite3.connect(path)
        try:
            conn().raw.backup(dst)
        finally:
            dst.close()
        return
    if os.path.exists(path):
        os.remove(path)
    dst = _open(f'sqlite:///{path}')
    try:
        migrate_schema(dst)
        copy_rows(conn(), dst)
    finally:
        dst.close()


def restore_from(path):
    """用 SQLite 备份文件覆盖当前库（当前是 SQLite 就走在线备份接口；PG / MySQL 逐表导入）。"""
    if driver() == 'sqlite':
        src = sqlite3.connect(str(path))
        try:
            src.backup(conn().raw)
        finally:
            src.close()
        return
    src = _open(f'sqlite:///{path}')
    try:
        copy_rows(src, conn())
    finally:
        src.close()


def copy_to(url, force=False):
    """kb db copy --to：把当前库整库复制到另一个库（目标先建表；目标已有数据要 force）。返回 {表: 行数}。"""
    if url == db_url():
        raise KBError('目标就是当前库')
    dst = _open(url)
    try:
        migrate_schema(dst)
        if not force:
            n = sum((dst.execute(f'SELECT count(*) FROM {t}').fetchone()[0] or 0) for t in ('users', 'periods', 'kv'))
            if n:
                raise KBError('目标库里已经有数据：确认要覆盖就加 --force')
        return copy_rows(conn(), dst)
    finally:
        dst.close()


def check_url(url):
    """连得上、能建表就返回 (驱动, 表版本)；kb db use 用。"""
    c = _open(url)
    try:
        migrate_schema(c)
        return c.d, c.execute('SELECT max(v) FROM schema_version').fetchone()[0] or 0
    finally:
        c.close()


def use_url(url):
    """kb db use：写 _system/db.url（权限 600），之后本机所有 kb 进程都用它；sqlite 或空 = 回到默认 SQLite 文件。"""
    url = (url or '').strip()
    if not url or url == 'sqlite' or url == f'sqlite:///{DB_FILE}':
        URL_FILE.unlink(missing_ok=True)
    else:
        check_url(url)
        fd = os.open(str(URL_FILE), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(url + '\n')
    reset_conn()
    return db_url()


def status():
    """kb db status：驱动、连接串（去密码）、表版本、各表行数。"""
    c = conn()
    return {'驱动': {'sqlite': 'SQLite', 'pg': 'PostgreSQL', 'mysql': 'MySQL'}[c.d], '连接': safe_url(),
            '来源': 'KB_DB_URL' if os.environ.get('KB_DB_URL') else ('db.url' if URL_FILE.exists() else '默认'),
            '表版本': schema_version(c), '行数': table_counts()}


def wipe():
    """清空成一个新库（回滚到「还没有库」的状态）。"""
    c = conn()
    if c.d == 'sqlite':
        names = c.tables()
        c.begin()
        for n in names:
            c.execute(f'DELETE FROM {n}')
        c.commit()
    else:
        for n in [*all_tables(), 'schema_version']:
            if n in c.tables():
                c.execute(f'DROP TABLE {n}', ddl=True)
    _READY.discard(db_url())


def is_busy_error(e):
    return isinstance(e, DBBusy)


# ---- 资源池（REQ-045 REQ-048）
def holds_like(pattern):
    """资源编号按前缀匹配的占用（如 phone:local:%），按开始时间。"""
    return [_dec(r) for r in q('SELECT * FROM holds WHERE resource LIKE ? ORDER BY start', (pattern,))]


def usage_of_task(task):
    r = q1('SELECT coalesce(sum(tin), 0) + coalesce(sum(tout), 0) FROM usage WHERE task=?', (task,))
    return r[0] or 0


def usage_rows(since, col=None, value=None):
    sql, a = 'SELECT * FROM usage WHERE ts >= ?', [since]
    if col:
        sql += f' AND {col}=?'
        a.append(value)
    return [dict(r) for r in q(sql, a)]


# ---- 管理员总览（T002）
def record_counts(since_str, by_day=False):
    """记录条数：按期，或按天（since_str 是 'YYYY-MM-DD …'）。"""
    if by_day:
        return {r['d']: r['n'] for r in q('SELECT substr(t, 1, 10) d, count(*) n FROM records WHERE t >= ? GROUP BY substr(t, 1, 10)',
                                          (since_str,))}
    return {r['period']: r['n'] for r in q('SELECT period, count(*) n FROM records WHERE t >= ? GROUP BY period', (since_str,))}


def audit_counts(since, kinds=None):
    """近一段时间审计：{(类型, 结果): 次数}。"""
    rs = q('SELECT kind, result, count(*) n FROM audit WHERE ts >= ? GROUP BY kind, result', (since,))
    return {(r['kind'], r['result']): r['n'] for r in rs if not kinds or r['kind'] in kinds}


def calls_since(since):
    return {r['account']: r['n'] for r in q('SELECT account, count(*) n FROM engine_calls WHERE ts >= ? GROUP BY account', (since,))}
