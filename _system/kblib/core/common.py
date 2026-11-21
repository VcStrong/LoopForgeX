# From 大大王.DT
"""路径、错误类型、小工具、设置文件：所有模块共用的最底层。"""
import html
import json
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from kblib import lazy
import contextvars

# 会话身份（KB_SESSION / KB_PERIOD）：本机后台会话从环境变量来；远程会话经网页的 /mcp 调用时按请求设置，不改进程环境
_SESS_ENV = contextvars.ContextVar('kb_sess_env', default=None)


def sess_env(k, default=''):
    o = _SESS_ENV.get()
    if o is not None and k in o:
        return o[k]
    return os.environ.get(k, default)


def with_sess_env(env, fn, *a, **kw):
    tok = _SESS_ENV.set(dict(env))
    try:
        return fn(*a, **kw)
    finally:
        _SESS_ENV.reset(tok)
_flows = lazy('kblib.ai.flows')

# 当前用户上下文（REQ-003 REQ-005 REQ-022）：网页请求、/mcp、执行机回调、管家各自设置；身份解析集中在 webapp/server.py 的 _identity。
#   {'user': 用户名, 'project': 当前项目, 'session': 会话 / 任务编号, 'via': 网页 | AI | 管家 | 终端 | 执行机, 'engine': 引擎名}
_USER_CTX = contextvars.ContextVar('kb_user_ctx', default=None)


def current_ctx():
    return _USER_CTX.get() or {}


def current_user():
    return current_ctx().get('user', '')


def current_project():
    return current_ctx().get('project', '')


def with_user(ctx, fn, *a, **kw):
    tok = _USER_CTX.set(dict(ctx or {}))
    try:
        return fn(*a, **kw)
    finally:
        _USER_CTX.reset(tok)


def set_user_ctx(ctx):
    """线程入口（请求处理器）直接设置；返回 token 供 reset。"""
    return _USER_CTX.set(dict(ctx or {}))


def actor_label(ctx=None):
    """记录里的操作人（REQ-004）：网页「甲（网页）」、AI「AI·claude（发起人 甲）」、管家「管家」。没有上下文返回空串。"""
    c = ctx if ctx is not None else current_ctx()
    via, u = c.get('via', ''), c.get('user', '')
    if via == '管家':
        return '管家'
    if via == 'AI':
        return f'AI·{c.get("engine") or "引擎"}（发起人 {u or "未知"}）'
    if via == '执行机':
        return f'执行机 {c.get("runner", "")}'.strip()
    if u:
        return f'{u}（{via or "网页"}）'
    return ''
      # 上层模块：用到时才 import（不形成循环）


#!/usr/bin/env python3
# From 大大王.DT
"""本地知识库：按「期」管理需求 / 设计图 / 接口文档 / 其他资料，索引本地代码仓库。

同一份代码提供两种入口：
  - 命令行：kb new / add / search / approve ...（给人用）
  - MCP（stdio）：kb serve（给 Claude Code 用）

开发闸门：一期的状态只有人能改成「已批准开发」（kb approve，必须在真实终端里敲）；
AI 只有在「已批准开发 / 开发中」时才能通过 kb_dev_package 拿到开发包。
"""


ROOT = Path(os.environ.get('KB_ROOT') or Path(__file__).resolve().parents[3])    # kblib/core/common.py → kblib → _system → 知识库根
SYS = ROOT / '_system'          # 数据目录里的 _system（索引、日志、管家记录、临时文件）
PROG = Path(__file__).resolve().parents[2]    # 程序目录（_system：kb.py、ui/、bin/、.venv）；KB_ROOT 指向别处时和 SYS 不同
PERIODS = ROOT / '期'
INBOX = ROOT / '收件箱'
REPOS_FILE = ROOT / '代码仓库.json'
BASE = ROOT / '基础'
DB = SYS / 'index.db'

