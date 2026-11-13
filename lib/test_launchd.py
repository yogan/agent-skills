#!/usr/bin/env python3
"""Tests for lib/launchd.py: the job it writes and the use stamp. Nothing is installed.

Run: `python3 lib/test_launchd.py`
"""
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib import launchd  # noqa: E402


class TestJob(unittest.TestCase):
    def test_the_job_runs_this_python_on_the_argv_every_interval(self):
        job = launchd.plist("x.y", ["/a/b.py", "idle"], 300, "/tmp/x.log", ("sh",))
        self.assertEqual(job["ProgramArguments"], [sys.executable, "/a/b.py", "idle"])
        self.assertEqual(job["StartInterval"], 300)
        self.assertIn("/usr/bin", job["EnvironmentVariables"]["PATH"].split(":"))


class TestStamp(unittest.TestCase):
    def test_unused_is_since_the_last_use_and_forever_before_any(self):
        with tempfile.TemporaryDirectory() as tmp:
            stamp = os.path.join(tmp, "sub", "stamp")
            self.assertEqual(launchd.unused_for(stamp), float("inf"))
            launchd.note_use(stamp)
            self.assertLess(launchd.unused_for(stamp), 5)
            old = time.time() - 3600
            os.utime(stamp, (old, old))
            self.assertGreater(launchd.unused_for(stamp), 3500)


if __name__ == "__main__":
    unittest.main()
