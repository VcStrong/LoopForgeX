# From 大大王.DT
"""命令行（kb …）。"""
import argparse
import json
import os
import re
import sys
from kblib.core.common import CATS, INBOX, KBError, ROOT, STATUSES
from kblib.core.periods import (
    TASK_KINDS,
    TASK_PRI,
    add_log,
    add_material,
    create_period,
    create_task,
    init,
    period_dir,
    set_status,
)
from kblib.core.notes import NOTE_KINDS, add_note, update_note
from kblib.core.repos import add_repo, link_repo
from kblib.core.search import reindex, search
from kblib.plugins.registry import PLUGIN_DIR, call_plugin, plugins
from kblib.plugins.run import plugin_run_sync
from kblib.infra.timing import timing_get
from kblib.api.overview import overview, period_detail
from kblib.sources.urlimport import _fetch, fetch_url, verify_source
from kblib.sources.refresh import refresh_all, refresh_sources
from kblib.sources.fixlink import fix_link
from kblib.bugs.sources import bug_sources, bug_test
from kblib.bugs.index import bug_assign, bug_get, bug_list, bug_sync, save_zentao_settings
from kblib.bugs.analysis import bug_analyze
from kblib.bugs.fix import bug_fixed
from kblib.anr.store import anr_add, anr_link, anr_list, anr_mapping, anr_set_version, anr_show
from kblib.api.mcp import serve
from kblib.plugins.stats import stats_facts, stats_text
from kblib.steward.patrol import steward_text, steward_tick
from kblib.steward.onboard import demo_create


def approve(name):
    d = period_dir(name)
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise KBError('批准开发必须由你本人在终端窗口里运行（防止 AI 代为批准）。')
    print(period_detail(d.name, log_lines=5))
    ans = input(f'\n确认批准「{d.name}」开始开发？请输入期名确认：').strip()
    if ans != d.name:
        raise KBError('输入不一致，已取消。')
    note = input('备注（可空）：').strip()
    return set_status(d.name, '已批准开发', note or '用户批准', by_human=True)


def _ask_pw(prompt, given=''):
    import getpass
    if given:
        return given
    p1 = getpass.getpass(prompt)
    if getpass.getpass('再输一次：') != p1:
        raise KBError('两次不一样')
    return p1


def _user_cmd(a):
    """kb user add / list / passwd（REQ-001）。"""
    from kblib.core import users
    try:
        if a.op == 'list':
            xs = users.users_list()
            print('\n'.join(f'- {u["用户名"]}（{u["系统角色"]}，{u["状态"]}）' + ('　本机用户' if u.get('本机用户') else '')
                            + (f'　最近登录 {u["最近登录"]}' if u['最近登录'] else '') for u in xs) or '还没有账号')
            return 0
        if not a.name:
            raise KBError(f'用法：kb user {a.op} <用户名>')
        if a.op == 'add':
            pw = _ask_pw(f'{a.name} 的初始密码（至少 8 位，首次登录要改）：', a.password)
            print(users.user_create_strict(a.name, pw, '管理员' if a.admin else '普通', by='终端'))
        else:
            pw = _ask_pw(f'{a.name} 的新密码（至少 8 位，下次登录要改）：', a.password)
            print(users.user_save(a.name, pw, by='终端'))
        return 0
    except KBError as x:
        print(f'✗ {x}', file=sys.stderr)
        return 1


