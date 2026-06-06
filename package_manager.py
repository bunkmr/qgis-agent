import subprocess
import sys
import os


class PackageManager:
    def __init__(self, required_modules):
        self.required_modules = required_modules
        self.missing = []

    def check_dependencies(self):
        self.missing = []
        for module in self.required_modules:
            try:
                __import__(module)
            except ImportError:
                self.missing.append(module)
        return self.missing

    def install_missing(self):
        if not self.missing:
            return True
        try:
            python_exe = self._find_python()
            if not python_exe:
                return False
            for module in self.missing:
                subprocess.check_call(
                    [python_exe, "-m", "pip", "install", module, "--user", "--quiet"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            self.missing = []
            return True
        except Exception:
            return False

    def _find_python(self):
        candidates = [
            os.path.join(os.path.dirname(sys.executable), "python.exe"),
            sys.executable,
            r"C:\OSGeo4W\bin\python.exe",
            r"C:\Program Files\QGIS 3.34.4\bin\python.exe",
            r"C:\Program Files\QGIS 3.34\bin\python.exe",
            r"C:\Program Files\QGIS 3.32\bin\python.exe",
            r"C:\Program Files\QGIS 3.30\bin\python.exe",
            r"C:\Program Files\QGIS 3.28\bin\python.exe",
            "python",
            "python3",
        ]
        seen = set()
        for exe in candidates:
            if exe in seen:
                continue
            seen.add(exe)
            if not os.path.isfile(exe) and exe not in ("python", "python3"):
                continue
            try:
                subprocess.check_call(
                    [exe, "-c", "import pip; print('ok')"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                )
                return exe
            except Exception:
                continue
        return None
