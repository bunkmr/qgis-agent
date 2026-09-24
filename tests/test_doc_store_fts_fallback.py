# -*- coding: utf-8 -*-
"""DocStore 的 FTS5 缺失降级守卫。

背景：QGIS 自带的 SQLite 常不带 FTS5 模块（实测 macOS QGIS 的 sqlite 3.53.2
就没有），此前 `_ensure_tables` 无条件 `CREATE VIRTUAL TABLE ... USING fts5`，
DocStore 初始化直接崩溃 —— searchpyqgisapi 连续报
「API 文档检索失败: no such module: fts5」，且重试无法修复（环境缺模块，
与参数无关）。修复后：探测失败则跳过建 FTS 表，检索全部降级为 LIKE 模糊匹配。
"""

import sqlite3
import unittest
from unittest import mock

try:  # 既支持以包方式导入（qgis_agent.tests.test_x）
    from . import support
except ImportError:  # 也支持 `unittest discover -s tests` 的顶层模块导入
    import support  # noqa: F401

from rag.doc_store import DocStore


def _fts5_ok() -> bool:
    """当前解释器的 SQLite 是否带 FTS5（用于对照测试的 skip 判定）。"""
    c = sqlite3.connect(":memory:")
    try:
        c.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        c.close()


def _doc(i=1):
    return {
        "class_name": "QgsVectorLayer",
        "method_name": "addAttributeField%d" % i,
        # 区分度放在 full_signature 里 —— LIKE 回退只搜 full_signature /
        # description / class_name 三列，不搜 method_name。
        "full_signature": "QgsVectorLayer.addAttributeField%d(field, defaultValue)" % i,
        "description": "向矢量图层添加字段 %d" % i,
        "example_code": 'layer.addAttributeField(QgsField("name", QVariant.String))',
    }


@unittest.skipUnless(_fts5_ok(), "当前 SQLite 无 FTS5，无法做「有 FTS5」对照")
class TestFts5Present(unittest.TestCase):
    """正常环境（dev 机 / 多数 Windows QGIS）走 FTS5 路径。"""

    def test_probe_true_and_match_search(self):
        store = DocStore(":memory:")
        self.assertTrue(store.fts_enabled, "FTS5 可用时必须启用 FTS 路径")
        store.insert_api_doc(_doc())
        hits = store.search_fts("addAttributeField", top_k=3)
        self.assertTrue(hits, "FTS5 可用时 MATCH 检索应命中")
        self.assertEqual(hits[0]["class_name"], "QgsVectorLayer")


class TestFts5Missing(unittest.TestCase):
    """模拟 QGIS 里 SQLite 缺 FTS5 的环境：初始化与检索必须全部可用。"""

    def setUp(self):
        p = mock.patch.object(
            DocStore, "_fts5_available", staticmethod(lambda conn: False)
        )
        p.start()
        self.addCleanup(p.stop)

    def test_init_survives_without_fts5(self):
        store = DocStore(":memory:")
        self.assertFalse(store.fts_enabled)

    def test_insert_and_like_search(self):
        store = DocStore(":memory:")
        store.insert_api_doc(_doc())
        store.insert_batch([_doc(2), _doc(3)])  # 内部的 FTS rebuild 须被守卫跳过
        hits = store.search_fts("addAttributeField2", top_k=5)
        self.assertEqual(
            [h["method_name"] for h in hits], ["addAttributeField2"],
            "无 FTS5 时 search_fts 应降级为 LIKE 并命中",
        )

    def test_search_tool_docs_exact_match(self):
        store = DocStore(":memory:")
        conn = store.get_connection()
        conn.execute(
            "INSERT INTO tool_docs (tool_id, tool_name, brief_description) VALUES (?, ?, ?)",
            ("gdal:contour", "Contour", "提取等高线"),
        )
        conn.commit()
        hits = store.search_tool_docs("gdal:contour")
        self.assertTrue(hits, "tool_id 精确匹配不依赖 FTS5，必须可用")
        self.assertEqual(hits[0]["tool_id"], "gdal:contour")

    def test_cookbook_fallback_and_clear_all(self):
        store = DocStore(":memory:")
        store.insert_cookbook_entry(
            {"task_summary": "缓冲区分析", "user_input": "做100米缓冲区"}
        )
        hits = store.search_cookbook("缓冲区")
        self.assertTrue(hits, "无 FTS5 时 cookbook 检索应走 LIKE 回退")
        store.clear_all()  # 内部 FTS rebuild 须被守卫跳过，不得抛 OperationalError
        self.assertEqual(store.get_cookbook_stats()["total"], 0)

    def test_stats_ok(self):
        store = DocStore(":memory:")
        stats = store.get_stats()
        self.assertEqual(stats["api_docs"], 0)
        self.assertIn("db_path", stats)


if __name__ == "__main__":
    unittest.main()
