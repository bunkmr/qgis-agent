import copy
import sqlite3
import os
import json
import requests
import logging

from .utils import get_current_timestamp, pack, unpack, tuple_to_dict, get_system_info


class DataLoader:
    # 允许的表名白名单，防止 SQL 注入
    _ALLOWED_TABLES = frozenset(["llm", "prompt", "conversation", "interaction", "credential"])

    @classmethod
    def _validate_table_name(cls, name: str) -> str:
        """验证表名是否在白名单中，防止 SQL 注入"""
        if name not in cls._ALLOWED_TABLES:
            raise ValueError(f"Invalid table name: {name}")
        return name

    def __init__(self, database_name: str):
        self.database_name = database_name
        self.connection = None
        self.cursor = None

        # 动态字典，从数据库加载，不再硬编码
        self.llm_full_dict = {}
        self.llm_endpoint_dict = {}
        self.api_key_dict = {}

        folder_path = os.path.expanduser("~/Documents/QGIS_Agent")
        if not os.path.exists(folder_path):
            os.makedirs(folder_path)
        self.database_path = os.path.join(folder_path, self.database_name)

        self.llm_table_name = "llm"
        self.llm_table_colname = ["ID", "name", "endpoint", "apiKey"]
        self.prompt_table_name = "prompt"
        self.prompt_table_colname = ["ID", "llmID", "version", "template", "promptType"]
        self.conversation_table_name = "conversation"
        self.conversation_table_colname = ["ID", "llmID", "title", "description", "created", "modified", "messageCount", "workflowCount", "userID"]
        self.interaction_table_name = "interaction"
        self.interaction_table_colname = ["ID", "conversationID", "promptID", "requestText", "contextText", "requestTime", "typeMessage", "responseText", "responseTime", "workflow", "executionLog"]
        self.credential_table_name = "credential"
        self.credential_table_colname = ["ID", "sessionID", "sessionKey"]

    def _check_existence(self, table_name):
        self._validate_table_name(table_name)
        query = "SELECT name FROM sqlite_master WHERE type='table' AND name = ?;"
        self.cursor.execute(query, (table_name,))
        return self.cursor.fetchone() is not None

    def _table_ref(self, table_name: str) -> str:
        """返回已验证的表名，用于安全构建 SQL"""
        return self._validate_table_name(table_name)

    def connect(self):
        self.connection = sqlite3.connect(self.database_path, check_same_thread=False)
        self.cursor = self.connection.cursor()
        self._create_llm_table()
        self._create_conversation_table()
        self._create_prompt_table()
        self._create_interaction_table()
        self._create_credential_table()

    def _create_llm_table(self):
        t = self._table_ref(self.llm_table_name)
        columns = ["ID TEXT NOT NULL PRIMARY KEY", "name TEXT NOT NULL", "endpoint TEXT", "apiKey TEXT"]
        creation_sql = f"CREATE TABLE IF NOT EXISTS {t} ({', '.join(columns)})"
        self.cursor.execute(creation_sql)
        self.connection.commit()

        # 从数据库加载配置到内存字典
        self._load_llm_configs_from_db()

    def _create_prompt_table(self):
        if not self._check_existence(self.prompt_table_name):
            t = self._table_ref(self.prompt_table_name)
            lt = self._table_ref(self.llm_table_name)
            columns = [
                "ID TEXT NOT NULL PRIMARY KEY", "llmID TEXT NOT NULL", "version INTEGER NOT NULL",
                "template TEXT NOT NULL", "promptType TEXT NOT NULL",
                f"FOREIGN KEY (llmID) REFERENCES {lt}(ID)"
            ]
            creation_sql = f"CREATE TABLE IF NOT EXISTS {t} ({', '.join(columns)})"
            self.cursor.execute(creation_sql)

            current_folder = os.path.dirname(os.path.abspath(__file__))
            prompt_path = os.path.join(current_folder, "resources", "prompt.json")
            if os.path.exists(prompt_path):
                with open(prompt_path, 'r', encoding='utf-8') as f:
                    prompt_dict = json.load(f)

                # 从数据库获取所有 LLM 配置
                all_llm_rows = self.fetch_all_config()
                rows_to_insert = []
                for prompt_type in prompt_dict:
                    for provider in prompt_dict[prompt_type]:
                        for llm_id, name, endpoint, api_key in all_llm_rows:
                            # 匹配 provider（LLM ID 的 :: 之前部分）
                            llm_provider = llm_id.split("::", 1)[0] if "::" in llm_id else "Custom"
                            if llm_provider != provider:
                                continue
                            prompt_id = f"{llm_id}::0::{prompt_type}"
                            version = 0
                            template = ""
                            for key, value in prompt_dict[prompt_type][provider].items():
                                template += key + ":\n\n" + value + "\n\n"
                            rows_to_insert.append([prompt_id, llm_id, version, template, prompt_type])

                if rows_to_insert:
                    self.cursor.executemany(f"""
                        INSERT INTO {t} (ID, llmID, version, template, promptType)
                        VALUES (?, ?, ?, ?, ?)
                    """, rows_to_insert)
                    self.connection.commit()

    def _create_conversation_table(self):
        if not self._check_existence(self.conversation_table_name):
            t = self._table_ref(self.conversation_table_name)
            lt = self._table_ref(self.llm_table_name)
            columns = [
                "ID TEXT NOT NULL PRIMARY KEY", "llmID TEXT NOT NULL", "title TEXT NOT NULL",
                "description TEXT NOT NULL", "created TEXT NOT NULL", "modified TEXT NOT NULL",
                "messageCount INT NOT NULL", "workflowCount INT NOT NULL", "userID TEXT NOT NULL",
                f"FOREIGN KEY (llmID) REFERENCES {lt}(ID)"
            ]
            sql = f"CREATE TABLE IF NOT EXISTS {t} ({', '.join(columns)})"
            self.cursor.execute(sql)
            self.connection.commit()

    def _create_interaction_table(self):
        if not self._check_existence(self.interaction_table_name):
            t = self._table_ref(self.interaction_table_name)
            ct = self._table_ref(self.conversation_table_name)
            columns = [
                "ID TEXT PRIMARY KEY", "conversationID TEXT NOT NULL", "promptID TEXT NOT NULL",
                "requestText TEXT NOT NULL", "contextText TEXT NOT NULL", "requestTime TEXT NOT NULL",
                "typeMessage TEXT NOT NULL", "responseText TEXT", "responseTime TEXT",
                "workflow TEXT", "executionLog TEXT",
                f"FOREIGN KEY (conversationID) REFERENCES {ct}(ID)"
            ]
            sql = f"CREATE TABLE IF NOT EXISTS {t} ({', '.join(columns)})"
            self.cursor.execute(sql)
            self.cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_conversation_id ON {t} (conversationID)")
            self.connection.commit()

    def _create_credential_table(self):
        if not self._check_existence(self.credential_table_name):
            t = self._table_ref(self.credential_table_name)
            columns = ["ID TEXT PRIMARY KEY", "sessionID TEXT", "sessionKey TEXT"]
            sql = f"CREATE TABLE IF NOT EXISTS {t} ({', '.join(columns)})"
            self.cursor.execute(sql)

    def _load_llm_configs_from_db(self):
        """从数据库加载所有 LLM 配置到内存字典"""
        rows = self.fetch_all_config()
        self.llm_full_dict = {}
        self.llm_endpoint_dict = {}
        self.api_key_dict = {}
        for row in rows:
            llm_id, name, endpoint, api_key = row
            provider = llm_id.split("::", 1)[0] if "::" in llm_id else "Custom"
            self.llm_full_dict.setdefault(provider, []).append(name)
            self.llm_endpoint_dict[provider] = endpoint
            self.api_key_dict[provider] = api_key

    def reload_llm_config(self):
        """重新从数据库加载 LLM 配置（保存设置后调用）"""
        self._load_llm_configs_from_db()

    def fetch_llm_list(self):
        rows = self.fetch_all_config()
        return [row[0] for row in rows]

    def fetch_llm_info(self, llm_id):
        t = self._table_ref(self.llm_table_name)
        sql = f"SELECT name, endpoint, apiKey FROM {t} WHERE ID = ?"
        self.cursor.execute(sql, (llm_id,))
        row = self.cursor.fetchone()
        if row:
            return row[0], row[1], row[2]
        return "default", "", ""

    def get_llm_info(self, llm_id):
        if "::" in llm_id:
            provider, model_name = llm_id.split("::", 1)
        else:
            provider, model_name = "Custom", llm_id
        return provider, model_name

    def insert_conversation_info(self, conversation_info_dict):
        t = self._table_ref(self.conversation_table_name)
        colnames = ", ".join(self.conversation_table_colname)
        placeholders = ", ".join(["?"] * len(self.conversation_table_colname))
        sql = f"INSERT INTO {t} ({colnames}) VALUES ({placeholders})"
        self.cursor.execute(sql, unpack(conversation_info_dict, "conversation"))
        self.connection.commit()

    def update_api_key(self, api_key, llm_id):
        _, old_api_key = self.fetch_api_key(llm_id)
        if old_api_key == api_key:
            return
        t = self._table_ref(self.llm_table_name)
        sql = f"UPDATE {t} SET apiKey = ? WHERE ID = ?"
        self.cursor.execute(sql, (api_key, llm_id))
        self.connection.commit()

    def fetch_api_key(self, llm_id):
        t = self._table_ref(self.llm_table_name)
        sql = f"SELECT endpoint, apiKey FROM {t} WHERE ID = ?"
        self.cursor.execute(sql, (llm_id,))
        result = self.cursor.fetchone()
        if result:
            return result[0], result[1]
        raise ValueError(f"未找到ID为 {llm_id} 的记录")

    def fetch_all_config(self):
        t = self._table_ref(self.llm_table_name)
        sql = f"SELECT * FROM {t}"
        self.cursor.execute(sql)
        return self.cursor.fetchall()

    def select_conversation_info(self, conversation_id=None):
        ct = self._table_ref(self.conversation_table_name)
        it = self._table_ref(self.interaction_table_name)
        if conversation_id is None:
            sql = f"SELECT * FROM {ct}"
            self.cursor.execute(sql)
            rows = tuple_to_dict(self.cursor.fetchall(), "conversation")
        else:
            sql = f"SELECT * FROM {ct} WHERE ID = ?"
            self.cursor.execute(sql, (conversation_id,))
            rows = tuple_to_dict(self.cursor.fetchall(), "conversation")

        for row in rows:
            cid = row["ID"]
            self.cursor.execute(
                f"SELECT COUNT(*) FROM {it} WHERE conversationID = ? AND typeMessage != 'internal'",
                (cid,)
            )
            row["messageCount"] = self.cursor.fetchone()[0]
            self.cursor.execute(
                f"SELECT COUNT(*) FROM {it} WHERE conversationID = ? AND workflow != 'empty'",
                (cid,)
            )
            row["workflowCount"] = self.cursor.fetchone()[0]

        return rows if conversation_id is None else rows[0]

    def delete_conversation_info(self, conversation_id):
        t = self._table_ref(self.conversation_table_name)
        sql = f"DELETE FROM {t} WHERE ID = ?"
        self.cursor.execute(sql, (conversation_id,))
        self.connection.commit()

    def update_conversation_info(self, meta_info: dict):
        conversation_id, llm_id, title, description, created, modified, message_count, workflow_count, user_id = unpack(meta_info, "conversation")
        t = self._table_ref(self.conversation_table_name)
        sql = f"UPDATE {t} SET llmID=?, title=?, description=?, created=?, modified=?, messageCount=?, workflowCount=?, userID=? WHERE ID=?"
        self.cursor.execute(sql, (llm_id, title, description, created, modified, message_count, workflow_count, user_id, conversation_id))
        self.connection.commit()

    def create_conversation(self, meta_info):
        self.insert_conversation_info(meta_info)

    def delete_conversation(self, conversation_id):
        self.delete_conversation_info(conversation_id)
        t = self._table_ref(self.interaction_table_name)
        sql = f"DELETE FROM {t} WHERE conversationID = ?"
        self.cursor.execute(sql, (conversation_id,))
        self.connection.commit()

    def insert_interaction(self, interaction_info: list, conversation_id: str) -> str:
        t = self._table_ref(self.interaction_table_name)
        self.cursor.execute(
            f"SELECT COUNT(*) FROM {t} WHERE conversationID = ? AND typeMessage != 'internal'",
            (conversation_id,)
        )
        interaction_index = conversation_id + str(self.cursor.fetchone()[0])
        all_colnames = ", ".join(self.interaction_table_colname)
        placeholders = ", ".join(["?"] * len(self.interaction_table_colname))
        sql = f"INSERT INTO {t} ({all_colnames}) VALUES ({placeholders})"
        interaction = tuple([interaction_index] + interaction_info)
        self.cursor.execute(sql, interaction)
        self.connection.commit()
        return interaction_index

    def select_interaction(self, conversation_id, columns=None):
        t = self._table_ref(self.interaction_table_name)
        if columns:
            sql = f"SELECT {', '.join(columns)} FROM {t} WHERE conversationID = ? AND typeMessage = ?"
        else:
            sql = f"SELECT * FROM {t} WHERE conversationID = ? AND typeMessage IN (?, ?)"
        self.cursor.execute(sql, (conversation_id, "input", "return"))
        return self.cursor.fetchall()

    def select_latest_interaction(self, conversation_id, interaction_id=None):
        t = self._table_ref(self.interaction_table_name)
        if interaction_id is not None:
            sql = f"SELECT * FROM {t} WHERE conversationID = ? AND ID = ?"
            self.cursor.execute(sql, (conversation_id, interaction_id))
            row = self.cursor.fetchone()
            if row is not None:
                return row
        rows = self.select_interaction(conversation_id)
        processed = []
        for row in rows:
            packed = pack(row, "interaction")
            if packed["conversationID"] in packed["ID"]:
                processed.append(list(row) + [int(packed["ID"][len(packed["conversationID"]):])])
        sorted_rows = sorted(processed, key=lambda x: x[-1])
        return sorted_rows[-1][:-1]

    def update_llm_config(self, llm_id, name, endpoint, api_key):
        t = self._table_ref(self.llm_table_name)
        sql = f"UPDATE {t} SET name=?, endpoint=?, apiKey=? WHERE ID=?"
        self.cursor.execute(sql, (name, endpoint, api_key, llm_id))
        self.connection.commit()

    def insert_llm_config(self, llm_id, name, endpoint, api_key):
        t = self._table_ref(self.llm_table_name)
        sql = f"INSERT OR REPLACE INTO {t} (ID, name, endpoint, apiKey) VALUES (?, ?, ?, ?)"
        self.cursor.execute(sql, (llm_id, name, endpoint, api_key))
        self.connection.commit()

    def delete_llm_config(self, llm_id):
        t = self._table_ref(self.llm_table_name)
        sql = f"DELETE FROM {t} WHERE ID=?"
        self.cursor.execute(sql, (llm_id,))
        self.connection.commit()

    def close(self):
        if self.connection:
            self.connection.close()
