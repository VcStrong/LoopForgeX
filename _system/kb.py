#!/usr/bin/env python3
# From 大大王.DT
"""本地知识库入口：命令行（kb …）、MCP 服务（kb.py serve）、web.py 的 import kb。

实现都在 kblib 包里（分层说明见 kblib/__init__.py）。这里只把各模块的名字汇总导出，
web.py、插件、老脚本照旧用 kb.xxx；命令行和 MCP 从 kblib.api.cli.main 启动。
"""
import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kblib import _Lazy  # noqa: E402

_MODULES = [   # 按依赖从下往上：后面的可以用前面的；分包说明见 kblib/__init__.py
    'core.common',
    'core.textx',
    'core.periods',
    'core.notes',
    'core.repos',
    'core.search',
    'core.baseinfo',
    'infra.term',
    'infra.timing',
    'infra.autopush',
    'ai.flows',
    'ai.team',
    'ai.engines',
    'plugins.registry',
    'api.overview',
    'ai.devprep',
    'ai.devsession',
    'ai.devices',
    'ai.branchswap',
    'sources.urlimport',
    'sources.refresh',
    'sources.quickadd',
    'sources.fixlink',
    'bugs.sources',
    'bugs.index',
    'bugs.analysis',
    'bugs.fix',
    'ai.talk',
    'ai.eta',
    'ai.live',
    'anr.store',
    'anr.manual',
    'plugins.builds',
    'plugins.install',
    'plugins.devrun',
    'tasks.memo',
    'plugins.run',
    'stack.plan',
    'stack.dev',
    'stack.launch',
    'stack.guide',
    'steward.onboard',
    'plugins.stats',
    'steward.patrol',
    'steward.intake',
    'steward.chat',
    'steward.brief',
    'steward.site',
    'steward.doctor',
    'infra.deploy',
    'api.mcp',
    'api.cli',
]
_OWNER = {}
_AMBIGUOUS = set()   # 多个模块各有一个同名的下划线函数：kb._xxx 指不清是哪个，合并完删掉，用到时直接 AttributeError
for _name in _MODULES:
    _m = importlib.import_module(f'kblib.{_name}')
    for _k, _v in vars(_m).items():       # 两个模块定义了同名函数，后一个会悄悄覆盖前一个（曾让 kb._allowed 指到 doctor 的同名函数，预览全挂）
        if callable(_v) and not _k.startswith('__') and getattr(_v, '__module__', '') == _m.__name__:
            if _k in _OWNER and _OWNER[_k] != _m.__name__:
                if not _k.startswith('_'):    # 不带下划线的重名：直接报错，别让网页出怪问题
                    raise ImportError(f'{_m.__name__}.{_k} 和 {_OWNER[_k]}.{_k} 重名，kb.{_k} 会被覆盖：请给其中一个改名')
                _AMBIGUOUS.add(_k)
            _OWNER[_k] = _m.__name__
    globals().update({k: v for k, v in vars(_m).items() if not k.startswith('__') and not isinstance(v, _Lazy)})
for _k in _AMBIGUOUS:
    globals().pop(_k, None)
from kblib.infra import devlock, fetch  # noqa: E402,F401
from kblib.anr import parse as anr  # noqa: E402,F401
from kblib.bugs import adapters as bugsrc  # noqa: E402,F401

if __name__ == '__main__':
    from kblib.api.cli import main
    sys.exit(main() or 0)
