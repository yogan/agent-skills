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
import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_spec = importlib.util.spec_from_file_location(
    "run_tests", Path(__file__).with_name("run_tests.py"))
rt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rt)

EVERY = set(rt.repo_files())


class TestSelectionCoversTheObviousCase(unittest.TestCase):
    def test_a_module_always_selects_its_own_test_file(self):
        """The floor. If `foo.py` changes and `test_foo.py` does not run, the feature is
        actively harmful."""
        missed = []
        graph = rt.import_graph(EVERY)      # one parse of the repo, not one per module
        for src in sorted(EVERY):
            if src.name.startswith("test_"):
                continue
            own = src.with_name("test_" + src.name)
            if own.exists() and own not in rt._dependents({src}, EVERY, graph):
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

    def test_a_bare_import_from_another_folder_is_an_edge(self):
        """`check_push.py` puts `e2e/` on sys.path and imports `scenarios` by its bare name;
        resolved only beside the importer, a new scenario's commit ran no test of check_push's
        and the one it broke was found a commit later."""
        picked = rt._dependents({rt.REPO_ROOT / "e2e/scenarios.py"}, EVERY)
        self.assertIn(rt.REPO_ROOT / "test_check_push.py", picked)

    def test_a_change_reaches_tests_transitively_not_just_direct_importers(self):
        """test_visualize.py never imports palette; it imports render, which imports it."""
        picked = rt._dependents({rt.REPO_ROOT / "lib/diagram/palette.py"}, EVERY)
        self.assertIn(rt.REPO_ROOT / "skills/visualize/scripts/test_visualize.py", picked)


# A file nothing names, this test included: written in pieces so its source does not name it.
UNNAMED = "docs/no-" + "reader" + ".json"


