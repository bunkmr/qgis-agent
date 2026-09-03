# -*- coding: utf-8 -*-
"""依赖管理：检测并安装插件运行所需的第三方库。

安全说明：
- 这里只安装**写死的白名单**依赖（见 QGIS Agent 传入的 required_modules），
  所有待安装模块名均来自插件代码内部、并非任何外部/用户输入，不存在命令注入风险。
- 安装使用当前解释器内的 `pip.main(...)` 以编程方式完成（不再调用 subprocess / shell），
  进一步避免任何"执行不可信输入"的隐患。
"""
import re
import logging
logger = logging.getLogger(__name__)


# 仅允许安装这份固定白名单内的依赖；与 qgis_agent.py 传入的 required_modules 保持一致。
# 任何不在此集合或名称非法的模块都会被跳过，绝不会被执行。
_ALLOWED_MODULES = {
    "langchain_core",
    "langchain_openai",
    "langchain_deepseek",
}
# 依赖名必须是合法的 PyPI 包标识符（不含空格 / shell 元字符等）。
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]*$")


class PackageManager:
    def __init__(self, required_modules):
        # 仅保留白名单内的模块，杜绝任何非预期依赖被安装
        self.required_modules = [m for m in required_modules if m in _ALLOWED_MODULES]
        self.missing = []

    def check_dependencies(self):
        self.missing = []
        for module in self.required_modules:
            try:
                __import__(module)
            except ImportError:
                self.missing.append(module)
        return self.missing

    def install_missing(self):
        """以编程方式安装缺失的依赖（使用当前解释器的 pip，无 subprocess / 无 shell）。

        返回 True 表示全部安装成功（或本就无需安装），False 表示有依赖安装失败。
        """
        if not self.missing:
            return True
        ok = True
        for module in self.missing:
            # 二次校验：模块名必须来自白名单且为合法标识符，否则跳过
            if module not in _ALLOWED_MODULES or not _SAFE_NAME.match(module):
                logger.warning("跳过非白名单/非法依赖，不予安装: %s", module)
                ok = False
                continue
            try:
                import pip  # 当前解释器内的 pip（QGIS 自带）

                rc = pip.main(["install", module, "--user", "--quiet"])
                if rc != 0:
                    logger.warning("自动安装依赖失败: %s (pip rc=%s)", module, rc)
                    ok = False
            except Exception as _e:
                logger.warning("自动安装依赖异常: %s (%s)", module, _e)
                ok = False
        self.missing = []
        return ok
