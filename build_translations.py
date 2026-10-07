#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""翻译文件维护脚本（开发期工具，不随插件分发）。

做五件事：

 1. ``--extract``  用 Qt 官方的 pylupdate 从源码提取待翻译字符串，
                   合并进 ``i18n/qgis_agent_en.ts``
 2. ``--identity`` 由 en.ts 生成 ``i18n/qgis_agent_zh_CN.ts``
                   （翻译值 = 原文，用于把界面锁定回中文，见 i18n/__init__.py）
 3. ``--qm``       用 lrelease 把两份 .ts 编译成 ``.qm``（随插件分发）
 4. ``--messages`` 由两份 .ts 生成 ``i18n/messages_<code>.json``
                   （运行时零依赖降级表，见下）
 5. ``--check``    校验两份 .ts 的 (context, source) 集合完全一致，
                   并校验 ``messages_*.json`` 与 .ts 同步

用法::

    # 全流程（extract 需要 QGIS 自带的 Python，因为 pylupdate 在其中）
    QGIS_PY="/Applications/QGIS.app/Contents/MacOS/python3.12" \\
        python3 build_translations.py

    # 只编译 .qm（换机器、只改了 zh 的手工译文时用）
    python3 build_translations.py --qm

    # 只校验（纯标准库即可，CI / 单测用）
    python3 build_translations.py --check

为什么自己合并、而不是让 pylupdate 直接改 .ts：

    pylupdate 对已有 .ts 的 merge 行为在 Qt 各个版本里并不一致，一旦它把
    ``translation`` 清空，人工翻译就**静默丢失**（而且 diff 里看不出来，
    因为改动只是若干空 <translation/>）。这里的做法是：pylupdate 只负责产出
    **骨架**（context/source/location），翻译一律由本脚本按 source 回填，
    保证任何一次提取都不会吃掉已有译文。

关于 lrelease（只影响第 3 步）：

    它是**构建期**依赖，最终用户不需要装。本机没有 Qt 自带的 lrelease，
    实际用的是 ``pip install PySide6-Essentials`` 带来的 ``pyside6-lrelease``。
    已实测它产出的 .qm（magic ``3cb86418``）**Qt 5.15.19 与 Qt 6.11.1 都能读**，
    与 QGIS 自带 .qm 的格式一致。找不到 lrelease 时只跳过编译并告警，
    不影响 .ts 与 messages_*.json 的生成 —— 运行时能退回读后者。

关于 ``messages_<code>.json``（第 4 步，运行时降级表）：

    运行时的正路是 .qm。但 .qm 缺失 / 损坏 / 与当前 Qt 版本不兼容时需要一条
    退路，而这条退路**不能**是直接解析 .ts —— 解析 XML 要用
    ``xml.etree.ElementTree``，插件仓库的 Bandit 扫描会把它判为 B405/B314，
    **只要包里有任意一条 Bandit 发现，整个版本就会被 BLOCKED**
    （v2.4.14 首次上传时正是这样被挡住的）。所以退路改成读 JSON，
    用标准库 ``json``，零依赖。它与 .qm 同源（同一份 .ts），不会漂移，
    且由 ``--check`` 兜住。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.abspath(__file__))
I18N = os.path.join(ROOT, "i18n")
EN_TS = os.path.join(I18N, "qgis_agent_en.ts")
ZH_TS = os.path.join(I18N, "qgis_agent_zh_CN.ts")
EN_QM = os.path.join(I18N, "qgis_agent_en.qm")
ZH_QM = os.path.join(I18N, "qgis_agent_zh_CN.qm")
#: 运行时零依赖降级表。文件名必须是 ``messages_<code>.json``，
#: 与 i18n/__init__.py 的 ``messages_file()`` 一一对应。
EN_MESSAGES = os.path.join(I18N, "messages_en.json")
ZH_MESSAGES = os.path.join(I18N, "messages_zh_CN.json")

#: 本机 lrelease 的兜底路径（来自托管 venv 里的 PySide6-Essentials）。
_FALLBACK_LRELEASE = (
    "/Users/Apple/.workbuddy/binaries/python/envs/default/bin/pyside6-lrelease",
)

#: Qt 5.15 起的 .qm magic。低于这个的旧格式 QGIS 4 未必读得动，
#: 编译完顺手验一下，避免"编出来但装不进去"这种静默失败。
QM_MAGIC = b"\x3c\xb8\x64\x18"

