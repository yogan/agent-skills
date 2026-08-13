#!/usr/bin/env python3
"""Tests for the diagram spec — see spec.py's module docstring for why validation is
loud (d2 renders a typo'd edge endpoint as a stray blank box instead of failing).

Run: `python3 lib/diagram/test_spec.py` (stdlib only).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.diagram.examples import REFERENCE
from lib.diagram.spec import (SpecError, bridges, components,  # noqa: E501
                              content_warnings, validate)


def arch(**over):
    base = {
        "kind": "architecture",
        "nodes": [
            {"id": "browser", "label": "Browser", "children": [
                {"id": "editor", "label": "Editor", "role": "client"},
            ]},
            {"id": "pg", "label": "PostgreSQL", "role": "store", "shape": "cylinder"},
        ],
        "edges": [{"from": "browser.editor", "to": "pg", "label": "GraphQL"}],
    }
    base.update(over)
    return base


class TestKindAndRoleVocabulary(unittest.TestCase):
    def test_a_valid_architecture_spec_passes_and_returns_the_spec(self):
        spec = arch()
        self.assertIs(validate(spec), spec)

    def test_unknown_kind_is_rejected(self):
        with self.assertRaisesRegex(SpecError, "kind"):
            validate(arch(kind="flowchart"))

    def test_unknown_role_is_rejected(self):
        spec = arch()
        spec["nodes"][1]["role"] = "database"
        with self.assertRaisesRegex(SpecError, "role"):
            validate(spec)

    def test_unthemeable_code_shape_is_rejected(self):
        """`shape: code` brings its own syntax palette that our var map has no entry for."""
        spec = arch()
        spec["nodes"][1]["shape"] = "code"
        with self.assertRaisesRegex(SpecError, "shape"):
            validate(spec)

    def test_a_spec_must_be_a_dict(self):
        with self.assertRaisesRegex(SpecError, "must be a dict"):
            validate([{"kind": "architecture"}])

    def test_bad_direction_is_rejected(self):
        with self.assertRaisesRegex(SpecError, "direction"):
            validate(arch(direction="sideways"))


class TestDanglingEdges(unittest.TestCase):
    """The headline case: d2 invents a node for an unknown endpoint rather than erroring."""

    def test_unknown_edge_target_is_rejected(self):
        with self.assertRaisesRegex(SpecError, "to='postgres'"):
            validate(arch(edges=[{"from": "browser.editor", "to": "postgres"}]))

    def test_unknown_edge_source_is_rejected(self):
        with self.assertRaisesRegex(SpecError, "from='edtior'"):
            validate(arch(edges=[{"from": "edtior", "to": "pg"}]))

    def test_a_container_child_must_be_addressed_by_dotted_path(self):
        """`editor` alone is not addressable — d2 scopes it under its container."""
        with self.assertRaisesRegex(SpecError, "from='editor'"):
            validate(arch(edges=[{"from": "editor", "to": "pg"}]))

    def test_the_container_itself_is_a_valid_endpoint(self):
        validate(arch(edges=[{"from": "browser", "to": "pg"}]))

    def test_error_names_the_known_ids_so_the_typo_is_obvious(self):
        with self.assertRaisesRegex(SpecError, "browser.editor"):
            validate(arch(edges=[{"from": "browser.editor", "to": "postgres"}]))


class TestNodeTree(unittest.TestCase):
    def test_duplicate_sibling_ids_are_rejected(self):
        with self.assertRaisesRegex(SpecError, "duplicate node id"):
            validate(arch(nodes=[{"id": "a", "role": "svc"}, {"id": "a", "role": "store"}]))

    def test_dotted_id_is_rejected_in_favour_of_nesting(self):
        with self.assertRaisesRegex(SpecError, "may not contain"):
            validate(arch(nodes=[{"id": "a.b", "role": "svc"}],
                          edges=[{"from": "a.b", "to": "a.b"}]))

    def test_a_container_may_not_also_carry_a_role(self):
        """It gets the container styling; a role on it would silently do nothing."""
        with self.assertRaisesRegex(SpecError, "remove its .role"):
            validate(arch(nodes=[{"id": "g", "role": "svc",
                                  "children": [{"id": "c", "role": "client"}]},
                                 {"id": "pg", "role": "store"}]))

    def test_empty_children_list_is_rejected(self):
        with self.assertRaisesRegex(SpecError, "empty .children"):
            validate(arch(nodes=[{"id": "g", "children": []}, {"id": "pg", "role": "store"}],
                          edges=[{"from": "pg", "to": "pg"}]))

    def test_nesting_two_deep_resolves(self):
        spec = {
            "kind": "architecture",
            "nodes": [{"id": "k8s", "children": [
                {"id": "api", "children": [{"id": "pod", "role": "svc"}]}]}],
            "edges": [{"from": "k8s.api.pod", "to": "k8s.api.pod"}],
        }
        validate(spec)


class TestNotes(unittest.TestCase):
    def test_note_with_a_valid_near_passes(self):
        spec = arch()
        spec["nodes"][1]["note"] = "new table"
        spec["nodes"][1]["near"] = "center-right"
        validate(spec)

    def test_near_outside_d2s_eight_constants_is_rejected(self):
        """d2 rejects `right-center` and `outside-*`; catch it before d2 does."""
        spec = arch()
        spec["nodes"][1]["note"] = "new table"
        spec["nodes"][1]["near"] = "right-center"
        with self.assertRaisesRegex(SpecError, "near"):
            validate(spec)

    def test_empty_note_is_rejected(self):
        spec = arch()
        spec["nodes"][1]["note"] = "  "
        with self.assertRaisesRegex(SpecError, "note"):
            validate(spec)

    def test_near_without_a_note_is_rejected(self):
        """On its own `near` does nothing, so the callout the author wanted is simply
        missing — the likeliest cause is a misspelt `note` key."""
        spec = arch()
        spec["nodes"][1]["near"] = "top-left"
        with self.assertRaisesRegex(SpecError, "did you misspell it"):
            validate(spec)

    def test_near_without_a_note_is_caught_on_a_table_too(self):
        spec = {"kind": "er", "tables": [
            {"id": "t", "role": "store", "near": "top-left",
             "columns": [{"name": "id", "type": "uuid"}]}]}
        with self.assertRaisesRegex(SpecError, "no `note`"):
            validate(spec)


class TestNewFlagIsRejected(unittest.TestCase):
    """A stroke-only accent has no legend, so it cannot say WHAT changed. It survived for a
    while inside the animated `steps` kind, whose per-board caption supplied the words; that
    kind is gone, and with it the flag."""

    def test_new_is_rejected_on_an_architecture_node(self):
        spec = arch()
        spec["nodes"][1]["new"] = True
        with self.assertRaisesRegex(SpecError, "sets `new`"):
            validate(spec)


class TestSequence(unittest.TestCase):
    def base(self, **over):
        spec = {
            "kind": "sequence",
            "participants": [{"id": "editor", "label": "Editor", "role": "client"},
                             {"id": "gw", "label": "Gateway", "role": "svc"}],
            "messages": [{"from": "editor", "to": "gw", "label": "WS upgrade"}],
        }
        spec.update(over)
        return spec

    def test_valid_sequence_passes(self):
        validate(self.base())

    def test_a_lane_may_carry_a_group(self):
        validate(self.base(participants=[{"id": "editor", "group": "browser"},
                                         {"id": "gw", "group": "server"}]))

    def test_group_and_role_together_are_rejected(self):
        """Colour says one thing per diagram; with groups it says which side of the wire a
        lane is on, so a role alongside would silently lose."""
        with self.assertRaisesRegex(SpecError, "both `group` and `role`"):
            validate(self.base(participants=[{"id": "editor", "group": "browser"},
                                             {"id": "gw", "group": "server", "role": "svc"}]))

    def test_a_split_group_warns_but_is_allowed(self):
        """Advisory on purpose: lanes that talk to each other sitting far apart costs a long
        arrow, and which is worse depends on the flow."""
        spec = self.base(participants=[{"id": "a", "group": "browser"},
                                       {"id": "b", "group": "server"},
                                       {"id": "c", "group": "browser"}],
                         messages=[{"from": "a", "to": "c", "label": "x"}])
        validate(spec)
        self.assertTrue(any("split across the row" in w for w in content_warnings(spec)))

    def test_a_contiguous_group_is_silent(self):
        spec = self.base(participants=[{"id": "a", "group": "browser"},
                                       {"id": "b", "group": "browser"},
                                       {"id": "c", "group": "server"}],
                         messages=[{"from": "a", "to": "c", "label": "x"}])
        self.assertEqual(content_warnings(spec), [])

    def test_half_grouped_lanes_warn(self):
        spec = self.base(participants=[{"id": "a", "group": "browser"}, {"id": "b"}],
                         messages=[{"from": "a", "to": "b", "label": "x"}])
        self.assertTrue(any("some lanes are grouped" in w for w in content_warnings(spec)))

    def test_a_message_may_be_a_push(self):
        validate(self.base(messages=[{"from": "gw", "to": "editor", "label": "peer joined",
                                      "push": True}]))

    def test_push_is_rejected_on_a_non_sequence_edge(self):
        """An arrow is only a *call* in a sequence, which is what makes "B never asked" a
        distinction. Elsewhere it is a relationship and `dashed` covers it."""
        with self.assertRaisesRegex(SpecError, "only meaningful on a sequence message"):
            validate(arch(edges=[{"from": "browser.editor", "to": "pg", "push": True}]))

    def test_a_non_boolean_push_is_rejected(self):
        with self.assertRaisesRegex(SpecError, "push must be"):
            validate(self.base(messages=[{"from": "gw", "to": "editor", "push": "yes"}]))

    def test_self_message_is_allowed(self):
        validate(self.base(messages=[{"from": "gw", "to": "gw", "label": "authenticate()"}]))

    def test_participants_cannot_nest(self):
        spec = self.base()
        spec["participants"][0]["children"] = [{"id": "x", "role": "client"}]
        with self.assertRaisesRegex(SpecError, "cannot have children"):
            validate(spec)

    def test_unknown_message_endpoint_is_rejected(self):
        with self.assertRaisesRegex(SpecError, "messages"):
            validate(self.base(messages=[{"from": "editor", "to": "redis"}]))

    def test_messages_are_required(self):
        spec = self.base()
        del spec["messages"]
        with self.assertRaisesRegex(SpecError, "messages"):
            validate(spec)


class TestTables(unittest.TestCase):
    def er(self, **over):
        spec = {
            "kind": "er",
            "tables": [
                {"id": "users", "role": "store",
                 "columns": [{"name": "id", "type": "uuid", "key": "pk"}]},
                {"id": "documents", "role": "store",
                 "columns": [{"name": "owner_id", "type": "uuid", "key": "fk"}]},
            ],
            "edges": [{"from": "documents.owner_id", "to": "users.id", "label": "belongs to"}],
        }
        spec.update(over)
        return spec

    def test_valid_er_passes(self):
        validate(self.er())

    def test_a_column_is_addressable_as_an_edge_endpoint(self):
        validate(self.er(edges=[{"from": "documents.owner_id", "to": "users.id"}]))

    def test_the_table_itself_is_addressable(self):
        validate(self.er(edges=[{"from": "documents", "to": "users"}]))

    def test_unknown_column_endpoint_is_rejected(self):
        with self.assertRaisesRegex(SpecError, "users.uuid"):
            validate(self.er(edges=[{"from": "documents.owner_id", "to": "users.uuid"}]))

    def test_unknown_key_kind_is_rejected(self):
        spec = self.er()
        spec["tables"][0]["columns"][0]["key"] = "primary_key"
        with self.assertRaisesRegex(SpecError, "key"):
            validate(spec)

    def test_duplicate_column_is_rejected(self):
        spec = self.er()
        spec["tables"][0]["columns"].append({"name": "id", "type": "text"})
        with self.assertRaisesRegex(SpecError, "duplicate"):
            validate(spec)

    def test_a_table_needs_columns(self):
        spec = self.er()
        spec["tables"][0]["columns"] = []
        with self.assertRaisesRegex(SpecError, "columns"):
            validate(spec)

    def test_er_edges_are_optional(self):
        spec = self.er()
        del spec["edges"]
        validate(spec)

    def test_class_kind_uses_members(self):
        validate({
            "kind": "class",
            "classes": [{"id": "Gateway", "role": "svc",
                         "members": [{"name": "+ handleUpgrade()", "type": "Socket"}]}],
        })

    def test_class_members_are_required(self):
        with self.assertRaisesRegex(SpecError, "members"):
            validate({"kind": "class", "classes": [{"id": "Gateway", "role": "svc"}]})


class TestState(unittest.TestCase):
    def test_valid_state_passes(self):
        validate({
            "kind": "state",
            "states": [{"id": "live", "role": "steady"}, {"id": "closed", "role": "terminal"}],
            "transitions": [{"from": "live", "to": "closed", "label": "user leaves"}],
        })

    def test_a_state_takes_the_state_vocabulary(self):
        for role in ("working", "steady", "transient", "terminal", "neutral"):
            with self.subTest(role=role):
                validate({"kind": "state",
                          "states": [{"id": "a", "role": role}, {"id": "b"}],
                          "transitions": [{"from": "a", "to": "b", "label": "x"}]})

    def test_an_architectural_role_on_a_state_is_rejected(self):
        """`live` was tagged `store` and `backoff` `cache` — which picked the colour and
        invented a justification. The names now have to mean what they say."""
        for role in ("client", "svc", "store", "cache", "ext"):
            with self.subTest(role=role):
                with self.assertRaisesRegex(SpecError, "role"):
                    validate({"kind": "state",
                              "states": [{"id": "a", "role": role}, {"id": "b"}],
                              "transitions": [{"from": "a", "to": "b", "label": "x"}]})

    def test_a_state_role_on_another_kind_is_rejected(self):
        with self.assertRaisesRegex(SpecError, "role"):
            validate({"kind": "architecture",
                      "nodes": [{"id": "a", "role": "steady"}],
                      "edges": []})

    def test_unknown_transition_endpoint_is_rejected(self):
        with self.assertRaisesRegex(SpecError, "transitions"):
            validate({
                "kind": "state",
                "states": [{"id": "live", "role": "steady"}],
                "transitions": [{"from": "live", "to": "backoff"}],
            })


class TestContentWarnings(unittest.TestCase):
    """Advisory, not fatal: they predict a size-gate failure with a better message."""

    def test_a_disciplined_diagram_warns_about_nothing(self):
        self.assertEqual(content_warnings(arch()), [])

    def test_too_many_states_warns(self):
        spec = {"kind": "state",
                "states": [{"id": f"s{i}", "role": "working"} for i in range(8)],
                "transitions": [{"from": "s0", "to": "s1"}]}
        self.assertTrue(any("8 states" in w for w in content_warnings(spec)))

    def test_many_transitions_per_state_warns_and_names_the_cause(self):
        """Four states with a terminal reached from all three others: 9/4 = 2.25."""
        spec = {"kind": "state",
                "states": [{"id": s} for s in ("open", "pending", "waiting", "done")],
                "transitions": [
                    {"from": "open", "to": "pending", "label": "pushed"},
                    {"from": "open", "to": "waiting", "label": "set waiting"},
                    {"from": "pending", "to": "waiting", "label": "set waiting"},
                    {"from": "pending", "to": "open", "label": "reviewer replies"},
                    {"from": "waiting", "to": "open", "label": "reviewer replies"},
                    {"from": "open", "to": "done", "label": "resolved"},
                    {"from": "pending", "to": "done", "label": "resolved"},
                    {"from": "waiting", "to": "done", "label": "resolved"},
                    {"from": "done", "to": "open", "label": "reopened"}]}
        warnings = content_warnings(spec)
        self.assertTrue(any("9 transitions across 4 states" in w for w in warnings), warnings)
        self.assertTrue(any("from any state" in w for w in warnings), warnings)

    def test_the_same_machine_with_the_repetition_removed_is_silent(self):
        """7/4 = 1.75. The fix the warning asks for is what clears it."""
        spec = {"kind": "state",
                "states": [{"id": "open"}, {"id": "pending"}, {"id": "waiting"},
                           {"id": "done", "note": "from any state"}],
                "transitions": [
                    {"from": "open", "to": "pending", "label": "pushed"},
                    {"from": "open", "to": "waiting", "label": "set waiting"},
                    {"from": "pending", "to": "waiting", "label": "set waiting"},
                    {"from": "pending", "to": "open", "label": "reviewer replies"},
                    {"from": "waiting", "to": "open", "label": "reviewer replies"},
                    {"from": "pending", "to": "done", "label": "resolved"},
                    {"from": "done", "to": "open", "label": "reopened"}]}
        self.assertEqual(content_warnings(spec), [])

    def test_the_reference_state_machine_stays_silent(self):
        """7 transitions / 5 states = 1.4, and it reads cleanly — the threshold has to let
        this through or it is measuring the wrong thing."""
        self.assertEqual(content_warnings(REFERENCE["state"]), [])

    def test_an_ascii_arrow_in_a_label_warns(self):
        spec = {"kind": "state",
                "states": [{"id": "a", "label": "data -> protectedRoutes"}, {"id": "b"}],
                "transitions": [{"from": "a", "to": "b", "label": "x => y"}]}
        warnings = content_warnings(spec)
        self.assertTrue(any("'->'" in w for w in warnings), warnings)
        self.assertTrue(any("'=>'" in w for w in warnings), warnings)

    def test_a_typeset_arrow_is_silent(self):
        spec = {"kind": "state",
                "states": [{"id": "a", "label": "data → protectedRoutes"}, {"id": "b"}],
                "transitions": [{"from": "a", "to": "b", "label": "x → y"}]}
        self.assertEqual(content_warnings(spec), [])

    def test_too_many_messages_warns(self):
        spec = {"kind": "sequence",
                "participants": [{"id": "a", "role": "svc"}, {"id": "b", "role": "svc"}],
                "messages": [{"from": "a", "to": "b", "label": f"m{i}"} for i in range(9)]}
        warns = content_warnings(spec)
        self.assertTrue(any("9 messages" in w for w in warns))

    def test_a_crowded_container_warns(self):
        spec = arch(nodes=[
            {"id": "g", "children": [{"id": f"c{i}", "role": "svc"} for i in range(7)]},
            {"id": "pg", "role": "store"}],
            edges=[{"from": "g.c0", "to": "pg"}])
        self.assertTrue(any("container g has 7 children" in w for w in content_warnings(spec)))

    def test_a_long_label_warns(self):
        spec = arch()
        spec["nodes"][1]["label"] = "the PostgreSQL primary, with streaming replication"
        self.assertTrue(any("label is" in w for w in content_warnings(spec)))

    def test_a_wordy_note_warns(self):
        spec = arch()
        spec["nodes"][1]["note"] = "this table now gains an extra revision column for locking"
        self.assertTrue(any("note is" in w for w in content_warnings(spec)))

    def test_a_wide_table_warns(self):
        spec = {"kind": "er", "tables": [
            {"id": "t", "role": "store",
             "columns": [{"name": f"c{i}", "type": "text"} for i in range(10)]}]}
        self.assertTrue(any("10 columns" in w for w in content_warnings(spec)))

    def test_a_wide_class_warns_about_members_and_is_named_correctly(self):
        spec = {"kind": "class", "classes": [
            {"id": "Fat", "label": "x" * 50,
             "members": [{"name": f"m{i}()", "type": "void"} for i in range(10)]}]}
        warns = content_warnings(spec)
        self.assertTrue(any("10 members" in w for w in warns), warns)
        self.assertTrue(any(w.startswith("class label is") for w in warns), warns)

    def test_a_bare_ratio_er_label_warns(self):
        """`n : 1` makes the reader work out which end is which; observed in a real run."""
        spec = {"kind": "er",
                "tables": [{"id": "a", "role": "store",
                            "columns": [{"name": "id", "type": "uuid"}]},
                           {"id": "b", "role": "store",
                            "columns": [{"name": "a_id", "type": "uuid"}]}],
                "edges": [{"from": "b.a_id", "to": "a.id", "label": "n : 1"}]}
        warns = content_warnings(spec)
        self.assertTrue(any("name the entities" in w for w in warns), warns)

    def test_a_named_cardinality_does_not_warn(self):
        spec = {"kind": "er",
                "tables": [{"id": "a", "role": "store",
                            "columns": [{"name": "id", "type": "uuid"}]},
                           {"id": "b", "role": "store",
                            "columns": [{"name": "a_id", "type": "uuid"}]}],
                "edges": [{"from": "b.a_id", "to": "a.id", "label": "1 a : n bs"}]}
        self.assertEqual(content_warnings(spec), [])

    def test_the_n_in_a_ratio_is_not_mistaken_for_a_word(self):
        """The first version of this check passed "n : 1" for containing a letter."""
        from lib.diagram.spec import _names_something
        for bare in ("n : 1", "1 : n", "n : m", "1 : 1", "0..1", "n:1"):
            self.assertFalse(_names_something(bare), bare)
        for named in ("1 doc : n sessions", "n : m via email_documents", "belongs to",
                      "1 doc : 0..1 claim"):
            self.assertTrue(_names_something(named), named)

    def test_an_unlabelled_relationship_warns(self):
        spec = {"kind": "er",
                "tables": [{"id": "a", "role": "store",
                            "columns": [{"name": "id", "type": "uuid"}]},
                           {"id": "b", "role": "store",
                            "columns": [{"name": "a_id", "type": "uuid"}]}],
                "edges": [{"from": "b.a_id", "to": "a.id"}]}
        self.assertTrue(any("no label" in w for w in content_warnings(spec)))

    def test_a_class_edge_without_a_label_warns_too(self):
        spec = {"kind": "class",
                "classes": [{"id": "A", "members": [{"name": "x", "type": "int"}]},
                            {"id": "B", "members": [{"name": "y", "type": "int"}]}],
                "edges": [{"from": "A", "to": "B"}]}
        self.assertTrue(any("no label" in w for w in content_warnings(spec)))

    def test_a_class_edge_may_be_a_bare_word_without_warning(self):
        """The named-cardinality rule is ER-specific; `owns` is a fine class label."""
        spec = {"kind": "class",
                "classes": [{"id": "A", "members": [{"name": "x", "type": "int"}]},
                            {"id": "B", "members": [{"name": "y", "type": "int"}]}],
                "edges": [{"from": "A", "to": "B", "label": "owns"}]}
        self.assertEqual(content_warnings(spec), [])

    def test_warnings_never_raise(self):
        """A caller may report them; it must not have to guard against an exception."""
        self.assertIsInstance(content_warnings({"kind": "er"}), list)
        self.assertIsInstance(content_warnings({}), list)


class TestIslands(unittest.TestCase):
    """Barely-connected groups are the one oversize an author can always fix — they are nearly
    separate pictures already, and the layout engine packs them along the cross axis, so the
    width is the sum of all of them."""

    def stages(self, *edges, jobs=5, names=("lint", "test", "post")):
        return {"kind": "architecture",
                "nodes": [{"id": s, "children": [{"id": f"{s}{i}", "role": "svc"}
                                                 for i in range(jobs)]} for s in names],
                "edges": list(edges)}

    def test_an_edge_joins_two_names(self):
        self.assertEqual(components(["a", "b"], [{"from": "a", "to": "b"}]), [["a", "b"]])

    def test_no_edges_means_every_name_is_its_own_island(self):
        self.assertEqual(components(["a", "b"], []), [["a"], ["b"]])

    def test_direction_does_not_matter(self):
        self.assertEqual(components(["a", "b"], [{"from": "b", "to": "a"}]), [["a", "b"]])

    def test_a_link_into_a_container_joins_the_whole_container(self):
        """Only the first path segment counts: a link into `k8s.api.pod` joins `k8s`."""
        self.assertEqual(components(["browser", "k8s"],
                                    [{"from": "browser", "to": "k8s.api.pod"}]),
                         [["browser", "k8s"]])

    def test_an_edge_to_an_unknown_name_is_ignored_rather_than_crashing(self):
        """content_warnings runs on specs validate() has not necessarily accepted yet."""
        self.assertEqual(components(["a"], [{"from": "a", "to": "gone"}]), [["a"]])

    def test_every_edge_of_a_chain_is_a_bridge(self):
        cuts = bridges(["a", "b", "c"], [{"from": "a", "to": "b"}, {"from": "b", "to": "c"}])
        self.assertEqual(sorted(cuts), [("a", "b"), ("b", "c")])

    def test_a_cycle_has_no_bridges(self):
        edges = [{"from": "a", "to": "b"}, {"from": "b", "to": "c"}, {"from": "c", "to": "a"}]
        self.assertEqual(bridges(["a", "b", "c"], edges), [])

    def test_two_parallel_edges_are_neither_a_bridge(self):
        """Removing one leaves the other, so the graph does not fall apart."""
        self.assertEqual(bridges(["a", "b"], [{"from": "a", "to": "b"},
                                              {"from": "b", "to": "a"}]), [])

    def test_four_disconnected_stages_warn_and_name_themselves(self):
        warnings = content_warnings(self.stages(names=("test", "lint", "post", "pre")))
        self.assertTrue(any("4 barely-connected groups" in w for w in warnings), warnings)
        self.assertTrue(any("lint" in w for w in warnings), warnings)

    def test_a_single_speculative_edge_no_longer_hides_the_split(self):
        """The case that got past the first version: a real pipeline had `stage -> stage: on
        success` added between otherwise unconnected stages, and two edges were enough to make
        the whole thing look like one graph."""
        warnings = content_warnings(self.stages(
            {"from": "lint", "to": "test", "label": "on success"},
            {"from": "test", "to": "post", "label": "on success"}))
        self.assertTrue(any("barely-connected" in w for w in warnings), warnings)
        self.assertTrue(any("single edge" in w for w in warnings), warnings)

    def test_a_small_chain_is_left_alone_though_every_edge_is_a_bridge(self):
        """Otherwise `a -> b -> c -> d` reads as four splittable groups, which is absurd."""
        spec = {"kind": "architecture",
                "nodes": [{"id": c, "role": "svc"} for c in "abcd"],
                "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "c"},
                          {"from": "c", "to": "d"}]}
        self.assertEqual([w for w in content_warnings(spec) if "barely-connected" in w], [])

    def test_two_big_groups_are_ordinary(self):
        spec = self.stages(names=("lint", "test"))
        self.assertEqual([w for w in content_warnings(spec) if "barely-connected" in w], [])

    def test_an_architecture_with_no_edges_is_valid(self):
        """A CI stage whose jobs have no dependencies is a picture of that stage. Requiring an
        edge made splitting a real pipeline impossible."""
        validate({"kind": "architecture", "title": "lint stage",
                  "nodes": [{"id": "a", "role": "svc"}, {"id": "b", "role": "svc"}]})


if __name__ == "__main__":
    unittest.main(verbosity=2)
