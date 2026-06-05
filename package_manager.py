import subprocess
import sys
import os


class PackageManager:
    def __init__(self, required_modules):
        self.required_modules = required_modules

    def check_dependencies(self):
        missing = []
        for module in self.required_modules:
            try:
                __import__(module)
            except ImportError:
                missing.append(module)

        if missing:
            self._install_missing(missing)

    def _install_missing(self, missing_modules):
        try:
            python_exe = sys.executable
            for module in missing_modules:
                subprocess.check_call(
                    [python_exe, "-m", "pip", "install", module, "--user", "--quiet"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        except Exception:
            pass
