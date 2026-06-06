常见问题
============

Q: 插件提示缺少依赖怎么办？
----------------------------

A: 首次启动时会提示自动安装，点击「是」即可。如果自动安装失败，
请在 OSGeo4W Shell 中手动运行：

.. code-block:: bash

    pip install langchain langchain-core langchain-openai langchain-deepseek requests

Q: 为什么发送消息后没有反应？
-------------------------------

A: 请检查：

1. 是否已在「模型配置」标签页添加了 LLM
2. API Key 是否正确
3. 网络是否可以访问 API 端点
4. 查看 QGIS 的「日志消息」面板是否有错误信息

Q: Agent 生成的代码是否正确？
-------------------------------

A: Agent 使用 LLM 生成代码，可能不完全正确。建议：

1. 在执行代码前，仔细检查确认对话框中的代码预览
2. 先用简单任务测试
3. 如果代码执行失败，Agent 会自动分析错误并尝试修复

Q: 如何让 Agent 记住我的偏好？
--------------------------------

A: Agent 支持长期记忆功能：

- 直接告诉 Agent "请记住..."，它会调用 save_memory 工具
- 也可以说 "帮我记住常用路径是..."
- 下次对话时，Agent 会自动加载之前的记忆

Q: 支持哪些数据格式？
-----------------------

A: 通过 QGIS 的 OGR/GDAL 驱动，支持几乎所有常见 GIS 格式：

- 矢量: Shapefile, GeoJSON, GPKG, KML, GML, CSV 等
- 栅格: GeoTIFF, IMG, ECW, JPEG2000 等

Q: 为什么标注设置失败？
-------------------------

A: QGIS 各版本标注 API 差异较大。QGIS Agent 的 set_layer_labeling
工具已处理了多版本兼容问题。如果仍有问题，请在 GitHub Issues
中反馈你的 QGIS 版本。

Q: 可以离线使用吗？
---------------------

A: 不可以。Agent 需要通过网络调用 LLM API 才能工作。
如果需要离线使用，可以考虑部署本地 LLM（如 Ollama），
然后通过「自定义」模型配置连接到本地服务。
