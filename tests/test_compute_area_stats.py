# -*- coding: utf-8 -*-
"""compute_area_stats 的回归守卫（v2.4.13）。

背景：用户要求「统计 COM_S 各个社区 / 街道乡镇 / 区县 / 地市州的面积，注意计算时
使用正确分度带投影和中央经线」。模型没有专用工具，只能裸写 PyQGIS，于是踩了
**本仓库最刁钻的一个坑**：

    ``QgsCoordinateReferenceSystem("+proj=tmerc +lat_0=0 +lon_0=99 ...")``
    构造出来的是**无效 CRS**（``isValid() == False``），而它**不抛任何异常**。
    接着 ``QgsGeometry.transform(tr)`` 因为 CRS 无效而**静默跳过**，
    面积仍是「平方度」，除以 1e6 再四舍五入 → **0.0**。
    **全程零报错**，工具还兴高采烈地回了 0。

所以这里把四件事钉死：
  A. 分带取带规则与官方 EPSG（CM 99E→4542、102E→4543、105E→4544，规律 4542+(CM-99)/3）；
  B. CRS 必须走 fromEpsgId / fromProj 且**显式校验 isValid()**，取不到就中止而不是给 0；
  C. 两种口径（3 度带投影 / GRS80 椭球）同时给出且互相校验；
  D. 分级汇总、CSV 落盘（UTF-8 BOM）、错误体（未知图层 / 未知字段 / 非面图层）与工具注册。
"""

import ast
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

import qgis.core as qgis_core  # noqa: E402

QT = support.import_mod("qgis_tools")
TOOLS_SRC = os.path.join(support.PROJECT_ROOT, "qgis_tools.py")

VECTOR = QT.QgsMapLayer.LayerType.VectorLayer
RASTER = QT.QgsMapLayer.LayerType.RasterLayer

