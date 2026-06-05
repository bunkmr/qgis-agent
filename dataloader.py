import copy
import sqlite3
import os
import json
import requests
import logging

from .utils import get_current_timestamp, pack, unpack, tuple_to_dict, get_system_info


class DataLoader:
    def __init__(self, database_name: str):
        self.database_name = database_name
        self.connection = None
        self.cursor = None
        self.llm_full_dict = {
            "GLM": ["glm-4", "glm-4v", "glm-4-plus", "glm-4-air", "glm-4-flash"],
            "DeepSeek": ["deepseek-chat", "deepseek-reasoner"],
            "XiaomiMiMo": ["MiMo", "MiMo-Pro"],
            "Gemini": ["gemini-2.0-flash", "gemini-2.0-pro", "gemini-1.5-pro"],
            "OpenAI": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo"],
            "default": ["default"],
        }
        self.llm_endpoint_dict = {
            "GLM": "https://open.bigmodel.cn/api/paas/v4/",
            "DeepSeek": "https://api.deepseek.com",
            "XiaomiMiMo": "https://api.xiaomi.com/v1/chat/completions",
            "Gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
            "OpenAI": "https://api.openai.com/v1",
            "default": "default",
        }
        self.api_key_dict = {
            "GLM": os.getenv("GLM_API_KEY", ""),
            "DeepSeek": os.getenv("DEEPSEEK_API_KEY", ""),
            "XiaomiMiMo": os.getenv("XIAOMI_API_KEY", ""),
            "Gemini": os.getenv("GEMINI_API_KEY", ""),
            "OpenAI": os.getenv("OPENAI_API_KEY", ""),
            "default": "default",
        }

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
        query = "SELECT name FROM sqlite_master WHERE type='table' AND name = ?;"
        self.cursor.execute(query, (table_name,))
        return self.cursor.fetchone() is not None

    def connect(self):
        self.connection = sqlite3.connect(self.database_path)
        self.cursor = self.connection.cursor()
        self._create_llm_table()
        self._create_conversation_table()
        self._create_prompt_table()
        self._create_interaction_table()
        self._create_credential_table()

    def _create_llm_table(self):
        columns = ["ID TEXT NOT NULL PRIMARY KEY", "name TEXT NOT NULL", "endpoint TEXT", "apiKey TEXT"]
        creation_sql = f"CREATE TABLE IF NOT EXISTS {self.llm_table_name} ({', '.join(columns)})"
        self.cursor.execute(creation_sql)

        rows_to_insert = []
        for provider, models in self.llm_full_dict.items():
            for model in models:
                llm_id = f"{provider}::{model}"
                endpoint = self.llm_endpoint_dict[provider]
                api_key = self.api_key_dict[provider]
                rows_to_insert.append([llm_id, model, endpoint, api_key])

        self.cursor.executemany(f"""
            INSERT OR IGNORE INTO {self.llm_table_name} (ID, name, endpoint, apiKey)
            VALUES (?, ?, ?, ?)
        """, rows_to_insert)
        self.connection.commit()

    def _create_prompt_table(self):
        if not self._check_existence(self.prompt_table_name):
            columns = [
                "ID TEXT NOT NULL PRIMARY KEY", "llmID TEXT NOT NULL", "version INTEGER NOT NULL",
                "template TEXT NOT NULL", "promptType TEXT NOT NULL",
                f"FOREIGN KEY (llmID) REFERENCES {self.llm_table_name}(ID)"
            ]
            creation_sql = f"CREATE TABLE IF NOT EXISTS {self.prompt_table_name} ({', '.join(columns)})"
            self.cursor.execute(creation_sql)

            current_folder = os.path.dirname(os.path.abspath(__file__))
            prompt_path = os.path.join(current_folder, "resources", "prompt.json")
            if os.path.exists(prompt_path):
                with open(prompt_path, 'r', encoding='utf-8') as f:
                    prompt_dict = json.load(f)
                rows_to_insert = []
                for prompt_type in prompt_dict:
                    for provider in prompt_dict[prompt_type]:
                        for model_name in self.llm_full_dict.get(provider, []):
                            prompt_id = f"{provider}::{model_name}::0::{prompt_type}"
                            llm_id = f"{provider}::{model_name}"
                            version = 0
                            template = ""
                            for key, value in prompt_dict[prompt_type][provider].items():
                                template += key + ":\n\n" + value + "\n\n"
                            rows_to_insert.append([prompt_id, llm_id, version, template, prompt_type])
                self.cursor.executemany(f"""
                    INSERT INTO {self.prompt_table_name} (ID, llmID, version, template, promptType)
                    VALUES (?, ?, ?, ?, ?)
                """, rows_to_insert)
                self.connection.commit()

    def _create_conversation_table(self):
        if not self._check_existence(self.conversation_table_name):
            columns = [
                "ID TEXT NOT NULL PRIMARY KEY", "llmID TEXT NOT NULL", "title TEXT NOT NULL",
                "description TEXT NOT NULL", "created TEXT NOT NULL", "modified TEXT NOT NULL",
                "messageCount INT NOT NULL", "workflowCount INT NOT NULL", "userID TEXT NOT NULL",
                f"FOREIGN KEY (llmID) REFERENCES {self.llm_table_name}(ID)"
            ]
            sql = f"CREATE TABLE IF NOT EXISTS {self.conversation_table_name} ({', '.join(columns)})"
            self.cursor.execute(sql)
            self.connection.commit()

    def _create_interaction_table(self):
        if not self._check_existence(self.interaction_table_name):
            columns = [
                "ID TEXT PRIMARY KEY", "conversationID TEXT NOT NULL", "promptID TEXT NOT NULL",
                "requestText TEXT NOT NULL", "contextText TEXT NOT NULL", "requestTime TEXT NOT NULL",
                "typeMessage TEXT NOT NULL", "responseText TEXT", "responseTime TEXT",
                "workflow TEXT", "executionLog TEXT",
                f"FOREIGN KEY (conversationID) REFERENCES {self.conversation_table_name}(ID)",
                f"FOREIGN KEY (promptID) REFERENCES {self.prompt_table_name}(ID)"
            ]
            sql = f"CREATE TABLE IF NOT EXISTS {self.interaction_table_name} ({', '.join(columns)})"
            self.cursor.execute(sql)
            self.cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_conversation_id ON {self.interaction_table_name} (conversationID)")
            self.connection.commit()

    def _create_credential_table(self):
        if not self._check_existence(self.credential_table_name):
            columns = ["ID TEXT PRIMARY KEY", "sessionID TEXT", "sessionKey TEXT"]
            sql = f"CREATE TABLE IF NOT EXISTS {self.credential_table_name} ({', '.join(columns)})"
            self.cursor.execute(sql)

    def get_llm_info(self, llm_id):
        provider, model_name = llm_id.split("::", 1)
        if provider in self.llm_full_dict and model_name in self.llm_full_dict[provider]:
            return provider, model_name
        return "default", "default"

    def insert_conversation_info(self, conversation_info_dict):
        colnames = ", ".join(self.conversation_table_colname)
        placeholders = ", ".join(["?"] * len(self.conversation_table_colname))
        sql = f"INSERT INTO {self.conversation_table_name} ({colnames}) VALUES ({placeholders})"
        self.cursor.execute(sql, unpack(conversation_info_dict, "conversation"))
        self.connection.commit()

    def update_api_key(self, api_key, llm_id):
        _, old_api_key = self.fetch_api_key(llm_id)
        if old_api_key == api_key:
            return
        sql = f"UPDATE {self.llm_table_name} SET apiKey = ? WHERE ID = ?"
        self.cursor.execute(sql, (api_key, llm_id))
        self.connection.commit()

    def fetch_api_key(self, llm_id):
        sql = f"SELECT endpoint, apiKey FROM {self.llm_table_name} WHERE ID = ?"
        self.cursor.execute(sql, (llm_id,))
        result = self.cursor.fetchone()
        if result:
            return result[0], result[1]
        raise ValueError(f"未找到ID为 {llm_id} 的记录")

    def fetch_all_config(self):
        sql = f"SELECT * FROM {self.llm_table_name}"
        self.cursor.execute(sql)
        return self.cursor.fetchall()

    def select_conversation_info(self, conversation_id=None):
        if conversation_id is None:
            sql = f"SELECT * FROM {self.conversation_table_name}"
            self.cursor.execute(sql)
            rows = tuple_to_dict(self.cursor.fetchall(), "conversation")
        else:
            sql = f"SELECT * FROM {self.conversation_table_name} WHERE ID = ?"
            self.cursor.execute(sql, (conversation_id,))
            rows = tuple_to_dict(self.cursor.fetchall(), "conversation")

        for row in rows:
            cid = row["ID"]
            self.cursor.execute(
                f"SELECT COUNT(*) FROM {self.interaction_table_name} WHERE conversationID = ? AND typeMessage != 'internal'",
                (cid,)
            )
            row["messageCount"] = self.cursor.fetchone()[0]
            self.cursor.execute(
                f"SELECT COUNT(*) FROM {self.interaction_table_name} WHERE conversationID = ? AND workflow != 'empty'",
                (cid,)
            )
            row["workflowCount"] = self.cursor.fetchone()[0]

        return rows if conversation_id is None else rows[0]

    def delete_conversation_info(self, conversation_id):
        sql = f"DELETE FROM {self.conversation_table_name} WHERE ID = ?"
        self.cursor.execute(sql, (conversation_id,))
        self.connection.commit()

    def update_conversation_info(self, meta_info: dict):
        conversation_id, llm_id, title, description, created, modified, message_count, workflow_count, user_id = unpack(meta_info, "conversation")
        sql = f"""UPDATE {self.conversation_table_name} SET llmID=?, title=?, description=?, created=?, modified=?, messageCount=?, workflowCount=?, userID=? WHERE ID=?"""
        self.cursor.execute(sql, (llm_id, title, description, created, modified, message_count, workflow_count, user_id, conversation_id))
        self.connection.commit()

    def create_conversation(self, meta_info):
        self.insert_conversation_info(meta_info)

    def delete_conversation(self, conversation_id):
        self.delete_conversation_info(conversation_id)
        sql = f"DELETE FROM {self.interaction_table_name} WHERE conversationID = ?"
        self.cursor.execute(sql, (conversation_id,))
        self.connection.commit()

    def insert_interaction(self, interaction_info: list, conversation_id: str) -> str:
        self.cursor.execute(
            f"SELECT COUNT(*) FROM {self.interaction_table_name} WHERE conversationID = ? AND typeMessage != 'internal'",
            (conversation_id,)
        )
        interaction_index = conversation_id + str(self.cursor.fetchone()[0])
        all_colnames = ", ".join(self.interaction_table_colname)
        placeholders = ", ".join(["?"] * len(self.interaction_table_colname))
        sql = f"INSERT INTO {self.interaction_table_name} ({all_colnames}) VALUES ({placeholders})"
        interaction = tuple([interaction_index] + interaction_info)
        self.cursor.execute(sql, interaction)
        self.connection.commit()
        return interaction_index

    def select_interaction(self, conversation_id, columns=None):
        if columns:
            sql = f"SELECT {', '.join(columns)} FROM {self.interaction_table_name} WHERE conversationID = ? AND typeMessage = ?"
        else:
            sql = f"SELECT * FROM {self.interaction_table_name} WHERE conversationID = ? AND typeMessage IN (?, ?)"
        self.cursor.execute(sql, (conversation_id, "input", "return"))
        return self.cursor.fetchall()

    def select_latest_interaction(self, conversation_id, interaction_id=None):
        if interaction_id is not None:
            sql = f"SELECT * FROM {self.interaction_table_name} WHERE conversationID = ? AND ID = ?"
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

    def close(self):
        if self.connection:
            self.connection.close()
