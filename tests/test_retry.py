"""重试机制测试。"""

from __future__ import annotations

import unittest

from campus_login.config import Config
from campus_login.retry import FakeSleeper, RetryPolicy, Sleeper, call_with_retries

from .support import TempDataDirTestCase


class RetryPolicyTest(unittest.TestCase):
    def test_schedule_progression(self):
        policy = RetryPolicy(schedule=[5, 10, 20], interval=60, max_retries=6)
        self.assertEqual(policy.delay_for(1), 5)
        self.assertEqual(policy.delay_for(2), 10)
        self.assertEqual(policy.delay_for(3), 20)
        self.assertEqual(policy.delay_for(4), 60)
        self.assertEqual(policy.delay_for(99), 60)

    def test_can_retry(self):
        policy = RetryPolicy(max_retries=3)
        self.assertTrue(policy.can_retry(1))
        self.assertTrue(policy.can_retry(2))
        self.assertFalse(policy.can_retry(3))

    def test_from_config(self):
        config = Config()
        config.retry_schedule_seconds = [1, 2, 3]
        config.retry_interval_seconds = 45
        config.max_retries = 4
        policy = RetryPolicy.from_config(config)
        self.assertEqual(policy.schedule, [1.0, 2.0, 3.0])
        self.assertEqual(policy.interval, 45)
        self.assertEqual(policy.max_retries, 4)


class SleeperTest(unittest.TestCase):
    def test_sleeper_records_and_stops(self):
        sleeper = Sleeper()
        self.assertTrue(sleeper.sleep(0))
        sleeper.stop()
        self.assertFalse(sleeper.sleep(5))
        self.assertTrue(sleeper.stopped)

    def test_fake_sleeper_does_not_wait(self):
        sleeper = FakeSleeper()
        self.assertTrue(sleeper.sleep(10))
        self.assertEqual(sleeper.waited, [10.0])


class CallWithRetriesTest(unittest.TestCase):
    def test_retries_until_success(self):
        sleeper = FakeSleeper()
        attempts = {"n": 0}

        def action(attempt):
            attempts["n"] = attempt
            return attempt >= 3

        result, count = call_with_retries(
            action,
            RetryPolicy(schedule=[1, 2], interval=5, max_retries=5),
            sleeper,
            should_retry=lambda outcome: not outcome,
        )
        self.assertTrue(result)
        self.assertEqual(count, 3)
        self.assertEqual(sleeper.waited, [1.0, 2.0])

    def test_stops_at_max_retries(self):
        sleeper = FakeSleeper()
        result, count = call_with_retries(
            lambda attempt: False, RetryPolicy(schedule=[1, 2], interval=5, max_retries=3), sleeper
        )
        self.assertFalse(result)
        self.assertEqual(count, 3)

    def test_exception_becomes_result(self):
        sleeper = FakeSleeper()

        def boom(attempt):
            raise RuntimeError("x")

        result, count = call_with_retries(
            boom, RetryPolicy(schedule=[1], interval=1, max_retries=2), sleeper
        )
        self.assertIsInstance(result, RuntimeError)
        self.assertEqual(count, 2)

    def test_interrupted_sleep_stops_retrying(self):
        sleeper = FakeSleeper()
        sleeper.stop()
        result, count = call_with_retries(
            lambda attempt: False, RetryPolicy(schedule=[1], interval=1, max_retries=5), sleeper
        )
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
