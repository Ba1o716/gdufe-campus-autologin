"""重试策略与可中断的等待。"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from .config import Config


@dataclass
class RetryPolicy:
    """重试间隔序列。

    第 1 次失败后等待 schedule[0] 秒，第 2 次失败后等待 schedule[1] 秒……
    超出序列长度后使用 interval 秒。
    """

    schedule: list[float] = field(default_factory=lambda: [5.0, 10.0, 20.0, 30.0, 60.0])
    interval: float = 60.0
    max_retries: int = 5

    @classmethod
    def from_config(cls, config: Config) -> "RetryPolicy":
        return cls(
            schedule=[float(x) for x in config.retry_schedule_seconds],
            interval=float(config.retry_interval_seconds),
            max_retries=int(config.max_retries),
        )

    def delay_for(self, failed_attempts: int) -> float:
        """failed_attempts 从 1 开始计数。"""
        if failed_attempts <= 0:
            return 0.0
        if failed_attempts <= len(self.schedule):
            return float(self.schedule[failed_attempts - 1])
        return float(self.interval)

    def can_retry(self, failed_attempts: int) -> bool:
        return failed_attempts < self.max_retries


class Sleeper:
    """可以被打断的等待。stop() 之后 sleep() 立即返回 False。"""

    def __init__(self, stop_event: threading.Event | None = None) -> None:
        self.stop_event = stop_event or threading.Event()
        self.waited: list[float] = []

    def sleep(self, seconds: float) -> bool:
        seconds = max(0.0, float(seconds))
        self.waited.append(seconds)
        if seconds == 0:
            return not self.stop_event.is_set()
        return not self.stop_event.wait(seconds)

    def stop(self) -> None:
        self.stop_event.set()

    @property
    def stopped(self) -> bool:
        return self.stop_event.is_set()


class FakeSleeper(Sleeper):
    """测试用：不真正等待，只记录请求的等待时长。"""

    def sleep(self, seconds: float) -> bool:
        self.waited.append(max(0.0, float(seconds)))
        return not self.stop_event.is_set()


def call_with_retries(
    action: Callable[[int], Any],
    policy: RetryPolicy,
    sleeper: Sleeper,
    should_retry: Callable[[Any], bool] | None = None,
    on_retry: Callable[[int, float, Any], None] | None = None,
):
    """通用重试执行器。返回 (最后一次结果, 尝试次数)。被 stop() 打断时立即返回。"""
    attempt = 0
    result: Any = None
    while True:
        attempt += 1
        try:
            result = action(attempt)
        except Exception as exc:
            result = exc
        retry = should_retry(result) if should_retry else True
        if not retry or not policy.can_retry(attempt):
            return result, attempt
        delay = policy.delay_for(attempt)
        if on_retry:
            on_retry(attempt, delay, result)
        if not sleeper.sleep(delay):
            return result, attempt
