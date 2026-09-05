#!/usr/bin/env python3
"""Tests for the one entry point: `figure.draw`.

The pipeline it runs is covered piece by piece elsewhere — `test_place.py` for the anchor
search, `gates/test_clipping.py` for what the clipping gate can see, `test_render.py` for the
layout search. What is tested HERE is the orchestration those pieces used to be wired into by
hand in two skills: which gates a target implies, that a blocked gate is never silence, that
the browser work batches across a document, and that the three views of "what is wrong" stay
distinct.

Free of d2 and of a browser by substituting the steps that need them, in the same spirit as
test_place.py — the real thing is exercised end to end by test_reference.py and by the
`visualize` CLI's own tests.

Run: `python3 lib/diagram/test_figure.py`
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.diagram import figure
from lib.diagram.gates import GateError, Result
from lib.diagram.spec import SpecError

STATE = {"kind": "state",
         "states": [{"id": "a", "role": "working"}, {"id": "b", "role": "terminal"}],
         "transitions": [{"from": "a", "to": "b", "label": "done"}]}
NOTED = {"kind": "state",
         "states": [{"id": "a", "role": "working", "note": "new"},
                    {"id": "b", "role": "terminal"}],
         "transitions": [{"from": "a", "to": "b", "label": "done"}]}


class Base(unittest.TestCase):
    """Everything the pipeline needs a browser or d2 for, replaced with something predictable."""

    def setUp(self):
        self.clipping_calls = []
        self.real = {
            "render": figure.render_mod.render,
            "standalone": figure.render_mod.standalone,
            "place": figure.place_mod.place,
            "drawing": figure.render_mod.choose_drawing,
            "available": figure.browser_mod.available,
            "size": figure._size.check,
            "analyse": figure._size.analyse,
            "contrast": figure._contrast.check,
            "theming": figure._theming.check,
            "clipping": figure._clipping.check_many,
        }
        figure.render_mod.render = lambda spec, name="d", **kw: f"<svg id='{name}'/>"
        figure.render_mod.standalone = lambda spec, name="d", **kw: f"<svg id='{name}-file'/>"
        figure.place_mod.place = lambda spec, name="d", **kw: (spec, [])
        # Real, this is a d2 run per figure and took this file from 1ms to 28s. What it
        # decides is `render`'s business and is tested there; what matters here is that it is
        # decided once and handed on.
        figure.render_mod.choose_drawing = (
            lambda spec, name="d", *a, **kw: (("down", None), 15, 15))
        figure.browser_mod.available = lambda: True
        figure._size.check = lambda svg, name, **kw: Result(name, "size")
        # Contrast is scaled by what this reports; the stub SVGs have no measurable geometry.
        figure._size.analyse = lambda svg, **kw: {"scale": 1.0}
        figure._contrast.check = lambda svg, name, **kw: Result(name, "contrast")
        figure._theming.check = lambda svg, name, **kw: Result(name, "theming")
        figure._clipping.check_many = self.spy_clipping

    def tearDown(self):
        figure.render_mod.render = self.real["render"]
        figure.render_mod.standalone = self.real["standalone"]
        figure.place_mod.place = self.real["place"]
        figure.render_mod.choose_drawing = self.real["drawing"]
        figure.browser_mod.available = self.real["available"]
        figure._size.check = self.real["size"]
        figure._size.analyse = self.real["analyse"]
        figure._contrast.check = self.real["contrast"]
        figure._theming.check = self.real["theming"]
        figure._clipping.check_many = self.real["clipping"]

    def spy_clipping(self, svgs, **kw):
        self.clipping_calls.append((sorted(svgs), kw))
        return [Result(name, "clipping") for name in svgs]


class TestTargets(Base):
    def test_an_unknown_target_is_rejected_rather_than_guessed(self):
        with self.assertRaises(ValueError):
            figure.draw({"a": STATE}, target="png")

    def test_embed_asks_the_renderer_for_a_themeable_svg(self):
        drawn = figure.draw({"a": STATE}, target="embed")[0]
        self.assertEqual(drawn.svg, "<svg id='a'/>")

    def test_file_asks_the_renderer_for_a_baked_one(self):
        drawn = figure.draw({"a": STATE}, target="file")[0]
        self.assertEqual(drawn.svg, "<svg id='a-file'/>")

    def test_theming_is_checked_for_a_page_and_not_for_a_file(self):
        """There are no vars left in a baked image for a toggle to follow, so the gate has
        nothing to say — and `render.standalone` verifies mappability itself, at the only
        moment it still means anything."""
        page = figure.draw({"a": STATE}, target="embed")[0]
        baked = figure.draw({"a": STATE}, target="file")[0]
        self.assertIn("theming", [r.gate for r in page.results])
        self.assertNotIn("theming", [r.gate for r in baked.results])

    def test_every_target_is_still_clipped_checked(self):
        for target in figure.TARGETS:
            drawn = figure.draw({"a": STATE}, target=target)[0]
            self.assertIn("clipping", [r.gate for r in drawn.results], target)


class TestBatching(Base):
    def test_a_document_is_gated_in_one_batch(self):
        """The reason `draw` takes a mapping at all: a six-figure article checks all six
        together rather than one at a time."""
        figure.draw({"a": STATE, "b": STATE, "c": STATE})
        self.assertEqual(len(self.clipping_calls), 1)
        self.assertEqual(self.clipping_calls[0][0], ["a", "b", "c"])

    def test_nothing_to_draw_starts_nothing(self):
        self.assertEqual(figure.draw({}), [])
        self.assertEqual(self.clipping_calls, [])

    def test_the_order_out_matches_the_order_in(self):
        drawn = figure.draw({"first": STATE, "second": STATE, "third": STATE})
        self.assertEqual([f.name for f in drawn], ["first", "second", "third"])

    def test_a_finding_lands_on_the_figure_it_belongs_to(self):
        figure._clipping.check_many = lambda svgs, **kw: [
            Result("b", "clipping", ["HIDDEN TEXT 88px²"])] + [
            Result(n, "clipping") for n in svgs if n != "b"]
        drawn = {f.name: f for f in figure.draw({"a": STATE, "b": STATE})}
        self.assertEqual(drawn["a"].problems, [])
        self.assertEqual(drawn["b"].problems, ["b: HIDDEN TEXT 88px²"])


class TestWhatComesBack(Base):
    def test_a_gate_finding_is_both_a_result_and_a_prefixed_problem(self):
        """Two callers, two shapes: a CLI prints the results as a table, and a document just
        needs a line it can put on stderr under the figure's name."""
        figure._size.check = lambda svg, name, **kw: Result(name, "size", ["too tall"])
        drawn = figure.draw({"flow": STATE})[0]
        self.assertIn("too tall", [p for r in drawn.results for p in r.problems])
        self.assertEqual(drawn.problems, ["flow: too tall"])

    def test_advice_is_kept_apart_from_problems(self):
        """A gate measured the drawing; advice is about the spec as authored. One list would
        make "wide" read as loudly as "clipped"."""
        bare = {"kind": "er",
                "tables": [{"id": "a", "columns": [{"name": "id", "type": "uuid"}]},
                           {"id": "b", "columns": [{"name": "a_id", "type": "uuid"}]}],
                "edges": [{"from": "b.a_id", "to": "a.id", "label": "n : 1"}]}
        drawn = figure.draw({"tables": bare})[0]
        self.assertEqual(drawn.problems, [])
        self.assertTrue(any("name the entities" in a for a in drawn.advice), drawn.advice)
        self.assertTrue(all(a.startswith("tables: ") for a in drawn.advice))

    def test_a_blocked_gate_is_never_a_pass(self):
        def boom(svg, name, **kw):
            raise GateError("no font metrics")

        figure._size.check = boom
        drawn = figure.draw({"flow": STATE})[0]
        self.assertEqual(drawn.problems, [])
        self.assertTrue(any("could not run" in b for b in drawn.blocked), drawn.blocked)
        self.assertFalse(drawn.ok, "a gate that could not run must not read as clean")

    def test_a_blocked_clipping_gate_says_what_it_alone_can_see(self):
        figure._clipping.check_many = lambda svgs, **kw: (_ for _ in ()).throw(
            GateError("chrome vanished"))
        drawn = figure.draw({"a": STATE, "b": STATE})
        for one in drawn:
            self.assertTrue(any("not a clean bill of health" in b for b in one.blocked))

    def test_an_invalid_spec_raises_because_there_is_no_picture_to_hand_back(self):
        with self.assertRaises(SpecError):
            figure.draw({"bad": {"kind": "state",
                                 "states": [{"id": "a", "role": "working"}],
                                 "transitions": [{"from": "a", "to": "typo"}]}})


