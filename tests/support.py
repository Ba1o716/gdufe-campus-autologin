"""测试辅助工具（不随程序发布，只用于自动化测试）。"""

from __future__ import annotations

import os
import shutil
import unittest
import uuid
import logging
from pathlib import Path

from campus_login.retry import FakeSleeper

# 测试时不希望日志被打印到控制台（logging 的 lastResort 处理器）
logging.getLogger("campus_login").addHandler(logging.NullHandler())

# 测试用的临时目录放在项目 work/ 下（而不是系统 %TEMP%，某些环境下不可写）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEST_TMP_ROOT = Path(os.environ.get("CAMPUSLOGIN_TEST_TMP") or (PROJECT_ROOT / "work" / "test-tmp"))


class TempDataDirTestCase(unittest.TestCase):
    """把 %APPDATA%\\CampusLogin 重定向到临时目录，避免污染真实用户数据。"""

    def setUp(self) -> None:
        super().setUp()
        self._previous = os.environ.get("CAMPUSLOGIN_DATA_DIR")
        TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
        # 注意：不要用 tempfile.mkdtemp —— 它在 Windows 上会创建带限制性 ACL 的目录，
        # 导致后续写入被拒绝。
        self._tempdir = TEST_TMP_ROOT / f"case-{uuid.uuid4().hex[:12]}"
        self._tempdir.mkdir(parents=True, exist_ok=True)
        os.environ["CAMPUSLOGIN_DATA_DIR"] = str(self._tempdir)
        self.data_dir = self._tempdir

    def tearDown(self) -> None:
        if self._previous is None:
            os.environ.pop("CAMPUSLOGIN_DATA_DIR", None)
        else:
            os.environ["CAMPUSLOGIN_DATA_DIR"] = self._previous
        shutil.rmtree(self._tempdir, ignore_errors=True)
        super().tearDown()


class LimitedSleeper(FakeSleeper):
    """最多等待 N 次后自动停止，避免 run_forever 的测试无限循环。"""

    def __init__(self, limit: int = 3) -> None:
        super().__init__()
        self.limit = limit
        self.count = 0

    def sleep(self, seconds: float) -> bool:
        self.count += 1
        if self.count > self.limit:
            self.stop()
            return False
        return super().sleep(seconds)


class StopAfterSleeper(FakeSleeper):
    """在第 N 次等待时打断（模拟用户退出 / 程序关闭）。"""

    def __init__(self, stop_at: int = 2) -> None:
        super().__init__()
        self.stop_at = stop_at
        self.count = 0

    def sleep(self, seconds: float) -> bool:
        self.count += 1
        if self.count >= self.stop_at:
            self.stop()
            return False
        return super().sleep(seconds)
