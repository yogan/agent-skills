#!/usr/bin/env python3
"""Tests for the d2 emitter. These pin the parts of the recipe that are *choices* —
the ones a well-meaning cleanup would undo, because nothing in d2's docs says they matter.

Pure string checks, no d2 binary needed; test_reference.py is what proves the source
these produce actually renders.

Run: `python3 lib/diagram/test_d2.py`
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.diagram import d2
from lib.diagram.examples import ARCHITECTURE, CLASS, ER, SEQUENCE, STATE, STEPS


class TestPrelude(unittest.TestCase):
    def test_root_fill_is_transparent(self):
        """Without this d2 paints an opaque page rect — a white slab in dark mode."""
        self.assertIn("style.fill: transparent", d2.emit(ARCHITECTURE))

    def test_edge_label_font_is_thirteen_not_eleven(self):
        """At 11 these were the sole cause of every sub-11px glyph in the prototype."""
        source = d2.emit(ARCHITECTURE)
        self.assertIn("(** -> **)[*].style.font-size: 13", source)

    def test_every_role_gets_a_class(self):
        source = d2.emit(ARCHITECTURE)
        for role in ("client", "svc", "store", "cache", "ext", "neutral", "grp"):
            self.assertIn(f"  {role}: {{ style:", source)


class TestQuoting(unittest.TestCase):
    def test_labels_with_a_colon_survive(self):
        spec = {"kind": "state",
                "states": [{"id": "a", "label": "1 doc : n sessions"},
                           {"id": "b"}],
                "transitions": [{"from": "a", "to": "b"}]}
        self.assertIn('"a": "1 doc : n sessions"', d2.emit(spec))

    def test_quotes_in_a_label_are_escaped(self):
        spec = {"kind": "state",
                "states": [{"id": "a", "label": 'say "hi"'}, {"id": "b"}],
                "transitions": [{"from": "a", "to": "b"}]}
        self.assertIn(r'"a": "say \"hi\""', d2.emit(spec))

    def test_dotted_paths_quote_each_segment(self):
        source = d2.emit(ARCHITECTURE)
        self.assertIn('"browser"."editor" -> "k8s"."api"."pod": "GraphQL"', source)


class TestNotesBecomeVisibleCallouts(unittest.TestCase):
    def test_a_note_emits_both_tooltip_and_near(self):
        """`tooltip` alone is a hover-only <title>; `near` is what makes it permanent."""
        source = d2.emit(SEQUENCE)
        self.assertIn("tooltip: \"new in this MR\"", source)
        self.assertIn("tooltip.near: bottom-right", source)

    def test_near_defaults_when_unspecified(self):
        spec = {"kind": "state",
                "states": [{"id": "a", "note": "new"}, {"id": "b"}],
                "transitions": [{"from": "a", "to": "b"}]}
        self.assertIn("tooltip.near: top-center", d2.emit(spec))

    def test_a_node_without_a_note_emits_no_tooltip(self):
        self.assertNotIn("tooltip", d2.emit(STATE).split("backoff")[0])


class TestTables(unittest.TestCase):
    """The three-role coupling and the sql_table-for-classes trick."""

    def test_the_three_coupled_roles_get_separate_sentinels(self):
        source = d2.emit(ER)
        self.assertIn(f'stroke: "{d2.TABLE_BODY}"', source)
        self.assertIn(f'font-color: "{d2.TABLE_TITLE}"', source)
        self.assertNotEqual(d2.TABLE_BODY, d2.TABLE_TITLE)

    def test_the_table_fill_is_a_text_grade_colour(self):
        """`fill` doubles as the member text, so a pastel background is unreadable."""
        self.assertIn('fill: "#2c6b30"', d2.emit(ER))       # store-tx, not store-bg

    def test_table_font_is_fourteen(self):
        """d2 renders the header at ~1.3x this and ignores the global font-size."""
        self.assertIn("font-size: 14", d2.emit(ER))

    def test_classes_render_as_sql_table_not_as_shape_class(self):
        """`shape: class` wastes ~47px of fixed padding per header."""
        source = d2.emit(CLASS)
        self.assertIn("shape: sql_table", source)
        self.assertNotIn("shape: class", source)

    def test_column_keys_become_d2_constraints(self):
        source = d2.emit(ER)
        self.assertIn("{constraint: primary_key}", source)
        self.assertIn("{constraint: foreign_key}", source)

    def test_a_stereotype_becomes_a_guillemet_row_and_a_dashed_outline(self):
        source = d2.emit(CLASS)
        self.assertIn('"«interface»": ""', source)
        self.assertIn("stroke-dash: 3", source)

    def test_tables_stack_downward_when_embedded(self):
        """Left-to-right makes three columns, overflowing the 777px content width."""
        self.assertIn("direction: down", d2.emit(ER))
        self.assertIn("direction: down", d2.emit(CLASS))

    def test_an_explicit_direction_still_wins(self):
        for standalone in (False, True):
            spec = dict(ER, direction="up")
            source = d2.emit(spec, standalone=standalone)
            self.assertIn("direction: up", source)
            self.assertNotIn("direction: down", source)
            self.assertNotIn("direction: right", source)


class TestStateRoles(unittest.TestCase):
    """A state's role names what is signalled; it reuses an architectural role's palette
    entry, so only the emitted *class* is shared. See d2.STATE_CLASS."""

    def test_each_state_role_emits_its_palette_class(self):
        source = d2.emit(STATE)
        for role, expected in d2.STATE_CLASS.items():
            if any(s.get("role") == role for s in STATE["states"]):
                self.assertIn(f"class: {expected}", source, f"{role} -> {expected}")

    def test_no_state_role_name_reaches_the_d2_source(self):
        """d2 only knows the six architectural classes — an unmapped name would silently
        style nothing at all."""
        source = d2.emit(STATE)
        for role in d2.STATE_CLASS:
            self.assertNotIn(f"class: {role}", source)


class TestDirectionPerTarget(unittest.TestCase):
    """One case per cell of `d2.DIRECTION`. The two targets want opposite layouts: embedded
    is scaled into a content column until its text breaks the 11px floor, standalone is
    opened full-screen. test_reference.py checks the geometry that follows from this."""

    def test_er_class_and_state_go_wide_standalone_and_down_embedded(self):
        for name, spec in (("er", ER), ("class", CLASS), ("state", STATE)):
            with self.subTest(kind=name):
                self.assertIn("direction: down", d2.emit(spec), f"{name} embedded")
                self.assertIn("direction: right", d2.emit(spec, standalone=True),
                              f"{name} standalone")

    def test_architecture_stays_down_for_both(self):
        """The only kind with containers: dagre packs nested groups differently, and `right`
        leaves a dead quadrant while crowding the callouts."""
        self.assertIn("direction: down", d2.emit(ARCHITECTURE))
        self.assertIn("direction: down", d2.emit(ARCHITECTURE, standalone=True))

    def test_a_sequence_is_never_given_a_direction(self):
        """d2's sequence engine ignores it, so emitting one would be noise."""
        for standalone in (False, True):
            self.assertNotIn("direction:", d2.emit(SEQUENCE, standalone=standalone))


