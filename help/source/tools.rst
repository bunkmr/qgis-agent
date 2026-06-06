内置工具参考
================

QGIS Agent 内置了 14 个 QGIS 工具，覆盖图层管理、空间分析、
地图渲染、记忆管理等场景。

图层管理
--------

get_qgis_info
~~~~~~~~~~~~~
获取 QGIS 当前状态信息：版本、项目路径、坐标系、所有图层列表。

get_layer_features
~~~~~~~~~~~~~~~~~~
获取指定矢量图层的属性表和几何数据（WKT 格式）。

add_vector_layer
~~~~~~~~~~~~~~~~
添加矢量图层（Shapefile、GeoJSON、GPKG 等）到当前项目。

add_raster_layer
~~~~~~~~~~~~~~~~
添加栅格图层（GeoTIFF、IMG 等）到当前项目。

remove_layer
~~~~~~~~~~~~
从项目中移除指定图层。

zoom_to_layer
~~~~~~~~~~~~~
将地图视图缩放到指定图层的范围。

空间分析与处理
--------------

execute_processing
~~~~~~~~~~~~~~~~~~
执行 QGIS Processing Toolbox 中的处理算法。

常用算法示例：

- ``native:buffer`` - 缓冲区分析
- ``native:clip`` - 裁剪
- ``native:intersection`` - 相交分析
- ``qgis:exporttospreadsheet`` - 导出表格
- ``gdal:contour`` - 生成等高线
- ``native:fieldcalculator`` - 字段计算器

execute_pyqgis
~~~~~~~~~~~~~~
在 QGIS Python 环境中直接执行 PyQGIS 代码。

已预导入的类型：
  QgsPoint, QgsPointXY, QgsGeometry, QgsFeature, QgsField, QgsFields,
  QgsWkbTypes, QgsCoordinateTransform, QgsFeatureRequest, QgsDistanceArea,
  QgsUnitTypes, QgsVectorLayer, QgsRasterLayer, QgsCoordinateReferenceSystem,
  QgsProject, Qgis, iface, QColor, QgsFillSymbol, QgsLineSymbol,
  QgsMarkerSymbol, QgsSingleSymbolRenderer 等

.. warning::
   执行前会弹出确认对话框，请仔细检查代码后再确认。

地图渲染与标注
--------------

render_map
~~~~~~~~~~
将当前地图画布渲染为 PNG 图片文件。

set_layer_labeling
~~~~~~~~~~~~~~~~~~
设置矢量图层的标注（Labeling）。

支持参数：
  - 标注字段 (field_name)
  - 字体大小 (font_size)
  - 文字颜色 (color)
  - 文字缓冲/描边 (buffer_enabled, buffer_color, buffer_size)
  - 放置方式 (placement): around_point, over_point, line, horizontal

项目操作
--------

save_project
~~~~~~~~~~~~
保存当前 QGIS 项目文件。

load_project
~~~~~~~~~~~~
加载 QGIS 项目文件。

长期记忆
--------

save_memory
~~~~~~~~~~~
将重要信息保存到长期记忆（MEMORY.md），跨对话持久化。

load_memory
~~~~~~~~~~~
读取之前保存的所有长期记忆内容。
