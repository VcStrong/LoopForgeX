"""期页面「跟这一期说」附文件（ai/talk.py talk_attach / period_say(files=…)）的单测。

    cd _system && .venv/bin/python -m unittest tests.test_talk_attach -v     （没有 .venv 用 python3）

用临时 KB_ROOT、临时 HOME，不起真的 AI 会话（期在「收集中」时消息记进「需求」文字资料）。
"""
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix='kb-attach-test-'))
os.environ['KB_ROOT'] = str(TMP / 'kb')
os.environ['HOME'] = str(TMP / 'home')
(TMP / 'kb').mkdir()
(TMP / 'home').mkdir()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import kb  # noqa: E402
from kblib.ai import talk  # noqa: E402


def stage(name, body=b'hello', folder=None):
    """模拟网页上传到暂存区：返回暂存号。folder：{相对路径: 内容} 时传一个文件夹。"""
    sid = uuid.uuid4().hex
    d = talk.STAGE / sid
    if folder:
        for rel, data in folder.items():
            f = d / name / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(data)
    else:
        d.mkdir(parents=True)
        (d / name).write_bytes(body)
    return sid


class AttachTest(unittest.TestCase):
    def setUp(self):
        kb.init()
        self.name = f'期{self._testMethodName}'
        kb.create_period(self.name)
        self.d = kb.period_dir(self.name)

    def test_copy_into_period(self):
        a, b = stage('shot.png', b'png'), stage('logs', folder={'a.txt': b'1', 'sub/b.txt': b'2'})
        fs = talk.talk_attach(self.d, [a, b, 'not-an-id', uuid.uuid4().hex])
        self.assertEqual([f.name for f in fs], ['shot.png', 'logs'])
        self.assertTrue(all(talk.ATTACH_DIR in f.parts and str(f).startswith(str(self.d)) for f in fs))
        self.assertEqual(fs[0].read_bytes(), b'png')
        self.assertEqual((fs[1] / 'sub' / 'b.txt').read_bytes(), b'2')
        txt = talk._with_files('看下这个', fs)
        self.assertTrue(txt.startswith('看下这个\n\n附件'))
        self.assertIn(f'- {fs[0]}\n', txt + '\n')
        self.assertIn(f'- {fs[1]}/', txt)

    def test_say_with_files_only(self):
        sid = stage('需求截图.png', b'x')
        msg = kb.period_say(self.name, '', files=[sid])         # 收集中：记进「需求」文字资料
        self.assertIn('收集中', msg)
        notes = kb.notes_of(self.d, '需求')
        self.assertTrue(any('需求截图.png' in n.get('内容', '') and talk.ATTACH_DIR in n.get('内容', '') for n in notes))

    def test_empty_rejected(self):
        with self.assertRaises(kb.KBError):
            kb.period_say(self.name, '  ')
        with self.assertRaises(kb.KBError):
            kb.period_say(self.name, '', files=[uuid.uuid4().hex])     # 暂存号已失效：不发空消息


if __name__ == '__main__':
    unittest.main()
