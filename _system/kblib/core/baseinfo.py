# From 大大王.DT
"""基础知识库：账号、环境等通用信息；机密只在钥匙串。"""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from kblib.infra import vault
from kblib.core.common import BASE, KBError, current_ctx, now, uuid_hex
from kblib.core import db as _db
from kblib.core.search import index_docs


# 所有期共用的基础信息：网站账号、测试账号、Wi-Fi / 代理 / VPN、环境配置等。
# 机密字段（密码、令牌）只存 macOS 钥匙串（服务名 kb-base），json / md / 索引 / 网页默认都只显示掩码。
BASE_FILE = BASE / '基础信息.json'
BASE_MD = BASE / '基础信息.md'
BASE_LOG = BASE / '记录.md'
BASE_CATS = ['账号密码', '测试账号', '网络代理', 'VPN', '环境配置', '远程仓库', '其他']
# 分类模板：网页新增 / 编辑时选了这个分类，按顺序出这些字段（名、提示、必填）。远程仓库一个马甲一条，同一个克隆地址的几条共用工作副本池
BASE_TEMPLATES = {
    '远程仓库': [
        {'名': '克隆地址', '提示': 'ssh://user@host:29418/xxx、git@host:group/xxx.git、https://…（ssh 密钥先在服务器 ~/.ssh 配好）', '必填': True},
        {'名': '基线分支', '提示': '这个马甲的 master，如 alpha-master；开发分支不存在时从这里拉，也用来认马甲线', '必填': True},
        {'名': '平台类型', '提示': 'Android / iOS / 鸿蒙 / 服务端 / Web…'},
        {'名': '运行模块', '提示': '运行到手机编哪个 app 模块，如 :app-alpha（空 = 按马甲线自动认）'},
        {'名': '运行变体', '提示': '如 alphaGpDebug（空 = debug）'},
        {'名': '包名', '提示': '如 com.example.alpha'},
        {'名': '克隆后命令', '提示': '可空；<dir> = 副本目录。Gerrit 地址不填时自动装 commit-msg 钩子'},
    ],
}
KC_SERVICE = os.environ.get('KB_KC_SERVICE', 'kb-base')
MASK = '●●●●●●'


def _kc_set(acct, value):           # 名字沿用「钥匙串」：Mac 上存钥匙串，远端服务器存加密文件（infra/vault.py）
    try:
        vault.put(KC_SERVICE, acct, value)
    except vault.VaultError as e:
        raise KBError(str(e))


def _kc_get(acct):
    try:
        return vault.get(KC_SERVICE, acct)
    except vault.VaultError as e:
        raise KBError(str(e))


def _kc_del(acct):
    try:
        vault.delete(KC_SERVICE, acct)
    except vault.VaultError as e:
        raise KBError(str(e))


def _base_log(action, content):
    BASE.mkdir(exist_ok=True)
    if not BASE_LOG.exists():
        BASE_LOG.write_text('# 基础知识库 · 记录\n\n', 'utf-8')
    with BASE_LOG.open('a', encoding='utf-8') as f:
        f.write(f'- {now()} ｜ {action} ｜ {content}\n')


def base_load():
    return json.loads(BASE_FILE.read_text('utf-8')) if BASE_FILE.exists() else {'条目': [], '序号': 0}


def _field_md(fd):
    tag = '（个人）' if fd.get('个人') else ''
    return f'{fd["名"]}{tag}：{MASK}（{vault.LABEL}）' if fd.get('机密') else f'{fd["名"]}{tag}：{fd.get("值", "")}'


def base_entry_md(e, head='##'):
    out = [f'{head} {e["编号"]} ［{e["分类"]}］{e["名称"]}']
    if e.get('模板'):
        out.append('- 模板：员工可「从模板复制」成自己的私有条目，只填标了（个人）的字段')
    elif e.get('可见') == '私有':
        out.append(f'- 可见：私有（{e.get("所有人") or "?"} 和管理员）')
    if e.get('适用'):
        out.append(f'- 适用：{e["适用"]}')
    out += [f'- {_field_md(fd)}' for fd in e.get('字段') or []]
    if e.get('附件'):
        out.append('- 附件：' + '、'.join(e['附件']))
    if e.get('备注'):
        out += ['', e['备注'].strip()]
    return '\n'.join(out)


