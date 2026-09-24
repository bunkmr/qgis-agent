# -*- coding: utf-8 -*-
"""「一问一答一条记录」的写入/渲染契约守卫（源码级，不需要真实 Qt）。

本轮报障点：对话区**只显示回答，看不到用户自己发出去的内容**。

真因是**写入约定与渲染约定不一致**，而且是静默的：
  - 写入（``processor.agent_chat``）：interaction 的 ``typeMessage`` 恒为
    ``"return"``，``requestText`` 与 ``responseText`` **存于同一行**；
  - 渲染（``updateConversation``）：只在 ``typeMessage == "input"`` 时渲染
    用户气泡 —— 该分支**永不成立**（库里全是 return）。
于是**每次重建历史都会把用户气泡丢掉**。偏偏回答到达后就会重建一次，
现场表现就是「只剩回答」。

这类 bug 的可怕之处在于：它不会报错、不会写日志、单测也毫无察觉
（写入侧与渲染侧各自看都是"自洽"的）。所以这里把它钉成**跨文件契约**：

1. 从 ``processor.py`` 抽出真正写进 ``typeMessage`` 列的字面量；
2. 从 ``updateConversation`` 抽出它真正会渲染的类型集合；
3. 断言「写入的类型」必须被「渲染的类型」覆盖 —— 任何一侧单独改动
   都会立刻变红，而不是等到用户反馈。
4. 用户气泡必须只有**一处**生成函数（``user_bubble_html``）：发送时的即时
   渲染与重建历史共用它，否则回答一到就会出现样式跳变。
"""

import ast
import os
import unittest

try:
    from . import support
except ImportError:
    import support

ROOT = support.PROJECT_ROOT
DOCK_V2 = os.path.join(ROOT, "qgis_agent_dockwidget_v2.py")
PLUGIN = os.path.join(ROOT, "qgis_agent.py")
PROCESSOR = os.path.join(ROOT, "processor.py")

#: interaction 的**写入**列序。注意 `dataloader.insert_interaction` 会把主键 ID
#: 单独前置（`tuple([interaction_index] + interaction_info)`），所以列表字面量里
#: 只有下面 10 列、**不含 ID** —— 这一点搞错就会把 typeMessage 的列号数错一位。
INTERACTION_COLUMNS = [
    "conversationID", "promptID", "requestText", "contextText", "requestTime",
    "typeMessage", "responseText", "responseTime", "workflow", "executionLog",
]
TYPE_MESSAGE_INDEX = INTERACTION_COLUMNS.index("typeMessage")
REQUEST_TEXT_INDEX = INTERACTION_COLUMNS.index("requestText")

#: 旧的「用户消息」硬编码样式。它是本轮 bug 的另一半：即便用户气泡被渲染，
#: 发送时用手写 div、重建时用气泡卡片，回答一到就会肉眼可见地跳变。
LEGACY_USER_STYLE_MARKER = "text-align:right;color:#6baad1"


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _parse(path):
    return ast.parse(_read(path), filename=path)