#: 人工维护的英文翻译表（source → translation）。存在时**优先于** .ts 里已有的
#: 译文 —— 它是真源，.ts 是产物。用 JSON 而不是直接编辑 XML：更好 diff、
#: 更好做批量维护，也更容易被单测读取校验。
EN_JSON = os.path.join(I18N, "en.json")

#: QTranslator 的上下文名，必须与 i18n/__init__.py 的 TRANSLATION_CONTEXT、
#: 以及代码里 _translate("QGISAgent", ...) 的第一个参数一致。
CONTEXT = "QGISAgent"

#: 不参与提取的目录（都是开发/数据资产，不含界面文案）。
SKIP_DIRS = {
    ".git", ".workbuddy", "__pycache__", ".pytest_cache", "dist", "build",
    "tests", "scripts", "rag", "help", "data", "extlibs", "i18n",
    "tool_docs", "skills",
}

#: 不参与提取的单个文件。
SKIP_FILES = {
    "build_plugin.py", "build_translations.py", "install_v2.py",
    "import_tool_docs.py", "generate_icon.py", "test_official_docs.py",
}


def source_files():
    """列出所有可能含界面文案的 .py。"""
    out = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in sorted(filenames):
            if not name.endswith(".py"):
                continue
            if name in SKIP_FILES or name.startswith("."):
                continue
            out.append(os.path.join(dirpath, name))
    return sorted(out)


def qgis_python():
    """找到带 pylupdate 的 Python（QGIS 自带）。"""
    for key in ("QGIS_PY", "QGIS_PYTHON"):
        value = os.environ.get(key)
        if value and os.path.isfile(value):
            return value
    for candidate in (
        "/Applications/QGIS.app/Contents/MacOS/python3.12",
        "/Applications/QGIS-final-4_2_1.app/Contents/MacOS/python3",
    ):
        if os.path.isfile(candidate):
            return candidate
    return sys.executable


def clean_env(executable=None):
    """构造跑 QGIS 自带 python 的环境变量，两件事缺一不可。

    ⚠️ 必须做：

    1. **清掉外来 PYTHONPATH**。在 WorkBuddy / 各类 IDE 的集成终端里，
       ``PYTHONPATH`` 常被指向工具自带的 shim 目录；QGIS 的 python 继承后会去
       错地方找 stdlib，报的是极具误导性的

           Fatal Python error: init_fs_encoding: failed to get the Python codec
           ModuleNotFoundError: No module named 'encodings'

       —— 看起来像"QGIS 的 Python 装坏了"，实际只是 PYTHONPATH 没剥干净。

    2. **补上 QGIS 自己的 stdlib 路径**。本机 QGIS.app 的 python 是在 CI 上构建的，
       二进制里烧死的 ``sys.base_prefix`` 指向 ``/Users/runner/work/QGIS/...``
       （本机根本不存在），因此它**只能**靠 PYTHONPATH 找到自己的 encodings。
       所以"只清不补"必然炸 —— 必须把对应 ``Contents/Frameworks/lib/pythonX.Y``
       系列路径补回去。
    """
    env = dict(os.environ)
    for key in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"):
        env.pop(key, None)

    exe = os.path.abspath(executable or "")
    marker = os.sep + "Contents" + os.sep
    if marker not in exe:
        return env                     # 普通 Python：剥掉外来 PYTHONPATH 就够了

    app = exe.split(marker)[0]
    lib = os.path.join(app, "Contents", "Frameworks", "lib")
    if not os.path.isdir(lib):
        return env
    for name in sorted(os.listdir(lib)):
        if not name.startswith("python"):
            continue
        base = os.path.join(lib, name)
        parts = [base, os.path.join(base, "lib-dynload"),
                 os.path.join(base, "site-packages")]
        env["PYTHONPATH"] = os.pathsep.join(p for p in parts if os.path.isdir(p))
        break
    return env


def _discard(path):
    """删临时产物，删不掉也不影响结果。

    ⚠️ 别把它写成硬失败：源码目录常挂在网络同步卷（Resilio / Dropbox 等）上，
    那种卷不支持 macOS 回收站，``os.remove`` 会被安全删除层拒掉。骨架文件本身
    没有任何价值，为此中断整个构建毫无意义。
    """
    try:
        os.remove(path)
    except OSError as exc:
        print("  ℹ️ 临时文件未删除（不影响结果）：%s" % exc)


