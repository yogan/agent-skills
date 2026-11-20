#!/usr/bin/env python3
"""Tests for how the skills know the review-ui mod draws a session — see ui.py.

Run: `python3 lib/test_ui.py` (stdlib only).
"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib import ui  # noqa: E402


class TestDrawn(unittest.TestCase):
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


class TestRecord(TestDrawn):
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


class TestExpire(TestDrawn):
    OLD, RECENT, OURS = ("0b2c4d6e-1111-4222-8333-944455556666", "0b2c4d6e-1111-4222-8333-944455557777",
                         "0b2c4d6e-1111-4222-8333-944455558888")

    def mark(self, name, days_ago):
        path = os.path.join(self.marks, name)
        open(path, "w").close()
        at = time.time() - days_ago * 86400
        os.utime(path, (at, at))

    def test_a_view_removes_the_marks_left_unused_longer_than_a_resume_can_reach(self):
        self.mark(self.OLD, ui.MARK_DAYS + 1)
        self.mark(self.RECENT, 3)                       # a --resume after a weekend finds it
        self.mark(self.OURS, ui.MARK_DAYS + 5)          # this session's own is written afresh
        self.mark("notes.txt", ui.MARK_DAYS + 9)        # not named like a mark: not ours to remove
        with self.env(CLAUDE_CODE_SESSION_ID=self.OURS):
            ui.record({"topic": "t2"})
        self.assertEqual(sorted(os.listdir(self.marks)), sorted(["notes.txt", self.RECENT, self.OURS]))

    def test_no_view_recorded_removes_nothing(self):
        self.mark(self.OLD, ui.MARK_DAYS + 1)
        with self.env(CLAUDE_CODE_SESSION_ID=self.RECENT):     # no mark: the mod does not draw it
            ui.record({"topic": "t2"})
        self.assertEqual(os.listdir(self.marks), [self.OLD])


class TestVocabulary(unittest.TestCase):
    def test_every_state_has_an_icon_and_a_word(self):
        self.assertEqual(set(ui.STATE_ICON), set(ui.STATE_WORD))
        self.assertTrue(set(ui.CLOSED) <= set(ui.STATE_ICON))

    def test_severity_and_kind_share_one_slot(self):
        self.assertEqual(ui.kind_icon("question", "high"), ui.KIND_ICON["question"])
        self.assertEqual(ui.kind_icon("issue", "high"), ui.SEVERITY_ICON["high"])
        self.assertEqual(ui.kind_icon("issue", None), ui.NO_SEVERITY)


class TestModMirror(unittest.TestCase):
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

    def test_the_buttons_review_mr_names_after_a_verdict_are_the_mods(self):
        """SKILL.md named "Accept & resolve" and "Not good enough…", which are not on screen:
        the model then told the user to press a button that does not exist."""
        import re
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "skills", "review-mr", "SKILL.md")) as fh:
            skill = " ".join(fh.read().split())
        with open(os.path.join(root, "mods", "review-ui", "hooks", "review.ts")) as fh:
            ts = fh.read()
        named = re.search(r"The user's buttons follow the verdict(.*?)On `Analyse more`", skill)
        self.assertIsNotNone(named, "the sentence naming the buttons moved: update this test")
        labels = re.findall(r"`([A-Z][^`]*)`", named.group(1))
        self.assertGreater(len(labels), 5)
        self.assertEqual([lb for lb in labels if f"'{lb}'" not in ts], [])


class TestRecommendationMidParagraph(unittest.TestCase):
    def test_one_at_the_end_of_a_paragraph_becomes_its_own_part(self):
        from lib.ui import RECOMMENDATION_ICON, with_recommendation
        self.assertEqual(with_recommendation("It breaks. **Recommendation:** merge into t1."),
                         f"It breaks.\n\n**{RECOMMENDATION_ICON} Recommendation**\n\nMerge into t1.")


class TestClosingLine(unittest.TestCase):
    def test_a_time_today_alone_an_earlier_one_with_its_day(self):
        import datetime
        at = datetime.datetime(2026, 11, 5, 11, 38).astimezone()
        self.assertEqual(ui.at_md(at.isoformat(), today=at.date()), "11:38")
        self.assertEqual(ui.at_md(at.isoformat(), today=at.date() + datetime.timedelta(days=1)), "Nov 5, 11:38")
        self.assertIsNone(ui.at_md(None))

    def test_it_says_what_closed_it_then_when(self):
        self.assertEqual(ui.closed_md("Miriam resolved it"), f"{ui.STEP_DONE_ICON} Miriam resolved it")
        # The time leads, so the mod draws it in a column of its own. No link: a resolve is
        # no comment to open, and the comments above it each link themselves.
        self.assertRegex(ui.closed_md("Miriam resolved it", "2026-11-05T11:38:00Z", "http://gl/n1"),
                         r"^(\w{3} \d+, )?\d\d:\d\d \S+ Miriam resolved it$")


class TestStory(unittest.TestCase):
    """A topic's story on its card: every event, in the order they happened."""

    def test_events_listed_by_kind_come_out_in_time_order(self):
        events = [{"at": "2026-11-05T09:00:00Z", "icon": "c", "text": "Robin commented"},
                  {"at": "2026-11-05T12:00:00Z", "icon": "c", "text": "Robin replied"},
                  {"at": "2026-11-05T10:00:00Z", "icon": "p", "text": "You agreed the plan"},
                  {"at": "2026-11-05T11:00:00Z", "icon": "u", "text": "You pushed a fix"}]
        self.assertEqual([x.split()[-1] for x in ui.story_md(events)], ["commented", "plan", "fix", "replied"])

    def test_an_event_with_no_time_stays_behind_the_one_it_was_listed_after(self):
        events = [{"at": "2026-11-05T09:00:00Z", "icon": "c", "text": "first"},
                  {"at": None, "icon": "p", "text": "untimed"},
                  {"at": "2026-11-05T08:00:00Z", "icon": "c", "text": "earlier"}]
        self.assertEqual([x.split()[-1] for x in ui.story_md(events)], ["earlier", "first", "untimed"])

    def test_a_comment_s_word_is_its_own_link(self):
        from lib.fences import LINK_ICON
        self.assertEqual(ui.said_md("replied", "http://gl/n9"), f"[replied {LINK_ICON}](http://gl/n9)")
        self.assertEqual(ui.said_md("replied", None), "replied")

    def test_what_else_a_push_carried_is_said_short_and_apart(self):
        self.assertEqual(ui.push_aside_md(["t2"], rebase=True, mixed=True), " _(includes t2 + rebase)_")
        self.assertEqual(ui.push_aside_md([], rebase=True), " _(includes a rebase)_")
        self.assertEqual(ui.push_aside_md(["t2", "t5"]), " _(includes t2, t5)_")
        self.assertEqual(ui.push_aside_md([], mixed=True), " _(may include other changes)_")
        self.assertEqual(ui.push_aside_md([]), "")


if __name__ == "__main__":
    unittest.main()
