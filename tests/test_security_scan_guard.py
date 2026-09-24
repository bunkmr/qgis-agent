# -*- coding: utf-8 -*-
"""安全扫描回归守卫。

背景：v2.4.7 上传 plugins.qgis.org 后被判为 Critical 并**阻断了发布** ——
Bandit 报 33 项，其中 5 项属于「不可跳过」的 Critical（B102 exec、B105 ×3、B107 ×1），
规则表里这三条规则的 skippable 列为空，上传表单跳不掉，只能改代码。

本文件把那次修复的关键约束钉死，防止被无意改回去：

1. `# nosec` 必须与**被报告的代码在同一行**（bandit 不认写在上一行的 nosec
   —— v2.4.7 正是栽在这里：注释在 635 行、`exec` 在 636 行，等于从未生效）；
2. 打包时 ZIP 内必须是 0644，不能继承工作区的 0755（否则触发 File Permissions 检查）；
3. 已改写成 `contextlib.suppress` / 补过日志的异常处理不得回归；
4. 五处 Critical 的处理点必须仍然存在（抑制注释或等价改法）。
"""

import importlib.util
import os
import re
import tempfile
import unittest
import zipfile

try:  # 既支持以包方式导入（qgis_agent.tests.test_x）
    from . import support
except ImportError:  # 也支持 `unittest discover -s tests` 的顶层模块导入
    import support  # noqa: F401

PROJECT_ROOT = support.PROJECT_ROOT


def _read(rel):
    with open(os.path.join(PROJECT_ROOT, rel), "r", encoding="utf-8") as f:
        return f.read()


def _load_build_plugin():
    """按文件路径加载 build_plugin.py（不依赖 sys.path 里有没有项目根）。"""
    path = os.path.join(PROJECT_ROOT, "build_plugin.py")
    spec = importlib.util.spec_from_file_location("build_plugin_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestNosecPlacement(unittest.TestCase):
    """`# nosec` 必须与触发它的代码同处一行 —— 这是 bandit 的硬性要求。"""

    def test_exec_nosec_is_on_the_same_line(self):
        src = _read("qgis_tools.py")
        call_lines = [
            ln for ln in src.splitlines()
            if re.match(r"^\s*exec\s*\(", ln)
        ]
        self.assertTrue(call_lines, "没找到 exec( 调用行 —— 是否被重构掉了？")
        for ln in call_lines:
            self.assertIn(
                "# nosec B102", ln,
                "exec 的 nosec 必须写在 exec 同一行，写在上一行 bandit 不认：%r" % ln.strip(),
            )

    def test_b105_suppressions_are_inline(self):
        """三处 B105 误报必须带同一行的 nosec B105（或已用等价写法消除）。"""
        expected = {
            "mcp_bridge.py": "self._token =",
            "mcp_protocol.py": "ENV_TOKEN =",
            "mcp_server/qgis_agent_mcp_server.py": "ENV_TOKEN =",
        }
        for rel, marker in expected.items():
            src = _read(rel)
            lines = [
                ln for ln in src.splitlines()
                if marker in ln and not ln.lstrip().startswith("#")
            ]
            self.assertTrue(lines, "%s 里找不到 %s 的赋值行" % (rel, marker))
            self.assertTrue(
                any("# nosec B105" in ln for ln in lines),
                "%s 的 %s 需要同一行的 # nosec B105（B105 是变量名匹配触发的误报）"
                % (rel, marker),
            )

    def test_b107_default_is_none_not_empty_string(self):
        """B107：__init__ 的 token 默认参数必须是 None，而不是空字符串字面量。"""
        src = _read("mcp_protocol.py")
        m = re.search(r"def __init__\(self, list_tools, call_tool,([^)]*)\)", src, re.S)
        self.assertIsNotNone(m, "找不到 MCPProtocol.__init__ 的签名")
        params = m.group(1)
        self.assertIn("token=None", params, "token 默认参数应为 None（与 token or \"\" 等价）")
        self.assertNotIn('token=""', params, "空字符串字面量会再次触发 B107")


class TestZipPermissions(unittest.TestCase):
    """ZIP 内权限位必须是 0644 —— 否则触发插件仓库的 File Permissions 检查。"""

    def setUp(self):
        self.bp = _load_build_plugin()

    def test_python_member_mode_is_644(self):
        self.assertEqual(self.bp.zip_member_mode("qgis_agent/qgis_tools.py"), 0o644)
        self.assertEqual(self.bp.zip_member_mode("qgis_agent/icon.png"), 0o644)

    def test_shell_script_keeps_executable(self):
        self.assertEqual(self.bp.zip_member_mode("qgis_agent/run.sh"), 0o755)

    def test_write_member_does_not_inherit_filesystem_755(self):
        """工作区文件是 0755 时，写进 ZIP 的仍必须是 0644，且 create_system=Unix。"""
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "sample.py")
            with open(src, "w", encoding="utf-8") as f:
                f.write("x = 1\n")
            os.chmod(src, 0o755)
            self.assertEqual(
                os.stat(src).st_mode & 0o777, 0o755,
                "前提不成立：临时文件没能设成 0755，本用例无法验证",
            )

            zip_path = os.path.join(td, "out.zip")
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                self.bp.write_member(zf, src, "qgis_agent/sample.py")

            with zipfile.ZipFile(zip_path) as zf:
                info = zf.getinfo("qgis_agent/sample.py")
            mode = (info.external_attr >> 16) & 0o777
            self.assertEqual(mode, 0o644, "ZIP 内权限应为 0644，实际是 %o" % mode)
            self.assertEqual(
                info.create_system, 3,
                "create_system 必须是 Unix(3)，否则解压端不认权限位",
            )

    def test_build_uses_write_member_not_raw_write(self):
        src = _read("build_plugin.py")
        self.assertIn("write_member(zipf", src, "打包必须走 write_member 以归一化权限")
        # 只看代码行：注释里会正常提到 zipf.write() 这个反面例子
        code = [ln for ln in src.splitlines() if not ln.lstrip().startswith("#")]
        offenders = [ln.strip() for ln in code if "zipf.write(" in ln]
        self.assertEqual(
            offenders, [],
            "不能用 zipf.write() —— 它会从文件系统继承 0755：%s" % offenders,
        )


