#!/usr/bin/env python3
"""Tests for what fixture.py removes on this machine when it makes one merge request again.

Run: `python3 e2e/test_fixture.py` (no GitLab, no docker: nothing is called).
"""
import os
import subprocess
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


class CopyTest(unittest.TestCase):
    """E2E_PROJECT and E2E_SRC build a copy of the rig, beside the demo's and apart from it."""

    def paths(self, **env):
        code = "import fixture as f; print(f.WORKDIR); print(f.MR4_DIR); print(f.BUILD)"
        clean = {k: v for k, v in os.environ.items() if k not in ("E2E_PROJECT", "E2E_SRC")}
        r = subprocess.run([sys.executable, "-c", code], cwd=os.path.dirname(os.path.abspath(__file__)),
                           env={**clean, **env}, capture_output=True, text=True, check=True)
        return r.stdout.split("\n")[:3]

    def test_the_demos_rig_is_where_it_always_was(self):
        work, scroll, build = self.paths()
        self.assertEqual(work, os.path.expanduser("~/src/agent-skills-demo"))
        self.assertEqual(scroll, os.path.expanduser("~/src/agent-skills-demo-scroll"))
        self.assertTrue(build.endswith("/.cache/build"))

    def test_a_copy_has_its_own_checkouts_and_its_own_folder_to_build_in(self):
        work, scroll, build = self.paths(E2E_PROJECT="demo/e2e-rework-fix", E2E_SRC="~/src/copies/rework-fix")
        self.assertEqual(work, os.path.expanduser("~/src/copies/rework-fix/agent-skills-demo"))
        self.assertEqual(scroll, work + "-scroll")
        self.assertTrue(build.endswith("/.cache/build-demo-e2e-rework-fix"))

    def test_a_copys_project_replaces_the_one_the_rig_was_set_up_with(self):
        with tempfile.NamedTemporaryFile("w", suffix=".env") as f:
            f.write("E2E_PROJECT=demo/bulletproof-react\nE2E_GITLAB_HOST=gitlab.test\n")
            f.flush()
            with mock.patch.object(fixture, "ENV_FILE", f.name):
                self.assertEqual(fixture.env()["E2E_PROJECT"], "demo/bulletproof-react")
                with mock.patch.dict(os.environ, {"E2E_PROJECT": "demo/e2e-rework-fix"}):
                    got = fixture.env()
        self.assertEqual((got["E2E_PROJECT"], got["E2E_GITLAB_HOST"]), ("demo/e2e-rework-fix", "gitlab.test"))


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
