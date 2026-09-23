#!/usr/bin/env python3
"""Client-specific block-manifest transport tests."""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib import critical_manifest as cm


class TestClientTransport(unittest.TestCase):
    def setUp(self):
        cm.reset()

    def test_opencode_gets_only_the_human_visible_block(self):
        with patch.dict(os.environ, {"OPENCODE": "1"}, clear=True):
            cm.mark("b")
            self.assertEqual(cm.with_manifest("a\nb"), "a\nb")

    def test_claude_gets_the_manifest(self):
        with patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": "session"}, clear=True):
            self.assertIn("<!-- paste-gate:critical\n", cm.with_manifest("a"))

    def test_override_can_enable_the_manifest_in_opencode(self):
        with patch.dict(
            os.environ, {"OPENCODE": "1", "AGENT_SKILLS_PASTE_GATE": "1"}, clear=True
        ):
            self.assertIn("<!-- paste-gate:critical\n", cm.with_manifest("a"))

    def test_override_can_disable_the_manifest_in_claude(self):
        with patch.dict(
            os.environ,
            {"CLAUDE_CODE_SESSION_ID": "session", "AGENT_SKILLS_PASTE_GATE": "0"},
            clear=True,
        ):
            self.assertEqual(cm.with_manifest("a"), "a")


if __name__ == "__main__":
    unittest.main(verbosity=2)
