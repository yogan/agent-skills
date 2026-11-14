#!/usr/bin/env python3
"""Tests for what fixture.py removes on this machine when it makes one merge request again.

Run: `python3 e2e/test_fixture.py` (no GitLab, no docker: nothing is called).
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fixture  # noqa: E402

SLUG = "demo-bulletproof-react"


class WipeTest(unittest.TestCase):
    def setUp(self):
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        self.home = home.name
        for patch in (mock.patch.dict(os.environ, {"HOME": self.home}),
                      mock.patch.object(fixture, "E", {"E2E_PROJECT": "demo/bulletproof-react"}),
                      mock.patch.object(fixture, "step"), mock.patch.object(fixture, "info")):
            patch.start()
            self.addCleanup(patch.stop)
        for d in (f"review-mr/{SLUG}", f"review-mr/{SLUG}--mr1", f"review-mr/{SLUG}--mr2",
                  f"review-mr/{SLUG}--mr12", "review-mr/someone-else--mr1",
                  f"rework-mr/{SLUG}--mr3", f"rework-mr/{SLUG}--mr1"):
            os.makedirs(os.path.join(self.home, ".claude", d))

    def left(self):
        base = os.path.join(self.home, ".claude")
        return sorted(f"{root}/{d}" for root in os.listdir(base) for d in os.listdir(os.path.join(base, root)))

    def test_one_merge_requests_state_goes_and_nothing_else(self):
        fixture.wipe_skill_state(1)
        self.assertEqual(self.left(), [
            f"review-mr/{SLUG}", f"review-mr/{SLUG}--mr12", f"review-mr/{SLUG}--mr2",
            "review-mr/someone-else--mr1", f"rework-mr/{SLUG}--mr3"])

    def test_a_full_reset_takes_the_projects_state_and_every_merge_requests(self):
        fixture.wipe_skill_state()
        self.assertEqual(self.left(), ["review-mr/someone-else--mr1"])


class CounterTest(unittest.TestCase):
    def test_the_next_merge_request_gets_the_number_asked_for(self):
        with mock.patch.object(fixture, "psql", return_value="2\nUPDATE 1") as psql:
            fixture.next_mr_number(513, 3)
        self.assertIn("set last_value = 2 where project_id = 513 and usage = 1", psql.call_args.args[0])

    def test_a_counter_that_was_not_set_stops_the_reset(self):
        # No such row: the merge request made next would get a number of GitLab's choosing.
        with mock.patch.object(fixture, "psql", return_value="UPDATE 0"), self.assertRaises(SystemExit):
            fixture.next_mr_number(513, 3)


if __name__ == "__main__":
    unittest.main()
