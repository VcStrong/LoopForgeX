"""会话私有 HOME（REQ-017 REQ-039）：<执行机数据目录>/sessions/<任务>/home，权限 0700。

只放这个会话用的东西：
  引擎账号登录态  task.引擎账号.登录态 = {文件: {相对 HOME 的路径: 内容}, 环境: {变量: 值}}，或 {apiKey: …}，
                  或直接 {".claude/.credentials.json": 内容}（以 . 开头的键当文件）；apiKey 按引擎写 ANTHROPIC_API_KEY / OPENAI_API_KEY
  git 凭据        控制面 vault.session_creds：task.凭据 = {git: {方式: 个人 | bot | 缺, 用户名, 密钥, 作者, coauthor, 缺}}
                  个人 / bot：凭据助手文件 .kb-git-cred（0600）回答任何主机；密钥是 ssh 私钥时写 .ssh/id_kb；
                  bot：commit-msg 钩子加 Co-authored-by 发起人；缺：pre-push 钩子拒绝推送并写明缺什么（不退回系统用户身份）。
                  钩子放在 HOME/.githooks（core.hooksPath），先跑仓库自己的同名钩子（Gerrit Change-Id 不受影响）。
                  也兼容 {git: [{url, 用户名, 密码}], ssh私钥, 作者: {name, email}, 环境}
结束（正常、异常、取消）删除整个 sessions/<任务>；登录态文件在会话里被刷新过的，结束前读出来回写控制面。
执行机启动时清理残留的会话目录（上报一条事件）。
"""
import hashlib
import json
import os
import shutil
from pathlib import Path
from urllib.parse import quote, urlparse


def _write(p, content, mode=0o600):
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = content if isinstance(content, (bytes, str)) else json.dumps(content, ensure_ascii=False)
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, 'wb') as f:
        f.write(data.encode() if isinstance(data, str) else data)
    os.chmod(p, mode)


def _h(p):
    try:
        return hashlib.sha256(Path(p).read_bytes()).hexdigest()
    except OSError:
        return ''