class TestWhatChanged(unittest.TestCase):
    """changed_files against a real repository: what git lists is the whole input."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = tmp.name
        for args in (("init", "-q"), ("commit", "-q", "--allow-empty", "-m", "one")):
            self._git(*args)
        with open(os.path.join(self.repo, "old.py"), "w") as f:
            f.write("x = 1\n")
        self._git("add", "old.py")
        self._git("commit", "-q", "-m", "two")

    def _git(self, *args):
        subprocess.run(["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
                       cwd=self.repo, check=True, capture_output=True)

    def test_a_staged_rename_counts_as_both_its_paths(self):
        """At commit time every change is staged, where git reports a rename by its new
        name alone: the tests importing the old one were never selected."""
        self._git("mv", "old.py", "new.py")
        self.assertEqual(rt.changed_files(root=self.repo), {"old.py", "new.py"})

    def test_an_unknown_revision_is_no_answer_rather_than_no_change(self):
        """It used to read as a clean tree, and `--changed-since` passed having run nothing."""
        self.assertIsNone(rt.changed_files("no-such-rev", root=self.repo))


class TestItDeclinesRatherThanGuess(unittest.TestCase):
    """Anything the selector cannot reason about must fall back to the whole suite."""

    def setUp(self):
        self.real = rt.changed_files

    def tearDown(self):
        rt.changed_files = self.real

    def _changed(self, *names):
        rt.changed_files = lambda base="HEAD": set(names)
        return rt.select_changed(EVERY)

    def test_an_unmappable_file_forces_the_full_run(self):
        """A file no Python file names is read by no test the selector can see."""
        selected, note = self._changed(UNNAMED)
        self.assertIsNone(selected)
        self.assertIn("cannot map", note)

    def test_a_diff_git_cannot_make_runs_everything(self):
        rt.changed_files = lambda base="HEAD": None
        selected, note = rt.select_changed(EVERY, "no-such-rev")
        self.assertIsNone(selected)
        self.assertIn("cannot tell", note)

    def test_one_unmappable_file_poisons_an_otherwise_clean_set(self):
        """It must not run "the tests for the .py part" and call that a pass."""
        selected, _ = self._changed("lib/gitlab.py", UNNAMED)
        self.assertIsNone(selected)

    def test_a_document_nothing_names_selects_nothing_rather_than_everything(self):
        """A README or RUNBOOK edit used to run the whole suite in the commit hook."""
        selected, _ = self._changed("e2e/NO-" + "SUCH-NOTES.md")
        self.assertEqual(selected, set())
        selected, _ = self._changed("lib/gitlab.py", "docs/no-" + "picture.png")
        self.assertIn(rt.REPO_ROOT / "lib/test_gitlab.py", selected)

    def test_a_document_a_test_names_still_selects_that_test(self):
        selected, _ = self._changed("skills/rework-mr/SKILL.md")
        self.assertIsNotNone(selected)
        self.assertTrue(selected)

    def test_a_non_python_file_selects_every_test_reaching_a_file_that_names_it(self):
        """A skill's SKILL.md, a script's JSON spec: whatever names it, and whatever reaches
        that, runs. It used to decline on every one of them, so each commit touching a
        SKILL.md ran the whole suite in the hook."""
        selected, _ = self._changed("skills/rework-mr/scripts/paste-gates.json")
        self.assertIsNotNone(selected)
        self.assertIn(rt.REPO_ROOT / "hooks/test_paste_gate.py", selected)
        self.assertIn(rt.REPO_ROOT / "skills/rework-mr/scripts/test_threads.py", selected)

    def test_a_mod_file_selects_the_mods_bridge_and_the_python_mirroring_it(self):
        """A mod's TypeScript is tested by the engine, through `mods/test_mods.py`; a Python
        test that reads one of its files (the icon tables) runs too."""
        selected, _ = self._changed("mods/review-ui/hooks/panel.ts")
        self.assertIn(rt.REPO_ROOT / "mods/test_mods.py", selected)
        self.assertIn(rt.REPO_ROOT / "lib/test_ui.py", selected)
        selected, _ = self._changed("mods/review-ui/hooks/register.tsx")
        self.assertIn(rt.REPO_ROOT / "mods/test_mods.py", selected)

    def test_a_changed_test_file_selects_the_check_that_reads_every_test_file(self):
        """test_repo_hygiene.py globs for test files, so nothing imports or names them:
        a new test that leaked a temp dir passed the commit hook."""
        selected, _ = self._changed("lib/test_parallel.py")
        self.assertIn(rt.REPO_ROOT / "test_repo_hygiene.py", selected)
        selected, _ = self._changed("lib/parallel.py")
        self.assertNotIn(rt.REPO_ROOT / "test_repo_hygiene.py", selected)

    def test_a_hunk_extension_file_selects_the_bridge_beside_it(self):
        """An extension's TypeScript is tested by Bun, through its own `test_*.py`. A Python
        comment naming one of its files once selected that module's tests alone, and the
        extension's were skipped at commit time."""
        for rel in ("hunk-extensions/keep-notes/src/disk.ts",
                    "hunk-extensions/keep-notes/src/review.ts"):
            selected, _ = self._changed(rel)
            self.assertIn(rt.REPO_ROOT / "hunk-extensions/keep-notes/test_keep_notes.py", selected)

    def test_a_declined_run_is_the_whole_run_and_uses_the_days_record(self):
        """The hook's `--changed` after a full pass used to run everything again, on a tree
        that had just passed, because a declined run skipped the record."""
        out = io.StringIO()
        with mock.patch.object(rt, "select_changed", return_value=(None, "x")), \
                mock.patch.object(rt, "_fingerprint", return_value="F") as fp, \
                mock.patch.object(rt, "_cached_pass", return_value={"files": 1, "tests": 2, "at": "10:00"}), \
                mock.patch.object(sys, "argv", ["run_tests.py", "--changed"]), redirect_stdout(out):
            self.assertEqual(rt.main(), 0)
        fp.assert_called_once_with("fast")
        self.assertIn("skipped", out.getvalue())


    def test_changed_since_reads_the_difference_from_that_revision(self):
        """`check_commits.py` checks a commit by what IT changed: in the commit's worktree,
        the difference from its parent."""
        seen = {}

        def fake(base="HEAD"):
            seen["base"] = base
            return {"lib/gitlab.py"}
        rt.changed_files = fake
        selected, _ = rt.select_changed(EVERY, "abc123^")
        self.assertEqual(seen["base"], "abc123^")
        self.assertIn(rt.REPO_ROOT / "lib/test_gitlab.py", selected)

    def test_measure_js_is_attributed_to_the_module_that_shells_out_to_it(self):
        """Nothing imports it, so without this a browser-side change selects nothing."""
        selected, _ = self._changed("lib/diagram/js/measure.js")
        self.assertIsNotNone(selected)
        self.assertIn(rt.REPO_ROOT / "lib/diagram/test_browser.py", selected)

    def test_a_file_in_a_data_folder_selects_the_tests_reaching_the_module_naming_it(self):
        """A recording is read by joining its folder's name to the module's location, so no
        code names the file, and a commit deleting two recordings ran all 70 test files in the
        hook. The folder's name in the code is the edge; a subfolder counts through it."""
        selected, _ = self._changed("e2e/recordings/no-such-" + "scenario.json")
        self.assertIsNotNone(selected)
        self.assertIn(rt.REPO_ROOT / "e2e/test_drive.py", selected)
        self.assertIn(rt.REPO_ROOT / "e2e/test_mock_model.py", selected)
        self.assertNotIn(rt.REPO_ROOT / "lib/test_gitlab.py", selected)
        selected, _ = self._changed("e2e/patches/no-such-mr/0001-no-" + "such.patch")
        self.assertIn(rt.REPO_ROOT / "e2e/test_fixture.py", selected)

    def test_a_folder_of_code_or_a_folder_nothing_names_still_declines(self):
        """Modules that walk `skills/` name it too; that says nothing about what reads one
        file in it."""
        for rel in ("skills/no-such-skill/no-" + "reader.json", "e2e/no-such-data/no-" + "reader.json"):
            with self.subTest(rel=rel):
                selected, _ = self._changed(rel)
                self.assertIsNone(selected)

    def test_no_changes_selects_nothing_rather_than_everything(self):
        """Its name was right and its assertion was not: `None` here means "run everything",
        so asking a clean tree what it affects used to cost the whole suite. An empty selection
        is the answer — there are no edits, so nothing can be affected by them. Only a change
        it CANNOT map deserves the fallback, which the two tests above pin.
        """
        selected, note = self._changed()
        self.assertEqual(selected, set())
        self.assertIn("nothing changed", note)


