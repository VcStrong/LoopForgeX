"""执行机上的手机：清单来自执行机心跳（资源池里 runner 不是 local 的手机），操作由执行机在本机 adb 完成（任务里带上手机）。"""
from kblib.core import db as _db
from kblib.devices.providers.base import Provider, ProviderError


class Runner(Provider):
    KIND = 'runner'
    NAME = '执行机上的手机'

    def list(self):
        out = []
        for r in _db.rows('resources', {'type': '手机', 'enabled': 1}):
            if r.get('runner') not in ('', 'local') and r.get('provider') == 'runner':
                out.append(dict(r.get('attrs') or {}, 执行机=r['runner']))
        return out

    def apply(self, cond, n, session):
        devs = self.list()
        if len(devs) < n:
            raise ProviderError('无空闲', f'执行机上只有 {len(devs)} 台在线')
        return devs[:n]

    def _remote(self, what):
        raise ProviderError('提供方不可用', f'{what}要在手机所在执行机上做：建「运行到手机 / 构建安装」任务派给它')

    def install(self, device, artifact_path):
        self._remote('装包')

    def run(self, device, cmd):
        self._remote('执行命令')

    def capture(self, device):
        self._remote('截图')