def extract():
    """跑 pylupdate 产出骨架 .ts（不含翻译），返回其路径。"""
    files = source_files()
    if not files:
        raise SystemExit("没找到任何源文件")

    # ⚠️ 骨架放在系统临时目录、且名字带 pid：放源码目录会踩同步卷的删除限制，
    # 复用同一个名字则会让 pylupdate 把上一轮的旧条目当"已消失"合并进来。
    skeleton = os.path.join(
        tempfile.gettempdir(), "_qgis_agent_skeleton_%d.ts" % os.getpid())
    py = qgis_python()
    cmd = [py, "-m", "PyQt5.pylupdate_main"] + files + ["-ts", skeleton]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          env=clean_env(py))
    if not os.path.isfile(skeleton):
        sys.stderr.write(proc.stdout + proc.stderr)
        raise SystemExit("pylupdate 未能生成骨架 .ts（检查 QGIS_PY 是否指向 QGIS 的 Python）")
    print("提取源文件 %d 个 → 骨架 %s" % (len(files), os.path.basename(skeleton)))
    return skeleton


def lrelease():
    """定位 lrelease；找不到返回 None（只跳过 .qm 编译，不算失败）。"""
    for key in ("LRELEASE", "QT_LRELEASE"):
        value = os.environ.get(key)
        if value and os.path.isfile(value):
            return value
    for name in ("pyside6-lrelease", "lrelease6", "lrelease"):
        found = shutil.which(name)
        if found:
            return found
    for candidate in _FALLBACK_LRELEASE:
        if os.path.isfile(candidate):
            return candidate
    return None


def _verify_qm(path):
    """校验 .qm 的 magic；不对就返回 False（宁可直读 .ts 也别装个读不动的）。"""
    try:
        with open(path, "rb") as handle:
            return handle.read(4) == QM_MAGIC
    except OSError:
        return False


def compile_qm(ts_path=EN_TS, qm_path=EN_QM, tool=None):
    """把 .ts 编译成 .qm。成功返回 True。"""
    tool = tool or lrelease()
    if tool is None:
        print("  ! 未找到 lrelease，跳过 .qm 编译（运行时将直读 .ts）")
        return False
    proc = subprocess.run([tool, ts_path, "-qm", qm_path],
                          capture_output=True, text=True, env=clean_env())
    if proc.returncode != 0 or not os.path.isfile(qm_path):
        print("  ! lrelease 失败：%s" % (proc.stdout + proc.stderr).strip())
        return False
    if not _verify_qm(qm_path):
        print("  ! %s 的 magic 不是 %s，Qt 版本可能过旧"
              % (os.path.basename(qm_path), QM_MAGIC.hex()))
        return False
    size = os.path.getsize(qm_path)
    print("写出 %s：%d 字节" % (os.path.basename(qm_path), size))
    return True


def compile_all(tool=None):
    """编译两份 .qm，返回是否全部成功。"""
    tool = tool or lrelease()
    ok = compile_qm(EN_TS, EN_QM, tool)
    ok = compile_qm(ZH_TS, ZH_QM, tool) and ok
    return ok


