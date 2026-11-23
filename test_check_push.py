#!/usr/bin/env python3
"""Tests for check_push.py: which scenarios a change reaches, and the pass record the pre-push
hook reads. The picker's one failure mode that matters is a scenario skipped that should have
run, so most cases here are about what must be picked.

Run: `python3 test_check_push.py` (no scenario runs, no push).
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
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
        # Every scenario playing it: the end-of-MR ones replay rework-fix's recording too.
        self.assertEqual(picks("e2e/recordings/rework-fix.json"),
                         ["rework-fix", "rework-pipeline", "rework-refused"])

    def test_a_scenario_that_is_the_start_of_another_is_left_to_the_whole_one(self):
        for part, whole in (("try-pushed-fix", "rework-fix"), ("try-pushed-twice", "rework-twice")):
            with self.subTest(part=part):
                self.assertNotIn(part, NAMES)
                self.assertIn(whole, NAMES)
        self.assertIn("try-scrolling", NAMES, "a try scenario that is nobody's start")

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
        for path in ("e2e/screenshot.py", "e2e/timing.py", "e2e/bootstrap.py", "e2e/tmux-demo.sh"):
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

    def test_a_scenario_that_passed_is_not_run_again_for_the_same_tree(self):
        cp.record_scenario("T-abc", "smoke")
        cp.record_scenario("T-abc", "rework-3")
        self.assertEqual(cp.scenarios_passed("T-abc"), {"smoke", "rework-3"})
        # Another tree is other code: nothing carries over, and its note replaces the old.
        self.assertEqual(cp.scenarios_passed("T-def"), set())
        cp.record_scenario("T-def", "smoke")
        self.assertEqual(cp.scenarios_passed("T-abc"), set())

    def test_a_scenarios_pass_alone_lets_no_push_through(self):
        with mock.patch.object(cp, "tree_of", lambda s: "T-abc"):
            cp.record_scenario("T-abc", "smoke")
            self.assertEqual(cp.verify(["abc"]), 1)
            cp.record_pass("T-abc", "abc", {})
            self.assertEqual(cp.verify(["abc"]), 0)
            self.assertEqual(cp.scenarios_passed("T-abc"), set())      # the whole pass replaces it

    def test_a_pass_that_ran_no_scenarios_is_no_starting_point_for_the_next(self):
        """Picked from it, the next run skipped the scenarios of what changed before it."""
        with mock.patch.object(cp, "git", lambda *a: "tree"):
            cp.record_pass("T-one", "c1" * 6, {"mode": "auto"})
            cp.record_pass("T-two", "c2" * 6, {"mode": "none"})
            self.assertEqual(cp.starting_point("origin/main")[0], "T-one")
            cp.record_pass("T-three", "c3" * 6, {})       # a record from before modes: a full pass
            self.assertEqual(cp.starting_point("origin/main")[0], "T-three")

    def test_a_tree_that_only_adds_the_scenario_times_to_a_passed_one_has_passed(self):
        """Folding the times re-recorded from a pass into its commit changes no code; any
        other file changed with it, or alone, does."""
        repo = Path(cp.RECORD).parent / "repo"
        (repo / "e2e").mkdir(parents=True)

        def tree(**files):
            for name, text in files.items():
                (repo / name).write_text(text)
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
            return subprocess.run(["git", "write-tree"], cwd=repo, check=True,
                                  capture_output=True, text=True).stdout.strip()

        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        passed = tree(**{"code.py": "a", "e2e/timing_baseline.json": "{}"})
        times = tree(**{"e2e/timing_baseline.json": '{"new": 1}'})
        times_and_code = tree(**{"code.py": "b"})
        tree(**{"e2e/timing_baseline.json": "{}"})
        code = tree()                                   # code.py "b" alone against the pass
        with mock.patch.object(cp, "HERE", repo):
            cp.record_pass(passed, "c1" * 20, {})
            self.assertTrue(cp.has_passed(passed))
            self.assertTrue(cp.has_passed(times))
            self.assertFalse(cp.has_passed(times_and_code))
            self.assertFalse(cp.has_passed(code))
            with mock.patch.object(cp, "tree_of", {"s1": times, "s2": code}.get), \
                    redirect_stderr(io.StringIO()):
                self.assertEqual(cp.verify(["s1"]), 0)
                self.assertEqual(cp.verify(["s2"]), 1)

    def test_deleting_a_branch_needs_no_pass(self):
        self.assertEqual(cp.verify(["0" * 40]), 0)

    def _main_while_the_checkout_moves(self, argv, *, moved_after):
        """`main` with every check passing, where `dirty` turns true after `moved_after` asks:
        another session editing the checkout while the checks run."""
        asked = []

        def dirty():
            asked.append(1)
            return " M lib/x.py" if len(asked) > moved_after else ""

        git = {("rev-parse", "HEAD"): "c" * 40,
               ("rev-parse", "--abbrev-ref", "@{u}"): "origin/main"}
        done = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(cp, "dirty", dirty), \
                mock.patch.object(cp, "tree_of", lambda rev: "T-start"), \
                mock.patch.object(cp, "git", lambda *a: git.get(a, "")), \
                mock.patch.object(cp, "scenarios", lambda: [("smoke", set(), [], None)]), \
                mock.patch.object(cp, "run", lambda step, argv: True), \
                mock.patch.object(cp.subprocess, "run", return_value=done), \
                redirect_stdout(io.StringIO()):
            return cp.main(argv)

    def test_nothing_is_recorded_when_the_checkout_changed_during_the_run(self):
        """What passed was the edited working tree, not the tree read at the start; recording
        it let the pre-push hook through for a tree whose checks never ran."""
        self.assertEqual(self._main_while_the_checkout_moves(["--scenarios", "none"],
                                                             moved_after=1), 1)
        self.assertEqual(cp.passed_trees(), set())
        self.assertEqual(self._main_while_the_checkout_moves(["--scenarios", "none"],
                                                             moved_after=99), 0)
        self.assertEqual(cp.passed_trees(), {"T-start"})

    def test_again_runs_a_tree_that_passed_and_every_scenario_it_noted(self):
        """Taken anew, a time baseline needs every scenario timed on this run: one skipped as
        passed before has no time of its own here."""
        cp.record_pass("T-start", "c" * 40, {})
        cp.record_scenario("T-start", "smoke")
        with mock.patch.object(cp.parallel, "each", side_effect=lambda f, xs, workers: [f(x) for x in xs]) as each:
            self._main_while_the_checkout_moves(["--scenarios", "all"], moved_after=99)
            self.assertFalse(each.called)
            self._main_while_the_checkout_moves(["--scenarios", "all", "--again"], moved_after=99)
            self.assertEqual(each.call_args[0][1], ["smoke"])

    def test_a_scenario_is_not_noted_when_the_checkout_changed_before_it_passed(self):
        self._main_while_the_checkout_moves(["--scenarios", "all", "--jobs", "1"], moved_after=1)
        self.assertEqual(cp.scenarios_passed("T-start"), set())

    def test_a_run_compares_and_records_the_times_of_only_the_scenarios_it_ran(self):
        """One noted as passed before is not run, so it has no time here: comparing it, or
        recording it, would pass off the baseline's number as this run's."""
        home = Path(cp.RECORD).parent
        baseline = home / "timing_baseline.json"
        entry = {"seconds": 30.0, **dict.fromkeys(cp.timing.PARTS, 0.0), "prompt": 30.0}
        baseline.write_text(json.dumps({
            "recorded": "2026-01-01", "commit": "b" * 40, "machine": {}, "jobs": 3,
            "wall_seconds": 60.0, "wall_scenarios": ["review-1", "smoke"],
            "scenarios": {"smoke": entry, "review-1": entry}}))

        def ran(argv, **_):
            if "e2e/drive.py" in argv:
                folder = Path(argv[argv.index("--out") + 1]) / argv[argv.index("verify") + 1]
                folder.mkdir(parents=True)
                (folder / "timing.json").write_text(json.dumps({
                    "scenario": folder.name, "reset": 5.0, "start": 3.0, "passed": True,
                    "steps": [{"kind": "expect", "seconds": 40.0}]}))
            return mock.Mock(returncode=0, stdout="", stderr="")

        git = {("rev-parse", "HEAD"): "c" * 40,
               ("rev-parse", "--abbrev-ref", "@{u}"): "origin/main"}
        cp.record_scenario("T-start", "smoke")
        out = io.StringIO()
        with mock.patch.object(cp, "HERE", home), \
                mock.patch.object(cp.timing, "BASELINE", baseline), \
                mock.patch.object(cp.timing, "machine", lambda: {}), \
                mock.patch.object(cp, "dirty", lambda: ""), \
                mock.patch.object(cp, "tree_of", lambda rev: "T-start"), \
                mock.patch.object(cp, "git", lambda *a: git.get(a, "")), \
                mock.patch.object(cp, "scenarios", lambda: [("smoke", set(), [], None),
                                                            ("review-1", set(), [], None)]), \
                mock.patch.object(cp, "run", lambda step, argv: True), \
                mock.patch.object(cp.subprocess, "run", side_effect=ran), \
                redirect_stdout(out):
            self.assertEqual(cp.main(["--scenarios", "all"]), 0)
        took = json.loads(cp.RECORD.read_text())["passed"][-1]["ran"]["timing"]
        self.assertEqual((list(took["scenarios"]), took["ran"]), (["review-1"], ["review-1"]))
        self.assertEqual(took["scenarios"]["review-1"]["expect"], 40.0)
        self.assertRegex(out.getvalue(), r"slower\s+review-1\s+30\.0s\s+->\s+48\.0s")
        self.assertNotRegex(out.getvalue(), r"(?:slower|faster|new)\s+smoke")
        self.assertIn("1 scenario(s) in the baseline not run here", out.getvalue())

    def test_the_record_keeps_the_newest_passes(self):
        for i in range(cp.KEEP + 5):
            cp.record_pass(f"T{i}", f"c{i}", {})
        trees = [p["tree"] for p in json.loads(cp.RECORD.read_text())["passed"]]
        self.assertEqual(len(trees), cp.KEEP)
        self.assertEqual(trees[-1], f"T{cp.KEEP + 4}")


if __name__ == "__main__":
    unittest.main()
