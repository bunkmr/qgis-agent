# 可折叠思考显示功能

## 功能说明

实现了类似 Claude Code 的思考显示效果：

1. **思考过程中** - 实时显示思考内容，默认展开
2. **思考结束后** - 自动折叠，点击可展开查看完整思考过程

## 效果预览

### 思考中（展开状态）

```
🧠 思考中... · 23:15:00          点击折叠
┌─────────────────────────────────────┐
│ 正在分析用户请求...                  │
│ 需要调用 get_qgis_info 获取项目信息 │
│ 然后根据结果决定下一步操作...        │
└─────────────────────────────────────┘
```

### 思考完成（折叠状态）

```
💭 思考完成                        点击展开
```

点击后展开查看完整思考过程。

## 安装步骤

### 方法 1: 自动替换（推荐）

运行安装脚本：

```bash
cd D:\Work\qgis_agent
python install_v2.py
```

然后重启 QGIS。

### 方法 2: 手动替换

1. 复制以下文件到 QGIS 插件目录：
   - `thinking_display.py` → `C:\Users\zhaos\AppData\Roaming\QGIS\QGIS3\profiles\default\python\plugins\qgis_agent\`
   - `qgis_agent_dockwidget_v2.py` → 同上

2. 修改 `qgis_agent.py`，找到以下代码：

```python
from .qgis_agent_dockwidget import QGISAgentDockWidget
```

替换为：

```python
try:
    from .qgis_agent_dockwidget_v2 import QGISAgentDockWidgetV2 as QGISAgentDockWidget
except ImportError:
    from .qgis_agent_dockwidget import QGISAgentDockWidget
```

3. 重启 QGIS

## 技术实现

### HTML <details> 标签

使用 HTML5 的 `<details>` 标签实现折叠效果：

```html
<details open>  <!-- open 表示默认展开 -->
    <summary>🧠 思考中...</summary>
    <div>思考内容...</div>
</details>

<details>  <!-- 无 open 属性表示默认折叠 -->
    <summary>💭 思考完成</summary>
    <div>思考内容...</div>
</details>
```

### 流式更新

使用标记注释 `<!-- THINKING_BLOCK -->` 定位思考块，实现增量更新：

1. 首次显示时插入完整的思考块
2. 更新时替换标记之间的内容
3. 完成时将 `<details open>` 改为 `<details>` 实现折叠

### 样式设计

- 深色背景 (`#1a1a2e`) 减少视觉干扰
- 渐变标题栏增加层次感
- 左侧边框颜色区分状态（蓝色=思考中，灰色=完成）
- 等宽字体显示代码和日志
- 最大高度 400px，超出可滚动

## 与 Claude Code 的对比

| 特性 | Claude Code | 本实现 |
|------|-------------|--------|
| 实时显示 | ✅ | ✅ |
| 折叠/展开 | ✅ | ✅ |
| 默认状态 | 折叠 | 展开（思考中）→ 折叠（完成） |
| 样式 | 终端风格 | 深色渐变风格 |

## 注意事项

1. **QTextBrowser 限制**：QGIS 的 QTextBrowser 基于 Qt WebKit，对 HTML5 支持有限，但 `<details>` 标签可以正常工作。

2. **JavaScript 不支持**：QTextBrowser 不支持 JavaScript，因此折叠/展开是浏览器原生行为。

3. **性能考虑**：大量思考内容时，建议在完成后自动折叠以节省空间。

## 故障排除

### 问题 1: 折叠不生效

**原因**：QTextBrowser 版本过旧

**解决**：更新 QGIS 到 3.x 版本

### 问题 2: 样式显示异常

**原因**：CSS 不被完全支持

**解决**：这是正常现象，核心折叠功能仍可使用

### 问题 3: 流式更新卡顿

**原因**：HTML 重建开销

**解决**：减少更新频率，或在思考完成后再显示完整内容
