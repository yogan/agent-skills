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

from lib.mr_common import (MR_LEVEL, first_name, load, loc_md, num, plain_text,  # noqa: E501
                           reads_as, save, short_summary,
                           state_file, topic_for, tref)

os.environ["REVIEW_UI_DRAWN"] = "0"   # the views as without the review-ui mod, whatever session runs this (lib/ui.py)


class TestTref(unittest.TestCase):
    def test_carries_the_topic_icon(self):
        self.assertEqual(tref("t3"), "◈ t3")


class TestLocMd(unittest.TestCase):
    """A location-less topic used to render as a bare pair of empty backticks in the
    overview table, which reads as a rendering bug rather than as "this one is about
    the merge request itself"."""

    def test_a_location_is_a_code_span(self):
        self.assertEqual(loc_md("a/b.py:42"), "`a/b.py:42`")

    def test_no_location_says_so_instead_of_empty_backticks(self):
        out = loc_md("")
        self.assertIn(MR_LEVEL, out)
        self.assertNotIn("``", out)


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


class TestHold(unittest.TestCase):
    """Two runs that write the same state take turns: the second waits until the first is
    done, so neither overwrites the other's write with an older copy."""

    RUN = ("import sys; sys.path.insert(0, {root!r}); from lib.mr_common import hold; "
           "hold({path!r}); print('held', flush=True); sys.stdin.readline()")

    def test_a_second_run_waits_for_the_first(self):
        import subprocess
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "state.json")
            code = self.RUN.format(root=root, path=path)
            first = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, text=True)
            self.assertEqual(first.stdout.readline().strip(), "held")
            second = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, text=True)
            with self.assertRaises(subprocess.TimeoutExpired):
                second.wait(timeout=0.5)              # still waiting on the first
            first.communicate("\n")
            self.assertEqual(second.stdout.readline().strip(), "held")
            second.communicate("\n")

    def test_a_run_holding_it_already_goes_on(self):
        import subprocess
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "state.json")
            code = (f"import sys; sys.path.insert(0, {root!r}); from lib.mr_common import hold; "
                    f"hold({path!r}); hold({path!r}); print('held twice')")
            run = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                                 timeout=10)
            self.assertEqual(run.stdout.strip(), "held twice")

    def test_save_replaces_the_file_whole(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "state.json")
            save(path, {"a": 1})
            save(path, {"b": 2})
            self.assertEqual(load(path), {"b": 2})
            self.assertEqual(sorted(os.listdir(d)), ["state.json"])     # no temp file left


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

    def test_the_fallback_is_flattened_markdown(self):
        """A reviewer's comment is markdown. Its first 70 characters raw put a code
        fence where a title belongs — unreadable, and it breaks the table row."""
        state = {"threads": {"d1": {"body": "Hier passt eine **Generator Function**:\n\n"
                                            "```py\nfrom collections import abc\n```"}}}
        out = short_summary(state, {"thread_ids": ["d1"]})
        self.assertEqual(out, "Hier passt eine Generator Function:")

    def test_a_comment_that_is_only_a_suggestion_says_so(self):
        """Showing the reviewer's proposed code as if it were their point is worse than
        admitting there is no prose to show."""
        state = {"threads": {"d1": {"body": "```suggestion:-6+0\nREQUIRED = {1}\n```"}}}
        self.assertEqual(short_summary(state, {"thread_ids": ["d1"]}),
                         "(no prose — code only)")

    def test_an_authored_summary_keeps_its_own_markup(self):
        """Only the FALLBACK is flattened: a title someone wrote may well name an
        identifier in backticks, and that renders fine where it is shown."""
        t = {"summary": "`retry()` loops forever"}
        self.assertEqual(short_summary({"threads": {}}, t), "`retry()` loops forever")


class TestPlainText(unittest.TestCase):
    def test_a_bare_suggestion_marker_is_dropped(self):
        """GitLab sometimes stores the marker without its fence; `suggestion:-1+0` alone
        was showing up as a whole topic's title."""
        self.assertEqual(plain_text("suggestion:-1+0"), "")

    def test_strikethrough_and_emphasis_unwrap(self):
        self.assertEqual(plain_text("~~retracted~~ but *this* stands"),
                         "retracted but this stands")

    def test_a_link_keeps_its_text(self):
        self.assertEqual(plain_text("see [the docs](https://example.test/x)"),
                         "see the docs")

    def test_quote_and_list_markers_go(self):
        self.assertEqual(plain_text("> - first point\n> - second"),
                         "first point second")

    def test_a_nested_fence_ends_at_the_longer_marker(self):
        """A comment quoting a ``` block wraps it in ````. Stopping at the first
        bare-backtick line ended the block early and swallowed the prose after it."""
        self.assertEqual(plain_text("so gehts:\n````md\n```py\nx=1\n```\n````\nklar?"),
                         "so gehts: klar?")

    def test_an_underscore_inside_a_word_is_literal(self):
        """`snake_case` identifiers and URLs are all over a reviewer's comment. Treating
        `_` like `*` turned `#note_1 … merge_requests` into `#note1 … mergerequests`."""
        self.assertEqual(plain_text("siehe https://gl.test/x/-/merge_requests/1#note_1"),
                         "siehe https://gl.test/x/-/merge_requests/1#note_1")
        self.assertEqual(plain_text("das feld x_field_prompt fehlt"),
                         "das feld x_field_prompt fehlt")

    def test_a_real_italic_still_unwraps(self):
        self.assertEqual(plain_text("das ist _wirklich_ so"), "das ist wirklich so")

    def test_a_markdown_table_loses_its_pipes(self):
        """Pipes would have to be escaped again by the table cell this lands in, and the
        rule line carries nothing at all."""
        self.assertEqual(plain_text("| a | b |\n|---|---|\n| 1 | 2 |\n\nsiehe oben"),
                         "a b 1 2 siehe oben")

    def test_an_unclosed_fence_takes_the_rest_with_it(self):
        """A comment that opens a fence and never closes it is the common case in a
        truncated paste; everything after the marker is code, not prose."""
        self.assertEqual(plain_text("hier:\n```py\nx = 1\ny = 2"), "hier:")


class TestReadsAs(unittest.TestCase):
    """Deliberately hard to trip: it decides whether a human has to rewrite a summary."""

    def test_two_german_function_words_are_enough(self):
        self.assertTrue(reads_as("Das Feld wird nicht mehr gesetzt", "de"))

    def test_one_is_not(self):
        self.assertFalse(reads_as("der retry loop never terminates", "de"))

    def test_an_identifier_in_backticks_is_not_evidence(self):
        self.assertFalse(reads_as("`von_der_liste` is never cleared", "de"))

    def test_an_unknown_language_is_never_checked(self):
        self.assertFalse(reads_as("le champ nest pas rempli", "fr"))

    def test_an_all_caps_token_is_not_a_function_word(self):
        """MIT, DES, AUS, DEM lowercase straight into the list."""
        self.assertFalse(reads_as("DES and MIT are constants here", "de"))


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
