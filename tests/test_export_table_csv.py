# -*- coding: utf-8 -*-
"""export_table_to_csv 的回归守卫（v2.4.12）。

现场故障（用户报障原文）：
    用户：「帮我将统计的表升成 csv 格式数据保存到桌面」
    插件：❌ 连续 10 次尝试 execute_pyqgis 均因同一原因失败 ——
          「禁止导入模块 csv」「禁止调用 open 函数」，最后只能建议用户自己去
          图形界面手动导出。

真因不是模型写错，而是**工具供给缺失**：沙箱按安全设计禁掉了 ``csv`` 与 ``open``
（这是对的，不能为方便而放松），但「把结果存成表格文件」是最高频的需求之一，
必须由专用工具承担（铁律 9），而不是指望模型在受限环境里裸写文件 IO。

这里钉住：
  A. 两种模式（rows 表格 / layer 属性表）都能落盘，且内容可被标准 csv 读回；
  B. 中文在 Windows Excel 下可直接读 → 必须带 UTF-8 BOM；
  C. 路径安全（系统目录拒绝、相对路径落桌面、扩展名校验）与覆盖确认（无确认通道即拒绝）；
  D. CSV 注入防护（属性值属不可信输入）；
  E. 被沙箱拒绝时，hint 必须点名 export_table_to_csv（跨文件约定对齐）。
"""

import csv
import os
import tempfile
import unittest
from unittest import mock

try:
    from . import support
except ImportError:
    import support  # noqa: F401

support.install_qgis_stub()

PROJECT_ROOT = support.PROJECT_ROOT
QT = support.import_mod("qgis_tools")
VECTOR = QT.QgsMapLayer.LayerType.VectorLayer


def _read(rel):
    with open(os.path.join(PROJECT_ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def _read_csv(path):
    """按 utf-8-sig 读回（BOM 会被自动吞掉，等价于 Excel 的行为）"""
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.reader(fh))


def _raw_head(path, n=3):
    with open(path, "rb") as fh:
        return fh.read(n)


# ────────────────────────────────────────────────
# 替身
# ────────────────────────────────────────────────

class _FakeField:
    def __init__(self, name):
        self._name = name

    def name(self):
        return self._name


class _FakeFeature:
    def __init__(self, values):
        self._values = values

    def attribute(self, name):
        return self._values.get(name)

    def __getitem__(self, name):
        return self._values.get(name)


class _FakeLayer:
    def __init__(self, name, fields=(), rows=(), selected=None):
        self._name = name
        self._fields = [_FakeField(f) for f in fields]
        self._rows = list(rows)
        self._selected = list(selected) if selected is not None else list(rows)

    def name(self):
        return self._name

    def id(self):
        return "id_" + self._name

    def type(self):
        return VECTOR

    def fields(self):
        return self._fields

    def getFeatures(self):
        return iter(_FakeFeature(dict(r)) for r in self._rows)

    def selectedFeatures(self):
        return iter(_FakeFeature(dict(r)) for r in self._selected)


class _FakeProject:
    def __init__(self, layers):
        self._layers = {lyr.id(): lyr for lyr in layers}

    def mapLayer(self, layer_id):
        return self._layers.get(layer_id)

    def mapLayers(self):
        return dict(self._layers)


def _project_stub(project):
    class _Stub:
        @staticmethod
        def instance():
            return project

    return _Stub


class _CsvCase(unittest.TestCase):
    """公共环境：临时 HOME（桌面必须是可控的）+ 关闭确认通道"""

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="csv_home_")
        os.makedirs(os.path.join(self.home, "Desktop"), exist_ok=True)
        env = mock.patch.dict(os.environ, {"HOME": self.home})
        env.start()
        self.addCleanup(env.stop)

        prev_cb = QT._code_confirm_callback
        prev_skip = QT.get_skip_all_confirms()
        QT.set_code_confirm_callback(None)
        QT.set_skip_all_confirms(False)
        self.addCleanup(lambda: QT.set_code_confirm_callback(prev_cb))
        self.addCleanup(lambda: QT.set_skip_all_confirms(prev_skip))

    def path(self, name):
        return os.path.join(self.home, "Desktop", name)


