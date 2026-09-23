# -*- coding: utf-8 -*-
"""「对话」窗口渲染 / 布局的回归守卫。

这里守的都是**真机上肉眼可见的 UI 事故**，每一条都对应一次实际排查：

1. ``QLayout.replaceWidget()`` 返回的 ``QWidgetItem`` 归 Python 持有。不接住返回值
   就会被 GC，而 C++ 侧布局项仍指向它 → 新控件**从未真正进入布局**，对话历史区
   整块空白（``indexOf`` 还能查到，``itemAt`` 却是个布局）。
2. ``QStackedWidget.addWidget()`` 会给控件换父对象，Qt 随即把它从原布局摘掉。
   所以必须**先取索引、先 removeWidget**，再 ``addWidget``；顺序反了会拿到 -1，
   退化成「追加到末尾」，输入框与底部栏跑到消息区上面。
3. ``QTextDocument`` 不解析 CSS 自定义属性：``var(--x, #fallback)`` 会被**整条丢弃**，
   连 fallback 都不生效（实测 ``color:var(--f,#f00)`` 最终渲染为默认黑色）。
   样式表里一旦出现 ``var()``，等价于整份失效。
4. 思考块折叠不能用 ``<details>/<summary>``：QTextDocument 不支持该标签，会把正文
   照常渲染出来 —— 「展开」是假的，折叠态也永远折不起来。
5. 思考流式更新若退回到「思考开始前的 HTML 快照」，会把这段时间新增的消息冲掉
   （实测整段对话历史从 5062 字符掉到 104 字符）。
6. ``QPlainTextDocumentLayout`` 是惰性的：文档尚未参与绘制时 ``textWidth`` 恒为 -1，
   ``document().size().height()`` 返回的是**块数**（1.0 / 2.0 / 3.0 …）而不是像素高，
   于是输入框自适应恒被夹到最小值，多行输入必然出现假滚动条。
"""

import ast
import os
import unittest

try:  # 既支持包方式导入，也支持 `unittest discover -s tests` 的顶层导入
    from . import support
except ImportError:
    import support  # noqa: F401

PROJECT_ROOT = support.PROJECT_ROOT
DOCK_V2 = os.path.join(PROJECT_ROOT, "qgis_agent_dockwidget_v2.py")
BASE_UI = os.path.join(PROJECT_ROOT, "qgis_agent_dockwidget_base_ui.py")
THINKING = os.path.join(PROJECT_ROOT, "thinking_display.py")
CONVERSATION = os.path.join(PROJECT_ROOT, "conversation.py")


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _parse(path):
    return ast.parse(_read(path), filename=path)


def _called_attrs(tree):
    """收集 AST 里所有「方法调用」的属性名，如 `x.replaceWidget(...)` → 'replaceWidget'。

    用 AST 而不是正则：注释与字符串里出现 `QLayout.replaceWidget()` 只是文档说明，
    不应被算作真实调用。
    """
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            names.add(node.func.attr)
    return names


