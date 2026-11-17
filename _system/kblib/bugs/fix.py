# From 大大王.DT
"""Bug 修复：审核通过后的独立修复会话和修复队列；只在知识库标记。

git 仓库：修复会话在独立 worktree（fixwt.py）里修，每个 Bug 本地提交一次（不推送），结束后合回目标分支；
  主工作区有改动、在别的分支上都不挡。worktree 建不起来才退回主工作区（要求干净、在目标分支上）。
svn 工作副本：svn 一提交就进服务器，修复会话只改不提交，kb_bug_fixed 报这个 Bug 改了哪些文件（修复.待提交）；
  你在 Bug 上点「⬆ 提交到 svn」才提交（bug_svn_commit，只在网页，不给 AI）。同一个工作副本有待提交的改动时，后面的修复先排队。"""
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from kblib.core.common import KBError, PERIODS, load_settings, now, uuid_hex
from kblib.core.periods import load_meta, log, period_dir, period_names
from kblib.ai.engines import engine_popen, role_engine
from kblib.ai.devprep import _git
from kblib.infra import hosts, timing
from kblib.ai.devsession import _alive, _dev_env, _notify
from kblib.ai.devices import dev_activity
from kblib.bugs.index import ZT_DIR, _zt_recheck, an_skip, zt_load, zt_save
from kblib.bugs.analysis import _AN, _an_finish, _an_load, _an_md, _an_save, _bug_repo, _fx_heal, an_sessions
from kblib.bugs import fixwt
from kblib.bugs import analysis as _bug_an
from kblib.plugins import registry as _plugins


FX_MAX_BUGS = 5
FIX_STATES = ('排队中', '修复中', '已修复', '未完成')
BLOCK_ALERT_MIN = 10       # 排队受阻超过几分钟：发通知、进管家卡片


def _use_wt(root):
    """git 仓库默认用独立 worktree 修；设置里「修复用worktree」=false 时退回主工作区。"""
    return not _is_svn(root) and load_settings().get('修复用worktree', True) is not False


def _set_wait(f, wait, block):
    """更新排队原因；刚变成受阻时记下「阻塞起」（超过 BLOCK_ALERT_MIN 分钟提醒），不受阻了清掉。返回有没有变化。"""
    new = {'等待': wait or '', '阻塞': bool(block)}
    if {k: f.get(k, '' if k == '等待' else False) for k in new} == new and (not block or f.get('阻塞起')):
        return False
    f.update(new)
    if block:
        f.setdefault('阻塞起', now())
    else:
        for k in ('阻塞起', '受阻提醒'):
            f.pop(k, None)
    return True


def _main_block(root, branch):
    """在主工作区修时的前提：没有开发会话占着、在目标分支上、没有未提交的改动。→ (等待, 受阻)。"""
    rp = Path(root)
    busy = _repo_busy(root)
    if busy:
        return busy, ''
    cur = _git(rp, 'branch', '--show-current').stdout.strip()
    if branch and cur != branch:
        return '', f'仓库当前在分支 {cur or "（游离）"}，这些 Bug 要在 {branch} 上修：切到 {branch} 后自动开始'
    if _git(rp, 'status', '--porcelain', '--untracked-files=no').stdout.strip():
        return '', '仓库有未提交的改动：可以「收起改动并继续」，或提交 / stash 后自动开始（修复会话每个 Bug 单独提交，不混进别的改动）'
    return '', ''


def _repo_busy(root, fix_only=False, me=''):
    """这个仓库有没有别的会话在改：期 / 事项的开发会话，或别的修复会话。fix_only：只看修复会话（用 worktree 修时开发会话不挡）。
    me：发起方自己（期名 = 那一期的开发会话，an:<编号> = 那个修复会话），不算占用（会话里按需调插件时用）。"""
    r = str(Path(root).resolve())
    for n in ([] if fix_only else period_names()):
        if n == me:
            continue
        m = load_meta(PERIODS / n)
        sx = m.get('开发会话') or {}
        if sx.get('状态') == '运行中' and _alive(sx.get('pid', 0)) and m.get('开发仓库') and str(Path(m['开发仓库']).resolve()) == r:
            return f'「{n}」的开发会话正在这个仓库里运行'
    for x in an_sessions():
        if x.get('类型') == '修复' and x.get('状态') == '运行中' and str(Path(x['仓库']).resolve()) == r and me != f'an:{x["编号"]}':
            return f'修复会话 {x["编号"]}（Bug {"、".join(x["Bug"])}）正在这个仓库里运行'
    return ''


def _is_svn(root):
    rp = Path(root)
    return (rp / '.svn').exists() and not (rp / '.git').exists()


