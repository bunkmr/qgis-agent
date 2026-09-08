# -*- coding: utf-8 -*-
"""smart_debugger.py 测试：错误模式匹配 / 上下文分析 / 降级策略 / 自适应学习

该模块纯 Python（re/json/os/datetime），无需 QGIS。
历史文件落在临时目录，避免污染项目根目录的 debug_history.json。
"""

import os
import shutil
import tempfile
import unittest

try:  # 既支持以包方式导入（qgis_agent.tests.test_x）
    from . import support
except ImportError:  # 也支持 `unittest discover -s tests` 的顶层模块导入
    import support  # noqa: F401


class DebuggerTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sd = support.import_mod("smart_debugger")

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="qgis_agent_debug_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.history_file = os.path.join(self.tmp, "debug_history.json")

    def debugger(self):
        return self.sd.SmartDebugger(self.history_file)


class TestErrorPatternMatcher(DebuggerTestCase):
    CASES = [
        ("ImportError: No module named 'geopandas'", "import_errors"),
        ("ModuleNotFoundError: No module named processing", "import_errors"),
        ("DLL load failed while importing _qgis_core", "import_errors"),
        ("cannot import name 'QgsVectorLayer' from 'qgis.core'", "import_errors"),
        ("QgsVectorLayer not found", "qgis_specific"),
        ("processing.run error: wrong parameters", "qgis_specific"),
        ("AttributeError: QgsProject instance has no attribute", "qgis_specific"),
        ("QgsApplication not initialized", "qgis_specific"),
        ("FileNotFoundError: /data/roads.shp", "data_path_errors"),
        ("No such file or directory", "data_path_errors"),
        ("Invalid data source: /tmp/x.gpkg", "data_path_errors"),
        ("Permission denied: /root/x.shp", "data_path_errors"),
        ("Algorithm native:buffer not found", "processing_algorithm_errors"),
        ("Parameter INPUT is required", "processing_algorithm_errors"),
        ("Algorithm native:xxx does not exist", "processing_algorithm_errors"),
        ("Geometry is invalid at or near point", "geometry_errors"),
        ("TopologyException: found non-noded intersection", "geometry_errors"),
        ("Self-intersection at 1.5 2.5", "geometry_errors"),
        ("Field 'POP_2020' not found", "field_errors"),
        ("Column population does not exist", "field_errors"),
        ("Invalid field name", "field_errors"),
        ("MemoryError: unable to allocate array", "memory_errors"),
        ("Out of memory: Killed process", "memory_errors"),
        ("CRS EPSG:99999 not found", "coordinate_system_errors"),
        ("Transform failed: Coordinate out of range", "coordinate_system_errors"),
    ]

    def test_error_texts_classified_correctly(self):
        matcher = self.sd.ErrorPatternMatcher()
        for text, expected in self.CASES:
            with self.subTest(text=text):
                category, _info = matcher.match_error_pattern(text)
                self.assertEqual(category, expected)

    def test_unknown_error_returns_none(self):
        matcher = self.sd.ErrorPatternMatcher()
        category, info = matcher.match_error_pattern("一切正常，没有报错")
        self.assertIsNone(category)
        self.assertEqual(info, {})

    def test_matching_is_case_insensitive(self):
        matcher = self.sd.ErrorPatternMatcher()
        category, _ = matcher.match_error_pattern("modulenotfounderror: no module")
        self.assertEqual(category, "import_errors")

    def test_every_category_has_solutions_and_severity(self):
        matcher = self.sd.ErrorPatternMatcher()
        for name, info in matcher.error_patterns.items():
            with self.subTest(category=name):
                self.assertGreater(len(info["patterns"]), 0)
                self.assertGreater(len(info["solutions"]), 0)
                self.assertIn(info["severity"], ("high", "medium", "low"))
                self.assertIsInstance(info["category"], str)


