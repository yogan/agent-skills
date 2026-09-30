#!/usr/bin/env python3
"""Tests for the current topic — see focus.py's module docstring for the rules.

Run: `python3 lib/test_focus.py` (stdlib only).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.focus import Current  # noqa: E402


def status(state, t):
    return t["status"]


def first_needing(state):
    todo = [t for t in state["topics"] if t["status"] in ("mine", "draft")]
    return todo[0] if todo else None


RULES = Current(status, ("mine", "draft"), first_needing)


def state_of(**statuses):
    return {"topics": [{"id": tid, "status": st} for tid, st in statuses.items()]}


def set_status(state, tid, st):
    next(t for t in state["topics"] if t["id"] == tid)["status"] = st


class TestCurrent(unittest.TestCase):
    def test_nothing_current_picks_the_first_that_needs_you(self):
        self.assertEqual(RULES.get(state_of(t1="theirs", t2="mine")), "t2")

    def test_nothing_needs_you_means_none(self):
        self.assertIsNone(RULES.get(state_of(t1="theirs")))

    def test_it_moves_on_when_its_topic_stops_needing_you(self):
        """Acked, posted, marked waiting — each is a status that no longer needs you."""
        state = state_of(t1="mine", t2="mine")
        RULES.get(state)
        set_status(state, "t1", "closed")
        self.assertEqual(RULES.get(state), "t2")

    def test_it_stays_while_its_status_changes_within_what_needs_you(self):
        state = state_of(t1="draft", t2="mine")
        RULES.get(state)
        set_status(state, "t1", "mine")
        self.assertEqual(RULES.get(state), "t1")

    def test_a_vanished_topic_is_replaced(self):
        state = state_of(t1="mine", t2="mine")
        RULES.get(state)
        state["topics"] = state["topics"][1:]        # dropped, or merged away
        self.assertEqual(RULES.get(state), "t2")


class TestJump(unittest.TestCase):
    def test_a_jump_holds_on_a_topic_that_does_not_need_you(self):
        """"Let's look at t2 first" — t2 only waits on the other side, and still holds."""
        state = state_of(t1="mine", t2="theirs")
        RULES.jump(state, "t2")
        self.assertEqual(RULES.get(state), "t2")

    def test_a_jump_holds_when_its_topic_comes_to_need_you(self):
        state = state_of(t1="mine", t2="theirs")
        RULES.jump(state, "t2")
        set_status(state, "t2", "mine")               # the other side answered
        self.assertEqual(RULES.get(state), "t2")

    def test_a_jump_ends_when_its_topic_is_settled(self):
        state = state_of(t1="mine", t2="theirs")
        RULES.jump(state, "t2")
        set_status(state, "t2", "closed")
        self.assertEqual(RULES.get(state), "t1")      # back to what needs you


class TestView(unittest.TestCase):
    JUMP = "quote t2 --focus"

    def test_another_topic_is_research_and_says_how_to_move(self):
        state = state_of(t1="mine", t2="mine")
        note = RULES.view(state, "t2", False, self.JUMP)
        self.assertIn("t2 is not the current topic (t1)", note)
        self.assertIn("`quote t2 --focus`", note)
        self.assertEqual(RULES.get(state), "t1")      # research moves nothing

    def test_the_current_topic_says_nothing(self):
        self.assertIsNone(RULES.view(state_of(t1="mine"), "t1", False, self.JUMP))

    def test_asking_to_focus_moves_it(self):
        state = state_of(t1="mine", t2="mine")
        self.assertIsNone(RULES.view(state, "t2", True, self.JUMP))
        self.assertEqual(RULES.get(state), "t2")

    def test_with_nothing_current_the_viewed_topic_becomes_current(self):
        state = state_of(t1="theirs")
        self.assertIsNone(RULES.view(state, "t1", False, self.JUMP))
        self.assertEqual(RULES.get(state), "t1")


if __name__ == "__main__":
    unittest.main()
