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
        figure.render_mod.choose_drawing = lambda spec, name="d", *a, **kw: (("down", None), 15)
        figure.browser_mod.available = lambda: True
        figure._size.check = lambda svg, name, **kw: Result(name, "size")
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
    def test_a_document_is_measured_in_one_browser_launch(self):
        """The reason `draw` takes a mapping at all: starting Chrome costs far more than
        measuring one more page, so a six-figure article pays for one launch and not six."""
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
    on top of a table, where the same spec unpinned renders 892x257 and clean.
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