class TestContextAnalyzer(DebuggerTestCase):
    def setUp(self):
        super().setUp()
        self.analyzer = self.sd.ContextAnalyzer()

    def test_code_complexity_thresholds(self):
        self.assertEqual(self.analyzer._assess_code_complexity("x = 1"), "simple")
        self.assertEqual(self.analyzer._assess_code_complexity("\n".join(["x"] * 9)), "simple")
        self.assertEqual(self.analyzer._assess_code_complexity("\n".join(["x"] * 10)), "moderate")
        self.assertEqual(self.analyzer._assess_code_complexity("\n".join(["x"] * 29)), "moderate")
        self.assertEqual(self.analyzer._assess_code_complexity("\n".join(["x"] * 30)), "complex")

    def test_identify_data_operations(self):
        code = ("layer = QgsVectorLayer(path, 'a', 'ogr')\n"
                "processing.run('native:buffer', {})\n"
                "QgsRasterLayer(p)\n"
                "join_fields()\n"
                "buffer_distance = 10")
        ops = self.analyzer._identify_data_operations(code)
        self.assertEqual(set(ops), {"processing_algorithm", "vector_layer",
                                    "raster_layer", "attribute_join", "buffer_operation"})

    def test_identify_data_operations_empty(self):
        self.assertEqual(self.analyzer._identify_data_operations("print(1)"), [])

    def test_operation_type_adds_contextual_solutions(self):
        context = self.analyzer.analyze_context("boom", "x = 1", "buffer")
        self.assertEqual(context["operation_type"], "buffer")
        self.assertIn("Ensure projected CRS", context["contextual_solutions"])

    def test_unknown_operation_type_has_no_contextual_solutions(self):
        context = self.analyzer.analyze_context("boom", "x = 1", "not_a_real_op")
        self.assertEqual(context["contextual_solutions"], [])

    def test_processing_run_plus_algorithm_error_hint(self):
        context = self.analyzer.analyze_context(
            "Algorithm native:buffer not found", "processing.run('native:buffer', {})")
        self.assertIn("Check algorithm ID is correct and available",
                      context["contextual_solutions"])

    def test_vector_layer_invalid_hint(self):
        context = self.analyzer.analyze_context(
            "layer is INVALID", "layer = QgsVectorLayer(path, 'a', 'ogr')")
        self.assertIn("Verify layer path and check if layer.isValid()",
                      context["contextual_solutions"])


class TestFallbackStrategy(DebuggerTestCase):
    def setUp(self):
        super().setUp()
        self.fb = self.sd.FallbackStrategy()

    def test_matching_category_is_selected(self):
        strategies = self.fb.get_fallback_strategies("algorithm_error")
        self.assertEqual([s["name"] for s in strategies], ["alternative_qgis_tool"])

    def test_operation_type_can_match_conditions(self):
        strategies = self.fb.get_fallback_strategies("unknown", "memory_error")
        self.assertEqual([s["name"] for s in strategies], ["break_into_steps"])

    def test_no_match_returns_top_three_by_priority(self):
        strategies = self.fb.get_fallback_strategies("unknown", "unknown")
        self.assertEqual([s["name"] for s in strategies],
                         ["alternative_qgis_tool", "geopandas_equivalent", "break_into_steps"])

    def test_results_sorted_by_priority(self):
        strategies = self.fb.get_fallback_strategies("no_suitable_tool", "specific_requirements")
        priorities = [s["priority"] for s in strategies]
        self.assertEqual(priorities, sorted(priorities))


class TestAdaptiveLearning(DebuggerTestCase):
    def test_record_and_recall_best_solution(self):
        learner = self.sd.AdaptiveLearning(self.history_file)
        learner.record_debug_attempt("import_errors", "pip install geopandas", True, 1.5)
        learner.record_debug_attempt("import_errors", "pip install geopandas", True, 1.0)
        learner.record_debug_attempt("import_errors", "换用内置工具", True, 2.0)
        self.assertEqual(learner.get_best_solution("import_errors"), "pip install geopandas")

    def test_failed_attempt_is_not_recommended(self):
        learner = self.sd.AdaptiveLearning(self.history_file)
        learner.record_debug_attempt("geometry_errors", "buffer(0)", False, 0.5)
        self.assertIsNone(learner.get_best_solution("geometry_errors"))
        self.assertEqual(len(learner.history["failed_attempts"]), 1)
        self.assertEqual(len(learner.history["successful_fixes"]), 0)

    def test_history_persists_across_instances(self):
        first = self.sd.AdaptiveLearning(self.history_file)
        first.record_debug_attempt("memory_errors", "分块处理", True, 3.0)
        second = self.sd.AdaptiveLearning(self.history_file)
        self.assertEqual(second.get_best_solution("memory_errors"), "分块处理")
        self.assertEqual(second.history["common_patterns"]["memory_errors"], 1)

    def test_corrupted_history_file_falls_back(self):
        with open(self.history_file, "w", encoding="utf-8") as f:
            f.write("{ not json")
        learner = self.sd.AdaptiveLearning(self.history_file)
        self.assertEqual(learner.history["successful_fixes"], [])

    def test_missing_history_file_uses_default_structure(self):
        learner = self.sd.AdaptiveLearning(os.path.join(self.tmp, "nope.json"))
        self.assertEqual(set(learner.history), {
            "successful_fixes", "failed_attempts", "common_patterns",
            "performance_metrics", "last_updated"})