def _fx_prompt(root, branch, items, wt=None):
    svn = _is_svn(root)
    where = (f"""代码目录：{wt[0]}——这是仓库 {root} 的独立 git worktree，已检出工作分支 {wt[2]}（从 {branch} 最新提交开始，工作区干净）。
  用户的主工作区 {root} 可能有别的未提交改动、可能在别的分支上，都和你无关：只在 {wt[0]} 里改、编译、提交，不要去主工作区改文件、不要动它的 git 状态。
  会话结束后知识库自动把你的提交合回 {branch}，不用你合。被 .gitignore 忽略的本机配置（构建脚本用的配置目录等）这里没有的，从 {root} 拷过来用，不要提交。
  要看分支：{branch}；代码路径都相对 {wt[0]}（和主仓库一样）。""" if wt else f'代码仓库：{root}，分支 {branch}（已检出，工作区干净）。')
    out = [f"""你是由知识库网页启动的【后台无人值守】Bug 修复会话。用户已在网页审核通过了下面每个 Bug 的分析，并点了「修复」（等同批准改代码）。终端里没有人，不要提问。
{where}""" + (
        '这是 svn 工作副本：不要 svn switch / update / revert，【不要 svn commit】（svn 一提交就进服务器，由用户在网页确认后提交）。' if svn else
        '不要切换分支、不要 push。') + """不要改和这些 Bug 无关的代码。不要改 Bug 平台（知识库从不回写）。
"""]
    for e in items:
        a = e['分析']
        locs = '\n'.join(f'    · {x["路径"]}:{x["行"]} {x["说明"]}' for x in a.get('代码位置') or [])
        out.append(f"""## Bug {e["编号"]}：{e["标题"]}
  {e["链接"]}
  审核通过的分析（{a.get("时间")}，置信度 {a.get("置信度") or "?"}）：{a["摘要"]}
  代码位置：
{locs or "    （无）"}
  原因：{a.get("原因") or "（无）"}
  修复思路：{a.get("思路") or "（无）"}
  影响范围：{a.get("影响") or "（无）"}
  自测要点：{a.get("自测") or "（无）"}""" + (f'\n  用户审核意见（最新，以这条为准；和上面的修复思路冲突时按意见改）：{a["意见"]}' if a.get('意见') else '')
                   + _fx_last(e))
    i18n = _plugins.i18n_hint('', wt[0] if wt else root, wt[0] if wt else root) if not hosts.is_remote(root) else ''
    if i18n:      # 修复用的代码缺多语言 key：先调多语言拉取插件（worktree 里要带 仓库=）
        out.append('\n' + i18n.removeprefix('- ').replace('然后继续', '然后继续修') + '。')
    out.append("""
每个 Bug 依次做：
1. kb_bug_get 实时拉取，确认 Bug 还要修（平台上已关闭、或内容跟分析时不一样了，就不改，直接 kb_bug_fixed 报告 done=false 并写明原因）。
2. 按审核通过的修复思路改，改动尽量小；思路在代码里走不通时，不要自己换一套大改，kb_bug_fixed 报告 done=false 写明卡在哪。
3. 编译（构建命令看仓库根目录的 AGENTS.md / README；安卓一般是 ./gradlew :模块:compileXxxJavaWithJavac 或 assembleXxx），有相关单测就跑。
""" + ("""4. 不提交。新文件 svn add、删文件 svn delete，让 svn status 能看到；记下这个 Bug 改了哪些文件。
5. kb_bug_fixed 标记：id、done=true、files=[这个 Bug 改的文件，相对仓库根目录]、commits 留空、note（改了什么、怎么验证的、还要人工确认什么）。
   几个 Bug 改了同一个文件也照实写，提交时会一起提交。""" if svn else """4. 只 git add 这次改的文件，提交一次，提交信息：「fix: Bug <编号> <一句话>」（仓库有提交规范就按规范，带上 Bug 编号）。
5. kb_bug_fixed 标记：id、done=true、commits=[提交号]、note（改了什么、怎么验证的、还要人工确认什么）。""") + """
   要上手机验证的：kb_devices 看、kb_device_claim 申请（只填台数 count，不要照历史记录点名序列号，由知识库分配空闲手机），只在申请到的手机上装包测试，测完 kb_device_release；没有手机就在 note 里写「待真机验证」，不要卡住。
全部处理完后结束，最后用中文回复每个 Bug 的结果。""")
    return '\n'.join(out)


def _fx_last(e):
    """上一次没修成的原因：带给这次的修复会话，免得再走同一条走不通的路。"""
    p = (e.get('修复') or {}).get('上次')
    if not p:
        return ''
    out = f'\n  上一次修复没修成（{p.get("时间")}）：{p.get("说明")}\n  这次按最新的审核意见改，不要重复上次走不通的做法。'
    if p.get('遗留'):
        out += (f'\n  上次会话留下的半成品已收起：{p["遗留"]}。先看一眼（git stash show -p --include-untracked <提交号> / 打开补丁），'
                '合适就拿来接着改（git stash apply / svn patch），不合适就重新改；不要删掉它。')
    return out


def set_fix_note(e, note, by):
    """点「修复」时意见框里写的就是最新审核意见：替换当前意见，旧的进历史（不再给修复会话）。"""
    note = (note or '').strip()
    a = e.get('分析') or {}
    if not note or note == (a.get('意见') or '').strip():
        return False
    if a.get('意见'):
        a.setdefault('意见历史', []).append({'意见': a['意见'], '时间': a.get('审核时间') or a.get('时间') or '', '人': a.get('审核人') or ''})
    a.update(意见=note, 审核时间=now(), 审核人=by)
    return True


