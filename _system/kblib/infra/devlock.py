# From 大大王.DT
"""手机占用记录：kb.py 和 bin/adb（知识库 adb 守卫）共用，不依赖 kb.py，启动要快。

多个期（不同分支）的后台会话可能同时在测试，同一台手机被两边装包会互相覆盖、打断测试。
记录在资源池（数据库 holds，资源 phone:local:<序列号>，REQ-048）；知识库/设备占用.json 是导出视图。
本模块是资源池的适配层，对外仍是 序列号 → {期, 分支, 仓库, 方式, 开始, 最后使用} 的字典接口（bin/adb 守卫、运行到手机、装包都在用）。
方式：申请（kb_device_claim）、自动（会话第一次往空闲手机装包 / 操作时记下）、本人（网页上标「我在用」）。
"""
import fcntl
import json
import os
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

SYS = Path(__file__).resolve().parents[2]      # kblib/infra/devlock.py → _system
ROOT = Path(os.environ.get('KB_ROOT') or SYS.parent)
FILE = ROOT / '设备占用.json'
LOCK = ROOT / '.设备占用.lock'
RECENT = ROOT / '.设备最近使用.json'     # 序列号 → 最近一次被申请的时间；释放后也保留，用来轮流挑手机
BIN = SYS / 'bin'
IDLE = {'自动': 30 * 60, '申请': 12 * 3600, '本人': 4 * 3600}   # 多久没用自动释放
LOOSE = 2 * 3600       # 不是知识库后台会话（终端里手动开发）申请的，没人释放时最多保留这么久


def stamp(t=None):
    return time.strftime('%Y-%m-%d %H:%M', time.localtime(t or time.time()))


def _kdb():
    """数据库（资源池）：拿不到（老环境、只读沙箱）时返回 None，退回只用 设备占用.json。"""
    try:
        from kblib.core import db
        db.conn()
    except Exception:  # noqa: BLE001
        return None
    _import_once(db)
    return db


def _import_once(db):
    """升级：老的 设备占用.json 导入资源池一次。"""
    if _IMPORTED or db.kv_get('设备占用已导入')[0]:
        _IMPORTED.append(1)
        return
    _IMPORTED.append(1)
    old = _load_file()
    if old:
        try:
            with db.tx():
                _write_db(db, {}, {k: v for k, v in old.items() if isinstance(v, dict)})
        except Exception as e:  # noqa: BLE001
            print('设备占用导入资源池失败：', repr(e))
            return
    db.kv_set('设备占用已导入', True, by='升级')


_IMPORTED = []


def _rid(sn):
    return f'phone:local:{sn}'


def _rec(h):
    """占用行 → 老格式记录（期、方式、开始、最后使用、分支、仓库、型号、用途）。"""
    info = dict(h.get('info') or {})
    sess = h.get('session') or ''
    r = dict(info, 期=h.get('period') or '', 方式=h.get('mode') or '自动', 开始=info.get('开始') or stamp(h.get('start')),
             最后使用=float(h.get('last_active') or h.get('start') or 0))
    if sess and not sess.startswith('period:'):
        r.update(会话=sess, 来源='资源池')
    if h.get('user'):
        r['人'] = h['user']
    return r


def _file_sig():
    try:
        st = FILE.stat()
        return f'{st.st_mtime_ns}:{st.st_size}'
    except OSError:
        return ''


def _sync_file(db):
    """设备占用.json 被外面改了（手工编辑、老脚本、测试重置）：以文件为准同步进资源池。调用方已在事务里。"""
    sig = _file_sig()
    if not sig or sig == (db.kv_get('设备占用导出')[0] or ''):
        return
    data = {k: v for k, v in _load_file().items() if isinstance(v, dict)}
    _write_db(db, _load_db(db, False), data)
    db.kv_set('设备占用导出', sig, by='设备占用')


def _load_db(db, sync=True):
    if sync:
        _sync_file(db)
    out = {}
    for h in db.holds_like('phone:local:%'):
        out.setdefault(h['resource'].split(':', 2)[2], _rec(h))
    return out


def load():
    db = _kdb()
    if db is None:
        return _load_file()
    if _file_sig() and _file_sig() != (db.kv_get('设备占用导出')[0] or ''):
        with db.tx():
            return _load_db(db)
    return _load_db(db, False)


def _load_file():
    try:
        return json.loads(FILE.read_text('utf-8'))
    except (OSError, ValueError):
        return {}


def _save(data):
    """导出视图 设备占用.json（网页按它的修改时间刷新）。"""
    try:
        tmp = FILE.with_suffix('.tmp')
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), 'utf-8')
        tmp.replace(FILE)
    except OSError:
        pass


