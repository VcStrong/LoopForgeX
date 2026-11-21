"""元数据库三种驱动（REQ-021 REQ-008 REQ-048）：SQL 方言翻译、跨库整库复制、全库写锁的并发安全。

    cd _system && python3 -m unittest tests.test_db_drivers -v
    # 连真库一起测（没有就跳过这几条）：
    KB_TEST_PG_URL=postgresql://kb:pw@127.0.0.1:5432/kb KB_TEST_MYSQL_URL=mysql://kb:pw@127.0.0.1:3306/kb python3 -m unittest tests.test_db_drivers

真库测试会清空目标库里 kb 的表，只给测试专用的库。
"""
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

if not os.environ.get('KB_ROOT', '').startswith(tempfile.gettempdir().rstrip('/')):
    TMP = Path(tempfile.mkdtemp(prefix='kb-dbdrv-test-'))
    os.environ['KB_ROOT'] = str(TMP / 'kb')
    os.environ['HOME'] = str(TMP / 'home')
    (TMP / 'kb').mkdir()
    (TMP / 'home').mkdir()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kblib.core import db  # noqa: E402

TARGETS = [(n, os.environ.get(v)) for n, v in (('pg', 'KB_TEST_PG_URL'), ('mysql', 'KB_TEST_MYSQL_URL'))]


def _drop_all(c):
    for t in [*db.all_tables(), 'schema_version']:
        if t in c.tables():
            c.execute(f'DROP TABLE {t}', ddl=True)


def _sample(c):
    """一份覆盖各种列的数据：保留字列名、中文、JSON、自增表、浮点时间戳。"""
    c.begin()
    c.execute("INSERT INTO users (name, pw, sys_role, created, profile, local) VALUES (?, ?, ?, ?, ?, ?)",
              ('张三', 'h', '管理员', '2026-11-20 10:00:00', '{"昵称": "三"}', 1))
    c.execute('INSERT INTO members VALUES (?, ?, ?)', ('default', '张三', '负责人'))
    c.execute('INSERT INTO bug_owners VALUES (?, ?, ?, ?)', ('禅道#1', 'default', '张三', 't'))
    for i in range(3):
        c.execute('INSERT INTO audit (ts, kind, subject, obj, project, result, detail) VALUES (?,?,?,?,?,?,?)',
                  (1763600000.123456 + i, '登录', '张三', '', 'default', '成功', '100% 正常'))
    c.execute('INSERT INTO usage (ts, user, project, account, task, seconds, tin, tout) VALUES (?,?,?,?,?,?,?,?)',
              (1.5, '张三', 'default', 'A1', 'T1', 60, 1000, 2000))
    c.execute("INSERT INTO kv (k, v, version, updated, by) VALUES (?, ?, ?, ?, ?)", ('设置', '{"a": [1, 2]}', 1, 2.0, '张三'))
    c.commit()


def _dump(c):
    out = {}
    for t in ('users', 'members', 'bug_owners', 'audit', 'usage', 'kv'):
        out[t] = sorted(tuple(sorted((k, v) for k, v in dict(r).items())) for r in c.execute(f'SELECT * FROM {t}').fetchall())
    return out


