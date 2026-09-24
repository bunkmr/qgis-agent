# -*- coding: utf-8 -*-
"""help_content.py 测试：内容准确性 + 「Qt 富文本能力边界」守卫。

为什么要用测试守住样式：

    QTextDocument 只实现 CSS 2.1 的一个子集，而且对不支持的声明是**静默忽略**
    （实测 `var(--x, #fallback)` 连 fallback 一起丢掉）。帮助页历史上因此又乱又扁：
    `display:flex` 整块布局失效、`linear-gradient` 渲染成白块、`border-radius`
    渲染成直角、`tr:nth-child` 的斑马纹不生效 —— 而在浏览器里预览却「看起来正常」。
    这些坑只能靠断言钉住，不能靠肉眼在富文本里发现。
"""

import unittest

try:  # 既支持以包方式导入，也支持 `unittest discover -s tests` 的顶层导入
    from . import support
except ImportError:
    import support  # noqa: F401

# QTextDocument 明确不支持 / 本模块刻意不用的写法
FORBIDDEN_CSS = (
    "var(--",            # CSS 自定义属性：整条声明被丢弃，连 fallback 都不生效
    "border-radius",     # 渲染为直角
    "linear-gradient",   # 渐变不支持
    "flex",              # display:flex / gap 等弹性布局不支持
    "nth-child",         # 结构化伪类不支持
    "<details",          # 折叠控件不支持
    "<summary",
    "<script",           # 不执行 JS
)

TAB_LABELS = ("对话", "历史", "模型", "工作流", "报告", "帮助")

FAKE_TOOLS = [
    {"name": "get_qgis_info", "description": "获取 QGIS 当前状态信息：版本、项目路径、坐标系"},
    {"name": "add_vector_layer", "description": "添加矢量图层到当前项目"},
    {"name": "<script>x</script>", "description": "危险名字要转义"},
]


class HelpContentTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.help_content = support.import_mod("help_content")

    def build(self, **kwargs):
        kwargs.setdefault("version", "9.9.9")
        kwargs.setdefault("tools", FAKE_TOOLS)
        kwargs.setdefault("tool_count", len(FAKE_TOOLS))
        return self.help_content.build_help_html(**kwargs)


class TestUnsupportedCssIsAbsent(HelpContentTestCase):
    def test_no_unsupported_css(self):
        html = self.build()
        lowered = html.lower()
        for token in FORBIDDEN_CSS:
            with self.subTest(token=token):
                self.assertNotIn(token.lower(), lowered)

    def test_table_width_must_be_attribute_not_css(self):
        """`table { width: 100% }` 在 QTextDocument 里不生效（实测 width=0），
        必须用 `width="100%"` 属性。"""
        html = self.build()
        self.assertIn('width="100%"', html)
        self.assertNotIn("width: 100%", html)

    def test_border_collapse_is_used(self):
        """不 collapse 时相邻单元格边框会叠成 2px 粗线。"""
        self.assertIn("border-collapse:collapse", self.build())

    def test_no_javascript_or_external_resources(self):
        """离线可用：不得引用任何外部资源。"""
        lowered = self.build().lower()
        self.assertNotIn("http://", lowered.replace("http://127.0.0.1", ""))
        self.assertNotIn("<img", lowered)
        self.assertNotIn("<iframe", lowered)


class TestNoUnresolvedPlaceholders(HelpContentTestCase):
    def test_all_tokens_substituted(self):
        html = self.build()
        for token in ("__VERSION__", "__TOOLS_ROWS__", "__TOOL_COUNT__",
                      "__BG__", "__FG__", "__MUTED__", "__BORDER__",
                      "__CODE_BG__", "__PANEL__", "__TOOLBG__", "__ACCENT__",
                      "__MONO__", "__SANS__",
                      "__CARD_TIP__", "__CARD_LOCAL__"):
            with self.subTest(token=token):
                self.assertNotIn(token, html)

    def test_no_double_percent_leftover(self):
        """模板里用 __TOKEN__ 占位而不是 % 格式化，正是为了避免 width="100%%" 这类
        转义遗漏；这里反向确认没有残留。"""
        self.assertNotIn("%%", self.build())


