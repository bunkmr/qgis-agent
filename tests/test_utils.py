# -*- coding: utf-8 -*-
"""utils.py 纯函数测试（从原 tests/__init__.py 拆出并补全）"""

import os
import unittest

try:  # 既支持以包方式导入（qgis_agent.tests.test_x）
    from . import support
except ImportError:  # 也支持 `unittest discover -s tests` 的顶层模块导入
    import support  # noqa: F401


class TestUniqueId(unittest.TestCase):
    def test_generate_unique_id(self):
        utils = support.import_mod("utils")
        uid1 = utils.generate_unique_id()
        uid2 = utils.generate_unique_id()
        self.assertNotEqual(uid1, uid2)
        self.assertIn("_", uid1)
        self.assertNotIn("-", uid1)  # uuid 的 - 被替换成 _

    def test_generate_unique_id_is_hex_like(self):
        utils = support.import_mod("utils")
        self.assertRegex(utils.generate_unique_id(), r"^[0-9a-f_]{32,}$")


class TestTimestamp(unittest.TestCase):
    def test_get_current_timestamp_format(self):
        """只校验格式（MM DD YYYY HH:MM:SS），不再硬编码年份"""
        utils = support.import_mod("utils")
        ts = utils.get_current_timestamp()
        self.assertIsInstance(ts, str)
        self.assertRegex(ts, r"^\d{2} \d{2} \d{4} \d{2}:\d{2}:\d{2}$")

    def test_get_current_timestamp_parses(self):
        from datetime import datetime
        utils = support.import_mod("utils")
        datetime.strptime(utils.get_current_timestamp(), "%m %d %Y %H:%M:%S")


class TestPackUnpack(unittest.TestCase):
    def test_pack_unpack_conversation(self):
        utils = support.import_mod("utils")
        row = ("id1", "GLM::glm-4", "测试对话", "测试描述",
               "06 05 2026 10:00:00", "06 05 2026 10:00:00",
               0, 0, "local")
        packed = utils.pack(row, "conversation")
        self.assertEqual(packed["ID"], "id1")
        self.assertEqual(packed["title"], "测试对话")
        self.assertEqual(packed["userID"], "local")
        self.assertEqual(utils.unpack(packed, "conversation"), list(row))

    def test_pack_unpack_interaction(self):
        utils = support.import_mod("utils")
        row = ("i1", "c1", "p1", "request", "context",
               "time1", "input", "response", "time2", "empty", "")
        packed = utils.pack(row, "interaction")
        self.assertEqual(packed["requestText"], "request")
        self.assertEqual(packed["typeMessage"], "input")
        self.assertEqual(packed["executionLog"], "")
        self.assertEqual(utils.unpack(packed, "interaction"), list(row))

    def test_pack_unpack_prompt(self):
        utils = support.import_mod("utils")
        row = ("p1", "GLM::glm-4", 0, "template text", "agent")
        packed = utils.pack(row, "prompt")
        self.assertEqual(packed["promptType"], "agent")
        self.assertEqual(utils.unpack(packed, "prompt"), list(row))

    def test_pack_unknown_table_raises(self):
        utils = support.import_mod("utils")
        with self.assertRaises(ValueError):
            utils.pack(("a",), "not_a_table")

    def test_unpack_key_mismatch_raises(self):
        utils = support.import_mod("utils")
        with self.assertRaises(KeyError):
            utils.unpack({"ID": "x"}, "conversation")

    def test_tuple_to_dict(self):
        utils = support.import_mod("utils")
        rows = [
            ("i1", "c1", "p1", "r1", "", "t1", "input", "", "", "empty", ""),
            ("i2", "c1", "p1", "r2", "", "t2", "return", "ok", "t3", "empty", ""),
        ]
        dicts = utils.tuple_to_dict(rows, "interaction")
        self.assertEqual([d["ID"] for d in dicts], ["i1", "i2"])
        self.assertEqual(dicts[1]["responseText"], "ok")


class TestExtractCode(unittest.TestCase):
    def test_extract_code(self):
        utils = support.import_mod("utils")
        response = """代码如下：```python
layer = iface.activeLayer()
print(layer.name())
```结束"""
        code = utils.extract_code(response)
        self.assertIn("layer = iface.activeLayer()", code)
        self.assertNotIn("```", code)

    def test_extract_code_no_code(self):
        utils = support.import_mod("utils")
        self.assertEqual(utils.extract_code("这里没有代码块"), "")

    def test_extract_code_ignores_non_python_fence(self):
        utils = support.import_mod("utils")
        self.assertEqual(utils.extract_code("```json\n{}\n```"), "")