class TestCssCapabilityGuards(unittest.TestCase):
    """第 3 类坑：QTextDocument 的能力边界，用真实渲染结果验证。"""

    SAMPLE_MD = (
        "> 提示：导出时保留了原始字段。\n\n"
        "| 行政区 | 面积 |\n| --- | --- |\n| 五华区 | 397.9 |\n\n"
        "```python\nprint(1)\n```\n\n"
        "行内 `code` 与 [链接](https://docs.qgis.org)。\n"
    )

    def _markdown(self):
        utils = support.import_mod("utils")
        return utils, utils.create_markdown(self.SAMPLE_MD)

    def test_no_css_variables_in_output(self):
        """输出里绝不能出现 var(--x)：QTextDocument 会整条丢弃该声明。"""
        _, html = self._markdown()
        self.assertNotIn("var(--", html)

    def test_no_border_radius_in_output(self):
        """border-radius 在 QTextDocument 里不生效，写了只是误导。"""
        utils, html = self._markdown()
        self.assertNotIn("border-radius", html)

    def test_component_css_uses_literal_colors(self):
        """组件 CSS 必须是字面色值，且代码块底色用 code_bg。"""
        utils = support.import_mod("utils")
        css = utils._component_css()
        self.assertIn("#", css)
        self.assertNotIn("var(--", css)
        code_bg = utils.chat_colors()["code_bg"]
        self.assertIn(code_bg, css)

    def test_code_bg_differs_from_ai_bg(self):
        """代码块底色必须与 AI 气泡底色不同，否则代码块看起来「不存在」。"""
        utils = support.import_mod("utils")
        c = utils.chat_colors()
        self.assertNotEqual(c["code_bg"], c["ai_bg"])

    def test_fallback_palette_keys(self):
        """回退配色与派生配色必须同键，否则深/浅主题会缺色。"""
        utils = support.import_mod("utils")
        fallback = set(utils._FALLBACK_CHAT_COLORS)
        derived = utils.derive_chat_colors("#FFFFFF", "#181C14")
        self.assertEqual(fallback, set(derived))
        self.assertIn("code_bg", derived)

    def test_blockquote_supported(self):
        """`> xxx` 必须渲染成 blockquote（历史上是原样输出 `> 提示：…`）。"""
        _, html = self._markdown()
        self.assertIn("<blockquote", html)

    def test_light_theme_gives_dark_foreground(self):
        """浅色主题下正文必须是**深色**（可读）。"""
        utils = support.import_mod("utils")
        c = utils.derive_chat_colors("#FFFFFF", "#181C14")
        self.assertFalse(utils.is_dark_color(c["chat_bg"]))
        self.assertTrue(utils.is_dark_color(c["fg"]))

    def test_dark_theme_gives_light_foreground(self):
        """深色主题下正文必须是**浅色**（否则就是白底白字 / 黑底黑字）。"""
        utils = support.import_mod("utils")
        c = utils.derive_chat_colors("#1B1D20", "#E8EAED")
        self.assertTrue(utils.is_dark_color(c["chat_bg"]))
        self.assertFalse(utils.is_dark_color(c["fg"]))


class TestLayoutAssemblyGuards(unittest.TestCase):
    """第 1 / 2 类坑：控件的装配方式与顺序。"""

    def test_no_replace_widget_call(self):
        """不得真实调用 replaceWidget（返回值被 GC 会让控件脱离布局）。"""
        self.assertNotIn("replaceWidget", _called_attrs(_parse(DOCK_V2)))

    def test_layout_order_invariant_present(self):
        """装配自检不变式必须在，且比较顺序正确。"""
        src = _read(DOCK_V2)
        self.assertIn("_chat_layout_ok", src)
        self.assertIn("i_search < chat_index < i_input < i_bar < i_footer", src)

    def test_stack_add_after_remove_widget(self):
        """必须先把 txHistory 摘出布局，再收进 chatStack。"""
        src = _read(DOCK_V2)
        i_remove = src.find("self.messagesLayout.removeWidget(self.txHistory)")
        i_add = src.find("self.chatStack.addWidget(self.txHistory)")
        self.assertGreater(i_remove, 0, "应显式 removeWidget(txHistory)")
        self.assertGreater(i_add, 0, "应把 txHistory 收进 chatStack")
        self.assertLess(i_remove, i_add, "removeWidget 必须早于 addWidget")

    def test_stacked_widget_used_for_empty_state(self):
        """空状态与历史必须是同一个 QStackedWidget 的两页（不是并排显示）。"""
        self.assertIn("addWidget(self.emptyStateWidget)", _read(DOCK_V2))


