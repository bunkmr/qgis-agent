# -*- coding: utf-8 -*-
"""逐要素出图与「轮次上限」相关的回归守卫（v2.4.10）。

背景（用户报障复现链）：
  用户问「根据现在的配图，对支局调整图层中的要素逐个在 B0 号图上输出图片，
  配置指北针和比例尺」，插件两次都答「无法完成」，原因有二：
    1. ``max_tool_rounds = 10`` 对多步制图任务太小，模型在「几步探查 + 一次
       失败重试」后就被强制总结，只能回一句「轮次已达上限」；
    2. 工具集里没有逐要素出图工具，模型只能裸写 execute_pyqgis 拼打印布局，
       而它写的 ``QgsLayoutItemNorthArrow`` 在 QGIS 3.44 / 4.x 中**已被移除**
       （实测两版均不存在），必然报错，于是重写、再报错，把轮次烧光。

因此这里钉住四件事：
  A. 逐要素出图必须由专用工具 ``export_features_maps`` 承担，且注册齐全；
  B. 源码中不得再出现已被移除的 ``QgsLayoutItemNorthArrow``；
  C. 指北针必须走现行做法（QgsLayoutItemPicture + setNorthMode(TrueNorth)）；
  D. 轮次上限不得退回个位数。
"""

import os
import re
import tempfile
import unittest
from unittest import mock

try:  # 既支持以包方式导入（qgis_agent.tests.test_x）
    from . import support
except ImportError:  # 也支持 `unittest discover -s tests` 的顶层模块导入
    import support  # noqa: F401

support.install_qgis_stub()

PROJECT_ROOT = support.PROJECT_ROOT