def _base_save_all(data):
    BASE.mkdir(exist_ok=True)
    BASE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), 'utf-8')
    es = [e for e in data['条目'] if not _private(e)]           # 私有条目不进 md（也就不进检索）
    md = ['# 基础知识库', '', '> 自动生成，请勿手改（用网页「🗂 基础信息」或让 Claude 修改）。所有期共用；机密字段只存钥匙串，这里只显示掩码。', '']
    for c in BASE_CATS:
        part = [e for e in es if e['分类'] == c]
        if part:
            md += [f'# {c}', ''] + [base_entry_md(e) + '\n' for e in part]
    BASE_MD.write_text('\n'.join(md) + '\n', 'utf-8')
    index_docs()


# ---------------------------------------------------------------- 公共 / 私有 / 模板（用户 2026-11-20 22:04 / 22:09）
# 公共：所有人能看能改；私有：只有「所有人」和管理员能看、能改、能删（机密明文按 REQ-007 个人层，只给本人）；
# 模板：管理员建，人人可见、只有管理员能改；字段标「个人」的在复制时由员工填，其余（平台名、链接）自动带上。
VISIBILITY = ['公共', '私有']


def _private(e):
    return e.get('可见') == '私有' and not e.get('模板')


def _viewer(ctx=None):
    """None = 知识库自己（插件、同步、Bug 平台登录），什么都能看；否则是登录人 / 会话发起人（管家是空串）。"""
    c = current_ctx() if ctx is None else ctx
    return c.get('user', '') if c.get('via') else None


def _admin(u):
    from kblib.core import authz
    return bool(u) and authz.is_admin(u)


def base_can_see(e, ctx=None):
    u = _viewer(ctx)
    if u is None or not _private(e):
        return True
    return bool(u) and (e.get('所有人') == u or _admin(u))


def base_edit_denied(e, ctx=None):
    """不能改 / 删这条的原因（空 = 可以）。"""
    u = _viewer(ctx)
    if u is None:
        return ''
    if e.get('模板') and not _admin(u):
        return '只有管理员能建基础信息模板'
    if _private(e) and not (e.get('所有人') == u or _admin(u)):
        return '这是别人的私有基础信息：只有本人和管理员能改'
    return ''


def attachment_denied(p, ctx=None):
    """基础/附件/ 里属于私有条目、当前人看不到的附件（个人 VPN 配置之类）：返回原因；空 = 可以。"""
    try:
        p = Path(p).resolve()
        base = BASE.resolve()
    except (OSError, RuntimeError):
        return ''
    if base not in p.parents:
        return ''
    for e in base_load()['条目']:
        if _private(e) and any((BASE / a).resolve() == p for a in e.get('附件') or []) and not base_can_see(e, ctx):
            return '这是别人的私有基础信息附件'
    return ''


def base_visible(ctx=None):
    """当前登录人 / 会话能看到的条目（网页、kb_base、开发包用）。"""
    return [e for e in base_load()['条目'] if base_can_see(e, ctx)]


def base_save(*a, **kw):
    """新增 / 修改 / 删除一条基础信息，返回说明文字（MCP / 命令行用）；要编号用 base_save_id。"""
    return base_save_id(*a, **kw)[0]