class TestComposerHeightGuards(unittest.TestCase):
    """第 6 类坑：输入框高度自适应。"""

    def _adjust_source(self):
        tree = _parse(DOCK_V2)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_adjust_message_input_height":
                return ast.get_source_segment(_read(DOCK_V2), node) or ""
        self.fail("找不到 _adjust_message_input_height")

    def test_does_not_use_document_size(self):
        """不得用 document().size().height() 当像素高度（惰性布局返回的是块数）。"""
        src = self._adjust_source()
        self.assertNotIn("document().size()", src)
        self.assertNotIn("doc_height", src)

    def test_does_not_use_font_metrics_bounding_rect(self):
        """不得用 boundingRect(..., TextWordWrap) 算行数：Qt6 下高度会翻倍。"""
        src = self._adjust_source()
        self.assertNotIn("boundingRect", src)

    def test_uses_text_layout_real_line_height(self):
        """必须用 QTextLayout 累加真实行高（Qt6 真实行高大于标称 lineSpacing）。"""
        src = self._adjust_source()
        self.assertIn("_message_input_text_height", src)

        whole = _read(DOCK_V2)
        marker = "def _message_input_text_height"
        body = whole[whole.find(marker):]
        body = body[:body.find("\n    def ", 10)]
        self.assertIn("QTextLayout", body)
        self.assertIn("setLineWidth", body)
        self.assertIn("line.height()", body)

    def test_height_is_clamped(self):
        """高度必须夹在 MIN/MAX 之间，避免长文本把输入区顶满整个 dock。"""
        src = self._adjust_source()
        self.assertIn("MESSAGE_INPUT_MIN_HEIGHT", src)
        self.assertIn("MESSAGE_INPUT_MAX_HEIGHT", src)

    def test_reentry_guard(self):
        """setFixedHeight 会再次触发 Resize，必须有防重入标记避免递归。"""
        self.assertIn("_adjusting_input", _read(DOCK_V2))

    def test_has_long_text_shortcut(self):
        """超长文本（大量块）必须有短路，别为每个块建一次 QTextLayout。"""
        whole = _read(DOCK_V2)
        marker = "def _message_input_text_height"
        body = whole[whole.find(marker):]
        body = body[:body.find("\n    def ", 10)]
        self.assertIn("blocks > 200", body)


class TestThinkingBlockGuards(unittest.TestCase):
    """第 4 / 5 类坑：思考块的折叠与流式更新。"""

    def test_no_details_tag(self):
        """<details>/<summary> 在 QTextDocument 里不生效，不得用于折叠。

        用 AST 取 ``create_thinking_block`` 的**函数体源码**（跳过 docstring 与注释），
        因为 docstring 里正是用 `<details>` 举例说明「为什么不用它」。
        """
        src = _read(THINKING)
        tree = ast.parse(src, filename=THINKING)
        target = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "create_thinking_block":
                target = node
                break
        self.assertIsNotNone(target, "找不到 create_thinking_block")

        body = target.body
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            body = body[1:]          # 跳过 docstring
        code = "\n".join(ast.get_source_segment(src, stmt) or "" for stmt in body)
        code = "\n".join(line for line in code.splitlines()
                         if not line.strip().startswith("#"))   # 去掉行内注释
        self.assertNotIn("<details>", code)
        self.assertNotIn("<summary>", code)

    def test_percent_escaped_in_html_template(self):
        """`%` 格式化模板里的 HTML 宽度必须写成 %%，否则抛 ValueError。"""
        self.assertIn('width="88%%"', _read(THINKING))

    def test_no_stale_snapshot_fallback(self):
        """思考流兜底不得用「思考开始前的快照」，否则会冲掉期间新增的消息。"""
        self.assertNotIn("_thinking_base_html", _read(DOCK_V2))

    def test_toggle_anchor_supported(self):
        """思考块折叠依赖 #toggle-thinking 锚点。"""
        src = _read(DOCK_V2)
        self.assertIn("#toggle-thinking", src)
        self.assertIn("_toggle_thinking_block", src)


class TestMetadataGuards(unittest.TestCase):
    """对话头部元信息：窄 dock 下不能把最小宽度顶起来。"""

    MAX_METADATA_WIDTH_PX = 400

    def test_label_wraps(self):
        """元信息标签必须允许折行，否则长串会顶高 dock 最小宽度。"""
        src = _read(BASE_UI)
        marker = "self.lbMetadata"
        block = src[src.find(marker):]
        block = block[:block.find("\n\n")] if "\n\n" in block else block[:1200]
        self.assertIn("setWordWrap(True)", block)

    def test_uses_non_breaking_space_before_units(self):
        """日期时间与「数字+单位」内部用不换行空格，折行只发生在 `·` 处。"""
        src = _read(CONVERSATION)
        self.assertIn('nb = "\\u00a0"', src)
        self.assertIn("format_timestamp(self.created).replace", src)

    def test_metadata_is_reasonably_short(self):
        """元信息模板不得无限增长（字数上限守卫）。"""
        src = _read(CONVERSATION)
        start = src.find("def get_metadata")
        body = src[start:src.find("def clear", start)]
        self.assertLess(len(body), 700, "get_metadata 实现异常膨胀")


if __name__ == "__main__":
    unittest.main()
