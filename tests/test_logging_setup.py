"""日志系统测试：格式、轮转、密码脱敏。"""

from __future__ import annotations

import logging
import unittest

from campus_login.config import Config
from campus_login.logging_setup import (
    LOGGER_NAME,
    get_logger,
    redact_text,
    redactor,
    setup_logging,
)

from .support import TempDataDirTestCase


class LoggingTest(TempDataDirTestCase):
    def tearDown(self) -> None:
        redactor().clear_secrets()
        super().tearDown()

    def test_log_file_is_created_with_timestamp(self):
        config = Config()
        path = setup_logging(config)
        get_logger("test").info("程序启动")
        for handler in logging.getLogger(LOGGER_NAME).handlers:
            handler.flush()
        content = path.read_text(encoding="utf-8")
        self.assertIn("程序启动", content)
        line = content.strip().splitlines()[-1]
        self.assertRegex(line, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} ")

    def test_password_is_redacted_from_log(self):
        config = Config()
        path = setup_logging(config)
        redactor().add_secret("SuperSecret123")
        logger = get_logger("test")
        logger.info("登录请求 password=SuperSecret123")
        logger.info("原始密码是 SuperSecret123 请勿外传")
        logger.info("Authorization: Bearer abcdef")
        logger.info("Cookie: JSESSIONID=ABCDEF")
        for handler in logging.getLogger(LOGGER_NAME).handlers:
            handler.flush()
        content = path.read_text(encoding="utf-8")
        self.assertNotIn("SuperSecret123", content)
        self.assertIn("***", content)
        self.assertNotIn("JSESSIONID=ABCDEF", content)
        self.assertNotIn("Bearer abcdef", content)

    def test_redact_text_patterns(self):
        self.assertEqual(redact_text("password=abc123"), "password=***")
        self.assertIn("***", redact_text("token=xyz"))
        self.assertIn("***", redact_text("Cookie: a=b"))
        self.assertIn("***", redact_text("Authorization: Basic zzz"))

    def test_log_file_rotates(self):
        config = Config()
        config.log_max_bytes = 16 * 1024
        config.log_backup_count = 2
        path = setup_logging(config)
        logger = get_logger("test")
        for index in range(600):
            logger.info("日志轮转测试第 %d 行，填充一些内容让文件变大 %s", index, "x" * 80)
        for handler in logging.getLogger(LOGGER_NAME).handlers:
            handler.flush()
        backups = sorted(path.parent.glob(path.name + ".*"))
        self.assertTrue(path.exists())
        self.assertTrue(backups, "应当生成轮转后的备份日志")
        self.assertLessEqual(len(backups), config.log_backup_count)

    def test_setup_logging_is_idempotent(self):
        config = Config()
        setup_logging(config)
        setup_logging(config)
        handlers = logging.getLogger(LOGGER_NAME).handlers
        self.assertEqual(len(handlers), 1)


if __name__ == "__main__":
    unittest.main()
