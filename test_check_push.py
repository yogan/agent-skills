#!/usr/bin/env python3
"""Tests for check_push.py: which scenarios a change reaches, and the pass record the pre-push
hook reads. The picker's one failure mode that matters is a scenario skipped that should have
run, so most cases here are about what must be picked.

Run: `python3 test_check_push.py` (no scenario runs, no push).
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import check_push as cp  # noqa: E402

SCENS = cp.scenarios()
REACH = cp.skill_modules()
NAMES = [n for n, _, _, _ in SCENS]
REVIEW = [n for n, typed, _, _ in SCENS if "review-mr" in typed]
REWORK = [n for n, typed, _, _ in SCENS if "rework-mr" in typed]
WITH_MOD = [n for n, _, mods, _ in SCENS if mods]


def picks(*paths):
    return cp.reached(list(paths), SCENS, REACH)


class TestWhatMustRun(unittest.TestCase):
    def test_the_scenarios_are_the_verified_ones_and_each_skill_has_some(self):
        self.assertIn("smoke", NAMES)
        self.assertFalse(any(n.startswith("stage-") for n in NAMES), "showcase-only ones")
        self.assertTrue(REVIEW and REWORK)

    def test_a_skill_reaches_the_scenarios_that_type_it(self):
        self.assertEqual(picks("skills/review-mr/scripts/findings.py"), REVIEW)
        self.assertEqual(picks("skills/rework-mr/SKILL.md"), REWORK)

    def test_a_skill_review_mr_hands_work_to_reaches_its_scenarios(self):
        self.assertEqual(picks("skills/explain-branch/SKILL.md"), REVIEW)

    def test_a_lib_module_reaches_the_scenarios_of_every_skill_importing_it(self):
        both = picks("lib/gitlab.py")
        self.assertTrue(set(REVIEW) <= set(both) and set(REWORK) <= set(both))

    def test_the_mod_reaches_every_scenario_that_loads_it(self):
        self.assertEqual(picks("mods/review-ui/hooks/panel.ts"), WITH_MOD)

    def test_the_harness_reaches_everything(self):
        for path in ("e2e/drive.py", "e2e/fixture.py", "e2e/patches/main/0001-x.patch",
                     "hooks/paste-gate.py"):
            with self.subTest(path=path):
                self.assertEqual(picks(path), NAMES)

    def test_a_recording_reaches_the_scenarios_playing_it(self):
        self.assertEqual(picks("e2e/recordings/smoke.json"), ["smoke"])
        shared = picks("e2e/recordings/rework-fix.json")
        self.assertIn("rework-fix", shared)
        self.assertIn("try-pushed-fix", shared)

    def test_anything_it_cannot_place_reaches_everything(self):
        self.assertEqual(picks("somewhere/new.txt"), NAMES)
        self.assertEqual(picks("README.md", "somewhere/new.txt"), NAMES)


class TestWhatNeedNotRun(unittest.TestCase):
    def test_documents_and_the_repos_own_tooling_reach_nothing(self):
        for path in ("README.md", "e2e/RUNBOOK.md", "CONTEXT.md", "run_tests.py",
                     "check_commits.py", ".githooks/pre-commit", "lib/test_gitlab.py",
                     "docs/review-skills-dark.png"):
            with self.subTest(path=path):
                self.assertEqual(picks(path), [])

    def test_the_rig_s_parts_a_verify_never_runs_reach_nothing(self):
        for path in ("e2e/screenshot.py", "e2e/bootstrap.py", "e2e/tmux-demo.sh"):
            with self.subTest(path=path):
                self.assertEqual(picks(path), [])
        everything = [n for n, *_ in SCENS]
        for path in ("e2e/drive.py", "e2e/rig.py", "e2e/docker-compose.yml"):   # what a verify does run
            with self.subTest(path=path):
                self.assertEqual(picks(path), everything)

    def test_skills_no_scenario_runs_and_code_only_they_use_reach_nothing(self):
        self.assertEqual(picks("skills/visualize/scripts/visualize.py"), [])
        self.assertEqual(picks("lib/diagram/palette.py"), [])


class TestTheRecord(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.object(cp, "RECORD", Path(tmp.name) / "passed.json")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_push_is_let_through_only_for_a_tree_that_passed(self):
        with mock.patch.object(cp, "tree_of", side_effect=lambda s: "T-" + s):
            self.assertEqual(cp.verify(["abc"]), 1)
            cp.record_pass("T-abc", "abc", {})
            self.assertEqual(cp.verify(["abc"]), 0)
            self.assertEqual(cp.verify(["abc", "def"]), 1)

    def test_deleting_a_branch_needs_no_pass(self):
        self.assertEqual(cp.verify(["0" * 40]), 0)

    def test_the_record_keeps_the_newest_passes(self):
        for i in range(cp.KEEP + 5):
            cp.record_pass(f"T{i}", f"c{i}", {})
        trees = [p["tree"] for p in json.loads(cp.RECORD.read_text())["passed"]]
        self.assertEqual(len(trees), cp.KEEP)
        self.assertEqual(trees[-1], f"T{cp.KEEP + 4}")


if __name__ == "__main__":
    unittest.main()
