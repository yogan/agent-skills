#!/usr/bin/env python3
"""Tests for the block manifest mechanism — see critical_manifest.py's module docstring
and hooks/README.md's "The block manifest" section for the mechanism this is part of.

Run: `python3 lib/test_critical_manifest.py` (stdlib only).
"""
import json
import os
import sys
import unittest

os.environ["AGENT_SKILLS_PASTE_GATE"] = "1"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib import critical_manifest as cm


class TestCriticalManifest(unittest.TestCase):
    def setUp(self):
        cm.reset()

    def test_mark_records_and_returns_unchanged(self):
        line = cm.mark("  padded  ")
        self.assertEqual(line, "  padded  ")           # returned value is untouched
        self.assertEqual(cm.current(), ["padded"])      # recorded value is stripped

    def payload_of(self, out):
        self.assertTrue(out.endswith("\n-->"), out)
        return json.loads(out.split("<!-- paste-gate:critical\n", 1)[1].rsplit("\n-->", 1)[0])

    def test_block_comes_through_untouched_with_the_payload_appended(self):
        cm.mark("b")
        out = cm.with_manifest("a\nb\nc")
        self.assertTrue(out.startswith("a\nb\nc\n\n<!-- paste-gate:critical\n"))
        self.assertEqual(self.payload_of(out), {"first": "a", "critical": ["b"]})

    def test_first_is_the_opening_line_that_carries_text(self):
        """It is what the hook looks for to find where the block begins inside a tool
        result that also holds whatever ran before it, so a leading blank line — or the
        indentation on the first real one — must not be what it searches for."""
        self.assertEqual(self.payload_of(cm.with_manifest("\n\n   **MR !7** — x\nrest"))
                         ["first"], "**MR !7** — x")

    def test_emitted_even_when_nothing_was_marked_critical(self):
        """`first` alone earns the payload: a block with no critical lines is exactly as
        likely to have a linter's output printed ahead of it as any other."""
        self.assertEqual(self.payload_of(cm.with_manifest("just prose")),
                         {"first": "just prose", "critical": []})

    def test_an_empty_block_gets_no_payload(self):
        """Nothing was printed, so there is nothing to locate or protect — and a marker
        with no block in front of it would be pure noise in the tool result."""
        self.assertEqual(cm.with_manifest(""), "")
        self.assertEqual(cm.with_manifest("\n  \n"), "\n  \n")

    def test_reset_clears_between_calls(self):
        cm.mark("a")
        cm.reset()
        self.assertEqual(cm.current(), [])
        self.assertEqual(self.payload_of(cm.with_manifest("x"))["critical"], [])

    def test_suspended_discards_what_is_marked_inside_it(self):
        """A render produced to be COMPARED, not shown: its code lines must not reach the
        manifest, or the hook demands lines that are nowhere in the message."""
        cm.mark("shown")
        with cm.suspended():
            cm.mark("compared only")
            self.assertEqual(cm.current(), ["compared only"])
        self.assertEqual(cm.current(), ["shown"])

    def test_suspended_restores_even_when_the_block_raises(self):
        cm.mark("shown")
        with self.assertRaises(ValueError):
            with cm.suspended():
                cm.mark("compared only")
                raise ValueError("a render that died mid-way")
        self.assertEqual(cm.current(), ["shown"])

    def test_current_is_a_snapshot_not_a_live_reference(self):
        cm.mark("a")
        snap = cm.current()
        snap.append("b")
        self.assertEqual(cm.current(), ["a"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
