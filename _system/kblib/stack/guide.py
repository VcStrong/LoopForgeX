"""每一期的「下一步」：这一期走到哪一步、下一步谁做什么、点哪里。

页面顶部的步骤条直接用它；管家也按它判断哪一期卡在哪。只读，不调模型。
全栈流程：资料 → 选仓库 → 出方案 → 拍板 → 开发 → 验收。
新产品（还没有代码，见 stack/launch.py）：资料 → 完善需求 → 出方案 → 拍板 → 建工程 → 开发 → 验收 → 发布。
老的单端期（用开发面板「开始开发」、没走过全栈）：资料 → 开始开发 → 开发中 → 完成。
"""
from kblib.core.periods import is_task, load_meta, period_dir
from kblib.core.notes import notes_of
from kblib.core.notes import _materials
from kblib.ai.devsession import _alive
from kblib.stack.plan import period_ends
from kblib.stack import launch

_PLANNED = ('待拍板', '已拍板', '开发中', '各端完成', '已验收')
_APPROVED = ('已拍板', '开发中', '各端完成', '已验收')
_BUILT = ('各端完成', '已验收')


def _go(text, why='', who='你', button='', to='', at=''):
    # 锚点：板块里要滚到的元素 id（| 分隔，按顺序找第一个有的），没有就只滚到板块顶部
    g = {'文字': text, '说明': why, '谁': who, '按钮': button, '去': to}
    if at:
        g['锚点'] = at
    return g


