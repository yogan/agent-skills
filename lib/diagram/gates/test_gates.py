#!/usr/bin/env python3
"""Tests for the shared gate plumbing — the Result type and the report table.

Small, but `report()` returns the number of failures and callers use that as an exit code,
so an off-by-one here turns a failing gate run into a passing build.

Run: `python3 lib/diagram/gates/test_gates.py`
"""
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))

from lib.diagram.gates import GateError, Result, report


class TestResult(unittest.TestCase):
    def test_no_problems_means_ok(self):
        self.assertTrue(Result("a", "size").ok)

    def test_any_problem_means_not_ok(self):
        self.assertFalse(Result("a", "size", ["TALL 900px"]).ok)

    def test_problems_are_copied_into_a_list(self):
        """A caller passing a tuple should still get a mutable, independent list."""
        source = ("one",)
        result = Result("a", "size", source)
        result.problems.append("two")
        self.assertEqual(source, ("one",))

    def test_repr_shows_the_verdict(self):
        self.assertIn("ok", repr(Result("a", "size")))
        self.assertIn("TALL", repr(Result("a", "size", ["TALL"])))


class TestReport(unittest.TestCase):
    def test_it_returns_the_failure_count(self):
        results = [Result("a", "size"), Result("b", "size", ["bad"]),
                   Result("c", "size", ["bad"])]
        self.assertEqual(report(results, stream=io.StringIO()), 2)

    def test_all_passing_returns_zero_so_it_can_be_an_exit_code(self):
        self.assertEqual(report([Result("a", "size")], stream=io.StringIO()), 0)

    def test_the_table_names_every_diagram_and_its_verdict(self):
        out = io.StringIO()
        report([Result("arch", "size", detail="887x771"),
                Result("er", "contrast", ["dark: 2.1:1"])], stream=out)
        text = out.getvalue()
        self.assertIn("arch", text)
        self.assertIn("887x771", text)
        self.assertIn("dark: 2.1:1", text)
        self.assertIn("1/2 checks pass", text)

    def test_multiple_problems_are_joined(self):
        out = io.StringIO()
        report([Result("a", "size", ["one", "two"])], stream=out)
        self.assertIn("one; two", out.getvalue())


class TestGateError(unittest.TestCase):
    def test_it_is_distinct_from_a_failed_measurement(self):
        """"I could not measure this" must never be reportable as a pass."""
        self.assertTrue(issubclass(GateError, RuntimeError))
        self.assertFalse(issubclass(GateError, AssertionError))


if __name__ == "__main__":
    unittest.main(verbosity=2)
