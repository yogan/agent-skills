#!/usr/bin/env python3
"""The test runner picks which tests run, so its selection logic needs testing itself.

Everything here is about ONE failure mode: the suite that should have run and didn't. Most
of it is under-SELECTION — a `--changed` run that misses the test which would have caught
your bug is worse than no `--changed` at all, because it reports success. `TestFingerprint`
is the same failure one level up: the whole run skipped as "nothing has changed" when
something had. Over-selection only costs seconds.

Run: `python3 test_run_tests.py`
"""
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_spec = importlib.util.spec_from_file_location(
    "run_tests", Path(__file__).with_name("run_tests.py"))
rt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rt)

EVERY = {p for p in rt.REPO_ROOT.rglob("*.py") if "__pycache__" not in p.parts}


class TestSelectionCoversTheObviousCase(unittest.TestCase):
    def test_a_module_always_selects_its_own_test_file(self):
        """The floor. If `foo.py` changes and `test_foo.py` does not run, the feature is
        actively harmful."""
        missed = []
        for src in sorted(EVERY):
            if src.name.startswith("test_"):
                continue
            own = src.with_name("test_" + src.name)
            if own.exists() and own not in rt._dependents({src}, EVERY):
                missed.append(str(src.relative_to(rt.REPO_ROOT)))
        self.assertEqual(missed, [])

    def test_a_hyphenated_script_selects_the_test_that_covers_it(self):
        """`hooks/paste-gate.py` cannot be imported by name, so its test loads it through
        importlib and no import edge exists. It selected ZERO tests until the naming
        convention itself was made an edge."""
        for script, expected in (("hooks/paste-gate.py", "hooks/test_paste_gate.py"),
                                 ("skills/explain-diff/scripts/resolve-target.py",
                                  "skills/explain-diff/scripts/test_resolve_target.py")):
            with self.subTest(script=script):
                picked = rt._dependents({rt.REPO_ROOT / script}, EVERY)
                self.assertIn(rt.REPO_ROOT / expected, picked)

    def test_a_packages_init_selects_the_tests_that_import_the_package(self):
        """`from lib.diagram.gates import GateError` resolves to gates/__init__.py, but the
        dotted name indexed for that file was `lib.diagram.gates.__init__`, so editing it
        selected nothing."""
        picked = rt._dependents({rt.REPO_ROOT / "lib/diagram/gates/__init__.py"}, EVERY)
        self.assertIn(rt.REPO_ROOT / "lib/diagram/gates/test_gates.py", picked)

    def test_relative_imports_are_followed(self):
        """`from . import palette` parses with module=None. Skipping those dropped nearly
        every edge inside lib/diagram, and palette.py selected 6 tests instead of 15."""
        picked = rt._dependents({rt.REPO_ROOT / "lib/diagram/palette.py"}, EVERY)
        for expected in ("lib/diagram/test_d2.py", "lib/diagram/test_render.py",
                         "lib/diagram/test_reference.py", "lib/diagram/test_compact.py"):
            self.assertIn(rt.REPO_ROOT / expected, picked, expected)

    def test_a_change_reaches_tests_transitively_not_just_direct_importers(self):
        """test_visualize.py never imports palette; it imports render, which imports it."""
        picked = rt._dependents({rt.REPO_ROOT / "lib/diagram/palette.py"}, EVERY)
        self.assertIn(rt.REPO_ROOT / "skills/visualize/scripts/test_visualize.py", picked)


class TestItDeclinesRatherThanGuess(unittest.TestCase):
    """Anything the selector cannot reason about must fall back to the whole suite."""

    def setUp(self):
        self.real = rt.changed_files

    def tearDown(self):
        rt.changed_files = self.real

    def _changed(self, *names):
        rt.changed_files = lambda: set(names)
        return rt.select_changed(EVERY)

    def test_an_unmappable_file_forces_the_full_run(self):
        selected, note = self._changed("skills/visualize/SKILL.md")
        self.assertIsNone(selected)
        self.assertIn("cannot map", note)

    def test_one_unmappable_file_poisons_an_otherwise_clean_set(self):
        """It must not run "the tests for the .py part" and call that a pass."""
        selected, _ = self._changed("lib/gitlab.py", "README.md")
        self.assertIsNone(selected)

    def test_measure_js_is_attributed_to_the_module_that_shells_out_to_it(self):
        """Nothing imports it, so without this a browser-side change selects nothing."""
        selected, _ = self._changed("lib/diagram/js/measure.js")
        self.assertIsNotNone(selected)
        self.assertIn(rt.REPO_ROOT / "lib/diagram/test_browser.py", selected)

    def test_no_changes_selects_nothing_rather_than_everything(self):
        """Its name was right and its assertion was not: `None` here means "run everything",
        so asking a clean tree what it affects used to cost the whole suite. An empty selection
        is the answer — there are no edits, so nothing can be affected by them. Only a change
        it CANNOT map deserves the fallback, which the two tests above pin.
        """
        selected, note = self._changed()
        self.assertEqual(selected, set())
        self.assertIn("nothing changed", note)