class TestContentAccuracy(HelpContentTestCase):
    def test_version_is_injected(self):
        self.assertIn("9.9.9", self.build(version="9.9.9"))

    def test_version_has_fallback_text(self):
        html = self.build(version="")
        self.assertNotIn("__VERSION__", html)
        self.assertIn("插件管理器", html)

    def test_mentions_every_tab(self):
        html = self.build()
        for label in TAB_LABELS:
            with self.subTest(label=label):
                self.assertIn(label, html)

    def test_documents_current_features(self):
        """帮助页必须覆盖 v2.3/v2.4 引入的能力，否则就是过期文档。"""
        html = self.build()
        for keyword in ("MCP", "诊断", "llama.cpp", "Ollama", "LM Studio",
                        "/v1", "curl_cffi", "function calling",
                        "测试连接与诊断", "复制报错详情"):
            with self.subTest(keyword=keyword):
                self.assertIn(keyword, html)

    def test_documents_local_model_pitfalls(self):
        """本地模型「别处能用、这里不行」的两个真因必须写在帮助里。"""
        html = self.build()
        self.assertIn("--jinja", html)
        self.assertIn("ctx-size", html)

    def test_github_url_matches_remote(self):
        """仓库地址曾写成 qgis_agent（下划线），与实际 remote（qgis-agent）不符。"""
        html = self.build()
        self.assertIn("github.com/bunkmr/qgis-agent", html)
        self.assertNotIn("github.com/bunkmr/qgis_agent", html)

    def test_has_no_duplicate_h2_titles(self):
        """旧版帮助页「相关链接」标题被写了两遍。"""
        import re
        titles = re.findall(r"<h2>(.*?)</h2>", self.build())
        self.assertEqual(len(titles), len(set(titles)), "存在重复的二级标题：%s" % titles)


class TestToolRows(HelpContentTestCase):
    def test_rows_cover_all_tools(self):
        rows = self.help_content.build_tool_rows(FAKE_TOOLS)
        self.assertIn("get_qgis_info", rows)
        self.assertIn("add_vector_layer", rows)

    def test_names_and_descriptions_are_escaped(self):
        rows = self.help_content.build_tool_rows(FAKE_TOOLS)
        self.assertNotIn("<script>", rows)
        self.assertIn("&lt;script&gt;", rows)

    def test_long_description_is_truncated(self):
        rows = self.help_content.build_tool_rows(
            [{"name": "t", "description": "很长的描述" * 40}], desc_limit=10)
        self.assertIn("…", rows)
        self.assertLess(len(rows), 400)

    def test_empty_tools_degrades_gracefully(self):
        rows = self.help_content.build_tool_rows([])
        self.assertIn("colspan", rows)

    def test_none_is_tolerated(self):
        self.assertIn("colspan", self.help_content.build_tool_rows(None))

    def test_tool_count_is_rendered(self):
        html = self.build(tools=FAKE_TOOLS, tool_count=3)
        self.assertIn("3 个", html)


class TestColorInjection(HelpContentTestCase):
    def test_palette_colors_are_used(self):
        html = self.build(colors={"fg": "#123456", "user_edge": "#ABCDEF"})
        self.assertIn("#123456", html)
        self.assertIn("#ABCDEF", html)

    def test_missing_colors_fall_back_to_light_theme(self):
        """调色板取不到时也要有可读的浅色兜底，不能出现空色值。"""
        html = self.build(colors=None)
        self.assertIn(self.help_content._FALLBACK_COLORS["fg"], html)
        self.assertNotIn("background-color: ;", html)

    def test_partial_colors_are_merged(self):
        html = self.build(colors={"fg": "#010203"})
        self.assertIn("#010203", html)
        self.assertIn(self.help_content._FALLBACK_COLORS["border"], html)

    def test_placeholder_map_covers_all_tokens_in_template(self):
        """占位符表与模板必须一一对应，漏一个就会把 __TOKEN__ 原样渲染给用户。"""
        for token in self.help_content._COLOR_TOKENS:
            with self.subTest(token=token):
                self.assertIn(token, self.help_content._TEMPLATE)


if __name__ == "__main__":
    unittest.main()