def _write_db(db, before, after):
    now = time.time()
    for sn in set(before) | set(after):
        a, b = after.get(sn), before.get(sn)
        if a == b:
            continue
        rid = _rid(sn)
        old = db.rows('holds', {'resource': rid})
        db.delete('holds', {'resource': rid})
        if not isinstance(a, dict):
            continue
        if not db.get('resources', id=rid):
            db.upsert('resources', 'id', id=rid, type='手机', provider='local', runner='local',
                      attrs={'序列号': sn, '型号': a.get('型号') or '', '系统': 'Android'}, capacity=1, enabled=1)
        mode = a.get('方式') or '自动'
        sess = a.get('会话') or next((h['session'] for h in old if h.get('period') == a.get('期')), '') or \
            (f"period:{a['期']}" if a.get('期') else '')
        info = {k: v for k, v in a.items() if k not in ('期', '方式', '最后使用', '会话', '来源', '人')}
        db.insert('holds', resource=rid, session=sess, period=a.get('期') or '', user=a.get('人') or (old[0].get('user') if old else ''),
                  mode=mode, start=(old[0]['start'] if old and old[0].get('period') == a.get('期') else now),
                  expires=0, last_active=float(a.get('最后使用') or now), idle_limit=IDLE.get(mode, IDLE['自动']), ticket='',
                  info=info, project=(old[0].get('project') if old else '') or '')


@contextmanager
def locked():
    """加锁读改写：with locked() as data: 修改 data，退出时保存。
    数据在资源池（数据库 holds，phone:local:<序列号>）：一个 BEGIN IMMEDIATE 事务里读改写；设备占用.json 是导出视图。"""
    db = _kdb()
    if db is None:
        with open(LOCK, 'a') as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            data = _load_file()
            yield data
            _save(data)
        return
    with db.tx():
        data = _load_db(db)
        before = json.loads(json.dumps(data))
        yield data
        _write_db(db, before, data)
        _save(data)
        db.kv_set('设备占用导出', _file_sig(), by='设备占用')


def recent():
    """序列号 → 最近一次被申请的时间（轮流挑手机用的提示，不是占用）。"""
    try:
        return json.loads(RECENT.read_text('utf-8'))
    except (OSError, ValueError):
        return {}


def touch_recent(sns, t=None):
    """记下这些手机刚被申请；资源池里的 last_used 一起更新（资源池按它轮流分配）。"""
    if not sns:
        return
    r = recent()
    r.update({sn: t or time.time() for sn in sns})
    try:
        tmp = RECENT.with_suffix('.tmp')
        tmp.write_text(json.dumps(r, ensure_ascii=False, indent=1), 'utf-8')
        tmp.replace(RECENT)
    except OSError:
        pass
    db = _kdb()
    if db is not None:
        for sn in sns:
            db.update('resources', {'id': _rid(sn)}, last_used=t or time.time())


def recalled(serial, period='', session=''):
    """这台手机是不是刚被管理员从这一期 / 会话收回（REQ-045）：是就返回收回记录。"""
    db = _kdb()
    if db is None or not (period or session):
        return None
    try:
        from kblib.core import resources
        return resources.recalled(_rid(serial), session=session, period=period)
    except Exception:  # noqa: BLE001
        return None


def session_alive(period):
    """这一期的知识库后台会话是否在运行。"""
    try:
        s = json.loads((ROOT / '期' / period / '期信息.json').read_text('utf-8')).get('开发会话') or {}
        pid = int(s.get('pid') or 0)
    except (OSError, ValueError, TypeError, AttributeError):
        return False
    if s.get('状态') != '运行中' or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def valid(c, now=None):
    """占用是否还有效：没超过闲置时间，且占用它的会话还在（本人标的、终端里申请的除外）。
    资源池按任务发的占用（执行机 / 队列）由资源池自己按超时释放，这里只看闲置。"""
    idle = (now or time.time()) - float(c.get('最后使用') or 0)
    if idle > IDLE.get(c.get('方式'), IDLE['自动']):
        return False
    if c.get('来源') == '资源池':
        return True
    if c.get('方式') == '本人':
        return True
    return session_alive(c.get('期', '')) or (c.get('方式') == '申请' and idle < LOOSE)


def holders():
    """当前有效的占用：序列号 → 记录。"""
    now = time.time()
    return {k: v for k, v in load().items() if valid(v, now)}


def real_adb():
    """真正的 adb（跳过守卫自己所在的目录）。"""
    for p in os.environ.get('PATH', '').split(os.pathsep):
        if not p or Path(p).resolve() == BIN:
            continue
        f = Path(p) / 'adb'
        if f.is_file() and os.access(f, os.X_OK):
            return str(f)
    for h in (os.environ.get('ANDROID_HOME'), os.environ.get('ANDROID_SDK_ROOT'), Path.home() / 'Library/Android/sdk'):
        if h and (Path(h) / 'platform-tools/adb').is_file():
            return str(Path(h) / 'platform-tools/adb')
    return ''


def online(adb):
    """[(序列号, 状态, 型号)]"""
    try:
        r = subprocess.run([adb, 'devices', '-l'], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return []
    out = []
    for line in r.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            model = next((x[6:] for x in parts[2:] if x.startswith('model:')), '')
            out.append((parts[0], parts[1], model.replace('_', ' ')))
    return out