def base_save_id(id='', category='', name='', fields=None, scope='', note='', file='', delete=False, by='Claude',
                 layer=None, project=None, owner=None, visibility=None, holder=None, template=None):
    """新增 / 修改 / 删除一条基础信息 → (说明, 编号)。fields：[{名, 值, 机密}]；修改时机密字段值留空表示不改。
    编号直接返回（不用全局变量，并发保存时不会拿到别人那条）。"""
    data = base_load()
    es = data['条目']
    e = next((x for x in es if x['编号'] == id), None) if id else None
    if id and (not e or not base_can_see(e)):
        raise KBError(f'没有这条基础信息：{id}')
    was_private = bool(e) and _private(e)
    if e:
        why = base_edit_denied(e)
        if why:
            from kblib.core.authz import Denied
            raise Denied(why)
        if visibility in VISIBILITY and not e.get('模板') and not delete and (visibility == '私有') != was_private:
            _check_visibility_change(e, visibility, _viewer(), current_ctx())     # 先校验再动任何字段 / 机密
    if not delete:
        _precheck_save(e, visibility, holder, template)                          # 权限 / 参数都过了才动字段和钥匙串
    if delete:
        for fd in e.get('字段') or []:
            if fd.get('机密'):
                _kc_del(f'{e["编号"]}/{fd["名"]}')
        es.remove(e)
        _base_save_all(data)
        _db.secret_meta_del(e['编号'])
        _base_log('删除', f'{e["编号"]} {e["名称"]}（{by}）')
        return f'已删除基础信息 {e["编号"]} {e["名称"]}', e['编号']
    if not e:
        if not (name or '').strip():
            raise KBError('名称不能为空')
        data['序号'] = data.get('序号', 0) + 1
        e = {'编号': f'B{data["序号"]:03d}', '分类': '其他', '名称': '', '适用': '', '字段': [], '备注': '', '附件': [],
             '创建时间': now()}
        es.append(e)
        action = '新增'
    else:
        action = '修改'
    if category:
        if category not in BASE_CATS:
            raise KBError(f'分类只能是：{" / ".join(BASE_CATS)}')
        e['分类'] = category
    for k, v in (('名称', name), ('适用', scope), ('备注', note)):
        if v is not None and (v or k != '名称'):
            e[k] = (v or '').strip()
    kc_ops = []          # 钥匙串的写 / 删先记下，所有校验都过了、保存前才执行：被拒的请求不改机密、不留孤儿
    if fields is not None:
        old = {fd['名']: fd for fd in e.get('字段') or []}
        new = []
        for fd in fields:
            nm = str(fd.get('名') or fd.get('name') or '').strip()
            if not nm:
                continue
            val = str(fd.get('值') if fd.get('值') is not None else fd.get('value') or '')
            secret = bool(fd.get('机密') if '机密' in fd else fd.get('secret'))
            personal = bool(fd.get('个人') if '个人' in fd else fd.get('personal'))
            tpl = template if template is not None else e.get('模板')
            acct = f'{e["编号"]}/{nm}'
            if secret:
                if val:
                    kc_ops.append(('set', acct, val))
                elif not (old.get(nm, {}).get('机密') and _kc_get(acct) is not None) and not (tpl and personal):
                    raise KBError(f'机密字段「{nm}」还没有值')
                new.append({'名': nm, '值': '', '机密': True, **({'个人': True} if personal else {})})
            else:
                if old.get(nm, {}).get('机密'):
                    kc_ops.append(('del', acct, None))
                new.append({'名': nm, '值': val, '机密': False, **({'个人': True} if personal else {})})
        for nm, fd in old.items():  # 删掉的机密字段同时清钥匙串
            if fd.get('机密') and nm not in {x['名'] for x in new}:
                kc_ops.append(('del', f'{e["编号"]}/{nm}', None))
        e['字段'] = new
    if file:
        src = Path(file).expanduser()
        if not src.is_file():
            raise KBError(f'附件不存在：{src}')
        (BASE / '附件').mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, BASE / '附件' / src.name)
        if f'附件/{src.name}' not in e['附件']:
            e['附件'].append(f'附件/{src.name}')
    if e['分类'] in BASE_TEMPLATES:
        vals = {fd['名']: fd.get('值', '') for fd in e.get('字段') or []}
        miss = [t['名'] for t in BASE_TEMPLATES[e['分类']] if t.get('必填') and not str(vals.get(t['名']) or '').strip()]
        if miss:
            raise KBError(f'「{e["分类"]}」要填：{"、".join(miss)}')
        if e['分类'] == '远程仓库':
            from kblib.infra.pool import is_clone_url
            if not is_clone_url(vals['克隆地址']):
                raise KBError(f'克隆地址不像 git 地址：{vals["克隆地址"]}（ssh://…、git@…:…、https://…）')
    c = current_ctx()
    u = _viewer()
    if template is not None:
        if template and u is not None and not _admin(u):
            from kblib.core.authz import Denied
            raise Denied('只有管理员能建基础信息模板')
        e['模板'] = bool(template)
        if template:
            e['可见'] = '公共'
            e.pop('所有人', None)
    if visibility is not None and not e.get('模板'):
        if visibility not in VISIBILITY:
            raise KBError(f'可见只能是：{" / ".join(VISIBILITY)}')
        e['可见'] = visibility
    if action == '新增':
        e.setdefault('可见', '公共')
        e['创建人'] = u or by
    if _private(e):
        who = holder or e.get('所有人') or u or ''
        if holder and u is not None and holder != u and not _admin(u):
            from kblib.core.authz import Denied
            raise Denied('只有管理员能给别人建私有基础信息')
        if holder and not _db.user_get(holder):
            raise KBError(f'没有这个账号：{holder}')
        if action == '修改' and e.get('所有人') and who != e['所有人']:
            _db.audit('机密', _subject(c) if c.get('via') else by, e['编号'], e.get('项目') or '', '成功',
                      f'私有基础信息转给 {who}（原 {e["所有人"]}）')
        e['所有人'] = who
        layer = '个人'                                  # 私有条目的机密按「个人」层：明文只给本人（REQ-007）
        owner = who
    elif e.get('所有人') and visibility == '公共':
        e.pop('所有人', None)
        if layer is None:
            layer = '项目'
    if action == '新增' or project is not None:
        e['项目'] = project or e.get('项目') or c.get('project') or 'default'
    if layer is not None or action == '新增' or visibility is not None:
        lay = layer or '项目'
        if lay not in LAYERS:
            raise KBError(f'机密层级只能是：{" / ".join(LAYERS)}')
        old = _db.secret_meta(e['编号'])
        if layer is None and old and action != '新增':
            lay = old['layer']
        if old and old['layer'] != lay and (old['layer'] == '团队' or lay == '团队') and c.get('via') == '网页':
            u = _db.user_get(c.get('user', ''))
            if not u or u.get('sys_role') != '管理员':
                raise KBError('只有管理员能改「团队」层级')
        _db.secret_meta_put(e['编号'], lay, owner if lay == '个人' else (owner or (old or {}).get('owner') or c.get('user', '')), e['项目'])
    e['更新时间'] = now()
    for op, acct, val in kc_ops:
        _kc_set(acct, val) if op == 'set' else _kc_del(acct)
    _base_save_all(data)
    _base_log(action, f'{e["编号"]} ［{e["分类"]}］{e["名称"]}' + ('（私有）' if _private(e) else '（模板）' if e.get('模板') else '') + f'（{by}）')
    return f'已{action}基础信息：\n' + base_entry_md(e), e['编号']


