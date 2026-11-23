#!/usr/bin/env python3
"""Tests for timing.py: a run's time by part, the fastest passing run of each scenario, and the
baseline the push level compares against.

Run: `python3 e2e/test_timing.py` (stdlib only).
"""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import timing  # noqa: E402


def run(folder, name, scenario, seconds, passed=True, reset=6.0):
    d = Path(folder) / name
    d.mkdir()
    (d / "timing.json").write_text(json.dumps({
        "scenario": scenario, "reset": reset, "start": 3.0, "passed": passed,
        "steps": [{"kind": "prompt", "seconds": seconds}, {"kind": "click", "seconds": 1.0},
                  {"kind": "panel", "seconds": 0.5}]}))


class TestTiming(unittest.TestCase):
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


def parts(total, expect=0.0):
    """A run's parts adding up to `total`, `expect` of it in the expect part."""
    return {**dict.fromkeys(timing.PARTS, 0.0), "prompt": total - expect, "expect": expect}


MAC = {"system": "Darwin", "arch": "arm64", "cores": 12, "node": "v1", "claude": "1 (Claude Code)"}


def baseline(**seconds):
    return {"recorded": "2026-01-01", "commit": "b" * 40, "machine": MAC, "jobs": 3,
            "wall_seconds": 100.0, "wall_scenarios": sorted(seconds),
            "scenarios": {n: {"seconds": t, **parts(t), "commit": "b" * 40}
                          for n, t in seconds.items()}}


class TestAgainstTheBaseline(unittest.TestCase):
    def kinds(self, base, ran, existing):
        return {k: [n for n, *_ in v] for k, v in timing.classified(base, ran, existing).items()}

    def test_each_scenario_run_is_slower_faster_same_or_new(self):
        base = baseline(slow=100.0, quick=100.0, steady=100.0, short=10.0)
        ran = {"slow": parts(125.0), "quick": parts(75.0), "steady": parts(115.0),
               "short": parts(12.5), "fresh": parts(30.0)}
        self.assertEqual(self.kinds(base, ran, list(ran)), {
            "slower": ["slow"], "faster": ["quick"], "same": ["steady", "short"],
            "new": ["fresh"], "gone": []})

    def test_a_short_scenarios_few_seconds_are_noise_however_large_a_share(self):
        # 25% of 10s is 2.5s: under the floor, so the same; 4s is past it.
        base = baseline(short=10.0)
        self.assertEqual(self.kinds(base, {"short": parts(12.5)}, ["short"])["same"], ["short"])
        self.assertEqual(self.kinds(base, {"short": parts(14.0)}, ["short"])["slower"], ["short"])

    def test_gone_is_a_scenario_that_no_longer_exists_not_one_that_did_not_run(self):
        base = baseline(kept=50.0, removed=50.0, other=50.0)
        kinds = self.kinds(base, {"kept": parts(50.0)}, ["kept", "other"])
        self.assertEqual(kinds["gone"], ["removed"])
        self.assertEqual(kinds["same"], ["kept"])
        self.assertNotIn("other", sum(kinds.values(), []))

    def test_the_table_names_what_moved_and_leaves_out_what_did_not_run(self):
        base = baseline(slow=100.0, other=40.0)
        run = {"scenarios": {"slow": parts(130.0, expect=30.0)}, "ran": ["slow"], "jobs": 3,
               "wall": 140.0}
        text = timing.comparison(base, run, ["slow", "other"], now=MAC)
        self.assertRegex(text, r"slower\s+slow\s+100\.0s\s+->\s+130\.0s\s+\+30%\s+mostly expect \+30\.0s")
        self.assertNotRegex(text, r"\bother\b")
        self.assertIn("1 scenario(s) in the baseline not run here", text)
        # A partial run's wall clock is not the baseline's quantity.
        self.assertIn("not compared: the baseline's 100s is for 2 scenario(s)", text)

    def test_the_wall_clock_is_compared_when_the_same_scenarios_ran_as_many_at_a_time(self):
        base = baseline(a=50.0, b=50.0)
        run = {"scenarios": {"a": parts(50.0), "b": parts(50.0)}, "ran": ["b", "a"], "jobs": 3,
               "wall": 90.0}
        self.assertIn("baseline 100s (-10%)", timing.comparison(base, run, ["a", "b"], now=MAC))
        run["jobs"] = 1
        self.assertIn("not compared", timing.comparison(base, run, ["a", "b"], now=MAC))

    def test_another_machine_is_said_and_no_baseline_is_said(self):
        base = baseline(a=50.0)
        run = {"scenarios": {"a": parts(50.0)}, "ran": ["a"], "jobs": 3, "wall": 50.0}
        self.assertIn("another machine (cores)",
                      timing.comparison(base, run, ["a"], now={**MAC, "cores": 8}))
        self.assertNotIn("another machine",
                         timing.comparison(base, run, ["a"], now={**MAC, "claude": "2"}))
        self.assertIn("no baseline yet", timing.comparison(None, run, ["a"], now=MAC))


