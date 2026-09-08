# -*- coding: utf-8 -*-
"""dataloader.py 测试：表名白名单 + 真 SQLite 读写往返

DataLoader 只依赖 stdlib（sqlite3/os/json）和 utils，无需 QGIS，
因此这里用真实的 sqlite 文件做端到端验证，而不是 mock 掉数据库。
构造 DataLoader 会在 ~/Documents/QGIS_Agent 建目录，故用临时 HOME 隔离。
"""

import os
import shutil
import sqlite3
import sys
import unittest

try:  # 既支持以包方式导入（qgis_agent.tests.test_x）
    from . import support
except ImportError:  # 也支持 `unittest discover -s tests` 的顶层模块导入
    import support  # noqa: F401
import fakes


def _patch_home(tmp_dir):
    """让 os.path.expanduser("~") 指向临时目录"""
    for var in ("HOME", "USERPROFILE"):
        os.environ[var] = tmp_dir


class DataloaderTestCase(unittest.TestCase):
    """提供临时 HOME + 已连接的 DataLoader"""

    def setUp(self):
        self._orig_home = os.environ.get("HOME")
        self._orig_userprofile = os.environ.get("USERPROFILE")
        self.tmp_home = support.temp_home()
        _patch_home(self.tmp_home)
        self.DataLoader = support.import_mod("dataloader").DataLoader

    def tearDown(self):
        if self._orig_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._orig_home
        if self._orig_userprofile is None:
            os.environ.pop("USERPROFILE", None)
        else:
            os.environ["USERPROFILE"] = self._orig_userprofile
        shutil.rmtree(self.tmp_home, ignore_errors=True)

    def connected_loader(self, name="test_agent.db"):
        loader = self.DataLoader(name)
        self.addCleanup(loader.close)
        loader.connect()
        return loader


class TestTableNameWhitelist(unittest.TestCase):
    """SQL 注入防护：表名必须命中白名单"""

    def setUp(self):
        self.DataLoader = support.import_mod("dataloader").DataLoader

    def test_valid_table_names_accepted(self):
        for name in ("llm", "prompt", "conversation", "interaction", "credential"):
            self.assertEqual(self.DataLoader._validate_table_name(name), name)

    def test_injection_attempts_rejected(self):
        illegal = [
            "interaction; DROP TABLE conversation",
            "interaction' OR '1'='1",
            "conversation--",
            "users",
            "sqlite_master",
            "Interaction",        # 大小写敏感
            " interaction",
            "",
            None,
            123,
        ]
        for name in illegal:
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    self.DataLoader._validate_table_name(name)

    def test_whitelist_is_frozen_and_exact(self):
        allowed = self.DataLoader._ALLOWED_TABLES
        self.assertIsInstance(allowed, frozenset)
        self.assertEqual(allowed, frozenset(
            ["llm", "prompt", "conversation", "interaction", "credential"]))

    def test_check_existence_rejects_illegal_name(self):
        loader = self.DataLoader.__new__(self.DataLoader)  # 不建目录，只测校验分支
        with self.assertRaises(ValueError):
            loader._check_existence("interaction; DROP TABLE llm")


class TestLLMRoundTrip(DataloaderTestCase):
    def test_database_path_under_home(self):
        loader = self.DataLoader("test_agent.db")
        self.assertEqual(os.path.basename(loader.database_path), "test_agent.db")
        self.assertTrue(loader.database_path.startswith(self.tmp_home))

    def test_insert_and_fetch_llm_config(self):
        loader = self.connected_loader()
        loader.insert_llm_config("GLM::glm-4", "glm-4", "https://open.bigmodel.cn/api/paas/v4/", "sk-1")
        loader.reload_llm_config()

        self.assertIn("glm-4", loader.llm_full_dict["GLM"])
        self.assertIn("https://open.bigmodel.cn", loader.llm_endpoint_dict["GLM"])
        self.assertEqual(loader.api_key_dict["GLM"], "sk-1")

    def test_fetch_llm_info_returns_tuple(self):
        loader = self.connected_loader()
        loader.insert_llm_config("DeepSeek::deepseek-chat", "deepseek-chat",
                                 "https://api.deepseek.com", "sk-2")
        self.assertEqual(
            loader.fetch_llm_info("DeepSeek::deepseek-chat"),
            ("deepseek-chat", "https://api.deepseek.com", "sk-2"))

    def test_fetch_llm_info_missing_row_falls_back(self):
        loader = self.connected_loader()
        self.assertEqual(loader.fetch_llm_info("Nope::nope"), ("default", "", ""))

    def test_fetch_llm_list(self):
        loader = self.connected_loader()
        loader.insert_llm_config("GLM::glm-4", "glm-4", "e1", "k1")
        loader.insert_llm_config("GLM::glm-4-flashx", "glm-4-flashx", "e2", "k2")
        self.assertEqual(sorted(loader.fetch_llm_list()),
                         ["GLM::glm-4", "GLM::glm-4-flashx"])

    def test_update_api_key_persists(self):
        loader = self.connected_loader()
        loader.insert_llm_config("GLM::glm-4", "glm-4", "e", "old")
        loader.update_api_key("new", "GLM::glm-4")
        self.assertEqual(loader.fetch_api_key("GLM::glm-4"), ("e", "new"))

    def test_fetch_api_key_unknown_id_raises(self):
        loader = self.connected_loader()
        with self.assertRaises(ValueError):
            loader.fetch_api_key("missing::id")

    def test_delete_llm_config(self):
        loader = self.connected_loader()
        loader.insert_llm_config("GLM::glm-4", "glm-4", "e", "k")
        loader.delete_llm_config("GLM::glm-4")
        self.assertEqual(loader.fetch_llm_list(), [])

    def test_get_llm_info_parses_id(self):
        loader = self.connected_loader()
        self.assertEqual(loader.get_llm_info("GLM::glm-4"), ("GLM", "glm-4"))
        self.assertEqual(loader.get_llm_info("custom-model"), ("Custom", "custom-model"))


