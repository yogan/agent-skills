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

from lib.diagram import d2, palette
from lib.diagram.examples import ARCHITECTURE, CLASS, ER, SEQUENCE, STATE


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
        """`tooltip` alone is a hover-only <title>; `near` is what makes it permanent.

        The anchor is set HERE rather than read off `SEQUENCE`, which no longer pins one — and
        should not have been read off it even when it did. What this covers is that an anchor
        reaches the emitted source, not which anchor the corpus happens to carry.
        """
        import copy
        spec = copy.deepcopy(SEQUENCE)
        next(p for p in spec["participants"] if p.get("note"))["near"] = "bottom-right"
        source = d2.emit(spec)
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


class TestParticipantDetailLine(unittest.TestCase):
    """A lane may be a subsystem; `detail` carries the real module names under its label."""

    def spec(self, **over):
        p = {"id": "routing", "label": "FE routing", "role": "svc"}
        p.update(over)
        return {"kind": "sequence",
                "participants": [{"id": "user", "role": "client"}, p],
                "messages": [{"from": "user", "to": "routing", "label": "navigate()"}]}

    def test_a_newline_in_a_label_is_escaped_not_literal(self):
        """d2 rejects a real line break inside a quoted string, so this is a compile error
        rather than something that merely looks wrong."""
        source = d2.emit(self.spec(detail="AppRoutes / react-router"))
        self.assertIn(r'"FE routing\nAppRoutes / react-router"', source)
        self.assertNotIn("FE routing\nAppRoutes", source)

    def test_every_participant_gets_the_taller_box_when_any_has_a_detail(self):
        """A row of boxes at two different heights reads as an accident."""
        source = d2.emit(self.spec(detail="AppRoutes / react-router"))
        self.assertEqual(source.count(f"height: {d2.ACTOR_HEIGHT_DETAIL}"), 2)
        self.assertNotIn(f"height: {d2.ACTOR_HEIGHT}\n", source)

    def test_without_a_detail_the_boxes_stay_short(self):
        source = d2.emit(self.spec())
        self.assertEqual(source.count(f"height: {d2.ACTOR_HEIGHT}"), 2)

    def test_the_id_is_used_when_there_is_no_label(self):
        spec = self.spec(detail="AppRoutes")
        del spec["participants"][1]["label"]
        self.assertIn(r'"routing\nAppRoutes"', d2.emit(spec))


class TestMessageOutcome(unittest.TestCase):
    def spec(self, outcome):
        return {"kind": "sequence", "participants": [{"id": "a"}, {"id": "b"}],
                "messages": [{"from": "a", "to": "b", "label": "409", "outcome": outcome}]}

    def test_an_error_is_drawn_in_the_ext_colour(self):
        source = d2.emit(self.spec("error"))
        self.assertIn(f'style.stroke: "{palette.vars_for("ext", table=True)}"', source)
        self.assertIn(f'style.font-color: "{palette.vars_for("ext", table=True)}"', source)

    def test_an_ok_is_drawn_in_the_store_colour(self):
        self.assertIn(f'style.stroke: "{palette.vars_for("store", table=True)}"',
                      d2.emit(self.spec("ok")))

    def test_a_message_without_one_keeps_the_muted_default(self):
        """Split on the last GLOBAL line rather than on an arrow selector: there are two of
        those now (`->` and `<->`) and both set a stroke, so splitting on one of them left the
        other's global in the tail and this passed for the wrong reason.
        """
        spec = {"kind": "sequence", "participants": [{"id": "a"}, {"id": "b"}],
                "messages": [{"from": "a", "to": "b", "label": "x"}]}
        self.assertNotIn("style.stroke:", d2.emit(spec).split("classes: {")[-1]
                         .split("}\n")[-1])

    def test_a_push_can_also_carry_one(self):
        """The dash says nobody asked for it, the colour says how it went — different facts."""
        spec = self.spec("error")
        spec["messages"][0]["push"] = True
        source = d2.emit(spec)
        self.assertIn("style.stroke-dash: 4", source)
        self.assertIn("style.stroke:", source)


