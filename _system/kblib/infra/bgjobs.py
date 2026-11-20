"""期里的后台任务登记表：不是开发会话、但在后台替这一期干活的东西（插件「自动」生成 QA 用例、带期名调用的工具插件……）。

为什么要有：插件在后台线程 / 子进程里跑，以前只在「记录」里留一两行，开发面板上看不到、停不了，
用户以为没在跑就删了期，插件跑完回写资料时又把期目录建了回来。

登记在 _system/后台任务.json（网页进程和 MCP 进程都可能起任务，所以落文件、加文件锁）：
  {编号: {期, 发起, 名称, 内容, 开始时间, 进度, 步骤: [[时间, 文字]], 状态, 进程, 子进程: [pid], 停止, 结束时间, 结果}}
  状态：运行中 / 已结束 / 失败 / 已停止。进程 = 跑这个任务的知识库进程；它和登记的子进程都没了、任务还挂着「运行中」就补记为已结束
  （进程没了但插件子进程还在跑：照样算在跑，面板上能看到、能停）。
停止：标「停止」并结束登记的子进程组（插件命令用 start_new_session 起，killpg 连它派生的都结束）；
  跑任务的线程在轮次之间看 job_cancelled() 自己收尾。
"""
import contextvars
import fcntl
import json
import os
import signal
import time
import uuid
from contextlib import contextmanager

from kblib.core.common import SYS, now

FILE = SYS / '后台任务.json'
LOCK = SYS / '后台任务.lock'
KEEP = 60                     # 已结束的最多留这么多条（给「AI 动作」看最近做过什么）
_CUR = contextvars.ContextVar('kb_bgjob', default='')


@contextmanager
def _locked():
    SYS.mkdir(parents=True, exist_ok=True)
    with open(LOCK, 'a') as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            try:
                data = json.loads(FILE.read_text('utf-8')) if FILE.exists() else {}
            except ValueError:
                data = {}
            yield data
            done = sorted((k for k, v in data.items() if v.get('状态') != '运行中'), key=lambda k: data[k].get('结束时间') or '')
            for k in done[:-KEEP] if len(done) > KEEP else []:
                data.pop(k, None)
            tmp = FILE.with_suffix('.tmp')
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), 'utf-8')
            tmp.replace(FILE)
        finally:
            fcntl.flock(lk, fcntl.LOCK_UN)


def _alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def job_start(period, who, name, content=''):
    """登记一个后台任务，返回编号；同时设成当前线程的「当前任务」（插件子进程自动挂上来）。"""
    jid = time.strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:4]
    with _locked() as data:
        data[jid] = {'期': period, '发起': who or '', '名称': name, '内容': content, '开始时间': now(), '进度': '启动中',
                     '步骤': [[now(), '开始：' + (content or name)]], '状态': '运行中', '进程': os.getpid(), '子进程': []}
    _CUR.set(jid)
    return jid


def job_current():
    return _CUR.get()


def job_progress(jid, text):
    if not jid:
        return
    with _locked() as data:
        x = data.get(jid)
        if x:
            x['进度'] = text
            x['步骤'] = (x.get('步骤') or [])[-40:] + [[now(), text]]


def job_proc(pid, jid=None, add=True):
    """把子进程挂到任务上（停止时一起结束）；add=False 是子进程已经退出、摘掉。"""
    jid = jid or _CUR.get()
    if not jid:
        return
    with _locked() as data:
        x = data.get(jid)
        if x:
            ps = [p for p in x.get('子进程') or [] if p != pid]
            x['子进程'] = ps + [pid] if add else ps


def job_end(jid, status='已结束', result=''):
    if not jid:
        return
    with _locked() as data:
        x = data.get(jid)
        if x and x.get('状态') == '运行中':
            if x.get('停止'):
                status = '已停止'
            x.update(状态=status, 结束时间=now(), 结果=(result or '')[-1500:], 子进程=[])
            x['步骤'] = (x.get('步骤') or [])[-40:] + [[now(), f'{status}' + (f'：{result[-200:]}' if result else '')]]
    if _CUR.get() == jid:
        _CUR.set('')


def job_cancelled(jid=None):
    jid = jid or _CUR.get()
    if not jid:
        return False
    try:
        x = json.loads(FILE.read_text('utf-8')).get(jid) or {}
    except (OSError, ValueError):
        return False
    return bool(x.get('停止'))


def _killpg(pid, sig):
    try:
        os.killpg(int(pid), sig)
    except (OSError, ValueError, TypeError):
        try:
            os.kill(int(pid), sig)
        except (OSError, ValueError, TypeError):
            pass


def job_stop(jid, by='用户（网页）'):
    """停止：标记停止 + 结束子进程组。返回任务（没有 / 已结束返回 None）。"""
    with _locked() as data:
        x = data.get(jid)
        if not x or x.get('状态') != '运行中':
            return None
        x['停止'] = by
        procs = list(x.get('子进程') or [])
        x['步骤'] = (x.get('步骤') or [])[-40:] + [[now(), f'停止（{by}）']]
        if not _alive(x.get('进程')):       # 跑它的进程没了：子进程下面结束掉，直接记已停止；否则线程下一轮检查时自己收尾
            x.update(状态='已停止', 结束时间=now(), 子进程=[])
        out = dict(x)
    for p in procs:
        _killpg(p, signal.SIGTERM)
    if procs:
        time.sleep(1)
        for p in procs:
            if _alive(p):
                _killpg(p, signal.SIGKILL)
    return out


def jobs_of(period, running=None):
    """这一期的后台任务（新的在前）。running=True 只要在跑的。跑任务的进程没了的补记为已结束。"""
    with _locked() as data:
        for x in data.values():
            if x.get('状态') == '运行中' and not _alive(x.get('进程')) and not any(_alive(c) for c in x.get('子进程') or []):
                x.update(状态='已结束', 结束时间=now(), 结果=(x.get('结果') or '') + '（跑它的知识库进程已退出）', 子进程=[])
        xs = [dict(x, 编号=k) for k, x in data.items() if x.get('期') == period]
    if running is not None:
        xs = [x for x in xs if (x['状态'] == '运行中') == running]
    return sorted(xs, key=lambda x: x.get('开始时间') or '', reverse=True)


def jobs_stop_period(period, by='用户（网页）'):
    """删期 / 停掉这一期所有后台任务：返回停掉的任务名。"""
    out = []
    for x in jobs_of(period, running=True):
        if job_stop(x['编号'], by):
            out.append(x['名称'])
    return out
