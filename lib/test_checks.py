"""lib/checks.py on throwaway repositories: a clone of a bare one, with hooks of its own that
log each run, so a test can tell what ran ahead of time and what ran again."""
import os
import subprocess
import sys
import shutil
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib import checks  # noqa: E402

# Strips trailing spaces from the staged files and stages them again: a formatter.
FORMAT = """#!/bin/sh
echo pre-commit >> "{log}"
for f in $(git diff --cached --name-only --diff-filter=AM); do
  sed -i.bak 's/ *$//' "$f" && rm -f "$f.bak" && git add "$f"
done
"""
PUSH = """#!/bin/sh
echo pre-push >> "{log}"
cat > /dev/null
"""
MSG = """#!/bin/sh
echo commit-msg >> "{log}"
grep -q '^fixup! ' "$1"
"""


def sh(cwd, *args):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, (args, r.stdout, r.stderr)
    return r.stdout.strip()


class Repo:
    """Two commits on `main`, pushed. Built once per run and copied for each test: a clone
    and a push cost more than every check a test runs."""
    _template = None

    @classmethod
    def build(cls, d):
        bare, root = os.path.join(d, "remote.git"), os.path.join(d, "work")
        sh(d, "git", "init", "-q", "--bare", "-b", "main", bare)
        sh(d, "git", "clone", "-q", bare, root)
        for k, v in (("user.name", "Test"), ("user.email", "test@example.invalid"),
                     ("core.hooksPath", ".hooks")):
            sh(root, "git", "config", k, v)
        os.makedirs(os.path.join(root, ".hooks"))
        for name, text in (("a.txt", "one\n"), ("b.txt", "two\n")):
            with open(os.path.join(root, name), "w") as fh:
                fh.write(text)
        sh(root, "git", "add", "-A")
        sh(root, "git", "commit", "-q", "--no-verify", "-m", "feat: a and b")
        with open(os.path.join(root, "c.txt"), "w") as fh:
            fh.write("three\n")
        sh(root, "git", "add", "-A")
        sh(root, "git", "commit", "-q", "--no-verify", "-m", "feat: c")
        sh(root, "git", "push", "-q", "--no-verify", "-u", "origin", "main")

    def __init__(self, d, template):
        self.d = d
        self.log = os.path.join(d, "hooks.log")
        self.root = os.path.join(d, "work")
        for name in ("remote.git", "work"):
            shutil.copytree(os.path.join(template, name), os.path.join(d, name), symlinks=True)
        sh(self.root, "git", "remote", "set-url", "origin", os.path.join(d, "remote.git"))
        self.target = sh(self.root, "git", "rev-parse", "HEAD^")

    def write(self, path, text):
        with open(os.path.join(self.root, path), "w") as fh:
            fh.write(text)

    def read(self, path):
        with open(os.path.join(self.root, path)) as fh:
            return fh.read()

    def hook(self, event, body):
        p = os.path.join(self.root, ".hooks", event)
        with open(p, "w") as fh:
            fh.write(body.format(log=self.log))
        os.chmod(p, 0o755)

    def ran(self):
        try:
            with open(self.log) as fh:
                return fh.read().split()
        except OSError:
            return []

    def record(self, tests="true", **early):
        hooks = []
        for ev in checks.present_hooks(self.root):
            hooks.append({"event": ev, "does": f"the {ev} hook", "rewrites": ev == "pre-commit",
                          "early": early.get(ev.replace("-", "_"),
                                             None if ev == "commit-msg" else f"git hook run {ev}")})
        parts = tests if isinstance(tests, list) else [{"name": "all", "paths": ["**"], "run": tests}]
        return checks.store(self.root, {"hooks": hooks, "tests": parts,
                                        "based_on": ["tests.cfg"]})


