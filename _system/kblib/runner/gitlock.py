"""同一仓库（同一基础克隆）的 fetch / gc / 清理串行（REQ-015 REQ-037）：按仓库名一把 fcntl 文件锁。

跨进程有效（同机多个执行机进程、会话里的 git 也可以用 KB_GITLOCK 文件 flock）；同进程多线程也串行。
"""
import fcntl
import os
import re
import threading
from contextlib import contextmanager
from pathlib import Path

_TLOCKS = {}
_G = threading.Lock()


def lock_file(name, root=None):
    base = Path(root or os.environ.get('KB_ROOT') or '.') / '.gitlock'
    base.mkdir(parents=True, exist_ok=True)
    return base / (re.sub(r'[^\w.\-]', '_', str(name)) + '.lock')


@contextmanager
def repo_lock(name, root=None):
    f = lock_file(name, root)
    with _G:
        tl = _TLOCKS.setdefault(str(f), threading.Lock())
    with tl, open(f, 'a') as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield f
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
