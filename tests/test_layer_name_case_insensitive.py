# -*- coding: utf-8 -*-
"""图层/字段名大小写不敏感的回归守卫（v2.4.12）。

现场故障（用户报障原文）：
    用户：「帮我将统计的表升成 csv 格式数据保存到桌面」
    插件：❌ 工具 getlayerprofile 连续失败 4 次，最后一次错误「未找到图层: com_s」
    用户：「刚才还能找到怎么现在找不到了？图层一直存在 是大写的」

真因：图层名匹配**大小写敏感**（工程里是 ``COM_S``，模型写的是 ``com_s``），
而错误信息既没提大小写、也没给真实图层名，模型只能反复试同一个错名字。

这里钉住四件事：
  A. ``_find_layer`` 必须容忍大小写 / 首尾空白 / 多余的扩展名；
  B. 所有「按名取图层」的工具出口必须走同一入口，失败时返回统一错误体
     （error + available_layers + did_you_mean + hint）；
  C. execute_pyqgis 命名空间必须提供 ``find_layer``（模型惯用的
     ``mapLayersByName("com_s")[0]`` 大小写敏感，失败时返回空列表 → IndexError，
     错误里既没有真实图层名也没有「大小写」这个线索）；
  D. 系统提示必须示范 ``find_layer``，不能再示范 ``mapLayersByName("x")[0]``。
"""

import os
import re
import tempfile
import unittest
from unittest import mock

try:  # 既支持包方式导入，也支持 `unittest discover -s tests` 的顶层导入
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


# ────────────────────────────────────────────────
# 替身：只实现被测路径真正用到的接口
# ────────────────────────────────────────────────

class _FakeField:
    def __init__(self, name):
        self._name = name

    def name(self):
        return self._name

    def typeName(self):
        return "String"

    def length(self):
        return 50

    def precision(self):
        return 0


class _FakeCrs:
    def authid(self):
        return "EPSG:4490"

    def description(self):
        return "China Geodetic Coordinate System 2000"


class _FakeExtent:
    def xMinimum(self):
        return 97.5

    def yMinimum(self):
        return 21.1

    def xMaximum(self):
        return 106.2

    def yMaximum(self):
        return 29.3


class _FakeFeature:
    def __init__(self, fid, values):
        self._fid = fid
        self._values = values

    def id(self):
        return self._fid

    def attribute(self, name):
        return self._values.get(name)

    def hasGeometry(self):
        return False


class _FakeLayer:
    def __init__(self, name, layer_id=None, fields=(), rows=()):
        self._name = name
        self._id = layer_id or ("id_" + name)
        self._fields = [_FakeField(f) for f in fields]
        self._rows = list(rows)
        self.labels_enabled = None
        self.labeling = None

    def name(self):
        return self._name

    def id(self):
        return self._id

    def type(self):
        return VECTOR

    def geometryType(self):
        return 2  # 面

    def fields(self):
        return self._fields

    def featureCount(self):
        return len(self._rows)

    def getFeatures(self):
        return iter(_FakeFeature(i, dict(r)) for i, r in enumerate(self._rows))

    def selectedFeatures(self):
        return self.getFeatures()

    def crs(self):
        return _FakeCrs()

    def extent(self):
        return _FakeExtent()

    def wkbType(self):
        return 3

    def dataProvider(self):
        return None

    def setLabelsEnabled(self, flag):
        self.labels_enabled = flag

    def setLabeling(self, labeling):
        self.labeling = labeling

    def triggerRepaint(self):
        pass


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


class _ProjectFixture(unittest.TestCase):
    """把 QgsProject 换成含 COM_S / CITY_S 的替身"""

    def setUp(self):
        self.com = _FakeLayer("COM_S", fields=["NAME", "AREA"],
                              rows=[{"NAME": "社区A", "AREA": 1.5}])
        self.city = _FakeLayer("CITY_S", fields=["NAME"], rows=[])
        self.project = _FakeProject([self.com, self.city])
        patcher = mock.patch.object(QT, "QgsProject", _project_stub(self.project))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tmp = tempfile.mkdtemp(prefix="layer_case_")


# ────────────────────────────────────────────────
# A. _find_layer 的容忍度
# ────────────────────────────────────────────────

class TestFindLayerTolerance(_ProjectFixture):

    def test_exact_name_and_id_still_work(self):
        self.assertIs(QT._find_layer("COM_S"), self.com)
        self.assertIs(QT._find_layer("id_COM_S"), self.com)

    def test_lowercase_matches_uppercase_layer(self):
        """用户图层叫 COM_S，模型写了 com_s —— 必须命中"""
        self.assertIs(QT._find_layer("com_s"), self.com)

    def test_mixed_case_and_whitespace(self):
        self.assertIs(QT._find_layer("  Com_S  "), self.com)

    def test_source_filename_suffix_is_tolerated(self):
        """模型常把数据源文件名当图层名（COM_S.shp / COM_S.gpkg）"""
        self.assertIs(QT._find_layer("com_s.shp"), self.com)

    def test_unknown_returns_none(self):
        self.assertIsNone(QT._find_layer("zzz_not_exist"))
        self.assertIsNone(QT._find_layer(""))
        self.assertIsNone(QT._find_layer(None))

    def test_field_name_tolerates_case(self):
        self.assertEqual(QT._find_field_name(self.com, "name"), "NAME")
        self.assertEqual(QT._find_field_name(self.com, " AREA "), "AREA")
        self.assertIsNone(QT._find_field_name(self.com, "不存在"))


