@echo off
REM QGIS Agent 插件安装脚本
REM 将插件复制到 QGIS 插件目录

set QGIS_PLUGIN_DIR=%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins
set PLUGIN_NAME=qgis_agent

echo ========================================
echo QGIS Agent 插件安装脚本
echo ========================================
echo.

REM 检查 QGIS 插件目录是否存在
if not exist "%QGIS_PLUGIN_DIR%" (
    echo 错误: 未找到 QGIS 插件目录
    echo 路径: %QGIS_PLUGIN_DIR%
    pause
    exit /b 1
)

echo QGIS 插件目录: %QGIS_PLUGIN_DIR%
echo.

REM 检查是否已存在插件
if exist "%QGIS_PLUGIN_DIR%\%PLUGIN_NAME%" (
    echo 警告: 插件已存在，将被覆盖
    echo.
    set /p CONFIRM="是否继续? (Y/N): "
    if /i not "%CONFIRM%"=="Y" (
        echo 已取消安装
        pause
        exit /b 0
    )
    echo.
    echo 删除旧版本...
    rmdir /s /q "%QGIS_PLUGIN_DIR%\%PLUGIN_NAME%"
)

echo 复制插件文件...
echo.

REM 创建插件目录
mkdir "%QGIS_PLUGIN_DIR%\%PLUGIN_NAME%"

REM 复制核心文件
copy /y "qgis_agent.py" "%QGIS_PLUGIN_DIR%\%PLUGIN_NAME%\" >nul
copy /y "qgis_agent_dockwidget.py" "%QGIS_PLUGIN_DIR%\%PLUGIN_NAME%\" >nul
copy /y "qgis_agent_dockwidget_base_ui.py" "%QGIS_PLUGIN_DIR%\%PLUGIN_NAME%\" >nul
copy /y "conversation.py" "%QGIS_PLUGIN_DIR%\%PLUGIN_NAME%\" >nul
copy /y "processor.py" "%QGIS_PLUGIN_DIR%\%PLUGIN_NAME%\" >nul
copy /y "response_worker.py" "%QGIS_PLUGIN_DIR%\%PLUGIN_NAME%\" >nul
copy /y "qgis_tools.py" "%QGIS_PLUGIN_DIR%\%PLUGIN_NAME%\" >nul
copy /y "llm_providers.py" "%QGIS_PLUGIN_DIR%\%PLUGIN_NAME%\" >nul
copy /y "dataloader.py" "%QGIS_PLUGIN_DIR%\%PLUGIN_NAME%\" >nul
copy /y "utils.py" "%QGIS_PLUGIN_DIR%\%PLUGIN_NAME%\" >nul
copy /y "config.py" "%QGIS_PLUGIN_DIR%\%PLUGIN_NAME%\" >nul
copy /y "__init__.py" "%QGIS_PLUGIN_DIR%\%PLUGIN_NAME%\" >nul
copy /y "resources.py" "%QGIS_PLUGIN_DIR%\%PLUGIN_NAME%\" >nul
copy /y "package_manager.py" "%QGIS_PLUGIN_DIR%\%PLUGIN_NAME%\" >nul
copy /y "icon.png" "%QGIS_PLUGIN_DIR%\%PLUGIN_NAME%\" >nul

REM 复制对话框文件
copy /y "dialog_new_conversation.py" "%QGIS_PLUGIN_DIR%\%PLUGIN_NAME%\" >nul
copy /y "dialog_new_conversation_ui.py" "%QGIS_PLUGIN_DIR%\%PLUGIN_NAME%\" >nul
copy /y "settings_dialog.py" "%QGIS_PLUGIN_DIR%\%PLUGIN_NAME%\" >nul
copy /y "settings_dialog_ui.py" "%QGIS_PLUGIN_DIR%\%PLUGIN_NAME%\" >nul

REM 复制 RAG 模块
mkdir "%QGIS_PLUGIN_DIR%\%PLUGIN_NAME%\rag"
copy /y "rag\__init__.py" "%QGIS_PLUGIN_DIR%\%PLUGIN_NAME%\rag\" >nul
copy /y "rag\doc_store.py" "%QGIS_PLUGIN_DIR%\%PLUGIN_NAME%\rag\" >nul
copy /y "rag\retriever.py" "%QGIS_PLUGIN_DIR%\%PLUGIN_NAME%\rag\" >nul
copy /y "rag\cookbook.py" "%QGIS_PLUGIN_DIR%\%PLUGIN_NAME%\rag\" >nul
copy /y "rag\doc_generator.py" "%QGIS_PLUGIN_DIR%\%PLUGIN_NAME%\rag\" >nul

REM 复制新的 Agent Loop 模块
mkdir "%QGIS_PLUGIN_DIR%\%PLUGIN_NAME%\agent_loop"
copy /y "agent_loop\__init__.py" "%QGIS_PLUGIN_DIR%\%PLUGIN_NAME%\agent_loop\" >nul
copy /y "agent_loop\state.py" "%QGIS_PLUGIN_DIR%\%PLUGIN_NAME%\agent_loop\" >nul
copy /y "agent_loop\tools.py" "%QGIS_PLUGIN_DIR%\%PLUGIN_NAME%\agent_loop\" >nul
copy /y "agent_loop\memory.py" "%QGIS_PLUGIN_DIR%\%PLUGIN_NAME%\agent_loop\" >nul
copy /y "agent_loop\loop.py" "%QGIS_PLUGIN_DIR%\%PLUGIN_NAME%\agent_loop\" >nul
copy /y "agent_loop\rag.py" "%QGIS_PLUGIN_DIR%\%PLUGIN_NAME%\agent_loop\" >nul
copy /y "agent_loop\qgis_adapter.py" "%QGIS_PLUGIN_DIR%\%PLUGIN_NAME%\agent_loop\" >nul
copy /y "agent_loop\processor.py" "%QGIS_PLUGIN_DIR%\%PLUGIN_NAME%\agent_loop\" >nul
copy /y "agent_loop\ui_enhancements.py" "%QGIS_PLUGIN_DIR%\%PLUGIN_NAME%\agent_loop\" >nul

REM 复制资源文件
if exist "resources" (
    xcopy /s /e /y "resources" "%QGIS_PLUGIN_DIR%\%PLUGIN_NAME%\resources\" >nul
)

REM 复制帮助文件
if exist "help" (
    xcopy /s /e /y "help" "%QGIS_PLUGIN_DIR%\%PLUGIN_NAME%\help\" >nul
)

echo.
echo ========================================
echo 安装完成!
echo ========================================
echo.
echo 插件已安装到: %QGIS_PLUGIN_DIR%\%PLUGIN_NAME%
echo.
echo 下一步:
echo 1. 启动 QGIS
echo 2. 进入 插件 - 管理和安装插件
echo 3. 在"已安装"标签页中找到 "QGIS Agent"
echo 4. 勾选启用插件
echo.
pause
