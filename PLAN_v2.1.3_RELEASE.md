# QGIS Agent v2.1.3 发布方案

> 目标：修复已知问题 → 打 2.1.3 包 → 上传 plugins.qgis.org 并通过审核
> 编制日期：2026-08-28

---

## 零、结论速览

| 项 | 结论 |
|----|------|
| 项目里存了 plugins.qgis.org 的账号信息吗？ | **没有**，零凭证（这是对的，本就不该入库） |
| 能不能直接上传？ | **不能**，有 3 个硬阻塞项必须先改，否则必被打回 |
| 最大的包体问题 | `tool_docs/` 679 个死文件占 **81% 体积**（993 KB / 1229 KB） |
| 删掉死资源后 | 1229 KB → **236 KB**，743 文件 → **64 文件** |
| 预计审核周期 | 首次提交 1–7 天（志愿者人工审，周末顺延） |

---

## 一、plugins.qgis.org 的账号信息：项目里有吗？

**没有，也不需要存。** 我查了 `.git`、`.claude/settings.local.json`、`.gitignore` 和全部根目录文件，没有任何 OSGEO ID、API token 或上传脚本。

但有**间接证据表明你此前已经尝试过上传**：

```
8675e46 fix: add nosec B608 to all SQL f-strings in dataloader.py
507a103 fix: remove PASSWORD keyword trigger from tool docs
5d00f82 fix: v2.1.1 - resolve all 260 security/quality scan issues
9edc560 fix: resolve security scan issues for QGIS plugin repository
```

这几条 commit 精准对应官方的 **Automated Security Validation** 环节（secrets detection / code quality / suspicious file analysis）。也就是说你卡在过扫描这一关。

### 需要你提供的三样东西

| 项 | 获取方式 | 用途 |
|----|----------|------|
| **OSGEO ID** | https://www.osgeo.org/osgeo_userid/ 注册 | 登录 plugins.qgis.org 的唯一凭证，无它无法上传 |
| **plugins.qgis.org 账号** | 用 OSGEO ID 登录即可 | 上传入口 https://plugins.qgis.org/plugins/add/ |
| **API Token** | 登录后个人页面生成 | 给我用于脚本自动上传（不入库，只放本地 `.env`） |

> 拿到上面三项后，我可以写一个 `upload_plugin.py`，一条命令完成打包 + 上传，不用每次手动点网页表单。

---

## 二、上传合规对照表（硬阻塞项）

逐条对照官方 https://plugins.qgis.org/docs/publish 与 /docs/approval：

| # | 官方要求 | 项目现状 | 判定 |
|---|----------|----------|------|
| 1 | 需要 OSGEO ID | 无 | 🔴 **阻塞** — 需注册 |
| 2 | 外部依赖必须在 `about` 字段声明 | `about` 只写功能，未提 langchain / psutil | 🔴 **阻塞** — 官方原话 "this needs to be clearly stated in the About metadata field" |
| 3 | 上传的 ZIP 与 GitHub 仓库代码一致 | 若只在打包层排除死代码，仓库仍保留 → 不一致 | 🔴 **阻塞** — 必须在仓库层同步清理 |
| 4 | LICENSE 文件，GPLv2+ 兼容 | MIT（FSF 认定与 GPLv2 兼容） | 🟡 可通过，但建议确认（见决策项 A） |
| 5 | 不含二进制文件 | 含 `icon.ico`（17 KB） | 🟡 建议移除，`icon.png` 已足够 |
| 6 | 包 ≤ 25 MB | 1.23 MB | 🟢 通过 |
| 7 | 仓库不含 zip 文件 | `.gitignore` 已忽略 `*.zip`，`git ls-files` 查得 0 个 | 🟢 通过 |
| 8 | homepage / repository / tracker 链接有效 | 均为 `github.com/bunkmr/qgis-agent` | 🟡 需实测仓库公开可访问 |
| 9 | 自动安全扫描 | 无硬编码密钥 ✓；`exec()` 已加 `# nosec B102` ✓ | 🟢 基本通过（见下） |
| 10 | `qgisMinimumVersion` 合理 | `=3.0`，但 README 说推荐 3.28+ | 🟡 自相矛盾，建议改 `3.22` |

### 安全扫描的两处残留风险

