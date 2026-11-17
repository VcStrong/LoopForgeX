# From 大大王.DT
"""模糊搜索（查询侧）：同义词 / 常见错别字、拼音和首字母、相似词纠错。

不改索引：词表从文档块里抽（2～4 字中文词 + 英文词），带拼音缓存在 index.db 的 vocab 表，文档有变化才重算。
pypinyin / rapidfuzz 是可选依赖：没装时拼音和相似词纠错自动关掉，同义词照常可用。
"""
import re
from collections import Counter

from kblib.core.common import BASE

try:
    from pypinyin import Style, lazy_pinyin
except ImportError:          # pragma: no cover - 可选依赖
    lazy_pinyin = None
try:
    from rapidfuzz import process
    from rapidfuzz.distance import Levenshtein
except ImportError:          # pragma: no cover - 可选依赖
    process = Levenshtein = None

USER_FILE = BASE / '同义词.txt'
USER_HINT = '# 同义词 / 常见错别字：每行一组，词之间用空格、逗号或 = 隔开；搜其中任何一个词都会连带搜同组的词。\n'

# 内置词组：常见同义 / 近义说法和输入法错别字（第一个词是推荐写法）
BUILTIN = '''
登录 登陆 登入 登錄
退出登录 注销 登出
崩溃 闪退 crash
卡顿 卡死 卡住 无响应 ANR
支付 付款 付钱
支付宝 支付包
微信 威信
账号 帐号 账户 帐户
密码 口令
验证码 校验码
手机号 手机号码 电话号码
头像 头象
图片 图像 照片
视频 影片
消息 通知 推送
弹窗 弹框 对话框 弹出框
按钮 按键
列表 清单
详情 明细
搜索 查询 检索
删除 移除
修改 编辑 更改
新增 添加 新建 创建
设置 设定 配置
权限 授权
接口 API
需求 PRD
设计稿 设计图 UI稿 视觉稿
埋点 打点 上报
优惠券 优惠卷 代金券
充值 储值
提现 取现
订单 定单
会员 VIP
签到 打卡
分享 转发
评论 留言
举报 投诉
升级 更新
安装 装包
渠道 渠道包
'''

_CJK = re.compile(r'[一-鿿]')
_CJK_RUN = re.compile(r'[一-鿿]{2,}')
_WORD = re.compile(r'[A-Za-z][A-Za-z0-9_]{3,}')
_ALPHA = re.compile(r'^[a-z]+$')
VOCAB_MIN_FREQ = 3
VOCAB_MAX = 60000

_groups_cache = {}


def _split(line):
    return [w for w in re.split(r'[\s,，=、/|;；]+', line.strip()) if w]


def groups():
    """全部同义词组（内置 + 基础/同义词.txt），按用户文件 mtime 缓存。"""
    try:
        mt = USER_FILE.stat().st_mtime
    except OSError:
        mt = None
    if _groups_cache.get('mt', 0) == mt and 'g' in _groups_cache:
        return _groups_cache['g']
    lines = BUILTIN.splitlines()
    if mt is not None:
        try:
            lines += USER_FILE.read_text(encoding='utf-8').splitlines()
        except OSError:
            pass
    out = []
    for line in lines:
        if line.strip().startswith('#'):
            continue
        ws = list(dict.fromkeys(_split(line)))
        if len(ws) > 1:
            out.append(ws)
    _groups_cache.update(mt=mt, g=out)
    return out


def synonyms(term):
    """term 的同义写法（不含 term 本身）。整词同组 → 同组其他词；含组内词 → 替换成同组其他词（「登陆页」→「登录页」）。"""
    low = term.lower()
    out = [w for g in groups() if low in (x.lower() for x in g) for w in g]
    if not out:
        hits = [(w, g) for g in groups() for w in g if len(w) >= 2 and _CJK.search(w) and w in term]
        if hits:                           # 只替换最长的那个组内词，避免「支付包」被拆成「支付」+「包」
            w, g = max(hits, key=lambda x: len(x[0]))
            out = [term.replace(w, o) for o in g if o != w and _CJK.search(o)]
    return list(dict.fromkeys(x for x in out if x and x.lower() != low))