def _precheck_save(e, visibility, holder, template):
    """保存前先校验模板 / 可见 / 所有人（审查第 1 轮：原来在写钥匙串之后才校验，被拒时机密已经改了）。"""
    from kblib.core.authz import Denied
    u = _viewer()
    if template and u is not None and not _admin(u):
        raise Denied('只有管理员能建基础信息模板')
    if visibility is not None and visibility not in VISIBILITY:
        raise KBError(f'可见只能是：{" / ".join(VISIBILITY)}')
    tpl = bool(template) if template is not None else bool(e and e.get('模板'))
    vis = visibility or ((e or {}).get('可见') or '公共')
    if holder and vis == '私有' and not tpl:
        _check_holder(holder, u)


def _check_holder(holder, u):
    from kblib.core.authz import Denied
    if u is not None and holder != u and not _admin(u):
        raise Denied('只有管理员能给别人建私有基础信息')
    if not _db.user_get(holder):
        raise KBError(f'没有这个账号：{holder}')


def _check_visibility_change(e, visibility, u, c):
    """公共 ↔ 私有：只有创建人、当前所有人或管理员能改；改成私有前，改的人必须本来就读得到这条里的每个机密
    （否则借「改成私有 → 机密降成个人层 → 自己是所有人」就能读到别的项目 / 团队的机密，审查第 2 轮复现过）。"""
    from kblib.core.authz import Denied
    if u is None:
        return
    if not (_admin(u) or (e.get('创建人') and e['创建人'] == u) or (e.get('所有人') and e['所有人'] == u)):
        raise Denied('只有创建人或管理员能改这条的可见范围')
    if visibility == '私有' and not _admin(u) and any(fd.get('机密') for fd in e.get('字段') or []):
        ok, why, _m = secret_allowed(e['编号'], c)
        if not ok:
            raise Denied(f'这条里有你读不到的机密（{why}），不能改成私有')
    _db.audit('机密', _subject(c) if c.get('via') else '系统', e['编号'], e.get('项目') or '', '成功',
              f'可见范围改成{visibility}')