def _port_cmd(a):
    """kb port / set / reset：启动前改网页端口（启动后改在网页「⚙ 设置 → 🌐 网页端口」）。"""
    from kblib.infra import webport
    try:
        if a.op == 'set':
            p = webport.save(a.port, by='终端')
            run = webport.actual()
            tip = '' if run.get('已停止') or not run else f'\n正在运行的网页还在 {run.get("端口")}：重启 kb web（或网页里改，自动重启）后生效'
            print(f'网页端口改成 {p}（写在 _system/web.json，开机自启也按它）{tip}\n'
                  '已接入的执行机：kb runner --server http://<地址>:' + str(p) + ' --rebind；kb token 接入的远程 Claude Code 也要改地址')
            return 0
        if a.op == 'reset':
            print(f'已恢复默认端口 {webport.reset()}（重启 kb web 生效）')
            return 0
        st = webport.status()
        run = webport.actual()
        print(f"配置端口：{st['配置端口']}（来源：{st['来源']}）")
        if run and not run.get('已停止'):
            print(f"正在运行：http://127.0.0.1:{run['端口']}" + (f"　{run['提示']}" if run.get('提示') else ''))
        else:
            print('网页没在运行')
        if st['不能改的原因']:
            print(st['不能改的原因'])
        return 0
    except KBError as x:
        print(f'✗ {x}', file=sys.stderr)
        return 1


def _db_cmd(a):
    """kb db status / use / copy（REQ-021：SQLite / PostgreSQL / MySQL 按场景选驱动）。"""
    from kblib.core import db
    try:
        if a.op == 'status':
            st = db.status()
            print(f"驱动：{st['驱动']}（来源：{st['来源']}）\n连接：{st['连接']}\n表版本：{st['表版本']}\n"
                  + '　'.join(f'{k} {v}' for k, v in st['行数'].items()))
            return 0
        if a.op == 'use':
            if os.environ.get('KB_DB_URL'):
                print('！环境变量 KB_DB_URL 已设置，它优先于 kb db use；要改先改环境变量', file=sys.stderr)
            url = db.use_url(a.url)
            print(f'之后用：{db.safe_url(url)}（重启 kb web 生效）。原来的数据不会自动搬：先 kb db copy --to <连接串> 再 use')
            return 0
        url = a.to or a.url
        if not url:
            raise KBError('用法：kb db copy --to <连接串>')
        n = db.copy_to(url, force=a.force)
        print(f'已复制到 {db.safe_url(url)}：' + '、'.join(f'{k} {v}' for k, v in n.items() if v) + f'\n切过去：kb db use {db.safe_url(url)}（密码照原样填）')
        return 0
    except KBError as x:
        print(f'✗ {x}', file=sys.stderr)
        return 1


def _move_cmd(a):
    """kb backup --for-move / kb restore（REQ-034）。"""
    from kblib.core import migrate
    import getpass
    try:
        if a.cmd == 'backup':
            if not a.for_move:
                raise KBError('现在只有搬家备份：kb backup --for-move')
            pw = a.passphrase or _ask_pw('给机密主密钥设个口令（至少 8 位，恢复时要输）：')
            print(migrate.backup_for_move(a.out, pw))
        else:
            pw = a.passphrase or (getpass.getpass('备份时设的口令（归档里没有加密主密钥就直接回车）：') if sys.stdin.isatty() else '')
            print(migrate.kb_restore(a.archive, pw))
        return 0
    except KBError as x:
        print(f'✗ {x}', file=sys.stderr)
        return 1


