#!/usr/bin/env python3
"""Tests for measure_checks.py: the order the sides run in, and what its summary says.

Run: `python3 test_measure_checks.py` (no subprocess: `run` is replaced).
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import measure_checks as mc  # noqa: E402


def sample(seconds, counts=(10,), rc=0):
    return {"seconds": seconds, "rc": rc, "counts": list(counts)}


class TestOrder(unittest.TestCase):
    def test_each_side_warms_up_once_then_they_take_turns_and_swap_who_goes_first(self):
        ran = []

        def fake(argv, cwd, count):
            ran.append(cwd)
            return sample(1.0)
        with mock.patch.object(mc, "run", side_effect=fake):
            kept = mc.measure("B", "A", ["x"], [], 3, None, log=lambda *_: None)
        self.assertEqual(ran, ["B", "A",                  # warm-ups, thrown away
                               "B", "A", "A", "B", "B", "A"])
        self.assertEqual([len(kept["before"]), len(kept["after"])], [3, 3])

    def test_the_after_arguments_reach_the_after_side_only(self):
        seen = {}

        def fake(argv, cwd, count):
            seen[cwd] = argv
            return sample(1.0)
        with mock.patch.object(mc, "run", side_effect=fake):
            mc.measure("B", "A", ["x"], ["-j12"], 1, None, log=lambda *_: None)
        self.assertEqual(seen, {"B": ["x"], "A": ["x", "-j12"]})


class TestSummary(unittest.TestCase):
    def test_best_of_n_not_the_mean(self):
        lines = mc.summary({"before": [sample(9), sample(5), sample(7)],
                            "after": [sample(4), sample(3), sample(8)]}, None)
        self.assertIn("before: best 5.0s, median 7.0s", lines)
        self.assertIn("after:  best 3.0s, median 4.0s", lines)
        self.assertIn("after/before, best of 3: 0.60", lines)

    def test_a_side_that_checks_something_else_is_called_out(self):
        lines = mc.summary({"before": [sample(5, (10,))], "after": [sample(3, (9,))]}, "x")
        self.assertTrue(any("DIFFERENT" in ln for ln in lines))
        same = mc.summary({"before": [sample(5, (10,))], "after": [sample(3, (10,))]}, "x")
        self.assertFalse(any("DIFFERENT" in ln for ln in same))

    def test_a_failed_run_is_called_out(self):
        lines = mc.summary({"before": [sample(5)], "after": [sample(3, rc=1)]}, None)
        self.assertTrue(any("failed on: after" in ln for ln in lines))


if __name__ == "__main__":
    unittest.main()
