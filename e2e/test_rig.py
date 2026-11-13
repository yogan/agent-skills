#!/usr/bin/env python3
"""Tests for rig.py: when the idle check pauses or stops GitLab, and the launchd job it installs.

Run: `python3 e2e/test_rig.py` (no docker, no launchctl: nothing is called).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import rig  # noqa: E402

HOUR = 3600


class TestIdleAction(unittest.TestCase):
    def test_recently_used_is_left_alone(self):
        self.assertIsNone(rig.idle_action(0.5 * HOUR, "running", busy=False))

    def test_an_hour_unused_pauses_it(self):
        self.assertEqual(rig.idle_action(1 * HOUR, "running", busy=False), "pause")

    def test_paused_stays_paused_until_the_night_is_over_then_stops(self):
        self.assertIsNone(rig.idle_action(3 * HOUR, "paused", busy=False))
        self.assertEqual(rig.idle_action(8 * HOUR, "paused", busy=False), "stop")
        self.assertEqual(rig.idle_action(8 * HOUR, "running", busy=False), "stop")

    def test_an_open_demo_or_driver_session_keeps_it_running(self):
        self.assertIsNone(rig.idle_action(20 * HOUR, "running", busy=True))

    def test_a_stopped_or_missing_container_is_not_touched(self):
        for status in ("exited", "created", None):
            self.assertIsNone(rig.idle_action(20 * HOUR, status, busy=False))

    def test_never_used_counts_as_long_unused(self):
        self.assertEqual(rig.idle_action(float("inf"), "running", busy=False), "stop")


class TestIdleCheckJob(unittest.TestCase):
    def test_the_launchd_job_runs_the_idle_check_of_this_file_with_a_path_to_docker(self):
        p = rig.plist()
        self.assertEqual(p["ProgramArguments"][1:], [os.path.abspath(rig.__file__), "idle"])
        self.assertEqual(p["StartInterval"], rig.CHECK_EVERY)
        self.assertIn("/usr/bin", p["EnvironmentVariables"]["PATH"].split(":"))


if __name__ == "__main__":
    unittest.main()