class TestADocumentIsReadWhereCodeNamesIt(unittest.TestCase):
    """A document's name in a docstring or a comment is prose about it, not a read: counted,
    a CLAUDE.md edit selected the tests of the eight modules whose docstrings cite it."""

    def readers(self, rel, sources):
        with tempfile.TemporaryDirectory() as d:
            files = set()
            for name, text in sources.items():
                path = Path(d) / name
                path.write_text(text)
                files.add(path)
            return sorted(p.name for p in rt._readers(rel, files))

    def test_only_code_that_uses_the_name_reads_a_document(self):
        sources = {
            "cites.py": '"""See NOTES.md on why."""\n# and NOTES.md again\nx = 1\n',
            "reads.py": 'def f():\n    """Checks the notes."""\n    return open("docs/NOTES.md").read()\n',
            "formats.py": 'def g(d):\n    return f"{d}/NOTES.md"\n',
        }
        self.assertEqual(self.readers("docs/NOTES.md", sources), ["formats.py", "reads.py"])

    def test_any_other_file_is_still_read_wherever_it_is_named(self):
        """A spec or a shell view, cited in a comment, may be what the code runs: kept."""
        self.assertEqual(self.readers("view.sh", {"a.py": "# runs view.sh\nx = 1\n"}), ["a.py"])

    def test_a_file_that_does_not_parse_counts_as_naming_it(self):
        self.assertEqual(self.readers("NOTES.md", {"broken.py": '"""NOTES.md"""\ndef (\n'}), ["broken.py"])