class TestEdgeLabelWrapping(unittest.TestCase):
    """Wrapping an edge label is the cheapest width in the renderer, so where it breaks
    matters — each rule here comes from a label that wrapped badly."""

    def test_a_cardinality_breaks_at_its_colon(self):
        """"1 doc : n sessions" is one fact about docs and one about sessions."""
        self.assertEqual(d2.wrap_edge_label("1 doc : n sessions", 14), "1 doc :\nn sessions")

    def test_the_colon_survives_the_break(self):
        """Unlike a stranded separator it is not punctuation between equals — it is the ratio,
        so dropping it would change what the label says."""
        self.assertIn(":", d2.wrap_edge_label("1 doc : n sessions", 8))

    def test_a_stranded_separator_is_dropped(self):
        self.assertEqual(d2.wrap_edge_label("list · read · stat", 8), "list\nread\nstat")

    def test_a_short_word_is_never_left_alone_on_a_line(self):
        """Even at a width that would strand it: "1 doc :" / "n" / "sessions" puts the whole
        meaning of the cardinality on a line of its own."""
        self.assertEqual(d2.wrap_edge_label("1 doc : n sessions", 8), "1 doc :\nn sessions")

    def test_a_trailing_orphan_joins_the_line_above(self):
        self.assertEqual(d2.wrap_edge_label("belongs to", 8), "belongs to")

    def test_a_short_label_is_left_alone(self):
        self.assertEqual(d2.wrap_edge_label("GraphQL", 8), "GraphQL")

    def test_a_line_runs_over_rather_than_the_label_spending_another(self):
        """The reference state machine's `retry (max 30s)`, which folded onto three lines whose
        longest was five characters. A label's box is as wide as its longest line, so that third
        break bought the figure 5px and cost it a line — see `WRAP_SLACK`."""
        self.assertEqual(d2.wrap_edge_label("retry (max 30s)", 8), "retry\n(max 30s)")

    def test_the_overrun_is_spent_only_where_it_saves_a_line(self):
        """Otherwise it is just a wider wrap width wearing a disguise: a label that folds to the
        same number of lines either way keeps the tighter fold and the narrower box."""
        self.assertEqual(d2.wrap_edge_label("transport error", 8), "transport\nerror")
        self.assertEqual(d2.wrap_edge_label("socket open", 8), "socket\nopen")

    def test_a_per_label_fold_spares_the_labels_it_names(self):
        """`(width, spared)` is what `render._relax` produces: one width for the figure, and
        the labels it measured as affordable on one line left alone."""
        self.assertEqual(d2.fold_for("transport error", (8, frozenset({"transport error"}))),
                         "transport error")
        self.assertEqual(d2.fold_for("socket open", (8, frozenset({"transport error"}))),
                         "socket\nopen")

    def test_a_plain_width_still_folds_everything(self):
        self.assertEqual(d2.fold_for("socket open", 8), "socket\nopen")

    def test_no_width_folds_nothing(self):
        self.assertEqual(d2.fold_for("a label that is really quite long", None),
                         "a label that is really quite long")

    def test_the_allowance_is_one_character(self):
        """Stated as a relationship: every fold above was measured at this value, and two was
        never measured at all."""
        self.assertEqual(d2.WRAP_SLACK, 1)

    def test_an_authored_newline_is_kept(self):
        self.assertEqual(d2.wrap_edge_label("one\ntwo", 40), "one\ntwo")

    def test_wrapping_is_off_unless_asked_for(self):
        spec = {"kind": "state", "states": [{"id": "a"}, {"id": "b"}],
                "transitions": [{"from": "a", "to": "b", "label": "a long label to wrap"}]}
        self.assertIn('"a long label to wrap"', d2.emit(spec))
        self.assertIn(r"\n", d2.emit(spec, wrap_edges=8))


