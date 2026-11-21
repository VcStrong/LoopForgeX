"""设备提供方注册表（REQ-043）：按文件自动发现 providers/*.py 里 Provider 的子类；配置在 providers 表（启用、配置、模拟行为）。

统一流程 session_run()：申请（资源池占用）→ 安装 → 执行 → 截图 / 日志 → 释放；任一步失败返回统一错误并自动释放已占资源。
"""
import importlib
import inspect
import pkgutil
from pathlib import Path

from kblib.core import db as _db
from kblib.core.common import KBError
from kblib.devices.providers.base import Provider, ProviderError  # noqa: F401

DEFAULTS = {'local': ('local', '本机手机（adb）', True), 'runner': ('runner', '执行机上的手机', True),
            'mock': ('remote', '模拟远程平台', False)}


def classes():
    """KIND → Provider 子类（local / runner / remote）。"""
    out = {}
    for m in pkgutil.iter_modules([str(Path(__file__).parent)]):
        if m.name == 'base':
            continue
        mod = importlib.import_module(f'{__name__}.{m.name}')
        for _, c in inspect.getmembers(mod, inspect.isclass):
            if issubclass(c, Provider) and c is not Provider and c.KIND:
                out.setdefault(c.KIND, c)
    return out


def _ensure():
    for pid, (kind, name, en) in DEFAULTS.items():
        if not _db.get('providers', id=pid):
            _db.insert('providers', id=pid, kind=kind, name=name, enabled=1 if en else 0, config={}, behavior='正常')


def get(pid):
    _ensure()
    row = _db.get('providers', id=pid)
    if not row:
        raise KBError(f'没有这个设备提供方：{pid}')
    c = classes().get(row['kind'])
    if not c:
        raise KBError(f'提供方类型 {row["kind"]} 没有实现')
    return c(pid, row.get('config') or {}, row.get('behavior') or '正常'), row


def listing():
    _ensure()
    out = []
    for row in _db.rows('providers', order='id'):
        try:
            p, _ = get(row['id'])
            st = ('正常' if p.status() == '正常' else '不可用') if row['enabled'] else '未启用'
            n = len(p.list()) if row['enabled'] else 0
            mock = p.MOCK
        except Exception:  # noqa: BLE001
            st, n, mock = '不可用', 0, False
        out.append({'id': row['id'], '类型': row['kind'], '名称': row['name'], '启用': bool(row['enabled']), '状态': st, '模拟': mock,
                    '设备数': n, '模拟行为': row.get('behavior') or '正常', '配置': _mask(row.get('config') or {})})
    return out


def _mask(cfg):
    return {k: ('●●●●●●' if '令牌' in k and v and not str(k).endswith('编号') else v) for k, v in cfg.items()}


def configure(pid, enabled=None, config=None, behavior=None, by=''):
    _ensure()
    row = _db.get('providers', id=pid)
    if not row:
        raise KBError(f'没有这个设备提供方：{pid}')
    if behavior and behavior not in ('正常', '故意失败', '故意慢'):
        raise KBError('模拟行为只能是 正常 / 故意失败 / 故意慢')
    f = {}
    if enabled is not None:
        f['enabled'] = 1 if enabled else 0
    if config is not None:
        if not isinstance(config, dict):
            raise KBError('配置要是对象')
        f['config'] = config
    if behavior:
        f['behavior'] = behavior
    if f:
        _db.update('providers', {'id': pid}, **f)
    sync(pid)
    _db.audit('设备提供方', by, pid, '', '成功', str({k: v for k, v in f.items() if k != 'config'}))
    return f'已保存设备提供方「{row["name"]}」'


def sync(pid):
    """把远程 / 模拟平台的设备登记进资源池（本机和执行机的手机由 adb / 心跳登记）。"""
    from kblib.core import resources
    p, row = get(pid)
    if row['kind'] != 'remote':
        return 0
    phones = [dict(d, 提供方=pid) for d in (p.list() if row['enabled'] else [])]
    return resources.sync_runner_phones(pid, phones)


def session_run(pid, cond, n, session, period='', user='', project='', artifact='', cmds=(), capture=True):
    """走一遍 申请 → 安装 → 执行 → 截图 / 日志 → 释放。失败返回统一错误，已占资源自动释放。"""
    from kblib.core import resources
    p, row = get(pid)
    if not row['enabled']:
        return {'ok': False, 'error': '提供方不可用：未启用'}
    sync(pid)
    r = resources.claim('手机', dict(cond or {}, 提供方=p.KIND if pid in ('local', 'runner') else pid), n, session, period, user, project,
                        mode='自动', wait=False)
    if not r['ok']:
        return {'ok': False, 'error': f'无空闲：{r["error"]}' if not r['error'].startswith('无空闲') else r['error'], '模拟': p.MOCK}
    held = [h['资源'] for h in r['holds']]
    devs = [{'序列号': rid.rsplit(':', 1)[-1]} for rid in held]
    steps = []
    out = {'ok': True, '模拟': p.MOCK, '设备': [d['序列号'] for d in devs], '步骤': steps}
    try:
        p.apply(cond, n, session)
        for d in devs:
            if artifact:
                steps.append({'步骤': '安装', '设备': d['序列号'], **p.install(d, artifact)})
            for c in cmds:
                steps.append({'步骤': '执行', '设备': d['序列号'], '命令': c, **p.run(d, c)})
            if capture:
                cap = p.capture(d)
                steps.append({'步骤': '截图日志', '设备': d['序列号'], '截图字节': len(cap.get('截图') or b''), '日志': (cap.get('日志') or '')[-2000:]})
            p.release(d)
    except ProviderError as e:
        out.update(ok=False, error=str(e), 错误类型=e.kind)
    except NotImplementedError:
        out.update(ok=False, error='提供方不可用：这一步没有实现', 错误类型='提供方不可用')
    finally:
        for rid in held:
            resources.release(rid, session=session, by='提供方流程')
    return out