def base_copy_template(tid, holder='', name='', values=None, by=''):
    """从模板复制一条私有基础信息：共享字段（平台名、链接、共用令牌）照抄，标「个人」的字段用 values 填。返回 (消息, 新编号)。"""
    t = next((x for x in base_load()['条目'] if x['编号'] == tid), None)
    if not t or not t.get('模板'):
        raise KBError(f'没有这个基础信息模板：{tid}')
    vals = {str(k): str(v) for k, v in (values or {}).items() if v is not None}
    fields, shared_secrets = [], []
    for fd in t.get('字段') or []:
        nm = fd['名']
        if fd.get('个人'):
            v = vals.get(nm, '').strip()
            if fd.get('机密') and not v:
                raise KBError(f'要填「{nm}」')
            fields.append({'名': nm, '值': v, '机密': bool(fd.get('机密'))})
        elif fd.get('机密'):
            shared_secrets.append(nm)
            fields.append({'名': nm, '值': None, '机密': True})
        else:
            fields.append({'名': nm, '值': fd.get('值', ''), '机密': False})
    u = _viewer()
    who = holder or u or ''
    c = current_ctx()
    if holder:
        _check_holder(holder, u)            # 先校验替谁复制，再读共用机密（被拒的请求不读机密、不写审计）
    if shared_secrets:          # 模板里共用的机密（如项目共用 token）：复制的人本来就要读得到，读了记审计（REQ-007）
        from kblib.core.authz import Denied
        ok, why, m = secret_allowed(tid, c)
        if not ok:
            raise Denied(f'模板里的「{"、".join(shared_secrets)}」是你读不到的机密（{why}）：请管理员替你复制，或让管理员把它标成「个人」')
        got = {nm: _kc_get(f'{tid}/{nm}') or '' for nm in shared_secrets}
        for fd in fields:
            if fd['值'] is None:
                fd['值'] = got.get(fd['名'], '')
    msg, nid = base_save_id(category=t['分类'], name=name or f'{t["名称"]}（{who}）', fields=fields, scope=t.get('适用', ''),
                            note=t.get('备注', ''), by=by or '用户（网页）', visibility='私有', holder=who)
    for nm in shared_secrets:               # 复制成功了才记「读了共用机密」
        _db.audit('机密', _subject(c) if c.get('via') else (by or '系统'), f'{tid}/{nm}', m.get('project') or '', '成功',
                  f'从模板复制给 {who}（{nid}）')
    data = base_load()
    for e in data['条目']:
        if e['编号'] == nid:
            e['来自模板'] = tid
    _base_save_all(data)
    return msg, nid