# ---------- 词表（拼音 / 纠错用） ----------

def _vocab_sig(c):
    r = c.execute("select count(*), coalesce(max(mtime),0), coalesce(sum(size),0) from files where kind='doc'").fetchone()
    return f'{r[0]}:{r[1]}:{r[2]}:{USER_FILE.stat().st_mtime if USER_FILE.exists() else 0}'


def _py(word):
    if not lazy_pinyin or not _CJK.search(word):
        return '', ''
    syl = lazy_pinyin(word, style=Style.NORMAL, errors='ignore')
    return ''.join(syl).lower(), ''.join(s[:1] for s in syl).lower()


def ensure_vocab(c):
    """文档有变化时重建词表；返回是否可用。"""
    c.execute('create table if not exists vocab(word text primary key, freq integer, py text, abbr text)')
    c.execute('create index if not exists vocab_py on vocab(py)')
    c.execute('create index if not exists vocab_abbr on vocab(abbr)')
    c.execute('create table if not exists kv(k text primary key, v text)')
    sig = _vocab_sig(c)
    old = c.execute("select v from kv where k='vocab_sig'").fetchone()
    if old and old[0] == sig:
        return True
    cnt = Counter()
    for (s,) in c.execute("select content from chunk where kind='doc'"):
        for run in _CJK_RUN.findall(s):
            for n in (2, 3, 4):
                for i in range(len(run) - n + 1):
                    cnt[run[i:i + n]] += 1
        for w in _WORD.findall(s):
            cnt[w.lower()] += 1
    words = [(w, f) for w, f in cnt.most_common(VOCAB_MAX) if f >= VOCAB_MIN_FREQ]
    have = {w for w, _ in words}
    for g in groups():                     # 同义词表里的词也进词表（拼音 / 纠错能落到它们）
        words += [(w, VOCAB_MIN_FREQ) for w in g if w not in have and _CJK.search(w)]
    c.execute('delete from vocab')
    c.executemany('insert or ignore into vocab values (?,?,?,?)', [(w, f, *_py(w)) for w, f in words])
    c.execute("insert or replace into kv values ('vocab_sig', ?)", (sig,))
    c.commit()
    return True


def pinyin_words(c, term, n=3):
    """纯字母输入 → 拼音 / 首字母对应的中文词（按词频）。"""
    t = term.lower()
    if not lazy_pinyin or not _ALPHA.match(t) or len(t) < 2:
        return []
    rows = c.execute('select word from vocab where py=? order by freq desc limit ?', (t, n)).fetchall()
    if not rows and len(t) <= 4:
        rows = c.execute('select word from vocab where abbr=? and length(word)=? order by freq desc limit ?',
                         (t, len(t), n)).fetchall()
    return [r[0] for r in rows]


def corrections(c, term, n=3):
    """错别字纠错：同音词优先（拼音输入法最常见），再按字形相似度（rapidfuzz）。只返回词表里有的词。"""
    out = []
    if _CJK.search(term):
        py, _ = _py(term)
        if py:
            out += [r[0] for r in c.execute('select word from vocab where py=? and word<>? order by freq desc limit ?',
                                            (py, term, n))]
        if process and len(term) >= 3:           # 中文错别字多是替换一个字：只比同长度的词，按编辑距离
            cands = [r[0] for r in c.execute('select word from vocab where length(word)=? order by freq desc', (len(term),))]
            out += [w for w, s, _ in process.extract(term, cands, scorer=Levenshtein.normalized_similarity,
                                                     limit=n, score_cutoff=0.6) if w != term]
    elif process and len(term) >= 4 and re.match(r'^[A-Za-z0-9_]+$', term):
        t = term.lower()
        cands = [r[0] for r in c.execute("select word from vocab where py='' and length(word) between ? and ?",
                                         (len(t) - 2, len(t) + 2))]
        out += [w for w, s, _ in process.extract(t, cands, scorer=Levenshtein.normalized_similarity, limit=n, score_cutoff=0.75) if w != t]
    return list(dict.fromkeys(out))[:n]
