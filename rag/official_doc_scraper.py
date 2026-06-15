# -*- coding: utf-8 -*-
"""
QGIS 官方 API 文档爬取器

从 QGIS 官方 API 文档网站爬取完整的 PyQGIS 文档。
支持:
- 爬取类文档
- 解析方法签名和描述
- 增量更新
"""

import os
import re
import json
import time
import urllib.request
import urllib.parse
from dataclasses import dataclass
from typing import Optional
from html.parser import HTMLParser


# QGIS 官方 API 文档基础 URL
QGIS_API_BASE_URL = "https://qgis.org/pyqgis"
QGIS_DOXYGEN_BASE_URL = "https://qgis.org/api"


@dataclass
class APIDocEntry:
    """API 文档条目"""
    class_name: str
    method_name: str
    full_signature: str
    description: str
    parameters: list[dict]
    return_type: str
    source: str = "official_docs"
    url: str = ""


class QGISDocParser(HTMLParser):
    """QGIS 文档 HTML 解析器"""

    def __init__(self):
        super().__init__()
        self._current_tag = None
        self._current_attrs = {}
        self._in_member = False
        self._in_description = False
        self._in_params = False

        self.members = []
        self.current_member = {}

        self._text_buffer = []
        self._depth = 0

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        self._depth += 1

        # 检测成员定义
        if tag == "td" and "memItemLeft" in attrs_dict.get("class", ""):
            self._in_member = True
            self.current_member = {"name": "", "signature": ""}

        # 检测成员名
        if tag == "a" and self._in_member:
            self.current_member["name"] = attrs_dict.get("name", "")

    def handle_endtag(self, tag):
        self._depth -= 1

        if tag == "tr" and self._in_member:
            self._in_member = False
            if self.current_member.get("name"):
                self.members.append(self.current_member)
            self.current_member = {}

    def handle_data(self, data):
        if self._in_member:
            self.current_member["signature"] = self.current_member.get("signature", "") + data


class SimpleDocParser:
    """简单的文档解析器（基于文本匹配）"""

    @staticmethod
    def parse_class_page(html_content: str, class_name: str) -> list[APIDocEntry]:
        """解析类页面 HTML，提取方法文档"""
        entries = []

        # 查找成员函数
        # 匹配模式: <td class="memItemRight" ... ><a class="el" href="...">methodName</a>(...)

        # 使用正则提取方法签名
        method_pattern = re.compile(
            r'<a\s+class="el"\s+href="[^"]*"[^>]*>(\w+)</a>\s*\(([^)]*)\)',
            re.MULTILINE
        )

        for match in method_pattern.finditer(html_content):
            method_name = match.group(1)
            params_str = match.group(2).strip()

            # 解析参数
            params = SimpleDocParser._parse_params(params_str)

            # 构建完整签名
            full_signature = f"{class_name}.{method_name}({params_str})"

            entries.append(APIDocEntry(
                class_name=class_name,
                method_name=method_name,
                full_signature=full_signature,
                description="",  # 需要进一步提取
                parameters=params,
                return_type="",
                source="official_docs"
            ))

        return entries

    @staticmethod
    def _parse_params(params_str: str) -> list[dict]:
        """解析参数字符串"""
        if not params_str or params_str.strip() == "void":
            return []

        params = []
        for param in params_str.split(","):
            param = param.strip()
            if not param:
                continue

            # 尝试分离类型和名称
            parts = param.rsplit(None, 1)
            if len(parts) == 2:
                params.append({
                    "name": parts[1].strip("*&"),
                    "type": parts[0].strip(),
                    "default": ""
                })
            else:
                params.append({
                    "name": param,
                    "type": "",
                    "default": ""
                })

        return params


