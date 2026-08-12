#!/usr/bin/env python3
"""Tests for callout placement.

The search logic is tested WITHOUT a browser by substituting the measurement step. That
split is the whole reason the browser side only measures: the interesting decisions —
clipping outranks overlap, greedy vs exhaustive, ties resolve deterministically — are
ordinary Python and get ordinary tests.

The end-to-end cases that need d2 and a real browser live in test_place_slow.py, because an
exhaustive two-callout search is 64 d2 compiles plus 64 browser measurements.

Run: `python3 lib/diagram/test_place.py`
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.diagram import place
from lib.diagram.examples import ARCHITECTURE, ER, SEQUENCE, STATE, STEPS
from lib.diagram.spec import NEAR


def fake_measurement(callouts):
    """A measurement dict shaped like the browser's, for scoring tests."""
    return {"callouts": [{"clip": c, "overlap": o} for c, o in callouts],
            "svg": {"width": 100, "height": 100}, "overflow": {}, "offenders": []}


class TestNoteSites(unittest.TestCase):
    def test_it_finds_notes_on_nested_architecture_nodes(self):
        sites = place.note_sites(ARCHITECTURE)
        self.assertEqual(len(sites), 2)
        self.assertEqual([s["note"] for s in sites],
                         ["new service", "now fans out presence"])

    def test_it_finds_notes_on_participants(self):
        self.assertEqual([s["note"] for s in place.note_sites(SEQUENCE)],
                         ["new in this MR"])

    def test_it_finds_notes_on_tables(self):
        self.assertEqual([s["note"] for s in place.note_sites(ER)],
                         ["gains a revision column", "new table"])

    def test_it_finds_notes_on_states(self):
        self.assertEqual([s["note"] for s in place.note_sites(STATE)],
                         ["new retry path"])

    def test_a_diagram_without_notes_has_no_sites(self):
        self.assertEqual(place.note_sites(STEPS), [])

    def test_the_order_is_stable_across_copies(self):
        """The search applies an anchor tuple by index, so the order has to be structural."""
        import copy
        first = [s["note"] for s in place.note_sites(ARCHITECTURE)]
        second = [s["note"] for s in place.note_sites(copy.deepcopy(ARCHITECTURE))]
        self.assertEqual(first, second)

    def test_it_returns_the_live_dicts(self):
        import copy
        spec = copy.deepcopy(STATE)
        place.note_sites(spec)[0]["near"] = "top-right"
        self.assertEqual(spec["states"][3]["near"], "top-right")


class TestApply(unittest.TestCase):
    def test_it_sets_near_by_index(self):
        out = place._apply(ER, ("bottom-left", "bottom-center"))
        self.assertEqual([s["near"] for s in place.note_sites(out)],
                         ["bottom-left", "bottom-center"])

    def test_the_original_spec_is_not_mutated(self):
        before = ER["tables"][1].get("near")
        place._apply(ER, ("bottom-right", "bottom-right"))
        self.assertEqual(ER["tables"][1].get("near"), before)


class TestScoring(unittest.TestCase):
    def test_clipping_dominates_any_amount_of_overlap(self):
        """A cut-off callout is strictly worse than one that merely covers something."""
        clipped = place._score(fake_measurement([(1, 0)]))[0]
        overlapping = place._score(fake_measurement([(0, 999_999)]))[0]
        self.assertGreater(clipped, overlapping)

    def test_overlap_breaks_ties_among_clip_free_candidates(self):
        low = place._score(fake_measurement([(0, 10)]))[0]
        high = place._score(fake_measurement([(0, 20)]))[0]
        self.assertLess(low, high)

    def test_clip_and_overlap_are_summed_across_callouts(self):
        total, clip, overlap = place._score(fake_measurement([(2, 5), (3, 7)]))
        self.assertEqual(clip, 5)
        self.assertEqual(overlap, 12)

    def test_a_diagram_with_no_callouts_scores_zero(self):
        self.assertEqual(place._score(fake_measurement([]))[0], 0)

    def test_best_picks_the_lowest_total(self):
        measured = [(("a",), fake_measurement([(0, 50)])),
                    (("b",), fake_measurement([(0, 10)])),
                    (("c",), fake_measurement([(1, 0)]))]
        total, anchors, clip, overlap = place._best(measured)
        self.assertEqual(anchors, ("b",))

    def test_best_is_deterministic_on_a_tie(self):
        """Two anchors can be exactly equal; the first one offered wins, every time."""
        measured = [(("first",), fake_measurement([(0, 7)])),
                    (("second",), fake_measurement([(0, 7)]))]
        self.assertEqual(place._best(measured)[1], ("first",))


