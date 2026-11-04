#!/usr/bin/env python3
"""Tests for how drive.py reads a screen: where the panel is, where to click, what a step is.

Run: `python3 e2e/test_drive.py` (stdlib only; no tmux, no claude).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import drive  # noqa: E402

WIDTH = 120
SPLIT = 70      # where the panel's divider sits in the screens below


def screen(left, right=None, rows=30):
    """A screen `rows` high: `left` lines in the transcript, `right` ones in a docked panel."""
    out = []
    for i in range(rows):
        a = left[i] if i < len(left) else ""
        if right is None:
            out.append(a)
        else:
            b = right[i] if i < len(right) else ""
            out.append(a.ljust(SPLIT) + "│" + b)
    return "\n".join(out)


PROMPT = ["─" * WIDTH, "❯ ", "─" * WIDTH]


class PanelTest(unittest.TestCase):
    def test_closed_without_a_divider(self):
        s = screen(["⏺ Hello"] + [""] * 25 + PROMPT)
        self.assertIsNone(drive.divider_column(s))

    def test_open_with_a_divider_down_most_rows(self):
        s = screen(["⏺ Hello"], [f"row {i}" for i in range(27)])
        self.assertEqual(drive.divider_column(s), SPLIT)

    def test_a_card_in_the_transcript_is_not_a_panel(self):
        # A bordered card has rules over its own rows only, and near the left edge.
        card = ["╭" + "─" * 40] + ["│ line"] * 8 + ["╰" + "─" * 40]
        s = screen(card + [""] * 16 + PROMPT)
        self.assertIsNone(drive.divider_column(s))

    def test_the_prompt_box_sides_are_not_a_panel(self):
        boxed = ["│" + " " * (WIDTH - 2) + "│"] * 30
        self.assertIsNone(drive.divider_column("\n".join(boxed)))

    def test_wide_glyphs_left_of_the_divider_keep_its_column(self):
        # A wide emoji takes two cells: the divider is found by cell, not by character.
        lines = []
        for i in range(30):
            a = "✅ done" if i % 2 else "ok done"
            lines.append(a + " " * (SPLIT - 1 - len(a) - (1 if i % 2 else 0)) + " │panel")
        self.assertEqual(drive.divider_column("\n".join(lines)), SPLIT)

    def test_regions_split_at_the_divider(self):
        s = screen(["⏺ in the transcript"], ["IN THE PANEL"] + ["x"] * 26)
        self.assertIn("IN THE PANEL", drive.region_of(s, "panel"))
        self.assertNotIn("IN THE PANEL", drive.region_of(s, "main"))
        self.assertIn("in the transcript", drive.region_of(s, "main"))
        self.assertNotIn("in the transcript", drive.region_of(s, "panel"))

    def test_a_closed_panel_has_an_empty_region(self):
        s = screen(["⏺ in the transcript"])
        self.assertEqual(drive.region_of(s, "panel"), "")
        self.assertIn("in the transcript", drive.region_of(s, "main"))


class FindTextTest(unittest.TestCase):
    def test_row_and_column_are_zero_based_cells(self):
        s = "first\n  ✕ close\n"
        self.assertEqual(drive.find_text(s, "✕"), (1, 2))

    def test_a_wide_glyph_before_the_text_moves_it_two_cells(self):
        self.assertEqual(drive.find_text("✅ Post", "Post"), (0, 3))

    def test_missing_text_is_none(self):
        self.assertIsNone(drive.find_text("nothing here", "✕"))


class PromptBoxTest(unittest.TestCase):
    def test_claudes_prompt_is_a_prompt_line_under_a_rule(self):
        self.assertTrue(drive.has_prompt_box("⏺ hi\n" + "─" * 80 + "\n❯ \n" + "─" * 80))

    def test_a_shell_prompt_is_not(self):
        self.assertFalse(drive.has_prompt_box("~/src/demo main\n❯ clear; claude --model sonnet"))


class StepTest(unittest.TestCase):
    def test_every_scenario_step_is_one_the_driver_runs(self):
        import scenarios
        known = (drive.Prompt, drive.Type, drive.Keys, drive.Click, drive.Expect,
                 drive.Panel, drive.Say, drive.Handover)
        for sc in scenarios.SCENARIOS:
            for step in sc.steps:
                self.assertIsInstance(step, known, f"{sc.name}: {step!r}")

    def test_scenario_names_are_unique(self):
        import scenarios
        names = [sc.name for sc in scenarios.SCENARIOS]
        self.assertEqual(len(names), len(set(names)))

    def test_an_unknown_step_fails_instead_of_passing(self):
        class Stranger(drive.Step):
            pass
        with self.assertRaises(drive.StepFailed):
            drive.run_step(None, Stranger(), verify=True)

    def test_a_verify_run_leaves_the_users_settings_out_and_brings_the_skills_in(self):
        sc = drive.Scenario("x", "x", cwd=None, steps=[], mods=["~/m"])
        cmd = drive.claude_command(sc, "haiku", verify=True, plugins=["/run/skills-plugin"])
        self.assertIn("--setting-sources project,local", cmd)
        self.assertIn("--plugin-dir /run/skills-plugin", cmd)
        self.assertIn(f"--plugin-dir {os.path.expanduser('~/m')}", cmd)
        self.assertIn("--model haiku", cmd)

    def test_a_showcase_keeps_the_users_settings_and_model(self):
        sc = drive.Scenario("x", "x", cwd=None, steps=[])
        cmd = drive.claude_command(sc, None, verify=False)
        self.assertNotIn("--setting-sources", cmd)
        self.assertNotIn("--model", cmd)


if __name__ == "__main__":
    unittest.main()