1. **`qgis_tools.py:343`** `exec(code, namespace)` —— 已标注 `# nosec B102`，但官方扫描器不一定认 nosec。这是插件核心功能（执行 LLM 生成的 PyQGIS 代码，且有用户确认弹窗），需要在注释里写清楚用途，便于人工审核理解。
2. **`scripts/build_api_index.py:6`** 有一行 `exec(open(r'D:\Work\qgis_agent\scripts\build_api_index.py').read())` —— 疑似调试残留，硬编码 Windows 路径。`scripts/` 已被打包排除，但在 GitHub 仓库里可见，建议删掉。

---

## 三、2.1.3 修复方案

### L1 — 发布合规（必做，不做必被拒）

| 序 | 文件 | 改动 |
|----|------|------|
| L1-1 | `metadata.txt` | `version=2.1.3`；`qgisMinimumVersion` 3.0 → 3.22；补 `hasProcessingProvider=False` |
| L1-2 | `metadata.txt` | `about` 末尾追加依赖声明段（langchain-core / langchain-openai / langchain-deepseek / psutil + 安装指引） |
| L1-3 | `config.py` | `PLUGIN_VERSION` → `2.1.3` |
| L1-4 | `CHANGELOG.md` | 新增 `[2.1.3]` 条目 |
| L1-5 | `build_plugin.py` | 排除 `tool_docs`、`tool_docs_index.json`、`CLAUDE.md`、`*.ico`、`.workbuddy`、`.claude` |
| L1-6 | `build_plugin.py` | 删除 `build_plugin_zip_flat()`（与 `build_plugin_zip()` 代码完全相同，产出的 ZIP 一模一样） |
| L1-7 | `build_plugin.py` | 删除从未被使用的 `INCLUDE_PATTERNS`（`should_include()` 只看 EXCLUDE） |
| L1-8 | `README.md` | 删除对死代码功能的宣传（Skills / Task Graph / Workflow Recorder / Workflow Executor / Clarification / Code Review / "679 built-in tools"） |

### L2 — 死代码处置（需你决策，见决策项 B）

现状：约 **2850 行**未被任何代码引用。

| 模块 | 行数 | 说明 |
|------|------|------|
| `agent_loop/` | ~1200 | 整包零生产引用，且内部有 3 个 P0 bug |
| `skills/` | ~950 | 整包零引用 |
| `task_graph.py` | 236 | 根级孤儿 |
| `workflow_recorder.py` | 263 | 根级孤儿 |
| `workflow_executor.py` | 150 | 根级孤儿 |
| `thinking_widget.py` | 210 | 根级孤儿（v2 UI 用的是 `thinking_display.py`） |
| `tool_doc_manager.py` | 223 | 根级孤儿 |
| `code_reviewer.py` | 149 | 根级孤儿 |
| `clarification_manager.py` | 292 | 根级孤儿 |
| `tool_docs/` 679 TOML | — | **占包体 81%**，无任何消费者 |

### L3 — 代码 bug 修复

**P0（若决定接入 `agent_loop` 则必修）**

| 序 | 位置 | 问题 | 修法 |
|----|------|------|------|
| L3-1 | `agent_loop/loop.py:290` | 只调 `short_term.get_messages()`，全项目无一处调 `load()` → 对话历史恒空 | `_build_context()` 中先调 `self.memory.short_term.load()` |
| L3-2 | `agent_loop/processor.py:140` | `_on_worker_finished` 解包 3 值，但 `ToolAgentSignals.finished` 是 2 参信号 → ValueError | 改签名为 `(response, workflow)` |
| L3-3 | `agent_loop/loop.py:345` | `insert_interaction({dict})` 缺第二参，而 `dataloader.insert_interaction(list, conversation_id)` 收 list → TypeError | 改为传 list + conversation_id |

**P1（无论 agent_loop 去留都建议修）**

| 序 | 位置 | 问题 |
|----|------|------|
| L3-4 | `processor.py:248` | `except (AttributeError, TypeError, NotImplementedError, Exception)` — 前三个是永远走不到的死分支 |
| L3-5 | `processor.py:121` | `self.streaming_llm` 创建后从未使用 |
| L3-6 | `processor.py:322` | lambda 延迟 50 ms 捕获循环变量 `tool_name`（late binding，一轮多工具时日志打错名字） |
| L3-7 | 仓库根 | 755 文件全 modified，实为换行符整体变更，diff ±58000 行 → 加 `.gitattributes` 锁 CRLF/LF |
| L3-8 | `scripts/build_api_index.py:6` | 删除硬编码 Windows 路径的自执行调试行 |

---

## 四、上传流程

### 前置：验证 GitHub 仓库可公开访问

