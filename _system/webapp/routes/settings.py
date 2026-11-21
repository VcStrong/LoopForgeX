"""接口 · AI 引擎、角色、开发流程。每个接口一个函数，用 @get / @post 注册（见 webapp/router.py）。"""
from webapp.router import get, post  # noqa: F401
from webapp.shared import *  # noqa: F401,F403


@get('/api/settings')
def _get_api_settings(self, u, q):
    return self._guard(lambda: {'flows': kb.flows(), 'fields': kb.FLOW_FIELDS, 'vars': kb.FLOW_VARS,
                                'engines': [dict(e, 状态=kb.engine_status(e), 健康=kb.engine_health_one(e), 接入说明=kb.engine_link_help(e))
                                            for e in kb.engines()],
                                'roles': kb.role_engines(), 'team': kb.team_status(), 'term': kb.term_enabled(), 'autopush': kb.autopush_global(), 'archiveDays': kb.archive_days(),
                                'engineFields': kb.ENGINE_FIELDS, 'engineVars': kb.ENGINE_VARS,
                                'plugins': kb.plugins(), 'pluginEvents': kb.PLUGIN_EVENTS, 'timings': kb.timings(), 'policies': kb.POLICY_TEXT,
                                'pluginEnv': kb.PLUGIN_ENV, 'pluginDir': str(kb.PLUGIN_DIR),
                                'baseEntries': [{'编号': e['编号'], '名称': e['名称'], '类型': e.get('类型', '')}
                                                for e in kb.base_visible()]})


@get('/api/settings/commands')
def _get_api_settings_commands(self, u, q):
    return self._guard(lambda: {'items': kb.list_commands()})


@post('/api/settings/flow', page_only=True)
def _post_api_settings_flow(self, u, n, body):
    return self._guard(lambda: dict(zip(('msg', 'id'), kb.save_flow(body.get('flow') or {}))))


@get('/api/engines/health')
def _get_api_engines_health(self, u, q):
    return self._guard(lambda: kb.engine_health())


@post('/api/engine/probe', page_only=True)
def _post_api_engine_probe(self, u, n, body):
    return self._guard(lambda: kb.engine_probe(body.get('id', '') or '', src='手动检测'))


@post('/api/engine/role', page_only=True)
def _post_api_engine_role(self, u, n, body):
    return self._guard(lambda: {'msg': kb.set_role_engine(body.get('role', ''), body.get('engine', ''))})


@post('/api/engine', page_only=True)
def _post_api_engine(self, u, n, body):
    return self._guard(lambda: dict(zip(('msg', 'id'), kb.save_engine(body.get('engine') or {}))))


@post('/api/engine/delete', page_only=True)
def _post_api_engine_delete(self, u, n, body):
    return self._guard(lambda: {'msg': kb.delete_engine(body.get('id', ''))})


@post('/api/engine/default', page_only=True)
def _post_api_engine_default(self, u, n, body):
    return self._guard(lambda: {'msg': kb.set_default_engine(body.get('id', ''))})


@post('/api/engine/link', page_only=True)
def _post_api_engine_link(self, u, n, body):
    return self._guard(lambda: {'msg': kb.engine_link(body.get('id', ''))})


@post('/api/engine/test', page_only=True)
def _post_api_engine_test(self, u, n, body):
    return self._guard(lambda: kb.engine_test(body.get('id', '')))


@post('/api/team', page_only=True)
def _post_api_team(self, u, n, body):
    """角色团全局设置：只传要改的字段 on / tier（旧版的每期上限 limit 已去掉，传了也忽略）。"""
    return self._guard(lambda: {'msg': kb.set_team(body.get('on'), body.get('tier'))})


@post('/api/team/suite', page_only=True)
def _post_api_team_suite(self, u, n, body):
    """角色套件安装 / 移除 / 恢复：action=install（home=套件目录，可空；engine=非 Claude 引擎 id 时只装这个引擎的适配）/ remove（projects=false 不动项目级安装；engine=引擎 id 时只移除它的适配）/ restore（backup=备份名，空 = 最近一次）。"""
    def run():
        act = body.get('action')
        if act == 'install':
            return kb.team_install(body.get('home') or '', body.get('engine') or '')
        if act == 'remove':
            return kb.team_remove(projects=body.get('projects', True) is not False, engine=body.get('engine') or '')
        if act == 'restore':
            return kb.team_restore(body.get('backup') or '')
        raise kb.KBError('action 只能是 install / remove / restore')
    return self._guard(lambda: {'msg': run()})


@post('/api/period/team', page_only=True)
def _post_api_period_team(self, u, n, body):
    """期级角色团覆盖（REQ-003）：mode=跟随 / 关。"""
    return self._guard(lambda: {'msg': kb.set_period_team(body.get('period', ''), body.get('mode') or '跟随')})


@post('/api/period/engine', page_only=True)
def _post_api_period_engine(self, u, n, body):
    return self._guard(lambda: {'msg': kb.set_period_engine(body.get('period', ''), body.get('id', ''))})


@post('/api/settings/flow/draft', page_only=True)
def _post_api_settings_flow_draft(self, u, n, body):
    return self._guard(lambda: {'flow': kb.flow_draft(body.get('path', ''), bool(body.get('ai')))})


