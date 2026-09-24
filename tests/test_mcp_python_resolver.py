# -*- coding: utf-8 -*-
"""MCP 客户端配置里 `command` 的解释器解析测试。

背景（真实报障）：用户在 macOS 上按「复制客户端配置」拿到的片段里
``command`` 是 ``/Applications/QGIS.app/Contents/MacOS/QGIS`` —— GUI 主程序。
它不是解释器，不会读 stdin、不讲 JSON-RPC，客户端拉起来只会再弹一个 QGIS
窗口然后握手超时。根因是旧实现直接写 ``sys.executable``：macOS 上 QGIS 的
Python 嵌在 app 里，GUI 进程的 ``sys.executable`` 就是 GUI 主程序本身。

本文件锁住四件事：
1. 名字闸门：不是 python 命名的可执行文件绝不当解释器（也绝不去执行它）；
2. 优先「干净环境下就能跑」的候选 —— 客户端是在自己的环境里拉起 command 的；
3. 只在 QGIS 环境里能跑的候选只能兜底，且必须带醒目提示；
4. 全都不可用时不能回吐 sys.executable（那正是 bug 本体）。
"""

import os
import sys
import unittest
from unittest import mock

try:  # 既支持包方式导入，也支持 `unittest discover -s tests` 的顶层导入
    from . import support
except ImportError:
    import support

support.install_qgis_stub()
mb = support.import_mod("mcp_bridge")

SERVER = os.path.join(os.path.dirname(support.TESTS_DIR),
                      "mcp_server", "qgis_agent_mcp_server.py")

MAC_GUI = "/Applications/QGIS.app/Contents/MacOS/QGIS"
MAC_BUNDLED_PY = "/Applications/QGIS.app/Contents/MacOS/python3.12"
WIN_GUI = r"C:\OSGeo4W\bin\qgis-bin.exe"


def _fake_probe(mapping):
    """构造 _probe_environment 替身：mapping[path] -> 'clean' / 'inherited' / None"""
    def _probe(candidate, script=None):
        return mapping.get(str(candidate))
    return _probe


class TestLooksLikePython(unittest.TestCase):
    """名字闸门：探测会真的执行候选，判错就等于去启动一个 GUI 程序。"""

    def test_python_names_accepted(self):
        for path in ("/usr/bin/python3", "/usr/bin/python3.12",
                     "/Users/x/.venv/bin/python",
                     r"C:\OSGeo4W\apps\Python312\python.exe",
                     r"C:\OSGeo4W\bin\pythonw.exe",
                     MAC_BUNDLED_PY):
            with self.subTest(path=path):
                self.assertTrue(mb._looks_like_python(path), path)

    def test_non_python_names_rejected(self):
        for path in (MAC_GUI, WIN_GUI, "/Applications/QGIS-final-4_2_1.app/"
                     "Contents/MacOS/QGIS-final-4_2_1", "", None,
                     "/usr/bin/env", "/bin/sh"):
            with self.subTest(path=path):
                self.assertFalse(mb._looks_like_python(path), path)

    def test_windows_path_uses_backslash_as_separator(self):
        # 在 POSIX 上 os.path.basename 会把整串当文件名，必须自己按两种分隔符切
        self.assertFalse(mb._looks_like_python(r"C:\QGIS\bin\qgis-bin.exe"))
        self.assertTrue(mb._looks_like_python(r"C:\QGIS\bin\python3.exe"))


class TestCandidateOrdering(unittest.TestCase):

    def test_non_python_candidates_are_filtered_out(self):
        with mock.patch.object(sys, "executable", MAC_GUI):
            candidates, _script = mb._python_candidates(
                python_executable=MAC_GUI, server_script=SERVER)
        self.assertNotIn(MAC_GUI, candidates)

    def test_explicit_candidate_comes_first(self):
        with mock.patch.object(sys, "executable", "/usr/bin/python3"):
            candidates, _script = mb._python_candidates(
                python_executable="/opt/py/bin/python3", server_script=SERVER)
        self.assertEqual(candidates[0], "/opt/py/bin/python3")
        self.assertIn("/usr/bin/python3", candidates)

    def test_candidates_are_unique(self):
        with mock.patch.object(sys, "executable", "/usr/bin/python3"):
            candidates, _script = mb._python_candidates(
                python_executable="/usr/bin/python3", server_script=SERVER)
        self.assertEqual(len(candidates), len(set(candidates)))