def bug_fix(ids, by='用户（网页）', note=None):
    """网页「🔧 修复」= 本人批准修：加入修复队列，能开始的立刻开始；仓库忙就排队，分支不对 / 有未提交改动就标「阻塞」等你处理。
    note：意见框里写的审核意见（只修一个 Bug 时），替换之前的意见，修复会话以它为准。不作为 MCP 工具提供。"""
    ids = [str(i).strip() for i in (ids if isinstance(ids, (list, tuple)) else [ids]) if str(i).strip()]
    ids, skipped = _zt_recheck(ids, '修复前核对')        # 已经转给别人的直接移除，不进队列
    added = []
    if not ids:
        raise KBError('没有加入修复队列：' + '；'.join(skipped))
    analyzing = {b for x in an_sessions() if x.get('状态') == '运行中' and x.get('类型') != '修复' for b in x.get('Bug', [])}
    with _FX_LOCK:
        data = zt_load()
        for bid in ids:
            e = data['Bug'].get(bid)
            a = (e or {}).get('分析') or {}
            f = (e or {}).get('修复') or {}
            if not e:
                skipped.append(f'{bid}：索引里没有')
            elif a.get('状态') != '已通过':
                skipped.append(f'{bid}：分析还没审核通过')
            elif a.get('过时'):
                skipped.append(f'{bid}：分析之后平台上又改过，先重新分析')
            elif f.get('状态') in ('修复中', '排队中'):
                skipped.append(f'{bid}：已经在{f["状态"][:-1]}')
            elif bid in analyzing:
                skipped.append(f'{bid}：正在重新分析，分析完审核通过再修')
            else:
                root = a.get('仓库') or _bug_repo(e)[0]
                if not root:
                    skipped.append(f'{bid}：不知道在哪个仓库修')
                    continue
                last = ({'时间': f.get('时间') or '', '说明': f.get('说明') or '', **({'遗留': f['遗留']} if f.get('遗留') else {})}
                        if f.get('状态') == '未完成' and (f.get('说明') or f.get('遗留')) else f.get('上次'))
                if note is not None and len(ids) == 1 and set_fix_note(e, note, by):
                    _fx_log(e, f'审核意见改为：{a["意见"]}（{by}）')
                    _an_md(e)
                e['修复'] = {'状态': '排队中', '排队时间': now(), '排队序号': time.time() + len(added) * 1e-3, '仓库': root, '分支': a.get('分支') or _bug_repo(e)[1], '发起': by,
                             **({'上次': last} if last else {})}
                _fx_log(e, f'加入修复队列（{by}）')
                added.append(bid)
        zt_save(data)
    if not added:
        raise KBError('没有加入修复队列：' + '；'.join(skipped))
    started = fix_queue_tick()
    out = [f'已加入修复队列：Bug {"、".join(added)}'] + started
    q = {b: (e.get('修复') or {}) for b, e in zt_load()['Bug'].items() if b in added}
    wait = [f'{b}：{f.get("等待")}' for b, f in q.items() if f.get('状态') == '排队中' and f.get('等待')]
    if wait:
        out.append('还在排队：' + '；'.join(wait))
    if skipped:
        out.append('没加入：' + '；'.join(skipped))
    return '\n'.join(out)


_FX_LOCK = threading.RLock()


def fix_queue():
    """修复队列和异常：排队中（等待 / 阻塞原因）、修复中（进度）、没修成的。给网页顶部展示。"""
    _fx_heal()
    data = zt_load()
    sess = {x['编号']: x for x in an_sessions() if x.get('类型') == '修复'}
    out = {'修复中': [], '排队中': [], '阻塞': [], '未完成': []}
    for e in data['Bug'].values():
        f = e.get('修复') or {}
        item = {'编号': e['编号'], '标题': e['标题'], '链接': e.get('链接', ''), '仓库': f.get('仓库', ''), '分支': f.get('分支', '')}
        if f.get('状态') == '修复中':
            x = sess.get(f.get('会话')) or {}
            item.update(开始时间=f.get('开始时间', ''), 会话=f.get('会话', ''),
                        动作=[a.get('内容', '')[:160] for a in dev_activity(x.get('日志'), 3)], 同批=x.get('Bug') or [],
                        代码基线=timing.baseline_text(x.get('代码基线')))
            out['修复中'].append(item)
        elif f.get('状态') == '排队中':
            item.update(排队时间=f.get('排队时间', ''), 序号=f.get('排队序号') or 0, 原因=f.get('等待', ''), 阻塞起=f.get('阻塞起', ''))
            out['阻塞' if f.get('阻塞') else '排队中'].append(item)
        elif f.get('状态') == '未完成' and e.get('状态') != '已关闭':
            item.update(时间=f.get('时间', ''), 原因=f.get('说明', ''))
            out['未完成'].append(item)
    for k in out:
        out[k].sort(key=lambda i: (i.get('序号') or 0, i.get('排队时间') or i.get('开始时间') or i.get('时间') or ''))
    out['警告'] = [{'会话': x['编号'], 'Bug': x['Bug'], '警告': x['警告']} for x in sess.values() if x.get('警告')]
    return out


def fix_queue_tick():
    """处理修复队列：按 仓库 + 分支 分组，仓库空闲、分支对、工作区干净就起修复会话（每次最多 FX_MAX_BUGS 个 Bug）。
    修复会话结束、网页每分钟都会调一次，所以阻塞解除（切好分支、提交了改动）后会自动开始。"""
    out = []
    with _FX_LOCK:
        _fx_heal()
        data = zt_load()
        q = sorted((e for e in data['Bug'].values() if (e.get('修复') or {}).get('状态') == '排队中'),
                   key=lambda e: (e['修复'].get('排队序号') or 0, e['修复'].get('排队时间', '')))
        groups, changed = {}, False
        for e in q:
            f, a = e['修复'], e.get('分析') or {}
            if e.get('状态') == '已关闭' or a.get('状态') != '已通过' or a.get('过时'):
                why = '平台上已关闭' if e.get('状态') == '已关闭' else '分析之后平台上又改过，需重新分析' if a.get('过时') else '分析不再是「已通过」'
                e['修复'] = {**f, '状态': '未完成', '时间': now(), '说明': f'排队期间{why}，没有修'}
                _fx_log(e, f'移出修复队列：{why}')
                changed = True
                continue
            groups.setdefault((f['仓库'], f.get('分支') or ''), []).append(e)
        starts = []
        busy_now = set()
        for (root, branch), items in groups.items():
            rp = Path(root)
            block = wait = ''
            if not rp.is_dir():
                block = f'仓库目录不存在：{root}'
            elif _is_svn(root):
                wait = _repo_busy(root) or ('本轮已在这个仓库起了修复会话' if str(rp.resolve()) in busy_now else '')
                if not wait:
                    cur = hosts.branch_of(root, 'svn')
                    if branch and branch not in (cur, cur.rsplit('/', 1)[-1]):
                        block = f'svn 工作副本当前在 {cur}，这些 Bug 要在 {branch} 上修：svn switch 过去后自动开始'
                    elif hosts.svn_changes(root)[0]:
                        pend = [e['编号'] for e in data['Bug'].values() if (e.get('修复') or {}).get('待提交') and (e['修复'].get('仓库') == root)]
                        block = (f'Bug {"、".join(pend)} 修好了还没提交到 svn：在 Bug 上点「⬆ 提交到 svn」后自动开始' if pend else
                                 'svn 工作副本有未提交的改动：提交或处理后自动开始（修复的改动要单独提交，不混进别的改动）')
            elif _use_wt(root):             # 独立 worktree：主工作区脏、在别的分支、开发会话占着都不挡，只要没有别的修复会话
                wait = _repo_busy(root, fix_only=True) or ('本轮已在这个仓库起了修复会话' if str(rp.resolve()) in busy_now else '')
            else:
                wait, block = _main_block(root, branch)
                wait = wait or ('本轮已在这个仓库起了修复会话' if str(rp.resolve()) in busy_now else '')
            go = not block and not wait
            for e in items:
                if go and _use_wt(root) and rp.is_dir():
                    continue                # 要开始了：受阻标记留给 _fx_start（worktree 建不起来时还要接着算受阻时长）
                changed |= _set_wait(e['修复'], block or wait, block)
            if go:
                busy_now.add(str(rp.resolve()))
                starts.append((root, branch or (hosts.branch_of(root, 'svn') if _is_svn(root) else _git(rp, 'branch', '--show-current').stdout.strip()),
                               items[:FX_MAX_BUGS]))
        changed |= _block_alert(data)
        if changed:
            zt_save(data)
        for root, branch, items in starts:
            keep, gone = _zt_recheck([e['编号'] for e in items], '修复前核对')   # 排队期间转给别人的不修
            out += gone
            items = [e for e in items if e['编号'] in keep]
            if items:
                out.append(_fx_start(root, branch, items))
    return out