CATS = ['需求', '设计图', '接口文档', '其他资料']
STATUSES = ['收集中', '已批准开发', '开发中', '已完成', '已暂停']
DEV_OK = ('已批准开发', '开发中')
HIST = '_历史版本'
PROTO_DIR = '原型图'   # 需求/原型图：PRD 里的原型图、参考图，只用于理解交互；UI 以「设计图」里的设计稿为准
DESIGN_HOSTS = ('figma.com', 'lanhuapp.com', 'lanhu.com')
NOTE_SUFFIX = '.说明.md'

IMG_EXT = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.heic', '.tif', '.tiff'}
TEXT_EXT = {'.md', '.txt', '.json', '.yaml', '.yml', '.csv', '.tsv', '.sql', '.xml', '.proto',
            '.log', '.ini', '.properties', '.svg', '.har'}
OFFICE_EXT = {'.docx', '.doc', '.rtf', '.odt', '.webarchive'}
DOC_EXT = TEXT_EXT | OFFICE_EXT | IMG_EXT | {'.html', '.htm', '.pdf', '.xlsx', '.pptx'}
CODE_EXT = {'.java', '.kt', '.kts', '.gradle', '.xml', '.json', '.md', '.py', '.js', '.ts', '.tsx',
            '.jsx', '.vue', '.swift', '.m', '.mm', '.h', '.c', '.cc', '.cpp', '.hpp', '.go', '.rs',
            '.proto', '.sql', '.yaml', '.yml', '.properties', '.sh', '.dart', '.cmake', '.toml',
            '.html', '.css', '.scss', '.aidl', '.pro', '.cfg', '.txt'}
SKIP_DIRS = {'.git', 'build', '.gradle', '.idea', 'node_modules', 'dist', 'out', 'Pods', '.cxx',
             'intermediates', '.venv', 'venv', '__pycache__', 'target', '.next', 'DerivedData',
             '.dart_tool', 'generated', '.externalNativeBuild'}
MAX_CODE_BYTES = 400_000
CHUNK_CHARS = 1500


class KBError(Exception):
    pass


class KBNeed(KBError):
    """需要用户在网页上选一下（开发仓库、分支）：data 交给网页弹窗，文字说明给 AI / 命令行。"""
    def __init__(self, msg, data):
        super().__init__(msg)
        self.data = data


def uuid_hex():
    return uuid.uuid4().hex


def now():
    return datetime.now().strftime('%Y-%m-%d %H:%M')


def safe_name(s):
    s = re.sub(r'[\\/:*?"<>|\r\n\t]+', '-', (s or '').strip()).strip('.- ')
    if not s:
        raise KBError('名称不能为空')
    return s


def read_text(p: Path):
    b = p.read_bytes()
    for enc in ('utf-8', 'gb18030'):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            pass
    return b.decode('utf-8', 'replace')


def html_text(s):
    s = re.sub(r'(?is)<(script|style|noscript)\b.*?</\1>', ' ', s)
    s = re.sub(r'(?i)<br\s*/?>|</(p|div|tr|li|h[1-6]|table|section)>', '\n', s)
    s = html.unescape(re.sub(r'<[^>]+>', ' ', s))
    s = re.sub(r'[ \t ]+', ' ', s)
    return re.sub(r'\n\s*\n+', '\n\n', s).strip()


# 设置文件：网页「⚙ 设置」和各功能的开关都存这里
SETTINGS_FILE = ROOT / '设置.json'


# 团队默认：进 git 的程序文件（_system/团队默认.json），同事 git pull / 新装就带过去。
# 本机 设置.json 没有的项用它补：管家规矩按编号合并（本机删过的记在「已删团队规矩」，不再补回），管家设置缺的键用团队值。
# 网页设置「管家规矩」的「⬆ 设为团队默认」把本机规矩和管家设置写回这个文件（kblib.steward.doctor.save_team_defaults）。
TEAM_FILE = PROG / '团队默认.json'
TEAM_STEWARD_KEYS = ('开启', '自主度', '每日上限', '新Bug天数', '中断续跑', '续跑上限', '学习', '自动拍板')


def team_defaults():
    try:
        t = json.loads(read_text(TEAM_FILE)) if TEAM_FILE.exists() else {}
    except (OSError, ValueError):
        t = {}
    return t if isinstance(t, dict) else {}