class TestLaneGroups(unittest.TestCase):
    """Colour groups lanes by which side of the wire they are on. See d2.GROUP_CLASSES."""

    def spec(self, *groups):
        return {"kind": "sequence",
                "participants": [{"id": f"p{i}", "group": g} for i, g in enumerate(groups)],
                "messages": [{"from": "p0", "to": f"p{len(groups) - 1}", "label": "x"}]}

    def test_lanes_in_one_group_share_a_colour(self):
        source = d2.emit(self.spec("browser", "browser", "server"))
        first = d2.GROUP_CLASSES[0]
        self.assertEqual(source.count(f"class: {first}"), 2)
        self.assertEqual(source.count(f"class: {d2.GROUP_CLASSES[1]}"), 1)

    def test_colours_are_assigned_in_order_of_first_appearance(self):
        """So the author picks which side gets which colour by ordering their lanes."""
        self.assertEqual(d2.group_classes([{"group": "server"}, {"group": "browser"}]),
                         {"server": d2.GROUP_CLASSES[0], "browser": d2.GROUP_CLASSES[1]})

    def test_more_groups_than_palette_entries_wrap_rather_than_crash(self):
        many = [{"group": f"g{i}"} for i in range(len(d2.GROUP_CLASSES) + 2)]
        mapping = d2.group_classes(many)
        self.assertEqual(len(mapping), len(many))
        self.assertEqual(mapping["g0"], mapping[f"g{len(d2.GROUP_CLASSES)}"])

    def test_a_group_name_never_reaches_the_d2_source_as_a_class(self):
        source = d2.emit(self.spec("browser", "server"))
        self.assertNotIn("class: browser", source)
        self.assertNotIn("class: server", source)

    def test_the_reference_sequence_uses_two_groups_not_four_roles(self):
        source = d2.emit(SEQUENCE)
        used = {c for c in d2.GROUP_CLASSES if f"class: {c}" in source}
        self.assertEqual(len(used), 2, f"expected two lane colours, got {sorted(used)}")


class TestDetailWrapping(unittest.TestCase):
    """A detail sets the box's width, so left on one line it shoves every other lane sideways.
    Wrapping trades that width for height, which a sequence diagram has to spare."""

    LONG = "Procrastinate worker: discover→ingest (creates Document)→extract→ocr→enrich"

    def test_a_short_detail_stays_on_one_line(self):
        self.assertEqual(d2.wrap_detail("Azure AI — OCR"), ["Azure AI — OCR"])

    def test_a_long_detail_wraps_within_the_budget(self):
        lines = d2.wrap_detail(self.LONG)
        self.assertGreater(len(lines), 1)
        for line in lines:
            self.assertLessEqual(len(line), d2.DETAIL_WRAP, line)

    def test_wrapping_loses_no_words(self):
        self.assertEqual(" ".join(d2.wrap_detail(self.LONG)).split(), self.LONG.split())

    def test_an_authors_own_newlines_are_kept(self):
        self.assertEqual(d2.wrap_detail("one\ntwo"), ["one", "two"])

    def test_a_single_over_long_token_is_left_whole(self):
        """These are module and job names; a name broken in half is worse than a wide box."""
        token = "x" * (d2.DETAIL_WRAP + 10)
        self.assertEqual(d2.wrap_detail(token), [token])

    def spec(self, detail):
        return {"kind": "sequence",
                "participants": [{"id": "a", "group": "app"},
                                 {"id": "b", "group": "app", "detail": detail}],
                "messages": [{"from": "a", "to": "b", "label": "x"}]}

    def test_the_box_grows_a_line_at_a_time_and_every_lane_matches(self):
        one = d2.emit(self.spec("short one"))
        three = d2.emit(self.spec(self.LONG))
        self.assertIn(f"height: {d2.ACTOR_HEIGHT_DETAIL}", one)
        taller = d2.ACTOR_HEIGHT_DETAIL + 2 * d2.LINE_H          # three detail lines
        self.assertIn(f"height: {taller}", three)
        self.assertEqual(three.count(f"height: {taller}"), 2, "the row must stay even")

    def test_the_emitted_label_carries_the_wrapped_form(self):
        source = d2.emit(self.spec(self.LONG))
        for line in d2.wrap_detail(self.LONG):
            self.assertIn(line, source)
        self.assertNotIn(self.LONG, source)