def _fx_start(root, branch, part, by='修复队列'):
    eng = role_engine('Bug修复')
    rp = Path(root)
    sid = datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid_hex()[:4]
    (ZT_DIR / '分析会话').mkdir(parents=True, exist_ok=True)
    logf = ZT_DIR / '分析会话' / f'修复-{sid}.jsonl'
    wt, why = None, ''
    lbl = f'Bug {"、".join(e["编号"] for e in part)}'
    if _use_wt(root):
        # 修复前更新代码（时机 bug_fix）：worktree 模式只 fetch（目标分支没检出就快进），不动主工作区
        base = timing.code_update('bug_fix', root, 'git', branch=branch, worktree=True, busy='', label=lbl)
        try:
            wt = fixwt.wt_prepare(root, branch, sid)
        except Exception as ex:                                            # worktree 建不起来：退回主工作区（要求干净、分支对）
            why = str(ex) if isinstance(ex, KBError) else repr(ex)
            wait, block = _main_block(root, branch)
            if wait or block:
                return _fx_hold(part, f'建修复 worktree 失败（{why[:200]}），只能在主工作区修：' + (block or wait), bool(block))
            base = timing.code_update('bug_fix', root, 'git', busy='', label=lbl)
    else:
        base = timing.code_update('bug_fix', root, 'svn' if _is_svn(root) else 'git', busy='', label=lbl)   # 队列已保证分支对、工作区干净
    up = base['结果']
    if wt:                                                                 # 基线按 worktree 里的代码算
        base.update(分支=wt[2], 提交=(wt[1] or '')[:8], 记录=base['记录'] + f'；在 worktree {wt[0]}（{wt[2]}）里修')
    for pd in _bug_an._owner_dirs(part):
        log(pd, '更新代码', base['记录'])
    plug = _plugins.run_plugins('bug_fix', None, wt[0] if wt else root, wt[2] if wt else branch) if not hosts.is_remote(root) else ''
    head = wt[1] if wt else hosts.head(root, 'svn') if _is_svn(root) else _git(rp, 'rev-parse', 'HEAD').stdout.strip()
    untracked = [] if _is_svn(root) or wt else _untracked(root)    # 结束时只收会话新建的未跟踪文件，用户原有的不动
    o = part[0].get('归属') or {}
    prompt = _fx_prompt(root, branch, part, wt) + (f'\n\n【修复前已运行的插件】（知识库自动运行，结果供参考；插件改动的文件不属于这个 Bug 的就别提交）\n{plug}' if plug else '')
    scene, bend = 'Bug修复', _bug_an.bug_team_end(part)
    proc = engine_popen(eng, prompt, f'kb·修复·Bug {"、".join(e["编号"] for e in part)}', logf, wt[0] if wt else root,
                        env_extra=dict(_dev_env(o.get('名称') or 'Bug 修复', branch, root), KB_SESSION='an:' + sid),
                        scene=scene, end=bend, period=_bug_an._owner_meta(part))
    _AN[sid] = proc
    rec = {'编号': sid, '类型': '修复', 'pid': proc.pid, '状态': '运行中', '开始时间': now(), '仓库': root, '分支': branch,
           '起点': head, '更新代码': up, '代码基线': base, '场景': scene, '端': bend, 'Bug': [e['编号'] for e in part], '日志': str(logf), '引擎': eng['名称'], '发起': by,
           **({'工作树': wt[0], '工作分支': wt[2]} if wt else {'未跟踪前': untracked}), **({'工作树失败': why} if why else {})}
    allx = _an_load()
    allx.append(rec)
    _an_save(allx)
    data = zt_load()
    for e in part:
        old = data['Bug'][e['编号']].get('修复') or {}
        data['Bug'][e['编号']]['修复'] = {'状态': '修复中', '开始时间': now(), '会话': sid, '分支': branch, '仓库': root,
                                          '排队时间': old.get('排队时间', ''), '发起': old.get('发起', '')}
        _fx_log(data['Bug'][e['编号']], f'开始修复（会话 {sid}；修复前更新代码：{up}）')
    zt_save(data)

    def wait(sid=sid, proc=proc):
        code = proc.wait()
        _AN.pop(sid, None)
        _an_finish(sid, code)
    threading.Thread(target=wait, daemon=True).start()
    return f'已开始：{eng["名称"]} 在 {root}（{branch}{"，独立 worktree " + wt[0] if wt else ""}）修复 Bug {"、".join(rec["Bug"])}'


