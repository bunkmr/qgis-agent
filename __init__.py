# -*- coding: utf-8 -*-

import os
import sys

scriptDir = os.path.dirname(os.path.abspath(__file__))
extpluginDir = os.path.join(scriptDir, "extlibs")

if not os.path.exists(extpluginDir):
    os.makedirs(extpluginDir, exist_ok=True)

if extpluginDir not in sys.path:
    sys.path.insert(0, extpluginDir)


def classFactory(iface):
    from .qgis_agent import QGISAgent
    return QGISAgent(iface)