class TestInteractionRoundTrip(DataloaderTestCase):
    def _new_conversation(self, loader, cid="c1"):
        loader.insert_conversation_info({
            "ID": cid, "llmID": "GLM::glm-4", "title": "t", "description": "d",
            "created": "06 05 2026 10:00:00", "modified": "06 05 2026 10:00:00",
            "messageCount": 0, "workflowCount": 0, "userID": "local",
        })

    def test_insert_interaction_returns_sequential_id(self):
        loader = self.connected_loader()
        self._new_conversation(loader)
        row = list(fakes.make_interaction_row()[1:])  # 去掉 ID，insert 时自动补
        first = loader.insert_interaction(row, "c1")
        second = loader.insert_interaction(row, "c1")
        self.assertEqual(first, "c10")
        self.assertEqual(second, "c11")

    def test_select_interaction_roundtrip(self):
        loader = self.connected_loader()
        self._new_conversation(loader)
        row = list(fakes.make_interaction_row(
            requestText="问题", responseText="回答", typeMessage="return")[1:])
        loader.insert_interaction(row, "c1")

        rows = loader.select_interaction("c1")
        self.assertEqual(len(rows), 1)
        packed = fakes.interaction_dict(rows[0])
        self.assertEqual(packed["requestText"], "问题")
        self.assertEqual(packed["responseText"], "回答")
        self.assertEqual(packed["conversationID"], "c1")

    def test_select_latest_interaction(self):
        loader = self.connected_loader()
        self._new_conversation(loader)
        for text in ("a", "b", "c"):
            loader.insert_interaction(
                list(fakes.make_interaction_row(requestText=text)[1:]), "c1")
        latest = fakes.interaction_dict(loader.select_latest_interaction("c1"))
        self.assertEqual(latest["requestText"], "c")

    def test_insert_interaction_persists_workflow_flag(self):
        loader = self.connected_loader()
        self._new_conversation(loader)
        loader.insert_interaction(
            list(fakes.make_interaction_row(workflow="withTool")[1:]), "c1")
        packed = fakes.interaction_dict(loader.select_latest_interaction("c1"))
        self.assertEqual(packed["workflow"], "withTool")

    @unittest.expectedFailure
    def test_select_interaction_with_columns(self):
        """已知主代码缺陷：dataloader.py:291-294 占位符与参数个数不匹配

        columns 分支的 SQL 只有 2 个占位符（conversationID = ?、typeMessage = ?），
        但 execute 固定传入 3 个参数，必然抛
        sqlite3.ProgrammingError: Incorrect number of bindings supplied。
        """
        loader = self.connected_loader()
        self._new_conversation(loader)
        loader.insert_interaction(list(fakes.make_interaction_row()[1:]), "c1")
        rows = loader.select_interaction("c1", columns=["ID", "requestText"])
        self.assertEqual(len(rows), 1)


class TestConversationRoundTrip(DataloaderTestCase):
    def test_create_update_select_delete(self):
        loader = self.connected_loader()
        meta = {
            "ID": "c9", "llmID": "GLM::glm-4", "title": "原标题", "description": "d",
            "created": "06 05 2026 10:00:00", "modified": "06 05 2026 10:00:00",
            "messageCount": 0, "workflowCount": 0, "userID": "local",
        }
        loader.create_conversation(meta)
        meta["title"] = "新标题"
        loader.update_conversation_info(meta)

        got = loader.select_conversation_info("c9")
        self.assertEqual(got["title"], "新标题")

        loader.delete_conversation("c9")
        self.assertEqual(loader.select_conversation_info(), [])

    def test_message_count_is_recomputed(self):
        loader = self.connected_loader()
        loader.insert_conversation_info({
            "ID": "c1", "llmID": "GLM::glm-4", "title": "t", "description": "d",
            "created": "x", "modified": "x", "messageCount": 99,
            "workflowCount": 0, "userID": "local",
        })
        loader.insert_interaction(list(fakes.make_interaction_row()[1:]), "c1")
        loader.insert_interaction(list(fakes.make_interaction_row()[1:]), "c1")
        got = loader.select_conversation_info("c1")
        self.assertEqual(got["messageCount"], 2)  # 由 COUNT(*) 重算，不是入库时的 99


class TestSchemaCreated(DataloaderTestCase):
    def test_all_whitelisted_tables_exist(self):
        loader = self.connected_loader()
        for table in sorted(self.DataLoader._ALLOWED_TABLES):
            with self.subTest(table=table):
                self.assertTrue(loader._check_existence(table), table)

    def test_conversation_foreign_key_to_llm(self):
        loader = self.connected_loader()
        conn = loader.connection
        sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='conversation'").fetchone()[0]
        self.assertIn("REFERENCES llm", sql)


if __name__ == "__main__":
    unittest.main()