def _fx_hold(part, why, block):
    """开始不了：这批 Bug 留在队列里，写上原因。"""
    data = zt_load()
    for e in part:
        f = (data['Bug'].get(e['编号']) or {}).get('修复') or {}
        if f.get('状态') == '排队中':
            _set_wait(f, why, block)
    zt_save(data)
    return f'Bug {"、".join(e["编号"] for e in part)} 没开始：{why}'


def _block_alert(data):
    """受阻超过 BLOCK_ALERT_MIN 分钟还没处理：发一次通知（管家卡片由 steward 按 阻塞起 生成）。返回有没有改 data。"""
    cut = datetime.now().timestamp() - BLOCK_ALERT_MIN * 60
    late = []
    for e in data['Bug'].values():
        f = e.get('修复') or {}
        if f.get('状态') != '排队中' or not f.get('阻塞') or not f.get('阻塞起') or f.get('受阻提醒') == f['阻塞起']:
            continue
        try:
            t = datetime.strptime(f['阻塞起'], '%Y-%m-%d %H:%M').timestamp()
        except ValueError:
            continue
        if t <= cut:
            f['受阻提醒'] = f['阻塞起']
            late.append(e)
    if late:
        _notify('知识库 · Bug 修复排队受阻', f'Bug {"、".join(e["编号"] for e in late)} 已受阻 {BLOCK_ALERT_MIN} 分钟以上：'
                + (late[0]['修复'].get('等待') or '')[:80] + '。到 Bug 页「修复进度」处理')
    return bool(late)


def fix_blocked(minutes=BLOCK_ALERT_MIN):
    """受阻超过 minutes 分钟的排队 Bug（给管家卡片）：[{编号, 标题, 原因, 阻塞起, 仓库}]。"""
    cut = datetime.now().timestamp() - minutes * 60
    out = []
    for e in zt_load()['Bug'].values():
        f = e.get('修复') or {}
        if f.get('状态') == '排队中' and f.get('阻塞') and f.get('阻塞起'):
            try:
                if datetime.strptime(f['阻塞起'], '%Y-%m-%d %H:%M').timestamp() > cut:
                    continue
            except ValueError:
                continue
            out.append({'编号': e['编号'], '标题': e.get('标题', ''), '原因': f.get('等待', ''), '阻塞起': f['阻塞起'], '仓库': f.get('仓库', '')})
    return out



# ---- 排队受阻卡片上的按钮：看改动 / 收起改动并继续 / 改动就是这个 Bug 的修复，提交并标记已修复

def _wc_changes(root):
    """主工作区里没提交的改动 → [(状态, 路径)]（git 只看跟踪文件；svn 不含没 svn add 的）。"""
    if _is_svn(root):
        return hosts.svn_changes(root)[0]
    out = []
    for ln in hosts.git(root, 'status', '--porcelain', '--untracked-files=no', '-z').stdout.split('\0'):
        if len(ln) > 3:
            out.append((ln[:2].strip() or 'M', ln[3:]))
    return out


def _queued_here(data, root):
    return [e for e in data['Bug'].values() if (e.get('修复') or {}).get('状态') == '排队中'
            and str(Path(e['修复'].get('仓库') or '/').resolve()) == str(Path(root).resolve())]


def _classify(path, bugs):
    """改动文件和排队 Bug 的关系：本 Bug（分析的代码位置 / 上次会话改过）、可能相关（同目录）、无关。"""
    hit = near = None
    for e in bugs:
        locs = [x.get('路径', '') for x in (e.get('分析') or {}).get('代码位置') or []]
        last = (e['修复'].get('上次') or {}).get('文件') or []
        if path in locs or path in last:
            hit = e['编号']
            break
        if any(l and str(Path(l).parent) == str(Path(path).parent) for l in locs):
            near = near or e['编号']
    return ('本 Bug', hit) if hit else ('可能相关', near) if near else ('无关', '')


def fix_block_view(root, diff=True):
    """网页「看改动」：仓库里挡住修复队列的未提交改动，按和排队 Bug 的关系分组，附 diff（每个文件截断）。"""
    if not root or not Path(root).is_dir():
        raise KBError(f'仓库目录不存在：{root}')
    data = zt_load()
    bugs = _queued_here(data, root)
    svn = _is_svn(root)
    files = []
    for st, pth in _wc_changes(root):
        kind, bid = _classify(pth, bugs)
        d = ''
        if diff:
            r = hosts.svn(root, 'diff', pth) if svn else hosts.git(root, 'diff', 'HEAD', '--', pth)
            d = (r.stdout or '')[:6000]
        files.append({'路径': pth, '状态': st, '关系': kind, 'Bug': bid, 'diff': d})
    files.sort(key=lambda f: ({'本 Bug': 0, '可能相关': 1, '无关': 2}[f['关系']], f['路径']))
    return {'仓库': root, 'svn': svn, '分支': hosts.branch_of(root, 'svn') if svn else _git(Path(root), 'branch', '--show-current').stdout.strip(),
            '文件': files, 'Bug': [{'编号': e['编号'], '标题': e.get('标题', ''), '原因': e['修复'].get('等待', '')} for e in bugs]}


