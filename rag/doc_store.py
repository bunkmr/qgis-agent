# -*- coding: utf-8 -*-
"""
SQLite FTS5 文档存储 — 零额外依赖的本地全文检索。

表结构:
- pyqgis_api_docs:     API 文档结构化存储
- pyqgis_api_fts:      FTS5 全文索引（关联 pyqgis_api_docs）
- cookbook_entries:     成功案例归档（Cookbook）
- cookbook_fts:         Cookbook FTS5 全文索引
"""

import os
import sqlite3
import json
import threading


class DocStore:
    """本地 SQLite FTS5 文档存储管理器。

    线程安全：每个工作线程需通过 get_connection() 获取独立连接。
    """

    def __init__(self, db_path: str = None):
        if db_path is None:
            # 默认存储在插件 data 目录下
            plugin_dir = os.path.dirname(os.path.dirname(__file__))
            db_path = os.path.join(plugin_dir, "data", "pyqgis_api.db")
        self.db_path = db_path
        self._local = threading.local()
        self._ensure_tables()

    # ── 线程安全连接 ──

    def get_connection(self) -> sqlite3.Connection:
        """获取当前线程的 SQLite 连接（自动创建）"""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(self.db_path)
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA foreign_keys=ON")
        return self._local.conn

    def close(self):
        """关闭当前线程的连接"""
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None

    # ── 表初始化 ──

    def _ensure_tables(self):
        """确保所有必要的表已创建"""
        # 内存数据库不需要创建目录
        if self.db_path != ":memory:":
            db_dir = os.path.dirname(self.db_path)
            if db_dir:
                os.makedirs(db_dir, exist_ok=True)
        conn = self.get_connection()

        # API 文档主表
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pyqgis_api_docs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                class_name TEXT NOT NULL,
                method_name TEXT,
                full_signature TEXT NOT NULL,
                description TEXT DEFAULT '',
                parameters TEXT DEFAULT '[]',
                return_type TEXT DEFAULT '',
                example_code TEXT DEFAULT '',
                source TEXT DEFAULT 'runtime',
                version_added TEXT DEFAULT '',
                deprecated INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                UNIQUE(class_name, method_name)
            )
        """)

        # FTS5 全文索引（独立表，内容同步）
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS pyqgis_api_fts USING fts5(
                class_name, method_name, full_signature, description, example_code,
                content='pyqgis_api_docs',
                content_rowid='id',
                tokenize='unicode61 remove_diacritics 1'
            )
        """)

        # Cookbook 案例表
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cookbook_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_summary TEXT NOT NULL,
                user_input TEXT NOT NULL,
                tools_used TEXT DEFAULT '[]',
                code_snippet TEXT DEFAULT '',
                success_rating INTEGER DEFAULT 5,
                complexity_rating INTEGER DEFAULT 3,
                quality_score REAL DEFAULT 15.0,
                created_at TEXT DEFAULT (datetime('now')),
                use_count INTEGER DEFAULT 1,
                last_used TEXT DEFAULT (datetime('now'))
            )
        """)

        # Cookbook FTS5
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS cookbook_fts USING fts5(
                task_summary, user_input, code_snippet,
                content='cookbook_entries',
                content_rowid='id',
                tokenize='unicode61 remove_diacritics 1'
            )
        """)

        conn.commit()

    # ── API 文档 CRUD ──

    def insert_api_doc(self, doc: dict) -> int:
        """插入或更新一条 API 文档记录。返回 rowid。"""
        conn = self.get_connection()
        params = (
            doc.get("class_name", ""),
            doc.get("method_name", ""),
            doc.get("full_signature", ""),
            doc.get("description", ""),
            json.dumps(doc.get("parameters", []), ensure_ascii=False),
            doc.get("return_type", ""),
            doc.get("example_code", ""),
            doc.get("source", "runtime"),
            doc.get("version_added", ""),
            doc.get("deprecated", 0),
        )
        conn.execute("""
            INSERT INTO pyqgis_api_docs
                (class_name, method_name, full_signature, description,
                 parameters, return_type, example_code, source, version_added, deprecated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(class_name, method_name) DO UPDATE SET
                full_signature=excluded.full_signature,
                description=excluded.description,
                parameters=excluded.parameters,
                return_type=excluded.return_type,
                example_code=excluded.example_code,
                deprecated=excluded.deprecated
        """, params)
        conn.commit()
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    def insert_batch(self, docs: list):
        """批量插入 API 文档"""
        conn = self.get_connection()
        for doc in docs:
            try:
                self.insert_api_doc(doc)
            except Exception:
                pass
        conn.commit()
        # 重建 FTS5 索引
        conn.execute("INSERT INTO pyqgis_api_fts(pyqgis_api_fts) VALUES('rebuild')")
        conn.commit()

    def get_api_count(self) -> int:
        """获取 API 文档总数"""
        conn = self.get_connection()
        return conn.execute("SELECT COUNT(*) FROM pyqgis_api_docs").fetchone()[0]

    # ── FTS5 检索 ──

    def search_fts(self, query: str, top_k: int = 5) -> list:
        """FTS5 全文搜索 API 文档。

        Args:
            query: 搜索关键词（支持多词，自动 OR 连接）
            top_k: 返回结果数量

        Returns:
            [{"class_name": ..., "method_name": ..., "full_signature": ..., "description": ..., ...}, ...]
        """
        conn = self.get_connection()
        # 将空格分隔的关键词转为 FTS5 OR 查询
        keywords = [k.strip() for k in query.split() if k.strip()]
        if not keywords:
            return []
        fts_query = " OR ".join(keywords)

        try:
            rows = conn.execute("""
                SELECT d.id, d.class_name, d.method_name, d.full_signature,
                       d.description, d.parameters, d.return_type, d.example_code,
                       d.source, d.version_added, d.deprecated
                FROM pyqgis_api_fts f
                JOIN pyqgis_api_docs d ON f.rowid = d.id
                WHERE pyqgis_api_fts MATCH ?
                ORDER BY rank
                LIMIT ?
            """, (fts_query, top_k)).fetchall()

            return [dict(row) for row in rows]
        except sqlite3.OperationalError:
            # FTS5 查询语法错误时回退到 LIKE 搜索
            return self._fallback_like_search(query, top_k)

    def _fallback_like_search(self, query: str, top_k: int = 5) -> list:
        """FTS5 失败时的 LIKE 回退搜索"""
        conn = self.get_connection()
        keywords = [k.strip() for k in query.split() if k.strip()]
        if not keywords:
            return []

        conditions = " OR ".join([
            "full_signature LIKE ? OR description LIKE ? OR class_name LIKE ?"
        ] * len(keywords))
        params = []
        for kw in keywords:
            params.extend([f"%{kw}%", f"%{kw}%", f"%{kw}%"])

        rows = conn.execute(f"""  # nosec B608 - conditions built from fixed SQL fragments, values parameterized
            SELECT * FROM pyqgis_api_docs
            WHERE {conditions}
            LIMIT ?
        """, params + [top_k]).fetchall()

        return [dict(row) for row in rows]

    def search_by_class(self, class_name: str, top_k: int = 20) -> list:
        """按类名搜索所有方法"""
        conn = self.get_connection()
        rows = conn.execute("""
            SELECT * FROM pyqgis_api_docs
            WHERE class_name = ?
            ORDER BY method_name
            LIMIT ?
        """, (class_name, top_k)).fetchall()
        return [dict(row) for row in rows]

    # ── Cookbook CRUD ──

    def insert_cookbook_entry(self, entry: dict) -> int:
        """插入一条 Cookbook 案例。返回 rowid。

        Args:
            entry: {
                "task_summary": "对图层做缓冲区分析",
                "user_input": "帮我做 100 米缓冲区",
                "tools_used": ["execute_pyqgis"],
                "code_snippet": "buffer_result = ...",
                "success_rating": 5,
                "complexity_rating": 3,
            }
        """
        conn = self.get_connection()
        quality_score = entry.get("quality_score", 0.0)
        if quality_score == 0.0:
            quality_score = (
                entry.get("success_rating", 5)
                * entry.get("complexity_rating", 3)
            )

        conn.execute("""
            INSERT INTO cookbook_entries
                (task_summary, user_input, tools_used, code_snippet,
                 success_rating, complexity_rating, quality_score)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            entry.get("task_summary", ""),
            entry.get("user_input", ""),
            json.dumps(entry.get("tools_used", []), ensure_ascii=False),
            entry.get("code_snippet", ""),
            entry.get("success_rating", 5),
            entry.get("complexity_rating", 3),
            quality_score,
        ))
        conn.commit()
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    def search_cookbook(self, query: str, top_k: int = 3) -> list:
        """搜索 Cookbook 案例。

        先用 FTS5 搜，失败则回退到 LIKE。
        结果按 quality_score 降序排列。
        """
        conn = self.get_connection()
        keywords = [k.strip() for k in query.split() if k.strip()]
        if not keywords:
            return self._get_top_cookbook(top_k)

        fts_query = " OR ".join(keywords)
        try:
            rows = conn.execute("""
                SELECT c.id, c.task_summary, c.user_input, c.tools_used,
                       c.code_snippet, c.success_rating, c.complexity_rating,
                       c.quality_score, c.use_count
                FROM cookbook_fts f
                JOIN cookbook_entries c ON f.rowid = c.id
                WHERE cookbook_fts MATCH ?
                ORDER BY c.quality_score DESC
                LIMIT ?
            """, (fts_query, top_k)).fetchall()
            if rows:
                # 更新使用计数
                ids = [row[0] for row in rows]
                conn.executemany(
                    "UPDATE cookbook_entries SET use_count=use_count+1, last_used=datetime('now') WHERE id=?",
                    [(i,) for i in ids]
                )
                conn.commit()
                return [dict(row) for row in rows]
        except sqlite3.OperationalError:
            pass

        return self._fallback_cookbook_like(query, top_k)

    def _fallback_cookbook_like(self, query: str, top_k: int = 3) -> list:
        """Cookbook LIKE 回退搜索"""
        conn = self.get_connection()
        keywords = [k.strip() for k in query.split() if k.strip()]
        conditions = " OR ".join(["task_summary LIKE ? OR user_input LIKE ?"] * len(keywords))
        params = []
        for kw in keywords:
            params.extend([f"%{kw}%", f"%{kw}%"])

        rows = conn.execute(f"""  # nosec B608 - conditions built from fixed SQL fragments, values parameterized
            SELECT * FROM cookbook_entries
            WHERE {conditions}
            ORDER BY quality_score DESC
            LIMIT ?
        """, params + [top_k]).fetchall()
        return [dict(row) for row in rows]

    def _get_top_cookbook(self, top_k: int = 3) -> list:
        """获取质量评分最高的 Cookbook 案例"""
        conn = self.get_connection()
        rows = conn.execute("""
            SELECT * FROM cookbook_entries
            ORDER BY quality_score DESC
            LIMIT ?
        """, (top_k,)).fetchall()
        return [dict(row) for row in rows]

    def get_cookbook_stats(self) -> dict:
        """获取 Cookbook 统计信息"""
        conn = self.get_connection()
        count = conn.execute("SELECT COUNT(*) FROM cookbook_entries").fetchone()[0]
        if count == 0:
            return {"total": 0, "avg_quality": 0.0}
        avg_q = conn.execute(
            "SELECT AVG(quality_score) FROM cookbook_entries"
        ).fetchone()[0]
        return {"total": count, "avg_quality": round(avg_q, 1)}

    # ── 数据库状态 ──

    def get_stats(self) -> dict:
        """获取存储统计信息"""
        conn = self.get_connection()
        api_count = conn.execute("SELECT COUNT(*) FROM pyqgis_api_docs").fetchone()[0]
        cookbook_count = conn.execute("SELECT COUNT(*) FROM cookbook_entries").fetchone()[0]
        return {
            "api_docs": api_count,
            "cookbook_entries": cookbook_count,
            "db_path": self.db_path,
        }

    def clear_all(self):
        """清空所有数据（用于重建索引）"""
        conn = self.get_connection()
        conn.execute("DELETE FROM pyqgis_api_docs")
        conn.execute("DELETE FROM cookbook_entries")
        conn.execute("INSERT INTO pyqgis_api_fts(pyqgis_api_fts) VALUES('rebuild')")
        conn.execute("INSERT INTO cookbook_fts(cookbook_fts) VALUES('rebuild')")
        conn.commit()