# ────────────────────────────────────────────────
# B. 统一的「未找到图层」返回体 + 各工具出口
# ────────────────────────────────────────────────

class TestUnifiedNotFoundBody(_ProjectFixture):

    def test_body_shape_is_stable(self):
        body = QT._layer_not_found_error("com")
        self.assertIn("未找到图层", body["error"])
        self.assertIn("available_layers", body)
        self.assertIn("COM_S", body["available_layers"])
        self.assertEqual(body["did_you_mean"][0], "COM_S")
        self.assertTrue(body["hint"])

    def test_empty_project_still_has_the_key(self):
        """键恒定存在（哪怕为空）：调用方按同一形状读取，不必分支判断"""
        empty = _FakeProject([])
        with mock.patch.object(QT, "QgsProject", _project_stub(empty)):
            body = QT._layer_not_found_error("com_s")
        self.assertEqual(body["available_layers"], [])
        self.assertIn("没有任何图层", body["hint"])

    def test_get_layer_profile_wrong_case_resolves(self):
        r = QT.get_layer_profile("com_s")
        self.assertNotIn("error", r)
        self.assertEqual(r["layer"]["name"], "COM_S")

    def test_get_layer_features_wrong_case_resolves(self):
        r = QT.get_layer_features("com_s", limit=5)
        self.assertNotIn("error", r)
        self.assertEqual(r["layer_name"], "COM_S")
        self.assertEqual(r["features"][0]["attributes"]["NAME"], "社区A")

    def test_every_entry_point_returns_unified_body(self):
        """所有按名取图层的出口，找不到时必须是同一份错误体"""
        cases = {
            "get_layer_features": lambda: QT.get_layer_features("zzz"),
            "get_layer_profile": lambda: QT.get_layer_profile("zzz"),
            "remove_layer": lambda: QT.remove_layer("zzz"),
            "zoom_to_layer": lambda: QT.zoom_to_layer("zzz"),
            "set_layer_labeling": lambda: QT.set_layer_labeling("zzz", "NAME"),
            "export_features_maps": lambda: QT.export_features_maps(
                layer="zzz", output_dir=self.tmp),
            "reproject_layer": lambda: QT.reproject_layer("zzz", "EPSG:4326"),
        }
        for name, call in cases.items():
            with self.subTest(tool=name):
                r = call()
                self.assertIn("error", r, "%s 未返回错误" % name)
                self.assertIn("available_layers", r,
                              "%s 的错误体缺少候选图层列表" % name)
                self.assertIn("hint", r, "%s 的错误体缺少 hint" % name)

    def test_labeling_field_wrong_case_resolves(self):
        r = QT.set_layer_labeling("com_s", "name")  # 字段实际叫 NAME
        self.assertNotIn("error", r)


# ────────────────────────────────────────────────
# C. 沙箱里的 find_layer
# ────────────────────────────────────────────────

class TestSandboxFindLayer(_ProjectFixture):

    def test_find_layer_is_available_inside_execute_pyqgis(self):
        r = QT.execute_pyqgis('print(find_layer("com_s").name())')
        self.assertTrue(r["executed"], r)
        self.assertIn("COM_S", r["stdout"])

    def test_missing_layer_raises_with_candidates(self):
        r = QT.execute_pyqgis('find_layer("com")')
        self.assertFalse(r["executed"])
        self.assertIn("COM_S", r["error"],
                      "抛出的错误必须带候选图层名，否则模型只能继续猜")


# ────────────────────────────────────────────────
# D. 单一入口 / 提示词守卫
# ────────────────────────────────────────────────

class TestSingleEntryPoint(unittest.TestCase):
    """所有出口必须走 _find_layer：不得再出现内联的精确匹配与内联错误体"""

    def test_only_one_case_sensitive_comparison_left(self):
        src = _read("qgis_tools.py")
        hits = re.findall(r"lyr\.name\(\)\s*==", src)
        self.assertEqual(
            len(hits), 1,
            "图层名精确匹配只应存在于 _find_layer 内部，实际 %d 处" % len(hits))

    def test_not_found_literal_is_returned_in_one_place_only(self):
        src = _read("qgis_tools.py")
        hits = re.findall(r'"error":\s*f?"未找到图层', src)
        self.assertEqual(
            len(hits), 1,
            "「未找到图层」错误体只应在 _layer_not_found_error 里构造，实际 %d 处" % len(hits))

    def test_no_inline_lookup_loop_left(self):
        src = _read("qgis_tools.py")
        hits = re.findall(r"for\s+\w+\s+in\s+project\.mapLayers\(\)\.items\(\):\s*\n\s*if\s+lyr\.name", src)
        self.assertEqual(hits, [], "仍有内联的图层查找循环未收敛到 _find_layer")


class TestPromptGuidance(unittest.TestCase):

    def test_prompt_documents_find_layer(self):
        src = _read("processor.py")
        self.assertIn("find_layer(", src)
        self.assertNotIn('mapLayersByName("图层名")[0]', src,
                         "统计模板不得再示范大小写敏感的 mapLayersByName(...)[0]")

    def test_prompt_warns_hint_fields(self):
        src = _read("processor.py")
        self.assertIn("did_you_mean", src)


if __name__ == "__main__":
    unittest.main()