class TestInternalKeys(Base):
    """`direction` is the renderer's to decide, so a spec may not carry one — and this is the
    only place that can say so. `spec.validate` cannot: `d2.emit` validates on every call, and
    by then the search has set a direction on its own copy.

    What it bought an author was a way to switch off the check that keeps text readable. A
    pinned direction skips `render._pick_layout`, and the spacing escalation lives there: the
    reference ER pinned to its OWN measured direction renders 862x257 with `n sessions : 1 doc`
    on top of a table, where the same spec unpinned renders 902x257 and clean.
    """

    def test_a_spec_that_pins_a_direction_is_refused(self):
        with self.assertRaises(SpecError) as caught:
            figure.draw({"flow": dict(STATE, direction="right")})
        self.assertIn("not yours to set", str(caught.exception))

    def test_the_refusal_names_the_figure_and_says_what_to_do(self):
        with self.assertRaises(SpecError) as caught:
            figure.draw({"flow": dict(STATE, direction="down")})
        message = str(caught.exception)
        self.assertIn("flow:", message)
        self.assertIn("Remove it", message)

    def test_a_spec_without_one_is_untouched(self):
        self.assertTrue(figure.draw({"flow": STATE})[0].ok)


class TestGateIndependence(Base):
    """A gate that cannot run must not take another down with it.

    Contrast needs the rendered scale to judge WCAG's large-text allowance, and gets it from
    the size gate — so an SVG the size gate cannot measure used to block both.
    """

    def test_contrast_still_runs_when_the_drawing_cannot_be_measured(self):
        def unmeasurable(svg, **kw):
            raise GateError("no intrinsic size")

        figure._size.analyse = unmeasurable
        drawn = figure.draw({"flow": STATE})[0]
        self.assertIn("contrast", [r.gate for r in drawn.results])

    def test_contrast_is_told_the_scale_an_embedded_figure_is_drawn_at(self):
        seen = {}
        figure._size.analyse = lambda svg, **kw: {"scale": 0.85}
        figure._contrast.check = lambda svg, name, **kw: (
            seen.update(kw) or Result(name, "contrast"))
        figure.draw({"flow": STATE}, target="embed")
        self.assertEqual(seen.get("scale"), 0.85)

    def test_a_file_is_shown_at_natural_size_so_its_scale_is_one(self):
        seen = {}
        figure._size.analyse = lambda svg, **kw: {"scale": 0.5}
        figure._contrast.check = lambda svg, name, **kw: (
            seen.update(kw) or Result(name, "contrast"))
        figure.draw({"flow": STATE}, target="file")
        self.assertEqual(seen.get("scale"), 1.0)


