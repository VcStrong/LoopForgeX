"""接口权限表（REQ-002）：POST 接口 → 要的动作（authz.ACTIONS）。没列的写接口默认「加资料」（只读账号不能调）。

期从请求体的 period / name 取；按期负责人 / 项目负责人判「拍板 / 开工 / 验收 / svn提交」；
删除期 / 事项只有本人（负责人）和管理员（用户 2026-11-20 22:03）。
"""
ADMIN = {   # 团队级设置：只有管理员
    '/api/settings/flow', '/api/settings/flow/draft', '/api/settings/flow/delete', '/api/settings/default',
    '/api/engine', '/api/engine/delete', '/api/engine/default', '/api/engine/link', '/api/engine/role',
    '/api/team', '/api/team/suite', '/api/repos/settings', '/api/timing', '/api/pool/settings', '/api/pool/add',
    '/api/pool/remove', '/api/pool/cleanup', '/api/plugin', '/api/plugin/dir', '/api/plugin/remove', '/api/zentao/settings', '/api/bug/source',
    '/api/steward/settings', '/api/ui/template', '/api/update', '/api/update/check', '/api/update/apply', '/api/update/settings',
    '/api/users', '/api/user', '/api/user/disable', '/api/user/enable', '/api/project', '/api/quota', '/api/runners/token',
    '/api/runners/update', '/api/alerts/ack', '/api/device-provider', '/api/schedules',
}
OWNER = {
    '/api/dev/start': '开工', '/api/stack/dev': '开工', '/api/stack/approve': '拍板', '/api/stack/accept': '验收',
    '/api/stack/release': '验收', '/api/autopush': '验收', '/api/zentao/svncommit': 'svn提交', '/api/period/delete': '删除',
    '/api/stack/auto-revert': '拍板', '/api/stack/reject': '拍板', '/api/period/owner': '项目管理',
}
PUSH = {   # 推动类：成员能做
    '/api/stack/plan', '/api/zentao/analyze', '/api/zentao/fix', '/api/devrun', '/api/build/install', '/api/plugin/run',
    '/api/plugin/run2', '/api/task/push', '/api/retest', '/api/retest/plan', '/api/period/branch-swap',
}
SELF = {   # 只和登录人自己有关：谁都能调（含只读）
    '/api/me/password', '/api/me/project', '/api/me/token', '/api/me/token/delete', '/api/me/profile', '/api/me/test',
    '/api/logout', '/api/onboard/hide', '/api/chat/clear',
}
READ_POST = {'/api/search', '/api/text', '/api/read'}


def action_for(path, body):
    if path in SELF or path in READ_POST:
        return None
    if path in ADMIN:
        return '管理'
    if path in OWNER:
        return OWNER[path]
    if path == '/api/status' and (body or {}).get('status') in ('已批准开发',):
        return '开工'
    if path in PUSH:
        return '推动'
    return '加资料'


def period_of(body):
    b = body or {}
    for k in ('period', 'name'):
        v = b.get(k)
        if isinstance(v, str) and v:
            return v
    return ''

# 待改密码的账号只能调这些（REQ-001）
MUST_CHANGE_OK = {'/api/me', '/api/me/password', '/api/logout', '/login', '/', '/api/events'}
