"""Include the same benchmark, UI, and evidence resources in installed wheels."""
from pathlib import Path
import shutil

from setuptools import setup
from setuptools.command.build_py import build_py


class BuildPy(build_py):
    def run(self):
        super().run()
        root = Path(__file__).parent
        for name in ("benchmarks", "web", "examples"):
            shutil.copytree(root / name, Path(self.build_lib) / "autotuner" / "resources" / name, dirs_exist_ok=True)


setup(cmdclass={"build_py": BuildPy})