M2_PER_KM2 = 1e6


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _func_node(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError("源码里找不到函数 %s" % name)


# ────────────────────────────────────────────────
# 替身
# ────────────────────────────────────────────────

class _FakeField:
    def __init__(self, name):
        self._name = name

    def name(self):
        return self._name


class _FakePoint:
    def __init__(self, x):
        self._x = x

    def x(self):
        return self._x


class _FakeCentroid:
    def __init__(self, x):
        self._x = x

    def asPoint(self):
        return _FakePoint(self._x)


class _FakeGeom:
    """几何替身：只需要 centroid().asPoint().x() / transform / area / isEmpty。

    数值直接由构造参数给定，好让用例把「投影面积」与「椭球面积」当成两个已知量
    断言聚合逻辑（真实几何数学由真机验收覆盖）。
    """

    def __init__(self, spec=None):
        if isinstance(spec, _FakeGeom):
            self.lon, self.zone_m2, self.ell_m2 = spec.lon, spec.zone_m2, spec.ell_m2
            self.empty = spec.empty
        else:
            self.lon, self.zone_m2, self.ell_m2 = spec
            self.empty = False

    def isEmpty(self):  # noqa: N802 - 保持 QGIS 命名
        return self.empty

    def centroid(self):
        return _FakeCentroid(self.lon)

    def transform(self, tr):
        return True

    def area(self):
        return self.zone_m2


class _FakeDistanceArea:
    def __init__(self):
        self.ellipsoid = None
        self.source = None

    def setSourceCrs(self, crs, tc):  # noqa: N802
        self.source = crs

    def setEllipsoid(self, name):  # noqa: N802
        self.ellipsoid = name

    def measureArea(self, geom):  # noqa: N802
        return geom.ell_m2


class _FakeCrs:
    def __init__(self, authid="EPSG:4490", desc="CGCS2000"):
        self._authid = authid
        self._desc = desc

    def authid(self):
        return self._authid

    def description(self):
        return self._desc

    def isValid(self):  # noqa: N802
        return True


class _FakeFeature:
    def __init__(self, values, geom):
        self._values = values
        self._geom = geom

    def geometry(self):
        return self._geom

    def __getitem__(self, name):
        return self._values[name]


class _FakeLayer:
    def __init__(self, name, fields=(), rows=(), layer_type=None, crs=None):
        self._name = name
        self._fields = [_FakeField(f) for f in fields]
        self._rows = list(rows)
        self._type = VECTOR if layer_type is None else layer_type
        self._crs = crs or _FakeCrs()

    def name(self):
        return self._name

    def id(self):
        return "id_" + self._name

    def type(self):
        return self._type

    def crs(self):
        return self._crs

    def fields(self):
        return self._fields

    def getFeatures(self):  # noqa: N802
        return iter(_FakeFeature(values, geom) for values, geom in self._rows)


class _FakeProject:
    def __init__(self, layers):
        self._layers = {lyr.id(): lyr for lyr in layers}

    def mapLayer(self, layer_id):  # noqa: N802
        return self._layers.get(layer_id)

    def mapLayers(self):  # noqa: N802
        return dict(self._layers)

    def transformContext(self):  # noqa: N802
        return "TC"


def _project_stub(project):
    class _Stub:
        @staticmethod
        def instance():
            return project

    return _Stub


#: 三个要素：CM99E 一个、CM105E 两个，数值为已知量（单位 m²）。
#: 刻意让两个县的面积**不相等**（甲县 300 km² / 乙县 350 km²），
#: 否则「按面积降序」这条断言会因为并列而失去意义。
ROWS = [
    ({"NAME": "甲社区", "COU_NAME": "甲县", "CODE": "530101"}, _FakeGeom((98.9, 1.0e8, 1.0e8))),
    ({"NAME": "乙社区", "COU_NAME": "甲县", "CODE": "530102"}, _FakeGeom((104.9, 2.0e8, 2.0e8))),
    ({"NAME": "丙社区", "COU_NAME": "乙县", "CODE": "530201"}, _FakeGeom((105.1, 3.5e8, 3.5e8))),
]


class _AreaCase(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="area_home_")
        os.makedirs(os.path.join(self.home, "Desktop"), exist_ok=True)
        env = mock.patch.dict(os.environ, {"HOME": self.home})
        env.start()
        self.addCleanup(env.stop)
        self.out = os.path.join(self.home, "out")

    def install(self, layer, geom_type="Polygon"):
        proj = mock.patch.object(QT, "QgsProject",
                                 _project_stub(_FakeProject([layer])))
        proj.start()
        self.addCleanup(proj.stop)
        gt = mock.patch.object(QT, "_geometry_type_name", lambda lyr: geom_type)
        gt.start()
        self.addCleanup(gt.stop)
        # QgsDistanceArea / QgsCoordinateTransform / QgsGeometry 是在
        # compute_area_stats **函数体内**导入的，模块里没有这些名字，
        # 必须打在 qgis.core 模块上才会生效。
        for name, repl in (("QgsGeometry", _FakeGeom),
                           ("QgsDistanceArea", _FakeDistanceArea),
                           ("QgsCoordinateTransform", lambda *a, **k: "TR")):
            p = mock.patch.object(qgis_core, name, repl)
            p.start()
            self.addCleanup(p.stop)
        cm = mock.patch.object(QT, "_zone_crs_for_central_meridian",
                               lambda cm: (_FakeCrs("EPSG:%d" % (4542 + (cm - 99) // 3)), None))
        cm.start()
        self.addCleanup(cm.stop)

    def layer(self, rows=None):
        return _FakeLayer("COM_S", fields=["NAME", "COU_NAME", "CODE"],
                          rows=ROWS if rows is None else rows)


# ────────────────────────────────────────────────
# A. 分带与 CRS（真源判据）
# ────────────────────────────────────────────────

class TestZoneCrs(unittest.TestCase):

    def test_official_epsg_mapping(self):
        """CM 99E→4542、102E→4543、105E→4544（规律 4542 + (CM-99)/3）。"""
        seen = {}

        class _Probe:
            def __init__(self, authid):
                self._authid = authid

            def isValid(self):  # noqa: N802
                return True

            def authid(self):
                return self._authid

        def _from_epsg(epsg):
            seen["epsg"] = epsg
            return _Probe("EPSG:%d" % epsg)

        with mock.patch.object(QT, "QgsCoordinateReferenceSystem") as crs:
            crs.fromEpsgId.side_effect = _from_epsg
            for cm, expect in ((99, 4542), (102, 4543), (105, 4544), (108, 4545)):
                with self.subTest(cm=cm):
                    crs_obj, err = QT._zone_crs_for_central_meridian(cm)
                    self.assertIsNone(err)
                    self.assertEqual(seen["epsg"], expect)

    def test_invalid_crs_aborts_instead_of_returning_zero_area(self):
        """两种构造都无效时必须**中止**，而不是放行去算出 0。"""

        class _Bad:
            def isValid(self):  # noqa: N802
                return False

        with mock.patch.object(QT, "QgsCoordinateReferenceSystem") as crs:
            crs.fromEpsgId.return_value = _Bad()
            crs.fromProj.return_value = _Bad()
            obj, err = QT._zone_crs_for_central_meridian(99)
        self.assertIsNone(obj)
        self.assertIn("中央经线", err)
        self.assertIn("已中止", err)

    def test_source_never_constructs_crs_from_proj_string_directly(self):
        """不得出现 `QgsCoordinateReferenceSystem("+proj=...")` 这种**真实调用**。

        注意按 AST 判，不能拿源码做字符串匹配：函数 docstring 里正拿这个写法当
        反例讲解（文档说「绝对不要这样写」），字符串匹配会把注释判成违规。
        """
        tree = ast.parse(_read(TOOLS_SRC))
        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Name) and func.id == "QgsCoordinateReferenceSystem"):
                continue
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) \
                        and arg.value.strip().startswith("+proj="):
                    offenders.append(node.lineno)
        self.assertEqual(offenders, [],
                         "无效 CRS 静默得 0 的坑会复活（行号 %s）" % offenders)

    def test_source_uses_from_proj_or_from_epsg_and_checks_isvalid(self):
        node = _func_node(ast.parse(_read(TOOLS_SRC)), "_zone_crs_for_central_meridian")
        attrs = {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}
        self.assertIn("fromEpsgId", attrs)
        self.assertIn("fromProj", attrs)
        self.assertIn("isValid", attrs, "缺 isValid 校验就等于放行无效 CRS")


# ────────────────────────────────────────────────
# B. _as_field_list / 取带规则
# ────────────────────────────────────────────────

class TestFieldList(unittest.TestCase):

    def test_none_and_empty(self):
        self.assertEqual(QT._as_field_list(None), [])
        self.assertEqual(QT._as_field_list(""), [])
        self.assertEqual(QT._as_field_list([]), [])

    def test_string_and_sequence(self):
        self.assertEqual(QT._as_field_list("COU_NAME"), ["COU_NAME"])
        self.assertEqual(QT._as_field_list(["A", "B"]), ["A", "B"])
        self.assertEqual(QT._as_field_list(("A",)), ["A"])

    def test_strips_and_drops_blanks(self):
        self.assertEqual(QT._as_field_list([" A ", "  ", ""]), ["A"])

    def test_ten_degree_rule(self):
        """中央经线 = 3 * round(lon / 3)（用真机同一条公式独立复算）。"""
        cases = {98.9: 99, 99.0: 99, 100.6: 102, 104.9: 105, 105.1: 105,
                 -8.2: -9, 174.9: 174}
        for lon, expect in cases.items():
            with self.subTest(lon=lon):
                self.assertEqual(int(round(lon / 3.0)) * 3, expect)


# ────────────────────────────────────────────────
# C. 双口径统计与分级汇总
# ────────────────────────────────────────────────

class TestComputeStats(_AreaCase):

    def test_both_methods_reported_and_consistent(self):
        self.install(self.layer())
        r = QT.compute_area_stats("COM_S", method="both")
        self.assertNotIn("error", r)
        self.assertEqual(r["features_used"], 3)
        self.assertEqual(r["unit"], "km²")
        # 1e8 + 2e8 + 3.5e8 m² = 650 km²
        self.assertAlmostEqual(r["total_km2_zone"], 650.0, places=4)
        self.assertAlmostEqual(r["total_km2_ellipsoid"], 650.0, places=4)
        self.assertAlmostEqual(r["difference_pct"], 0.0, places=6)

    def test_zone_feature_counts_per_central_meridian(self):
        self.install(self.layer())
        r = QT.compute_area_stats("COM_S", method="zone")
        self.assertEqual(r["zone_feature_counts"], {"CM99E": 1, "CM105E": 2})
        self.assertEqual(r["zone_crs_used"], {"CM99E": "EPSG:4542", "CM105E": "EPSG:4544"})
        self.assertNotIn("total_km2_ellipsoid", r, "只算 zone 时不该回吐椭球口径")

    def test_ellipsoid_only(self):
        self.install(self.layer())
        r = QT.compute_area_stats("COM_S", method="ellipsoid")
        self.assertNotIn("total_km2_zone", r)
        self.assertAlmostEqual(r["total_km2_ellipsoid"], 650.0, places=4)

    def test_group_by_sorts_by_area_desc_and_counts_features(self):
        self.install(self.layer())
        r = QT.compute_area_stats("COM_S", group_by="COU_NAME")
        groups = r["groups"]
        self.assertEqual(len(groups), 1)
        entry = groups[0]
        self.assertEqual(entry["field"], "COU_NAME")
        self.assertEqual(entry["group_count"], 2)
        self.assertEqual([g["COU_NAME"] for g in entry["items"]], ["乙县", "甲县"],
                         "必须按面积降序")
        self.assertEqual([g["feature_count"] for g in entry["items"]], [1, 2])
        self.assertAlmostEqual(entry["items"][0]["area_km2"], 350.0, places=4)

    def test_group_by_multiple_fields_carries_extra_columns(self):
        self.install(self.layer())
        r = QT.compute_area_stats("COM_S", group_by=["COU_NAME", "CODE"],
                                  carry_fields="NAME")
        fields = [g["field"] for g in r["groups"]]
        self.assertEqual(fields, ["COU_NAME", "CODE"])
        first = r["groups"][0]["items"][0]
        self.assertIn("NAME", first)

    def test_no_group_gets_actionable_hint(self):
        self.install(self.layer())
        r = QT.compute_area_stats("COM_S")
        self.assertIn("group_by", r["hint"])

    def test_limit_caps_groups_and_flags_truncation(self):
        self.install(self.layer())
        r = QT.compute_area_stats("COM_S", group_by="CODE", limit=1)
        entry = r["groups"][0]
        self.assertEqual(entry["group_count"], 3)
        self.assertEqual(len(entry["items"]), 1)
        self.assertTrue(entry.get("truncated"))
        self.assertIn("CSV", entry["hint"])

    def test_features_skipped_when_empty(self):
        rows = [(ROWS[0][0], _FakeGeom((98.9, 1.0e8, 1.0e8)))]
        empty = _FakeGeom((98.9, 0.0, 0.0))
        empty.empty = True
        rows.append((ROWS[1][0], empty))
        self.install(self.layer(rows))
        r = QT.compute_area_stats("COM_S")
        self.assertEqual(r["features_used"], 1)
        self.assertEqual(r["features_skipped"], 1)


# ────────────────────────────────────────────────
# D. 错误体与参数校验
# ────────────────────────────────────────────────

class TestErrorBodies(_AreaCase):

    def test_unknown_layer_lists_available(self):
        self.install(self.layer())
        r = QT.compute_area_stats("不存在")
        self.assertIn("error", r)
        self.assertIn("COM_S", r["available_layers"])

    def test_raster_layer_refused(self):
        self.install(_FakeLayer("底图", layer_type=RASTER))
        r = QT.compute_area_stats("底图")
        self.assertIn("不是矢量图层", r["error"])

    def test_non_polygon_refused_with_hint(self):
        self.install(self.layer(), geom_type="Line")
        r = QT.compute_area_stats("COM_S")
        self.assertIn("不是面图层", r["error"])
        self.assertIn("Polygon", r["hint"])

    def test_bad_method_refused(self):
        self.install(self.layer())
        r = QT.compute_area_stats("COM_S", method="degrees")
        self.assertIn("method", r["error"])
        self.assertIn("hint", r)

    def test_unknown_group_field_lists_available_fields(self):
        self.install(self.layer())
        r = QT.compute_area_stats("COM_S", group_by="NOPE")
        self.assertIn("未找到字段", r["error"])
        self.assertIn("NAME", r["available_fields"])

    def test_unknown_carry_field_lists_available_fields(self):
        self.install(self.layer())
        r = QT.compute_area_stats("COM_S", carry_fields="NOPE")
        self.assertIn("未找到附加字段", r["error"])
        self.assertIn("COU_NAME", r["available_fields"])

    def test_group_field_is_case_insensitive(self):
        """铁律 10：外部输入的名字匹配必须容忍大小写。"""
        self.install(self.layer())
        r = QT.compute_area_stats("COM_S", group_by="cou_name")
        self.assertNotIn("error", r)
        self.assertEqual(r["groups"][0]["field"], "COU_NAME")

    def test_layer_name_is_case_insensitive(self):
        self.install(self.layer())
        r = QT.compute_area_stats("com_s")
        self.assertNotIn("error", r)


# ────────────────────────────────────────────────
# E. CSV 落盘
# ────────────────────────────────────────────────

class TestCsvOutput(_AreaCase):

    @staticmethod
    def _read_csv(path):
        with open(path, encoding="utf-8-sig", newline="") as fh:
            return list(csv.reader(fh))

    def test_files_named_per_level_plus_summary(self):
        """每级一个文件 + 一个汇总文件；文件名带图层名。"""
        self.install(self.layer())
        r = QT.compute_area_stats("COM_S", group_by=["COU_NAME", "CODE"],
                                  output_dir=self.out)
        names = {os.path.basename(f["path"]) for f in r["csv_files"]}
        self.assertEqual(names, {"COM_S_面积_COU_NAME.csv", "COM_S_面积_CODE.csv",
                                 "COM_S_面积_汇总.csv"})
        for f in r["csv_files"]:
            self.assertTrue(os.path.exists(f["path"]))
            self.assertGreater(f["bytes"], 0)

    def test_utf8_bom_for_excel(self):
        self.install(self.layer())
        r = QT.compute_area_stats("COM_S", group_by="COU_NAME", output_dir=self.out)
        for f in r["csv_files"]:
            with open(f["path"], "rb") as fh:
                self.assertEqual(fh.read(3), b"\xef\xbb\xbf",
                                 "%s 缺 BOM，Windows Excel 会乱码" % f["path"])

    def test_grouped_csv_content_and_order(self):
        self.install(self.layer())
        r = QT.compute_area_stats("COM_S", group_by="COU_NAME", output_dir=self.out)
        path = [f["path"] for f in r["csv_files"]
                if f["path"].endswith("COU_NAME.csv")][0]
        rows = self._read_csv(path)
        self.assertEqual(rows[0], ["COU_NAME", "面积_km2", "椭球面积_km2", "要素数"])
        self.assertEqual(rows[1], ["乙县", "350.0", "350.0", "1"])
        self.assertEqual(rows[2], ["甲县", "300.0", "300.0", "2"])

    def test_summary_csv_content(self):
        self.install(self.layer())
        r = QT.compute_area_stats("COM_S", output_dir=self.out)
        path = [f["path"] for f in r["csv_files"] if "汇总" in f["path"]][0]
        rows = self._read_csv(path)
        self.assertEqual(rows[0],
                         ["图层", "坐标系", "要素数", "总面积_km2", "椭球总面积_km2"])
        self.assertEqual(rows[1][0], "COM_S")
        self.assertEqual(rows[1][2], "3")

    def test_system_directory_refused(self):
        self.install(self.layer())
        for bad in ("/etc/qgis_agent_area", "/System/qgis_agent_area"):
            with self.subTest(path=bad):
                r = QT.compute_area_stats("COM_S", output_dir=bad)
                self.assertTrue(any("error" in f for f in r["csv_files"]))
                self.assertFalse(os.path.exists(bad), "绝不能真的写到系统目录")

    def test_filename_is_sanitised(self):
        self.install(_FakeLayer("COM/S:2026", fields=["NAME"], rows=ROWS))
        r = QT.compute_area_stats("COM/S:2026", group_by="NAME", output_dir=self.out)
        for f in r["csv_files"]:
            self.assertNotIn("/", os.path.basename(f["path"]))
            self.assertNotIn(":", os.path.basename(f["path"]))

    def test_relative_output_dir_lands_under_home_desktop(self):
        self.install(self.layer())
        r = QT.compute_area_stats("COM_S", group_by="NAME")
        # 不给 output_dir 时不应落盘
        self.assertNotIn("csv_files", r)


# ────────────────────────────────────────────────
# F. 注册与提示词约定对齐
# ────────────────────────────────────────────────

class TestRegistration(unittest.TestCase):

    def test_registered_in_tool_map(self):
        self.assertIs(QT.TOOL_MAP.get("compute_area_stats"), QT.compute_area_stats)

    def test_declared_in_tool_definitions(self):
        entry = [d for d in QT.TOOL_DEFINITIONS
                 if d.get("name") == "compute_area_stats"]
        self.assertEqual(len(entry), 1)
        self.assertIn("layer_id_or_name", entry[0]["parameters"]["required"])
        props = entry[0]["parameters"]["properties"]
        for key in ("group_by", "carry_fields", "output_dir", "method", "limit"):
            self.assertIn(key, props)

    def test_description_forbids_hand_written_pyqgis(self):
        """铁律 9：工具描述必须明写「不要用 execute_pyqgis 手写」。"""
        entry = [d for d in QT.TOOL_DEFINITIONS
                 if d.get("name") == "compute_area_stats"][0]
        self.assertIn("execute_pyqgis", entry["description"])

    def test_system_prompt_points_to_the_tool(self):
        src = _read(os.path.join(support.PROJECT_ROOT, "processor.py"))
        self.assertIn("compute_area_stats", src)


if __name__ == "__main__":
    unittest.main()
