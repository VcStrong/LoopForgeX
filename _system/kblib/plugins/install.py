"""安装到手机：把构建平台（Jenkins）上某次构建的 apk 下载到本机缓存，用 adb 装到手机（网页「构建」表、开发面板的手机卡片用）。

只做 Android（.apk；.aab、未签名的 unsigned 包不列）。iOS / 前端预览、本地 installDebug 不在这里。
- 渠道包：插件配置「渠道包路径前缀」过滤；默认渠道 = 本期上次选的（期信息「安装渠道」，去掉版本号后的路径）→「默认渠道匹配」正则 → 路径最短。
- 选机：本期占着的优先 → 空闲（最近最少用）；别的期 / 本人在用的不可直接装，force（网页二次确认）才装。
  网页安装直接调真正的 adb（不走 bin/adb 守卫），占用由这里自己检查；装成功记为本期占用（方式「申请」，沿用超时释放）。
- 下载按 任务+构建号+文件名 缓存在 _system/cache/apk/，保留最近 KEEP 个；同一个包第二次安装不重新下载。
- adb：版本降级自动加 -d 重试；签名不一致要网页确认「卸载会清空数据」后才卸载重装；失败原样给出 adb 输出，不改占用。
"""
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from kblib.core.common import SYS, KBError, KBNeed, now
from kblib.core.periods import load_meta, log, period_dir, save_meta
from kblib.infra import devlock
from kblib.ai import devices as _dev
from kblib.plugins.registry import _plugin_env
from kblib.plugins import builds as _builds
from kblib import lazy
_plan = lazy('kblib.stack.plan')         # 上层模块：认端（detect_ends）

CACHE_DIR = SYS / 'cache' / 'apk'
KEEP = 5                                  # 缓存保留最近几个包
DL_TIMEOUT = 30 * 60
ADB_TIMEOUT = 5 * 60
_JOBS = {}                                # 任务号 → 进度
_LOCK = threading.Lock()
_FILE_LOCKS = {}                          # 缓存文件 → Lock：同一个包同时只下载一次


# ---------------- 选包
def channel_key(path):
    """去掉版本号（95.0.0.21 这类）后的路径：每次构建版本不同，渠道是同一个。"""
    return re.sub(r'\d+(?:\.\d+){2,}', '*', path or '')


def installable(build):
    """这次构建能装的 apk（不含 .aab、unsigned）。"""
    return [a for a in build.get('产物') or []
            if a['路径'].lower().endswith('.apk') and 'unsigned' not in a['路径'].lower()]


def apk_choices(build, prefix='', rx='', last=''):
    """→ (候选 apk 列表, 默认的路径, 默认依据)。前缀一个都对不上时不过滤（免得配错了一个都装不了）。"""
    apks = installable(build)
    if prefix:
        apks = [a for a in apks if a['路径'].startswith(prefix)] or apks
    apks = sorted(apks, key=lambda a: a['路径'])
    if not apks:
        return [], '', ''
    if last:
        hit = next((a for a in apks if channel_key(a['路径']) == last), None)
        if hit:
            return apks, hit['路径'], '本期上次选的'
    if rx:
        try:
            r = re.compile(rx, re.I)
        except re.error:
            r = None
        hits = [a for a in apks if r and r.search(a['路径'])]
        if hits:
            return apks, min(hits, key=lambda a: (len(a['路径']), a['路径']))['路径'], f'匹配「{rx}」'
    return apks, min(apks, key=lambda a: (len(a['路径']), a['路径']))['路径'], '路径最短'


def _conf():
    p = _builds.build_plugin()
    c = p.get('配置') or {}
    return p, (c.get('渠道包路径前缀') or '').strip(), (c.get('默认渠道匹配') or '').strip()


def period_android(name):
    """这一期是不是 Android 端：认得出端（开发仓库 / 全栈各端）且没有 Android → 否；认不出 → 看构建里有没有 apk（由调用方判断）。"""
    d = period_dir(name)
    m = load_meta(d)
    ends = set((m.get('各端') or {}).keys())
    if m.get('开发仓库'):
        try:
            ends |= set(_plan.detect_ends(m['开发仓库']))
        except (KBError, OSError):
            pass
    return None if not ends else 'Android' in ends