class TestIsolation(unittest.TestCase):
    def test_no_test_sees_the_session_that_runs_the_suite(self):
        seen = {}

        def fake_run(argv, **kw):
            seen.update(kw["env"])
            return subprocess.CompletedProcess(argv, 0, "", "")
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": "s", "REVIEW_UI_DRAWN": "1",
                                          "HOME": os.environ.get("HOME", "/")}), \
                mock.patch.object(rt.subprocess, "run", side_effect=fake_run):
            rt.run_one((Path("x.py"), None))
        self.assertNotIn("CLAUDE_CODE_SESSION_ID", seen)
        self.assertNotIn("REVIEW_UI_DRAWN", seen)
        self.assertIn("HOME", seen)

    def test_a_test_that_reaches_glab_fails_naming_the_call(self):
        """A test that left GitLab unstubbed called the real instance with the real login,
        and the whole run hung whenever that call did. It passed, so only a hang said so."""
        with tempfile.TemporaryDirectory() as tmp:
            test = Path(tmp) / "test_x.py"
            with mock.patch.object(rt, "NO_GLAB", Path(tmp) / "no-glab"):
                for body, fails in (("subprocess.run(['glab', 'api', 'user'])", True),
                                    ("subprocess.run(['true'])", False)):
                    test.write_text(f"import subprocess\n{body}\n")
                    _, proc, _ = rt.run_one((test, None), tmp)
                    self.assertEqual(proc.returncode != 0, fails, proc.stderr)
                    self.assertEqual("glab api user" in proc.stderr, fails)
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()),
                             ["no-glab", "test_x.py"])          # no call log left behind


class TestTempFolder(unittest.TestCase):
    def test_the_named_folder_is_every_tests_temp_folder_and_none_without_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            setting = Path(tmp) / "setting"
            folder = Path(tmp) / "scanned-less"
            with mock.patch.object(rt, "TMPDIR_FILE", setting):
                self.assertIsNone(rt.test_tmpdir())
                setting.write_text(f"{folder}\n")
                self.assertEqual(rt.test_tmpdir(), folder)
                self.assertTrue(folder.is_dir())
            seen = {}

            def fake_run(argv, **kw):
                seen.update(kw["env"])
                return subprocess.CompletedProcess(argv, 0, "", "")
            with mock.patch.object(rt.subprocess, "run", side_effect=fake_run):
                rt.run_one((Path("x.py"), None), folder)
            self.assertEqual(seen["TMPDIR"], f"{folder}/")


class TestFailureSummary(unittest.TestCase):
    def test_each_failed_test_is_named_with_what_it_raised(self):
        """Last in the run, so a `tail` names them: the bare count used to send sessions
        back for a second full run just to learn which file failed."""
        output = ("..F\n" + "=" * 70 + "\nFAIL: test_b (test_x.TestThing.test_b)\n" + "-" * 70
                  + "\nTraceback (most recent call last):\n  File \"x\", line 1\n"
                  "AssertionError: 1 != 2\n\n" + "=" * 70
                  + "\nERROR: test_c (test_x.TestOther.test_c)\n" + "-" * 70
                  + "\nKeyError: 'topics'\n")
        self.assertEqual(rt.failed_tests(output), [
            "FAIL TestThing.test_b — AssertionError: 1 != 2",
            "ERROR TestOther.test_c — KeyError: 'topics'"])