class OfficialDocScraper:
    """
    QGIS 官方 API 文档爬取器

    从 QGIS 官方网站爬取完整的 API 文档。
    """

    def __init__(self, cache_dir: str = None):
        """
        Args:
            cache_dir: 缓存目录（避免重复请求）
        """
        self._cache_dir = cache_dir or os.path.join(
            os.path.dirname(__file__), ".doc_cache"
        )
        os.makedirs(self._cache_dir, exist_ok=True)

        self._parser = SimpleDocParser()
        self._request_delay = 0.5  # 请求间隔（秒）

    def fetch_class_list(self) -> list[str]:
        """
        获取 QGIS 核心类列表

        Returns:
            类名列表
        """
        # QGIS 核心类列表（可以从文档首页提取）
        core_classes = [
            # 图层
            "QgsVectorLayer",
            "QgsRasterLayer",
            "QgsMapLayer",
            "QgsMeshLayer",
            "QgsPointCloudLayer",

            # 几何
            "QgsGeometry",
            "QgsPoint",
            "QgsPointXY",
            "QgsPointZ",
            "QgsPointXY",
            "QgsRectangle",
            "QgsPolygon",
            "QgsPolyline",
            "QgsMultiPolygon",
            "QgsMultiPolyline",

            # 要素
            "QgsFeature",
            "QgsFields",
            "QgsField",
            "QgsFeatureIterator",

            # 项目
            "QgsProject",
            "QgsApplication",

            # 坐标系
            "QgsCoordinateReferenceSystem",
            "QgsCoordinateTransform",
            "QgsCoordinateTransformContext",

            # 渲染
            "QgsMapSettings",
            "QgsMapRendererJob",
            "QgsMapRendererParallelJob",
            "QgsMapRendererSequentialJob",

            # 符号
            "QgsSymbol",
            "QgsFillSymbol",
            "QgsLineSymbol",
            "QgsMarkerSymbol",
            "QgsSymbolLayer",

            # 渲染器
            "QgsRenderer",
            "QgsSingleSymbolRenderer",
            "QgsCategorizedSymbolRenderer",
            "QgsGraduatedSymbolRenderer",
            "QgsRuleBasedRenderer",

            # 标注
            "QgsPalLayerSettings",
            "QgsVectorLayerSimpleLabeling",
            "QgsTextFormat",
            "QgsTextBufferSettings",
            "QgsLabeling",
            "QgsLabelUtils",

            # 表达式
            "QgsExpression",
            "QgsExpressionContext",
            "QgsExpressionContextUtils",

            # 空间索引
            "QgsSpatialIndex",
            "QgsSpatialIndexKDBush",
            "QgsFeatureRequest",

            # 距离/单位
            "QgsDistanceArea",
            "QgsUnitTypes",
            "QgsWkbTypes",

            # 枚举
            "Qgis",

            # GUI
            "QgsMapCanvas",
            "QgsMapTool",
            "QgsMapToolPan",
            "QgsMapToolZoom",

            # 分析
            "QgsVectorFileWriter",
            "QgsCoordinateFormatter",

            # Processing
            "QgsProcessingAlgorithm",
            "QgsProcessingFeedback",
            "QgsProcessingParameters",

            # 数据提供者
            "QgsVectorDataProvider",
            "QgsRasterDataProvider",

            # 其他
            "QgsRasterFileWriter",
            "QgsRasterPipe",
            "QgsFeatureSink",
            "QgsFeatureSource",
        ]

        return list(set(core_classes))  # 去重

    def fetch_class_doc(self, class_name: str, use_cache: bool = True) -> list[APIDocEntry]:
        """
        获取单个类的 API 文档

        Args:
            class_name: 类名
            use_cache: 是否使用缓存

        Returns:
            API 文档条目列表
        """
        # 检查缓存
        cache_file = os.path.join(self._cache_dir, f"{class_name}.json")
        if use_cache and os.path.exists(cache_file):
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return [APIDocEntry(**entry) for entry in data]
            except Exception:
                pass

        # 构建 URL
        url = f"{QGIS_API_BASE_URL}/class{class_name}.html"

        try:
            # 下载页面
            html_content = self._fetch_url(url)
            if not html_content:
                return []

            # 解析文档
            entries = self._parser.parse_class_page(html_content, class_name)

            # 缓存结果
            if entries:
                with open(cache_file, "w", encoding="utf-8") as f:
                    json.dump([vars(e) for e in entries], f, ensure_ascii=False, indent=2)

            return entries

        except Exception as e:
            print(f"Failed to fetch docs for {class_name}: {e}")
            return []

    def fetch_all_docs(self, progress_callback=None) -> list[APIDocEntry]:
        """
        获取所有核心类的 API 文档

        Args:
            progress_callback: 进度回调 callback(current, total)

        Returns:
            所有 API 文档条目
        """
        classes = self.fetch_class_list()
        all_entries = []

        for i, class_name in enumerate(classes):
            if progress_callback:
                progress_callback(i + 1, len(classes))

            entries = self.fetch_class_doc(class_name)
            all_entries.extend(entries)

            # 请求间隔
            time.sleep(self._request_delay)

        return all_entries

    def _fetch_url(self, url: str) -> Optional[str]:
        """下载 URL 内容"""
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (compatible; QGISAgent/1.0)"
            }
            req = urllib.request.Request(url, headers=headers)

            with urllib.request.urlopen(req, timeout=30) as response:  # nosec B310 - only http/https URLs from QGIS docs
                return response.read().decode("utf-8")
        except Exception as e:
            print(f"Failed to fetch {url}: {e}")
            return None

    def clear_cache(self):
        """清除缓存"""
        import shutil
        if os.path.exists(self._cache_dir):
            shutil.rmtree(self._cache_dir)
            os.makedirs(self._cache_dir, exist_ok=True)


