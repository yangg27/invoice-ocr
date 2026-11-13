"""打包入口（PyInstaller 用）。"""
from __future__ import annotations

import multiprocessing
import sys

from app.main import _main_guard

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(_main_guard())