class TestPushMessages(unittest.TestCase):
    def base(self, **over):
        msg = {"from": "gw", "to": "editor", "label": "peer joined"}
        msg.update(over)
        return {"kind": "sequence",
                "participants": [{"id": "editor", "role": "client"},
                                 {"id": "gw", "role": "svc"}],
                "messages": [msg]}

    def test_a_push_is_dashed_with_an_open_arrowhead(self):
        """Both, not either: a dash alone is UML's *reply* arrow."""
        source = d2.emit(self.base(push=True))
        self.assertIn("style.stroke-dash: 4", source)
        self.assertIn("target-arrowhead.shape: arrow", source)

    def test_an_ordinary_message_gets_neither(self):
        source = d2.emit(self.base())
        self.assertNotIn("stroke-dash", source)
        self.assertNotIn("arrowhead", source)

    def test_push_false_is_an_ordinary_call(self):
        self.assertNotIn("stroke-dash", d2.emit(self.base(push=False)))

    def test_a_push_carries_no_colour(self):
        """Colour has no convention a reader can decode, and there is no legend."""
        source = d2.emit(self.base(push=True))
        self.assertNotIn(palette.ACCENT, source.split("classes: {")[1].split("}")[-1])


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

    def test_every_state_role_gets_a_colour_of_its_own(self):
        """The vocabulary exists so a diagram cannot paint two meanings the same colour. Two
        roles sharing a palette entry would put that back — which is the bug `stuck` fixed,
        where "deferred" and "imported, still there" were both `transient`."""
        classes = list(d2.STATE_CLASS.values())
        self.assertEqual(len(classes), len(set(classes)), d2.STATE_CLASS)

    def test_every_role_in_the_vocabulary_has_a_class(self):
        """`neutral` is the one that maps to itself; the rest must be in STATE_CLASS or they
        reach d2 as an unknown class name and style nothing."""
        from lib.diagram.spec import STATE_ROLES
        self.assertEqual(set(STATE_ROLES) - set(d2.STATE_CLASS), {"neutral"})

    def test_a_stuck_state_emits_the_one_class_no_other_state_role_uses(self):
        source = d2.emit({"kind": "state",
                          "states": [{"id": "a", "role": "stuck", "start": True},
                                     {"id": "b", "role": "terminal"}],
                          "transitions": [{"from": "a", "to": "b", "label": "cleaned up"}]})
        self.assertIn("class: svc", source)