def fix_block_stash(root, files=None, by='用户（网页）'):
    """网页「收起改动并继续」：git stash push（-m 写明来源，可 git stash apply 恢复）；svn 存补丁后 revert。files 空 = 全部。"""
    if not root or not Path(root).is_dir():
        raise KBError(f'仓库目录不存在：{root}')
    live = [p for _, p in _wc_changes(root)]
    files = [f for f in (files or live) if f in live]
    if not files:
        raise KBError('没有要收起的改动（可能已经处理过了），刷新看看')
    label = f'kb 修复队列：{by}在网页收起的主工作区改动（{now()}，{len(files)} 个文件）'
    with _FX_LOCK:
        if _is_svn(root):
            d = hosts.svn(root, 'diff', *files)
            if d.returncode:
                raise KBError(f'svn diff 失败：{(d.stderr or d.stdout).strip()[-300:]}')
            pf = ZT_DIR / '收起的改动' / f'{datetime.now():%Y%m%d-%H%M%S}-{Path(root).name}.patch'
            pf.parent.mkdir(parents=True, exist_ok=True)
            pf.write_text(d.stdout, 'utf-8')
            r = hosts.svn(root, 'revert', *files)
            if r.returncode:
                raise KBError(f'svn revert 失败（补丁已存到 {pf}）：{(r.stderr or r.stdout).strip()[-300:]}')
            where = f'补丁 {pf}（在工作副本根目录 svn patch 它即可恢复）'
        else:
            r = hosts.git(root, 'stash', 'push', '-m', label, '--', *files, timeout=300)
            if r.returncode:
                raise KBError(f'git stash 失败：{(r.stderr or r.stdout).strip()[-300:]}')
            ref = hosts.git(root, 'rev-parse', '--short', 'stash@{0}').stdout.strip()
            where = f'git stash「{label}」（{ref}；git stash apply {ref} 恢复）'
        data = zt_load()
        for e in _queued_here(data, root):
            _fx_log(e, f'{by}收起了挡住修复的 {len(files)} 个改动：{where}')
    started = fix_queue_tick()
    return f'已收起 {len(files)} 个文件的改动：{where}' + ('\n' + '\n'.join(started) if started else '')


def fix_block_commit(bid, files, by='用户（网页）'):
    """网页「改动就是这个 Bug 的修复，提交并标记已修复」：只提交选中的文件（git 本地提交不 push；svn 记成待提交，等你点「提交到 svn」）。"""
    data = zt_load()
    e = data['Bug'].get(str(bid))
    f = (e or {}).get('修复') or {}
    if not e or f.get('状态') != '排队中':
        raise KBError(f'Bug {bid} 不在修复队列里')
    root = f.get('仓库') or ''
    live = [p for _, p in _wc_changes(root)]
    files = [x for x in (files or []) if x in live]
    if not files:
        raise KBError('先勾选这个 Bug 的修复改了哪些文件')
    note = f'工作区里已有的改动（{len(files)} 个文件），{by}在网页确认就是这个 Bug 的修复'
    with _FX_LOCK:
        if _is_svn(root):
            msg = bug_fixed(bid, True, None, note, by=by, files=files)
        else:
            r = hosts.git(root, 'add', '--', *files)
            if r.returncode:
                raise KBError(f'git add 失败：{(r.stderr or r.stdout).strip()[-300:]}')
            r = hosts.git(root, 'commit', '-q', '-m', f'fix: Bug {bid} {e.get("标题", "")[:60]}', '--', *files, timeout=300)
            if r.returncode:
                raise KBError(f'git commit 失败：{(r.stderr or r.stdout).strip()[-300:]}')
            sha = hosts.git(root, 'rev-parse', '--short=12', 'HEAD').stdout.strip()
            msg = bug_fixed(bid, True, [sha], note + f'；提交 {sha}（没 push）', by=by)
    started = fix_queue_tick()
    return msg + ('\n' + '\n'.join(started) if started else '')

def _fx_log(e, text):
    o = e.get('归属') or {}
    if o.get('类型') in ('期', '事项') and o.get('名称'):
        try:
            log(period_dir(o['名称']), 'Bug 修复', f'Bug {e["编号"]} {e["标题"]}：{text}')
        except KBError:
            pass


def _untracked(root):
    """git 仓库里的未跟踪文件（不含 .gitignore 忽略的）。"""
    try:
        p = hosts.git(root, 'ls-files', '--others', '--exclude-standard', '-z')
    except KBError:
        return []
    return [s for s in p.stdout.split('\0') if s] if p.returncode == 0 else []


def _shelve_git(root, x, label):
    tracked = hosts.git(root, 'status', '--porcelain', '--untracked-files=no').stdout.strip()
    before = set(x.get('未跟踪前') or [])
    new = [p for p in _untracked(root) if p not in before] if '未跟踪前' in x else []    # 老会话没记，不碰未跟踪文件
    if not tracked and not new:
        return ''
    if new:
        r = hosts.git(root, 'add', '--', *new)
        if r.returncode:
            raise KBError(f'git add 会话新建的文件失败：{(r.stderr or r.stdout).strip()[-300:]}')
    r = hosts.git(root, 'stash', 'push', '-m', label)
    if r.returncode:
        if new:
            hosts.git(root, 'reset', '-q', '--', *new)          # 撤回上面的 add，保持原样
        raise KBError(f'git stash 失败：{(r.stderr or r.stdout).strip()[-300:]}')
    ref = hosts.git(root, 'rev-parse', '--short', 'stash@{0}').stdout.strip()
    return f'git stash「{label}」（{ref}，git stash list 里找这条）'