class SessionHome:
    def __init__(self, data, task_id):
        self.root = Path(data) / 'sessions' / str(task_id)
        self.home = self.root / 'home'
        self._env = {}
        self._login = {}        # 相对路径 → 写入时的哈希

    def create(self):
        (Path(self.root).parent).mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.mkdir(exist_ok=True, mode=0o700)
        self.home.mkdir(exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        os.chmod(self.home, 0o700)
        return self

    def _inside(self, rel):
        p = (self.home / rel).resolve()
        if self.home.resolve() not in p.parents:
            raise ValueError(f'登录态文件路径不能跳出会话 HOME：{rel}')
        return p

    def write_login(self, blob, engine=''):
        """引擎账号登录态：只拷入这个账号的。"""
        blob = blob or {}
        self._blob = blob
        files = dict(blob.get('文件') or blob.get('files') or {})
        files.update({k: v for k, v in blob.items() if isinstance(k, str) and k.startswith('.')})
        key = blob.get('apiKey') or blob.get('API Key') or blob.get('key')
        if key:
            self._env['OPENAI_API_KEY' if 'codex' in (engine or '').lower() else 'ANTHROPIC_API_KEY'] = str(key)
        for rel, content in files.items():
            p = self._inside(rel)
            _write(p, content)
            self._login[rel] = _h(p)
        self._env.update({k: str(v) for k, v in (blob.get('环境') or blob.get('env') or {}).items()})

    def write_git(self, creds):
        creds = creds or {}
        if isinstance(creds.get('git'), dict):
            return self._write_git_session(creds['git'])
        lines = []
        for c in creds.get('git') or []:
            u = urlparse(c.get('url') or '')
            if not u.scheme or not u.hostname:
                continue
            user, pw = quote(c.get('用户名') or '', safe=''), quote(c.get('密码') or c.get('令牌') or '', safe='')
            lines.append(f'{u.scheme}://{user}:{pw}@{u.hostname}' + (f':{u.port}' if u.port else ''))
        if lines:
            _write(self.home / '.git-credentials', '\n'.join(lines) + '\n')
        a = creds.get('作者') or {}
        cfg = ['[credential]', '\thelper = store'] if lines else []
        if a.get('name') or a.get('email'):
            cfg += ['[user]'] + ([f'\tname = {a["name"]}'] if a.get('name') else []) + ([f'\temail = {a["email"]}'] if a.get('email') else [])
        if cfg:
            _write(self.home / '.gitconfig', '\n'.join(cfg) + '\n', 0o600)
        if creds.get('ssh私钥'):
            key = self.home / '.ssh' / 'id_kb'
            _write(key, creds['ssh私钥'].rstrip() + '\n')
            self._env['GIT_SSH_COMMAND'] = f'ssh -i {key} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o BatchMode=yes'
        self._env.update({k: str(v) for k, v in (creds.get('环境') or {}).items()})

    def _hooks(self, commit_msg='', pre_push=''):
        d = self.home / '.githooks'
        chain = 'h="$(git rev-parse --git-common-dir)/hooks/$(basename "$0")"; [ -x "$h" ] && { "$h" "$@" || exit $?; }\n'
        if commit_msg:
            _write(d / 'commit-msg', '#!/bin/sh\n' + chain + commit_msg, 0o700)
        if pre_push:
            _write(d / 'pre-push', '#!/bin/sh\n' + chain + pre_push, 0o700)
        return d

    def _write_git_session(self, g):
        """控制面下发的会话凭据（REQ-017）。"""
        how, user, key = g.get('方式', ''), g.get('用户名', ''), g.get('密钥', '')
        cfg = []
        if how in ('个人', 'bot') and user and key:
            if '-----BEGIN' in key:
                k = self.home / '.ssh' / 'id_kb'
                _write(k, key.rstrip() + '\n')
                self._env['GIT_SSH_COMMAND'] = f'ssh -i {k} -l {user} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o BatchMode=yes'
            else:
                _write(self.home / '.kb-git-cred', f'username={user}\npassword={key}\n')
                cfg += ['[credential]', '\thelper = "!f() { cat \\"$HOME/.kb-git-cred\\"; }; f"']
        name = g.get('作者') or ''
        if how == '个人' and name:
            cfg += ['[user]', f'\tname = {name}'] + ([f'\temail = {g["邮箱"]}'] if g.get('邮箱') else [])
        hooks = None
        if how == 'bot' and g.get('coauthor'):
            co = g['coauthor'].replace('"', '')
            hooks = self._hooks(commit_msg=f'git interpret-trailers --in-place --if-exists addIfDifferent --trailer "Co-authored-by: {co}" "$1"\n')
            self._env['KB_COAUTHOR'] = co
        if how == '缺' or not key:
            why = (g.get('缺') or '没有推送凭据').replace("'", '')
            hooks = self._hooks(pre_push=f"echo '知识库：不能推送——{why}（不会用执行机系统用户的身份推送）' >&2\nexit 1\n")
            self._env['KB_GIT_MISSING'] = why
        if hooks:
            cfg += ['[core]', f'\thooksPath = {hooks}']
        if cfg:
            _write(self.home / '.gitconfig', '\n'.join(cfg) + '\n')

    def env(self):
        """会话进程的环境变量：HOME 指向私有目录（~/.claude、~/.codex、git 凭据都在这里）。"""
        return {'HOME': str(self.home), 'XDG_CONFIG_HOME': str(self.home / '.config'), 'GIT_TERMINAL_PROMPT': '0', **self._env}

    def changed_blob(self):
        """登录态被刷新过时，返回更新后的完整登录态（原格式，文件内容换成新的）；没变返回 None。"""
        ch = self.changed_login()
        if not ch:
            return None
        b = dict(getattr(self, '_blob', {}) or {})
        if 'files' in b or '文件' in b:
            k = '文件' if '文件' in b else 'files'
            b[k] = {**b[k], **ch}
        else:
            b.update(ch)
        return b

    def changed_login(self):
        """会话里被引擎刷新过的登录态文件：{相对路径: 内容}（结束时回写控制面）。"""
        out = {}
        for rel, h in self._login.items():
            p = self.home / rel
            nh = _h(p)
            if nh and nh != h:
                out[rel] = p.read_text('utf-8', 'replace')
        return out

    def destroy(self):
        shutil.rmtree(self.root, ignore_errors=True)
        return not self.root.exists()


def cleanup_leftovers(data, active=()):
    """执行机重启后：删掉不属于在跑会话的残留目录 → 删掉的任务编号列表。"""
    base = Path(data) / 'sessions'
    if not base.is_dir():
        return []
    gone = []
    for d in base.iterdir():
        if d.is_dir() and d.name not in set(active):
            shutil.rmtree(d, ignore_errors=True)
            gone.append(d.name)
    return gone
