#!/usr/bin/env python3
"""Tests for how drive.py reads a screen: where the panel is, where to click, what a step is.

Run: `python3 e2e/test_drive.py` (stdlib only; no tmux, no claude).
"""
import io
import os
import sys
import unittest
from unittest import mock

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


class TestPanel(unittest.TestCase):
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

    def test_cards_bordering_the_divider_on_most_rows_do_not_take_its_place(self):
        # Their right border stands one column left of the divider; a sidebar cut there
        # loses every row between two cards, and keeps the divider as its first column.
        inner = SPLIT - 2
        card = ["╭" + "─" * inner + "╮"] + ["│" + " " * inner + "│"] * 6 + ["╰" + "─" * inner + "╯", ""]
        s = screen((card * 4)[:30], [f"row {i}" for i in range(30)])
        self.assertEqual(drive.divider_column(s), SPLIT)
        self.assertEqual(drive.region_of(s, "panel").split("\n")[:9], [f"row {i}" for i in range(9)])

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


class TestFindText(unittest.TestCase):
    def test_row_and_column_are_zero_based_cells(self):
        s = "first\n  ✕ close\n"
        self.assertEqual(drive.find_text(s, "✕"), (1, 2))

    def test_a_wide_glyph_before_the_text_moves_it_two_cells(self):
        self.assertEqual(drive.find_text("✅ Post", "Post"), (0, 3))

    def test_missing_text_is_none(self):
        self.assertIsNone(drive.find_text("nothing here", "✕"))

    def test_the_panel_s_own_is_found_past_the_chat_s(self):
        s = screen(["⏺ t1 is done"], [f"row {i}" for i in range(26)] + ["▌ t1 summary"])
        self.assertEqual(drive.find_text(s, " t1 ")[1], 1)
        row, col = drive.find_text(s, " t1 ", "panel")
        self.assertGreater(col, SPLIT)
        self.assertIn("summary", s.split("\n")[row])
        self.assertIsNone(drive.find_text(screen(["⏺ t1 is done"] + [""] * 25 + PROMPT), " t1 ", "panel"))


class TestPromptBox(unittest.TestCase):
    def test_claudes_prompt_is_a_prompt_line_under_a_rule(self):
        self.assertTrue(drive.has_prompt_box("⏺ hi\n" + "─" * 80 + "\n❯ \n" + "─" * 80))

    def test_a_shell_prompt_is_not(self):
        self.assertFalse(drive.has_prompt_box("~/src/demo main\n❯ clear; claude --model sonnet"))