class TestSequence(unittest.TestCase):
    def test_the_root_declares_the_sequence_shape(self):
        self.assertIn("shape: sequence_diagram", d2.emit(SEQUENCE))

    def test_participants_are_emitted_in_the_order_written(self):
        """d2 keeps this order; graphviz reorders lifelines, which is why d2 won."""
        source = d2.emit(SEQUENCE)
        positions = [source.index(f'"{i}"') for i in ("editor", "api", "gw", "redis")]
        self.assertEqual(positions, sorted(positions))


class TestArchitecture(unittest.TestCase):
    def test_a_container_gets_the_group_class(self):
        self.assertIn("class: grp", d2.emit(ARCHITECTURE))

    def test_children_are_nested_inside_their_container(self):
        source = d2.emit(ARCHITECTURE)
        block = source[source.index('"browser"'):source.index('"k8s"')]
        self.assertIn('"editor": "Editor"', block)
        self.assertIn('"wsc": "usePresence()"', block)

    def test_a_shape_is_emitted_when_given(self):
        self.assertIn("shape: cylinder", d2.emit(ARCHITECTURE))
        self.assertIn("shape: hexagon", d2.emit(ARCHITECTURE))

    def test_a_leaf_without_a_role_falls_back_to_neutral(self):
        spec = {"kind": "state", "states": [{"id": "a"}, {"id": "b"}],
                "transitions": [{"from": "a", "to": "b"}]}
        self.assertIn("class: neutral", d2.emit(spec))


class TestSteps(unittest.TestCase):
    def test_a_caption_node_is_emitted_because_d2_omits_board_names(self):
        source = d2.emit(STEPS)
        self.assertIn('"caption": "phase 1 of 4 — today: polling only"', source)
        self.assertIn("near: top-center", source)

    def test_the_caption_is_borderless_so_it_does_not_read_as_a_box(self):
        self.assertIn("stroke-width: 0; fill: transparent", d2.emit(STEPS))

    def test_each_step_becomes_a_numbered_board(self):
        source = d2.emit(STEPS)
        self.assertIn("steps: {", source)
        for i in ("1", "2", "3", "4"):
            self.assertIn(f'  "{i}": {{', source)

    def test_a_step_can_relabel_the_caption(self):
        self.assertIn('"caption".label: "phase 2 of 4 — deploy gateway, no traffic yet"',
                      d2.emit(STEPS))

    def test_removing_an_edge_uses_the_indexed_null_form(self):
        self.assertIn('("editor" -> "api")[0]: null', d2.emit(STEPS))

    def test_relabelling_an_edge_uses_the_indexed_label_form(self):
        self.assertIn('("editor" -> "gw")[0].label: "WebSocket 100%"', d2.emit(STEPS))

    def test_emphasis_uses_stroke_width(self):
        self.assertIn('("editor" -> "api")[0].style.stroke-width: 3', d2.emit(STEPS))

    def test_a_new_node_composes_the_new_class_with_its_role(self):
        self.assertIn("class: [svc; new]", d2.emit(STEPS))

    def test_an_added_edge_comes_before_its_emphasis_within_a_step(self):
        """Emphasising an edge d2 has not seen yet is an error, so order matters."""
        source = d2.emit(STEPS)
        step3 = source[source.index('"3": {'):source.index('"4": {')]
        self.assertLess(step3.index('"editor" -> "gw"'),
                        step3.index("style.stroke-width: 3"))

    def test_only_a_steps_diagram_is_animated(self):
        self.assertTrue(d2.is_animated(STEPS))
        self.assertFalse(d2.is_animated(ARCHITECTURE))


class TestEdges(unittest.TestCase):
    def test_a_dashed_edge_carries_the_stroke_dash_style(self):
        self.assertIn("{style.stroke-dash: 3}", d2.emit(CLASS))

    def test_an_unlabelled_edge_emits_no_colon(self):
        source = d2.emit(ARCHITECTURE)
        self.assertIn('"k8s"."api"."pod" -> "pg"\n', source)

    def test_emit_validates_first(self):
        from lib.diagram.spec import SpecError
        with self.assertRaises(SpecError):
            d2.emit({"kind": "architecture", "nodes": [{"id": "a"}],
                     "edges": [{"from": "a", "to": "nope"}]})


if __name__ == "__main__":
    unittest.main(verbosity=2)