class TestContainerLabels(unittest.TestCase):
    def test_a_container_label_sits_in_the_corner(self):
        """Centred along the top edge is where an edge entering from above arrives, and the
        two collided on the reference architecture."""
        source = d2.emit(ARCHITECTURE)
        self.assertIn("label.near: top-left", source)

    def test_a_leaf_box_gets_no_label_placement(self):
        source = d2.emit({"kind": "state",
                          "states": [{"id": "a", "start": True}, {"id": "b"}],
                          "transitions": [{"from": "a", "to": "b", "label": "x"}]})
        self.assertNotIn("label.near", source)


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
        """The only kind with containers: nested groups pack differently from plain boxes, and
        `right` leaves a dead quadrant while crowding the callouts. Measured under dagre
        originally and still the way it comes out under ELK."""
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

    def test_a_new_leaf_composes_the_accent_with_its_role(self):
        spec = {"kind": "architecture",
                "nodes": [{"id": "a", "role": "svc", "new": True, "note": "new"}], "edges": []}
        self.assertIn("class: [svc; new]", d2.emit(spec))
        self.assertIn(f'new: {{ style: {{ stroke: "{palette.ACCENT}"', d2.emit(spec))

    def test_a_new_table_is_accented_through_its_fill(self):
        """A table has no border to accent — `stroke` is its body fill — so the one colour
        that reaches its border, header and member text is what changes."""
        spec = {"kind": "er", "tables": [
            {"id": "t", "role": "store", "new": True, "note": "new",
             "columns": [{"name": "id", "type": "uuid"}]}]}
        self.assertIn(f'fill: "{palette.ACCENT}"', d2.emit(spec))
        self.assertNotIn('fill: "#2c6b30"', d2.emit(spec))

    def test_a_leaf_label_is_not_bold(self):
        """d2 bolds node labels by default. A filled shape with a coloured border already
        announces itself, and next to a muted subtitle the weight reads as a third signal."""
        self.assertIn("style.bold: false", d2.emit(ARCHITECTURE))

    def test_a_container_label_keeps_its_own_styling(self):
        source = d2.emit(ARCHITECTURE)
        block = source[source.index('"browser"'):source.index('"pg"')]
        self.assertIn("class: grp", block)

    def test_a_leaf_without_a_role_falls_back_to_neutral(self):
        spec = {"kind": "state", "states": [{"id": "a"}, {"id": "b"}],
                "transitions": [{"from": "a", "to": "b"}]}
        self.assertIn("class: neutral", d2.emit(spec))


class TestEdges(unittest.TestCase):
    def test_a_dashed_edge_carries_the_stroke_dash_style(self):
        self.assertIn("{style.stroke-dash: 3}", d2.emit(CLASS))

    def test_a_bidirectional_edge_uses_d2s_two_headed_arrow(self):
        source = d2.emit({"kind": "architecture",
                          "nodes": [{"id": "api", "role": "svc"},
                                    {"id": "db", "role": "store"}],
                          "edges": [{"from": "api", "to": "db", "label": "read · write",
                                     "bidirectional": True}]})
        self.assertIn('"api" <-> "db": "read · write"', source)

    def test_both_arrow_forms_get_the_muted_styling(self):
        """d2's connection glob matches the arrow AS WRITTEN, so `(** -> **)` does not select a
        `<->` edge. Missing that, a two-headed arrow came out in d2's near-black at its own
        font size next to six muted grey ones — and no gate caught it, because the colour it
        falls back to is one the palette already maps.
        """
        source = d2.emit(ARCHITECTURE)
        for arrow in ("->", "<->"):
            self.assertIn(f'(** {arrow} **)[*].style.stroke: "{palette.MUTED}"', source)
            self.assertIn(f"(** {arrow} **)[*].style.font-size: {d2.BASE_FONT}", source)

    def test_an_unlabelled_edge_emits_no_colon(self):
        """Its own spec, not the corpus. This used to read the reference architecture's one
        bare edge — which was a defect the corpus happened to be carrying (`spec.py` warns
        about an unlabelled architecture edge now), so labelling it broke a test of something
        else entirely. A fixture that exists by accident is a fixture that vanishes by accident.
        """
        source = d2.emit({"kind": "architecture",
                          "nodes": [{"id": "a", "role": "svc"}, {"id": "b", "role": "store"}],
                          "edges": [{"from": "a", "to": "b"}]})
        self.assertIn('"a" -> "b"\n', source)

    def test_emit_validates_first(self):
        from lib.diagram.spec import SpecError
        with self.assertRaises(SpecError):
            d2.emit({"kind": "architecture", "nodes": [{"id": "a"}],
                     "edges": [{"from": "a", "to": "nope"}]})


if __name__ == "__main__":
    unittest.main(verbosity=2)