# ────────────────────────────────────────────────
# A. rows 模式
# ────────────────────────────────────────────────

class TestRowsMode(_CsvCase):

    def test_dict_rows_write_header_and_values(self):
        out = self.path("统计.csv")
        r = QT.export_table_to_csv(
            rows=[{"社区": "顺龙社区", "面积km2": 37.97},
                  {"社区": "箐口社区", "面积km2": 123.38}],
            output_path=out)
        self.assertEqual(r["path"], out)
        self.assertEqual(r["rows"], 2)
        self.assertGreater(r["bytes"], 0)
        self.assertEqual(_read_csv(out),
                         [["社区", "面积km2"], ["顺龙社区", "37.97"], ["箐口社区", "123.38"]])

    def test_union_of_keys_keeps_first_seen_order(self):
        out = self.path("union.csv")
        QT.export_table_to_csv(rows=[{"a": 1}, {"b": 2, "a": 3}], output_path=out)
        self.assertEqual(_read_csv(out), [["a", "b"], ["1", ""], ["3", "2"]])

    def test_list_rows_with_string_header(self):
        out = self.path("matrix.csv")
        QT.export_table_to_csv(
            rows=[["社区", "面积"], ["A", 1.5], ["B", 2]],
            output_path=out)
        self.assertEqual(_read_csv(out), [["社区", "面积"], ["A", "1.5"], ["B", "2"]])

    def test_list_rows_without_header_gets_generated_columns(self):
        out = self.path("noheader.csv")
        QT.export_table_to_csv(rows=[[1, 2], [3, 4]], output_path=out)
        self.assertEqual(_read_csv(out), [["列1", "列2"], ["1", "2"], ["3", "4"]])

    def test_columns_select_and_order(self):
        out = self.path("order.csv")
        QT.export_table_to_csv(
            rows=[{"社区": "A", "面积": 1, "编码": "530581"}],
            columns=["编码", "社区"],
            output_path=out)
        self.assertEqual(_read_csv(out), [["编码", "社区"], ["530581", "A"]])

    def test_limit_caps_rows_and_flags_truncation(self):
        out = self.path("limited.csv")
        r = QT.export_table_to_csv(
            rows=[{"i": i} for i in range(10)], limit=3, output_path=out)
        self.assertEqual(r["rows"], 3)
        self.assertTrue(r.get("truncated"))

    def test_empty_rows_is_an_error(self):
        r = QT.export_table_to_csv(rows=[], output_path=self.path("x.csv"))
        self.assertIn("error", r)

    def test_no_input_is_an_error_with_hint(self):
        r = QT.export_table_to_csv()
        self.assertIn("error", r)
        self.assertIn("hint", r)


# ────────────────────────────────────────────────
# B. 编码：Windows Excel 下载即读
# ────────────────────────────────────────────────

class TestEncoding(_CsvCase):

    def test_utf8_bom_present(self):
        out = self.path("bom.csv")
        QT.export_table_to_csv(rows=[{"社区": "昆明"}], output_path=out)
        self.assertEqual(_raw_head(out), b"\xef\xbb\xbf",
                         "缺少 UTF-8 BOM，Windows 版 Excel 打开会中文乱码")

    def test_reported_encoding(self):
        out = self.path("enc.csv")
        r = QT.export_table_to_csv(rows=[{"社区": "昆明"}], output_path=out)
        self.assertEqual(r["encoding"], "utf-8-sig")


# ────────────────────────────────────────────────
# C. 路径安全与覆盖确认
# ────────────────────────────────────────────────

