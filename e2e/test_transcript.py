#!/usr/bin/env python3
"""e2e/transcript.py on a transcript of its own: one line per event, in order."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import transcript  # noqa: E402

ROWS = [
    {"type": "user", "timestamp": "2026-01-01T10:00:00Z", "origin": {"kind": "human"},
     "message": {"role": "user", "content": "/rework-mr !3"}},
    {"type": "user", "isMeta": True, "timestamp": "2026-01-01T10:00:01Z",
     "message": {"role": "user", "content": [{"type": "text", "text": "Base directory for this skill: x"}]}},
    {"type": "assistant", "timestamp": "2026-01-01T10:00:02Z", "message": {"role": "assistant", "content": [
        {"type": "text", "text": "Looking."},
        {"type": "tool_use", "id": "u1", "name": "Bash", "input": {"command": "python3 x.py sync\n--iid 3"}}]}},
    {"type": "user", "timestamp": "2026-01-01T10:00:03Z", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "u1", "is_error": True, "content": "boom\nline two"}]}},
    {"type": "system", "subtype": "informational", "timestamp": "2026-01-01T10:05:00Z",
     "content": "Prompt dropped by a hook: x"},
    {"type": "system", "subtype": "stop_hook_summary", "timestamp": "2026-01-01T10:05:01Z",
     "preventedContinuation": False, "hookErrors": []},
]


class TestLines(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "s.jsonl")
        with open(self.path, "w") as fh:
            fh.write("\n".join(json.dumps(r) for r in ROWS) + "\nnot json\n")

    def test_each_event_is_one_line_in_order(self):
        self.assertEqual(transcript.lines(self.path), [
            "[10:00] PROMPT (human): /rework-mr !3",
            "[10:00] LOADED: Base directory for this skill: x",
            "[10:00] SAY: Looking.",
            "[10:00] CALL Bash: python3 x.py sync ⏎ --iid 3",
            "[10:00]   ERROR: boom ⏎ line two",
            "[10:05] NOTE  Prompt dropped by a hook: x",
        ])

    def test_since_starts_at_that_time(self):
        self.assertEqual(transcript.lines(self.path, since="10:05"),
                         ["[10:05] NOTE  Prompt dropped by a hook: x"])

    def test_a_folder_no_session_ran_in_has_none(self):
        self.assertIsNone(transcript.session_file(self._tmp.name))

    def test_a_folder_reached_through_a_link_finds_the_sessions_of_where_it_points(self):
        real = os.path.join(os.path.realpath(self._tmp.name), "work")
        os.mkdir(real)
        link = os.path.join(self._tmp.name, "link")
        os.symlink(real, link)
        projects = os.path.join(self._tmp.name, "projects")
        filed = os.path.join(projects, "".join(c if c.isalnum() else "-" for c in real))
        os.makedirs(filed)
        with open(os.path.join(filed, "s.jsonl"), "w") as fh:
            fh.write("{}\n")
        old, transcript.PROJECTS = transcript.PROJECTS, projects
        self.addCleanup(setattr, transcript, "PROJECTS", old)
        self.assertEqual(transcript.session_file(link), os.path.join(filed, "s.jsonl"))


if __name__ == "__main__":
    unittest.main()
