#!/usr/bin/env python3
"""Tests for collapsing an architecture to one box per area.

The derivation has to be trustworthy in three ways, and each has a case here: it must not lose an
area, it must not invent an edge, and its truncation must keep the members that matter. The last
one is the only judgement in the module — members are ranked by how many edges touch them, so a
hub survives the cut — and it is the one a well-meaning simplification would undo.

Run: `python3 lib/diagram/test_overview.py`
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.diagram import overview
from lib.diagram.spec import SpecError, validate


def area(name, *kids):
    return {"id": name, "label": name.title(),
            "children": [{"id": k, "role": "svc"} for k in kids]}


def spec(**over):
    base = {
        "kind": "architecture", "title": "Backend",
        "nodes": [area("core", "hub", "quiet", "alsoquiet"),
                  area("ingest", "mail", "files"),
                  {"id": "api", "label": "API routers", "role": "client"}],
        "edges": [
            {"from": "api", "to": "core.hub", "label": "CRUD"},
            {"from": "ingest.mail", "to": "core.hub", "label": "create"},
            {"from": "ingest.files", "to": "core.hub", "label": "create"},
            {"from": "core.hub", "to": "core.quiet", "label": "validates"},
        ],
    }
    base.update(over)
    return base


class TestCollapse(unittest.TestCase):
    def test_every_top_level_node_survives(self):
        out = overview.collapse(spec())
        self.assertEqual([n["id"] for n in out["nodes"]], ["core", "ingest", "api"])

    def test_the_result_is_itself_a_valid_spec(self):
        validate(overview.collapse(spec()))

    def test_a_container_gets_a_role_so_it_is_not_neutral(self):
        """It had none — spec.py rejects a role on a container — so collapsed it needs one."""
        self.assertEqual(overview.collapse(spec())["nodes"][0]["role"], "svc")

    def test_a_loose_node_keeps_its_own_role(self):
        by_id = {n["id"]: n for n in overview.collapse(spec())["nodes"]}
        self.assertEqual(by_id["api"]["role"], "client")

    def test_edges_between_areas_are_merged_and_counted(self):
        edges = overview.collapse(spec())["edges"]
        self.assertIn({"from": "ingest", "to": "core", "label": "2 calls"}, edges)

    def test_a_single_edge_keeps_its_own_verb(self):
        edges = overview.collapse(spec())["edges"]
        self.assertIn({"from": "api", "to": "core", "label": "CRUD"}, edges)

    def test_an_edge_inside_one_area_disappears(self):
        """`core.hub -> core.quiet` is detail, and detail is what this view gives up."""
        for edge in overview.collapse(spec())["edges"]:
            self.assertNotEqual(edge["from"], edge["to"])
        self.assertEqual(len(overview.collapse(spec())["edges"]), 2)

    def test_dropping_an_entry_point_takes_its_edges_with_it(self):
        out = overview.collapse(spec(), drop=("api",))
        self.assertNotIn("api", [n["id"] for n in out["nodes"]])
        self.assertEqual([e["from"] for e in out["edges"]], ["ingest"])

    def test_the_title_says_it_is_the_area_view(self):
        self.assertEqual(overview.collapse(spec())["title"], "Backend — areas")

    def test_only_an_architecture_can_be_collapsed(self):
        with self.assertRaisesRegex(ValueError, "only an architecture"):
            overview.collapse({"kind": "state",
                               "states": [{"id": "a"}, {"id": "b"}],
                               "transitions": [{"from": "a", "to": "b", "label": "x"}]})

    def test_an_invalid_spec_is_rejected_before_anything_else(self):
        with self.assertRaises(SpecError):
            overview.collapse(spec(edges=[{"from": "api", "to": "typo"}]))


class TestMemberList(unittest.TestCase):
    def test_a_small_area_lists_all_of_its_members(self):
        by_id = {n["id"]: n for n in overview.collapse(spec())["nodes"]}
        self.assertEqual(by_id["ingest"]["detail"], "mail\nfiles")

    def test_a_big_area_truncates_with_a_count(self):
        """Three children, two listed, so one is left over."""
        by_id = {n["id"]: n for n in overview.collapse(spec())["nodes"]}
        self.assertEqual(by_id["core"]["detail"].splitlines(), ["hub", "quiet", "+1 more"])

    def test_the_truncation_keeps_the_most_connected_member(self):
        """Taking the first two written would drop the hub, which is the whole point of the box."""
        by_id = {n["id"]: n for n in overview.collapse(spec())["nodes"]}
        self.assertEqual(by_id["core"]["detail"].splitlines()[0], "hub")

    def test_a_tie_keeps_the_authors_order(self):
        listed = overview.members(area("z", "first", "second"), [])
        self.assertEqual(listed, ["first", "second"])

    def test_a_member_is_named_by_its_label_when_it_has_one(self):
        node = {"id": "z", "children": [{"id": "svc1", "label": "Nice Name", "role": "svc"}]}
        self.assertEqual(overview.members(node, []), ["Nice Name"])

    def test_a_loose_node_has_no_member_list(self):
        by_id = {n["id"]: n for n in overview.collapse(spec())["nodes"]}
        self.assertNotIn("detail", by_id["api"])

    def test_the_list_never_exceeds_the_line_budget(self):
        big = area("z", *[f"s{i}" for i in range(20)])
        self.assertLessEqual(len(overview.members(big, [])), overview.MAX_MEMBERS + 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