def period_guide(name):
    """返回 {流程, 步骤: [{名称, 状态: done|cur|todo}], 下一步: {文字, 说明, 谁, 按钮, 去}}；事项返回 None。
    去：tab:<资料 mat / 开发 dev / 全栈 stack>，页面据此切到对应板块。"""
    d = period_dir(name)
    m = load_meta(d)
    if is_task(m):
        return None
    mats = _materials(d)
    req = len(mats.get('需求') or []) + len(notes_of(d, '需求', m))
    design = len(mats.get('设计图') or []) + len(notes_of(d, '设计图', m))
    st = m.get('状态', '')
    fs = (m.get('全栈') or {}).get('状态', '')
    ps = m.get('全栈会话') or {}
    planning = ps.get('状态') == '运行中' and _alive(ps.get('pid', 0))
    dev = m.get('开发会话') or {}
    dev_running = dev.get('状态') == '运行中' and _alive(dev.get('pid', 0))
    q = sum(1 for x in m.get('待确认') or [] if x.get('状态') == '待回答')
    no_design = '' if design else '还没有设计稿，有的话也放进来（Figma / 蓝湖链接或图片）。'
    single = not m.get('全栈') and bool(m.get('开发会话') or m.get('开发仓库'))
    newp = not single and launch.is_new_product(m)
    sess = m.get(launch.SESS) or {}
    busy = lambda k: (sess.get(k) or {}).get('状态') == '运行中' and _alive((sess.get(k) or {}).get('pid', 0))
    rel = m.get('发布') or {}

    if single:
        flow = '单端'
        done = [req > 0, st in ('开发中', '已完成') or bool(dev), st == '已完成', st == '已完成']
        names = ['资料', '开始开发', '开发中', '完成']
    elif newp:
        flow = '全栈'
        done = [req > 0, launch.prd_done(d, m), fs in _PLANNED, fs in _APPROVED, fs in _BUILT or fs == '开发中' or launch.build_done(d, m),
                fs in _BUILT, fs == '已验收', rel.get('状态') == '已上线' or (st == '已完成' and not rel)]
        names = ['资料', '完善需求', '出方案', '拍板', '建工程', '开发', '验收', '发布']
    else:
        flow = '全栈'
        ends = period_ends(d, m)
        done = [req > 0, bool(ends), fs in _PLANNED, fs in _APPROVED, fs in _BUILT, fs == '已验收' or st == '已完成']
        names = ['资料', '选仓库', '出方案', '拍板', '开发', '验收']
    cur = next((i for i, x in enumerate(done) if not x), len(done))
    steps = [{'名称': n, '状态': 'done' if i < cur or done[i] else 'cur' if i == cur else 'todo'} for i, n in enumerate(names)]

    if q:
        nxt = _go(f'AI 有 {q} 个问题等你回答', '不回答它会停在那里；不想被打扰可以开「🌙 免打扰」，它按自己的建议继续。',
                  button='去回答', to='tab:dev')
    elif st == '已暂停':
        nxt = _go('这一期已暂停', '要继续就把右上角状态改回「开发中」。', button='')
    elif cur >= len(names):
        nxt = _go('这一期已经完成 🎉', '后面发现的问题在最上面的输入框里说，或者到 Bug 板块看。', who='')
    elif names[cur] == '资料':
        nxt = _go('先把需求放进来', ('一句想法也行，后面 AI 会追问细节。' if newp else '粘贴需求链接、文档，或者产品说的话，AI 会自动整理归类。') + no_design,
                  button='去放资料', to='tab:mat')
    elif names[cur] == '完善需求':
        nxt = _go('AI 正在追问需求、写 PRD', '它会在「开发 → 待确认」里一条条问你目标用户、核心场景、MVP 范围、不做什么。', who='AI', button='看进度', to='tab:stack') \
            if busy('需求') else _go('把想法打磨成 PRD', '在「全栈」点「💬 完善需求」，AI 一条条问你，问完写成 需求/PRD.md；需求已经很清楚可以点「跳过」。',
                                     button='去完善需求', to='tab:stack')
    elif names[cur] == '建工程':
        nxt = _go('AI 正在生成脚手架', '各端工程建好、脚手架提交后就可以开工。', who='AI', button='看进度', to='tab:stack') \
            if any(x.get('状态') == '生成脚手架中' for x in (m.get('建工程') or {}).values()) else \
            _go('给各端新建工程', '在「全栈」各端那一行点「🛠 新建工程」，选个父目录，知识库建目录、git init、按方案生成脚手架并首次提交；已有代码的端直接选仓库。',
                button='去建工程', to='tab:stack')
    elif names[cur] == '发布':
        if busy('发布'):
            nxt = _go('AI 正在列发布清单', '按端列出部署、域名 / HTTPS、打包签名、上架资料，缺的账号证书会记成问题。', who='AI', button='看进度', to='tab:stack')
        elif rel.get('清单'):
            left = sum(1 for x in rel['清单'] if not x.get('完成'))
            nxt = _go(f'发布清单还有 {left} 项没做完', '逐项做完在「全栈 → 发布」里勾上，全勾完这一期就算上线。', button='去发布', to='tab:stack')
        else:
            nxt = _go('验收通过，准备发布', '在「全栈」点「📦 出发布清单」，AI 按端列出上线前要做的事。', button='去发布', to='tab:stack')
    elif flow == '单端':
        if names[cur] == '开始开发':
            nxt = _go('资料齐了就开始开发', '在「开发」里写上代码路径和分支，点「▶ 开始开发」。' + no_design, button='去开始开发', to='tab:dev')
        else:
            nxt = _go('AI 正在开发' if dev_running else '开发会话停了', '有问题会在「开发」里问你。' if dev_running else '去「开发」看看停在哪，接着做或改状态。',
                      who='AI' if dev_running else '你', button='看进度', to='tab:dev')
    elif names[cur] == '选仓库':
        nxt = _go('给涉及的端选代码仓库', '在「全栈」里给每个要改的端（服务端、Android、iOS…）选仓库和分支；不涉及的端留空。'
                  '全新产品还没有代码？点「🌱 这是新产品」，从完善需求开始。' + no_design,
                  button='去选仓库', to='tab:stack')
    elif names[cur] == '出方案':
        nxt = _go('AI 正在出方案，好了会通知你', '方案包括需求规格、设计稿对照、各端改动和接口契约。', who='AI', button='看进度', to='tab:stack') if planning else \
            _go('让 AI 出方案', '在「全栈」点「🧭 出方案」。' + ('还没有代码也行：AI 会建议要哪些端、各端技术栈、目录结构和接口契约。' if newp else '') + no_design,
                button='去出方案', to='tab:stack')
    elif names[cur] == '拍板':
        qs = launch.plan_questions(d)
        left = sum(1 for x in qs if not x.get('回答'))
        nxt = _go(f'方案出好了，有 {len(qs)} 个问题要你确认' + (f'（还剩 {left} 个）' if left and left < len(qs) else ''),
                  '在「全栈」拍板按钮上方逐条选「按建议」或写你的意见；都按建议就直接拍板，写了意见的点「按我的回答重出方案」。',
                  button='去确认', to='tab:stack') if qs else \
            _go('方案出好了，等你拍板', '看一遍需求规格、设计对照和接口契约，没问题点「✓ 拍板」；不对就写意见重出。', button='去拍板', to='tab:stack')
    elif names[cur] == '开发':
        nxt = _go('各端正在开发', 'AI 互相审查，有问题会停下来问你。', who='AI', button='看进度', to='tab:stack') if fs == '开发中' else \
            _go('方案已拍板，点开工', '在「全栈」点「▶ 各端开工」，各端并行开发。', button='去开工', to='tab:stack')
    elif names[cur] == '验收' and newp:
        nxt = _go('各端做完了，等你验收', '看证据包，点「✓ 验收」后推送 / 提交；验收后还有「发布」一步。', button='去验收', to='tab:stack', at='stAcc|stEv')
    else:
        nxt = _go('各端做完了，等你验收', '看证据包（每条需求在各端的完成度和审查结论），点「✓ 验收」后推送 / 提交。', button='去验收', to='tab:stack', at='stAcc|stEv')
    return {'流程': flow, '步骤': steps, '下一步': nxt}