class TestDiscovery(unittest.TestCase):
    def test_slow_files_are_excluded_unless_asked_for(self):
        fast = {p.name for p in rt.find_test_files(False, [])}
        every = {p.name for p in rt.find_test_files(True, [])}
        self.assertEqual(every - fast,
                         {"test_place_slow.py", "test_paste_gate_slow.py"})

    def test_sharding_only_names_classes_unittest_will_accept(self):
        """A shard runs as `python3 <file> <ClassName>`, so a plain helper class picked up
        here would fail the shard outright."""
        for path in rt.find_test_files(True, []):
            for name in rt._classes(path):
                with self.subTest(path=path.name, klass=name):
                    self.assertTrue(name.startswith("Test"))

    def test_only_files_slow_enough_to_earn_it_are_split(self):
        files = list(rt.find_test_files(False, []))
        slow = str(next(p for p in files if p.name == "test_visualize.py")
                   .relative_to(rt.REPO_ROOT))
        jobs = rt._jobs(files, {slow: rt.SHARD_OVER + 1})
        shards = [j for j in jobs if j[1] is not None]
        self.assertTrue(shards, "the slow file should have been split")
        self.assertEqual({str(p.relative_to(rt.REPO_ROOT)) for p, _ in shards}, {slow})
        self.assertEqual(rt._jobs(files, {}), [(p, None) for p in files],
                         "with no timings, nothing is split")

    def test_every_test_in_a_split_file_lands_in_exactly_one_shard(self):
        """Sharding must partition the file, not sample it."""
        path = next(p for p in rt.find_test_files(False, []) if p.name == "test_visualize.py")
        import ast as _ast
        tree = _ast.parse(path.read_text(encoding="utf-8"))
        in_classes = sum(
            1 for n in tree.body if isinstance(n, _ast.ClassDef) and n.name.startswith("Test")
            for m in n.body
            if isinstance(m, _ast.FunctionDef) and m.name.startswith("test_"))
        loose = [n.name for n in tree.body
                 if isinstance(n, _ast.FunctionDef) and n.name.startswith("test_")]
        self.assertEqual(loose, [], "a module-level test would be dropped by sharding")
        self.assertGreater(in_classes, 0)

    def test_a_pattern_filters_by_path_substring(self):
        picked = [p.name for p in rt.find_test_files(False, ["gates/"])]
        self.assertTrue(picked)
        self.assertTrue(all("test_" in n for n in picked))
        self.assertNotIn("test_gitlab.py", picked)


class TestFingerprint(unittest.TestCase):
    """A passing full run is cached against `_fingerprint`, so anything the fingerprint
    cannot see is a change that reports as "already tested today" — the skip fires exactly
    where the working agreement says a commit must not be created with a failing test.

    Runs against a throwaway repo rather than this one: the fingerprint is a claim about a
    git working tree, and the only way to test it is to change one.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name)
        self._git("init", "-q", ".")
        self._git("config", "user.email", "t@example.com")
        self._git("config", "user.name", "T")
        (self.repo / "src.py").write_text("x = 1\n")
        self._git("add", "src.py")
        self._git("commit", "-q", "-m", "first")
        original = rt.REPO_ROOT
        rt.REPO_ROOT = self.repo
        self.addCleanup(setattr, rt, "REPO_ROOT", original)

    def _git(self, *args):
        subprocess.run(["git", *args], cwd=self.repo, check=True,
                       capture_output=True, text=True)

    def _fp(self):
        fp = rt._fingerprint("fast")
        self.assertIsNotNone(fp, "git could not answer in the throwaway repo")
        return fp

    def test_an_unchanged_tree_fingerprints_the_same(self):
        """The whole point of the cache: the same suite twice in one sitting runs once."""
        self.assertEqual(self._fp(), self._fp())

    def test_an_unstaged_edit_changes_it(self):
        """The bug this class exists for. `ls-files -s` names INDEX blobs, so an edit that
        has not been staged — the normal state of an edit loop — left the fingerprint
        identical and a real run was skipped with eight modified files in the tree."""
        before = self._fp()
        (self.repo / "src.py").write_text("x = 2\n")
        self.assertNotEqual(before, self._fp())

    def test_a_staged_edit_changes_it(self):
        before = self._fp()
        (self.repo / "src.py").write_text("x = 3\n")
        self._git("add", "src.py")
        self.assertNotEqual(before, self._fp())

    def test_an_untracked_file_changes_it(self):
        before = self._fp()
        (self.repo / "new.py").write_text("y = 1\n")
        self.assertNotEqual(before, self._fp())

    def test_a_deleted_tracked_file_changes_it_and_still_caches(self):
        """Deleting a tracked file must not disable the cache until the deletion is
        committed — an unreadable path is digested as the fact that it is gone."""
        before = self._fp()
        (self.repo / "src.py").unlink()
        after = rt._fingerprint("fast")
        self.assertIsNotNone(after, "a pending deletion disabled caching altogether")
        self.assertNotEqual(before, after)

    def test_the_mode_is_part_of_it(self):
        """A `--slow` pass is not a fast pass: the two must not share a cache entry."""
        self.assertNotEqual(rt._fingerprint("fast"), rt._fingerprint("slow"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
