"""本机 adb 连着的手机。"""
import subprocess

from kblib.devices.providers.base import Provider, ProviderError
from kblib.infra import devlock


class Local(Provider):
    KIND = 'local'
    NAME = '本机手机（adb）'

    def _adb(self):
        a = devlock.real_adb()
        if not a:
            raise ProviderError('提供方不可用', '本机找不到 adb')
        return a

    def list(self):
        try:
            a = self._adb()
        except ProviderError:
            return []
        return [{'序列号': sn, '型号': m, '系统': 'Android'} for sn, st, m in devlock.online(a) if st == 'device']

    def status(self):
        return '正常' if devlock.real_adb() else '不可用'

    def apply(self, cond, n, session):
        devs = self.list()
        if len(devs) < n:
            raise ProviderError('无空闲', f'本机只连着 {len(devs)} 台')
        return devs[:n]

    def _run(self, args, timeout=300):
        try:
            r = subprocess.run([self._adb(), *args], capture_output=True, text=True, errors='replace', timeout=timeout)
        except subprocess.TimeoutExpired:
            raise ProviderError('超时', 'adb ' + ' '.join(args[:3]))
        return {'退出码': r.returncode, '输出': (r.stdout + r.stderr)[-4000:]}

    def install(self, device, artifact_path):
        return self._run(['-s', device['序列号'], 'install', '-r', str(artifact_path)], 600)

    def run(self, device, cmd):
        return self._run(['-s', device['序列号'], 'shell', cmd])

    def capture(self, device):
        sn = device['序列号']
        try:
            png = subprocess.run([self._adb(), '-s', sn, 'exec-out', 'screencap', '-p'], capture_output=True, timeout=60).stdout
            log = subprocess.run([self._adb(), '-s', sn, 'logcat', '-d', '-t', '2000'], capture_output=True, text=True,
                                 errors='replace', timeout=60).stdout
        except subprocess.TimeoutExpired:
            raise ProviderError('超时', '截图 / 日志')
        return {'截图': png, '日志': log}
