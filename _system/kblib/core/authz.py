"""权限对照表（流程架构 §8，REQ-002）：can(用户上下文, 动作, 期/项目)。

主体：网页登录人（看 系统角色 + 在项目里的角色 + 是否期负责人）、AI 会话（令牌绑 发起人 + 项目 + 期）、管家。
动作：
  看            看期、资料、记录                    ┐ 期 / 事项的资料全平台可看可改（用户 2026-11-20 22:03，T003）：
  加资料        加资料、补充需求、回答 AI 问题      │ 不按项目拦，每次修改记操作人（REQ-004）；项目只做分组和默认视图。
  推动          推动事项、分析 Bug、运行到手机      ┘ 只读账号仍不能写。
  删除          删除期 / 事项：只有本人（负责人 = 创建人）和管理员
  拍板          拍板方案（拿不准的）
  开工 / 验收 / svn提交
  项目机密      读项目机密
  管理          用户、执行机、团队设置
  项目管理      改项目成员、项目设置（项目负责人 / 管理员）
"""
from kblib.core.common import KBError
from kblib.core import db as _db

ACTIONS = ['看', '加资料', '推动', '拍板', '开工', '验收', 'svn提交', '删除', '项目机密', '管理', '项目管理']
PERIOD_ACTIONS = ['看', '加资料', '推动', '拍板', '开工', '验收', 'svn提交', '删除']
OWNER_ONLY = ('拍板', '开工', '验收', 'svn提交')
WRITE = ('加资料', '推动', '拍板', '开工', '验收', 'svn提交', '删除', '项目管理', '管理')
OPEN = ('看', '加资料', '推动')        # 期 / 事项资料：全平台开放，不看项目
MSG_DEL = '只有本人或管理员能删除'
MSG_OWNER = '只有本期负责人或项目负责人能操作'
MSG_RO = '只读账号不能修改'
MSG_PROJ = '没有这个项目的权限'


class Denied(KBError):
    """403：没有权限。"""


def _user(name):
    return _db.user_get(name) if name else None


def is_admin(user):
    u = _user(user) if isinstance(user, str) else user
    return bool(u) and u.get('sys_role') == '管理员' and u.get('status') != '停用'


def project_role(user, project):
    """在项目里的角色：管理员视同负责人；不在项目里返回空。"""
    if not project:
        return '成员' if user else ''
    if is_admin(user):
        return '负责人'
    return _db.member_roles(user).get(project, '')


def visible_projects(user):
    if is_admin(user):
        return [p['id'] for p in _db.project_list()]
    return list(_db.member_roles(user))


def can(ctx, action, period=None, project=None):
    """返回 (是否允许, 不允许的原因)。ctx 是 common.current_ctx() 那样的字典。"""
    ctx = ctx or {}
    via, user = ctx.get('via', ''), ctx.get('user', '')
    owner = ''
    if period:
        r = _db.period_row(period)
        if r:
            project = project or r['project']
            owner = r['owner']
    if via == '管家':
        if action in OWNER_ONLY or action in ('删除', '管理', '项目管理'):
            return False, '管家不能做这个动作'
        return True, ''
    if via == 'AI':
        if action not in OPEN and project:            # 期资料全平台开放；项目机密等别的动作仍限项目
            if ctx.get('project') and ctx['project'] != project:
                return False, MSG_PROJ
            if not ctx.get('project') and user and not project_role(user, project):
                return False, MSG_PROJ      # 个人令牌：只能碰持有者参与的项目
        if project and user and project_role(user, project) == '只读' and action in WRITE:
            return False, MSG_RO
        if action in OWNER_ONLY or action in ('删除', '管理', '项目管理'):
            return False, '只能在知识库网页上由本人操作'
        if action in ('推动',) and ctx.get('period') and period and ctx['period'] != period:
            return False, '会话只能操作本期'
        if action == '加资料' and ctx.get('period') and period and ctx['period'] != period:
            return False, '会话只能写本期'
        return True, ''
    u = _user(user)
    if not u:
        return False, '要先登录'
    if u.get('status') == '停用':
        return False, '账号已停用，联系管理员'
    if u.get('sys_role') == '管理员':
        return True, ''
    if action == '管理':
        return False, '只有管理员能操作'
    role = project_role(user, project) if project else '成员'
    if role == '只读' and action in WRITE:
        return False, MSG_RO
    if action in OPEN:                  # 不是这个项目的成员也能看、能改（记操作人）
        return True, ''
    if action == '删除':
        return (bool(owner) and owner == user), ('' if owner and owner == user else MSG_DEL)
    if project and not role and action not in OWNER_ONLY:
        return False, MSG_PROJ
    if action == '项目管理':
        return (role == '负责人'), ('' if role == '负责人' else '只有项目负责人或管理员能操作')
    if action in OWNER_ONLY:
        if role == '负责人' or (owner and owner == user):
            return True, ''
        return False, MSG_OWNER
    return True, ''


def check(ctx, action, period=None, project=None):
    ok, why = can(ctx, action, period, project)
    if not ok:
        raise Denied(why)


def period_can(ctx, period):
    """期详情里返回的 can 列表（网页按钮置灰用）。"""
    return [a for a in PERIOD_ACTIONS if can(ctx, a, period)[0]]


def can_see_period(ctx, period):
    return can(ctx, '看', period)[0]
