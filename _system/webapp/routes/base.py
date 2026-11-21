"""接口 · 基础信息、文档。每个接口一个函数，用 @get / @post 注册（见 webapp/router.py）。"""
from webapp.router import get, post  # noqa: F401
from webapp.shared import *  # noqa: F401,F403


@get('/api/docs')
def _get_api_docs(self, u, q):
    return self._guard(api_docs)


@get('/api/doc')
def _get_api_doc(self, u, q):
    return self._guard(lambda: api_doc(q.get('name', '')))


@get('/api/base')
def _get_api_base(self, u, q):
    return self._guard(api_base)


@get('/api/links')
def _get_api_links(self, u, q):
    return self._guard(lambda: kb.period_links(q.get('name', '')))


@post('/api/base/save', page_only=True)
def _post_api_base_save(self, u, n, body):
    def run():
        msg, bid = kb.base_save_id(body.get('id', ''), body.get('category', ''), body.get('name'), body.get('fields'),
                                   body.get('scope'), body.get('note'), delete=bool(body.get('delete')),
                                   by=kb.actor_label() or '用户（网页）', visibility=body.get('visibility') or None,
                                   holder=body.get('holder') or None, template=body.get('template') if 'template' in body else None)
        return {'msg': msg, 'id': bid}
    return self._guard(run)


@post('/api/base/copy', page_only=True)
def _post_api_base_copy(self, u, n, body):
    """从模板复制成私有基础信息（补充接口，契约外）：{id: 模板编号, holder: 给谁（管理员可填别人，默认自己）, name, values: {字段: 值}}。"""
    def run():
        msg, nid = kb.base_copy_template(body.get('id', ''), body.get('holder') or '', body.get('name') or '', body.get('values') or {},
                                         by=kb.actor_label() or '用户（网页）')
        return {'msg': msg, 'id': nid}
    return self._guard(run)


@post('/api/base/secret', page_only=True)
def _post_api_base_secret(self, u, n, body):
    return self._guard(lambda: {'value': kb.base_secret(body.get('id', ''), body.get('field', ''), by='用户（网页）')})