class BuiltinOfficialDocs:
    """
    内置的官方 API 文档

    预定义的核心 API 文档，不需要网络访问。
    可以作为 fallback 或快速初始化使用。
    """

    # 核心类的完整 API 文档
    OFFICIAL_DOCS = [
        # QgsVectorLayer
        {
            "class_name": "QgsVectorLayer",
            "method_name": "QgsVectorLayer",
            "full_signature": "QgsVectorLayer(path: str, baseName: str = '', providerKey: str = 'ogr', options: QgsVectorLayer.LayerOptions = QgsVectorLayer.LayerOptions())",
            "description": "Constructs a vector layer with the specified provider key, path and base file name.",
            "parameters": [
                {"name": "path", "type": "str", "default": ""},
                {"name": "baseName", "type": "str", "default": "''"},
                {"name": "providerKey", "type": "str", "default": "'ogr'"},
            ],
            "return_type": "",
            "source": "official_docs",
        },
        {
            "class_name": "QgsVectorLayer",
            "method_name": "featureCount",
            "full_signature": "QgsVectorLayer.featureCount() -> int",
            "description": "Returns the number of features in the layer.",
            "parameters": [],
            "return_type": "int",
            "source": "official_docs",
        },
        {
            "class_name": "QgsVectorLayer",
            "method_name": "fields",
            "full_signature": "QgsVectorLayer.fields() -> QgsFields",
            "description": "Returns the field list for this layer.",
            "parameters": [],
            "return_type": "QgsFields",
            "source": "official_docs",
        },
        {
            "class_name": "QgsVectorLayer",
            "method_name": "getFeatures",
            "full_signature": "QgsVectorLayer.getFeatures(request: QgsFeatureRequest = QgsFeatureRequest()) -> QgsFeatureIterator",
            "description": "Returns an iterator for the features in this layer.",
            "parameters": [
                {"name": "request", "type": "QgsFeatureRequest", "default": "QgsFeatureRequest()"}
            ],
            "return_type": "QgsFeatureIterator",
            "source": "official_docs",
        },
        {
            "class_name": "QgsVectorLayer",
            "method_name": "selectByExpression",
            "full_signature": "QgsVectorLayer.selectByExpression(expression: str, behavior: QgsFeatureRequest.SelectionBehavior = QgsFeatureRequest.SetSelection)",
            "description": "Selects features in this layer using an expression.",
            "parameters": [
                {"name": "expression", "type": "str", "default": ""},
                {"name": "behavior", "type": "QgsFeatureRequest.SelectionBehavior", "default": "QgsFeatureRequest.SetSelection"}
            ],
            "return_type": "",
            "source": "official_docs",
        },
        {
            "class_name": "QgsVectorLayer",
            "method_name": "startEditing",
            "full_signature": "QgsVectorLayer.startEditing() -> bool",
            "description": "Makes the layer editable.",
            "parameters": [],
            "return_type": "bool",
            "source": "official_docs",
        },
        {
            "class_name": "QgsVectorLayer",
            "method_name": "commitChanges",
            "full_signature": "QgsVectorLayer.commitChanges() -> bool",
            "description": "Attempts to commit any changes to the underlying data provider.",
            "parameters": [],
            "return_type": "bool",
            "source": "official_docs",
        },
        {
            "class_name": "QgsVectorLayer",
            "method_name": "addFeature",
            "full_signature": "QgsVectorLayer.addFeature(feature: QgsFeature, skipDefaultValues: bool = False) -> bool",
            "description": "Adds a feature to this layer.",
            "parameters": [
                {"name": "feature", "type": "QgsFeature", "default": ""},
                {"name": "skipDefaultValues", "type": "bool", "default": "False"}
            ],
            "return_type": "bool",
            "source": "official_docs",
        },
        {
            "class_name": "QgsVectorLayer",
            "method_name": "deleteFeature",
            "full_signature": "QgsVectorLayer.deleteFeature(fid: int) -> bool",
            "description": "Deletes a feature from this layer.",
            "parameters": [
                {"name": "fid", "type": "int", "default": ""}
            ],
            "return_type": "bool",
            "source": "official_docs",
        },
        {
            "class_name": "QgsVectorLayer",
            "method_name": "updateFeature",
            "full_signature": "QgsVectorLayer.updateFeature(feature: QgsFeature) -> bool",
            "description": "Updates a feature in this layer.",
            "parameters": [
                {"name": "feature", "type": "QgsFeature", "default": ""}
            ],
            "return_type": "bool",
            "source": "official_docs",
        },
        {
            "class_name": "QgsVectorLayer",
            "method_name": "setRenderer",
            "full_signature": "QgsVectorLayer.setRenderer(renderer: QgsFeatureRenderer)",
            "description": "Sets the renderer for this layer.",
            "parameters": [
                {"name": "renderer", "type": "QgsFeatureRenderer", "default": ""}
            ],
            "return_type": "",
            "source": "official_docs",
        },
        {
            "class_name": "QgsVectorLayer",
            "method_name": "setLabeling",
            "full_signature": "QgsVectorLayer.setLabeling(labeling: QgsAbstractVectorLayerLabeling)",
            "description": "Sets the labeling for this layer.",
            "parameters": [
                {"name": "labeling", "type": "QgsAbstractVectorLayerLabeling", "default": ""}
            ],
            "return_type": "",
            "source": "official_docs",
        },
        {
            "class_name": "QgsVectorLayer",
            "method_name": "setCrs",
            "full_signature": "QgsVectorLayer.setCrs(crs: QgsCoordinateReferenceSystem, emitSignal: bool = True) -> bool",
            "description": "Sets the layer's coordinate reference system.",
            "parameters": [
                {"name": "crs", "type": "QgsCoordinateReferenceSystem", "default": ""},
                {"name": "emitSignal", "type": "bool", "default": "True"}
            ],
            "return_type": "bool",
            "source": "official_docs",
        },
        {
            "class_name": "QgsVectorLayer",
            "method_name": "crs",
            "full_signature": "QgsVectorLayer.crs() -> QgsCoordinateReferenceSystem",
            "description": "Returns the layer's coordinate reference system.",
            "parameters": [],
            "return_type": "QgsCoordinateReferenceSystem",
            "source": "official_docs",
        },
        {
            "class_name": "QgsVectorLayer",
            "method_name": "name",
            "full_signature": "QgsVectorLayer.name() -> str",
            "description": "Returns the name of the layer.",
            "parameters": [],
            "return_type": "str",
            "source": "official_docs",
        },
        {
            "class_name": "QgsVectorLayer",
            "method_name": "extent",
            "full_signature": "QgsVectorLayer.extent() -> QgsRectangle",
            "description": "Returns the extent of the layer.",
            "parameters": [],
            "return_type": "QgsRectangle",
            "source": "official_docs",
        },
        {
            "class_name": "QgsVectorLayer",
            "method_name": "dataProvider",
            "full_signature": "QgsVectorLayer.dataProvider() -> QgsVectorDataProvider",
            "description": "Returns the data provider for this layer.",
            "parameters": [],
            "return_type": "QgsVectorDataProvider",
            "source": "official_docs",
        },

        # QgsGeometry
        {
            "class_name": "QgsGeometry",
            "method_name": "buffer",
            "full_signature": "QgsGeometry.buffer(distance: float, segments: int) -> QgsGeometry",
            "description": "Returns a buffer region around the geometry, with additional style options.",
            "parameters": [
                {"name": "distance", "type": "float", "default": ""},
                {"name": "segments", "type": "int", "default": ""}
            ],
            "return_type": "QgsGeometry",
            "source": "official_docs",
        },
        {
            "class_name": "QgsGeometry",
            "method_name": "simplify",
            "full_signature": "QgsGeometry.simplify(tolerance: float) -> QgsGeometry",
            "description": "Returns a simplified version of the geometry.",
            "parameters": [
                {"name": "tolerance", "type": "float", "default": ""}
            ],
            "return_type": "QgsGeometry",
            "source": "official_docs",
        },
        {
            "class_name": "QgsGeometry",
            "method_name": "intersection",
            "full_signature": "QgsGeometry.intersection(geom: QgsGeometry) -> QgsGeometry",
            "description": "Returns the intersection of this geometry and geom.",
            "parameters": [
                {"name": "geom", "type": "QgsGeometry", "default": ""}
            ],
            "return_type": "QgsGeometry",
            "source": "official_docs",
        },
        {
            "class_name": "QgsGeometry",
            "method_name": "combine",
            "full_signature": "QgsGeometry.combine(geom: QgsGeometry) -> QgsGeometry",
            "description": "Returns the combination of this geometry and geom.",
            "parameters": [
                {"name": "geom", "type": "QgsGeometry", "default": ""}
            ],
            "return_type": "QgsGeometry",
            "source": "official_docs",
        },
        {
            "class_name": "QgsGeometry",
            "method_name": "difference",
            "full_signature": "QgsGeometry.difference(geom: QgsGeometry) -> QgsGeometry",
            "description": "Returns the difference of this geometry and geom.",
            "parameters": [
                {"name": "geom", "type": "QgsGeometry", "default": ""}
            ],
            "return_type": "QgsGeometry",
            "source": "official_docs",
        },
        {
            "class_name": "QgsGeometry",
            "method_name": "union",
            "full_signature": "QgsGeometry.union(geom: QgsGeometry) -> QgsGeometry",
            "description": "Returns the union of this geometry and geom.",
            "parameters": [
                {"name": "geom", "type": "QgsGeometry", "default": ""}
            ],
            "return_type": "QgsGeometry",
            "source": "official_docs",
        },
        {
            "class_name": "QgsGeometry",
            "method_name": "asPoint",
            "full_signature": "QgsGeometry.asPoint() -> QgsPointXY",
            "description": "Returns the geometry as a point.",
            "parameters": [],
            "return_type": "QgsPointXY",
            "source": "official_docs",
        },
        {
            "class_name": "QgsGeometry",
            "method_name": "asPolyline",
            "full_signature": "QgsGeometry.asPolyline() -> List[QgsPointXY]",
            "description": "Returns the geometry as a polyline.",
            "parameters": [],
            "return_type": "List[QgsPointXY]",
            "source": "official_docs",
        },
        {
            "class_name": "QgsGeometry",
            "method_name": "asPolygon",
            "full_signature": "QgsGeometry.asPolygon() -> List[List[QgsPointXY]]",
            "description": "Returns the geometry as a polygon.",
            "parameters": [],
            "return_type": "List[List[QgsPointXY]]",
            "source": "official_docs",
        },
        {
            "class_name": "QgsGeometry",
            "method_name": "asWkt",
            "full_signature": "QgsGeometry.asWkt(precision: int = -1) -> str",
            "description": "Returns the geometry as WKT.",
            "parameters": [
                {"name": "precision", "type": "int", "default": "-1"}
            ],
            "return_type": "str",
            "source": "official_docs",
        },
        {
            "class_name": "QgsGeometry",
            "method_name": "asJson",
            "full_signature": "QgsGeometry.asJson(precision: int = -1) -> str",
            "description": "Returns the geometry as GeoJSON.",
            "parameters": [
                {"name": "precision", "type": "int", "default": "-1"}
            ],
            "return_type": "str",
            "source": "official_docs",
        },
        {
            "class_name": "QgsGeometry",
            "method_name": "isGeosValid",
            "full_signature": "QgsGeometry.isGeosValid() -> bool",
            "description": "Checks validity of the geometry using GEOS.",
            "parameters": [],
            "return_type": "bool",
            "source": "official_docs",
        },
        {
            "class_name": "QgsGeometry",
            "method_name": "isEmpty",
            "full_signature": "QgsGeometry.isEmpty() -> bool",
            "description": "Returns true if the geometry is empty.",
            "parameters": [],
            "return_type": "bool",
            "source": "official_docs",
        },
        {
            "class_name": "QgsGeometry",
            "method_name": "type",
            "full_signature": "QgsGeometry.type() -> QgsWkbTypes.GeometryType",
            "description": "Returns the geometry type.",
            "parameters": [],
            "return_type": "QgsWkbTypes.GeometryType",
            "source": "official_docs",
        },
        {
            "class_name": "QgsGeometry",
            "method_name": "convertToType",
            "full_signature": "QgsGeometry.convertToType(destType: QgsWkbTypes.GeometryType, destMultiType: bool = False) -> QgsGeometry",
            "description": "Converts the geometry to a specified type.",
            "parameters": [
                {"name": "destType", "type": "QgsWkbTypes.GeometryType", "default": ""},
                {"name": "destMultiType", "type": "bool", "default": "False"}
            ],
            "return_type": "QgsGeometry",
            "source": "official_docs",
        },
        {
            "class_name": "QgsGeometry",
            "method_name": "makeValid",
            "full_signature": "QgsGeometry.makeValid() -> QgsGeometry",
            "description": "Makes the geometry valid if it is not already.",
            "parameters": [],
            "return_type": "QgsGeometry",
            "source": "official_docs",
        },

        # QgsProject
        {
            "class_name": "QgsProject",
            "method_name": "instance",
            "full_signature": "QgsProject.instance() -> QgsProject",
            "description": "Returns the QgsProject singleton instance.",
            "parameters": [],
            "return_type": "QgsProject",
            "source": "official_docs",
        },
        {
            "class_name": "QgsProject",
            "method_name": "mapLayers",
            "full_signature": "QgsProject.mapLayers() -> Dict[str, QgsMapLayer]",
            "description": "Returns a dictionary of all registered map layers.",
            "parameters": [],
            "return_type": "Dict[str, QgsMapLayer]",
            "source": "official_docs",
        },
        {
            "class_name": "QgsProject",
            "method_name": "addMapLayer",
            "full_signature": "QgsProject.addMapLayer(layer: QgsMapLayer, addToLegend: bool = True, takeOwnership: bool = True) -> QgsMapLayer",
            "description": "Adds a layer to this project.",
            "parameters": [
                {"name": "layer", "type": "QgsMapLayer", "default": ""},
                {"name": "addToLegend", "type": "bool", "default": "True"},
                {"name": "takeOwnership", "type": "bool", "default": "True"}
            ],
            "return_type": "QgsMapLayer",
            "source": "official_docs",
        },
        {
            "class_name": "QgsProject",
            "method_name": "removeMapLayer",
            "full_signature": "QgsProject.removeMapLayer(layerId: str)",
            "description": "Removes a layer from this project.",
            "parameters": [
                {"name": "layerId", "type": "str", "default": ""}
            ],
            "return_type": "",
            "source": "official_docs",
        },
        {
            "class_name": "QgsProject",
            "method_name": "crs",
            "full_signature": "QgsProject.crs() -> QgsCoordinateReferenceSystem",
            "description": "Returns the project's coordinate reference system.",
            "parameters": [],
            "return_type": "QgsCoordinateReferenceSystem",
            "source": "official_docs",
        },
        {
            "class_name": "QgsProject",
            "method_name": "setCrs",
            "full_signature": "QgsProject.setCrs(crs: QgsCoordinateReferenceSystem)",
            "description": "Sets the project's coordinate reference system.",
            "parameters": [
                {"name": "crs", "type": "QgsCoordinateReferenceSystem", "default": ""}
            ],
            "return_type": "",
            "source": "official_docs",
        },
        {
            "class_name": "QgsProject",
            "method_name": "fileName",
            "full_signature": "QgsProject.fileName() -> str",
            "description": "Returns the project file name.",
            "parameters": [],
            "return_type": "str",
            "source": "official_docs",
        },
        {
            "class_name": "QgsProject",
            "method_name": "write",
            "full_signature": "QgsProject.write(file: str) -> bool",
            "description": "Writes the project to the given file.",
            "parameters": [
                {"name": "file", "type": "str", "default": ""}
            ],
            "return_type": "bool",
            "source": "official_docs",
        },
        {
            "class_name": "QgsProject",
            "method_name": "read",
            "full_signature": "QgsProject.read(file: str) -> bool",
            "description": "Reads a project from the given file.",
            "parameters": [
                {"name": "file", "type": "str", "default": ""}
            ],
            "return_type": "bool",
            "source": "official_docs",
        },
        {
            "class_name": "QgsProject",
            "method_name": "layerTreeRoot",
            "full_signature": "QgsProject.layerTreeRoot() -> QgsLayerTreeGroup",
            "description": "Returns the root node of the project's layer tree.",
            "parameters": [],
            "return_type": "QgsLayerTreeGroup",
            "source": "official_docs",
        },
        {
            "class_name": "QgsProject",
            "method_name": "count",
            "full_signature": "QgsProject.count() -> int",
            "description": "Returns the number of layers in the project.",
            "parameters": [],
            "return_type": "int",
            "source": "official_docs",
        },

        # QgsFeature
        {
            "class_name": "QgsFeature",
            "method_name": "QgsFeature",
            "full_signature": "QgsFeature(fields: QgsFields = QgsFields(), id: int = FID_NULL)",
            "description": "Constructs a feature with the given fields and id.",
            "parameters": [
                {"name": "fields", "type": "QgsFields", "default": "QgsFields()"},
                {"name": "id", "type": "int", "default": "FID_NULL"}
            ],
            "return_type": "",
            "source": "official_docs",
        },
        {
            "class_name": "QgsFeature",
            "method_name": "id",
            "full_signature": "QgsFeature.id() -> int",
            "description": "Returns the feature's id.",
            "parameters": [],
            "return_type": "int",
            "source": "official_docs",
        },
        {
            "class_name": "QgsFeature",
            "method_name": "fields",
            "full_signature": "QgsFeature.fields() -> QgsFields",
            "description": "Returns the feature's fields.",
            "parameters": [],
            "return_type": "QgsFields",
            "source": "official_docs",
        },
        {
            "class_name": "QgsFeature",
            "method_name": "geometry",
            "full_signature": "QgsFeature.geometry() -> QgsGeometry",
            "description": "Returns the feature's geometry.",
            "parameters": [],
            "return_type": "QgsGeometry",
            "source": "official_docs",
        },
        {
            "class_name": "QgsFeature",
            "method_name": "setGeometry",
            "full_signature": "QgsFeature.setGeometry(geometry: QgsGeometry)",
            "description": "Sets the feature's geometry.",
            "parameters": [
                {"name": "geometry", "type": "QgsGeometry", "default": ""}
            ],
            "return_type": "",
            "source": "official_docs",
        },
        {
            "class_name": "QgsFeature",
            "method_name": "attributes",
            "full_signature": "QgsFeature.attributes() -> List[Any]",
            "description": "Returns the feature's attributes as a list.",
            "parameters": [],
            "return_type": "List[Any]",
            "source": "official_docs",
        },
        {
            "class_name": "QgsFeature",
            "method_name": "setAttributes",
            "full_signature": "QgsFeature.setAttributes(attributes: List[Any])",
            "description": "Sets the feature's attributes.",
            "parameters": [
                {"name": "attributes", "type": "List[Any]", "default": ""}
            ],
            "return_type": "",
            "source": "official_docs",
        },
        {
            "class_name": "QgsFeature",
            "method_name": "setAttribute",
            "full_signature": "QgsFeature.setAttribute(field: str, value: Any) -> bool",
            "description": "Sets a single attribute's value by field name.",
            "parameters": [
                {"name": "field", "type": "str", "default": ""},
                {"name": "value", "type": "Any", "default": ""}
            ],
            "return_type": "bool",
            "source": "official_docs",
        },
        {
            "class_name": "QgsFeature",
            "method_name": "isValid",
            "full_signature": "QgsFeature.isValid() -> bool",
            "description": "Returns true if the feature is valid.",
            "parameters": [],
            "return_type": "bool",
            "source": "official_docs",
        },

        # QgsCoordinateReferenceSystem
        {
            "class_name": "QgsCoordinateReferenceSystem",
            "method_name": "QgsCoordinateReferenceSystem",
            "full_signature": "QgsCoordinateReferenceSystem()",
            "description": "Constructs a CRS using the default WGS84 CRS.",
            "parameters": [],
            "return_type": "",
            "source": "official_docs",
        },
        {
            "class_name": "QgsCoordinateReferenceSystem",
            "method_name": "authid",
            "full_signature": "QgsCoordinateReferenceSystem.authid() -> str",
            "description": "Returns the authority identifier for the CRS.",
            "parameters": [],
            "return_type": "str",
            "source": "official_docs",
        },
        {
            "class_name": "QgsCoordinateReferenceSystem",
            "method_name": "description",
            "full_signature": "QgsCoordinateReferenceSystem.description() -> str",
            "description": "Returns a human-readable description for the CRS.",
            "parameters": [],
            "return_type": "str",
            "source": "official_docs",
        },
        {
            "class_name": "QgsCoordinateReferenceSystem",
            "method_name": "isValid",
            "full_signature": "QgsCoordinateReferenceSystem.isValid() -> bool",
            "description": "Returns true if the CRS is valid.",
            "parameters": [],
            "return_type": "bool",
            "source": "official_docs",
        },
        {
            "class_name": "QgsCoordinateReferenceSystem",
            "method_name": "toWkt",
            "full_signature": "QgsCoordinateReferenceSystem.toWkt() -> str",
            "description": "Returns the WKT representation of the CRS.",
            "parameters": [],
            "return_type": "str",
            "source": "official_docs",
        },
        {
            "class_name": "QgsCoordinateReferenceSystem",
            "method_name": "createFromString",
            "full_signature": "QgsCoordinateReferenceSystem.createFromString(definition: str) -> bool",
            "description": "Sets the CRS from a string definition.",
            "parameters": [
                {"name": "definition", "type": "str", "default": ""}
            ],
            "return_type": "bool",
            "source": "official_docs",
        },
        {
            "class_name": "QgsCoordinateReferenceSystem",
            "method_name": "createFromEpsg",
            "full_signature": "QgsCoordinateReferenceSystem.createFromEpsg(epsg: int) -> bool",
            "description": "Sets the CRS from an EPSG code.",
            "parameters": [
                {"name": "epsg", "type": "int", "default": ""}
            ],
            "return_type": "bool",
            "source": "official_docs",
        },
        {
            "class_name": "QgsCoordinateReferenceSystem",
            "method_name": "postgisSrid",
            "full_signature": "QgsCoordinateReferenceSystem.postgisSrid() -> int",
            "description": "Returns the PostGIS SRID for the CRS.",
            "parameters": [],
            "return_type": "int",
            "source": "official_docs",
        },
        {
            "class_name": "QgsCoordinateReferenceSystem",
            "method_name": "isValid",
            "full_signature": "QgsCoordinateReferenceSystem.isValid() -> bool",
            "description": "Returns true if the CRS is valid.",
            "parameters": [],
            "return_type": "bool",
            "source": "official_docs",
        },

        # QgsMapCanvas
        {
            "class_name": "QgsMapCanvas",
            "method_name": "extent",
            "full_signature": "QgsMapCanvas.extent() -> QgsRectangle",
            "description": "Returns the current map extent.",
            "parameters": [],
            "return_type": "QgsRectangle",
            "source": "official_docs",
        },
        {
            "class_name": "QgsMapCanvas",
            "method_name": "setExtent",
            "full_signature": "QgsMapCanvas.setExtent(rect: QgsRectangle)",
            "description": "Sets the map extent.",
            "parameters": [
                {"name": "rect", "type": "QgsRectangle", "default": ""}
            ],
            "return_type": "",
            "source": "official_docs",
        },
        {
            "class_name": "QgsMapCanvas",
            "method_name": "zoomToFullExtent",
            "full_signature": "QgsMapCanvas.zoomToFullExtent()",
            "description": "Zooms to the full extent of all layers.",
            "parameters": [],
            "return_type": "",
            "source": "official_docs",
        },
        {
            "class_name": "QgsMapCanvas",
            "method_name": "refresh",
            "full_signature": "QgsMapCanvas.refresh()",
            "description": "Repaints the map canvas.",
            "parameters": [],
            "return_type": "",
            "source": "official_docs",
        },
        {
            "class_name": "QgsMapCanvas",
            "method_name": "scale",
            "full_signature": "QgsMapCanvas.scale() -> float",
            "description": "Returns the map scale denominator.",
            "parameters": [],
            "return_type": "float",
            "source": "official_docs",
        },
        {
            "class_name": "QgsMapCanvas",
            "method_name": "setDestinationCrs",
            "full_signature": "QgsMapCanvas.setDestinationCrs(crs: QgsCoordinateReferenceSystem)",
            "description": "Sets the destination CRS for the map canvas.",
            "parameters": [
                {"name": "crs", "type": "QgsCoordinateReferenceSystem", "default": ""}
            ],
            "return_type": "",
            "source": "official_docs",
        },
        {
            "class_name": "QgsMapCanvas",
            "method_name": "layers",
            "full_signature": "QgsMapCanvas.layers() -> List[QgsMapLayer]",
            "description": "Returns the list of layers rendered in the canvas.",
            "parameters": [],
            "return_type": "List[QgsMapLayer]",
            "source": "official_docs",
        },

        # QgsApplication
        {
            "class_name": "QgsApplication",
            "method_name": "qgisSettingsDirPath",
            "full_signature": "QgsApplication.qgisSettingsDirPath() -> str",
            "description": "Returns the path to the QGIS settings directory.",
            "parameters": [],
            "return_type": "str",
            "source": "official_docs",
        },
        {
            "class_name": "QgsApplication",
            "method_name": "processingRegistry",
            "full_signature": "QgsApplication.processingRegistry() -> QgsProcessingRegistry",
            "description": "Returns the application's processing registry.",
            "parameters": [],
            "return_type": "QgsProcessingRegistry",
            "source": "official_docs",
        },
    ]

    @classmethod
    def get_docs(cls) -> list[APIDocEntry]:
        """获取内置的官方文档"""
        entries = []
        for doc in cls.OFFICIAL_DOCS:
            entries.append(APIDocEntry(**doc))
        return entries

    @classmethod
    def get_doc_count(cls) -> int:
        """获取内置文档数量"""
        return len(cls.OFFICIAL_DOCS)