def base_list(query='', category=''):
    es = base_visible()
    if category:
        es = [e for e in es if e['分类'] == category]
    if query:
        norm = lambda x: re.sub(r'[\s\-_./]', '', x.lower())
        terms = [norm(t) for t in query.split()]
        es = [e for e in es if all(t in norm(base_entry_md(e)) for t in terms)]
    if not es:
        return '基础知识库里没有匹配的条目。' + ('' if query or category else '（用 kb_base_save 添加）')
    return ('\n\n'.join(base_entry_md(e) for e in es)
            + f'\n\n（机密字段显示为 {MASK}；执行时确实要用，再用 kb_base_secret 读取）')


LAYERS = ['个人', '项目', '团队']


def secret_layer(id):
    """机密的层级和所属（没有登记的老机密算「团队」，REQ-007 迁移规则）。"""
    m = _db.secret_meta(id)
    if m:
        return m
    e = next((x for x in base_load()['条目'] if x['编号'] == id), None)
    return {'id': id, 'layer': '团队', 'owner': '', 'project': (e or {}).get('项目') or 'default'}


def _subject(c):
    via, u = c.get('via', ''), c.get('user', '')
    if via == 'AI':
        return f'会话 {c.get("session") or "?"}（发起人 {u or "?"}）'
    if via == '管家':
        return '管家'
    return u or '系统'


def secret_allowed(id, ctx=None):
    """按会话发起人和项目校验能不能读（流程架构 §8）：个人机密只给本人和他发起的会话；项目机密只给本项目；
    团队机密给所有正常成员（只读账号不行）。没有上下文的是知识库自己（插件配置、Bug 平台登录），放行。"""
    c = current_ctx() if ctx is None else ctx
    m = secret_layer(id)
    via, u = c.get('via', ''), c.get('user', '')
    if not via:
        return True, '', m
    if via == '管家':
        return (m['layer'] != '个人', '管家不能读个人机密', m)
    if m['layer'] == '个人':
        return (bool(u) and u == m['owner'], '这是别人的个人机密', m)
    if via == 'AI':
        if m['layer'] == '项目' and c.get('project') and c['project'] != m['project']:
            return False, '不是本项目的机密', m
        if m['layer'] == '项目' and not c.get('project'):       # 个人令牌：看持有者在不在这个项目
            from kblib.core import authz
            if not authz.is_admin(u) and authz.project_role(u, m['project']) not in ('负责人', '成员'):
                return False, '不是本项目的机密', m
        if m['layer'] == '团队' and u and not c.get('project'):
            from kblib.core import authz
            if not authz.is_admin(u) and not any(r != '只读' for r in _db.member_roles(u).values()):
                return False, '只读账号不能读机密', m
        return True, '', m
    from kblib.core import authz
    if authz.is_admin(u):
        return True, '', m
    if m['layer'] == '项目':
        role = authz.project_role(u, m['project'])
        return (role in ('负责人', '成员'), '不是本项目的机密' if not role else '只读账号不能读机密', m)
    roles = _db.member_roles(u)
    return (any(r != '只读' for r in roles.values()), '只读账号不能读机密', m)


def base_secret(id, field, by='Claude', purpose=''):
    if str(id).startswith('TA') and not any(x['编号'] == id for x in base_load()['条目']):
        return test_account_secret(id, purpose)
    e = next((x for x in base_load()['条目'] if x['编号'] == id), None)
    if not e or not base_can_see(e):
        raise KBError(f'没有这条基础信息：{id}')
    fd = next((x for x in e['字段'] if x['名'] == field), None)
    if not fd:
        raise KBError(f'{id} 没有字段「{field}」，有：{"、".join(x["名"] for x in e["字段"])}')
    if not fd.get('机密'):
        return fd.get('值', '')
    c = current_ctx()
    ok, why, m = secret_allowed(id, c)
    _db.audit('机密', _subject(c) if c.get('via') else (by or '系统'), f'{id}/{field}', m.get('project') or '',
              '成功' if ok else '拒绝', purpose or why)
    if not ok:
        raise KBError(f'不能读 {id} 的「{field}」：{why}')
    v = _kc_get(f'{id}/{field}')
    if v is None:
        raise KBError(f'钥匙串里找不到 {id} 的「{field}」')
    _base_log('读取机密', f'{id} {e["名称"]} · {field}（{by}）' + (f'：{purpose}' if purpose else ''))
    return v


