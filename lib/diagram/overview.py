"""Collapse an `architecture` spec to one box per area — the same subject, one altitude up.

Asked "how do the backend services work together", a real repo produced 25 boxes and 38 labelled
edges on a 4651x1120 canvas. Nothing about the rendering was wrong: the labels were correct, the
gates passed, and it was unreadable, because at that many edges the labels detach from the lines
they belong to and no layout engine, palette or compaction step recovers it.

The question, though, was asked about *areas*. Collapsed to its containers the same spec is 11
boxes and 19 edges — one screen, every label on its own edge — and the detail that gets dropped is
a different question, one diagram per area, which the splitting advice already covers.

Nothing here is a judgement, which is why it is code rather than guidance: the areas are the
containers the author already wrote, the edges between them are their edges, and the member list
under each name is its children. The counts are facts.

Two things it deliberately does NOT do:

  * decide *whether* to collapse. That depends on what was asked, and only the author knows.
  * invent areas for loose top-level nodes. A node that is not a container survives as itself —
    usually an entry point or an external system, which is exactly what you want to still see.
"""

from collections import defaultdict

from .d2 import MAX_DETAIL_LINES
from .spec import _walk, validate

# Members listed under an area's name before the rest become "+N more". One line is spent on that
# summary, so this is MAX_DETAIL_LINES - 1: four lines in the box, counting the area's own name.
MAX_MEMBERS = MAX_DETAIL_LINES - 1


def _outer(ref):
    return str(ref).split(".", 1)[0]


def _degree(edges):
    """How many edges touch each *fully qualified* node — the ranking for what to list."""
    counts = defaultdict(int)
    for edge in edges:
        for end in ("from", "to"):
            counts[str(edge.get(end, ""))] += 1
    return counts


def members(node, edges, limit=MAX_MEMBERS):
    """The children to name under an area, most-connected first, plus a "+N more" line.

    Ranked by degree rather than taken in order, because the interesting members of an area are
    the ones that talk to the rest of the system; a truncation that keeps the first two written
    would routinely drop the hub. Ties keep the author's order, which is usually meaningful.
    """
    counts = _degree(edges)
    kids = [(nid, child) for nid, child in _walk(node.get("children") or [])]
    labelled = [(child.get("label") or child["id"], counts[f"{node['id']}.{nid}"])
                for nid, child in kids]
    if not labelled:
        return []
    ranked = sorted(range(len(labelled)), key=lambda i: (-labelled[i][1], i))
    kept = [labelled[i][0] for i in ranked[:limit]]
    rest = len(labelled) - len(kept)
    return kept + ([f"+{rest} more"] if rest else [])


def collapse(spec, drop=(), limit=MAX_MEMBERS):
    """An `architecture` spec with every container reduced to a single box.

    `drop` removes top-level nodes entirely, for the entry point that touches every area: `api`
    and `tasks` were half of all 38 edge ends in the diagram that prompted this, and "everything
    is reachable from the API" is a sentence, not nineteen arrows. Their edges go with them.
    """
    validate(spec)
    if spec["kind"] != "architecture":
        raise ValueError(f"only an architecture can be collapsed, not {spec['kind']!r}")

    edges = spec.get("edges") or []
    keep = [n for n in spec["nodes"] if n["id"] not in drop]
    nodes = []
    for node in keep:
        flat = {"id": node["id"], "label": node.get("label") or node["id"]}
        # A container has no role of its own (spec.py rejects one), so collapsed it needs a role
        # to be anything but neutral. `svc` is the honest default for an area of services; a
        # caller that knows better can post-process.
        flat["role"] = node.get("role") or ("svc" if node.get("children") else "neutral")
        if node.get("shape"):
            flat["shape"] = node["shape"]
        named = members(node, edges, limit)
        if named:
            flat["detail"] = "\n".join(named)
        nodes.append(flat)

    merged = defaultdict(list)
    for edge in edges:
        a, b = _outer(edge.get("from")), _outer(edge.get("to"))
        # A self-edge here means both ends were inside one area: that is detail, and detail is
        # what this view gives up.
        if a == b or a in drop or b in drop:
            continue
        merged[(a, b)].append(edge.get("label") or "")

    out = []
    for (a, b), labels in merged.items():
        named = [text for text in labels if text]
        # One edge keeps its verb; several become a count, because four concatenated verbs
        # outrun the boxes they join. An unlabelled single edge gets no label at all.
        label = f"{len(labels)} calls" if len(labels) > 1 else (named[0] if named else "")
        out.append({"from": a, "to": b, "label": label} if label else {"from": a, "to": b})

    collapsed = {"kind": "architecture", "nodes": nodes, "edges": out}
    if spec.get("slug"):
        collapsed["slug"] = spec["slug"]
    if spec.get("title"):
        collapsed["title"] = f"{spec['title']} — areas"
    return collapsed