class TestStep(unittest.TestCase):
    def test_every_scenario_step_is_one_the_driver_runs(self):
        import scenarios
        known = (drive.Prompt, drive.Type, drive.Keys, drive.Click, drive.Expect, drive.Check,
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

    def test_a_real_model_run_has_the_mod_keep_its_own_calls_for_the_recording(self):
        sc = drive.Scenario("x", "x", cwd=None, steps=[])
        cmd = drive.claude_command(sc, "sonnet", verify=True, calls="/run/calls.json")
        self.assertIn("REVIEW_UI_CALLS=/run/calls.json", cmd)
        self.assertNotIn("REVIEW_UI_CALLS", drive.claude_command(sc, "sonnet", verify=True))

    def test_a_showcase_keeps_the_users_settings_and_model(self):
        sc = drive.Scenario("x", "x", cwd=None, steps=[], mods=["~/m"])
        cmd = drive.claude_command(sc, None, verify=False)
        self.assertNotIn("--setting-sources", cmd)
        self.assertNotIn("--plugin-dir", cmd)
        self.assertNotIn("--model", cmd)


class TestEnginePrompt(unittest.TestCase):
    def test_the_engines_own_question_is_answered_before_a_step_reads_the_screen(self):
        screens = ["cards\n  Teach auto mode about your environment?\n  1. Yes\n  2. Not now",
                   "cards\n  ❯ "]
        sent, logged = [], []

        def fake(*args, check=True):
            if args[0] == "capture-pane":
                return screens.pop(0) if len(screens) > 1 else screens[0]
            sent.append(args)
            return ""
        old, drive.tmux = drive.tmux, fake
        self.addCleanup(setattr, drive, "tmux", old)
        old_sleep, drive.time.sleep = drive.time.sleep, lambda s: None
        self.addCleanup(setattr, drive.time, "sleep", old_sleep)
        self.assertEqual(drive.Pane("t", log=logged.append).screen(), "cards\n  ❯ ")
        self.assertEqual(sent, [("send-keys", "-t", "t", "2")])
        self.assertTrue(logged)


class TestRecording(unittest.TestCase):
    REC = {"streams": [
        {"name": "main", "first": "go", "replies": [
            {"prompts": ["go"], "content": [{"type": "tool_use", "id": "a", "name": "Bash", "input": {}}]},
            {"content": [{"type": "tool_use", "id": "b", "name": "Edit", "input": {}},
                         {"type": "text", "text": "done"}]}]},
        {"name": "agent: judge", "first": "judge", "replies": [
            {"prompts": ["judge"], "content": [{"type": "tool_use", "id": "c", "name": "Bash", "input": {}}]}]}]}

    def test_a_run_on_the_recording_may_use_exactly_the_recorded_tools_and_nothing_asks(self):
        sc = drive.Scenario("x", "x", cwd=None, steps=[])
        env = {"ANTHROPIC_BASE_URL": "http://127.0.0.1:9"}
        cmd = drive.claude_command(sc, "mock", verify=True,
                                   mock=(env, drive.recorded_tools(self.REC)))
        self.assertIn("--permission-mode dontAsk --allowedTools Bash,Edit", cmd)
        self.assertIn("ANTHROPIC_BASE_URL=http://127.0.0.1:9", cmd)
        self.assertNotIn("--model", cmd)
        self.assertNotIn("python3 *", cmd)          # the real-model allow list is not added

    def test_recording_a_scenario_that_plays_anothers_is_refused_before_it_runs(self):
        """It was saved under its own name, which nothing plays, after a paid run."""
        with mock.patch.object(drive, "verify") as ran, self.assertRaises(SystemExit) as stop:
            drive.main(["verify", "rework-pipeline", "--model", "haiku", "--record"])
        self.assertIn("record rework-fix", str(stop.exception))
        ran.assert_not_called()

    def test_every_scenario_that_runs_unattended_has_a_recording(self):
        """Looked up the way `verify` looks it up: a scenario that plays another's recording
        needs that one, and a missing recording would otherwise surface only at the push level."""
        import scenarios
        missing = [sc.name for sc in scenarios.SCENARIOS
                   if not any(isinstance(s, drive.Handover) for s in sc.steps)
                   and not os.path.isfile(drive.mock_model.recording_path(sc.recording or sc.name))]
        self.assertEqual(missing, [])

    def test_a_command_is_split_where_the_shell_splits_it(self):
        self.assertEqual(drive.segments(
            'SD=x; T="python3 $SD/t.py"; $T reply t1 --text "a; b | c" && echo "$(pwd)"\n'
            "cat <<'EOF' | python3 $SD/t.py set\nnpm install x; make clean\nEOF"),
            ['$T reply t1 --text "a; b | c"', "pwd", 'echo "$(…)"',
             "cat <<'EOF'", "python3 $SD/t.py set"])

    def test_improvised_lists_what_is_not_the_skills_own_work(self):
        import json
        import tempfile
        calls = ["SD=~/.claude/skills/rework-mr/scripts; python3 $SD/threads.py sync --iid 3",
                 "cd /w && git status && glab mr view 3 | head -5",
                 'for t in t1 t2; do python3 $SD/threads.py set "$t" --iid 3; done; pwd',
                 "npm install -g left-pad",
                 "python3 -c 'import os'; npm install -g left-pad"]
        lines = [{"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": c}}]}} for c in calls]
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
            fh.write("\n".join(json.dumps(x) for x in lines))
        self.addCleanup(os.remove, fh.name)
        self.assertEqual(drive.improvised(fh.name),
                         ["npm install -g left-pad", "python3 -c 'import os'"])


class TestOutput(unittest.TestCase):
    def test_each_line_reaches_a_file_as_it_is_printed(self):
        """A file gets block-buffered output, so a log of a running verify stayed empty to
        the end. Each line has to be there before anything flushes."""
        raw = io.BytesIO()
        out = io.TextIOWrapper(raw, encoding="utf-8")
        with mock.patch.object(sys, "stdout", out):
            drive.main(["list"])
            written = raw.getvalue()
        self.assertIn(b"smoke", written)


class Clock:
    """Time that only passes when the driver sleeps, and a screen that changes with it."""

    def __init__(self, test, screens):
        """`screens`: (from this second on, the screen), in order."""
        self.now, self.screens, self.clicks = 0.0, screens, []
        for name, fake in (("time", lambda: self.now), ("sleep", self.sleep)):
            test.addCleanup(setattr, drive.time, name, getattr(drive.time, name))
            setattr(drive.time, name, fake)
        test.addCleanup(setattr, drive, "tmux", drive.tmux)
        drive.tmux = self.tmux

    def sleep(self, seconds):
        self.now += seconds

    def tmux(self, *args, check=True):
        if args[0] == "capture-pane":
            return [s for t, s in self.screens if t <= self.now][-1]
        self.clicks.append(self.now)
        return ""


