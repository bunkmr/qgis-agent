# QGIS Agent 插件安装脚本 (PowerShell)
# 将插件复制到 QGIS 插件目录

$ErrorActionPreference = "Stop"

# QGIS 插件目录
$QGISPluginDir = Join-Path $env:APPDATA "QGIS\QGIS3\profiles\default\python\plugins"
$PluginName = "qgis_agent"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "QGIS Agent 插件安装脚本" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# 检查 QGIS 插件目录
if (-not (Test-Path $QGISPluginDir)) {
    Write-Host "错误: 未找到 QGIS 插件目录" -ForegroundColor Red
    Write-Host "路径: $QGISPluginDir" -ForegroundColor Yellow
    Read-Host "按 Enter 退出"
    exit 1
}

Write-Host "QGIS 插件目录: $QGISPluginDir" -ForegroundColor Green
Write-Host ""

# 目标插件目录
$TargetDir = Join-Path $QGISPluginDir $PluginName

# 检查是否已存在
if (Test-Path $TargetDir) {
    Write-Host "警告: 插件已存在，将被覆盖" -ForegroundColor Yellow
    $confirm = Read-Host "是否继续? (Y/N)"
    if ($confirm -ne "Y") {
        Write-Host "已取消安装" -ForegroundColor Yellow
        exit 0
    }
    Write-Host ""
    Write-Host "删除旧版本..." -ForegroundColor Gray
    Remove-Item -Recurse -Force $TargetDir
}

Write-Host "复制插件文件..." -ForegroundColor Cyan
Write-Host ""

# 创建目标目录
New-Item -ItemType Directory -Path $TargetDir -Force | Out-Null

# 要复制的文件列表
$Files = @(
    "qgis_agent.py",
    "qgis_agent_dockwidget.py",
    "qgis_agent_dockwidget_base_ui.py",
    "conversation.py",
    "processor.py",
    "response_worker.py",
    "qgis_tools.py",
    "llm_providers.py",
    "dataloader.py",
    "utils.py",
    "config.py",
    "__init__.py",
    "resources.py",
    "package_manager.py",
    "icon.png",
    "dialog_new_conversation.py",
    "dialog_new_conversation_ui.py",
    "settings_dialog.py",
    "settings_dialog_ui.py"
)

# 复制核心文件
foreach ($file in $Files) {
    if (Test-Path $file) {
        Copy-Item -Path $file -Destination $TargetDir -Force
        Write-Host "  ✓ $file" -ForegroundColor Gray
    }
}

# 复制 RAG 模块
$RagDir = Join-Path $TargetDir "rag"
New-Item -ItemType Directory -Path $RagDir -Force | Out-Null
$RagFiles = @("__init__.py", "doc_store.py", "retriever.py", "cookbook.py", "doc_generator.py")
foreach ($file in $RagFiles) {
    $src = Join-Path "rag" $file
    if (Test-Path $src) {
        Copy-Item -Path $src -Destination $RagDir -Force
        Write-Host "  ✓ rag/$file" -ForegroundColor Gray
    }
}

# 复制 Agent Loop 模块
$AgentLoopDir = Join-Path $TargetDir "agent_loop"
New-Item -ItemType Directory -Path $AgentLoopDir -Force | Out-Null
$AgentLoopFiles = @(
    "__init__.py", "state.py", "tools.py", "memory.py",
    "loop.py", "rag.py", "qgis_adapter.py", "processor.py", "ui_enhancements.py"
)
foreach ($file in $AgentLoopFiles) {
    $src = Join-Path "agent_loop" $file
    if (Test-Path $src) {
        Copy-Item -Path $src -Destination $AgentLoopDir -Force
        Write-Host "  ✓ agent_loop/$file" -ForegroundColor Gray
    }
}

# 复制资源目录
if (Test-Path "resources") {
    $ResourcesDir = Join-Path $TargetDir "resources"
    Copy-Item -Path "resources" -Destination $ResourcesDir -Recurse -Force
    Write-Host "  ✓ resources/" -ForegroundColor Gray
}

# 复制帮助目录
if (Test-Path "help") {
    $HelpDir = Join-Path $TargetDir "help"
    Copy-Item -Path "help" -Destination $HelpDir -Recurse -Force
    Write-Host "  ✓ help/" -ForegroundColor Gray
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "安装完成!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "插件已安装到: $TargetDir" -ForegroundColor Cyan
Write-Host ""
Write-Host "下一步:" -ForegroundColor Yellow
Write-Host "1. 启动 QGIS" -ForegroundColor White
Write-Host "2. 进入 插件 → 管理和安装插件" -ForegroundColor White
Write-Host "3. 在'已安装'标签页中找到 'QGIS Agent'" -ForegroundColor White
Write-Host "4. 勾选启用插件" -ForegroundColor White
Write-Host ""
Read-Host "按 Enter 退出"
