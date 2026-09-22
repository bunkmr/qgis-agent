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


def describe_broken_dependency(module, exc, limit=300):
    """把「已安装但无法导入」的异常渲染成一句可读诊断。

    单独成函数是为了便于单测，也让 GUI 层不必关心异常类型判断。
    """
    name = type(exc).__name__
    msg = str(exc).strip() or name
    if len(msg) > limit:
        msg = msg[:limit].rstrip() + " …"
    return "• %s —— %s: %s" % (module, name, msg)


def broken_dependency_hint(broken):
    """针对 broken 列表给出人话版排查建议（不自动改用户环境）。"""
    text = " ".join("%s %s" % (m, e) for m, e in broken).lower()
    lines = []
    if "pydantic" in text and "incompatible" in text:
        # QGIS 自带 pydantic 与 pydantic-core 版本错配（官方安装包已知问题）。
        # 异常消息里带齐了两个版本号，直接解析出来给出可直接执行的命令。
        need = actual = None
        for _m, _e in broken:
            # 版本号只取数字与点，避免把句末的 "." 一起吞掉
            m = re.search(r"pydantic-core version \((\d+(?:\.\d+)*)\).*?"
                          r"requires (\d+(?:\.\d+)*)", str(_e), re.S)
            if m:
                actual, need = m.group(1), m.group(2)
                break
        lines.append("检测到 pydantic 与其底层 pydantic-core 版本不匹配"
                     + ("（需要 %s，实际 %s）" % (need, actual) if need else "") + "。")
        lines.append("这属于 Python 环境问题（常见于 QGIS 自带的包与其它 pip 安装"
                     "相互覆盖），并非插件缺陷。")
        lines.append("请勿直接重装插件依赖，可任选其一修复：")
        if need:
            lines.append('  · 在 QGIS 自带的 Python 中执行：'
                         'pip install --force-reinstall "pydantic-core==%s"' % need)
        else:
            lines.append('  · 在 QGIS 自带的 Python 中重装与 pydantic 配套的 pydantic-core')
        lines.append("  · 或改用独立的虚拟环境运行本插件")
    else:
        lines.append("上述模块可以找到，但导入过程报错，通常意味着安装被破坏"
                     "（版本错配、缺少动态库、权限问题等）。")
        lines.append("建议在终端里用同样的 Python 解释器手动 import 一次，"
                     "按报错信息重装对应包。")
    return "\n".join(lines)


class PackageManager:
    def __init__(self, required_modules):
        # 仅保留白名单内的模块，杜绝任何非预期依赖被安装
        self.required_modules = [m for m in required_modules if m in _ALLOWED_MODULES]
        self.missing = []   # 找不到的模块（ImportError）→ 可自动安装
        self.broken = []    # 找得到但导入即报错的模块 [(name, exc)] → 不可自动安装

    def check_dependencies(self):
        """探测依赖可用性，返回缺失（本轮真正没有的）模块名列表。

        除了返回 `missing`，还会填充 `self.broken`：模块存在、但导入时抛出了
        ImportError 之外的异常。这类情况**不能**当成"没装"去 pip install：
        例如 pydantic 与 pydantic-core 版本错配会抛 SystemError，此时重装
        上层包只会把环境改得更乱。

        之所以要单独分离出来，是因为历史上这里只捕获 ImportError，异常直接
        逃逸出 run()，表现为「插件启用后毫无反应、只有一条日志 Traceback」。
        """
        self.missing = []
        self.broken = []
        for module in self.required_modules:
            try:
                __import__(module)
            except ImportError:
                self.missing.append(module)
            except Exception as exc:  # noqa: BLE001
                logger.warning("依赖已安装但无法导入: %s (%s: %s)",
                               module, type(exc).__name__, exc)
                self.broken.append((module, exc))
        return self.missing

    def broken_report(self):
        """broken 列表的可读多行摘要（无内容时返回空串）。"""
        return "\n".join(describe_broken_dependency(m, e) for m, e in self.broken)

    def hint_text(self):
        """针对 broken 的排查建议（无内容时为空串）。"""
        return broken_dependency_hint(self.broken) if self.broken else ""

    def install_missing(self):
        """以编程方式安装缺失的依赖（使用当前解释器的 pip，无 subprocess / 无 shell）。

        只处理 `self.missing`。`self.broken`（已安装但坏掉的）不在其中，
        因此绝不会被重装。

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