class TestResolvePrefersCleanEnv(unittest.TestCase):
    """客户端在**自己的**环境里拉起 command，所以干净环境可用才是硬指标。"""

    def setUp(self):
        mb.clear_python_cache()
        self.addCleanup(mb.clear_python_cache)
        self.bundled = "/Applications/QGIS.app/Contents/MacOS/python3.12"
        self.system = "/usr/bin/python3"

    def _resolve(self, mapping, candidates, executable="", script=SERVER):
        with mock.patch.object(mb, "_python_candidates",
                               return_value=(list(candidates), script)), \
                mock.patch.object(mb, "_probe_environment",
                                  side_effect=_fake_probe(mapping)), \
                mock.patch.object(sys, "executable", executable):
            mb.clear_python_cache()
            return mb.resolve_python_executable(server_script=script)

    def test_clean_capable_candidate_beats_earlier_inherited_only(self):
        # 反向验证：把优先级改回「第一个能跑的就行」，这里会返回 python3.12 → 失败
        path, note = self._resolve(
            {self.bundled: "inherited", self.system: "clean"},
            [self.bundled, self.system], executable=self.bundled)
        self.assertEqual(path, self.system)
        # 换掉了 sys.executable 就该说明；但**不能**是那句「只在 QGIS 环境下能跑」
        # 的警告 —— 选中的这个在干净环境里是好的
        self.assertIn("已自动改用", note)
        self.assertNotIn("环境变量", note)

    def test_inherited_only_is_last_resort_with_warning(self):
        path, note = self._resolve(
            {self.bundled: "inherited"}, [self.bundled], executable=self.bundled)
        self.assertEqual(path, self.bundled)
        self.assertIn("⚠", note)
        self.assertIn("环境变量", note)

    def test_unusable_candidate_is_skipped(self):
        path, _note = self._resolve(
            {self.bundled: None, self.system: "clean"},
            [self.bundled, self.system], executable=self.bundled)
        self.assertEqual(path, self.system)

    def test_gui_executable_is_never_returned(self):
        path, note = self._resolve(
            {}, [MAC_GUI], executable=MAC_GUI)
        # 宁可给一个裸命令名让客户端走 PATH，也绝不回吐 GUI 主程序
        self.assertNotEqual(path, MAC_GUI)
        self.assertEqual(path, mb.PYTHON_PATH_COMMAND)
        self.assertIn("⚠", note)

    def test_fallback_when_current_executable_is_not_python(self):
        # macOS GUI 场景：全都不可用时，宁可给一个裸命令名让客户端走 PATH，
        # 也不能回吐 GUI 主程序（那正是用户踩到的 bug）
        path, note = self._resolve({}, [], executable=MAC_GUI)
        self.assertEqual(path, mb.PYTHON_PATH_COMMAND)
        self.assertIn("⚠", note)

    def test_fallback_keeps_python_executable(self):
        path, note = self._resolve({}, [], executable=self.system)
        self.assertEqual(path, self.system)
        self.assertIn("⚠", note)

    def test_result_is_cached(self):
        calls = []

        def _counting(candidate, script=None):
            calls.append(candidate)
            return "clean"

        with mock.patch.object(mb, "_python_candidates",
                               return_value=([self.system], SERVER)), \
                mock.patch.object(mb, "_probe_environment",
                                  side_effect=_counting):
            mb.clear_python_cache()
            first = mb.resolve_python_executable(server_script=SERVER)
            second = mb.resolve_python_executable(server_script=SERVER)
        self.assertEqual(first, second)
        self.assertEqual(len(calls), 1)

    def test_probe_limit_counts_real_probes_not_positions(self):
        # 按下标截断会把 PATH 上的可用解释器一起截掉（实测踩过）
        nonexistent = ["/nope/python%s" % i for i in range(20)]
        with mock.patch.object(
                mb, "_python_candidates",
                return_value=(nonexistent + [self.system], SERVER)), \
                mock.patch.object(mb, "_probe_environment",
                                  side_effect=_fake_probe({self.system: "clean"})):
            mb.clear_python_cache()
            path, _note = mb.resolve_python_executable(server_script=SERVER)
        self.assertEqual(path, self.system)


