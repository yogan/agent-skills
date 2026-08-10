#!/usr/bin/env python3
"""Tests for the critical-lines manifest mechanism — see critical_manifest.py's module
docstring and hooks/README.md's "The critical-lines manifest" section for the mechanism
this is part of.

Run: `python3 lib/test_critical_manifest.py` (stdlib only).
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib import critical_manifest as cm


class TestCriticalManifest(unittest.TestCase):
    def setUp(self):
        cm.reset()

    def test_mark_records_and_returns_unchanged(self):
        line = cm.mark("  padded  ")
        self.assertEqual(line, "  padded  ")           # returned value is untouched
        self.assertEqual(cm.current(), ["padded"])      # recorded value is stripped

    def test_manifest_empty_when_nothing_marked(self):
        self.assertEqual(cm.manifest(), "")

    def test_manifest_carries_every_marked_line_as_json(self):
        cm.mark("a")
        cm.mark("b")
        payload = cm.manifest()
        self.assertTrue(payload.startswith("\n\n<!-- paste-gate:critical\n"))
        self.assertTrue(payload.endswith("\n-->"))
        body = payload.split("<!-- paste-gate:critical\n", 1)[1].rsplit("\n-->", 1)[0]
        self.assertEqual(json.loads(body), ["a", "b"])

    def test_reset_clears_between_calls(self):
        cm.mark("a")
        cm.reset()
        self.assertEqual(cm.current(), [])
        self.assertEqual(cm.manifest(), "")

    def test_current_is_a_snapshot_not_a_live_reference(self):
        cm.mark("a")
        snap = cm.current()
        snap.append("b")
        self.assertEqual(cm.current(), ["a"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
