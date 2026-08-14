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
import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.diagram import place
from lib.diagram.examples import ARCHITECTURE, CLASS, ER, SEQUENCE, STATE
from lib.diagram.spec import NEAR


def fake_measurement(callouts, svg_clip=None, fmin=13.0):
    """A measurement dict shaped like the browser's, for scoring tests.

    `measure.js` reports a callout's overflow against two boundaries and `_score` uses the
    CARD one, because that is what actually clips on the page and what the clipping gate
    holds a callout to. So the (clip, overlap) pairs here are card-relative; `svg_clip`
    supplies the svg-box number for the one test that cares that they can differ.

    `fmin` is the smallest glyph in the finished drawing, which `_measure_candidates` attaches
    from the size gate. It defaults to a comfortable value so a test about overlap is not
    accidentally a test about glyph size.
    """
    return {"callouts": [{"clipVsCard": c, "clip": c if svg_clip is None else svg_clip[i],
                          "overlap": o} for i, (c, o) in enumerate(callouts)],
            "fmin": fmin,
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
        self.assertEqual(place.note_sites({"kind": "state", "states": [{"id": "a"}],
                                          "transitions": []}), [])

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

    def test_overhanging_the_svg_box_is_not_clipping_if_the_card_absorbs_it(self):
        """The bug this pins: the search scored against the svg box while the clipping gate
        held callouts to the card, so an anchor that clipped NOTHING on the page lost to one
        with twice its overlap. On the reference state machine that was `center-left` — 16px
        past the svg box, 0 past the card, overlap 10488 against the winner's 20104 — and the
        callout it rejected sat out in an empty margin instead of on top of three arrows.
        """
        absorbed = place._score(fake_measurement([(0, 10_000)], svg_clip=[16]))
        real = place._score(fake_measurement([(16, 10_000)], svg_clip=[16]))
        self.assertLess(absorbed[0], real[0],
                        "an overhang the card absorbs must not be penalised")
        self.assertEqual(absorbed[1], 0, "it should register no clip at all")
        self.assertEqual(absorbed[0][0], 0, "and carry no clip penalty")

    def test_a_diagram_with_no_callouts_costs_nothing_but_its_glyph_size(self):
        key, clip, overlap = place._score(fake_measurement([]))
        self.assertEqual((clip, overlap), (0, 0))
        # Everything except the glyph term, which is negated size and so never zero. Indexing
        # by position broke the day a term was inserted; taking the whole tuple does not.
        self.assertEqual([term for i, term in enumerate(key) if i != 2], [0, 0, 0],
                         f"nothing to clip, hide or cover: {key}")

    def test_hidden_text_outranks_everything_the_search_could_trade_it_for(self):
        """The gap this closes. A buried label was only ever visible as weighted overlap, in a
        term `HEIGHT_PRICE` can outbid — so on a real explainer figure the search took an anchor
        lying across 8% of `connectionTimeout` while three anchors hid nothing, because the
        clean ones were slightly taller. Being unreadable is not a price."""
        buried = fake_measurement([(0, 0)]); buried["hiddenText"] = 111; buried["rend_h"] = 300
        clean = fake_measurement([(0, 9000)]); clean["hiddenText"] = 0; clean["rend_h"] = 350
        self.assertLess(place._score(clean)[0], place._score(buried)[0],
                        "a clean anchor must win however tall and however much it covers")

    def test_only_what_the_ANCHOR_hides_counts_against_it(self):
        """Scored on the page total, an anchor could earn credit for damage it did not cause.
        The layout's share is near-constant across a search and cancels — but only near: an
        anchor can change the canvas, and a different canvas hides a different amount by
        itself. So the anchor that buries nothing must win even when the page total says
        otherwise."""
        clean = fake_measurement([(0, 0)])
        clean["hiddenText"], clean["hiddenByLayout"] = 500, 500      # all of it the layout's
        buries = fake_measurement([(0, 0)])
        buries["hiddenText"], buries["hiddenByLayout"] = 400, 300    # 100 of it its own
        self.assertLess(place._score(clean)[0], place._score(buries)[0],
                        "the anchor covering nothing must win on a worse page total")

    def test_a_clip_still_outranks_hidden_text(self):
        """Both mean a reader loses something; a clipped callout can lose ALL of it, and it is
        the one the placement report can act on by asking for a shorter note."""
        clipped = fake_measurement([(4, 0)]); clipped["hiddenText"] = 0
        buried = fake_measurement([(0, 0)]); buried["hiddenText"] = 5000
        self.assertLess(place._score(buried)[0], place._score(clipped)[0])

    def test_less_hidden_text_wins_when_no_anchor_is_clean(self):
        """Where every anchor buries something the remedy is editorial, but the search still
        has to hand back the least bad one rather than treating them as equal."""
        worse = fake_measurement([(0, 0)]); worse["hiddenText"] = 800
        better = fake_measurement([(0, 0)]); better["hiddenText"] = 120
        self.assertLess(place._score(better)[0], place._score(worse)[0])

    def test_shrinking_the_whole_diagram_outranks_a_large_overlap(self):
        """An anchor can grow the canvas, and a wider canvas is scaled further down in the
        content column — so a callout can shrink every letter in the figure. Blind to that,
        the search moved a callout on the reference ER to `center-left` and took 12.6px text
        down to 11.5px everywhere to buy a 22% cut in that one callout's overlap."""
        roomy = place._score(fake_measurement([(0, 3082)], fmin=12.6))[0]
        cramped = place._score(fake_measurement([(0, 2398)], fmin=11.5))[0]
        self.assertLess(roomy, cramped,
                        "bigger text must win even with more overlap")

    def test_height_is_priced_against_overlap_rather_than_ordered_against_it(self):
        """Whichever of the two goes first, the other becomes free. Ordered by height, the
        search buried text to save 50px (an anchor covering 15228 beat one covering 3082);
        ordered by overlap, it spent 47px of height to save 490 — noise on totals in the
        thousands. The real ER numbers, both ways round."""
        shorter = fake_measurement([(0, 3478)]); shorter["rend_h"] = 245
        taller = fake_measurement([(0, 2988)]); taller["rend_h"] = 292
        self.assertLess(place._score(shorter)[0], place._score(taller)[0],
                        "47px of height must outrank a 490-unit overlap saving")
        buried = fake_measurement([(0, 15228)]); buried["rend_h"] = 245
        clean = fake_measurement([(0, 3082)]); clean["rend_h"] = 292
        self.assertLess(place._score(clean)[0], place._score(buried)[0],
                        "but 12000 units of covered text must outrank 47px of height")

    def test_a_glyph_difference_too_small_to_see_does_not_outrank_overlap(self):
        """Rounded to the nearest half pixel, so measurement noise cannot decide a layout."""
        noisy = place._score(fake_measurement([(0, 500)], fmin=12.9))[0]
        clean = place._score(fake_measurement([(0, 100)], fmin=13.0))[0]
        self.assertLess(clean, noisy, "0.1px apart should tie on size, then overlap decides")

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
        # `place` asks the renderer which layout to hold the anchor search at, and that is a
        # real d2 run. These tests are deliberately free of d2 and of a browser — see the
        # module docstring — so the answer is stubbed rather than computed.
        self.real_layout = place.render_mod.choose_layout
        place.render_mod.choose_layout = (
            lambda spec, name="d", binary="d2": (("down", None), 15))

    def tearDown(self):
        place._measure_candidates = self.real
        place.render_mod.choose_layout = self.real_layout

    def spy(self, spec, name, combos, theme, standalone=False, layout=None,
            layers=None):
        self.calls.append(list(combos))
        # Prefer "bottom-left" wherever it appears; everything else is worse. Nothing clips,
        # so greedy always succeeds and the joint fallback stays untouched.
        out = []
        for anchors in combos:
            overlap = sum(0 if a == "bottom-left" else 100 for a in anchors)
            out.append((anchors, fake_measurement([(0, overlap)])))
        return out

    def test_two_callouts_are_searched_jointly_in_one_grid(self):
        """Greedy used to be the default here, on the claim that it reached the same anchors.
        It does not: settling one callout at a time cannot see a pair that only works
        together, and on the real ER diagram that cost 5483 overlap against the grid's 216."""
        place.place(ER, name="er")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(self.calls[0]), len(NEAR) ** 2)

    def test_one_callout_tries_every_anchor(self):
        place.place(STATE, name="state")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(self.calls[0]), len(NEAR))

    def test_many_callouts_are_settled_one_at_a_time(self):
        """One round of 8 per callout, never 8^n — that grid is unaffordable past two."""
        spec = {
            "kind": "state",
            "states": [{"id": f"s{i}", "role": "working", "note": "changed"} for i in range(3)]
                      + [{"id": "end", "role": "terminal"}],
            "transitions": [{"from": "s0", "to": "end"}],
        }
        place.place(spec, name="s")
        self.assertEqual(len(self.calls), 3)
        for combos in self.calls:
            self.assertEqual(len(combos), len(NEAR))

    def test_greedy_keeps_earlier_decisions_while_settling_later_ones(self):
        spec = {
            "kind": "state",
            "states": [{"id": f"s{i}", "role": "working", "note": "changed"} for i in range(3)]
                      + [{"id": "end", "role": "terminal"}],
            "transitions": [{"from": "s0", "to": "end"}],
        }
        place.place(spec, name="s")
        # Round 2 varies only index 1, and index 0 is already fixed at the winner.
        self.assertTrue(all(combo[0] == "bottom-left" for combo in self.calls[1]))

    def test_greedy_starts_from_an_anchor_the_spec_already_pinned(self):
        spec = {
            "kind": "state",
            "states": [{"id": "a", "role": "working", "note": "x", "near": "top-right"},
                       {"id": "b", "role": "working", "note": "y", "near": "top-right"},
                       {"id": "c", "role": "working", "note": "z", "near": "top-right"},
                       {"id": "end", "role": "terminal"}],
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
        self.assertEqual(report[0]["strategy"], "joint")
        self.assertEqual(report[0]["candidates"], len(NEAR) ** 2)

    def test_every_report_entry_carries_the_FINAL_cost(self):
        """Not each greedy round's own cost. Recording that made a finished, clip-free
        placement report the first round's clip, and unplaceable() cried wolf."""
        _, report = place.place(ER, name="er")
        self.assertEqual({e["clip"] for e in report}, {0})
        self.assertEqual(len({e["overlap"] for e in report}), 1)

    def test_a_spec_without_notes_costs_nothing(self):
        plain = {"kind": "state", "states": [{"id": "a"}, {"id": "b"}],
                 "transitions": [{"from": "a", "to": "b"}]}
        out, report = place.place(plain, name="plain")
        self.assertIs(out, plain)
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

    # The start point is pinned HERE rather than taken from `examples.ER`, because the premise
    # depends on it exactly: greedy's reach is "one index at a time from wherever it starts".
    # Read off the fixture, this broke silently the day the ER's own anchors moved — the good
    # pair became reachable in round 0 and greedy stopped failing, which is the one thing this
    # class needs it to do.
    START = ("top-left", "top-left")

    # Unreachable by changing one index at a time from START: every round-0 candidate clips
    # equally, so the tie-break leaves index 0 at NEAR[0] ("top-left"), and no round-1
    # candidate can recover from that, because the good pair needs index 0 to be something
    # else entirely.
    ONLY_GOOD_PAIR = ("bottom-right", "bottom-right")

    def setUp(self):
        self.calls = []
        self.spec = copy.deepcopy(ER)
        for site, anchor in zip(place.note_sites(self.spec), self.START):
            site["near"] = anchor
        self.real = place._measure_candidates
        place._measure_candidates = self.spy
        # `place` asks the renderer which layout to hold the anchor search at, and that is a
        # real d2 run. These tests are deliberately free of d2 and of a browser — see the
        # module docstring — so the answer is stubbed rather than computed.
        self.real_layout = place.render_mod.choose_layout
        place.render_mod.choose_layout = (
            lambda spec, name="d", binary="d2": (("down", None), 15))

    def tearDown(self):
        place._measure_candidates = self.real
        place.render_mod.choose_layout = self.real_layout

    def spy(self, spec, name, combos, theme, standalone=False, layout=None,
            layers=None):
        self.calls.append(list(combos))
        out = []
        for anchors in combos:
            # Every combination clips except one exact pair.
            clip = 0 if tuple(anchors) == self.ONLY_GOOD_PAIR else 40
            out.append((anchors, fake_measurement([(clip, 100)])))
        return out

    def test_greedy_alone_cannot_find_the_only_workable_pair(self):
        """The premise of the whole fallback: this is a real limitation, not a hypothetical."""
        _, report = place.place(self.spec, name="er", joint_max=0)
        self.assertEqual(len(self.calls), 2)
        self.assertGreater(report[0]["clip"], 0)
        self.assertNotEqual([e["near"] for e in report], list(self.ONLY_GOOD_PAIR))

    def test_the_joint_search_finds_the_only_workable_pair(self):
        _, report = place.place(self.spec, name="er", joint_max=2)
        self.assertEqual(report[0]["strategy"], "joint")
        self.assertEqual([e["near"] for e in report], list(self.ONLY_GOOD_PAIR))
        self.assertEqual(report[0]["clip"], 0)

    def test_the_joint_search_costs_the_grid_and_nothing_else(self):
        """It no longer runs greedy first and escalates, so the greedy rounds are not on the
        bill — affordability is the trigger now, not a clip greedy left behind."""
        _, report = place.place(self.spec, name="er", joint_max=2)
        self.assertEqual(report[0]["candidates"], len(NEAR) ** 2)

    def test_it_does_not_escalate_when_the_count_is_unaffordable(self):
        """8^3 is 512 candidates; the clip is reported instead of paid for."""
        spec = {
            "kind": "state",
            "states": [{"id": f"s{i}", "role": "working", "note": "changed"} for i in range(3)]
                      + [{"id": "end", "role": "terminal"}],
            "transitions": [{"from": "s0", "to": "end"}],
        }
        _, report = place.place(spec, name="s", joint_max=2)
        self.assertEqual(report[0]["strategy"], "greedy")
        self.assertTrue(place.unplaceable(report))

    def test_a_still_clipped_result_is_surfaced_rather_than_hidden(self):
        _, report = place.place(self.spec, name="er", joint_max=0)
        self.assertTrue(place.unplaceable(report),
                        "a placement that could not avoid clipping must say so")


class TestOneDrawingForTheWholeSearch(unittest.TestCase):
    """Every candidate has to be measured on the SAME drawing, or their overlaps are being
    compared across different pictures.

    Embedded that means one `(direction, wrap)` and one layer spacing. Standalone there is no
    layout to choose — the direction is a per-kind default and nothing wraps — but there IS
    still a spacing, and it used not to be chosen at all: the ladder lived in `_pick_layout`,
    which `render.standalone` does not go through, so every image the skill wrote of the
    reference ER had its cardinality label on the `presence_sessions` table.

    Chosen ONCE and passed down, never escalated per candidate: `_measure_candidates` measures
    all 64 in a single browser launch, and a ladder inside the loop would launch 64.
    """

    def setUp(self):
        self.seen = []
        self.real = place._measure_candidates
        self.real_layout = place.render_mod.choose_layout
        self.real_alone = place.render_mod.choose_standalone_layers
        place._measure_candidates = self.spy
        place.render_mod.choose_layout = (
            lambda spec, name="d", binary="d2": (("down", None), 15))
        place.render_mod.choose_standalone_layers = (
            lambda spec, name="d", theme="dark", binary="d2": 30)

    def tearDown(self):
        place._measure_candidates = self.real
        place.render_mod.choose_layout = self.real_layout
        place.render_mod.choose_standalone_layers = self.real_alone

    def spy(self, spec, name, combos, theme, standalone=False, layout=None, layers=None):
        self.seen.append({"standalone": standalone, "layout": layout, "layers": layers})
        return [(anchors, fake_measurement([(0, 100)])) for anchors in combos]

    def test_the_embedded_search_is_held_at_one_layout_and_one_spacing(self):
        place.place(ER, name="er")
        self.assertEqual(self.seen,
                         [{"standalone": False, "layout": ("down", None), "layers": 15}])

    def test_the_standalone_search_is_held_at_one_spacing_and_asks_for_no_layout(self):
        place.place(ER, name="er", standalone=True)
        self.assertEqual(self.seen,
                         [{"standalone": True, "layout": None, "layers": 30}])


class TestUnplaceable(unittest.TestCase):
    def test_a_clip_free_report_has_no_unplaceable_entries(self):
        self.assertEqual(place.unplaceable([{"clip": 0, "index": 0}]), [])

    def test_a_clipping_entry_is_reported(self):
        entries = place.unplaceable([{"clip": 0, "index": 0}, {"clip": 12, "index": 1}])
        self.assertEqual([e["index"] for e in entries], [1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
