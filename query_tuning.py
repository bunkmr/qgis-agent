"""
Query Tuning and Data Overview for QGIS Agent
Optimizes user input understanding and provides data context to LLM.
Inspired by SpatialAnalysisAgent's Query Tuning system.
"""

from typing import Dict, List, Optional, Any


class QueryTuner:
    """Optimizes user queries for better GIS task understanding"""

    QUERY_TUNING_PROMPT = """You are a GIS expert. Convert the following user request into a **short GIS task description**.

Think step-by-step about what the user is asking, and then write a **concise, domain-specific description** of the GIS task they want to perform.

INSTRUCTIONS:
- Do NOT include steps related to data acquisition or downloading data.
- Do NOT mention specific software (e.g., GIS software, ArcGIS, QGIS).
- Focus ONLY on spatial analysis or GIS operations.
- Use technical GIS terms where appropriate (e.g., Buffer, Clip, Reproject, Attribute Query).
- Check the Data overview and select the suitable layers, attributes, and field information that would be needed for the User Query.
- Please include the layer name in the task description.
- Please always check the Data overview for projection information and the User Query before deciding whether reprojection of data is needed.
- Only do the reprojection as needed when 1) e.g., calculating distances/buffers that needs projected CRS, and the layers have different projections.
- Start each operation with a label
- Output ONLY the GIS task description - do NOT explain your reasoning.

User Query:
"{query}"

Data Overview:
"{data_overview}"

Let's think step-by-step:
1. What is the user's goal?
2. What data is available (review the Data overview for layers, attributes, and field information)?
3. What GIS operations are needed to achieve it?
4. Write a concise summary of the GIS task
5. List labeled operations to perform.

Output Sample:
Perform a spatial analysis to identify and quantify the counties in Pennsylvania with suitability for tree planting based on annual rainfall. Specifically, execute the following tasks:
1. **Attribute Query**: Filter the counties of Pennsylvania using an attribute query to select those with annual rainfall greater than 2.5 inches.
2. **Calculate Area**: Determine the total area of the selected counties to assess the percentage of Pennsylvania suitable for tree planting.
3. **Count Features**: Count the number of counties meeting the rainfall criteria to identify how many are suitable for tree planting.
"""

    def __init__(self, llm=None):
        self.llm = llm

    def tune_query(self, user_query: str, data_overview: str = None) -> str:
        """
        Tune user query for better GIS task understanding

        Args:
            user_query: Original user query
            data_overview: Optional data overview context

        Returns:
            Tuned query description
        """
        if not data_overview:
            data_overview = "No data overview available"

        prompt = self.QUERY_TUNING_PROMPT.format(
            query=user_query,
            data_overview=data_overview
        )

        if self.llm:
            try:
                response = self.llm.invoke([HumanMessage(content=prompt)])
                return response.content if hasattr(response, 'content') else str(response)
            except Exception as e:
                print(f"Query tuning failed: {e}")
                return user_query
        else:
            # Fallback: return original query with basic formatting
            return self._basic_tuning(user_query)

    def _basic_tuning(self, query: str) -> str:
        """Basic query tuning without LLM"""
        # Add step-by-step structure
        tuned = f"Perform the following GIS task:\n\n{query}\n\n"
        tuned += "Steps to perform:\n"
        tuned += "1. Analyze the request\n"
        tuned += "2. Identify required data\n"
        tuned += "3. Execute operations\n"
        tuned += "4. Report results"
        return tuned


class DataOverview:
    """Generates data overview for context injection"""

    def __init__(self, qgis_interface=None):
        self.iface = qgis_interface

    def get_data_overview(self) -> str:
        """
        Generate overview of currently loaded data in QGIS

        Returns:
            Formatted data overview string
        """
        try:
            from qgis.core import QgsProject, QgsMapLayer

            project = QgsProject.instance()
            layers = project.mapLayers()

            if not layers:
                return "No layers loaded in the current project."

            overview_parts = []

            for layer_id, layer in layers.items():
                layer_info = self._get_layer_info(layer)
                overview_parts.append(layer_info)

            return "\n\n".join(overview_parts)

        except Exception as e:
            return f"Error generating data overview: {str(e)}"

    def _get_layer_info(self, layer) -> str:
        """Get detailed information about a layer"""
        from qgis.core import QgsMapLayer, QgsVectorLayer, QgsRasterLayer

        info_parts = [f"Layer: {layer.name()}"]
        info_parts.append(f"Type: {self._get_layer_type(layer)}")

        if layer.type() == QgsMapLayer.VectorLayer:
            info_parts.append(f"Feature Count: {layer.featureCount()}")
            info_parts.append(f"CRS: {layer.crs().authid()}")

            # Get field information
            fields = layer.fields()
            if fields.count() > 0:
                field_names = [field.name() for field in fields]
                info_parts.append(f"Fields: {', '.join(field_names[:10])}")  # Limit to 10 fields

            # Get extent
            extent = layer.extent()
            if extent.isValid():
                info_parts.append(f"Extent: {extent.toString()}")

        elif layer.type() == QgsMapLayer.RasterLayer:
            info_parts.append(f"CRS: {layer.crs().authid()}")

            # Get raster info
            extent = layer.extent()
            if extent.isValid():
                info_parts.append(f"Extent: {extent.toString()}")

            # Get band count
            if layer.dataProvider():
                info_parts.append(f"Bands: {layer.dataProvider().bandCount()}")

        return "\n".join(info_parts)

    def _get_layer_type(self, layer) -> str:
        """Get human-readable layer type"""
        from qgis.core import QgsMapLayer, QgsVectorLayer, QgsRasterLayer

        if layer.type() == QgsMapLayer.VectorLayer:
            geom_type = layer.geometryType()
            geom_names = {0: "Point", 1: "Line", 2: "Polygon", 3: "No Geometry", 4: "Unknown"}
            return f"Vector ({geom_names.get(geom_type, 'Unknown')})"
        elif layer.type() == QgsMapLayer.RasterLayer:
            return "Raster"
        elif layer.type() == QgsMapLayer.MeshLayer:
            return "Mesh"
        elif layer.type() == QgsMapLayer.VectorTileLayer:
            return "Vector Tile"
        else:
            return f"Unknown ({layer.type()})"

    def get_layer_summary(self) -> Dict:
        """Get summary statistics of loaded layers"""
        try:
            from qgis.core import QgsProject, QgsMapLayer, QgsVectorLayer

            project = QgsProject.instance()
            layers = project.mapLayers()

            summary = {
                "total_layers": len(layers),
                "vector_layers": 0,
                "raster_layers": 0,
                "total_features": 0,
                "layer_names": []
            }

            for layer_id, layer in layers.items():
                summary["layer_names"].append(layer.name())

                if layer.type() == QgsMapLayer.VectorLayer:
                    summary["vector_layers"] += 1
                    summary["total_features"] += layer.featureCount()
                elif layer.type() == QgsMapLayer.RasterLayer:
                    summary["raster_layers"] += 1

            return summary

        except Exception as e:
            return {"error": str(e)}