# A drawing where the note had to sit on the arrow's HEAD — the case that is worth telling an
# author about, since nothing left in the picture says which way that arrow pointed. The stub
# `render` hands this back, so the advice is exercised without d2 or a browser.
COVERED = ('<svg viewBox="0 0 300 100">'
           '<path d="M 10 50 L 190 50" class="connection" stroke="black" fill="none" '
           'style="stroke-width:2;" marker-end="url(#head)"/>'
           '<g class="positioned-tooltip"><rect x="150" y="30" width="80" height="40"/>'
           '<foreignObject x="160" y="40" width="60" height="20">'
           '<div class="md"><p>new</p></div></foreignObject></g></svg>')
CLEAR = COVERED.replace('<rect x="150" y="30"', '<rect x="150" y="2"')


class TestPriming(Base):
    """One browser launch measures every string the drawing needs a width for, before any
    figure is drawn. A string that misses it is not drawn at all — see `compact.add_legend` —
    so what is tested here is that the legend's words are in that batch with the notes."""

    LEGENDED = {"kind": "state", "legend": {"steady": "running today"},
                "states": [{"id": "a", "role": "steady", "note": "new"},
                           {"id": "b", "role": "terminal"}],
                "transitions": [{"from": "a", "to": "b", "label": "done"}]}

    def setUp(self):
        super().setUp()
        self.primed = []
        self.real_prime = figure.callout_mod.prime
        figure.callout_mod.prime = self.primed.extend

    def tearDown(self):
        figure.callout_mod.prime = self.real_prime
        super().tearDown()

    def test_a_legends_words_are_measured_with_the_notes(self):
        figure.draw({"flow": self.LEGENDED})
        self.assertIn("running today", self.primed)
        self.assertIn("new", self.primed)

    def test_a_diagram_with_no_legend_primes_only_its_notes(self):
        figure.draw({"flow": NOTED})
        self.assertEqual(self.primed, ["new"])