class TestWait(unittest.TestCase):
    """The driver waits for what the screen says, not for a fixed time."""
    BUTTON = " Agree plan \ue0b4"

    def card(self, row):
        return "\n".join(["╭─ t1"] + [""] * row + [f"│ {self.BUTTON}"])

    def test_a_button_is_clicked_once_it_has_stood_still(self):
        # Drawn at second 1, moved down by a redraw at 1.2: found only where it stays.
        Clock(self, [(0, "╭─ t1"), (1, self.card(2)), (1.2, self.card(5))])
        self.assertEqual(drive.Pane("t").standing(self.BUTTON, "screen"), (6, 2))

    def test_a_button_whose_row_is_redrawn_is_not_standing_yet(self):
        busy = self.card(2) + "  ⠋"
        clock = Clock(self, [(0, busy), (0.3, self.card(2))])
        drive.Pane("t").standing(self.BUTTON, "screen")
        self.assertGreaterEqual(clock.now, 0.3 + drive.STANDS)

    def test_a_button_that_never_shows_is_none_after_the_grace(self):
        clock = Clock(self, [(0, "╭─ t1")])
        self.assertIsNone(drive.Pane("t").standing(self.BUTTON, "screen"))
        self.assertGreaterEqual(clock.now, drive.GRACE)

    def test_a_click_the_screen_moved_away_from_is_pressed_again_where_the_button_went(self):
        # A background agent's two lines arrive as the click is sent: the card moves up two rows.
        card = ["╭─ t1", "│ rename it", "│ in 3 files", f"│ {self.BUTTON}", "╰─", "", "", "❯"]
        moved = card[2:] + ["  ⏺ main", "  ◯ Judge news"]
        clock = Clock(self, [(0, "\n".join(card)), (0.05, "\n".join(moved))])
        at = drive.Pane("t").press((3, 2), self.BUTTON, "screen")
        self.assertEqual(at, (1, 2))
        self.assertEqual(len(clock.clicks), 2)

    def test_a_press_whose_prompt_moves_the_transcript_is_not_pressed_again(self):
        # Its button stays while the prompt it sent is written under the card, which moves
        # the card up two rows as a background agent's lines would.
        card = ["╭─ t1", "│ rename it", "│ in 3 files", f"│ {self.BUTTON}", "╰─", "", "", "❯"]
        moved = card[2:5] + ["", drive.PLUGIN_PROMPT, "❯ Draft the reply for t1.", "", "❯"]
        clock = Clock(self, [(0, "\n".join(card)), (0.05, "\n".join(moved))])
        drive.Pane("t").press((3, 2), self.BUTTON, "screen")
        self.assertEqual(len(clock.clicks), 1)

    def test_a_press_that_landed_is_not_pressed_again(self):
        clock = Clock(self, [(0, self.card(2)), (0.05, "╭─ t1")])
        drive.Pane("t").press((3, 2), self.BUTTON, "screen")
        self.assertEqual(len(clock.clicks), 1)

    def test_the_next_cards_button_elsewhere_is_not_the_one_missed(self):
        # The press landed and t2's card came with its own Agree plan, two rows higher.
        t2 = "\n".join(["╭─ t2", "│ refetch the detail", f"│ {self.BUTTON}"])
        clock = Clock(self, [(0, self.card(2)), (0.05, t2)])
        drive.Pane("t").press((3, 2), self.BUTTON, "screen")
        self.assertEqual(len(clock.clicks), 1)

    def test_a_press_is_through_when_its_button_has_left_the_screen(self):
        clock = Clock(self, [(0, self.card(2)), (0.2, "╭─ t1")])
        drive.Pane("t").pressed((3, 2), self.BUTTON, "screen", hurry=True)
        self.assertLess(clock.now, 1)

    def test_a_press_that_leaves_its_text_standing_gets_the_whole_pause(self):
        clock = Clock(self, [(0, self.card(2))])
        drive.Pane("t").pressed((3, 2), self.BUTTON, "screen", hurry=True)
        self.assertGreaterEqual(clock.now, drive.PRESS_TAKEN)

    def test_a_press_before_a_step_that_looks_once_gets_the_whole_pause(self):
        clock = Clock(self, [(0, self.card(2)), (0.2, "╭─ t1")])
        drive.Pane("t").pressed((3, 2), self.BUTTON, "screen", hurry=False)
        self.assertGreaterEqual(clock.now, drive.PRESS_TAKEN)

    def test_only_a_step_that_keeps_looking_does_the_waiting_for_a_press(self):
        self.assertTrue(drive.waits(drive.Expect("x", region="screen")))
        self.assertTrue(drive.waits(drive.Panel(False)))
        self.assertTrue(drive.waits(drive.Check("glab api x", "y", wait=30)))
        # Not there yet is "not there" to these, and typing or clicking on is no look at all.
        self.assertFalse(drive.waits(drive.Expect("x", region="screen", absent=True)))
        self.assertFalse(drive.waits(drive.Expect("x")))
        self.assertFalse(drive.waits(drive.Check("news.py approve", "approved")))
        self.assertFalse(drive.waits(drive.Prompt("x")))
        self.assertFalse(drive.waits(drive.Click("x")))
        self.assertFalse(drive.waits(None))

    def test_what_a_press_brings_is_found_when_it_arrives_late(self):
        Clock(self, [(0, "╭─ t1"), (4, self.card(2))])
        drive.run_step(drive.Pane("t"), drive.Expect("Agree plan", region="screen"), verify=True)

    def test_something_absent_is_judged_on_a_screen_that_stands_still(self):
        # Not there at first, drawn half a second later: found, where one early look would not.
        Clock(self, [(0, "╭─ t1"), (0.5, self.card(2))])
        with self.assertRaises(drive.StepFailed):
            drive.run_step(drive.Pane("t"), drive.Expect("Agree plan", region="screen", absent=True),
                           verify=True)

    def test_a_check_that_only_looks_asks_again_until_its_time_is_up(self):
        clock = Clock(self, [(0, "")])
        outputs = ["1 miriam", "1 miriam", "2 frank"]
        run = mock.Mock(side_effect=lambda *a, **k: mock.Mock(stdout=outputs.pop(0), stderr=""))
        with mock.patch.object(drive.subprocess, "run", run):
            drive.run_step(drive.Pane("t"), drive.Check("look", r"^2 frank$", wait=30), verify=True)
            self.assertEqual(run.call_count, 3)
            outputs[:] = ["1 miriam"]
            with self.assertRaises(drive.StepFailed):      # and one that acts runs once
                drive.run_step(drive.Pane("t"), drive.Check("act", r"^2 frank$"), verify=True)
        self.assertLess(clock.now, 30)


