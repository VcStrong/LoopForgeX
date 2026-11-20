"""接口 · 开发会话、手机、复测、问题、免打扰、回答 AI 的提问。每个接口一个函数，用 @get / @post 注册（见 webapp/router.py）。"""
from webapp.router import get, post  # noqa: F401
from webapp.shared import *  # noqa: F401,F403


@get('/api/dev/activity')
def _get_api_dev_activity(self, u, q):
    return self._guard(lambda: kb.dev_activity_all(q.get('name', '')))


@get('/api/devices')
def _get_api_devices(self, u, q):
    return self._guard(lambda: {'items': kb.devices() if kb._adb() else None})


@get('/api/dev')
def _get_api_dev(self, u, q):
    return self._guard(lambda: {**kb.dev_overview(q.get('name', '')), **kb.stack_brief(q.get('name', '')), **kb.autopush_view(q.get('name', '')),
                                '后台': kb.period_bg(q.get('name', ''))})


@get('/api/period/bg')
def _get_api_period_bg(self, u, q):
    """这一期在跑的所有后台会话 / 插件任务（开发面板「会话」栏、删期确认框用）。"""
    return self._guard(lambda: {'items': kb.period_bg(q.get('name', ''))})


@post('/api/period/bg/stop', page_only=True)
def _post_api_period_bg_stop(self, u, n, body):
    return self._guard(lambda: {'msg': kb.period_bg_stop(body.get('period', ''), body.get('key', ''))})


@post('/api/dev/start', page_only=True)
def _post_api_dev_start(self, u, n, body):
    def run():
        if body.get('confirm', '').strip() != kb.period_dir(body.get('period', '')).name:
            raise kb.KBError('输入的期名不一致，已取消')
        return {'msg': kb.dev_start(body.get('period', ''), body.get('note', ''), body.get('repo', ''),
                                    body.get('branch', ''), bool(body.get('keepBranch')))}
    return self._guard(run)


@post('/api/dev/chat', page_only=True)
def _post_api_dev_chat(self, u, n, body):
    return self._guard(lambda: {'msg': kb.dev_chat(body.get('period', ''), body.get('text', ''), bool(body.get('interrupt')))})


@post('/api/retest', page_only=True)
def _post_api_retest(self, u, n, body):
    return self._guard(lambda: {'msg': kb.retest_start(body.get('period', ''), body.get('scope', ''), body.get('note', ''))})


@post('/api/retest/plan', page_only=True)
def _post_api_retest_plan(self, u, n, body):
    return self._guard(lambda: {'msg': kb.retest_plan(body.get('period', ''), bool(body.get('auto')), body.get('phones') or 1,
                                                      body.get('scope', ''))})


@post('/api/issue', page_only=True)
def _post_api_issue(self, u, n, body):
    return self._guard(lambda: {'msg': kb.set_issue(body.get('period', ''), body.get('id', ''), body.get('status', ''))})


@post('/api/device', page_only=True)
def _post_api_device(self, u, n, body):
    return self._guard(lambda: {'msg': kb.device_mark(body.get('serial', ''), body.get('action', ''))})


@post('/api/dev/stop', page_only=True)
def _post_api_dev_stop(self, u, n, body):
    return self._guard(lambda: {'msg': kb.dev_stop(body.get('period', ''))})


@post('/api/dnd', page_only=True)
def _post_api_dnd(self, u, n, body):
    return self._guard(lambda: {'msg': kb.set_dnd(body.get('period', ''), bool(body.get('on')))})


@post('/api/autopush', page_only=True)
def _post_api_autopush(self, u, n, body):
    """自动推送开关：带 period = 这一期；不带 = 「⚙ 设置 → 常用」总开关。"""
    on = bool(body.get('on'))
    return self._guard(lambda: {'msg': kb.autopush_set(body['period'], on) if body.get('period') else kb.autopush_set_global(on)})


@post('/api/answer', page_only=True)
def _post_api_answer(self, u, n, body):
    return self._guard(lambda: {'msg': kb.answer(body.get('period', ''), body.get('id', ''), body.get('answer', ''))})


@get('/api/period/branch-swap')
def _get_api_period_branch_swap(self, u, q):
    return self._guard(lambda: kb.swap_info(q.get('name', '')))


@post('/api/period/branch-swap', page_only=True)
def _post_api_period_branch_swap(self, u, n, body):
    return self._guard(lambda: {'msg': kb.branch_swap(body.get('period', ''), body.get('branch', ''), body.get('base', ''),
                                                      body.get('reason', ''), body.get('end', ''), carry=bool(body.get('carry')))})


# ---------------- ▶ 运行到手机（kblib/plugins/devrun.py）
@get('/api/devrun/options')
def _get_api_devrun_options(self, u, q):
    return self._guard(lambda: kb.devrun_options(q.get('name', ''), q.get('repo', '')))


@get('/api/devrun/job')
def _get_api_devrun_job(self, u, q):
    return self._guard(lambda: kb.devrun_job(q.get('id', '')))


@post('/api/devrun', page_only=True)
def _post_api_devrun(self, u, n, body):
    return self._guard(lambda: {'job': kb.devrun_start(body.get('period', ''), body.get('repo', ''), body.get('serial', ''),
                                                       body.get('module', ''), body.get('variant', ''), bool(body.get('remember')),
                                                       build=body.get('build', ''))})


@post('/api/devrun/cancel', page_only=True)
def _post_api_devrun_cancel(self, u, n, body):
    return self._guard(lambda: {'msg': kb.devrun_cancel(body.get('id', ''))})