def _merge_team(s, t):
    rules = [r for r in s.get('管家规矩') or [] if isinstance(r, dict)]
    gone = set(s.get('已删团队规矩') or [])
    have = {r.get('编号'): r for r in rules}
    for r in t.get('管家规矩') or []:
        if not isinstance(r, dict) or not r.get('编号') or not r.get('内容') or r['编号'] in gone:
            continue
        mine = have.get(r['编号'])
        if mine is None:
            rules.append(dict(r, 来自='团队默认'))
        elif mine.get('来自') == '团队默认' and mine.get('内容') != r['内容']:      # 团队默认里改了措辞：跟着改（本机自己记的同编号不动）
            mine['内容'] = r['内容']
    if rules or '管家规矩' in s:
        s['管家规矩'] = rules
    tm = {k: v for k, v in (t.get('管家') or {}).items() if k in TEAM_STEWARD_KEYS}
    if tm:
        mine = s.get('管家') if isinstance(s.get('管家'), dict) else {}
        s['管家'] = dict(tm, **mine)
    return s


class Snap(dict):
    """带「读出时快照」的字典：保存时只把改过的顶层键合并进库里的最新值，两个请求同时改不同字段都保留（REQ-008）。"""
    __slots__ = ('_base',)


def snap(d):
    o = Snap(d)
    o._base = json.loads(json.dumps(d, ensure_ascii=False, default=str))
    return o


def snap_diff(d):
    """(改过或新增的键, 删掉的键)。不是 Snap 的字典当作整份替换。"""
    base = getattr(d, '_base', None)
    if base is None:
        return list(d), None
    cur = json.loads(json.dumps(dict(d), ensure_ascii=False, default=str))
    changed = [k for k in cur if k not in base or base[k] != cur[k]]
    removed = [k for k in base if k not in cur]
    return changed, removed


def _settings_raw():
    """设置存在数据库（kv「设置」）；设置.json 是导出视图。有人直接改了文件（比库里新）就以文件为准导入一次。"""
    from kblib.core import db
    s, ver = db.kv_get('设置', None)
    try:
        fm = SETTINGS_FILE.stat().st_mtime if SETTINGS_FILE.exists() else 0
    except OSError:
        fm = 0
    if s is None or (fm and fm > _settings_exported.get(str(SETTINGS_FILE), 0) + 1 and fm > _kv_updated()):
        try:
            f = json.loads(read_text(SETTINGS_FILE)) if SETTINGS_FILE.exists() else {}
        except ValueError:
            f = {}
        if not isinstance(f, dict):
            f = {}
        if s is None or f != s:
            db.kv_set('设置', f, by='设置.json')
        _settings_exported[str(SETTINGS_FILE)] = fm
        s = f
    return s if isinstance(s, dict) else {}


_settings_exported = {}


def _kv_updated():
    from kblib.core import db
    r = db.q1('SELECT updated FROM kv WHERE k=?', ('设置',))
    return (r[0] or 0) if r else 0


def load_settings():
    s = _settings_raw()
    o = snap(s)
    _merge_team(o, team_defaults())
    o.setdefault('默认流程', _flows.DEFAULT_FLOW)
    o.setdefault('流程', {})       # id → 自定义流程，或对内置流程的修改
    return o


def save_settings(s):
    from kblib.core import db
    changed, removed = snap_diff(s)
    with db.tx():
        cur = _settings_raw() if removed is not None else {}
        out = dict(cur)
        for k in changed:
            out[k] = s[k]
        for k in removed or []:
            out.pop(k, None)
        db.kv_set('设置', out, by=current_user() or '')
    if isinstance(s, Snap):
        s._base = json.loads(json.dumps(dict(s), ensure_ascii=False, default=str))
    tmp = SETTINGS_FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps(out, ensure_ascii=False, indent=1), 'utf-8')
    tmp.replace(SETTINGS_FILE)
    try:
        _settings_exported[str(SETTINGS_FILE)] = SETTINGS_FILE.stat().st_mtime
    except OSError:
        pass
