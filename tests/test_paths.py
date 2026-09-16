"""数据目录 / 日志目录的健壮性测试。

目标：配置目录或日志文件不可写时，程序也不能启动失败。
"""

from __future__ import annotations

import logging
import os
import unittest

from campus_login.config import Config, load_config, save_config
from campus_login.logging_setup import LOGGER_NAME, get_logger, setup_logging
from campus_login.paths import data_dir, log_path

from .support import TempDataDirTestCase


class DataDirTest(TempDataDirTestCase):
    def test_uses_override(self):
        self.assertEqual(data_dir().resolve(), self.data_dir.resolve())

    def test_fallback_when_first_candidate_not_writable(self):
        """首选目录不可写时，应当自动改用下一个可用目录。"""
        import campus_login.paths as paths_module

        blocked = self.data_dir / "blocked"
        blocked.write_text("占用", encoding="utf-8")   # 同名文件，无法当目录用
        good = self.data_dir / "good"
        original = paths_module._data_dir_candidates
        paths_module._data_dir_candidates = lambda: [blocked, good]
        os.environ["CAMPUSLOGIN_DATA_DIR"] = str(blocked)
        try:
            resolved = data_dir()
            self.assertEqual(resolved, good)
            save_config(Config())
            self.assertTrue((good / "config.json").exists())
            self.assertEqual(load_config().adapter, "generic")
        finally:
            paths_module._data_dir_candidates = original
            os.environ["CAMPUSLOGIN_DATA_DIR"] = str(self.data_dir)


class LoggingFallbackTest(TempDataDirTestCase):
    def test_unwritable_log_file_does_not_crash(self):
        config = Config()
        blocked = self.data_dir / "logs" / "app.log"
        blocked.parent.mkdir(parents=True, exist_ok=True)
        os.makedirs(blocked, exist_ok=True)      # 用目录占住日志文件位置
        used = setup_logging(config, path=blocked)
        get_logger("test").info("这条日志不应该让程序崩溃")
        for handler in logging.getLogger(LOGGER_NAME).handlers:
            handler.flush()
        self.assertTrue(str(used))
        self.assertNotEqual(str(used), str(blocked))

    def test_normal_path_still_works(self):
        config = Config()
        used = setup_logging(config)
        get_logger("test").info("正常写入")
        for handler in logging.getLogger(LOGGER_NAME).handlers:
            handler.flush()
        self.assertEqual(used, log_path())
        self.assertIn("正常写入", used.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