class Translate(unittest.TestCase):
    def test_placeholders_and_reserved(self):
        s = db._tr('SELECT user, role FROM members WHERE project=? AND user LIKE ? ORDER BY role, user', 'pg')
        self.assertEqual(s, 'SELECT "user", role FROM members WHERE project=%s AND "user" LIKE %s ORDER BY role, "user"')
        s = db._tr('SELECT * FROM usage WHERE ts >= ? AND end IS NULL', 'mysql')
        self.assertEqual(s, 'SELECT * FROM `usage` WHERE ts >= %s AND `end` IS NULL')
        self.assertEqual(db._tr('SELECT 1 FROM tokens_user', 'pg'), 'SELECT 1 FROM tokens_user')   # 整词才加引号
        self.assertEqual(db._tr("SELECT '100%'", 'pg'), "SELECT '100%%'")
        self.assertEqual(db._tr('SELECT ?', 'sqlite'), 'SELECT ?')

    def test_upsert(self):
        sql = 'INSERT INTO kv (k, v, by) VALUES (?, ?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v, by=excluded.by'
        self.assertIn('ON CONFLICT(k) DO UPDATE SET v=excluded.v, "by"=excluded."by"', db._tr(sql, 'pg'))
        self.assertTrue(db._tr(sql, 'mysql').endswith('ON DUPLICATE KEY UPDATE v=VALUES(v), `by`=VALUES(`by`)'))

    def test_ddl(self):
        pg = db._ddl('CREATE TABLE audit (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, by TEXT)', 'pg')
        self.assertEqual(pg, 'CREATE TABLE audit (id BIGSERIAL PRIMARY KEY, ts DOUBLE PRECISION, "by" TEXT)')
        my = db._ddl("CREATE TABLE runners (id TEXT PRIMARY KEY, info TEXT DEFAULT '{}', capacity INTEGER DEFAULT 2)", 'mysql')
        self.assertIn('`id` VARCHAR(255) PRIMARY KEY'.replace('`id`', 'id'), my)
        self.assertIn("info LONGTEXT DEFAULT ('{}')", my)
        self.assertIn('capacity BIGINT DEFAULT 2', my)
        self.assertIn('COLLATE=utf8mb4_bin', my)
        # 建了索引的 TEXT 列要能建索引
        self.assertIn('kind VARCHAR(255)', db._ddl('CREATE TABLE audit (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, detail TEXT)', 'mysql'))
        self.assertIn('detail LONGTEXT', db._ddl('CREATE TABLE audit (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, detail TEXT)', 'mysql'))

    def test_url(self):
        self.assertEqual(db.dialect_of('postgresql://a:b@h/kb'), 'pg')
        self.assertEqual(db.dialect_of('mysql://a:b@h/kb'), 'mysql')
        self.assertEqual(db.dialect_of('sqlite:///x.db'), 'sqlite')
        self.assertEqual(db.safe_url('postgresql://kb:secret@h:5432/kb'), 'postgresql://kb:***@h:5432/kb')
        with self.assertRaises(Exception):
            db.dialect_of('oracle://x')

    def test_sqlite_roundtrip(self):
        d = Path(tempfile.mkdtemp())
        a, b = db._open(f'sqlite:///{d}/a.db'), db._open(f'sqlite:///{d}/b.db')
        db.migrate_schema(a)
        db.migrate_schema(b)
        _sample(a)
        db.copy_rows(a, b)
        self.assertEqual(_dump(a), _dump(b))


class RealDB(unittest.TestCase):
    """连得上真 PG / MySQL 时：SQLite → 真库 → SQLite 整库往返不丢不变；10 个线程并发「读后写」不丢更新。"""

    def _each(self):
        got = False
        for name, url in TARGETS:
            if url:
                got = True
                with self.subTest(driver=name):
                    yield name, url
        if not got:
            self.skipTest('没设 KB_TEST_PG_URL / KB_TEST_MYSQL_URL')

    def test_roundtrip(self):
        for name, url in self._each():
            d = Path(tempfile.mkdtemp())
            src, back = db._open(f'sqlite:///{d}/src.db'), db._open(f'sqlite:///{d}/back.db')
            db.migrate_schema(src)
            db.migrate_schema(back)
            _sample(src)
            t = db._open(url)
            _drop_all(t)
            db.migrate_schema(t)
            n = db.copy_rows(src, t)
            self.assertEqual(n['audit'], 3)
            self.assertEqual(_dump(src), _dump(t))
            # 自增在复制后接着往上长（PG 要 setval）
            t.begin()
            nid = t.execute('INSERT INTO audit (ts, kind) VALUES (?, ?)', (1.0, 'x')).lastrowid
            t.commit()
            self.assertGreater(nid, 3)
            # SUM 之类返回的是普通数字，不是 Decimal（json 能序列化）
            r = t.execute('SELECT sum(tin) s, sum(seconds) n FROM usage').fetchone()
            self.assertEqual((type(r['s']), r['s'], r[1]), (int, 1000, 60))
            t.execute('DELETE FROM audit WHERE kind=?', ('x',))
            db.copy_rows(t, back)
            self.assertEqual(_dump(src), _dump(back))
            t.close()

    def test_write_lock(self):
        for name, url in self._each():
            t = db._open(url)
            _drop_all(t)
            db.migrate_schema(t)
            t.execute("INSERT INTO kv (k, v, version) VALUES ('计数', '0', 0)")
            errs = []

            def inc():
                c = db._open(url)
                try:
                    for _ in range(5):
                        c.begin()
                        v = int(c.execute("SELECT v FROM kv WHERE k='计数'").fetchone()[0])
                        c.execute("UPDATE kv SET v=? WHERE k='计数'", (str(v + 1),))
                        c.commit()
                except Exception as e:      # noqa: BLE001
                    errs.append(e)
                finally:
                    c.close()
            ths = [threading.Thread(target=inc) for _ in range(10)]
            [x.start() for x in ths]
            [x.join() for x in ths]
            self.assertEqual(errs, [])
            self.assertEqual(t.execute("SELECT v FROM kv WHERE k='计数'").fetchone()[0], '50')
            t.close()


if __name__ == '__main__':
    unittest.main()