def _find_build(name, url=''):
    """本期构建里找一次构建（url 空 = 最近一次成功且有 apk 的）→ (任务, 构建)。"""
    r = _builds.period_builds(name)
    for j in r['任务']:
        for b in j.get('构建') or []:
            if url and b['地址'].rstrip('/') == url.rstrip('/'):
                return j, b
            if not url and b.get('结果') == 'SUCCESS' and not b.get('进行中') and installable(b):
                return j, b
    raise KBError('本期构建里没找到这次构建（刷新一下构建表？）' if url else '本期还没有成功且带 apk 的构建')


# ---------------- 选机
def _phones(name):
    """手机候选：可装的排前面（本期占着的 → 空闲按最近最少用），不可装的写原因。"""
    last = devlock.recent()
    out = []
    for x in _dev.devices(name):
        c = x['占用']
        if x['状态'] != '在线':
            ok, why, rank = False, x['状态'], 9
        elif x['本期']:
            ok, why, rank = True, '本期占用', 0
        elif x['空闲']:
            ok, why, rank = True, '空闲', 1
        else:
            ok, why, rank = False, _dev._who(c), 5
        out.append({'序列号': x['序列号'], '型号': x['型号'], '可装': ok, '说明': why, '可强制': x['状态'] == '在线' and not ok,
                    '_k': (rank, float(last.get(x['序列号']) or 0))})
    out.sort(key=lambda p: p['_k'])
    for p in out:
        p.pop('_k')
    return out


def install_options(name, build_url=''):
    """「安装到手机」弹框：这次构建的渠道包（含默认）和手机。build_url 空 = 本期最近一次成功的构建。"""
    if not _dev._adb():
        raise KBError('本机找不到 adb：装好 Android SDK platform-tools 后再试')
    d = period_dir(name)
    if period_android(d.name) is False:
        raise KBError('这一期不是 Android 端，不提供安装到手机')
    p, prefix, rx = _conf()
    j, b = _find_build(d.name, build_url)
    apks, dft, why = apk_choices(b, prefix, rx, load_meta(d).get('安装渠道', ''))
    if not apks:
        raise KBError('这次构建没有能装的 apk（.aab 和未签名包不能直接装）')
    cached = {f.name for f in CACHE_DIR.glob('*.apk')} if CACHE_DIR.is_dir() else set()
    phones = _phones(d.name)
    return {'任务': j['名称'], '构建': {'编号': b['编号'], '地址': b['地址'], '版本': b.get('版本', ''), '时间': b.get('时间', '')},
            '包': [{'名称': a['名称'], '路径': a['路径'], '已缓存': _cache_name(j['名称'], b['编号'], a['路径']) in cached} for a in apks],
            '默认': dft, '默认依据': why, '前缀': prefix, '匹配': rx,
            '手机': phones, '默认手机': next((x['序列号'] for x in phones if x['可装']), '')}


def install_latest(name):
    """手机卡片「安装最新构建」要不要显示：Android 期（或认不出端）且有成功的 apk 构建。"""
    try:
        if not _builds.build_plugin(False) or period_android(name) is False or not _dev._adb():
            return {'可装': False}
        j, b = _find_build(name)
        return {'可装': True, '任务': j['名称'], '编号': b['编号'], '地址': b['地址'], '版本': b.get('版本', '')}
    except KBError as e:
        return {'可装': False, '原因': str(e)}


# ---------------- 下载缓存
def _cache_name(job, num, path):
    return re.sub(r'[^\w.()-]+', '_', f'{job}-{num}-{Path(path).name}')


def _prune(keep=KEEP):
    fs = sorted(CACHE_DIR.glob('*.apk'), key=lambda f: f.stat().st_mtime, reverse=True)
    for f in fs[keep:]:
        try:
            f.unlink()
        except OSError:
            pass