class TestLaunch(unittest.TestCase):
    """A launch in claude's fallback interface is not one a scenario can run in."""
    NOTE = ("Claude Code's fullscreen renderer didn't finish starting last time on this machine, "
            "so this launch is using the classic renderer.")

    def pane(self, histories):
        pane = mock.Mock()
        pane.history.side_effect = histories
        return pane

    def test_a_fullscreen_launch_is_used_as_it_is(self):
        again = mock.Mock()
        drive.ready_in_fullscreen(self.pane(["❯ "]), again)
        again.assert_not_called()

    def test_a_launch_in_the_fallback_interface_is_launched_again_and_says_so(self):
        again, pane = mock.Mock(), self.pane([self.NOTE, "❯ "])
        with mock.patch("builtins.print") as said:
            drive.ready_in_fullscreen(pane, again)
        again.assert_called_once()
        self.assertEqual(pane.wait_ready.call_count, 2)
        self.assertIn("launched again", said.call_args.args[0])

    def test_the_fallback_twice_in_a_row_fails_the_run(self):
        with mock.patch("builtins.print"), self.assertRaises(drive.StepFailed):
            drive.ready_in_fullscreen(self.pane([self.NOTE, self.NOTE]), mock.Mock())


class TestCopy(unittest.TestCase):
    """A scenario on the rig runs in a copy of the rig that is its alone."""

    def scenario(self, name, cwd="~/src/agent-skills-demo-review-mr1"):
        return drive.Scenario(name, "x", cwd=cwd, steps=[], needs_rig=True)

    def test_two_scenarios_share_neither_project_nor_folder(self):
        a, b = drive.copy_of(self.scenario("rework-fix")), drive.copy_of(self.scenario("rework-mind"))
        self.assertEqual(a, ("demo/e2e-rework-fix", "~/src/agent-skills-e2e/rework-fix"))
        self.assertNotEqual(a[0], b[0])
        self.assertNotEqual(a[1], b[1])
        self.assertNotEqual(a[0], drive.DEMO_PROJECT)       # nor the demo's with anyone

    def test_its_folder_is_the_copys_checkout_of_the_same_name(self):
        sc = self.scenario("review-1")
        cwd, _ = drive.workdir(sc, drive.copy_of(sc)[1])
        self.assertEqual(cwd, os.path.expanduser("~/src/agent-skills-e2e/review-1/agent-skills-demo-review-mr1"))
        # A showcase runs where the scenario says: the demo's own checkout.
        self.assertEqual(drive.workdir(sc)[0], os.path.expanduser("~/src/agent-skills-demo-review-mr1"))

    def test_a_steps_command_reaches_the_copy_through_its_environment(self):
        env = drive.rig_env(*drive.copy_of(self.scenario("rework-fix")))
        self.assertEqual((env["E2E_PROJECT"], env["P"], env["SLUG"]),
                         ("demo/e2e-rework-fix", "demo%2Fe2e-rework-fix", "demo-e2e-rework-fix"))
        ran = mock.Mock(return_value=mock.Mock(stdout="ok", stderr=""))
        with mock.patch.object(drive.subprocess, "run", ran):
            drive.run_step(drive.Pane("t", env=env), drive.Check("glab api projects/$P", "ok"), verify=True)
        self.assertEqual(ran.call_args.kwargs["env"]["P"], "demo%2Fe2e-rework-fix")

    def test_no_scenario_names_the_demos_project_in_a_command(self):
        # It would read or change the demo's merge request from a copy's run.
        import scenarios
        for sc in scenarios.SCENARIOS:
            for step in sc.steps:
                if isinstance(step, drive.Check):
                    self.assertNotIn("bulletproof-react", step.command, sc.name)

    def test_a_verify_runs_skills_keep_their_state_in_the_runs_own_place(self):
        # Never in ~/.claude, where every scenario run once left a folder per merge request.
        sys.path.insert(0, str(drive.E2E.parent))
        from lib.mr_common import STATE_ENV
        self.assertEqual(drive.STATE_ENV, STATE_ENV)
        sc, out = self.scenario("rework-fix"), drive.Path("/run/x")
        state = drive.state_of(sc, out)
        self.assertEqual(state, os.path.expanduser("~/src/agent-skills-e2e/rework-fix/state"))
        self.assertEqual(drive.state_of(drive.Scenario("smoke", "x", cwd=None, steps=[]), out), "/run/x/state")
        env = drive.rig_env(*drive.copy_of(sc), state=state)
        self.assertEqual(env[STATE_ENV], state)                 # for the reset and a step's command
        self.assertIn(f"{STATE_ENV}={state}", drive.claude_command(sc, "mock", verify=True, state=state))
        self.assertNotIn(STATE_ENV, drive.rig_env())            # the demo's is the person's own

    def test_the_reset_builds_the_scenarios_copy_not_the_demo(self):
        ran = mock.Mock(return_value=mock.Mock(returncode=0, stdout="", stderr=""))
        env = drive.rig_env(*drive.copy_of(self.scenario("rework-fix")))
        with mock.patch.object(drive.subprocess, "run", ran), mock.patch.object(drive.Path, "write_text"):
            drive.reset_rig(drive.Path("/nowhere"), 3, env)
        self.assertEqual(ran.call_args.kwargs["env"]["E2E_PROJECT"], "demo/e2e-rework-fix")
        self.assertEqual(ran.call_args.kwargs["env"]["E2E_SRC"], "~/src/agent-skills-e2e/rework-fix")


class TestReset(unittest.TestCase):
    def test_every_rig_scenario_names_the_merge_request_it_works_on(self):
        # One that named none would get the whole reset: slower, and nothing says so.
        import scenarios
        got = {sc.name: sc.mr() for sc in scenarios.SCENARIOS if sc.needs_rig}
        self.assertEqual({n for n, mr in got.items() if mr is None}, set())
        self.assertEqual((got["review-1"], got["rereview-2"], got["rework-fix"], got["try-scrolling"]),
                         (1, 2, 3, 4))
        self.assertIsNone(next(sc for sc in scenarios.SCENARIOS if sc.name == "smoke").mr())

    def test_the_reset_makes_only_the_scenarios_merge_request_again(self):
        ran = mock.Mock(return_value=mock.Mock(returncode=0, stdout="", stderr=""))
        with mock.patch.object(drive.subprocess, "run", ran), \
                mock.patch.object(drive.Path, "write_text"):
            drive.reset_rig(drive.Path("/nowhere"), 3)
            drive.reset_rig(drive.Path("/nowhere"))
        self.assertEqual([c.args[0][2:] for c in ran.call_args_list], [["--mr", "3"], []])


if __name__ == "__main__":
    unittest.main()