class TestPathSafety(_CsvCase):

    def test_relative_path_lands_on_desktop(self):
        r = QT.export_table_to_csv(rows=[{"a": 1}], output_path="面积统计.csv")
        self.assertEqual(r["path"], self.path("面积统计.csv"))
        self.assertTrue(os.path.exists(r["path"]))

    def test_empty_path_autonamed_on_desktop(self):
        r = QT.export_table_to_csv(rows=[{"a": 1}])
        self.assertEqual(os.path.dirname(r["path"]),
                         os.path.join(self.home, "Desktop"))
        self.assertTrue(os.path.basename(r["path"]).endswith(".csv"))

    def test_missing_extension_is_completed(self):
        r = QT.export_table_to_csv(rows=[{"a": 1}], output_path=self.path("noext"))
        self.assertTrue(r["path"].endswith(".csv"))

    def test_non_csv_extension_refused(self):
        r = QT.export_table_to_csv(rows=[{"a": 1}], output_path=self.path("x.xlsx"))
        self.assertIn("error", r)
        self.assertIn(".csv", r["error"])

    def test_system_directory_refused(self):
        for bad in ("/etc/qgis_agent_test.csv", "/System/qgis_agent_test.csv",
                    "/usr/bin/qgis_agent_test.csv"):
            with self.subTest(path=bad):
                r = QT.export_table_to_csv(rows=[{"a": 1}], output_path=bad)
                self.assertIn("error", r)
                self.assertIn("系统目录", r["error"])
                self.assertFalse(os.path.exists(bad), "绝不能真的写到系统目录")

    def test_existing_file_refused_without_confirm_channel(self):
        out = self.path("exists.csv")
        with open(out, "w", encoding="utf-8") as fh:
            fh.write("原始内容\n")
        r = QT.export_table_to_csv(rows=[{"a": 1}], output_path=out)
        self.assertIn("error", r)
        with open(out, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "原始内容\n", "被拒绝时不得改动原文件")

    def test_existing_file_overwritten_after_confirm(self):
        out = self.path("confirm.csv")
        with open(out, "w", encoding="utf-8") as fh:
            fh.write("旧\n")
        seen = []

        def _confirm(tool, preview):
            seen.append(tool)
            return True

        # 替身环境里 QApplication/QThread 都是 MagicMock，_request_confirmation
        # 的线程判定不可靠，这里直接替换该函数，只验证「确认后才覆盖」这条链路。
        with mock.patch.object(QT, "_request_confirmation", _confirm):
            r = QT.export_table_to_csv(rows=[{"a": 1}], output_path=out)
        self.assertEqual(seen, ["export_table_to_csv"])
        self.assertEqual(r["path"], out)
        self.assertEqual(_read_csv(out), [["a"], ["1"]])


# ────────────────────────────────────────────────
# D. CSV 注入防护
# ────────────────────────────────────────────────

class TestInjectionGuard(_CsvCase):

    def test_formula_like_text_is_prefixed(self):
        for raw in ("=1+1", "+86", "-abc", "@SUM(A1)"):
            with self.subTest(value=raw):
                cells = QT._csv_cell(raw)
                self.assertTrue(cells.startswith("'"),
                                "%r 会被 Excel 当公式执行，必须加前导单引号" % raw)

    def test_plain_text_untouched(self):
        self.assertEqual(QT._csv_cell("顺龙社区"), "顺龙社区")

    def test_numbers_are_not_prefixed(self):
        """负数是最常见的数据（-1.5 不能变成文本 '-1.5'）"""
        self.assertEqual(QT._csv_cell(-1.5), -1.5)
        self.assertEqual(QT._csv_cell(0), 0)

    def test_written_file_carries_escaped_cell(self):
        out = self.path("inject.csv")
        QT.export_table_to_csv(rows=[{"备注": "=cmd|'/C calc'!A0"}], output_path=out)
        self.assertEqual(_read_csv(out)[1][0], "'=cmd|'/C calc'!A0")