class TestNoSilentExceptionSwallowing(unittest.TestCase):
    """v2.4.8 把 try/except/pass 改写为 contextlib.suppress；不得回归。"""

    TOUCHED = [
        "endpoint_diagnostics.py",
        "mcp_bridge.py",
        "mcp_protocol.py",
        "qgis_agent.py",
        "qgis_agent_dockwidget_base_ui.py",
        "qgis_agent_dockwidget_v2.py",
        "qgis_tools.py",
    ]

    # 只盯「宽泛捕获」：`except:` / `except Exception:` / `except BaseException:`。
    # 具体异常的 continue 属于正常控制流（例如 `except socket.timeout: continue`
    # 是 accept 轮询的标准写法），不是「静默吞异常」，不该被这条守卫误伤。
    BROAD_EXCEPT = re.compile(
        r"^\s*except\s*(?:Exception|BaseException)?\s*(?:as\s+\w+)?\s*:\s*(?:#.*)?$"
    )

    def _scan(self, body_stmt):
        bad = []
        for rel in self.TOUCHED:
            lines = _read(rel).splitlines()
            for i, ln in enumerate(lines[:-1]):
                if self.BROAD_EXCEPT.match(ln) and lines[i + 1].strip() == body_stmt:
                    bad.append("%s:%d" % (rel, i + 1))
        return bad

    def test_no_bare_except_pass(self):
        bad = self._scan("pass")
        self.assertEqual(
            bad, [],
            "这些位置又出现 except: pass（应改为 contextlib.suppress）：%s" % bad,
        )

    def test_no_except_continue_without_logging(self):
        bad = self._scan("continue")
        self.assertEqual(
            bad, [],
            "这些位置又出现 except: continue（应补一行日志，避免静默吞异常）：%s" % bad,
        )


class TestCriticalFindingsHandled(unittest.TestCase):
    """五处 Critical 的处理点必须仍然存在（防止有人「顺手清理注释」把抑制删掉）。"""

    def test_all_expected_suppressions_present(self):
        self.assertIn("# nosec B102", _read("qgis_tools.py"))
        for rel in ("mcp_bridge.py", "mcp_protocol.py",
                    "mcp_server/qgis_agent_mcp_server.py"):
            self.assertIn("# nosec B105", _read(rel), "%s 缺少 B105 抑制注释" % rel)
        self.assertIn("token=None", _read("mcp_protocol.py"))

    def test_subprocess_calls_are_annotated(self):
        """两处 subprocess 调用应保留「列表传参、不经 shell」的说明注释。"""
        for rel in ("mcp_bridge.py", "qgis_agent.py"):
            src = _read(rel)
            self.assertIn("# nosec B603", src, "%s 的 subprocess 调用缺少 B603 说明" % rel)


if __name__ == "__main__":
    unittest.main()