```bash
curl -sI https://github.com/bunkmr/qgis-agent | head -1   # 期望 200
```

审核者会访问 `metadata.txt` 里的 homepage / repository / tracker。若仓库是 private，直接拒。

### 方式 A：网页表单（首次推荐，最直观）

1. 注册 OSGEO ID：https://www.osgeo.org/osgeo_userid/
2. 用 OSGEO ID 登录 https://plugins.qgis.org/
3. 进入 https://plugins.qgis.org/plugins/add/
4. 上传 `qgis_agent_v2.1.3_20260828.zip`
5. 收到确认邮件 → 等待自动安全扫描结果邮件
6. 扫描通过 → 进入人工审核队列（1–7 天）

### 方式 B：脚本自动上传（我来实现，需 API Token）

官方提供三种接口：

```bash
# REST + Token
curl -X POST https://plugins.qgis.org/plugins/<package_name>/version/add/api/ \
     -H "Authorization: Bearer <token>" \
     -F "package=@qgis_agent_v2.1.3_20260828.zip" \
     -F "auto_approve_after_scan=true"

# XML-RPC
server = xmlrpc.client.ServerProxy("https://user:pass@plugins.qgis.org/plugins/RPC2/")
server.plugin.upload(xmlrpc.client.Binary(zip_bytes), True)
```

我会写 `upload_plugin.py`，从 `.env` 读 token（.env 已在 `.gitignore` 中），一条命令完成：版本校验 → 打包 → 上传 → 轮询扫描结果。

### 审核通过后

- 插件出现在 QGIS 内置「插件管理器」可搜索列表
- 后续更新走 https://plugins.qgis.org/plugins/<name>/version/add/ ，只需更新 `metadata.txt` 的 `version` + `changelog`

---

## 五、待你决策的三件事

### 决策项 A — 许可证保持 MIT 还是改 GPLv2+？

官方要求「与 GPLv2 或更新版本兼容」。MIT 经 FSF 认定与 GPLv2 兼容，法理上可通过；但审核者是人，见 MIT 有时会多问一句。

- **保持 MIT**：零改动，风险低（推荐）
- **改 GPLv2+**：需替换 `LICENSE` 全文 + `README.md` 徽章 + 头部版权声明，最保险

### 决策项 B — 死代码怎么处置？

- **方案 1（保守，推荐）**：只删 `tool_docs/` + `tool_docs_index.json`（81% 体积，纯死资源），其余死代码原样保留但打包排除。改动最小，审核 diff 干净。
- **方案 2（推荐给长期维护）**：删 `tool_docs/` + 7 个根级孤儿模块 + `skills/`，保留 `agent_loop/` 并在 README 明确标注「实验性，未接入」。仓库瘦身约 1650 行。
- **方案 3（激进）**：把 `agent_loop/` 接进主链路（修 L3-1/2/3 + 替换 `qgis_agent.py:242` 的 import），其余全删。收益最大但风险最高，需要 QGIS 实机回归测试。

### 决策项 C — 是否现在就写上传脚本？

需要你先提供 API Token 才能实测。可以先把脚本写好，token 留空占位。

---

## 六、执行顺序（建议）

```
第 1 步  你确认决策项 A / B              ← 卡在这里
第 2 步  我执行 L1 合规改动 + L3 代码修复
第 3 步  我执行 L2 死代码处置
第 4 步  本地打包 + zip 内容自检（文件清单、无 __pycache__、无 .ico/zip）
第 5 步  你提供 OSGEO ID + API Token
第 6 步  首次上传（走网页表单最稳）
第 7 步  等扫描邮件；若报 issue，我对症修后传 2.1.3 修订版
```

---

## 附：自检清单（打包后我会逐项验证）

- [ ] ZIP 根目录结构为 `qgis_agent/...`（单层插件名前缀）
- [ ] 无 `__pycache__` / `*.pyc` / `.git` / `.DS_Store` / `__MACOSX`
- [ ] 无 `*.zip`、无 `*.ico`
- [ ] 含 `metadata.txt`、`__init__.py`、`LICENSE`、`README.md`、`icon.png`
- [ ] 文件数 ≤ 100，体积 ≤ 500 KB
- [ ] `metadata.txt` 的 version 与 `config.py` 的 PLUGIN_VERSION 一致
- [ ] `metadata.txt` 的 about 含外部依赖声明
- [ ] GitHub 仓库已同步 push，且与 ZIP 内容一致
