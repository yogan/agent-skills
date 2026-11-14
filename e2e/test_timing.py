#!/usr/bin/env python3
"""Tests for timing.py: a run's time by part, and the fastest passing run of each scenario.

Run: `python3 e2e/test_timing.py` (stdlib only).
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import timing  # noqa: E402


def run(folder, name, scenario, seconds, passed=True, reset=6.0):
    d = Path(folder) / name
    d.mkdir()
    (d / "timing.json").write_text(json.dumps({
        "scenario": scenario, "reset": reset, "start": 3.0, "passed": passed,
        "steps": [{"kind": "prompt", "seconds": seconds}, {"kind": "click", "seconds": 1.0},
                  {"kind": "panel", "seconds": 0.5}]}))


class TimingTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name

    def test_a_runs_seconds_are_added_up_by_part(self):
        run(self.dir, "a", "rework-3", 9.0)
        parts = timing.fastest(self.dir)["rework-3"]
        self.assertEqual((parts["reset"], parts["start"], parts["prompt"], parts["click"], parts["other"]),
                         (6.0, 3.0, 9.0, 1.0, 0.5))

    def test_the_fastest_run_of_a_scenario_stands_for_it(self):
        run(self.dir, "a", "rework-3", 12.0)
        run(self.dir, "b", "rework-3", 9.0)
        self.assertEqual(timing.fastest(self.dir)["rework-3"]["prompt"], 9.0)

    def test_a_failed_run_never_stands_for_its_scenario(self):
        # It stopped early: fastest by not having run.
        run(self.dir, "a", "rework-3", 9.0)
        run(self.dir, "b", "rework-3", 1.0, passed=False)
        self.assertEqual(timing.fastest(self.dir)["rework-3"]["prompt"], 9.0)

    def test_two_folders_are_compared_over_the_scenarios_both_ran(self):
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        run(self.dir, "a", "rework-3", 9.0, reset=30.0)
        run(self.dir, "b", "smoke", 9.0)
        run(other.name, "a", "rework-3", 9.0, reset=6.0)
        text = timing.compared(timing.fastest(self.dir), timing.fastest(other.name))
        self.assertIn("1 scenario(s) in both: rework-3", text)
        self.assertRegex(text, r"reset\s+30\.0\s+6\.0\s+-24\.0\s+\(-80%\)")


if __name__ == "__main__":
    unittest.main()