# ────────────────────────────────────────────────
# E. layer 模式
# ────────────────────────────────────────────────

class TestLayerMode(_CsvCase):

    def setUp(self):
        super().setUp()
        self.layer = _FakeLayer(
            "COM_S", fields=["NAME", "AREA"],
            rows=[{"NAME": "社区A", "AREA": 1.5}, {"NAME": "社区B", "AREA": 2.5}],
            selected=[{"NAME": "社区B", "AREA": 2.5}])
        patcher = mock.patch.object(QT, "QgsProject",
                                    _project_stub(_FakeProject([self.layer])))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_attribute_table_export(self):
        out = self.path("attr.csv")
        r = QT.export_table_to_csv(layer="COM_S", output_path=out)
        self.assertEqual(r["rows"], 2)
        self.assertEqual(_read_csv(out),
                         [["NAME", "AREA"], ["社区A", "1.5"], ["社区B", "2.5"]])

    def test_layer_name_case_insensitive(self):
        r = QT.export_table_to_csv(layer="com_s", output_path=self.path("ci.csv"))
        self.assertNotIn("error", r)

    def test_columns_are_case_insensitive_and_ordered(self):
        out = self.path("cols.csv")
        QT.export_table_to_csv(layer="COM_S", columns=["area", "name"], output_path=out)
        self.assertEqual(_read_csv(out)[0], ["AREA", "NAME"])

    def test_unknown_column_lists_available_fields(self):
        r = QT.export_table_to_csv(layer="COM_S", columns=["NOPE"],
                                   output_path=self.path("bad.csv"))
        self.assertIn("error", r)
        self.assertIn("NAME", str(r["error"]))

    def test_unknown_layer_returns_unified_body(self):
        r = QT.export_table_to_csv(layer="zzz", output_path=self.path("z.csv"))
        self.assertIn("error", r)
        self.assertIn("available_layers", r)

    def test_limit_and_only_selected(self):
        out = self.path("sel.csv")
        QT.export_table_to_csv(layer="COM_S", only_selected=True, output_path=out)
        self.assertEqual(_read_csv(out), [["NAME", "AREA"], ["社区B", "2.5"]])


# ────────────────────────────────────────────────
# F. 工具注册 + 沙箱提示对齐（跨文件约定）
# ────────────────────────────────────────────────

class TestRegistrationAndHints(unittest.TestCase):

    def test_registered_in_tool_map(self):
        self.assertIs(QT.TOOL_MAP.get("export_table_to_csv"),
                      QT.export_table_to_csv)

    def test_declared_in_tool_definitions(self):
        entry = [d for d in QT.TOOL_DEFINITIONS
                 if d.get("name") == "export_table_to_csv"]
        self.assertEqual(len(entry), 1)
        props = entry[0]["parameters"]["properties"]
        for key in ("rows", "layer", "output_path", "columns", "limit", "only_selected"):
            self.assertIn(key, props)

    def test_csv_import_rejected_with_tool_hint(self):
        """execute_pyqgis 里 import csv 被拒时，hint 必须指向专用工具"""
        r = QT.execute_pyqgis("import csv\nprint('x')")
        self.assertFalse(r["executed"])
        self.assertIn("export_table_to_csv", r["hint"])

    def test_open_call_rejected_with_tool_hint(self):
        r = QT.execute_pyqgis("f = open('/tmp/x.csv', 'w')")
        self.assertFalse(r["executed"])
        self.assertIn("export_table_to_csv", r["hint"])

    def test_generic_rejection_still_mentions_tools(self):
        hint = QT._sandbox_reject_hint("禁止调用 'eval'", "eval('1')")
        self.assertIn("export_table_to_csv", hint)

    def test_system_prompt_documents_the_tool(self):
        src = _read("processor.py")
        self.assertIn("export_table_to_csv", src)
        self.assertIn("写不了文件", src)


if __name__ == "__main__":
    unittest.main()