class Case(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._template = tempfile.TemporaryDirectory()
        Repo.build(cls._template.name)

    @classmethod
    def tearDownClass(cls):
        cls._template.cleanup()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.r = Repo(self._tmp.name, self._template.name)

    def tearDown(self):
        self._tmp.cleanup()


class TestRecord(Case):
    def test_none_then_kept_then_stale_on_each_kind_of_change(self):
        self.r.hook("pre-commit", FORMAT)
        rec, why = checks.status(self.r.root)
        self.assertIsNone(rec)
        self.assertIn("no record", why)
        self.r.record()
        self.assertIsNone(checks.status(self.r.root)[1])
        self.r.hook("pre-commit", FORMAT + "true\n")
        self.assertEqual(checks.status(self.r.root)[1], "the pre-commit hook changed")
        self.r.record()
        self.r.write("tests.cfg", "x")
        self.assertEqual(checks.status(self.r.root)[1], "tests.cfg changed")
        self.r.record()
        self.r.hook("pre-push", PUSH)
        self.assertEqual(checks.status(self.r.root)[1], "a pre-push hook was added")

    def test_kept_in_the_git_folder_out_of_the_tree(self):
        self.r.record()
        self.assertTrue(os.path.exists(os.path.join(self.r.root, ".git", checks.RECORD)))
        self.assertEqual(sh(self.r.root, "git", "status", "--porcelain"), "")

    def test_a_reading_that_leaves_a_hook_out_is_refused(self):
        self.r.hook("pre-push", PUSH)
        with self.assertRaisesRegex(ValueError, "pre-push"):
            checks.store(self.r.root, {"hooks": [], "tests": []})

    def test_a_repository_without_hooks_records_only_its_tests(self):
        rec = self.r.record()
        self.assertEqual(rec["hooks"], [])
        self.assertIn("No pre-commit", checks.brief(self.r.root, "no record yet"))


class TestRun(Case):
    def test_the_formatter_runs_ahead_and_its_changes_are_what_is_checked(self):
        self.r.hook("pre-commit", FORMAT)
        rec = self.r.record()
        self.r.write("a.txt", "one fixed   \n")
        stamp = checks.run(self.r.root, rec)
        self.assertTrue(stamp["ok"], stamp)
        self.assertEqual(self.r.read("a.txt"), "one fixed\n")
        self.assertEqual(stamp["tree"], checks.snapshot(self.r.root))
        self.assertEqual(stamp["ran"], ["pre-commit hook", "all checks"])

    def test_an_early_command_is_told_the_fix_s_files(self):
        rec = self.r.record(tests='printf "%s," $REWORK_FIX_FILES; exit 1')
        self.r.write("a.txt", "one fixed\n")
        self.r.write("d.txt", "new\n")
        stamp = checks.run(self.r.root, rec, ["d.txt"])
        self.assertEqual(stamp["output"], "a.txt,d.txt,")

    def test_the_repository_s_own_node_tools_are_on_path(self):
        """A JavaScript project's hook manager sits in its `node_modules/.bin`, which the
        agent's shell does not have on PATH: an early command calling it bare failed with
        `command not found`, though the same command worked in the person's terminal."""
        bin_dir = os.path.join(self.r.root, "node_modules", ".bin")
        os.makedirs(bin_dir)
        tool = os.path.join(bin_dir, "hookrunner")
        with open(tool, "w") as fh:
            fh.write("#!/bin/sh\necho hookrunner \"$@\" >> \"$0.log\"\n")
        os.chmod(tool, 0o755)
        with open(os.path.join(self.r.root, ".git", "info", "exclude"), "a") as fh:
            fh.write("node_modules/\n")
        self.r.hook("pre-push", "#!/bin/sh\nhookrunner pre-push\n")
        rec = self.r.record(tests="hookrunner test", pre_push="hookrunner run pre-push")
        self.r.write("a.txt", "one fixed\n")
        stamp = checks.run(self.r.root, rec)
        self.assertTrue(stamp["ok"], stamp)
        self.assertEqual(checks._hook(self.r.root, "pre-push"), (True, ""))   # the real hook too
        with open(tool + ".log") as fh:
            self.assertEqual(fh.read().splitlines(),
                             ["hookrunner run pre-push", "hookrunner test", "hookrunner pre-push"])

    def test_a_fix_runs_the_parts_it_touches_and_all_where_it_touches_none(self):
        parts = [{"name": "front", "paths": ["front/**"], "run": "true"},
                 {"name": "back", "paths": ["back/**/*.py", "back/pyproject.toml"], "run": "true"}]
        rec = {"tests": parts}
        names = lambda files: [p["name"] for p in checks.parts_for(rec, files)]
        self.assertEqual(names(["front/src/a.ts"]), ["front"])
        self.assertEqual(names(["back/x/y.py", "back/pyproject.toml"]), ["back"])
        self.assertEqual(names(["front/a.ts", "back/b.py"]), ["front", "back"])
        self.assertEqual(names(["back/README.md"]), ["front", "back"])   # no part covers it
        self.assertEqual(names(["package.json"]), ["front", "back"])

    def test_failing_tests_keep_their_output(self):
        rec = self.r.record(tests="echo boom; exit 1")
        self.r.write("a.txt", "one fixed\n")
        stamp = checks.run(self.r.root, rec)
        self.assertFalse(stamp["ok"])
        self.assertEqual((stamp["failed"], stamp["output"]), ("all checks", "boom"))

    def test_tests_that_change_files_fail(self):
        rec = self.r.record(tests="echo x >> b.txt")
        self.r.write("a.txt", "one fixed\n")
        stamp = checks.run(self.r.root, rec)
        self.assertFalse(stamp["ok"])
        self.assertIn("b.txt", stamp["output"])

    def test_an_edit_after_the_checks_reads_as_another_tree(self):
        rec = self.r.record()
        self.r.write("a.txt", "one fixed\n")
        stamp = checks.run(self.r.root, rec)
        self.r.write("a.txt", "one fixed again\n")
        self.assertNotEqual(checks.snapshot(self.r.root), stamp["tree"])


class TestCovered(Case):
    def test_a_hook_the_tests_cover_runs_neither_early_nor_at_push_once_trusted(self):
        self.r.hook("pre-push", PUSH)
        checks.store(self.r.root, {"hooks": [{"event": "pre-push", "does": "types", "rewrites": False,
                                              "early": None, "covered": True}],
                                   "tests": [{"name": "all", "paths": ["**"], "run": "true"}]})
        checks.trust(self.r.root)
        rec = checks.load(self.r.root)
        self.r.write("a.txt", "one fixed\n")
        stamp = checks.run(self.r.root, rec)
        self.assertEqual(stamp["ran"], ["all checks"])
        before = sh(self.r.root, "git", "rev-parse", "HEAD")
        checks.commit_fixup(self.r.root, rec, stamp, self.r.target)
        checks.autosquash(self.r.root, self.r.target, stamp["tree"])
        checks.push(self.r.root, rec, before)
        self.assertEqual(self.r.ran(), [])

    def test_covered_and_an_early_run_is_refused(self):
        self.r.hook("pre-push", PUSH)
        with self.assertRaisesRegex(ValueError, "no early run of its own"):
            checks.store(self.r.root, {"hooks": [{"event": "pre-push", "does": "x", "rewrites": False,
                                                  "early": "true", "covered": True}], "tests": []})


class TestPush(Case):
    def fix(self, rec):
        self.r.write("a.txt", "one fixed\n")
        stamp = checks.run(self.r.root, rec)
        self.assertTrue(stamp["ok"], stamp)
        return stamp

    def push(self, rec, stamp):
        before = sh(self.r.root, "git", "rev-parse", "HEAD")
        checks.commit_fixup(self.r.root, rec, stamp, self.r.target)
        checks.autosquash(self.r.root, self.r.target, stamp["tree"])
        checks.push(self.r.root, rec, before)

    def hooks(self):
        self.r.hook("pre-commit", FORMAT)
        self.r.hook("pre-push", PUSH)
        self.r.hook("commit-msg", MSG)

    def test_a_trusted_record_runs_nothing_again_but_the_message_check(self):
        self.hooks()
        self.r.record()
        checks.trust(self.r.root)
        rec = checks.load(self.r.root)
        stamp = self.fix(rec)
        self.assertEqual(self.r.ran(), ["pre-commit", "pre-push"])
        self.push(rec, stamp)
        self.assertEqual(self.r.ran(), ["pre-commit", "pre-push", "commit-msg"])
        # Squashed into its commit, pushed, and the pushed files are the checked ones.
        self.assertEqual(sh(self.r.root, "git", "log", "--format=%s"), "feat: c\nfeat: a and b")
        self.assertEqual(sh(self.r.root, "git", "rev-parse", "origin/main^{tree}"), stamp["tree"])

    def test_a_new_record_runs_the_real_hooks_once(self):
        self.hooks()
        rec = self.r.record()
        stamp = self.fix(rec)
        self.push(rec, stamp)
        self.assertEqual(self.r.ran(), ["pre-commit", "pre-push", "pre-commit", "commit-msg", "pre-push"])

    def test_a_hook_that_fails_at_commit_after_passing_early_marks_the_record_wrong(self):
        self.r.hook("pre-commit", '#!/bin/sh\n[ -e "{log}.armed" ] && exit 1\ntrue\n')
        rec = self.r.record()
        stamp = self.fix(rec)
        open(self.r.log + ".armed", "w").close()
        with self.assertRaisesRegex(checks.Stop, "pre-commit hook failed"):
            checks.commit_fixup(self.r.root, rec, stamp, self.r.target)
        self.assertIn("wrong", checks.status(self.r.root)[1])

    def test_a_commit_on_the_remote_that_the_branch_lacks_is_never_overwritten(self):
        rec = self.r.record()
        stamp = self.fix(rec)
        other = os.path.join(self.r.d, "other")
        sh(self.r.d, "git", "clone", "-q", os.path.join(self.r.d, "remote.git"), other)
        with open(os.path.join(other, "b.txt"), "a") as fh:
            fh.write("suggested\n")
        sh(other, "git", "-c", "user.name=R", "-c", "user.email=r@example.invalid", "commit",
           "-qam", "Apply suggestion")
        sh(other, "git", "push", "-q")
        sh(self.r.root, "git", "fetch", "-q")
        with self.assertRaisesRegex(checks.Stop, "has commits your branch does not"):
            self.push(rec, stamp)
        self.assertEqual(sh(other, "git", "rev-parse", "HEAD"),
                         sh(self.r.root, "git", "rev-parse", "origin/main"))

    def test_files_changed_since_the_checks_are_not_committed(self):
        rec = self.r.record()
        stamp = self.fix(rec)
        self.r.write("b.txt", "two changed\n")
        with self.assertRaisesRegex(checks.Stop, "not the ones the checks ran on"):
            checks.commit_fixup(self.r.root, rec, stamp, self.r.target)
        self.assertEqual(sh(self.r.root, "git", "rev-list", "--count", "HEAD"), "2")


if __name__ == "__main__":
    unittest.main()
