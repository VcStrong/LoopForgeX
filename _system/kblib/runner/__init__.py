"""执行机代理 kb runner（REQ-011~REQ-018 REQ-029 REQ-035 REQ-037 REQ-039 REQ-049 REQ-050）。

只做出站 HTTP(S)：注册 → 每 15 秒心跳 → 长轮询领活（50 秒）→ 确认 → 回传日志 / 状态 → 完成。不监听任何端口。
执行机进程用独立的 KB_ROOT=<数据目录>/root（工作副本池、手机占用都在这里），不碰控制面的数据；
所以要在 import kblib.core 之前设好环境变量——launch() 负责（必要时重新 exec 自己）。

  client.py   HTTP 客户端（https + 自签证书 --ca），记延迟 / 失败率
  sysinfo.py  自动标签、自检、CPU / 内存 / 磁盘 / 网络
  home.py     会话私有 HOME（0700）：登录态、git 凭据；结束删除，残留清理
  gitlock.py  同一仓库 fetch / gc / 清理串行
  disk.py     磁盘水位清理（REQ-037）、导出补丁
  agent.py    主循环（心跳线程 + 领活循环 + 每个会话一个线程）
"""
import os
import sys
from pathlib import Path

PROTO = 1                  # 执行机协议版本：和控制面不一致时领活返回 409（REQ-029）
SYS_DIR = Path(__file__).resolve().parents[2]     # kblib/runner/__init__.py → _system


def data_dir(arg=''):
    return Path(arg or os.environ.get('KB_RUNNER_DATA') or Path.home() / '.kb-runner').expanduser().resolve()


def launch(argv):
    """kb runner …：在独立 KB_ROOT 下起执行机（kb.py 已按控制面的 KB_ROOT import 过 kblib，所以重新 exec 一次）。"""
    data = ''
    for i, a in enumerate(argv):
        if a == '--data' and i + 1 < len(argv):
            data = argv[i + 1]
        elif a.startswith('--data='):
            data = a.split('=', 1)[1]
    d = data_dir(data)
    env = dict(os.environ, KB_ROOT=str(d / 'root'), KB_RUNNER_DATA=str(d),
               PYTHONPATH=str(SYS_DIR) + os.pathsep + os.environ.get('PYTHONPATH', ''))
    env.setdefault('KB_SECRET_BACKEND', 'file')
    env.pop('KB_DB_URL', None)          # 执行机绝不连控制面的数据库：只走 HTTP 协议
    (d / 'root').mkdir(parents=True, exist_ok=True)
    os.execve(sys.executable, [sys.executable, '-m', 'kblib.runner.agent', *argv], env)
