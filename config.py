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


def _read_metadata_version():
    """从 metadata.txt 读取插件版本号（版本号的唯一真源）。

    metadata.txt 缺失或缺少 version 字段时直接抛异常，
    绝不静默回退到默认版本号（会导致打出版本号错误的包）。
    """
    metadata_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "metadata.txt")
    if not os.path.exists(metadata_path):
        raise RuntimeError("找不到 metadata.txt，无法确定插件版本号：%s" % metadata_path)

    # 用 utf-8-sig 兼容可能存在的 BOM
    with open(metadata_path, "r", encoding="utf-8-sig") as f:
        for raw_line in f:
            line = raw_line.strip()
            if line.startswith("["):
                continue  # 跳过 section 头
            if line.startswith("version="):
                version = line.split("=", 1)[1].strip()
                if not version:
                    raise RuntimeError("metadata.txt 中的 version 字段为空：%s" % metadata_path)
                return version

    raise RuntimeError("metadata.txt 中缺少 version 字段：%s" % metadata_path)


DEBUG_MODE = os.environ.get("QGIS_AGENT_DEBUG", "False") == "True"
DB_NAME = "QGIS_Agent.db"
PLUGIN_NAME = "QGIS Agent"
PLUGIN_VERSION = _read_metadata_version()