def _shelve_svn(root, sid, data, label):
    keep = {p for e in data['Bug'].values() if (e.get('修复') or {}).get('仓库') == root
            for p in (e['修复'].get('待提交') or [])}            # 修好了等提交到 svn 的文件不动
    ch = [pth for _, pth in hosts.svn_changes(root)[0] if pth not in keep]
    if not ch:
        return ''
    d = hosts.svn(root, 'diff', *ch)
    if d.returncode:
        raise KBError(f'svn diff 失败：{(d.stderr or d.stdout).strip()[-300:]}')
    if re.search(r'^(Cannot display: file marked as a binary type|无法显示)', d.stdout, re.M):     # svn 输出可能是中文
        raise KBError('有二进制文件改动，补丁存不下，没有收起')
    pf = ZT_DIR / '分析会话' / f'修复-{sid}-遗留.patch'
    pf.parent.mkdir(parents=True, exist_ok=True)
    pf.write_text(d.stdout, 'utf-8')
    r = hosts.svn(root, 'revert', '--depth', 'infinity', *ch)
    if r.returncode:
        raise KBError(f'svn revert 失败（补丁已存到 {pf}）：{(r.stderr or r.stdout).strip()[-300:]}')
    return f'补丁 {pf}（{label}；在工作副本根目录 svn patch 它即可恢复，svn add 的新文件 revert 后还留在磁盘上，要用就重新 svn add）'


def fx_shelve(x, data):
    """修复会话结束后工作区还有改动 → 收起来，不卡后面的修复队列（开始前队列保证过干净，所以都是这个会话留下的）。
    git：会话新建的未跟踪文件 git add 后 git stash push；svn：「待提交」的文件不动，其余存成补丁后 svn revert。
    收在哪记到没修成的 Bug 的 修复.遗留 和会话记录的 遗留；收不起来保持原样（队列照旧提示），记到 遗留失败。
    x：会话记录，data：zt_load() 的数据，都由调用方保存。返回给通知用的一句话（没有遗留就是空串）。"""
    root, sid = x.get('仓库') or '', x['编号']
    if not root or not Path(root).is_dir():
        return ''
    mine = [data['Bug'][b] for b in x['Bug'] if (data['Bug'].get(b, {}).get('修复') or {}).get('会话') == sid]
    left = [e for e in mine if e['修复'].get('状态') != '已修复']
    label = f'kb 修复会话 {sid} 遗留（Bug {"、".join(e["编号"] for e in (left or mine)) or "、".join(x["Bug"])}）'
    if x.get('工作树'):
        return _fx_wrap_wt(x, data, mine, left, label)
    try:
        with _FX_LOCK:
            where = _shelve_svn(root, sid, data, label) if _is_svn(root) else _shelve_git(root, x, label)
    except Exception as ex:                  # 收不起来：保持原样，队列照旧提示「有未提交的改动」
        msg = str(ex) if isinstance(ex, KBError) else repr(ex)
        x['遗留失败'] = msg
        for e in (left or mine[:1]):
            _fx_log(e, f'修复会话 {sid} 留下的改动没能自动收起：{msg}；请手动处理')
        return f'；⚠ 遗留改动没收起：{msg}'
    if not where:
        return ''
    x['遗留'] = where
    for e in left:
        e['修复']['遗留'] = where
    for e in (left or mine[:1]):
        _fx_log(e, f'修复会话 {sid} 结束时工作区还有改动，已收起：{where}')
    return f'；遗留改动已收起（{where}）'


def _fx_wrap_wt(x, data, mine, left, label):
    """worktree 模式的收尾：没提交的改动 git stash（记到 遗留），提交合回目标分支（提交号变了就改记录），合不回记 警告。"""
    root, sid, wt = x['仓库'], x['编号'], x['工作树']
    tail = ''
    with _FX_LOCK:
        try:
            where, ref = fixwt.wt_shelve(wt, label)
        except Exception as ex:
            msg = str(ex) if isinstance(ex, KBError) else repr(ex)
            x['遗留失败'] = msg
            for e in (left or mine[:1]):
                _fx_log(e, f'修复会话 {sid} 留下的改动没能自动收起：{msg}；改动还在 worktree {wt} 里')
            return f'；⚠ 遗留改动没收起：{msg}'
        if where:
            x.update(遗留=where, 遗留提交=ref)
            for e in left:
                e['修复']['遗留'] = where
            for e in (left or mine[:1]):
                _fx_log(e, f'修复会话 {sid} 结束时 worktree 里还有没提交的改动，已收起：{where}')
            tail += f'；遗留改动已收起（{where}）'
        try:
            msg, ok, remap, log = fixwt.wt_integrate(root, x['分支'], wt, x.get('起点', ''), x.get('工作分支', ''))
        except Exception as ex:
            msg, ok, remap, log = f'⚠ 合回 {x["分支"]} 出错：{ex}；提交留在分支 {x.get("工作分支")}', False, {}, []
        x['新提交'] = log[:20]
        if msg:
            x['合回'] = msg
        if not ok:
            x['警告'] = msg
        for e in mine:
            f = e['修复']
            for c in f.get('提交') or []:
                full = next((v for k, v in remap.items() if v and k.startswith(c['提交'])), None)
                if full:
                    c['提交'] = full[:12]
            if not ok and f.get('状态') == '已修复':
                f['说明'] = (f.get('说明') or '') + f'\n{msg}'
            if msg:
                _fx_log(e, f'修复会话 {sid}：{msg}')
    return tail + (f'；{msg}' if msg else '')