class TestTheUpdate(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        for name, value in (("BASELINE", self.dir / "baseline.json"),
                            ("PUSH_RECORD", self.dir / "passed.json")):
            patcher = mock.patch.object(timing, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def record(self, *passes):
        timing.PUSH_RECORD.write_text(json.dumps({"passed": [
            {"tree": f"T{i}", "commit": c, "ran": {"timing": t} if t else {}}
            for i, (c, t) in enumerate(passes)]}))

    def update(self, existing, now=MAC, force=False):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return timing.update(force=force, existing=existing, now=now)

    def test_it_records_the_last_push_run_that_timed_scenarios(self):
        run = {"scenarios": {"a": parts(40.0), "b": parts(60.0)}, "ran": ["a", "b"], "jobs": 3,
               "wall": 70.0}
        self.record(("c1" * 20, run), ("c2" * 20, None))         # the newest ran no scenario
        self.assertEqual(self.update(["a", "b"]), 0)
        new = json.loads(timing.BASELINE.read_text())
        self.assertEqual((new["commit"], new["jobs"], new["wall_seconds"], new["wall_scenarios"]),
                         ("c1" * 20, 3, 70.0, ["a", "b"]))
        self.assertEqual(new["scenarios"]["b"]["seconds"], 60.0)
        self.assertEqual(set(new["scenarios"]["b"]) - {"seconds", "commit"}, set(timing.PARTS))

    def test_a_partial_run_keeps_the_entries_and_wall_clock_it_did_not_time(self):
        timing.BASELINE.write_text(json.dumps(baseline(a=40.0, b=60.0, removed=9.0)))
        run = {"scenarios": {"a": parts(45.0)}, "ran": ["a"], "jobs": 3, "wall": 50.0}
        self.record(("c1" * 20, run))
        self.assertEqual(self.update(["a", "b"]), 0)
        new = json.loads(timing.BASELINE.read_text())
        self.assertEqual({n: e["seconds"] for n, e in new["scenarios"].items()},
                         {"a": 45.0, "b": 60.0})
        self.assertEqual((new["wall_seconds"], new["scenarios"]["b"]["commit"]), (100.0, "b" * 40))

    def test_it_refuses_a_baseline_from_another_machine_without_force(self):
        timing.BASELINE.write_text(json.dumps(baseline(a=40.0)))
        self.record(("c1" * 20, {"scenarios": {"a": parts(10.0)}, "ran": ["a"], "jobs": 3,
                                 "wall": 10.0}))
        self.assertEqual(self.update(["a"], now={**MAC, "arch": "x86_64"}), 2)
        self.assertEqual(json.loads(timing.BASELINE.read_text())["scenarios"]["a"]["seconds"], 40.0)
        self.assertEqual(self.update(["a"], now={**MAC, "arch": "x86_64"}, force=True), 0)
        self.assertEqual(json.loads(timing.BASELINE.read_text())["scenarios"]["a"]["seconds"], 10.0)

    def test_nothing_to_record_from_is_an_error_and_writes_nothing(self):
        self.record(("c1" * 20, None))
        self.assertEqual(self.update(["a"]), 1)
        self.assertFalse(timing.BASELINE.exists())


if __name__ == "__main__":
    unittest.main()
