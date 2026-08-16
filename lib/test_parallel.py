#!/usr/bin/env python3
"""Tests for the shared fan-out helper.

The contract worth pinning is ORDER: every caller zips results back against the inputs that
produced them, so a helper that returned them as they finished would silently pair a
measurement with the wrong candidate — and on a fast machine most of the time it would not
even look wrong.

Run: `python3 lib/test_parallel.py`
"""
import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib import parallel


class TestEach(unittest.TestCase):
    def test_results_come_back_in_the_order_given(self):
        """Deliberately inverted durations: finishing order is the reverse of input order."""
        def slow_first(n):
            time.sleep((10 - n) / 200)
            return n

        self.assertEqual(parallel.each(slow_first, range(10)), list(range(10)))

    def test_the_work_really_does_overlap(self):
        """Otherwise this is an expensive `map`. Ten sleeps of 100ms in under half a second
        can only happen concurrently."""
        started = time.monotonic()
        parallel.each(lambda _: time.sleep(0.1), range(10))
        self.assertLess(time.monotonic() - started, 0.5)

    def test_one_item_is_not_worth_a_pool(self):
        """The single-callout figure is the common case, and a thread and a queue to run one
        d2 compile is pure overhead. Checked by running on the calling thread."""
        seen = []
        parallel.each(lambda _: seen.append(threading.current_thread()), ["only"])
        self.assertEqual(seen, [threading.current_thread()])

    def test_no_items_is_no_work(self):
        self.assertEqual(parallel.each(lambda _: 1 / 0, []), [])

    def test_an_exception_reaches_the_caller(self):
        """A worker that raises must not be swallowed into a shorter result list — a caller
        zipping against its inputs would then pair everything after it with the wrong item."""
        def boom(n):
            if n == 3:
                raise ValueError("boom")
            return n

        with self.assertRaises(ValueError):
            parallel.each(boom, range(6))

    def test_the_pool_is_never_wider_than_the_work(self):
        parallel.each(lambda n: n, range(2), workers=64)   # would raise if 0/negative

    def test_workers_is_at_least_one(self):
        self.assertGreaterEqual(parallel.WORKERS, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
