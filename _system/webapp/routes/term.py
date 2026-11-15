"""接口 · 网页终端（期页面「终端 → ⌨ 我的终端」，用 ttyd，实现见 kblib/infra/term.py；/term/<编号>/ 的转发在 webapp/server.py）。
开 / 关只能本人在网页上操作（page_only）。"""
from webapp.router import get, post  # noqa: F401
from webapp.shared import *  # noqa: F401,F403


def _dirs(name):
    d = kb.period_dir(name)
    m = kb.load_meta(d)
    pe = kb.period_ends(d, m)
    extra = [('开发仓库', m.get('开发仓库'))] if m.get('开发仓库') else []
    return kb.term_dirs(name, {e: pe[e].get('仓库') for e in kb.sort_ends(pe)}, extra)


@get('/api/term/list')
def _get_api_term_list(self, u, q):
    n = q.get('name', '')
    return self._guard(lambda: {'开启': kb.term_enabled(), 'ttyd': bool(kb.ttyd_path()), '安装': kb.TERM_INSTALL,
                                '终端': kb.term_list(n), '目录': _dirs(n)})


@get('/api/term/tail')
def _get_api_term_tail(self, u, q):
    return self._guard(lambda: {'文本': kb.term_tail(q.get('id', ''))})


@post('/api/term/open', page_only=True)
def _post_api_term_open(self, u, n, body):
    return self._guard(lambda: kb.term_open(body.get('period', ''), body.get('cwd', '')))


@post('/api/term/close', page_only=True)
def _post_api_term_close(self, u, n, body):
    return self._guard(lambda: {'msg': kb.term_close(body.get('id', ''))})


@post('/api/term/enable', page_only=True)
def _post_api_term_enable(self, u, n, body):
    return self._guard(lambda: {'msg': kb.term_set_enabled(bool(body.get('on')))})