def _download(p, url, dest, job):
    """缓存里有就直接用（touch 一下算最近用过）；没有就让插件下载，按 .part 大小报进度。"""
    with _LOCK:
        lk = _FILE_LOCKS.setdefault(str(dest), threading.Lock())
    with lk:
        if dest.is_file() and dest.stat().st_size > 0:
            os.utime(dest)
            job.update(进度=100, 缓存=True)
            return
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        part, size = Path(str(dest) + '.part'), Path(str(dest) + '.size')
        env = _plugin_env(p, '安装到手机', KB_EVENT='调用')
        cmd = [sys.executable if x == '{python}' else x for x in p['命令']] + ['download', url, str(dest)]
        pr = subprocess.Popen(cmd, cwd=p['目录'], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        t0 = time.time()
        while pr.poll() is None:
            if time.time() - t0 > DL_TIMEOUT:
                pr.kill()
                raise KBError(f'下载超时（{DL_TIMEOUT // 60} 分钟）')
            try:
                tot = int(size.read_text() or 0)
                got = part.stat().st_size
                job.update(已下=got, 总=tot, 进度=int(100 * got / tot) if tot else 0)
            except (OSError, ValueError):
                pass
            time.sleep(0.5)
        out, err = pr.communicate()
        for f in (part, size):
            if pr.returncode and f.exists():
                f.unlink()
        if pr.returncode or not dest.is_file():
            raise KBError((err or out or '').strip()[-1500:] or f'下载失败（退出码 {pr.returncode}）')
        job.update(进度=100, 已下=dest.stat().st_size)
    _prune()


# ---------------- adb
def _adb_run(args, timeout=ADB_TIMEOUT):
    try:
        r = subprocess.run([_dev._adb(), *args], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 1, f'adb {" ".join(args[:3])} 超时（{timeout} 秒）'
    out = (r.stdout + ('\n' + r.stderr if r.stderr.strip() else '')).strip()
    return (0 if r.returncode == 0 and 'Success' in out and 'Failure' not in out else (r.returncode or 1)), out


def adb_failure(out):
    """adb install 的输出 → 失败类型：降级 / 签名冲突 / 其他（''）。"""
    if 'INSTALL_FAILED_VERSION_DOWNGRADE' in out:
        return '降级'
    if 'INSTALL_FAILED_UPDATE_INCOMPATIBLE' in out or re.search(r'signatures do not match', out, re.I):
        return '签名冲突'
    return ''


def adb_package(out, apk=None):
    """签名冲突时要卸载的包名：先从 adb 输出里取，取不到用 aapt 读 apk。"""
    m = re.search(r'(?:Existing package|Package|package) ([A-Za-z_][\w]*(?:\.[\w]+)+) signatures', out)
    if m:
        return m.group(1)
    adb = _dev._adb()
    if apk and adb:
        sdk = Path(adb).resolve().parent.parent
        for tool in sorted(sdk.glob('build-tools/*/aapt'), reverse=True)[:1]:
            try:
                r = subprocess.run([str(tool), 'dump', 'badging', str(apk)], capture_output=True, text=True, timeout=60)
                m = re.search(r"package: name='([^']+)'", r.stdout)
                if m:
                    return m.group(1)
            except (OSError, subprocess.TimeoutExpired):
                pass
    return ''


def _holder_block(name, sn):
    """这台手机现在不能直接装的原因（别的期 / 本人在用）；能装返回空。"""
    c = devlock.holders().get(sn)
    if not c or (c.get('期') == name and c.get('方式') != '本人'):
        return ''
    return _dev._who(dict(c, 最后使用=devlock.stamp(c.get('最后使用'))))


def install_start(name, build_url, path, serial, force=False, uninstall=False, by='用户（网页）'):
    """开始安装（后台线程，网页用 install_job 轮询）。别的期 / 本人在用的手机没 force 时返回 409 让网页二次确认。"""
    d = period_dir(name)
    p, _, _ = _conf()
    j, b = _find_build(d.name, build_url)
    a = next((x for x in installable(b) if x['路径'] == path), None)
    if not a:
        raise KBError(f'这次构建里没有这个 apk：{path}')
    if not serial or serial not in {x['序列号'] for x in _dev.devices(d.name) if x['状态'] == '在线'}:
        raise KBError(f'手机 {serial or "（没选）"} 没连着或不在线')
    why = _holder_block(d.name, serial)
    if why and not force:
        raise KBNeed(f'{serial} {why}，往它装包会覆盖对方的包、打断对方测试', {'需要': '强制安装', '序列号': serial, '原因': why})
    with _LOCK:
        if any(x['序列号'] == serial and x['状态'] in ('下载中', '安装中') for x in _JOBS.values()):
            raise KBError(f'{serial} 正在装别的包，等它装完')
        jid = uuid.uuid4().hex[:10]
        job = _JOBS[jid] = {'编号': jid, '期': d.name, '序列号': serial, '包': a['名称'], '构建': b['编号'], '状态': '下载中',
                            '进度': 0, '已下': 0, '总': 0, '缓存': False, '输出': '', '包名': '', '开始': now()}
    m = load_meta(d)
    if m.get('安装渠道') != channel_key(path):
        m['安装渠道'] = channel_key(path)
        save_meta(d, m)
    dest = CACHE_DIR / _cache_name(j['名称'], b['编号'], path)
    threading.Thread(target=_run, args=(job, p, d, j, b, a, dest, serial, force, uninstall, by), daemon=True).start()
    return jid


def _run(job, p, d, j, b, a, dest, sn, force, uninstall, by):
    try:
        _download(p, a['地址'], dest, job)
        job['状态'] = '安装中'
        if not force:                       # 下载期间可能被别的期占了
            why = _holder_block(d.name, sn)
            if why:
                raise KBError(f'下载期间 {sn} 被占用了：{why}；没有安装')
        rc, out = _adb_run(['-s', sn, 'install', '-r', str(dest)])
        kind = adb_failure(out) if rc else ''
        if kind == '降级':
            rc, out2 = _adb_run(['-s', sn, 'install', '-r', '-d', str(dest)])
            out = out + '\n\n（版本降级，加 -d 重试）\n' + out2
            kind = adb_failure(out2) if rc else ''
        if kind == '签名冲突':
            pkg = adb_package(out, dest)
            job['包名'] = pkg
            if not uninstall:
                job.update(状态='签名冲突', 输出=out)
                return
            if not pkg:
                raise KBError('签名不一致，但认不出包名，没法卸载：\n' + out)
            rc_u, out_u = _adb_run(['-s', sn, 'uninstall', pkg], timeout=120)
            out += f'\n\n（签名不一致，已确认卸载 {pkg}）\n' + out_u
            if rc_u and 'Success' not in out_u:
                job.update(状态='失败', 输出=out)
                return
            rc, out2 = _adb_run(['-s', sn, 'install', '-r', '-d', str(dest)])
            out += '\n' + out2
        job['输出'] = out
        if rc:
            job['状态'] = '失败'
            log(d, '安装到手机', f'{sn} 安装 {j["名称"]} #{b["编号"]} {a["名称"]} 失败（{by}）')
            return
        with devlock.locked() as data:
            old = data.get(sn) or {}
            t = time.time()
            data[sn] = {'期': d.name, '方式': '申请', '开始': old.get('开始') if old.get('期') == d.name else devlock.stamp(),
                        '分支': (load_meta(d).get('开发会话') or {}).get('分支') or b.get('分支', ''), '仓库': load_meta(d).get('开发仓库', ''),
                        '型号': next((x['型号'] for x in _dev.devices(d.name) if x['序列号'] == sn), old.get('型号', '')),
                        '用途': f'网页安装 #{b["编号"]} {a["名称"]}'[:200], '最后使用': t}
            devlock.touch_recent([sn], t)
        job['状态'] = '成功'
        log(d, '安装到手机', f'{sn} 装上 {j["名称"]} #{b["编号"]} {a["名称"]}（{by}{"，强制" if force else ""}{"，卸载重装" if uninstall and job["包名"] else ""}），记为本期占用')
    except KBError as e:
        job.update(状态='失败', 输出=(job.get('输出') + '\n' if job.get('输出') else '') + str(e))
    except Exception as e:       # noqa: BLE001 线程里的意外也要让网页看到
        job.update(状态='失败', 输出=repr(e))


def install_job(jid):
    j = _JOBS.get(jid)
    if not j:
        raise KBError('没有这个安装任务（网页服务重启过？）')
    return j