class TestProbeEnvironment(unittest.TestCase):
    """探测本身的两个硬约束：不执行非 python 候选、必须先试干净环境。"""

    def test_probe_refuses_non_python_binary(self):
        with mock.patch.object(mb, "_try_run") as runner:
            self.assertIsNone(mb._probe_environment(MAC_GUI, SERVER))
        runner.assert_not_called()

    def test_probe_tries_clean_env_first(self):
        seen = []

        def _try_run(argv, env=None):
            seen.append(env)
            return env is not None      # 只有干净环境能跑

        with mock.patch.object(mb, "_try_run", side_effect=_try_run), \
                mock.patch.object(mb.os.path, "exists", return_value=True), \
                mock.patch.object(mb.os, "access", return_value=True):
            result = mb._probe_environment("/usr/bin/python3", SERVER)
        self.assertEqual(result, "clean")
        self.assertIsNotNone(seen[0])

    def test_probe_falls_back_to_inherited_env(self):
        def _try_run(argv, env=None):
            return env is None          # 只有继承环境能跑

        with mock.patch.object(mb, "_try_run", side_effect=_try_run), \
                mock.patch.object(mb.os.path, "exists", return_value=True), \
                mock.patch.object(mb.os, "access", return_value=True):
            result = mb._probe_environment("/usr/bin/python3", SERVER)
        self.assertEqual(result, "inherited")

    def test_missing_file_is_not_probed(self):
        with mock.patch.object(mb, "_try_run") as runner, \
                mock.patch.object(mb.os.path, "exists", return_value=False):
            self.assertIsNone(mb._probe_environment("/x/python3", SERVER))
        runner.assert_not_called()

    def test_clean_env_drops_python_vars(self):
        with mock.patch.dict(os.environ,
                             {"PYTHONPATH": "/x", "PYTHONHOME": "/y",
                              "PATH": "/usr/bin", "GENERIC": "1"}):
            env = mb._clean_env()
        self.assertNotIn("PYTHONPATH", env)
        self.assertNotIn("PYTHONHOME", env)
        self.assertEqual(env.get("PATH"), "/usr/bin")
        self.assertEqual(env.get("GENERIC"), "1")


class TestClientConfig(unittest.TestCase):

    def setUp(self):
        mb.clear_python_cache()
        self.addCleanup(mb.clear_python_cache)

    def _bridge(self):
        bridge = mb.MCPBridge()
        bridge._port = 9876
        bridge._token = "a" * 64
        return bridge

    def test_config_shape(self):
        with mock.patch.object(mb, "_python_candidates",
                               return_value=(["/usr/bin/python3"], SERVER)), \
                mock.patch.object(mb, "_probe_environment",
                                  side_effect=_fake_probe({"/usr/bin/python3": "clean"})), \
                mock.patch.object(sys, "executable", MAC_GUI):
            mb.clear_python_cache()
            cfg = self._bridge().client_config()

        spec = cfg["mcpServers"]["qgis-agent"]
        self.assertEqual(spec["command"], "/usr/bin/python3")
        self.assertEqual(spec["args"], [SERVER])
        self.assertEqual(spec["env"]["QGIS_AGENT_MCP_PORT"], "9876")
        self.assertEqual(spec["env"]["QGIS_AGENT_MCP_TOKEN"], "a" * 64)

    def test_config_command_is_python_even_when_gui_is_executable(self):
        with mock.patch.object(mb, "_python_candidates",
                               return_value=(["/usr/bin/python3"], SERVER)), \
                mock.patch.object(mb, "_probe_environment",
                                  side_effect=_fake_probe({"/usr/bin/python3": "clean"})), \
                mock.patch.object(sys, "executable", MAC_GUI):
            mb.clear_python_cache()
            cfg = self._bridge().client_config()
        self.assertTrue(mb._looks_like_python(
            cfg["mcpServers"]["qgis-agent"]["command"]))

    def test_hint_is_recorded_for_ui(self):
        with mock.patch.object(mb, "_python_candidates",
                               return_value=(["/nope/python3"], SERVER)), \
                mock.patch.object(mb, "_probe_environment",
                                  side_effect=_fake_probe({})), \
                mock.patch.object(sys, "executable", MAC_GUI):
            mb.clear_python_cache()
            bridge = self._bridge()
            bridge.client_config()
        self.assertTrue(bridge.last_python_hint)
        self.assertIn("⚠", bridge.last_python_hint)

    def test_hint_is_empty_when_nothing_needed_replacing(self):
        with mock.patch.object(mb, "_python_candidates",
                               return_value=(["/usr/bin/python3"], SERVER)), \
                mock.patch.object(mb, "_probe_environment",
                                  side_effect=_fake_probe({"/usr/bin/python3": "clean"})), \
                mock.patch.object(sys, "executable", "/usr/bin/python3"):
            mb.clear_python_cache()
            bridge = self._bridge()
            bridge.client_config()
        self.assertEqual(bridge.last_python_hint, "")


if __name__ == "__main__":
    unittest.main()