@post('/api/settings/flow/delete', page_only=True)
def _post_api_settings_flow_delete(self, u, n, body):
    return self._guard(lambda: {'msg': kb.delete_flow(body.get('id', ''))})


@post('/api/settings/default', page_only=True)
def _post_api_settings_default(self, u, n, body):
    return self._guard(lambda: {'msg': kb.set_default_flow(body.get('id', ''))})


@post('/api/period/flow', page_only=True)
def _post_api_period_flow(self, u, n, body):
    return self._guard(lambda: {'msg': kb.set_period_flow(body.get('period', ''), body.get('id', ''))})


@get('/api/repos/settings')
def _get_api_repos_settings(self, u, q):
    return self._guard(lambda: {'items': kb.repo_settings(), 'ends': list(kb.REPO_ENDS), 'flows': [{'id': f['id'], '名称': f['名称']} for f in kb.flows()]})


@post('/api/repos/settings', page_only=True)
def _post_api_repos_settings(self, u, n, body):
    return self._guard(lambda: {'msg': kb.repo_set(body.get('path', ''), body.get('end'), body.get('flow'), code=body.get('code'),
                                                    run=body.get('run'))})


@get('/api/timings')
def _get_api_timings(self, u, q):
    return self._guard(lambda: {'items': kb.timings(), 'policies': kb.POLICY_TEXT})


@post('/api/timing', page_only=True)
def _post_api_timing(self, u, n, body):
    return self._guard(lambda: {'msg': kb.reset_timings() if body.get('reset') else kb.save_timing(body.get('id', ''), body.get('policy', ''))})


# ---- 工作副本池：按克隆地址登记仓库，开工时分配空闲副本（kblib/infra/pool.py）
from kblib.infra import pool as _wcpool  # noqa: E402


@get('/api/pool')
def _get_api_pool(self, u, q):
    def run():
        from kblib.core import queue as _q
        st = _wcpool.pool_status()
        mine = [dict(c, 仓库=r['名称'], 占用会话=(c.get('占用') or None) and str(c.get('占用'))) for r in st['仓库'] for c in r['副本']]
        st['items'] = [{'执行机': '本机', 'id': _q.LOCAL_ID, '副本': mine}] + _q.runner_groups('副本')   # 按执行机分组（REQ-015）
        return st
    return self._guard(run)


@post('/api/pool/settings', page_only=True)
def _post_api_pool_settings(self, u, n, body):
    return self._guard(lambda: {'msg': _wcpool.pool_save_settings(body.get('limit'), body.get('idle'), body.get('mode'))})


@post('/api/pool/add', page_only=True)
def _post_api_pool_add(self, u, n, body):
    return self._guard(lambda: {'msg': kb.add_repo(body.get('url', ''), body.get('name') or None, body.get('post', ''), background=True)})


@post('/api/pool/remove', page_only=True)
def _post_api_pool_remove(self, u, n, body):
    return self._guard(lambda: {'msg': kb.remove_repo(body.get('name', ''))})


@post('/api/pool/cleanup', page_only=True)
def _post_api_pool_cleanup(self, u, n, body):
    return self._guard(lambda: {'msg': _wcpool.pool_cleanup()['说明']})


# ---------------------------------------------------------------- 网页端口（启动后在平台里改并重启；补充接口，契约外，只给本端网页用）
@get('/api/web/port')
def _get_api_web_port(self, u, q):
    from kblib.infra import webport
    return self._guard(lambda: webport.status(webport.RUNNING['port'] or None))


@post('/api/web/port', page_only=True)
def _post_api_web_port(self, u, n, body):
    import sys as _sys
    from kblib.core import authz as _az, db as _kdb, queue as _q
    from kblib.infra import webport

    def run():
        me = (getattr(self, 'ident', None) or {}).get('user', '')
        if not _az.is_admin(me):
            raise _az.Denied('只有管理员能改网页端口')
        cur = webport.RUNNING['port']
        st = webport.status(cur)
        if not st['可在线修改']:
            raise kb.KBError(st['不能改的原因'])
        p = webport.check_new(body.get('port'), cur)
        webport.save(p, by=me)
        online = [r['id'] for r in _q.remote_online()]
        webport.begin_move(p, cur, online)
        webport.restart_after_notify(webport.strip_port_args([_sys.executable, *_sys.argv]), wait=20 if online else 0)
        _kdb.audit('设置', me, '网页端口', '', '成功', f'{cur} → {p}')
        host = (self.headers.get('Host') or f'127.0.0.1:{cur}').rsplit(':', 1)[0] if not (self.headers.get('Host') or '').startswith('[') \
            else self.headers.get('Host').rsplit(':', 1)[0]
        url = f'{"https" if self._https() else "http"}://{host}:{p}/'
        tips = [f'{len(online)} 台在线执行机会在下一次心跳时自动改连 {p}（经反向代理接入的不受影响）'] if online else []
        tips.append('kb token 接入的远程 Claude Code 要把地址里的端口改成 ' + str(p) + '；本机 Claude Code 走 stdio 不受影响')
        return {'msg': f'网页端口改成 {p}，正在重启' + ('（先等执行机拿到新端口，最多 20 秒）' if online else ''), 'port': p, 'url': url,
                '提示': tips}
    return self._guard(run)