class TestMarkdown(unittest.TestCase):
    @unittest.expectedFailure
    def test_create_markdown_code_block_restored(self):
        """已知主代码缺陷：占位符被 html.escape 转义后还原步骤失效

        现象：utils.py:129 用 `<!--CODEBLOCK_n-->` 占位；utils.py:132 对整段文本
        做 html.escape，占位符变成 `&lt;!--CODEBLOCK_0--&gt;`；utils.py:169 的
        text.replace 再也匹配不上，代码块最终以字面量形式泄漏到聊天窗口。
        """
        utils = support.import_mod("utils")
        md = utils.create_markdown("```python\nprint('hello')\n```")
        self.assertIn("<pre", md)
        self.assertIn("print('hello')", md)
        self.assertNotIn("CODEBLOCK", md)

    def test_create_markdown_escapes_html(self):
        utils = support.import_mod("utils")
        md = utils.create_markdown("<script>alert(1)</script>")
        self.assertNotIn("<script>", md)
        self.assertIn("&lt;script&gt;", md)

    def test_create_markdown_headings_and_bold(self):
        utils = support.import_mod("utils")
        md = utils.create_markdown("## 标题\n\n这是 **粗体** 内容")
        self.assertIn("<h3", md)
        self.assertIn("<b>粗体</b>", md)

    def test_create_markdown_lists_and_hr(self):
        utils = support.import_mod("utils")
        md = utils.create_markdown("- 第一项\n- 第二项\n\n---\n")
        self.assertIn("<li", md)
        self.assertIn("<hr", md)

    def test_create_markdown_inline_text_escaped_once(self):
        utils = support.import_mod("utils")
        md = utils.create_markdown("x = a < b")
        self.assertIn("&lt;", md)
        self.assertNotIn("&amp;lt;", md)


class TestMisc(unittest.TestCase):
    def test_nested_dict_to_list(self):
        utils = support.import_mod("utils")
        d = {"A": ["a1", "a2"], "B": ["b1"]}
        result = utils.nested_dict_to_list(d)
        self.assertIn("A::a1", result)
        self.assertIn("A::a2", result)
        self.assertIn("B::b1", result)
        self.assertEqual(len(result), 3)

    def test_nested_dict_to_list_empty(self):
        utils = support.import_mod("utils")
        self.assertEqual(utils.nested_dict_to_list({}), [])

    def test_format_description(self):
        utils = support.import_mod("utils")
        self.assertEqual(utils.format_description("test"), "test\n")

    def test_get_qgis_version(self):
        """无 QGIS 时固定返回 0.0；有 QGIS（或替身）时必须是 主版本.次版本"""
        utils = support.import_mod("utils")
        if utils._HAS_QGIS:
            self.assertRegex(utils.get_qgis_version(), r"^\d+\.\d+$")
        else:
            self.assertEqual(utils.get_qgis_version(), "0.0")

    def test_set_font_color_luminance(self):
        """亮底用深色字，暗底用浅色字；无 QGIS 时退化为固定深色"""
        utils = support.import_mod("utils")

        if not utils._HAS_QGIS:
            self.assertEqual(utils.set_font_color(None), "#181C14")
            return

        class Color:
            def __init__(self, r, g, b):
                self._r, self._g, self._b = r, g, b

            def red(self):
                return self._r

            def green(self):
                return self._g

            def blue(self):
                return self._b

        self.assertEqual(utils.set_font_color(Color(255, 255, 255)), "#181C14")
        self.assertEqual(utils.set_font_color(Color(0, 0, 0)), "#F1F0E9")

    def test_handle_none_conversation_skips_call(self):
        utils = support.import_mod("utils")

        class Obj:
            @utils.handle_none_conversation
            def run(self, conversation, value):
                return "called:%s" % value

        obj = Obj()
        self.assertIsNone(obj.run(None, 1))
        self.assertEqual(obj.run({"ID": "c1"}, 5), "called:5")

    def test_psutil_is_not_a_dependency(self):
        """回归守卫：psutil 已彻底移除（macOS QGIS 上加不载，且 get_system_info 从未被调用）

        该函数自 Initial commit 起全历史零调用，却把 psutil 拖进了运行依赖，
        在 macOS 的 QGIS 上 import 会因 hardened runtime 直接失败。若日后有人
        重新引入，本用例会立刻失败，避免同一条死依赖复活。
        """
        utils = support.import_mod("utils")
        self.assertFalse(
            hasattr(utils, "get_system_info"),
            "get_system_info 应已删除（它是 psutil 唯一的用武之地，但从未被调用）",
        )
        for rel in ("utils.py", "requirements.txt"):
            path = os.path.join(support.PROJECT_ROOT, rel)
            with open(path, encoding="utf-8") as fh:
                self.assertNotIn("psutil", fh.read(), f"{rel} 不应再出现 psutil")

        # metadata.txt 的 changelog 里会正常提到「移除了 psutil」这一事实，
        # 所以这里只校验**依赖声明行**（列运行依赖的那一行），而不是整份文件。
        with open(os.path.join(support.PROJECT_ROOT, "metadata.txt"), encoding="utf-8") as fh:
            declared = [line for line in fh if "langchain_core" in line]
        self.assertTrue(declared, "metadata.txt 里应能找到运行依赖声明行")
        for line in declared:
            self.assertNotIn("psutil", line, "依赖声明行不应再把 psutil 列为运行依赖")


if __name__ == "__main__":
    unittest.main()