class TestSearchStrategy(unittest.TestCase):
    """Substitutes the measurement step, so no browser or d2 is involved."""

    def setUp(self):
        self.calls = []
        self.real = place._measure_candidates
        place._measure_candidates = self.spy

    def tearDown(self):
        place._measure_candidates = self.real

    def spy(self, spec, name, combos, theme, standalone=False):
        self.calls.append(list(combos))
        # Prefer "bottom-left" wherever it appears; everything else is worse. Nothing clips,
        # so greedy always succeeds and the joint fallback stays untouched.
        out = []
        for anchors in combos:
            overlap = sum(0 if a == "bottom-left" else 100 for a in anchors)
            out.append((anchors, fake_measurement([(0, overlap)])))
        return out

    def test_greedy_is_the_default_even_for_two_callouts(self):
        """Measured: greedy reaches the same anchors as 8^n in a third of the time."""
        place.place(ER, name="er")
        self.assertEqual(len(self.calls), 2)
        for combos in self.calls:
            self.assertEqual(len(combos), len(NEAR))

    def test_one_callout_tries_every_anchor(self):
        place.place(STATE, name="state")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(self.calls[0]), len(NEAR))

    def test_many_callouts_are_settled_one_at_a_time(self):
        """One round of 8 per callout, never 8^n — that grid is unaffordable past two."""
        spec = {
            "kind": "state",
            "states": [{"id": f"s{i}", "role": "svc", "note": "changed"} for i in range(3)]
                      + [{"id": "end", "role": "ext"}],
            "transitions": [{"from": "s0", "to": "end"}],
        }
        place.place(spec, name="s")
        self.assertEqual(len(self.calls), 3)
        for combos in self.calls:
            self.assertEqual(len(combos), len(NEAR))

    def test_greedy_keeps_earlier_decisions_while_settling_later_ones(self):
        spec = {
            "kind": "state",
            "states": [{"id": f"s{i}", "role": "svc", "note": "changed"} for i in range(3)]
                      + [{"id": "end", "role": "ext"}],
            "transitions": [{"from": "s0", "to": "end"}],
        }
        place.place(spec, name="s")
        # Round 2 varies only index 1, and index 0 is already fixed at the winner.
        self.assertTrue(all(combo[0] == "bottom-left" for combo in self.calls[1]))

    def test_greedy_starts_from_an_anchor_the_spec_already_pinned(self):
        spec = {
            "kind": "state",
            "states": [{"id": "a", "role": "svc", "note": "x", "near": "top-right"},
                       {"id": "b", "role": "svc", "note": "y", "near": "top-right"},
                       {"id": "c", "role": "svc", "note": "z", "near": "top-right"},
                       {"id": "end", "role": "ext"}],
            "transitions": [{"from": "a", "to": "end"}],
        }
        place.place(spec, name="s", joint_max=0)
        self.assertTrue(all(combo[1] == "top-right" and combo[2] == "top-right"
                            for combo in self.calls[0]))

    def test_the_chosen_anchors_are_written_into_the_returned_spec(self):
        out, report = place.place(ER, name="er")
        self.assertEqual([s["near"] for s in place.note_sites(out)],
                         ["bottom-left", "bottom-left"])
        self.assertEqual([e["near"] for e in report], ["bottom-left", "bottom-left"])

    def test_the_report_records_the_strategy_and_the_candidate_count(self):
        _, report = place.place(ER, name="er")
        self.assertEqual(report[0]["strategy"], "greedy")
        self.assertEqual(report[0]["candidates"], len(NEAR) * 2)

    def test_every_report_entry_carries_the_FINAL_cost(self):
        """Not each greedy round's own cost. Recording that made a finished, clip-free
        placement report the first round's clip, and unplaceable() cried wolf."""
        _, report = place.place(ER, name="er")
        self.assertEqual({e["clip"] for e in report}, {0})
        self.assertEqual(len({e["overlap"] for e in report}), 1)

    def test_a_spec_without_notes_costs_nothing(self):
        out, report = place.place(STEPS, name="animated")
        self.assertIs(out, STEPS)
        self.assertEqual(report, [])
        self.assertEqual(self.calls, [])

    def test_an_invalid_spec_is_rejected_before_any_rendering(self):
        from lib.diagram.spec import SpecError
        with self.assertRaises(SpecError):
            place.place({"kind": "state", "states": [{"id": "a"}],
                         "transitions": [{"from": "a", "to": "nope"}]})
        self.assertEqual(self.calls, [])


