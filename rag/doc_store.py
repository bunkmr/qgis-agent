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
import re
import sqlite3
import json
import threading
import logging
logger = logging.getLogger(__name__)

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11 时回退到 tomli
    import tomli as tomllib


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
        # FTS5 可用性在 _ensure_tables 里探测：QGIS 自带的 sqlite 常不带 FTS5
        # （实测 macOS QGIS 的 sqlite 3.53.2 就没有），缺模块时必须降级为 LIKE
        # 检索 —— 可选加速项绝不能成为主链路失败点。
        self.fts_enabled = False
        self._ensure_tables()

    @staticmethod
    def _fts5_available(conn) -> bool:
        """探测当前 SQLite 是否带 FTS5 模块。"""
        try:
            conn.execute("CREATE VIRTUAL TABLE temp._fts5_probe USING fts5(x)")
            conn.execute("DROP TABLE temp._fts5_probe")
            return True
        except sqlite3.OperationalError:
            return False

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

        # FTS5 全文索引（独立表，内容同步）—— 仅在 SQLite 带 FTS5 模块时创建。
        # QGIS 打包的 sqlite 常缺此模块，此时跳过建表并降级，不让初始化崩溃。
        self.fts_enabled = self._fts5_available(conn)
        if self.fts_enabled:
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
        if self.fts_enabled:
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS cookbook_fts USING fts5(
                    task_summary, user_input, code_snippet,
                    content='cookbook_entries',
                    content_rowid='id',
                    tokenize='unicode61 remove_diacritics 1'
                )
            """)

        # tool_docs：Processing 算法参考目录（679 条，来自 tool_docs/*.toml）
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tool_docs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tool_id TEXT NOT NULL UNIQUE,
                tool_name TEXT DEFAULT '',
                brief_description TEXT DEFAULT '',
                full_description TEXT DEFAULT '',
                parameters TEXT DEFAULT '',
                code_example TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)

        if self.fts_enabled:
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS tool_docs_fts USING fts5(
                    tool_id, tool_name, brief_description, full_description, parameters, code_example,
                    content='tool_docs',
                    content_rowid='id',
                    tokenize='unicode61 remove_diacritics 1'
                )
            """)
        else:
            logger.warning(
                "当前 SQLite 缺少 FTS5 模块（QGIS 自带 sqlite 常见），"
                "API 文档检索降级为 LIKE 模糊匹配：功能可用，但相关性排序与速度略差"
            )

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
        if self.fts_enabled:
            # 外部内容表（content='pyqgis_api_docs'）不会自动同步 FTS 索引，
            # 单条写入后必须重建，否则 MATCH 查不到这条文档（既有 bug）。
            conn.execute("INSERT INTO pyqgis_api_fts(pyqgis_api_fts) VALUES('rebuild')")
            conn.commit()
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    def insert_batch(self, docs: list):
        """批量插入 API 文档"""
        conn = self.get_connection()
        for doc in docs:
            try:
                self.insert_api_doc(doc)
            except Exception as _e:
                logger.debug("ignored exception", exc_info=True)
        conn.commit()
        # 重建 FTS5 索引（无 FTS5 时跳过，主表数据本身已可被 LIKE 检索）
        if self.fts_enabled:
            conn.execute("INSERT INTO pyqgis_api_fts(pyqgis_api_fts) VALUES('rebuild')")
            conn.commit()

    def get_api_count(self) -> int:
        """获取 API 文档总数"""
        conn = self.get_connection()
        return conn.execute("SELECT COUNT(*) FROM pyqgis_api_docs").fetchone()[0]

    # ── tool_docs（Processing 算法参考目录）──

    def get_tool_docs_count(self) -> int:
        """获取 tool_docs 索引条数"""
        conn = self.get_connection()
        try:
            return conn.execute("SELECT COUNT(*) FROM tool_docs").fetchone()[0]
        except sqlite3.OperationalError:
            return 0

    @staticmethod
    def _read_text(path: str) -> str:
        """以容错方式读取文本：依次尝试 utf-8 / gbk / latin-1，最后用替换兜底。"""
        for enc in ("utf-8", "gbk", "latin-1"):
            try:
                with open(path, "r", encoding=enc) as f:
                    return f.read()
            except (UnicodeDecodeError, UnicodeError):
                continue
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()

    @staticmethod
    def _lenient_parse_toml(text: str) -> dict:
        """宽松解析 tool_docs TOML。

        生成脚本把 code_example 中的三重引号直接写入，导致标准 TOML 解析失败。
        这里按字段名定位多行字符串块（以 name = 三个双引号 开头），
        截取到下一个字段或文件末尾，从而在不依赖严格语法的情况下提取全部字段。
        """
        fields = {}
        # 单行基础字符串字段（tool_ID / tool_name）
        for key in ("tool_ID", "tool_name"):
            m = re.search(rf'^{key}\s*=\s*"([^"\n]*)"', text, re.M)
            if m:
                fields[key] = m.group(1)

        # 多行三重引号块
        pattern = re.compile(r'^([A-Za-z_]+)\s*=\s"""', re.M)
        matches = list(pattern.finditer(text))
        for i, m in enumerate(matches):
            name = m.group(1)
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            content = text[start:end]
            content = content.lstrip("\n")
            # 去掉结尾可能残留的闭合三重引号
            if content.rstrip().endswith('"""'):
                content = content.rstrip()[:-3]
            fields[name] = content

        return fields

    def ingest_tool_docs(self, tool_docs_dir: str = None) -> int:
        """将 tool_docs/*.toml 批量导入 FTS5 索引。

        Args:
            tool_docs_dir: TOML 目录；默认取插件根目录下的 tool_docs/

        Returns:
            成功导入的条数
        """
        if tool_docs_dir is None:
            plugin_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            tool_docs_dir = os.path.join(plugin_dir, "tool_docs")
        if not os.path.isdir(tool_docs_dir):
            return 0

        conn = self.get_connection()
        count = 0
        for filename in os.listdir(tool_docs_dir):
            if not filename.endswith(".toml"):
                continue
            filepath = os.path.join(tool_docs_dir, filename)
            try:
                with open(filepath, "rb") as f:
                    doc = tomllib.load(f)
            except Exception:
                # 容错：部分 TOML 因 code_example 内含 """ 或编码问题而非法，用宽松解析兜底
                try:
                    doc = self._lenient_parse_toml(self._read_text(filepath))
                except Exception:
                    doc = None
                if not doc:
                    continue

            tool_id = doc.get("tool_ID", "")
            if not tool_id:
                continue
            try:
                conn.execute("""
                    INSERT INTO tool_docs
                        (tool_id, tool_name, brief_description, full_description, parameters, code_example)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(tool_id) DO UPDATE SET
                        tool_name=excluded.tool_name,
                        brief_description=excluded.brief_description,
                        full_description=excluded.full_description,
                        parameters=excluded.parameters,
                        code_example=excluded.code_example
                """, (
                    tool_id,
                    doc.get("tool_name", ""),
                    doc.get("brief_description", ""),
                    doc.get("full_description", ""),
                    doc.get("parameters", ""),
                    doc.get("code_example", ""),
                ))
                count += 1
            except Exception as _e:
                logger.debug("ignored exception in loop", exc_info=True)
                continue
        conn.commit()
        # 重建 FTS5 索引（无 FTS5 时跳过）
        if self.fts_enabled:
            try:
                conn.execute("INSERT INTO tool_docs_fts(tool_docs_fts) VALUES('rebuild')")
                conn.commit()
            except sqlite3.OperationalError:
                pass
        return count

    def ensure_tool_docs(self, tool_docs_dir: str = None):
        """若 tool_docs 索引为空则自动入库（幂等）。"""
        if self.get_tool_docs_count() > 0:
            return
        self.ingest_tool_docs(tool_docs_dir)

    def search_tool_docs(self, query: str, top_k: int = 5) -> list:
        """检索 tool_docs 中的 Processing 算法参考。

        先按 tool_id 精确匹配（最快，execute_processing 传入的 algorithm 即 tool_id），
        未命中再做 FTS5 全文检索。
        """
        conn = self.get_connection()
        q = (query or "").strip()
        if not q:
            return []

        results = []
        try:
            row = conn.execute("SELECT * FROM tool_docs WHERE tool_id = ?", (q,)).fetchone()
            if row:
                results.append(dict(row))
        except sqlite3.OperationalError:
            pass

        # FTS5 全文检索：清理 FTS 特殊字符（如冒号）；无 FTS5 时跳过（前面已做过精确匹配）
        safe = re.sub(r"[^0-9a-zA-Z一-鿿]+", " ", q).strip()
        if safe and self.fts_enabled:
            try:
                rows = conn.execute("""
                    SELECT d.tool_id, d.tool_name, d.brief_description, d.full_description,
                           d.parameters, d.code_example
                    FROM tool_docs_fts f
                    JOIN tool_docs d ON f.rowid = d.id
                    WHERE tool_docs_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                """, (safe, top_k)).fetchall()
                seen = {r["tool_id"] for r in results}
                for r in rows:
                    if r["tool_id"] not in seen:
                        results.append(dict(r))
                        seen.add(r["tool_id"])
            except sqlite3.OperationalError:
                pass

        return results[:top_k]

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
        # 无 FTS5 时直接走 LIKE 回退（否则建虚表时已经崩了，到不了这里）
        if not self.fts_enabled:
            return self._fallback_like_search(query, top_k)
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

        query = (
            "SELECT * FROM pyqgis_api_docs"
            " WHERE " + conditions  # nosec B608
            + " LIMIT ?"  # noqa: W503
        )
        rows = conn.execute(query, params + [top_k]).fetchall()

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
                * entry.get("complexity_rating", 3)  # noqa: W503
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
        if not self.fts_enabled:
            return self._fallback_cookbook_like(query, top_k)
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

        query = (
            "SELECT * FROM cookbook_entries"
            " WHERE " + conditions  # nosec B608
            + " ORDER BY quality_score DESC"  # noqa: W503
            + " LIMIT ?"  # noqa: W503
        )
        rows = conn.execute(query, params + [top_k]).fetchall()
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
        tool_docs_count = self.get_tool_docs_count()
        return {
            "api_docs": api_count,
            "cookbook_entries": cookbook_count,
            "tool_docs": tool_docs_count,
            "db_path": self.db_path,
        }

    def clear_all(self):
        """清空所有数据（用于重建索引）"""
        conn = self.get_connection()
        conn.execute("DELETE FROM pyqgis_api_docs")
        conn.execute("DELETE FROM cookbook_entries")
        if self.fts_enabled:
            conn.execute("INSERT INTO pyqgis_api_fts(pyqgis_api_fts) VALUES('rebuild')")
            conn.execute("INSERT INTO cookbook_fts(cookbook_fts) VALUES('rebuild')")
        conn.commit()
