import os

def load_env_file(path=None):
    if path is None:
        path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    os.environ[key.strip()] = value.strip()

load_env_file()

DEBUG_MODE = os.environ.get("QGIS_AGENT_DEBUG", "False") == "True"
DB_NAME = "QGIS_Agent.db"
PLUGIN_NAME = "QGIS Agent"
PLUGIN_VERSION = "1.0.0"
