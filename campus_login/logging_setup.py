"""日志初始化与脱敏。

要求：
  * 日志文件自动轮转，避免无限增长；
  * 绝不记录密码、Cookie、Token、Authorization 等敏感信息。
"""

from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Iterable

from .config import Config
from .paths import log_path

LOGGER_NAME = "campus_login"

_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(?i)\b(password|passwd|pwd)\s*[=:]\s*([^\s&;,]+)"), r"\1=***"),
    (re.compile(r"(?i)\b(token|access_token|session_id|sessionid)\s*[=:]\s*([^\s&;,]+)"), r"\1=***"),
    (re.compile(r"(?i)(authorization)\s*[=:]\s*([^\r\n]+)"), r"\1: ***"),
    (re.compile(r"(?i)(cookie)\s*[=:]\s*([^\r\n]+)"), r"\1: ***"),
    (re.compile(r"(?i)\b(JSESSIONID|PHPSESSID)=([^\s;]+)"), r"\1=***"),
]


def redact_text(text: str, secrets: Iterable[str] = ()) -> str:
    """先替换已知密文（真实密码），再对常见敏感字段做正则脱敏。"""
    result = text
    for secret in secrets:
        if secret and len(secret) >= 3:
            result = result.replace(secret, "***")
    for pattern, replacement in _PATTERNS:
        result = pattern.sub(replacement, result)
    return result


class RedactingFilter(logging.Filter):
    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__()
        self._secrets: list[str] = [s for s in secrets if s]

    def add_secret(self, secret: str | None) -> None:
        if secret and len(secret) >= 3 and secret not in self._secrets:
            self._secrets.append(secret)

    def clear_secrets(self) -> None:
        self._secrets.clear()

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        cleaned = redact_text(message, self._secrets)
        if cleaned != message:
            record.msg = cleaned
            record.args = ()
        return True


_redactor = RedactingFilter()


def redactor() -> RedactingFilter:
    """全局共享的脱敏过滤器，读到凭据后可追加要屏蔽的字符串。"""
    return _redactor


def setup_logging(config: Config, console: bool = False, path: Path | None = None) -> Path:
    """初始化日志，返回实际使用的日志文件路径。可重复调用（幂等）。

    日志文件不可写（被占用、磁盘满、目录权限异常）时**不会**让程序启动失败，
    而是自动改用备用位置；实在不行就只保留控制台/空处理器。
    """
    requested = path or log_path()
    level = getattr(logging, config.log_level.upper(), logging.INFO)
    root = logging.getLogger(LOGGER_NAME)
    root.setLevel(logging.DEBUG)
    root.propagate = False

    for handler in list(root.handlers):
        root.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    def build_file_logger(target: Path):
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        handler = RotatingFileHandler(
            target,
            maxBytes=int(config.log_max_bytes),
            backupCount=int(config.log_backup_count),
            encoding="utf-8",
        )
        handler.setFormatter(formatter)
        handler.setLevel(level)
        handler.addFilter(_redactor)
        return handler

    used_path = requested
    try:
        root.addHandler(build_file_logger(requested))
    except Exception as exc:
        import tempfile

        used_path = Path(tempfile.gettempdir()) / f"{LOGGER_NAME}.log"
        try:
            root.addHandler(build_file_logger(used_path))
            root.warning("无法写入日志文件（%s），已改用 %s", type(exc).__name__, used_path)
        except Exception as exc2:
            used_path = requested
            root.addHandler(logging.NullHandler())
            if console:
                print(f"[警告] 日志文件不可写（{type(exc2).__name__}），本次运行不写日志文件")

    if console:
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        stream.setLevel(level)
        stream.addFilter(_redactor)
        root.addHandler(stream)

    return used_path


def get_logger(name: str) -> logging.Logger:
    if name.startswith(LOGGER_NAME):
        return logging.getLogger(name)
    return logging.getLogger(f"{LOGGER_NAME}.{name}")