def _doctor_migrate():
    """kb doctor 末尾：迁移校验（条数一致、每期能打开、机密能读，REQ-027 REQ-034）。"""
    from kblib.core import migrate
    try:
        bad, stats = migrate.migrate_check(True)
    except Exception as e:      # noqa: BLE001
        print(f'\n✗ 迁移校验出错：{e!r}')
        return 1
    brief = '、'.join(f'{k} {v}' for k, v in stats.items())
    if bad:
        print('\n✗ 迁移校验不通过：\n- ' + '\n- '.join(bad) + f'\n（{brief}）')
        return 1
    print(f'\n✓ 迁移校验通过：{brief}')
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog='kb', description='本地知识库（需求 / 设计图 / 接口文档 / 其他资料 / 代码）')
    sp = ap.add_subparsers(dest='cmd')
    sp.add_parser('init', help='初始化目录')
    sp.add_parser('list', help='总览')
    x = sp.add_parser('new', help='新建一期')
    x.add_argument('name')
    x.add_argument('summary', nargs='?', default='')
    x = sp.add_parser('show', help='查看一期')
    x.add_argument('name')
    x = sp.add_parser('add', help='归档文件到某期')
    x.add_argument('name')
    x.add_argument('files', nargs='+')
    x.add_argument('-c', '--category', choices=CATS)
    x.add_argument('-m', '--note', default='')
    x = sp.add_parser('note', help='记一条文字资料：kb note <期名> "内容" [-c 分类] [-k 类型] [-u 链接]；--rm T001 删除')
    x.add_argument('name')
    x.add_argument('content', nargs='?', default='')
    x.add_argument('-c', '--category', choices=CATS)
    x.add_argument('-k', '--kind', choices=NOTE_KINDS)
    x.add_argument('-t', '--title', default='')
    x.add_argument('-u', '--url', default='')
    x.add_argument('-f', '--source', default='', help='来自谁')
    x.add_argument('--rm', metavar='编号', help='删除这条文字资料')
    x = sp.add_parser('inbox', help='把收件箱里的文件全部归档到某期（自动分类）')
    x.add_argument('name')
    x = sp.add_parser('search', help='检索')
    x.add_argument('query', nargs='+')
    x.add_argument('-p', '--period')
    x.add_argument('-s', '--scope', choices=['all', 'docs', 'code'], default='all')
    x.add_argument('-n', '--limit', type=int, default=10)
    x = sp.add_parser('repo', help='登记代码仓库：kb repo <路径或克隆地址> [名称] [--post 克隆后命令]')
    x.add_argument('path')
    x.add_argument('repo_name', nargs='?')
    x.add_argument('--post', default='', help='克隆后命令（<dir> 换成副本目录）；Gerrit 地址不填时自动装 commit-msg 钩子')
    x = sp.add_parser('link', help='关联仓库到某期')
    x.add_argument('name')
    x.add_argument('repo')
    x = sp.add_parser('index', help='更新索引：all / docs / code / 仓库名')
    x.add_argument('target', nargs='?', default='all')
    x = sp.add_parser('approve', help='批准某期开始开发（只能本人在终端执行）')
    x.add_argument('name')
    x = sp.add_parser('status', help='修改状态')
    x.add_argument('name')
    x.add_argument('status', choices=STATUSES)
    x.add_argument('-m', '--note', default='')
    x = sp.add_parser('log', help='追加记录')
    x.add_argument('name')
    x.add_argument('note')
    x = sp.add_parser('login', help='保存某网站的登录凭据（Mac 存钥匙串，服务器存加密文件）：kb login confluence.example.com 用户名')
    x.add_argument('host')
    x.add_argument('user')
    x = sp.add_parser('fetch', help='预览网页 / Confluence 页面')
    x.add_argument('url')
    x = sp.add_parser('verify', help='核对来源：kb verify <期名> S001 已核对|有问题|待核对 [-m 备注]')
    x.add_argument('name')
    x.add_argument('sid')
    x.add_argument('status', choices=['已核对', '有问题', '待核对'])
    x.add_argument('-m', '--note', default='')
    x = sp.add_parser('fixlink', help='更正链接：kb fixlink <期名> <旧链接> <新链接> [-m 原因]')
    x.add_argument('name')
    x.add_argument('old')
    x.add_argument('new')
    x.add_argument('-m', '--reason', default='')
    x = sp.add_parser('refresh', help='按登记的网址刷新来源：kb refresh <期名> [S001]；kb refresh --all 检查所有未完成的期')
    x.add_argument('name', nargs='?', default='')
    x.add_argument('sid', nargs='?', default='')
    x.add_argument('--all', action='store_true')
    x = sp.add_parser('plugin', help='插件：kb plugin（列出）；kb plugin run <插件> <期名或分支> [仓库路径] [--push]（手动运行一次：和网页一样先切分支、拉最新）；'
                                     'kb plugin call <工具插件> [参数…]（按需调用）')
    x.add_argument('action', nargs='?', default='list', choices=['list', 'run', 'call'])
    x.add_argument('plugin', nargs='?', default='')
    x.add_argument('name', nargs='?', default='')
    x.add_argument('event', nargs='?', default='')
    x.add_argument('rest', nargs='*', default=[])
    x.add_argument('--push', action='store_true', help='plugin run：插件改了文件就提交并推送')
    x = sp.add_parser('stats', help='工作统计（统计插件算）：kb stats [今天|本周|本月|近7天|近30天|全部]；--facts 输出给插件的原始事实 JSON')
    x.add_argument('range', nargs='?', default='')
    x.add_argument('--facts', action='store_true')
    sp.add_parser('demo', help='建一个示例期（服务端 + Android 示例仓库），体验出方案 → 拍板 → 开发 → 验收')
    x = sp.add_parser('steward', help='管家：kb steward 看待你处理的事和管家做了什么；kb steward tick 立即巡检一轮（明确的事会自动做）')
    x.add_argument('action', nargs='?', default='view', choices=['view', 'tick'])
    x = sp.add_parser('task', help='新建事项（不是产品需求的工作）：kb task <名称> [说明] [-k 类型] [-f 来源] [-p 高|中|低] [--bug 编号…]')
    x.add_argument('name')
    x.add_argument('summary', nargs='?', default='')
    x.add_argument('-k', '--kind', choices=TASK_KINDS)
    x.add_argument('-f', '--source', default='', help='来自哪里')
    x.add_argument('-p', '--priority', choices=TASK_PRI)
    x.add_argument('--bug', nargs='*', default=[])
    x = sp.add_parser('bug', aliases=['zentao'], help='Bug 平台：kb bug sources | test [-s 平台] | sync [--full] [-s 平台] | bugs [期名] [--pending] | get <编号> | assign <编号> <期名|待归纳|事项:名称> | platform <全部|安卓|iOS|服务端|前端> [-s 平台] | scope <我的|全部|指定人> [账号,账号] [-s 平台] | analyze <编号…>（后台只读分析）| fixed <编号> [提交…]（标记已修复）；kb zentao … 是旧名字')
    x.add_argument('action', choices=['sources', 'test', 'sync', 'bugs', 'get', 'bug', 'assign', 'platform', 'scope', 'analyze', 'fixed'])
    x.add_argument('args', nargs='*')
    x.add_argument('-s', '--source', default='', help='平台名称（Bug 页「平台」里的名字；空 = 第一个 / 全部）')
    x.add_argument('--full', action='store_true', help='忽略游标，重新扫一遍')
    x.add_argument('--pending', action='store_true', help='只看待归纳')
    x = sp.add_parser('anr', help='ANR / 崩溃：kb anr add <文件…> [-v 版本] [-t 事项] [-f 来源] | list [关键词] | show <编号> | mapping <版本> <mapping.txt> | task <编号> [事项名|＋新建] | version <编号> <版本> | ignore <编号> | unignore <编号>')
    x.add_argument('action', choices=['add', 'list', 'show', 'mapping', 'task', 'version', 'ignore', 'unignore'])
    x.add_argument('args', nargs='*')
    x.add_argument('-v', '--version', default='')
    x.add_argument('-t', '--task', default='')
    x.add_argument('-f', '--source', default='')
    x.add_argument('-n', '--count', type=int, default=1)
    sp.add_parser('serve', help='以 MCP（stdio）方式运行')
    x = sp.add_parser('web', help='启动网页（默认 http://127.0.0.1:11555 只给本机；部署在远端：kb web --host 0.0.0.0，要先 kb passwd）')
    x.add_argument('--port', type=int, default=None, help='端口（不给就按 KB_PORT > kb port set 的配置 > 11555；被占自动顺延）')
    x.add_argument('--host', default='', help='监听地址，默认 127.0.0.1（或环境变量 KB_HOST）')
    x = sp.add_parser('doctor', help='体检：规矩、管家设置、kb 工具放行、auto 权限模式、角色套件残留、MCP 接入，和团队默认对比；--fix [项,…] 一键修复')
    x.add_argument('--fix', nargs='?', const='all', default='', help='all（默认）或 rules,steward,allow,auto,mcp')
    sp.add_parser('passwd', help='设置 / 取消网页访问密码（部署在远端、或网页经反向代理开放时必须设）')
    x = sp.add_parser('token', help='远处的 Claude Code 连知识库用的访问令牌：kb token add <名称> | list | del <名称>')
    x.add_argument('op', choices=['add', 'list', 'del'])
    x.add_argument('name', nargs='?', default='')
    x.add_argument('--user', default='', help='令牌绑哪个账号（默认本机管理员）')
    x = sp.add_parser('user', help='账号：kb user add <用户名> [--admin] [--password …] | list | passwd <用户名>')
    x.add_argument('op', choices=['add', 'list', 'passwd'])
    x.add_argument('name', nargs='?', default='')
    x.add_argument('--admin', action='store_true', help='建成管理员')
    x.add_argument('--password', default='', help='不给就交互输入')
    x = sp.add_parser('migrate', help='升级迁移到数据库：kb migrate --dry-run 只列清单；kb migrate 备份 → 导入 → 校验，失败自动回滚')
    x.add_argument('--dry-run', action='store_true')
    x = sp.add_parser('backup', help='搬家：kb backup --for-move [--out 文件] [--passphrase 口令]（数据库 + 资料目录 + 机密主密钥）')
    x.add_argument('--for-move', action='store_true')
    x.add_argument('--out', default='')
    x.add_argument('--passphrase', default='')
    x = sp.add_parser('restore', help='从搬家归档恢复：kb restore <归档> [--passphrase 口令]，之后 kb doctor 校验')
    x.add_argument('archive')
    x.add_argument('--passphrase', default='')
    sp.add_parser('runner', help='执行机：kb runner --server <控制面地址> --token <注册令牌> [--name] [--data] [--ca] [--rebind] [--capacity N]',
                  add_help=False)
    x = sp.add_parser('port', help='网页端口：kb port（看）| kb port set <端口> | kb port reset（回到 11555）；重启 kb web 生效')
    x.add_argument('op', nargs='?', default='show', choices=['show', 'set', 'reset'])
    x.add_argument('port', nargs='?', default='')
    x = sp.add_parser('db', help='元数据库驱动：kb db status | use <连接串> | copy --to <连接串> [--force]（sqlite:// postgresql:// mysql://）')
    x.add_argument('op', choices=['status', 'use', 'copy'])
    x.add_argument('url', nargs='?', default='')
    x.add_argument('--to', default='', help='copy 的目标连接串')
    x.add_argument('--force', action='store_true', help='目标库已有数据也覆盖')
    x = sp.add_parser('runner-reg-token', help='生成执行机注册令牌（同网页「🖥 执行机 → 生成注册令牌」，24 小时内用一次）：kb runner-reg-token [名称]')
    x.add_argument('name', nargs='?', default='')
    sp.add_parser('dev-cluster', help='本机验证多执行机：kb dev-cluster up [--runners N] [--docker] | down [--purge] | status | chaos <kill-runner|kill-control|net-drop|disk-full>',
                  add_help=False)
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv and argv[0] == 'runner':                # 执行机：独立 KB_ROOT，重新 exec（REQ-011）
        from kblib.runner import launch
        return launch(argv[1:])
    if argv and argv[0] == 'dev-cluster':
        from kblib import devcluster
        return devcluster.main(argv[1:])
    a = ap.parse_args(argv)
    if a.cmd == 'user':
        return _user_cmd(a)
    if a.cmd == 'port':
        return _port_cmd(a)
    if a.cmd == 'db':
        return _db_cmd(a)
    if a.cmd == 'runner-reg-token':                 # 容器 / 脚本里接执行机用（REQ-011 REQ-034）
        from kblib.infra.deploy import runner_reg_token
        print(runner_reg_token(a.name, '终端')[0])
        return 0
    if a.cmd == 'migrate':
        from kblib.core.migrate import migrate
        try:
            print(migrate(dry_run=a.dry_run))
        except KBError as x:
            print(f'✗ {x}', file=sys.stderr)
            return 1
        return 0
    if a.cmd in ('backup', 'restore'):
        return _move_cmd(a)
    if a.cmd == 'serve':
        return serve()
    if a.cmd == 'web':
        import web
        return web.main(a.port, a.host)
    if a.cmd == 'doctor':
        from kblib.steward.doctor import doctor, doctor_fix, doctor_text
        try:
            if a.fix:
                print('\n'.join(doctor_fix(a.fix)) + '\n')
            items = doctor()
        except KBError as x:
            print(x, file=sys.stderr)
            return 1
        print(doctor_text(items))
        return _doctor_migrate()
    if a.cmd == 'passwd':
        import getpass
        from kblib.infra.deploy import set_password
        p1 = getpass.getpass('新的网页访问密码（至少 8 位；直接回车 = 取消密码）：')
        if p1 and getpass.getpass('再输一次：') != p1:
            print('两次不一样，没有改')
            return 1
        print(set_password(p1))
        return 0
    if a.cmd == 'token':
        from kblib.infra.deploy import token_add, token_del, token_list
        if a.op == 'add':
            t = token_add(a.name, a.user or None)
            print(f'令牌「{a.name}」（只显示这一次，丢了就删掉重建）：\n{t}\n\n在要连知识库的电脑上：\n'
                  f'  claude mcp add --transport http kb <网页地址>/mcp --header "Authorization: Bearer {t}"')
        elif a.op == 'list':
            print('\n'.join(f'- {x["名称"]}（{x["时间"]} 创建，最近使用 {x["最近使用"] or "无"}）' for x in token_list()) or '还没有令牌')
        else:
            print(token_del(a.name))
        return 0
    try:
        if a.cmd == 'init':
            init()
            r = f'已初始化：{ROOT}'
        elif a.cmd in (None, 'list'):
            r = overview()
        elif a.cmd == 'new':
            r = create_period(a.name, a.summary)
        elif a.cmd == 'show':
            r = period_detail(a.name)
        elif a.cmd == 'add':
            r = '\n\n'.join(add_material(a.name, f, a.category, a.note) for f in a.files)
        elif a.cmd == 'note':
            r = (update_note(a.name, a.rm, delete=True) if a.rm else
                 add_note(a.name, a.content, a.category, a.kind, a.title, a.url, a.source))
        elif a.cmd == 'inbox':
            fs = [p for p in INBOX.iterdir() if not p.name.startswith('.')]
            r = '\n\n'.join(add_material(a.name, f) for f in fs) if fs else '收件箱是空的'
        elif a.cmd == 'search':
            r = search(' '.join(a.query), a.period, None, a.scope, None, a.limit)
        elif a.cmd == 'repo':
            r = add_repo(a.path, a.repo_name, a.post)
        elif a.cmd == 'link':
            r = link_repo(a.name, a.repo)
        elif a.cmd == 'index':
            r = reindex(a.target)
        elif a.cmd == 'approve':
            r = approve(a.name)
        elif a.cmd == 'login':
            host = re.sub(r'^https?://', '', a.host).split('/')[0]
            what = 'Figma 个人访问令牌' if 'figma' in host else f'{a.user}@{host} 的密码'
            from kblib.infra import vault
            print(f'请输入{what}（' + ('由 macOS 钥匙串接收，知识库和 AI 看不到明文' if vault.BACKEND == 'keychain' else '存进加密文件，不回显') + '）')
            _fetch().save_cred(host, a.user)
            r = f'已保存到{vault.LABEL}（服务名 kb:{host}）'
        elif a.cmd == 'fetch':
            r = fetch_url(a.url)
        elif a.cmd == 'verify':
            r = verify_source(a.name, a.sid, a.status, a.note)
        elif a.cmd == 'fixlink':
            r = fix_link(a.name, a.old, a.new, a.reason, by='终端')
        elif a.cmd == 'refresh':
            if a.all or not a.name:
                ch = refresh_all(by='终端')
                r = '\n'.join(f'{n}：\n' + '\n'.join(h) for n, h in ch) or '所有未完成的期，来源都没有变化'
            else:
                r = refresh_sources(a.name, a.sid, by='终端')
        elif a.cmd == 'status':
            if a.status == '已批准开发':
                raise KBError('批准开发请用：kb approve <期名>')
            r = set_status(a.name, a.status, a.note, by_human=True)
        elif a.cmd == 'log':
            r = add_log(a.name, a.note)
        elif a.cmd == 'task':
            r = create_task(a.name, a.summary, a.kind or '', a.source, a.priority or '', a.bug, by='终端')
        elif a.cmd == 'anr':
            g = lambda i: a.args[i] if len(a.args) > i else ''
            if a.action == 'add':
                r = anr_add(a.args, a.version, a.source, a.task, a.count, by='终端')
            elif a.action == 'list':
                r = anr_list(g(0), a.task, include_ignored=True)
            elif a.action == 'show':
                r = anr_show(g(0))
            elif a.action == 'mapping':
                r = anr_mapping(g(0), g(1), by='终端')
            elif a.action == 'version':
                r = anr_set_version(g(0), g(1), by='终端')
            elif a.action == 'task':
                r = anr_link(g(0), g(1) or '＋新建', by='终端')
            else:
                r = anr_link(g(0), status='忽略' if a.action == 'ignore' else '', by='终端')
        elif a.cmd in ('bug', 'zentao'):
            g = lambda i: a.args[i] if len(a.args) > i else ''
            if a.action == 'sources':
                r = '\n'.join(f'- {c["ID"]}（{c["适配器"]}，{c["类型"]}）{"" if c["启用"] else "【已停用】"} 账号 {c.get("账号条目") or "自动找"}　范围 {c["范围"]}　平台 {c["平台"]}'
                               for c in bug_sources()) or '还没接入 Bug 平台'
            elif a.action == 'test':
                r = bug_test(a.source)
            elif a.action == 'sync':
                r = bug_sync(by='终端', full=a.full, only=a.source)
            elif a.action == 'bugs':
                r = bug_list(g(0), '待归纳' if a.pending else '')
            elif a.action in ('bug', 'get'):
                r = bug_get(g(0))
            elif a.action == 'assign':
                r = bug_assign(g(0), g(1), by='终端')
            elif a.action == 'fixed':
                r = bug_fixed(g(0), True, a.args[1:], by='终端')
            elif a.action == 'analyze':
                r = bug_analyze(a.args, by='终端')
            elif a.action == 'scope':
                save_zentao_settings(scope=g(0), people=g(1) or None, source=a.source)
                r = f'已改范围：{g(0)}'
            else:
                save_zentao_settings(platform=g(0), source=a.source)
                r = f'已改平台：{g(0)}'
        elif a.cmd == 'demo':
            r = demo_create('终端')
        elif a.cmd == 'steward':
            r = steward_tick('终端') if a.action == 'tick' else steward_text()
        elif a.cmd == 'stats':
            r = json.dumps(stats_facts(), ensure_ascii=False) if a.facts else stats_text(a.range)
        elif a.cmd == 'plugin':
            if a.action == 'run':
                if not a.plugin or not a.name:
                    raise KBError('用法：kb plugin run <插件> <期名或分支> [仓库路径] [--push]')
                r = plugin_run_sync(a.plugin, a.name, '' if a.event in ('', '开发开始', '开发完成', '手动运行') else a.event, push=a.push)
            elif a.action == 'call':
                r = call_plugin(a.plugin, [x for x in [a.name, a.event] + a.rest if x], by='终端')
            else:
                r = '\n'.join(f'{p["id"]}：{p.get("名称", "")} · ' + (p.get('错误') or (f'已挂载（{"、".join(timing_get(t)["名称"] for t in p["时机"]) or "按需调用"}）' if p['启用'] else '未挂载'))
                              + f' · {p["目录"]}' for p in plugins()) or f'没有插件（放到 {PLUGIN_DIR}/<名称>/plugin.json）'
        print(r)
    except KBError as e:
        print(f'✗ {e}', file=sys.stderr)
        return 1
