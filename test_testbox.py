#!/usr/bin/env python3
"""Tests for testbox.py: which jobs the box takes, and the tree it is sent. No VM is started.

Run: `python3 test_testbox.py`
"""
import io
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import testbox  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


class TestWhatRunsInTheBox(unittest.TestCase):
    def test_a_file_nobody_listed_runs_on_the_mac(self):
        self.assertFalse(testbox.in_box("lib/test_new_thing.py", None))

    def test_the_browser_and_mod_tests_stay_on_the_mac(self):
        for rel in ("lib/diagram/test_browser.py", "mods/test_mods.py",
                    "skills/visualize/scripts/test_visualize.py"):
            self.assertFalse(testbox.in_box(rel, None), rel)

    def test_a_split_file_runs_class_by_class_without_the_named_classes(self):
        rel = "skills/rework-mr/scripts/test_threads.py"
        self.assertFalse(testbox.in_box(rel, None), "whole, it holds classes that need the Mac")
        self.assertFalse(testbox.in_box(rel, "TestPushFix"))
        self.assertTrue(testbox.in_box(rel, "TestReplyDraft"))

    def test_every_listed_file_exists(self):
        missing = [rel for rel in testbox.BOX if not os.path.isfile(os.path.join(HERE, rel))]
        self.assertEqual(missing, [])


class TestTheTreeSent(unittest.TestCase):
    def test_tracked_and_untracked_files_go_and_ignored_ones_do_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            def git(*a):
                subprocess.run(["git", *a], cwd=tmp, check=True, capture_output=True)
            git("init", "-q")
            for name, text in (("kept.py", "x"), (".gitignore", "ignored.txt\n"),
                               ("ignored.txt", "secret"), ("new.py", "y")):
                with open(os.path.join(tmp, name), "w") as fh:
                    fh.write(text)
            git("add", "kept.py", ".gitignore")
            names = tarfile.open(fileobj=io.BytesIO(testbox.tree_tar(tmp))).getnames()
            self.assertEqual(sorted(n.removeprefix("./") for n in names),
                             [".gitignore", "kept.py", "new.py"])


if __name__ == "__main__":
    unittest.main()
