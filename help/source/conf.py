# Configuration file for the Sphinx documentation builder.
import os
import sys

project = 'QGIS Agent'
copyright = '2026, QGIS Agent Contributors'
author = 'QGIS Agent Contributors'
release = '1.0.0'

extensions = [
    'sphinx.ext.autodoc',
    'sphinx.ext.napoleon',
    'sphinx.ext.viewcode',
    'sphinx.ext.intersphinx',
]

templates_path = ['_templates']
exclude_patterns = []

language = 'zh_CN'

html_theme = 'sphinx_rtd_theme'
html_static_path = ['_static']
html_title = 'QGIS Agent 文档'
