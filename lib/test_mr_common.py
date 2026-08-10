#!/usr/bin/env python3
"""Tests for the small identity/rendering helpers shared between review-mr's
findings.py and rework-mr's threads.py — see mr_common.py's module docstring for why
these, specifically, are the ones that moved here.

Run: `python3 lib/test_mr_common.py` (stdlib only).
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.mr_common import first_name, load, num, save, short_summary, state_file, topic_for, tref


class TestTref(unittest.TestCase):
    def test_carries_the_topic_icon(self):
        self.assertEqual(tref("t3"), "◈ t3")


class TestNum(unittest.TestCase):
    def test_extracts_the_digits(self):
        self.assertEqual(num("t3"), 3)
        self.assertEqual(num("t42"), 42)

    def test_no_digits_is_zero(self):
        self.assertEqual(num("t"), 0)


class TestTopicFor(unittest.TestCase):
    def test_finds_by_id(self):
        state = {"topics": [{"id": "t1"}, {"id": "t2"}]}
        self.assertEqual(topic_for(state, "t2"), {"id": "t2"})

    def test_none_when_absent(self):
        self.assertIsNone(topic_for({"topics": []}, "t1"))


class TestLoadSave(unittest.TestCase):
    def test_missing_file_loads_as_none(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(load(os.path.join(d, "nope.json")))

    def test_round_trips_through_save(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "state.json")
            save(path, {"topics": [{"id": "t1"}], "note": "héllo"})
            self.assertEqual(load(path), {"topics": [{"id": "t1"}], "note": "héllo"})


class TestFirstName(unittest.TestCase):
    def test_lastname_comma_firstname(self):
        self.assertEqual(first_name("Doe, Jane"), "Jane")

    def test_firstname_lastname(self):
        self.assertEqual(first_name("Jane Doe"), "Jane")

    def test_trailing_account_id_is_stripped(self):
        self.assertEqual(first_name("Doe, Jane - AB12345"), "Jane")

    def test_empty_input(self):
        self.assertEqual(first_name(""), "")
        self.assertEqual(first_name(None), "")


class TestShortSummary(unittest.TestCase):
    def test_short_text_is_unchanged(self):
        t = {"summary": "fix the thing"}
        self.assertEqual(short_summary({"threads": {}}, t), "fix the thing")

    def test_long_text_is_truncated_with_ellipsis(self):
        t = {"summary": "x" * 100}
        out = short_summary({"threads": {}}, t, width=10)
        self.assertEqual(out, "x" * 9 + "…")
        self.assertEqual(len(out), 10)

    def test_falls_back_to_first_threads_body_when_no_summary(self):
        state = {"threads": {"d1": {"body": "reviewer's opening note"}}}
        t = {"thread_ids": ["d1"]}
        self.assertEqual(short_summary(state, t), "reviewer's opening note")

    def test_whitespace_is_collapsed(self):
        t = {"summary": "line one\n  line   two"}
        self.assertEqual(short_summary({"threads": {}}, t), "line one line two")


class TestStateFile(unittest.TestCase):
    def test_creates_directory_and_returns_path(self):
        with tempfile.TemporaryDirectory() as root:
            path = state_file(root, "my-slug", 42, "topics.json")
            self.assertEqual(path, os.path.join(root, "my-slug--mr42", "topics.json"))
            self.assertTrue(os.path.isdir(os.path.dirname(path)))

    def test_filename_is_per_caller(self):
        with tempfile.TemporaryDirectory() as root:
            a = state_file(root, "slug", 1, "findings.json")
            b = state_file(root, "slug", 1, "topics.json")
            self.assertNotEqual(a, b)
            self.assertEqual(os.path.dirname(a), os.path.dirname(b))


if __name__ == "__main__":
    unittest.main(verbosity=2)
