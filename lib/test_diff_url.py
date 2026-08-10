#!/usr/bin/env python3
"""Tests for the diff-url CLI — see diff_url.py's module docstring for why this,
unlike findings.py/threads.py, moved here wholesale rather than being split by what's
shared vs. duplicated (there was no per-skill logic to split out).

Run: `python3 lib/test_diff_url.py` (stdlib only).
"""
import io
import os
import sys
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib import diff_url as D


def run_main(argv):
    old_argv = sys.argv
    sys.argv = ["diff-url.py"] + argv
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            D.main()
    finally:
        sys.argv = old_argv
    return buf.getvalue().strip()


class TestBaseline(unittest.TestCase):
    def setUp(self):
        D.context = lambda: {"web": "https://gitlab.example.com/g/p"}
        D.mr_view = lambda: {"iid": 9}
        D.versions = lambda ctx, iid: [{"id": 3, "head_commit_sha": "newest"},
                                       {"id": 2, "head_commit_sha": "middle"}]

    def test_prints_the_newest_version_head(self):
        self.assertEqual(run_main(["baseline"]), "newest")

    def test_explicit_iid_skips_mr_view(self):
        D.mr_view = lambda: (_ for _ in ()).throw(AssertionError("should not be called"))
        self.assertEqual(run_main(["baseline", "--iid", "9"]), "newest")

    def test_dies_when_no_versions_exist(self):
        D.versions = lambda ctx, iid: []
        with self.assertRaises(SystemExit):
            run_main(["baseline"])


class TestUrl(unittest.TestCase):
    def setUp(self):
        D.context = lambda: {"web": "https://gitlab.example.com/g/p"}
        D.mr_view = lambda: {"iid": 9}
        D.mr_object = lambda ctx, iid: {"web_url": None}
        D.versions = lambda ctx, iid: [{"id": 3, "head_commit_sha": "newest"},
                                       {"id": 2, "head_commit_sha": "middle"}]

    def test_uses_previous_version_head_when_start_sha_omitted(self):
        out = run_main(["url"])
        self.assertEqual(out, "https://gitlab.example.com/g/p/-/merge_requests/9/diffs"
                               "?diff_id=3&start_sha=middle")

    def test_uses_the_given_start_sha(self):
        out = run_main(["url", "--start-sha", "captured-baseline"])
        self.assertEqual(out, "https://gitlab.example.com/g/p/-/merge_requests/9/diffs"
                               "?diff_id=3&start_sha=captured-baseline")

    def test_prefers_the_mr_web_url_over_the_reconstructed_one(self):
        D.mr_object = lambda ctx, iid: {
            "web_url": "https://on-prem.example.com/g/p/-/merge_requests/9"}
        out = run_main(["url", "--start-sha", "x"])
        self.assertTrue(out.startswith("https://on-prem.example.com/g/p/-/merge_requests/9/diffs"))

    def test_dies_when_only_one_version_and_no_start_sha_given(self):
        D.versions = lambda ctx, iid: [{"id": 3, "head_commit_sha": "newest"}]
        with self.assertRaises(SystemExit):
            run_main(["url"])

    def test_dies_when_no_versions_exist(self):
        D.versions = lambda ctx, iid: []
        with self.assertRaises(SystemExit):
            run_main(["url"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
