# -*- coding: utf-8 -*-
"""
独立脚本：构建 PyQGIS API 文档索引。

使用方法（在 QGIS Python 控制台中运行）:
    exec(open(r'D:\Work\qgis_agent\scripts\build_api_index.py').read())

或在命令行（需要 QGIS 环境）:
    python build_api_index.py
"""

import os
import sys

# 确保 qgis_agent 在 Python path 中
plugin_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if plugin_dir not in sys.path:
    sys.path.insert(0, plugin_dir)

from rag import DocStore, generate_pyqgis_docs


def main():
    print("=" * 60)
    print("  QGIS Agent — PyQGIS API 文档索引构建工具")
    print("=" * 60)

    store = DocStore()

    # 检查现有索引
    stats = store.get_stats()
    if stats["api_docs"] > 0:
        print(f"\n当前已有 {stats['api_docs']} 条 API 文档。")
        choice = input("是否重建索引？(y/N): ").strip().lower()
        if choice == "y":
            print("清空旧索引...")
            store.clear_all()
        else:
            print("取消。")
            return

    print("\n开始构建 API 文档索引...")
    print("-" * 40)

    def progress(phase, current, total):
        phases = {
            "inspect": "正在反射 QGIS 核心类...",
            "processing": "正在提取 Processing 算法...",
            "manual": "正在导入手动补充文档...",
        }
        label = phases.get(phase, phase)
        if total > 1:
            print(f"  [{current}/{total}] {label}")
        else:
            print(f"  {label}")

    result = generate_pyqgis_docs(store, progress_callback=progress)

    print("-" * 40)
    print(f"\n✅ 索引构建完成！")
    print(f"   - 运行时 API 反射: {result['api_count']} 条")
    print(f"   - Processing 算法: {result['processing_count']} 条")
    print(f"   - 手动补充文档:   {result['manual_count']} 条")
    print(f"   - 总计:           {result['total']} 条")
    print(f"\n数据库位置: {store.db_path}")
    print("\n索引已就绪，QGIS Agent 将在下次对话时自动使用 RAG 检索。")


if __name__ == "__main__":
    main()