class TestJointEscalation(unittest.TestCase):
    """The fallback exists for the case greedy cannot reach: two callouts that each look
    fine alone and only fit in one particular combination. Greedy fixes the first one
    against the wrong partner and never revisits it."""

    # Unreachable by changing one index at a time from ER's pinned start
    # ("top-left", "top-right"): every round-0 candidate clips, so the tie-break fixes
    # index 0 at NEAR[0] ("top-left") — and no round-1 candidate can recover from that,
    # because the good pair needs index 0 to be something else entirely.
    ONLY_GOOD_PAIR = ("bottom-right", "top-left")

    def setUp(self):
        self.calls = []
        self.real = place._measure_candidates
        place._measure_candidates = self.spy

    def tearDown(self):
        place._measure_candidates = self.real

    def spy(self, spec, name, combos, theme, standalone=False):
        self.calls.append(list(combos))
        out = []
        for anchors in combos:
            # Every combination clips except one exact pair.
            clip = 0 if tuple(anchors) == self.ONLY_GOOD_PAIR else 40
            out.append((anchors, fake_measurement([(clip, 100)])))
        return out

    def test_greedy_alone_cannot_find_the_only_workable_pair(self):
        """The premise of the whole fallback: this is a real limitation, not a hypothetical."""
        _, report = place.place(ER, name="er", joint_max=0)
        self.assertEqual(len(self.calls), 2)
        self.assertGreater(report[0]["clip"], 0)
        self.assertNotEqual([e["near"] for e in report], list(self.ONLY_GOOD_PAIR))

    def test_it_escalates_to_an_exhaustive_search_when_greedy_still_clips(self):
        _, report = place.place(ER, name="er", joint_max=2)
        self.assertEqual(report[0]["strategy"], "joint")
        self.assertEqual([e["near"] for e in report], list(self.ONLY_GOOD_PAIR))
        self.assertEqual(report[0]["clip"], 0)

    def test_escalation_costs_the_greedy_rounds_plus_the_full_grid(self):
        _, report = place.place(ER, name="er", joint_max=2)
        self.assertEqual(report[0]["candidates"], len(NEAR) * 2 + len(NEAR) ** 2)

    def test_it_does_not_escalate_when_the_count_is_unaffordable(self):
        """8^3 is 512 candidates; the clip is reported instead of paid for."""
        spec = {
            "kind": "state",
            "states": [{"id": f"s{i}", "role": "svc", "note": "changed"} for i in range(3)]
                      + [{"id": "end", "role": "ext"}],
            "transitions": [{"from": "s0", "to": "end"}],
        }
        _, report = place.place(spec, name="s", joint_max=2)
        self.assertEqual(report[0]["strategy"], "greedy")
        self.assertTrue(place.unplaceable(report))

    def test_a_still_clipped_result_is_surfaced_rather_than_hidden(self):
        _, report = place.place(ER, name="er", joint_max=0)
        self.assertTrue(place.unplaceable(report),
                        "a placement that could not avoid clipping must say so")


class TestUnplaceable(unittest.TestCase):
    def test_a_clip_free_report_has_no_unplaceable_entries(self):
        self.assertEqual(place.unplaceable([{"clip": 0, "index": 0}]), [])

    def test_a_clipping_entry_is_reported(self):
        entries = place.unplaceable([{"clip": 0, "index": 0}, {"clip": 12, "index": 1}])
        self.assertEqual([e["index"] for e in entries], [1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
