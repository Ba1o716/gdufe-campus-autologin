"""源码运行入口（可被 python.exe / pythonw.exe 直接执行，不依赖当前工作目录）。

    python  campus_login_main.py --status
    pythonw campus_login_main.py --tray
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from campus_login.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
