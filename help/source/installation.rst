安装指南
============

系统要求
--------

- QGIS 3.0+ / 4.x（PyQt5/PyQt6 双兼容）
- Python 3.x（QGIS 内置）
- 网络连接（访问 LLM API）

安装步骤
--------

方式一：从 ZIP 安装（推荐）
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

1. 从 `GitHub Releases <https://github.com/bunkmr/qgis-agent/releases>`_ 下载最新版 `qgis_agent_v2.1.3_20260902.zip`
2. 打开 QGIS → 插件 → 管理并安装插件
3. 点击「从 ZIP 安装」
4. 选择下载的 zip 文件
5. 重启 QGIS

方式二：手动安装
~~~~~~~~~~~~~~~~

1. 下载项目源码到 QGIS 插件目录::

    git clone https://github.com/bunkmr/qgis-agent.git
    # QGIS 3 用 QGIS3，QGIS 4 用 QGIS4
    %APPDATA%/QGIS/QGIS3/profiles/default/python/plugins/qgis_agent/

2. 安装 Python 依赖::

    pip install langchain_core langchain_openai langchain_deepseek httpx psutil

3. 在 QGIS 插件管理器中启用「QGIS Agent」

依赖说明
--------

首次启动时，如果缺少以下 Python 库，插件会提示自动安装：

- ``langchain_core``: LangChain 核心库
- ``langchain_openai``: OpenAI 兼容接口
- ``langchain_deepseek``: DeepSeek 接口
- ``httpx``: 异步 HTTP 客户端
- ``psutil``: 系统资源监控
