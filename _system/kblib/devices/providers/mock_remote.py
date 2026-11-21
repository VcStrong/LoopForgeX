"""模拟远程设备平台（REQ-043）：不接真平台，按设置里的设备清单返回固定假数据，结果带 模拟=True。
模拟行为：正常 / 故意失败（每一步都报「提供方不可用」）/ 故意慢（每一步睡几秒后报「超时」），用来跑异常路径用例。"""
import time

from kblib.devices.providers.base import Provider, ProviderError

DEFAULT_DEVICES = [{'序列号': 'MOCK-001', '型号': 'Mock Pixel 8', '系统': 'Android', '版本': '14', '品牌': 'Google'},
                   {'序列号': 'MOCK-002', '型号': 'Mock Mi 13', '系统': 'Android', '版本': '13', '品牌': 'Xiaomi'}]
# 1x1 透明 PNG
PNG = bytes.fromhex('89504e470d0a1a0a0000000d4948445200000001000000010806000000'
                    '1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082')


class MockRemote(Provider):
    KIND = 'remote'
    NAME = '模拟远程平台'
    MOCK = True

    def _gate(self, step):
        if self.behavior == '故意失败':
            raise ProviderError('提供方不可用', f'模拟平台故意失败（{step}）')
        if self.behavior == '故意慢':
            time.sleep(float(self.config.get('慢秒数') or 0.2))
            raise ProviderError('超时', f'模拟平台故意慢（{step}）')

    def list(self):
        return [dict(d, 提供方=self.id) for d in (self.config.get('设备') or DEFAULT_DEVICES)]

    def apply(self, cond, n, session):
        self._gate('申请')
        devs = self.list()
        if len(devs) < n:
            raise ProviderError('无空闲', f'模拟平台只有 {len(devs)} 台')
        return [dict(d, 模拟=True) for d in devs[:n]]

    def install(self, device, artifact_path):
        self._gate('安装')
        return {'退出码': 0, '输出': f'[模拟数据] 已安装 {artifact_path} 到 {device["序列号"]}', '模拟': True}

    def run(self, device, cmd):
        self._gate('执行')
        return {'退出码': 0, '输出': f'[模拟数据] {device["序列号"]} $ {cmd}\nOK', '模拟': True}

    def capture(self, device):
        self._gate('截图')
        return {'截图': PNG, '日志': f'[模拟数据] {device["序列号"]} logcat\nI/Mock: hello', '模拟': True}

    def release(self, device):
        return True