class TestCoverageAdvice(Base):
    """The placement search minimises what a note covers and usually reaches zero. On a
    crowded drawing it cannot, and the least bad placement ships — which was silent."""

    def test_a_note_that_had_to_cover_an_arrows_end_is_said_out_loud(self):
        figure.render_mod.render = lambda spec, name="d", **kw: COVERED
        drawn = figure.draw({"flow": NOTED})[0]
        said = " ".join(drawn.advice)
        self.assertIn("'new'", said)
        self.assertIn("the end of an arrow", said)
        self.assertTrue(said.startswith("flow: "), said)

    def test_it_is_advice_and_does_not_fail_the_figure(self):
        """The drawing is the best one available, so nothing is broken and nothing should
        fail; the remedy is the author's. A `placement` message is the other case — a
        callout that does not FIT — and that one does reach `problems`."""
        figure.render_mod.render = lambda spec, name="d", **kw: COVERED
        drawn = figure.draw({"flow": NOTED})[0]
        self.assertEqual(drawn.problems, [])
        self.assertTrue(drawn.ok)

    def test_a_note_clear_of_every_arrow_says_nothing(self):
        """Only about coverage: this spec also earns a content warning (no start state),
        and asserting on the whole list would pass for the wrong reason."""
        figure.render_mod.render = lambda spec, name="d", **kw: CLEAR
        advice = figure.draw({"flow": NOTED})[0].advice
        self.assertEqual([a for a in advice if "px of line" in a], [], advice)

    def test_a_bridged_stretch_of_straight_run_says_nothing(self):
        """The narrowing that keeps this from being noise: line showing on both sides of the
        note reads straight through, so covering it is not worth an author's attention. What
        is worth it is a landmark — a corner, or a head — and this route has neither under
        the box. The search still prices the line; it just is not reported."""
        long_run = ('<svg viewBox="0 0 600 100">'
                    '<path d="M 10 50 L 590 50" class="connection" stroke="black" '
                    'fill="none" style="stroke-width:2;"/>'
                    '<g class="positioned-tooltip"><rect x="260" y="30" width="80" '
                    'height="40"/><foreignObject x="270" y="40" width="60" height="20">'
                    '<div class="md"><p>mid</p></div></foreignObject></g></svg>')
        self.assertEqual(figure._coverage_advice("f", long_run), [])

    def test_one_hidden_corner_reads_as_a_corner_and_several_as_a_count(self):
        one = ('<svg viewBox="0 0 200 200">'
               '<path d="M 0 0 L 0 40 S 0 50 10 50 L 190 50" class="connection" '
               'stroke="black" fill="none" style="stroke-width:2;"/>'
               '<g class="positioned-tooltip"><rect x="0" y="40" width="80" height="20"/>'
               '<foreignObject x="10" y="45" width="60" height="10">'
               '<div class="md"><p>one</p></div></foreignObject></g></svg>')
        two = one.replace('L 190 50"',
                          'L 60 50 S 70 50 70 60 L 70 190"')
        self.assertIn("the corner where an arrow turns",
                      figure._coverage_advice("f", one)[0])
        self.assertIn("2 corners where arrows turn",
                      figure._coverage_advice("f", two)[0])