class TestSmartDebugger(DebuggerTestCase):
    def test_analyze_error_shape(self):
        debugger = self.debugger()
        result = debugger.analyze_error("ModuleNotFoundError: No module named x", "import x")
        self.assertEqual(result["error_category"], "import_errors")
        self.assertIn("solutions", result["pattern_info"])
        self.assertIn("operation_type", result["context"])
        self.assertIn("fallback_strategies", result)
        self.assertIsNone(result["best_historical_solution"])

    def test_confidence_scales_with_signals(self):
        debugger = self.debugger()
        # 命中类别(0.4) + low/medium 严重度(0)
        low = debugger.analyze_error("Invalid field name", "x = 1")
        # 命中类别(0.4) + high 严重度(0.3)
        high = debugger.analyze_error("ModuleNotFoundError: No module named x", "x = 1")
        self.assertAlmostEqual(low["confidence"], 0.4)
        self.assertAlmostEqual(high["confidence"], 0.7)

    def test_confidence_reaches_one_with_history(self):
        debugger = self.debugger()
        debugger.record_fix_attempt("ModuleNotFoundError: No module named x", "pip install", True)
        result = debugger.analyze_error("ModuleNotFoundError: No module named x", "x = 1")
        self.assertAlmostEqual(result["confidence"], 1.0)
        self.assertEqual(result["best_historical_solution"], "pip install")

    def test_unknown_error_still_returns_fallbacks(self):
        debugger = self.debugger()
        result = debugger.analyze_error("完全没见过的报错", "x = 1")
        self.assertIsNone(result["error_category"])
        self.assertEqual(len(result["fallback_strategies"]), 3)

    def test_suggestions_include_pattern_solutions(self):
        debugger = self.debugger()
        suggestions = debugger.generate_debug_suggestions(
            "FileNotFoundError: /data/a.shp", "open('/data/a.shp')")
        self.assertIn("Verify data file paths exist and are accessible", suggestions)

    def test_low_confidence_appends_fallback_lines(self):
        debugger = self.debugger()
        suggestions = debugger.generate_debug_suggestions(
            "FileNotFoundError: /data/a.shp", "open('/data/a.shp')")
        self.assertTrue(any(s.startswith("Fallback:") for s in suggestions))

    def test_high_confidence_skips_fallback_lines(self):
        debugger = self.debugger()
        debugger.record_fix_attempt("ModuleNotFoundError: No module named x", "pip install", True)
        suggestions = debugger.generate_debug_suggestions(
            "ModuleNotFoundError: No module named x", "import x")
        self.assertFalse(any(s.startswith("Fallback:") for s in suggestions))
        self.assertTrue(suggestions[0].startswith("Previously successful:"))

    def test_record_fix_attempt_ignores_unmatched_error(self):
        debugger = self.debugger()
        debugger.record_fix_attempt("完全没见过的报错", "瞎改了一通", True)
        self.assertEqual(debugger.adaptive_learning.history["successful_fixes"], [])

    def test_convenience_function(self):
        suggestions = self.sd.get_debug_suggestions(
            "Algorithm native:buffer not found", "processing.run('native:buffer')")
        self.assertIsInstance(suggestions, list)
        self.assertGreater(len(suggestions), 0)


if __name__ == "__main__":
    unittest.main()
