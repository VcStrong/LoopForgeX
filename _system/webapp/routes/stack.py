"""接口 · 全栈：各端仓库、出方案、拍板、开工、变更、验收。每个接口一个函数，用 @get / @post 注册（见 webapp/router.py）。"""
from webapp.router import get, post  # noqa: F401
from webapp.shared import *  # noqa: F401,F403
from kblib.core import queue as _q


@get('/api/stack')
def _get_api_stack(self, u, q):
    return self._guard(lambda: {**kb.stack_get(q.get('name', '')), **kb.autopush_view(q.get('name', ''))})


@get('/api/stack/repos')
def _get_api_stack_repos(self, u, q):
    return self._guard(lambda: {'items': kb.stack_repos(), 'ends': list(kb.ENDS)})


@get('/api/stack/branches')
def _get_api_stack_branches(self, u, q):
    return self._guard(lambda: kb.stack_branches(q.get('repo', '')))


@post('/api/stack/end', page_only=True)
def _post_api_stack_end(self, u, n, body):
    return self._guard(lambda: {'msg': kb.stack_set_end(body.get('period', ''), body.get('end', ''), body.get('repo', ''), body.get('branch', ''))})


@post('/api/stack/remote', page_only=True)
def _post_api_stack_remote(self, u, n, body):
    return self._guard(lambda: {'msg': kb.stack_set_remote(body.get('period', ''), body.get('end', ''), body.get('url', ''))})


@post('/api/stack/flow', page_only=True)
def _post_api_stack_flow(self, u, n, body):
    return self._guard(lambda: {'msg': kb.stack_set_flow(body.get('period', ''), body.get('end', ''), body.get('flow', ''))})


@post('/api/stack/plan', page_only=True)
def _post_api_stack_plan(self, u, n, body):
    return self._guard(lambda: {'msg': kb.stack_plan_start(body.get('period', ''), body.get('note', ''))})


@post('/api/stack/change', page_only=True)
def _post_api_stack_change(self, u, n, body):
    return self._guard(lambda: {'msg': kb.stack_change_add(body.get('period', ''), body.get('title', ''), body.get('detail', ''), body.get('ends') or [])})


@post('/api/stack/reject', page_only=True)
def _post_api_stack_reject(self, u, n, body):
    return self._guard(lambda: {'msg': kb.stack_change_reject(body.get('period', ''), body.get('id', ''))})


@post('/api/stack/accept', page_only=True)
def _post_api_stack_accept(self, u, n, body):
    return self._guard(lambda: {'msg': kb.stack_accept(body.get('period', ''))})


@post('/api/stack/dev', page_only=True)
def _post_api_stack_dev(self, u, n, body):
    def run():
        p, end = body.get('period', ''), body.get('end', '')
        r = _q.launch('全栈端', p, lambda: kb.stack_dev_start(p, end), check={'kind': 'stack', 'end': end},
                      remote=(lambda: _q.remote_dev_payload(p, kind='全栈端', end=end)) if end else None)
        return {'msg': _q.launch_msg(r), 'task': r['task']}
    return self._guard(run)


@post('/api/stack/stop', page_only=True)
def _post_api_stack_stop(self, u, n, body):
    return self._guard(lambda: {'msg': kb.stack_dev_stop(body.get('period', ''), body.get('end', ''))})


@post('/api/stack/answer', page_only=True)
def _post_api_stack_answer(self, u, n, body):
    return self._guard(lambda: {'msg': kb.stack_answer(body.get('period', ''), body.get('idx'), body.get('mode', ''), body.get('text', ''))})


@post('/api/stack/approve', page_only=True)
def _post_api_stack_approve(self, u, n, body):
    return self._guard(lambda: {'msg': kb.stack_approve(body.get('period', ''))})


@post('/api/stack/auto-revert', page_only=True)
def _post_api_stack_auto_revert(self, u, n, body):
    return self._guard(lambda: {'msg': kb.stack_auto_revert(body.get('period', ''))})


# ---------------- 新产品：完善需求、建工程、发布（kblib/stack/launch.py）
@post('/api/stack/newproduct', page_only=True)
def _post_api_stack_newproduct(self, u, n, body):
    return self._guard(lambda: {'msg': kb.stack_new_product(body.get('period', ''), body.get('on', True) is not False)})


@post('/api/stack/prd', page_only=True)
def _post_api_stack_prd(self, u, n, body):
    return self._guard(lambda: {'msg': kb.stack_prd_start(body.get('period', ''), body.get('note', ''), bool(body.get('skip')))})


@get('/api/stack/newdir')
def _get_api_stack_newdir(self, u, q):
    return self._guard(lambda: {'dir': kb.default_dir(q.get('name', ''), q.get('end', ''))})


@post('/api/stack/create', page_only=True)
def _post_api_stack_create(self, u, n, body):
    if body.get('retry'):
        return self._guard(lambda: {'msg': kb.stack_scaffold_start(body.get('period', ''), body.get('end', ''))})
    return self._guard(lambda: {'msg': kb.stack_new_project(body.get('period', ''), body.get('end', ''), body.get('parent', ''), body.get('dir', ''))})


@get('/api/stack/dirkind')
def _get_api_stack_dirkind(self, u, q):
    return self._guard(lambda: kb.stack_dir_kind(q.get('path', '')))


@post('/api/stack/gitinit', page_only=True)
def _post_api_stack_gitinit(self, u, n, body):
    return self._guard(lambda: {'msg': kb.stack_git_init(body.get('path', ''))})


@post('/api/stack/release', page_only=True)
def _post_api_stack_release(self, u, n, body):
    return self._guard(lambda: {'msg': kb.stack_release_start(body.get('period', ''))})


@post('/api/stack/release/check', page_only=True)
def _post_api_stack_release_check(self, u, n, body):
    return self._guard(lambda: {'msg': kb.stack_release_check(body.get('period', ''), body.get('id', ''), body.get('done', True) is not False)})