def base_secret_entry_delete(id):
    """删掉一条基础信息的全部机密值（停用成员时删他的个人机密，REQ-028）。"""
    e = next((x for x in base_load()['条目'] if x['编号'] == id), None)
    if e:
        base_save(id=id, delete=True, by='停用交接')
    else:
        _db.secret_meta_del(id)


def base_context():
    """给开发包 / SDD 资料用的基础信息摘要（机密只显示掩码；私有的只给本人的会话）。"""
    es = base_visible()
    if not es:
        return ''
    return '\n\n'.join(base_entry_md(e, '###') for e in es)


# ================================================================ 测试账号清单（REQ-046 REQ-047）
# 从「基础信息 → 测试账号」升级为独立清单（test_accounts 表）：归属项目 + 环境（测试 / 预发 / 线上）、标签、允许同时使用数；
# 密码仍只在钥匙串 / 加密文件。老条目迁移时保留原基础信息（kb_base / kb_base_secret 照旧能用），清单里记它的机密位置。
# 每个账号在资源池里是一个资源 acct:<编号>（容量 = 允许同时使用数），会话用 kb_account_claim 申请、排队。
TA_ENVS = ['测试', '预发', '线上']
_ACCT_FIELDS = ('账号', '用户名', '登录名', '手机号', '手机', '邮箱', 'uid', 'UID', '用户')


def _ta_res(t):
    from kblib.core import resources
    resources.put_resource(f'acct:{t["id"]}', '测试账号', {'项目': t['project'], '环境': t['env'], '标签': t.get('tags') or [],
                                                          '名称': t['name'], '账号': t.get('account') or '', '允许AI': bool(t.get('allow_ai'))},
                           capacity=t.get('conc') or 1, provider='清单')


def test_accounts_migrate():
    """升级：基础信息里原有「测试账号」归到它的项目（没有就默认项目）的测试环境；幂等。返回迁了几个。"""
    have = {t['secret'].split('/')[0] for t in _db.rows('test_accounts') if t.get('secret')} | \
        {t['id'][3:] for t in _db.rows('test_accounts') if t['id'].startswith('TA-')}
    n = 0
    for e in base_load()['条目']:
        if e.get('分类') != '测试账号' or e['编号'] in have:
            continue
        fs = e.get('字段') or []
        acct = next((f.get('值', '') for f in fs if not f.get('机密') and f['名'] in _ACCT_FIELDS), '') or \
            next((f.get('值', '') for f in fs if not f.get('机密')), '')
        sec = next((f['名'] for f in fs if f.get('机密')), '')
        t = dict(id=f'TA-{e["编号"]}', project=e.get('项目') or 'default', env='测试', name=e.get('名称') or e['编号'], account=acct,
                 tags=[], conc=1, allow_ai=1, secret=f'{e["编号"]}/{sec}' if sec else '', created=now())
        _db.insert('test_accounts', **t)
        _ta_res(t)
        n += 1
    return n


def _ta_view(t, holds=True):
    from kblib.core import resources
    out = {'id': t['id'], '项目': t['project'], '环境': t['env'], '名称': t['name'], '账号': t.get('account') or '',
           '标签': t.get('tags') or [], '允许同时使用数': t.get('conc') or 1, '允许AI': bool(t.get('allow_ai')),
           '密码': MASK if t.get('secret') else ''}
    if holds:
        out['占用'] = [resources.hold_view(h, '测试账号') for h in resources.holds_of(f'acct:{t["id"]}')]
    return out


def test_account_list(project=None, env=None, projects=None):
    test_accounts_migrate()
    w = {}
    if project:
        w['project'] = project
    if env:
        w['env'] = env
    out = [_ta_view(t) for t in _db.rows('test_accounts', w, order='project, env, name')]
    if projects is not None:
        out = [t for t in out if t['项目'] in projects]
    return out