class TestDiscovery(unittest.TestCase):
    def test_slow_files_are_excluded_unless_asked_for(self):
        fast = {p.name for p in rt.find_test_files(False, [])}
        every = {p.name for p in rt.find_test_files(True, [])}
        self.assertEqual(every - fast,
                         {"test_place_slow.py", "test_paste_gate_slow.py"})

    # Counts what unittest itself finds in a file, in all and class by class, in a process of
    # its own: test files put their own folders on sys.path and import modules by short names.
    COUNT = """if True:
        import importlib.util, os, sys, unittest
        path = sys.argv[1]
        sys.path.insert(0, os.path.dirname(path))
        spec = importlib.util.spec_from_file_location("counted", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules["counted"] = module
        spec.loader.exec_module(module)
        load = unittest.defaultTestLoader
        print(load.loadTestsFromModule(module).countTestCases())
        print(sum(load.loadTestsFromName(name, module).countTestCases() for name in sys.argv[2:]))
    """

    def test_a_split_file_s_shards_run_exactly_its_tests(self):
        """A shard runs as `python3 <file> <ClassName>`: a helper class picked up as one fails
        it outright, a base class counted as one runs its tests twice, and a TestCase not
        named `Test*` would be silently dropped. The files split today, by the box's list
        and by being slow (`test_visualize.py` is the one that is)."""
        split = set(rt.testbox.SPLIT) | {"skills/visualize/scripts/test_visualize.py"}
        for rel in sorted(split):
            path = rt.REPO_ROOT / rel
            with self.subTest(rel):
                out = subprocess.run([sys.executable, "-c", self.COUNT, str(path),
                                      *rt._classes(path)],
                                     capture_output=True, text=True, timeout=120)
                self.assertEqual(out.returncode, 0, out.stderr[-2000:])
                whole, shards = out.stdout.split()
                self.assertEqual(shards, whole)

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

    def test_a_run_of_some_files_keeps_the_other_files_timings(self):
        """A filtered run used to replace the record with its own few files, so the next whole
        run split nothing."""
        a, b = "test_run_tests.py", "test_check_push.py"
        kept = rt.record_timings({a: 20.0, b: 1.0}, {b: {"seconds": 2.0}})
        self.assertEqual(kept, {a: 20.0, b: 2.0})

    def test_a_file_that_is_gone_drops_out_of_the_timings(self):
        kept = rt.record_timings({"gone/test_nothing_here.py": 3.0}, {})
        self.assertEqual(kept, {})

    def test_a_pattern_filters_by_path_substring(self):
        picked = [p.name for p in rt.find_test_files(False, ["gates/"])]
        self.assertTrue(picked)
        self.assertTrue(all("test_" in n for n in picked))
        self.assertNotIn("test_gitlab.py", picked)


class TestOnlyThisCheckoutsFiles(unittest.TestCase):
    """The folder holds more than the checkout: a git worktree made inside it and an ignored
    copy of the repo had their tests run with its own, and the commit hook took 8 to 11
    minutes."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name).resolve()
        self._git("init", "-q")
        self._write(".gitignore", "/.cache/\n")
        self._write("test_tracked.py")
        self._git("add", ".")
        self._git("commit", "-q", "-m", "one")
        self._write("test_new.py")                                  # untracked, not ignored
        self._write(".cache/before/test_copy.py")                   # ignored
        self._git("worktree", "add", "-q", "--detach", ".claude/worktrees/side")
        self._write(".claude/worktrees/side/test_side.py")          # another checkout's

    def _git(self, *args, cwd=None):
        subprocess.run(["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
                       cwd=cwd or self.repo, check=True, capture_output=True)

    def _write(self, rel, text="x = 1\n"):
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def _names(self, root):
        return [str(p.relative_to(root)) for p in rt.repo_files(root=root)]

    def test_neither_a_worktree_inside_nor_an_ignored_folder_is_part_of_it(self):
        self.assertEqual(self._names(self.repo), ["test_new.py", "test_tracked.py"])

    def test_inside_a_worktree_its_own_files_are_found(self):
        """Where an agent works: a worktree under `.claude/worktrees/` of the main checkout."""
        side = self.repo / ".claude/worktrees/side"
        self.assertEqual(self._names(side), ["test_side.py", "test_tracked.py"])

    def test_the_runner_finds_this_checkouts_own_tests(self):
        """The live repo, wherever it is checked out: its own test file is found, and nothing
        under a folder git leaves out."""
        found = list(rt.find_test_files(True, []))
        self.assertIn(Path(__file__).resolve(), found)
        self.assertFalse([p for p in found
                          if {".claude", ".cache"} & set(p.relative_to(rt.REPO_ROOT).parts)])


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

    def test_a_pass_is_not_recorded_when_the_files_changed_during_the_run(self):
        """The record is what lets the next run skip, so it must describe the files the tests
        saw. An edit by another session mid-run used to be recorded under the starting
        fingerprint, and undoing it afterwards then made an untested state count as tested."""
        passed = self.repo / "passed.json"
        with mock.patch.object(rt, "PASSED", passed), redirect_stdout(io.StringIO()):
            before = self._fp()
            (self.repo / "src.py").write_text("x = 4\n")          # edited while it ran
            rt._record_pass(before, "fast", 1, 1)
            self.assertFalse(passed.exists())
            rt._record_pass(self._fp(), "fast", 1, 1)
            self.assertTrue(passed.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