def _func(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError("没找到函数 %s" % name)


def _const_str(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def written_type_messages(tree):
    """从 processor.py 抽出真正写进 typeMessage 列的字面量。

    写入形如::

        interaction_row = [conv_id, prompt_id, user_input, "", request_time,
                           "return", final_response, ...,  workflow, tool_log]
        self.dataloader.insert_interaction(interaction_row, self.conversation_id)

    这里只在**赋值就是列表字面量**时取值；若将来改成变量拼装，本守卫会
    返回空集合并在测试里显式失败（宁可提示"守卫失效"，也不要假绿）。
    """
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        targets = [t for t in node.targets if isinstance(t, ast.Name)]
        if not any(t.id == "interaction_row" for t in targets):
            continue
        value = node.value
        if not isinstance(value, ast.List):
            continue
        if len(value.elts) != len(INTERACTION_COLUMNS):
            continue
        literal = _const_str(value.elts[TYPE_MESSAGE_INDEX])
        if literal:
            found.add(literal)
    return found


def rendered_type_messages(func):
    """从 updateConversation 抽出它真正会渲染的 typeMessage 取值。"""
    render_types = set()
    for node in ast.walk(func):
        # message_type = msg_dict["typeMessage"]
        if isinstance(node, ast.Assign):
            names = [t for t in node.targets if isinstance(t, ast.Name)]
            if any(t.id == "message_type" for t in names):
                sub = node.value
                if (isinstance(sub, ast.Subscript)
                        and _const_str(sub.slice) == "typeMessage"):
                    render_types.add("__from_message_type__")
        if not isinstance(node, ast.Compare):
            continue
        left = node.left
        is_type_expr = (
            (isinstance(left, ast.Name) and left.id == "message_type")
            or (isinstance(left, ast.Subscript)
                and _const_str(left.slice) == "typeMessage")
        )
        if not is_type_expr:
            continue
        for op, comparator in zip(node.ops, node.comparators):
            if isinstance(op, ast.Eq):
                literal = _const_str(comparator)
                if literal:
                    render_types.add(literal)
    render_types.discard("__from_message_type__")
    return render_types


def _count_calls(func, attr_name):
    total = 0
    for node in ast.walk(func):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == attr_name:
                total += 1
    return total


class TestWriteRenderContract(unittest.TestCase):
    """写入侧与渲染侧必须对同一个约定负责。"""

    def test_processor_writes_return_type(self):
        """当前约定：一问一答存同一行，typeMessage 为 "return"。"""
        written = written_type_messages(_parse(PROCESSOR))
        self.assertTrue(
            written,
            "没能从 processor.py 抽出 interaction_row 的 typeMessage 列 —— "
            "写入方式可能被改成了变量拼装，请同步更新本守卫而不是删掉它",
        )
        self.assertIn("return", written,
                      "写入约定变了：现在实际写入的是 %s" % sorted(written))

    def test_render_covers_every_written_type(self):
        """★ 核心断言：写进去的类型，渲染必须认。"""
        written = written_type_messages(_parse(PROCESSOR))
        rendered = rendered_type_messages(_func(_parse(DOCK_V2), "updateConversation"))
        uncovered = written - rendered
        self.assertFalse(
            uncovered,
            "这些类型写进了库，但 updateConversation 不会渲染它：%s\n"
            "（渲染分支全集 = %s）—— 用户消息会因此凭空消失。"
            % (sorted(uncovered), sorted(rendered)),
        )

    def test_return_records_also_render_request_text(self):
        """return 行自带 requestText，必须一并渲染出用户气泡。"""
        func = _func(_parse(DOCK_V2), "updateConversation")
        # 注意：不要用 ast.dump 后 grep —— 它是否给字符串加引号随 Python 版本变，
        # 会得到一个随解释器飘的断言。这里直接收集字符串常量。
        literals = set()
        for node in ast.walk(func):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                literals.add(node.value)
            # msg_dict["requestText"] 的下标也是字符串常量，同样会被收进来
        self.assertIn("requestText", literals,
                      "updateConversation 不再读取 requestText —— "
                      "用户提问会被丢掉（本轮 bug 的原始形态）")
        self.assertGreaterEqual(
            _count_calls(func, "user_bubble_html"), 2,
            "updateConversation 里生成用户气泡的调用少于 2 处："
            "input 与 return 两条路径都要能渲染用户消息",
        )


class TestSingleSourceOfUserBubble(unittest.TestCase):
    """用户气泡只能有一个生成函数，否则两条路径的样式会漂移。"""

    def test_user_bubble_html_defined_on_dock(self):
        func = _func(_parse(DOCK_V2), "user_bubble_html")
        self.assertGreaterEqual(
            _count_calls(func, "_bubble_html"), 1,
            "user_bubble_html 必须复用 _bubble_html（气泡样式单一真源）",
        )

    def test_send_path_uses_the_same_helper(self):
        tree = _parse(PLUGIN)
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "user_bubble_html"
        ]
        self.assertTrue(
            calls,
            "qgis_agent.py 发送时没有调用 dockwidget.user_bubble_html —— "
            "即时渲染与重建历史各写一套 HTML，回答一到样式就会跳变",
        )

    def test_no_legacy_hardcoded_user_style(self):
        plugin_src = _read(PLUGIN)
        self.assertNotIn(
            LEGACY_USER_STYLE_MARKER, plugin_src,
            "qgis_agent.py 又出现了硬编码的用户消息样式 %r —— "
            "请改用 dockwidget.user_bubble_html" % LEGACY_USER_STYLE_MARKER,
        )


if __name__ == "__main__":
    unittest.main()
