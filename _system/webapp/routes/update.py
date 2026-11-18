"""接口 · 程序更新：看有没有新版本、检查、一键更新并重启、更新设置。更新和改设置只能在知识库网页上由本人点。"""
from webapp.router import get, post  # noqa: F401
from webapp.shared import *  # noqa: F401,F403
from kblib.steward import update as _upd


@get('/api/update')
def _get_api_update(self, u, q):
    return self._guard(_upd.update_status)


@post('/api/update/check', page_only=True)
def _post_api_update_check(self, u, n, body):
    return self._guard(lambda: _upd.update_check(True, by='用户（网页）'))


@post('/api/update/apply', page_only=True)
def _post_api_update_apply(self, u, n, body):
    return self._guard(lambda: {'msg': _upd.update_apply(by='用户（网页）')})


@post('/api/update/settings', page_only=True)
def _post_api_update_settings(self, u, n, body):
    return self._guard(lambda: {'msg': _upd.update_set(body.get('mode'), body.get('push'))})