def bug_fixed(bid, done=True, commits=None, note='', by='Claude', files=None):
    """标记 Bug 修好了（或没修成）：只记在知识库，不改 Bug 平台。修复会话、对话里的 AI 或用户在网页上手动标记都用它。
    svn 仓库：files = 这个 Bug 改的文件（还没提交），记成「待提交」，等你在网页点「提交到 svn」。"""
    data = zt_load()
    e = data['Bug'].get(str(bid))
    if not e:
        raise KBError(f'Bug 索引里没有 {bid}')
    old = e.get('修复') or {}
    root = old.get('仓库') or (e.get('分析') or {}).get('仓库') or _bug_repo(e)[0]
    if isinstance(commits, str):
        commits = [x for x in re.split(r'[\s,，]+', commits) if x]
    if isinstance(files, str):
        files = [x for x in re.split(r'[\s,，]+', files) if x]
    pending = []
    if root and _is_svn(root) and done and files:
        live = {pth for _, pth in hosts.svn_changes(root)[0]}
        miss = [f for f in files if f.strip('/') not in live]
        if miss:
            raise KBError(f'这些文件在 svn 工作副本里没有改动（新文件要先 svn add）：{"、".join(miss)}')
        pending = [f.strip('/') for f in files]
        commits = [c for c in commits or [] if not re.fullmatch(r'r?\d+', c)] or None     # svn 版本号不按 git 提交校验
    ok, bad = [], []
    for c in commits or []:
        r = _git(Path(root), 'rev-parse', '--verify', '--quiet', f'{c}^{{commit}}') if root else None
        if r is not None and r.returncode == 0:
            full = r.stdout.strip()
            subj = _git(Path(root), 'log', '-1', '--format=%s', full).stdout.strip()
            ok.append({'提交': full[:12], '说明': subj})
        else:
            bad.append(c)
    if done and bad:
        raise KBError(f'这些提交在 {root or "仓库"} 里找不到：{"、".join(bad)}，确认后重新标记')
    e['修复'] = {**old, '状态': '已修复' if done else '未完成', '时间': now(), '提交': ok or old.get('提交', []),
                 '说明': note or '', '标记': by, '修改时间': e.get('修改时间', '')}
    e['修复'].pop('结果', None)
    if pending:
        e['修复']['待提交'] = pending
    elif not done:
        e['修复'].pop('待提交', None)
    a = e.get('分析') or {}
    if done:
        an_skip(e)
    elif a.get('状态') == '无需审核':
        a['状态'] = a.pop('跳过前', '待审核')
    zt_save(data)
    _fx_log(e, ('已修复' if done else '未修成') + (f'，提交 {"、".join(x["提交"][:8] for x in ok)}' if ok else '') + (f'：{note}' if note else '') + f'（{by}）')
    return f'Bug {bid} 已在知识库标记为「{"已修复" if done else "未完成"}」（不改 Bug 平台）' + (
        f'；{len(pending)} 个文件留在 svn 工作副本，等用户在网页点「提交到 svn」' if pending else '')


def bug_svn_commit(bid, by='用户（网页）'):
    """网页「⬆ 提交到 svn」（本人点）：把修好的 Bug 改的文件提交到 svn。和它改了同一批文件的其他待提交 Bug 一起提交。"""
    with _FX_LOCK:
        data = zt_load()
        e = data['Bug'].get(str(bid))
        f = (e or {}).get('修复') or {}
        if not f.get('待提交'):
            raise KBError(f'Bug {bid} 没有待提交到 svn 的改动')
        root = f.get('仓库') or ''
        group = [e]
        files = set(f['待提交'])
        grew = True
        while grew:                     # 文件有交集的待提交 Bug 一起交（同一个文件分不开）
            grew = False
            for x in data['Bug'].values():
                xf = (x.get('修复') or {})
                if x not in group and xf.get('待提交') and xf.get('仓库') == root and files & set(xf['待提交']):
                    group.append(x)
                    files |= set(xf['待提交'])
                    grew = True
        hosts.sync(root, 'svn', strict=True)                 # 提交前先 svn update（冲突就停下）
        live = {pth for _, pth in hosts.svn_changes(root)[0]}
        bad = [pth for st, pth in hosts.svn_changes(root)[0] if st in 'C!~' and pth in files]
        if bad:
            raise KBError(f'这些文件有冲突或丢失：{"、".join(bad)}；在工作副本里处理后再提交')
        todo = sorted(x for x in files if x in live)
        ids = [x['编号'] for x in group]
        if todo:
            msg = 'fix: ' + '；'.join(f'Bug {x["编号"]} {x["标题"][:40]}' for x in group)
            rev = hosts.svn_commit(root, todo, msg)
        else:
            rev, msg = '', ''
        for x in group:
            xf = x['修复']
            xf.pop('待提交', None)
            if rev:
                xf['提交'] = (xf.get('提交') or []) + [{'提交': f'r{rev}', '说明': msg}]
            _fx_log(x, (f'已提交到 svn r{rev}' if rev else '待提交的文件已经没有改动（可能已手动提交），清掉待提交标记') + f'（{by}）')
        zt_save(data)
    fix_queue_tick()                    # 工作副本干净了，排队的修复可以开始
    return (f'已提交到 svn r{rev}：Bug {"、".join(ids)}，{len(todo)} 个文件' if rev
            else f'Bug {"、".join(ids)} 的文件已经没有改动，清掉了待提交标记')


def bug_unfix(bid, by='用户（网页）'):
    data = zt_load()
    e = data['Bug'].get(str(bid))
    if not e or not e.get('修复'):
        raise KBError(f'Bug {bid} 没有修复标记')
    if e['修复'].get('状态') == '修复中':
        raise KBError(f'Bug {bid} 正在修复，等会话结束再撤销')
    queued = e['修复'].get('状态') == '排队中'
    e.pop('修复')
    a = e.get('分析') or {}
    if a.get('状态') == '无需审核':
        a['状态'] = a.pop('跳过前', '待审核')      # 撤销已修复：分析回到原来的审核状态
    zt_save(data)
    _fx_log(e, f'{"移出修复队列" if queued else "撤销修复标记"}（{by}）')
    return f'已把 Bug {bid} 移出修复队列' if queued else f'已撤销 Bug {bid} 的修复标记'
