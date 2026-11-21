"""执行机 → 控制面的 HTTP 客户端：只出站；https 支持自签证书（--ca）；每次请求记延迟和成败（网络指标，REQ-035）。"""
import json
import ssl
import time
import urllib.error
import urllib.request
import uuid
from collections import deque
from threading import Lock


class ApiError(Exception):
    """控制面返回了 4xx / 5xx。"""

    def __init__(self, status, body):
        self.status, self.body = status, body or {}
        super().__init__(f'HTTP {status}：{self.body.get("error") or self.body}')


class NetError(Exception):
    """连不上控制面（网络断、控制面挂了）。"""


class NetStats:
    """滑动 5 分钟窗口：目标 → [(时间, 延迟毫秒, 是否成功)]。"""
    WINDOW = 300

    def __init__(self):
        self._d, self._lock = {}, Lock()

    def add(self, target, ms, ok):
        with self._lock:
            q = self._d.setdefault(target, deque())
            q.append((time.time(), None if ms is None else int(ms), bool(ok)))
            while q and q[0][0] < time.time() - self.WINDOW:
                q.popleft()

    def report(self):
        out = []
        with self._lock:
            for t, q in self._d.items():
                if not q:
                    continue
                oks = [ms for _, ms, ok in q if ok and ms is not None]
                out.append({'目标': t, '延迟ms': int(sum(oks) / len(oks)) if oks else 0,
                            '失败率': round(sum(1 for *_, ok in q if not ok) / len(q), 3)})
        return out


class Client:
    def __init__(self, server, token='', ca='', stats=None):
        self.server = server.rstrip('/')
        self.token = token
        self.stats = stats or NetStats()
        self.ctx = None
        if self.server.startswith('https://'):
            self.ctx = ssl.create_default_context(cafile=ca or None)

    def _req(self, path, data=None, headers=None, timeout=30, token=None):
        h = {'User-Agent': 'kb-runner'}
        tok = self.token if token is None else token
        if tok:
            h['Authorization'] = f'Bearer {tok}'
        h.update(headers or {})
        req = urllib.request.Request(self.server + path, data=data, headers=h, method='POST' if data is not None else 'GET')
        t0 = time.time()
        longpoll = path.startswith('/api/runner/claim')       # 长轮询要等活最多 50 秒：只记成败，不算延迟（否则误报「网络差」）

        def ms():
            return None if longpoll else (time.time() - t0) * 1000
        try:
            r = urllib.request.urlopen(req, timeout=timeout, context=self.ctx)
            self.stats.add('控制面', ms(), True)
            return r
        except urllib.error.HTTPError as e:
            self.stats.add('控制面', ms(), e.code < 500)
            try:
                body = json.loads(e.read() or b'{}')
            except ValueError:
                body = {}
            raise ApiError(e.code, body) from None
        except (urllib.error.URLError, OSError) as e:
            self.stats.add('控制面', ms(), False)
            raise NetError(str(getattr(e, 'reason', e))) from None

    def post(self, path, body=None, timeout=30, token=None):
        data = json.dumps(body or {}, ensure_ascii=False).encode()
        with self._req(path, data, {'Content-Type': 'application/json'}, timeout, token) as r:
            raw = r.read()
        try:
            return json.loads(raw or b'{}')
        except ValueError:
            return {}

    def post_multipart(self, path, fields, file_path=None, file_field='file', timeout=300):
        """multipart/form-data：fields 是文本字段，file_path 是要上传的文件（产物、截图、logcat）。"""
        b = uuid.uuid4().hex
        parts = []
        for k, v in fields.items():
            parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
        if file_path:
            from pathlib import Path
            p = Path(file_path)
            parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{p.name}"\r\n'
                         f'Content-Type: application/octet-stream\r\n\r\n'.encode() + p.read_bytes() + b'\r\n')
        parts.append(f'--{b}--\r\n'.encode())
        with self._req(path, b''.join(parts), {'Content-Type': f'multipart/form-data; boundary={b}'}, timeout) as r:
            raw = r.read()
        try:
            return json.loads(raw or b'{}')
        except ValueError:
            return {}

    def download(self, path, dest, timeout=600):
        with self._req(path, None, None, timeout) as r, open(dest, 'wb') as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
        return dest
