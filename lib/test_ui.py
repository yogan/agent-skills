#!/usr/bin/env python3
"""Tests for how the skills know the review-ui mod draws a session — see ui.py.

Run: `python3 lib/test_ui.py` (stdlib only).
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib import ui  # noqa: E402


class DrawnTest(unittest.TestCase):
    def setUp(self):
        self.marks = tempfile.mkdtemp()            # this test's own; removed whole
        self.addCleanup(shutil.rmtree, self.marks)
        patcher = patch.object(ui, "MARKS", self.marks)
        patcher.start()
        self.addCleanup(patcher.stop)

    def env(self, **kw):
        base = {k: v for k, v in os.environ.items()
                if k not in ("REVIEW_UI_DRAWN", "CLAUDE_CODE_SESSION_ID")}
        return patch.dict(os.environ, {**base, **kw}, clear=True)

    def test_a_marked_session_is_drawn(self):
        open(os.path.join(self.marks, "s1"), "w").close()
        with self.env(CLAUDE_CODE_SESSION_ID="s1"):
            self.assertTrue(ui.drawn())

    def test_an_unmarked_session_or_no_session_is_not(self):
        with self.env(CLAUDE_CODE_SESSION_ID="s1"):
            self.assertFalse(ui.drawn())
        with self.env():
            self.assertFalse(ui.drawn())

    def test_the_override_wins_both_ways(self):
        open(os.path.join(self.marks, "s1"), "w").close()
        with self.env(CLAUDE_CODE_SESSION_ID="s1", REVIEW_UI_DRAWN="0"):
            self.assertFalse(ui.drawn())
        with self.env(REVIEW_UI_DRAWN="1"):
            self.assertTrue(ui.drawn())


class RecordTest(DrawnTest):
    def test_a_drawn_session_keeps_the_last_view_in_its_mark(self):
        open(os.path.join(self.marks, "s1"), "w").close()
        with self.env(CLAUDE_CODE_SESSION_ID="s1"):
            ui.record({"topic": "t2"})
        with open(os.path.join(self.marks, "s1")) as fh:
            self.assertEqual(json.load(fh), {"topic": "t2"})

    def test_an_unmarked_session_gets_no_file(self):
        with self.env(CLAUDE_CODE_SESSION_ID="s1"):
            ui.record({"topic": "t2"})
        self.assertEqual(os.listdir(self.marks), [])

    def test_views_drawn_by_the_override_leave_no_note_in_any_session(self):
        # A stand-in run from another session's shell: that session must not take it up.
        with self.env(CLAUDE_CODE_SESSION_ID="s1", REVIEW_UI_DRAWN="1"):
            ui.record({"topic": "t2"})
        self.assertEqual(os.listdir(self.marks), [])
        open(os.path.join(self.marks, "s1"), "w").close()
        with self.env(CLAUDE_CODE_SESSION_ID="s1", REVIEW_UI_DRAWN="1"):
            ui.record({"topic": "t2"})
        with open(os.path.join(self.marks, "s1")) as fh:
            self.assertEqual(fh.read(), "")


class VocabularyTest(unittest.TestCase):
    def test_every_state_has_an_icon_and_a_word(self):
        self.assertEqual(set(ui.STATE_ICON), set(ui.STATE_WORD))
        self.assertTrue(set(ui.CLOSED) <= set(ui.STATE_ICON))

    def test_severity_and_kind_share_one_slot(self):
        self.assertEqual(ui.kind_icon("question", "high"), ui.KIND_ICON["question"])
        self.assertEqual(ui.kind_icon("issue", "high"), ui.SEVERITY_ICON["high"])
        self.assertEqual(ui.kind_icon("issue", None), ui.NO_SEVERITY)


class ModMirrorTest(unittest.TestCase):
    """The mod draws the panel with its own copy of these icons (hooks/panel.ts)."""

    def test_the_mods_icons_are_these(self):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "mods", "review-ui", "hooks", "panel.ts")
        with open(path) as fh:
            ts = fh.read()
        # The source is the cards' alone: the panel's rows no longer show it.
        for table in (ui.STATE_ICON, ui.SEVERITY_ICON, ui.KIND_ICON):
            for icon in table.values():
                self.assertIn(f"\\u{{{ord(icon):x}}}", ts)


class TestRecommendationMidParagraph(unittest.TestCase):
    def test_one_at_the_end_of_a_paragraph_becomes_its_own_part(self):
        from lib.ui import RECOMMENDATION_ICON, with_recommendation
        self.assertEqual(with_recommendation("It breaks. **Recommendation:** merge into t1."),
                         f"It breaks.\n\n**{RECOMMENDATION_ICON} Recommendation**\n\nMerge into t1.")


if __name__ == "__main__":
    unittest.main()