def _read(rel):
    with open(os.path.join(PROJECT_ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def _load_qgis_tools():
    return support.import_mod("qgis_tools")


QT = _load_qgis_tools()


# ────────────────────────────────────────────────
# A. 文件名净化
# ────────────────────────────────────────────────

class TestSafeFilename(unittest.TestCase):
    """要素属性是不可信外部数据，拼进输出路径前必须净化。"""

    def test_chinese_name_kept(self):
        self.assertEqual(QT._safe_filename("支局01"), "支局01")

    def test_path_separators_replaced(self):
        out = QT._safe_filename("../../etc/passwd")
        self.assertNotIn("/", out)
        self.assertFalse(out.startswith("."),
                         "不得以点开头（会被当作隐藏/相对路径）")
        self.assertNotIn("\\", QT._safe_filename("a\\b\\c"))
        # 净化结果必须是一个纯文件名：basename 等于自身
        for raw in ("../../etc/passwd", "a/b/c", "..\\..\\win.ini", "/abs/path"):
            s = QT._safe_filename(raw)
            self.assertEqual(os.path.basename(s), s,
                             "净化后仍是路径：%r -> %r" % (raw, s))

    def test_windows_forbidden_chars_replaced(self):
        out = QT._safe_filename('a:b*c?d"e<f>g|h')
        for ch in ':*?"<>|':
            self.assertNotIn(ch, out)

    def test_control_chars_replaced(self):
        out = QT._safe_filename("a\x00b\x01c\x1fd")
        self.assertNotIn("\x00", out)
        self.assertNotIn("\x01", out)

    def test_empty_become_placeholder(self):
        for raw in ("", "   ", None, "...", "..."):
            self.assertEqual(QT._safe_filename(raw), "feature")

    def test_reserved_device_names_prefixed(self):
        # con/prn/aux/nul 在 Windows 上无法作为文件名
        for name in ("con", "PRN", "aux", "NUL", "com1", "LPT9"):
            self.assertTrue(QT._safe_filename(name).startswith("_"))

    def test_length_capped(self):
        self.assertLessEqual(len(QT._safe_filename("很长的名字" * 100)), 80)

    def test_numeric_values(self):
        self.assertEqual(QT._safe_filename(123), "123")


# ────────────────────────────────────────────────
# B. 指北针 SVG 查找（跨平台 + 容错）
# ────────────────────────────────────────────────

class TestNorthArrowLookup(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="qgis_arrow_")

    def _make_arrows(self, base, name="NorthArrow_01.svg"):
        d = os.path.join(base, "svg", "arrows")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
            fh.write("<svg/>")
        return os.path.join(d, name)

    def test_found_via_prefix_upward_search(self):
        """macOS bundle 布局：prefix 指向 Contents/MacOS，需上溯找到
        Contents/Resources/qgis/svg/arrows —— 官方 svgPaths() 在这种布局下
        会返回带重复段的无效路径，必须靠上溯兜底。"""
        app = os.path.join(self.tmp, "QGIS.app", "Contents")
        expected = self._make_arrows(os.path.join(app, "Resources", "qgis"))
        env = {
            "QGIS_PREFIX_PATH": os.path.join(app, "MacOS"),
            "QGIS_APP": os.path.join(self.tmp, "QGIS.app"),
            "QGIS_PLUGINPATH": "",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            with mock.patch.object(QT.QgsApplication, "svgPaths",
                                   staticmethod(lambda: ["/nonexistent/svg/"])):
                with mock.patch.object(QT.QgsApplication, "prefixPath",
                                       staticmethod(lambda: os.path.join(app, "MacOS"))):
                    got = QT._find_north_arrow_svg()
        self.assertEqual(got, expected)

    def test_returns_none_when_absent(self):
        env = {"QGIS_PREFIX_PATH": self.tmp, "QGIS_APP": "", "QGIS_PLUGINPATH": ""}
        with mock.patch.dict(os.environ, env, clear=False):
            self.assertIsNone(QT._find_north_arrow_svg())

    def test_tolerates_non_string_api_returns(self):
        """发行版/替身环境下这些 API 可能返回非字符串，不得抛 TypeError。"""
        with mock.patch.object(QT.QgsApplication, "svgPaths",
                               staticmethod(lambda: [None, 42, object()])):
            with mock.patch.object(QT.QgsApplication, "prefixPath",
                                   staticmethod(lambda: object())):
                with mock.patch.object(QT.QgsApplication, "pkgDataPath",
                                       staticmethod(lambda: 12345)):
                    self.assertIsNone(QT._find_north_arrow_svg())


# ────────────────────────────────────────────────
# C. 不得使用已被移除的 API
# ────────────────────────────────────────────────

class TestRemovedApiNotUsed(unittest.TestCase):

    def test_north_arrow_class_absent_from_code(self):
        """QgsLayoutItemNorthArrow 在 QGIS 3.44 / 4.x 已被移除。

        允许在注释/文档字符串里作为「反面例子」出现（有助于后来者理解为什么
        不用它），但**不得**出现在任何可执行的 import / 名字引用里 —— 用 AST
        判定，避免把说明文字误判成代码。
        """
        import ast as _ast
        tree = _ast.parse(_read("qgis_tools.py"))
        offenders = []
        for node in _ast.walk(tree):
            if isinstance(node, _ast.ImportFrom):
                for a in node.names:
                    if a.name == "QgsLayoutItemNorthArrow":
                        offenders.append("line %s: from %s import %s"
                                         % (node.lineno, node.module, a.name))
            elif isinstance(node, _ast.Import):
                for a in node.names:
                    if a.name.endswith("QgsLayoutItemNorthArrow"):
                        offenders.append("line %s: import %s" % (node.lineno, a.name))
            elif isinstance(node, _ast.Name) and node.id == "QgsLayoutItemNorthArrow":
                offenders.append("line %s: 名字引用" % node.lineno)
            elif isinstance(node, _ast.Attribute) and node.attr == "QgsLayoutItemNorthArrow":
                offenders.append("line %s: 属性引用" % node.lineno)
        self.assertEqual(
            offenders, [],
            "qgis_tools.py 仍在使用已被移除的 API（QGIS 3.44/4.x 均不存在，"
            "会直接报错）：" + "; ".join(offenders),
        )

    def test_uses_picture_setnorthmode(self):
        src = _read("qgis_tools.py")
        self.assertIn("setNorthMode", src, "指北针必须走 QgsLayoutItemPicture.setNorthMode")
        self.assertIn("QgsLayoutItemPicture.TrueNorth", src,
                      "枚举必须取自 QgsLayoutItemPicture（用 Handler.NorthMode 会 TypeError）")

    def test_scalebar_recomputed_after_extent(self):
        """比例尺长度依赖地图当前比例尺，必须在 setExtent 之后重算，
        否则算出来是 0（图上只显示一个「0」）。"""
        src = _read("qgis_tools.py")
        idx_extent = src.index("map_item.setExtent(rect)")
        idx_apply = src.index("applyDefaultSize(int(map_w")
        self.assertGreater(idx_apply, idx_extent,
                           "applyDefaultSize 必须在 setExtent 之后调用")


# ────────────────────────────────────────────────
# D. 工具注册与参数校验
# ────────────────────────────────────────────────

class TestToolRegistration(unittest.TestCase):

    def _definition(self, name):
        for d in QT.TOOL_DEFINITIONS:
            if d.get("name") == name:
                return d
        return None

    def test_in_tool_definitions(self):
        self.assertIsNotNone(self._definition("export_features_maps"))

    def test_in_tool_map(self):
        self.assertIn("export_features_maps", QT.TOOL_MAP)
        self.assertTrue(callable(QT.TOOL_MAP["export_features_maps"]))

    def test_schema_required_and_optional(self):
        d = self._definition("export_features_maps")
        props = d["parameters"]["properties"]
        self.assertEqual(sorted(d["parameters"]["required"]), ["layer", "output_dir"])
        for key in ("name_field", "width_px", "height_px", "dpi",
                    "margin_percent", "north_arrow", "scale_bar", "limit",
                    "page_size", "orientation"):
            self.assertIn(key, props, "缺少参数 %s" % key)
        # 标准图纸尺寸（用户说「按 B0 号图出图」时会用到）
        self.assertIn("B0", props["page_size"]["description"])

    def test_description_steers_away_from_raw_pyqgis(self):
        d = self._definition("export_features_maps")
        desc = d["description"]
        self.assertIn("不要用 execute_pyqgis", desc,
                      "描述必须明确阻止模型手写布局代码（那正是报障根因）")
        self.assertIn("指北针", desc)
        self.assertIn("比例尺", desc)


class TestParameterValidation(unittest.TestCase):
    """参数校验必须在主线程真正建布局之前完成，且错误信息可读。"""

    def test_empty_output_dir_rejected(self):
        r = QT.export_features_maps(layer="任何图层", output_dir="")
        self.assertIn("error", r)
        self.assertIn("output_dir", r["error"])

    def test_relative_output_dir_rejected(self):
        r = QT.export_features_maps(layer="任何图层", output_dir="out/rel")
        self.assertIn("error", r)
        self.assertIn("绝对路径", r["error"])

    def test_creates_missing_output_dir(self):
        target = os.path.join(tempfile.mkdtemp(prefix="eff_"), "nested", "out")
        # 目录会被创建；随后因找不到图层而返回 error（stub 环境无图层）
        r = QT.export_features_maps(layer="不存在的图层", output_dir=target)
        self.assertTrue(os.path.isdir(target), "output_dir 不存在应自动创建")
        self.assertIn("error", r)

    def test_missing_layer_lists_available(self):
        target = tempfile.mkdtemp(prefix="eff_")
        r = QT.export_features_maps(layer="", output_dir=target)
        self.assertIn("error", r)
        self.assertIn("available_layers", r)


# ────────────────────────────────────────────────
# E. 轮次上限（报障直接原因）
# ────────────────────────────────────────────────

class TestAgentLoopBudget(unittest.TestCase):

    def test_max_tool_rounds_raised(self):
        src = _read("processor.py")
        m = re.search(r"self\.max_tool_rounds\s*=\s*(\d+)", src)
        self.assertIsNotNone(m, "未找到 max_tool_rounds 赋值")
        rounds = int(m.group(1))
        self.assertGreaterEqual(
            rounds, 20,
            "轮次上限 %d 过小：多步制图任务（探查→建布局→逐要素导出）会在半途"
            "被强制总结，用户看到「工具调用轮次已达上限，无法完成」" % rounds,
        )

    def test_loop_uses_the_budget(self):
        src = _read("processor.py")
        self.assertIn("range(self.max_tool_rounds)", src,
                      "循环必须真正使用 max_tool_rounds 作为预算")


if __name__ == "__main__":
    unittest.main()
