# -*- coding: utf-8 -*-
"""
测试官方 API 文档加载
"""

import sys
import os

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_builtin_docs():
    """测试内置官方文档"""
    print("Testing BuiltinOfficialDocs...")

    from rag.official_doc_scraper import BuiltinOfficialDocs, APIDocEntry

    # 获取文档
    docs = BuiltinOfficialDocs.get_docs()
    print(f"Loaded {len(docs)} official API docs")

    # 统计每个类的方法数
    class_methods = {}
    for doc in docs:
        if doc.class_name not in class_methods:
            class_methods[doc.class_name] = []
        class_methods[doc.class_name].append(doc.method_name)

    print("\nClass coverage:")
    for class_name, methods in sorted(class_methods.items()):
        print(f"  {class_name}: {len(methods)} methods")
        for method in methods[:3]:  # 只显示前3个
            print(f"    - {method}")
        if len(methods) > 3:
            print(f"    ... and {len(methods) - 3} more")

    return docs


def test_doc_store():
    """测试文档存储"""
    print("\nTesting DocStore with official docs...")

    from rag.doc_store import DocStore

    store = DocStore(":memory:")  # 使用内存数据库

    # 加载官方文档
    from rag.official_doc_scraper import BuiltinOfficialDocs
    docs = BuiltinOfficialDocs.get_docs()

    # 转换为字典格式并插入
    dicts = []
    for doc in docs:
        dicts.append({
            "class_name": doc.class_name,
            "method_name": doc.method_name,
            "full_signature": doc.full_signature,
            "description": doc.description,
            "parameters": doc.parameters,
            "return_type": doc.return_type,
            "source": doc.source,
        })

    store.insert_batch(dicts)

    # 测试搜索
    results = store.search_fts("buffer")
    print(f"Search 'buffer': found {len(results)} results")
    for r in results[:3]:
        print(f"  - {r['full_signature']}")

    results = store.search_fts("feature")
    print(f"Search 'feature': found {len(results)} results")
    for r in results[:3]:
        print(f"  - {r['full_signature']}")

    # 统计
    stats = store.get_stats()
    print(f"\nDocStore stats: {stats}")


def test_full_generation():
    """测试完整的文档生成"""
    print("\nTesting full document generation...")

    from rag.doc_generator import generate_pyqgis_docs
    from rag.doc_store import DocStore

    store = DocStore(":memory:")

    def progress(phase, current, total):
        print(f"  [{phase}] {current}/{total}")

    stats = generate_pyqgis_docs(
        store,
        include_runtime=False,  # 跳过运行时检查（需要 QGIS 环境）
        include_processing=False,  # 跳过 Processing（需要 QGIS 环境）
        include_manual=True,
        include_official=True,
        progress_callback=progress,
    )

    print(f"\nGeneration stats: {stats}")

    # 测试搜索
    results = store.search_fts("layer")
    print(f"Search 'layer': found {len(results)} results")


if __name__ == "__main__":
    print("=" * 50)
    print("Testing Official API Documentation")
    print("=" * 50)

    test_builtin_docs()
    test_doc_store()
    test_full_generation()

    print("\n" + "=" * 50)
    print("All tests completed!")
    print("=" * 50)