def test_account_get(tid):
    return _db.get('test_accounts', id=tid)


def test_account_save(body, by=''):
    """新增 / 修改 / 删除。返回 (消息, id)。权限由调用方判（成员可加，删改限项目负责人）。"""
    tid = body.get('id') or ''
    old = test_account_get(tid) if tid else None
    if tid and not old:
        raise KBError(f'没有这个测试账号：{tid}')
    if body.get('删除'):
        from kblib.core import resources
        resources.drop_resource(f'acct:{tid}')
        _db.delete('test_accounts', {'id': tid})
        if old.get('secret') and old['secret'].startswith(tid + '/'):
            _kc_del(old['secret'])
            _db.secret_meta_del(tid)
        _db.audit('测试账号', by, tid, old['project'], '成功', '删除')
        return f'已删除测试账号 {old["name"]}', tid
    env = body.get('环境') or (old or {}).get('env') or '测试'
    if env not in TA_ENVS:
        raise KBError(f'环境只能是：{" / ".join(TA_ENVS)}')
    name = (body.get('名称') or (old or {}).get('name') or '').strip()
    project = body.get('项目') or (old or {}).get('project') or current_ctx().get('project') or 'default'
    if not name:
        raise KBError('名称不能为空')
    conc = int(body.get('允许同时使用数') or (old or {}).get('conc') or 1)
    if conc < 1:
        raise KBError('允许同时使用数至少 1')
    tags = body.get('标签') if body.get('标签') is not None else (old or {}).get('tags') or []
    if isinstance(tags, str):
        tags = [x.strip() for x in re.split(r'[,，\s]+', tags) if x.strip()]
    allow = body.get('允许AI')
    if allow is None:
        allow = (old or {}).get('allow_ai') if old else env != '线上'      # 线上环境默认不给 AI
    f = dict(project=project, env=env, name=name, account=str(body.get('账号') if body.get('账号') is not None else (old or {}).get('account') or ''),
             tags=tags, conc=conc, allow_ai=1 if allow else 0)
    if not old:
        tid = 'TA' + uuid_hex()[:6]
        _db.insert('test_accounts', id=tid, created=now(), secret='', **f)
    else:
        _db.update('test_accounts', {'id': tid}, **f)
    if body.get('密码'):
        acct = (old or {}).get('secret') or f'{tid}/密码'
        _kc_set(acct, str(body['密码']))
        _db.update('test_accounts', {'id': tid}, secret=acct)
        if acct.startswith(tid + '/'):
            _db.secret_meta_put(tid, '项目', by, project)
    t = test_account_get(tid)
    _ta_res(t)
    _db.audit('测试账号', by, tid, project, '成功', '新增' if not old else '修改')
    return ('已添加' if not old else '已保存') + f'测试账号 {name}', tid


def test_account_secret(tid, purpose=''):
    """读测试账号密码明文：按机密规则（项目层）判权限并写审计（REQ-046）。"""
    t = test_account_get(tid)
    if not t:
        raise KBError(f'没有这个测试账号：{tid}')
    if not t.get('secret'):
        return ''
    sid, field = t['secret'].split('/', 1)
    if not sid.startswith('TA'):                       # 迁移来的：沿用基础信息的机密
        return base_secret(sid, field, by=current_ctx().get('user') or 'Claude', purpose=purpose or f'测试账号 {t["name"]}')
    c = current_ctx()
    ok, why, m = secret_allowed(sid, c)
    _db.audit('机密', _subject(c) if c.get('via') else '系统', t['secret'], t['project'], '成功' if ok else '拒绝', purpose or why)
    if not ok:
        raise KBError(f'不能读测试账号 {t["name"]} 的密码：{why}')
    v = _kc_get(t['secret'])
    if v is None:
        raise KBError(f'钥匙串里找不到测试账号 {t["name"]} 的密码')
    return v
