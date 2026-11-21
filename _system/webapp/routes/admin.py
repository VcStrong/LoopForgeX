"""接口 · 管理员「📊 总览」看板（T002）。契约外补充接口，只给管理员（非管理员 403）。"""
from webapp.router import get  # noqa: F401
from webapp.shared import *  # noqa: F401,F403
from kblib.core import authz as _authz


@get('/api/admin/overview')
def _get_api_admin_overview(self, u, q):
    def run():
        me = (getattr(self, 'ident', None) or {}).get('user', '')
        if not _authz.is_admin(me):
            raise _authz.Denied('只有管理员能看总览')
        from kblib.api.admin_overview import admin_overview
        return admin_overview()
    return self._guard(run)
