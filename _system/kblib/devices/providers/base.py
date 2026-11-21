"""设备提供方接口（REQ-043）：本机 adb、执行机上的手机、远程设备平台都实现这五步。

新增一类提供方只要在 providers/ 下加一个文件，定义 Provider 的子类并设 KIND；注册表按文件自动发现，
占用 / 排队 / 会话调用代码不用改（占用走资源池 core/resources.py，资源 id 形如 phone:<执行机或提供方>:<序列号>）。
"""


class ProviderError(Exception):
    """统一错误：kind ∈ 无空闲 / 超时 / 提供方不可用。"""
    KINDS = ('无空闲', '超时', '提供方不可用')

    def __init__(self, kind, msg=''):
        self.kind = kind if kind in self.KINDS else '提供方不可用'
        super().__init__(f'{self.kind}：{msg}' if msg else self.kind)


class Provider:
    KIND = ''           # local / runner / remote
    NAME = ''
    MOCK = False        # 模拟实现：报告上显著标「模拟数据」，不算进真机通过率

    def __init__(self, pid, config=None, behavior='正常'):
        self.id, self.config, self.behavior = pid, dict(config or {}), behavior or '正常'

    # 设备清单（登记进资源池用）：[{序列号, 型号, 系统, 版本, 品牌}]
    def list(self):
        return []

    def apply(self, cond, n, session):
        """申请：返回 n 台设备 [{序列号, ...}]；不够抛 ProviderError('无空闲')。"""
        raise NotImplementedError

    def install(self, device, artifact_path):
        raise NotImplementedError

    def run(self, device, cmd):
        """执行命令 → {退出码, 输出}"""
        raise NotImplementedError

    def capture(self, device):
        """截图 / 日志 → {截图: bytes 或路径, 日志: str}"""
        raise NotImplementedError

    def release(self, device):
        return True

    def status(self):
        return '正常'