def write_messages_json(ts_path, out_path):
    """由 .ts 生成运行时零依赖降级表，返回写出的条目数。

    ⚠️ 只保留 ``context == CONTEXT`` 的条目：``MessageTranslator.translate()``
    收到的 context 参数虽然仍是 "QGISAgent"，但表里再存一份没有意义，
    而且以后若真出现第二个 context，键冲突会静默覆盖。
    """
    messages = {}
    for (context, source), text in sorted(read_ts(ts_path).items()):
        if context == CONTEXT and source and text:
            messages[source] = text
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(messages, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    return len(messages)


def write_all_messages():
    """生成两份降级表，返回是否都非空。"""
    ok = True
    for ts_path, out_path in ((EN_TS, EN_MESSAGES), (ZH_TS, ZH_MESSAGES)):
        count = write_messages_json(ts_path, out_path)
        print("  %s ← %s（%d 条）" % (
            os.path.basename(out_path), os.path.basename(ts_path), count))
        # 空表等于没有降级能力，而且是**静默**的：宁可在这里报出来
        ok = (count > 0) and ok
    return ok


def read_json_translations(path=EN_JSON):
    """读 i18n/en.json，返回 ``{source: translation}``（不含 context —— 只有一个）。"""
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception as exc:
        print("  ! 解析 %s 失败: %s" % (path, exc))
        return {}
    # 允许一个描述性的顶层包装："_comment" / "_note" 之类的下划线键一律忽略
    return {k: v for k, v in data.items()
            if not k.startswith("_") and isinstance(v, str)}


def read_ts(path):
    """读 .ts，返回 ``{(context, source): translation}``（只含已翻译条目）。"""
    data = {}
    if not os.path.isfile(path):
        return data
    try:
        root = ET.parse(path).getroot()
    except Exception as exc:
        print("  ! 解析 %s 失败: %s" % (path, exc))
        return data
    for context in root.findall("context"):
        name = (context.findtext("name") or "").strip()
        for message in context.findall("message"):
            source = message.findtext("source") or ""
            node = message.find("translation")
            if node is None or (node.get("type") or "") in ("unfinished", "vanished"):
                continue
            if node.text:
                data[(name, source)] = node.text
    return data


def merge(skeleton_path, translations, out_path):
    """把翻译回填进骨架并写出。"""
    tree = ET.parse(skeleton_path)
    root = tree.getroot()
    filled = missing = 0
    for context in root.findall("context"):
        name = (context.findtext("name") or "").strip()
        for message in context.findall("message"):
            source = message.findtext("source") or ""
            node = message.find("translation")
            if node is None:
                node = ET.SubElement(message, "translation")
            value = translations.get((name, source))
            if value:
                node.text = value
                node.attrib.pop("type", None)
                filled += 1
            else:
                node.text = None
                node.set("type", "unfinished")
                missing += 1
    _indent(root)
    tree.write(out_path, encoding="utf-8", xml_declaration=True)
    print("写出 %s：已翻译 %d 条，待翻译 %d 条" % (
        os.path.basename(out_path), filled, missing))
    return missing


def write_identity(src_ts, out_path):
    """由 en.ts 生成中文恒等 .ts（translation = source）。"""
    tree = ET.parse(src_ts)
    root = tree.getroot()
    count = 0
    for context in root.findall("context"):
        for message in context.findall("message"):
            source = message.findtext("source") or ""
            node = message.find("translation")
            if node is None:
                node = ET.SubElement(message, "translation")
            node.text = source
            node.attrib.pop("type", None)
            count += 1
    _indent(root)
    tree.write(out_path, encoding="utf-8", xml_declaration=True)
    print("写出 %s：%d 条恒等映射（中文 = 原文）" % (
        os.path.basename(out_path), count))


def expected_messages(ts_path):
    """由 .ts 推出运行时降级表应当是什么样（与 write_messages_json 同一口径）。"""
    return {source: text
            for (context, source), text in read_ts(ts_path).items()
            if context == CONTEXT and source and text}


def check(en_ts=EN_TS, zh_ts=ZH_TS):
    """校验两份 .ts 的 (context, source) 集合一致、且降级表与 .ts 同步。

    返回问题清单（空 = 通过）。
    """
    def sources(path):
        root = ET.parse(path).getroot()
        out = set()
        for context in root.findall("context"):
            name = (context.findtext("name") or "").strip()
            for message in context.findall("message"):
                out.add((name, message.findtext("source") or ""))
        return out

    en = sources(en_ts)
    zh = sources(zh_ts)
    print("en 条目 %d / zh_CN 条目 %d" % (len(en), len(zh)))
    problems = []
    if en - zh:
        problems.append("zh_CN 缺少 %d 条: %s" % (len(en - zh), sorted(en - zh)[:5]))
    if zh - en:
        problems.append("zh_CN 多出 %d 条: %s" % (len(zh - en), sorted(zh - en)[:5]))
    if not problems:
        print("  ✓ 两份 .ts 的条目集合一致")

    # 降级表必须与 .ts 同步 —— 它不参与运行时的 XML 解析，所以两者漂移了
    # 只会在"正好用上降级路径"时暴露，属于最难发现的那种静默退化。
    for ts_path, json_path in ((en_ts, EN_MESSAGES), (zh_ts, ZH_MESSAGES)):
        expected = expected_messages(ts_path)
        try:
            with open(json_path, encoding="utf-8") as handle:
                actual = json.load(handle)
        except (OSError, ValueError) as exc:
            problems.append("%s 读不了（跑 --messages 生成）: %s"
                            % (os.path.basename(json_path), exc))
            continue
        if actual != expected:
            lost = sorted(set(expected) - set(actual))
            extra = sorted(set(actual) - set(expected))
            changed = sorted(k for k in set(expected) & set(actual)
                             if expected[k] != actual[k])
            problems.append(
                "%s 与 %s 不同步：缺 %d 条%s / 多 %d 条%s / 内容不同 %d 条%s"
                % (os.path.basename(json_path), os.path.basename(ts_path),
                   len(lost), ("（如 %r）" % lost[0] if lost else ""),
                   len(extra), ("（如 %r）" % extra[0] if extra else ""),
                   len(changed), ("（如 %r）" % changed[0] if changed else "")))
    if not any("不同步" in p or "读不了" in p for p in problems):
        print("  ✓ 两份 messages_*.json 与 .ts 同步")

    for p in problems:
        print("  ✗", p)
    return problems


def main():
    parser = argparse.ArgumentParser(description="翻译文件维护")
    parser.add_argument("--extract", action="store_true", help="从源码提取并合并进 en.ts")
    parser.add_argument("--identity", action="store_true", help="由 en.ts 生成 zh_CN.ts")
    parser.add_argument("--qm", action="store_true", help="把两份 .ts 编译成 .qm")
    parser.add_argument("--messages", action="store_true",
                        help="由两份 .ts 生成 messages_<code>.json 降级表")
    parser.add_argument("--check", action="store_true",
                        help="校验两份 .ts 一致、且降级表与 .ts 同步")
    args = parser.parse_args()

    if not any(vars(args).values()):
        args.extract = args.identity = args.qm = args.messages = args.check = True

    if args.extract:
        skeleton = extract()
        from_ts = read_ts(EN_TS)                 # ⚠️ 先读旧译文，再回填，保证不丢
        from_json = {(CONTEXT, key): value
                     for key, value in read_json_translations().items()}
        merged = dict(from_ts)
        merged.update(from_json)                 # en.json 优先（它是真源）
        print("  译文来源：.ts %d 条 / en.json %d 条 → 合计 %d 条" % (
            len(from_ts), len(from_json), len(merged)))
        missing = merge(skeleton, merged, EN_TS)   # 再回填

        # 反向检查：en.json 里有、但源码里已经不存在的条目（代码删了文案）
        sources = set()
        root = ET.parse(EN_TS).getroot()
        for context in root.findall("context"):
            name = (context.findtext("name") or "").strip()
            for message in context.findall("message"):
                sources.add((name, message.findtext("source") or ""))
        stale = sorted(set(from_json) - sources)
        if stale:
            print("  ⚠️ en.json 里有 %d 条已不存在于源码（可删除）：" % len(stale))
            for item in stale[:8]:
                print("      %r" % (item[1][:60],))
        if missing:
            print("  ℹ️ 还有 %d 条待翻译：补进 i18n/en.json 后重跑本脚本" % missing)
        _discard(skeleton)

    if args.identity:
        write_identity(EN_TS, ZH_TS)

    # 顺序有意为之：先生成 / 编译产物，**最后**再 check —— 否则全流程里
    # check 会拿着上一轮的旧产物判断，一有改动就先 exit(1)，根本走不到生成那步。
    if args.messages:
        if not write_all_messages():
            print("  ⚠️ 降级表为空；运行时若用不上 .qm 就只剩源码原文")

    if args.qm:
        tool = lrelease()
        if tool:
            print("lrelease: %s" % tool)
        if not compile_all(tool):
            print("  ⚠️ .qm 未全部产出；包内将缺 .qm，运行时退回读 messages_*.json")

    if args.check:
        problems = check()
        if problems:
            sys.exit(1)


def _indent(elem, level=0):
    """让 ET 输出的 XML 有缩进（Qt 不在乎，但人要看 diff）。"""
    pad = "\n" + "    " * level
    if len(elem):
        if not (elem.text or "").strip():
            elem.text = pad + "    "
        for child in elem:
            _indent(child, level + 1)
        if not (elem.tail or "").strip():
            elem.tail = pad
        if not (elem[-1].tail or "").strip():
            elem[-1].tail = pad
    else:
        if level and not (elem.tail or "").strip():
            elem.tail = pad


if __name__ == "__main__":
    main()
