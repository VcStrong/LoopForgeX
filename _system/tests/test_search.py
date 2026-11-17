"""知识库搜索（事项「知识库搜索支持模糊搜索」）的单测：同义词 / 错别字、拼音、相似词纠错、短词 + 过滤条件。

    cd _system && .venv/bin/python -m unittest tests.test_search -v

用临时 KB_ROOT，直接往索引里写块，不碰真实数据。没装 pypinyin / rapidfuzz 时对应用例跳过。
"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix='kb-search-test-'))
os.environ['KB_ROOT'] = str(TMP / 'kb')
(TMP / 'kb').mkdir()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kblib.core import fuzzy, search  # noqa: E402

DOCS = [
    ('需求.md', '用户登录页面：输入手机号和验证码完成登录。登录失败要提示。'),
    ('崩溃.md', '首页崩溃排查结论：列表为空时崩溃。崩溃率下降。'),
    ('支付.md', '支付宝支付流程，支付宝回调后刷新订单。支付宝签名校验。'),
    ('知识库.md', '知识库检索说明：知识库按期管理，知识库索引用 SQLite。'),
]
CODE = [('Login.kt', 'fun doLogin() { /* 登录 */ }'), ('Util.kt', 'val recieveData = 1 // 页面')]


def setUpModule():
    with search.db() as c:
        for i, (name, body) in enumerate(DOCS + CODE):
            kind = 'doc' if i < len(DOCS) else 'code'
            path = str(TMP / name)
            c.execute('insert into chunk(path,kind,period,cat,repo,start,end,content) values (?,?,?,?,?,?,?,?)',
                      (path, kind, 'P1' if kind == 'doc' else '', '需求' if kind == 'doc' else '', '' if kind == 'doc' else 'app',
                       1, 1, body))
            c.execute('insert into files values (?,?,?,?,?,?,?)', (path, 1.0, len(body), kind, '', '', ''))


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


def names(q, **kw):
    hits, mode = search.search_hits(q, **kw)
    return [Path(h['path']).name for h in hits], mode


class SynonymTest(unittest.TestCase):
    def test_whole_word(self):
        self.assertIn('登录', fuzzy.synonyms('登陆'))
        self.assertIn('崩溃', fuzzy.synonyms('闪退'))

    def test_longest_inner_word(self):
        self.assertEqual(fuzzy.synonyms('支付包回调')[:1], ['支付宝回调'])
        self.assertIn('登录页', fuzzy.synonyms('登陆页'))

    def test_user_file(self):
        fuzzy.USER_FILE.parent.mkdir(parents=True, exist_ok=True)
        fuzzy.USER_FILE.write_text('# 注释\n钱包 零钱包\n', encoding='utf-8')
        try:
            self.assertEqual(fuzzy.synonyms('零钱包'), ['钱包'])
        finally:
            fuzzy.USER_FILE.unlink()

    def test_search_typo_word(self):
        got, mode = names('登陆')
        self.assertIn('需求.md', got)
        self.assertIn('模糊', mode)
        got, mode = names('闪退', scope='docs')
        self.assertEqual(got, ['崩溃.md'])


class ShortTermFilterTest(unittest.TestCase):
    """2 字词 + 过滤条件（scope / period）以前参数顺序错位，永远搜不到。"""

    def test_scope_docs(self):
        self.assertEqual(names('登录', scope='docs')[0], ['需求.md'])

    def test_scope_code(self):
        self.assertEqual(names('登录', scope='code')[0], ['Login.kt'])

    def test_docs_first(self):
        got, mode = names('登录')
        self.assertEqual((got, mode), (['需求.md', 'Login.kt'], '全部词'))


@unittest.skipUnless(fuzzy.lazy_pinyin, '没装 pypinyin')
class PinyinTest(unittest.TestCase):
    def test_full_pinyin(self):
        got, mode = names('zhishiku')
        self.assertEqual(got[:1], ['知识库.md'])
        self.assertIn('zhishiku→知识库', mode)

    def test_abbr(self):
        got, _ = names('zsk', scope='docs')
        self.assertEqual(got, ['知识库.md'])

    def test_homophone_typo(self):
        with search.db() as c:
            fuzzy.ensure_vocab(c)
            self.assertIn('知识库', fuzzy.corrections(c, '知识酷'))


@unittest.skipUnless(fuzzy.process, '没装 rapidfuzz')
class CorrectionTest(unittest.TestCase):
    def test_shape_typo(self):
        got, mode = names('支付室', scope='docs')      # 字形 / 随手打错（不同音），且「支付」本身有同义词
        self.assertEqual(got, ['支付.md'])
        self.assertIn('支付室→支付宝', mode)

    def test_no_wild_guess(self):
        self.assertEqual(names('龘龘龘龘')[0], [])


if __name__ == '__main__':
    unittest.main()
