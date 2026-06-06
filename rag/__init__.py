# -*- coding: utf-8 -*-
"""
RAG 模块 — 本地 PyQGIS API 文档检索 + Cookbook 自我进化。

核心组件:
- doc_store:   SQLite FTS5 文档存储
- retriever:   API 文档检索器
- doc_generator: 从 QGIS 运行时提取 API 文档
- cookbook:    成功案例自动归档与检索
"""

from .doc_store import DocStore
from .retriever import APIDocRetriever, get_retriever, init_retriever
from .doc_generator import generate_pyqgis_docs
from .cookbook import Cookbook

__all__ = ["DocStore", "APIDocRetriever", "get_retriever", "init_retriever", "generate_pyqgis_docs", "Cookbook"]