class TestPlacement(Base):
    def test_a_callout_no_anchor_fits_is_reported_but_not_as_a_gate_verdict(self):
        """The remedy is editorial — shorten the note — so a CLI says it rather than failing
        its exit code on it. It still reaches `problems`, which is the union."""
        figure.place_mod.place = lambda spec, name="d", **kw: (
            spec, [{"index": 0, "near": "top-left", "clip": 12}])
        drawn = figure.draw({"flow": NOTED})[0]
        self.assertEqual(drawn.results and [p for r in drawn.results for p in r.problems], [])
        self.assertTrue(any("shorten its note" in p for p in drawn.placement))
        self.assertEqual(drawn.placement, drawn.problems)

    def test_placement_can_be_turned_off(self):
        called = []
        figure.place_mod.place = lambda spec, name="d", **kw: called.append(name) or (spec, [])
        figure.draw({"flow": NOTED}, place_callouts=False)
        self.assertEqual(called, [])

    def test_without_a_browser_a_diagram_with_callouts_says_so(self):
        figure.browser_mod.available = lambda: False
        drawn = figure.draw({"flow": NOTED})[0]
        self.assertTrue(any("no browser" in p for p in drawn.placement), drawn.placement)

    def test_without_a_browser_a_diagram_with_no_callouts_says_nothing(self):
        figure.browser_mod.available = lambda: False
        drawn = figure.draw({"flow": STATE})[0]
        self.assertEqual(drawn.placement, [])

    def test_gates_can_be_turned_off_for_debugging(self):
        drawn = figure.draw({"flow": STATE}, gates=False)[0]
        self.assertEqual((drawn.results, drawn.problems, drawn.blocked), ([], [], []))
        self.assertEqual(self.clipping_calls, [])




class TestFoldingIsAFallback(unittest.TestCase):
    """A line break in an edge label is spent only when the finished drawing needs it.

    `render._folds` makes an unbroken label the default among candidates, but "needed" is
    measured before the callouts exist and a callout takes canvas: the repo state machine is
    947px unwrapped and 1014px once its callout has an anchor, which puts its smallest label
    at 10.0px against a 10px floor. `figure._settle` is the only place that can see that, so
    the escalation happens there — one step at a time, and no further than it takes.

    Pure arithmetic on the pinned tuple, so no d2 and no browser: the corpus-level claim is
    `test_reference.MEASURED` and the captures.
    """

    def test_it_steps_one_level_at_a_time(self):
        from lib.diagram import d2
        gentlest, harder = None, d2.EDGE_WRAPS[0]
        self.assertEqual(figure._next_fold((("right", gentlest), 15, 15)),
                         (("right", harder), 15, 15))

    def test_it_keeps_the_direction_and_the_spacings(self):
        """Only the fold escalates. A retry that also re-picked the direction would be
        answering a different question from the one the size gate asked."""
        self.assertEqual(figure._next_fold((("down", None), 30, 25))[0][0], "down")
        self.assertEqual(figure._next_fold((("down", None), 30, 25))[1:], (30, 25))

    def test_it_escalates_from_a_relaxed_fold_and_gives_the_sparing_up(self):
        """`render._relax` spares labels it measured as affordable. If the finished drawing
        then does not fit, that measurement no longer holds, so the sparing goes with the
        step. Reading the width out of the pair is also what stops this raising."""
        from lib.diagram import d2
        relaxed = (d2.EDGE_WRAPS[0], frozenset({"a label"}))
        self.assertEqual(figure._next_fold((("right", relaxed), 15, 15)),
                         (("right", d2.EDGE_WRAPS[1]), 15, 15))

    def test_the_hardest_fold_has_nowhere_left_to_go(self):
        from lib.diagram import d2
        self.assertIsNone(figure._next_fold((("right", d2.EDGE_WRAPS[-1]), 15, 15)))
        self.assertIsNone(figure._next_fold(
            (("right", (d2.EDGE_WRAPS[-1], frozenset({"a label"}))), 15, 15)))

    def test_a_figure_with_no_layout_to_pin_never_folds(self):
        """A standalone image and a sequence diagram: neither is laid out into a column that
        could make its text too small, so neither has a fold to escalate."""
        self.assertIsNone(figure._next_fold((None, 15, 15)))

    def test_an_unmeasurable_drawing_is_not_folded_over(self):
        """Spending a reader's line break on a parse failure would be a guess; the gates say
        so properly a step later."""
        self.assertTrue(figure._fits("not an svg at all", "x", False))


if __name__ == "__main__":
    unittest.main(verbosity=2)
