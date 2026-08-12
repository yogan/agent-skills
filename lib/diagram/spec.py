"""The diagram spec: what a caller describes, independent of how d2 draws it.

A spec is plain JSON-able data — dicts, lists, strings — so a skill can build one from
whatever it explored (a diff, a schema, a set of classes) and hand it straight to
`render.py`. Everything d2-specific (which shape, which colour, which of its three
coupled table properties gets what) lives in `d2.py`, not here. A caller picks a *role*
("this is a datastore") and a *kind* ("this is a sequence diagram"); it never picks a
hex colour.

Validation is loud on purpose. Two mistakes are silent in d2 and expensive to debug:

  * a typo'd edge endpoint. `a -> tpyo` does not error — d2 invents an empty node and
    draws an edge to it, so the diagram renders "successfully" with a stray blank box.
  * a role or shape d2 will not theme. `shape: code` ships its own syntax-highlighting
    palette (measured: `#1e1e2e` / `#cdd6f4`) that our literal -> CSS-var substitution
    has no mapping for, so it stays light-mode-coloured in dark mode.

`validate()` rejects both. The soft limits — how much content fits — are separate
(`content_warnings()`), because they are predictions about the *rendered* size and the
size gate is the real authority on that.
"""

# The kinds map 1:1 onto the reader's question — see skills/visualize/SKILL.md for which
# question each one answers. That mapping is the playbook; this is just the vocabulary.
KINDS = ("architecture", "sequence", "er", "class", "state", "steps")

# A role is what a box *is*, not what colour it is. The same role must mean the same
# thing in every figure of a document, which is most of why a set of diagrams reads as
# one system rather than six unrelated pictures.
ROLES = ("client", "svc", "store", "cache", "ext", "neutral")

# A `state` takes its own vocabulary. The architectural roles describe what a thing *is* — a
# datastore, a cache, something outside our control — and a state is none of them, so tagging
# `live` as `store` and `backoff` as `cache` (which this corpus did) picks the colour and
# invents a justification. These four name what is actually being signalled, and they inherit
# the same palette entries: readers decode green-steady / amber-retrying / red-terminal by
# convention, without a legend, which is the one case where colour carries meaning here.
STATE_ROLES = ("working", "steady", "transient", "terminal", "neutral")

# Shapes that survive the literal -> CSS-var substitution, each verified by rendering it
# and checking for unmapped colour literals (that is the `theming` gate). Adding one
# means re-running that gate: `shape: code` is excluded because it failed it, bringing
# its own two-colour syntax theme that no palette entry claims.
SHAPES = (
    "rectangle", "cylinder", "hexagon", "queue", "document", "stored_data",
    "person", "diamond", "oval", "circle", "package", "step", "page", "cloud",
)

# d2 accepts exactly these `tooltip.near` constants and rejects everything else --
# `right-center` and `outside-*` are both errors, and `near: <some other object>` is
# rejected under the dagre layout engine. The placement pass (gates/browser side) picks
# one of these by measuring; a spec may also pin one explicitly.
NEAR = (
    "top-left", "top-center", "top-right",
    "center-left", "center-right",
    "bottom-left", "bottom-center", "bottom-right",
)

DIRECTIONS = ("up", "down", "left", "right")

# Soft limits, all from the prototype's measurements. d2 exposes no `ranksep`, so a
# sprawling diagram cannot be compacted after the fact — only authored smaller. These
# predict a size-gate failure early and with a better message ("split this diagram")
# than the gate itself can give ("height 1035px > 800px").
MAX_STATES = 6
MAX_MESSAGES = 7
MAX_SIBLINGS = 6      # direct children of one container, or of the diagram root
MAX_ROWS = 8          # columns in a table / members in a class
MAX_LABEL = 40        # characters
MAX_NOTE_WORDS = 5    # a callout is a margin note, not a sentence
MAX_STEPS = 4         # boards in an animation

# Transitions per state, above which a state diagram stops being readable even though every
# individual state and label is fine. What actually goes wrong is repetition: when one
# terminal is reachable from everywhere on the same trigger, the same word appears on three
# edges and the reader can no longer tell which label belongs to which line. Calibrated on
# two diagrams — the reference socket lifecycle sits at 1.4 and reads cleanly, a derived
# four-status machine with three `resolved` edges sits at 2.25 and does not. Advisory, like
# the rest of this file, so being wrong costs a line of output.
MAX_TRANSITIONS_PER_STATE = 2


class SpecError(ValueError):
    """A spec that cannot be rendered, or would render misleadingly.

    Raised rather than worked around: every case it covers produces a diagram that
    *looks* fine, so a caller that swallowed this would ship the broken picture.
    """


def _require(cond, msg):
    if not cond:
        raise SpecError(msg)


def _str(value, what):
    _require(isinstance(value, str) and value.strip(), f"{what} must be a non-empty string")
    return value


def _one_of(value, allowed, what):
    _require(value in allowed,
             f"{what}: {value!r} is not one of {', '.join(allowed)}")
    return value


def _list(spec, key, what):
    value = spec.get(key)
    _require(isinstance(value, list) and value, f"{what} needs a non-empty {key!r} list")
    return value


def _check_note(obj, where):
    """A `note` becomes a permanently-visible `tooltip.near` callout in the drawing."""
    if "note" not in obj:
        # `near` only ever positions a note's callout. On its own it does nothing, and the
        # likeliest reason it is here alone is a misspelt `note` key — in which case the
        # callout the author wanted is simply absent from the drawing, with no error.
        _require("near" not in obj,
                 f"{where}: `near` positions a `note`'s callout, but there is no `note` "
                 "here — did you misspell it?")
        return
    _str(obj["note"], f"{where}: note")
    if "near" in obj:
        _one_of(obj["near"], NEAR, f"{where}: near")


def _walk(nodes, prefix=""):
    """Yield (dotted_id, node) for a node tree, depth first.

    Dotted ids are d2's own addressing scheme (`browser.editor`), so a spec and the d2
    source it produces refer to a node the same way — one less mapping to get wrong.
    """
    for node in nodes:
        nid = f"{prefix}{node['id']}"
        yield nid, node
        for child in node.get("children", []) or []:
            yield from _walk([child], f"{nid}.")


def _check_nodes(nodes, where, ids, allow_new=False, roles=ROLES):
    """Validate a node tree and add every node's *fully qualified* id to `ids`.

    Only the outermost call populates `ids`, from a single `_walk` of the whole tree.
    Letting the recursive calls do it too was a real bug: a nested subtree walked from
    itself yields bare `editor` alongside the correct `browser.editor`, so an edge to
    the unqualified name passed validation and then drew d2's stray blank box — exactly
    the failure this module exists to prevent.
    """
    _check_tree(nodes, where, allow_new, roles)
    ids.update(nid for nid, _ in _walk(nodes))


def _check_tree(nodes, where, allow_new, roles=ROLES):
    _require(isinstance(nodes, list) and nodes, f"{where} needs a non-empty node list")
    seen_here = set()
    for node in nodes:
        _require(isinstance(node, dict), f"{where}: each node must be a dict")
        nid = _str(node.get("id"), f"{where}: node id")
        _require("." not in nid, f"{where}: node id {nid!r} may not contain '.' "
                                 "(nest it under `children` instead)")
        _require(nid not in seen_here, f"{where}: duplicate node id {nid!r}")
        seen_here.add(nid)
        if "label" in node:
            _str(node["label"], f"{where}: {nid} label")
        if "role" in node:
            _one_of(node["role"], roles, f"{where}: {nid} role")
        if "shape" in node:
            _one_of(node["shape"], SHAPES, f"{where}: {nid} shape")
        if node.get("new"):
            # A stroke-only accent says "something here is special" without saying WHAT,
            # and there is no legend to look it up in -- rejected as a general
            # change-marker in favour of `note` callouts, which say it in words. It stays
            # legal inside `steps`, where the per-step caption supplies the meaning.
            _require(allow_new, f"{where}: {nid} sets `new`, which is only meaningful in a "
                                "'steps' diagram (its caption says what changed). "
                                "Elsewhere use `note` to name the change in words.")
        _check_note(node, f"{where}: {nid}")
        children = node.get("children")
        if children is not None:
            _require(isinstance(children, list) and children,
                     f"{where}: {nid} has an empty `children` list; omit it instead")
            _require("role" not in node,
                     f"{where}: {nid} has children, so it is a container and takes the "
                     "container styling; remove its `role`")
            _check_tree(children, f"{where}: {nid}", allow_new)


def _check_edges(spec, ids, where, key="edges", required=True, allow_push=False):
    edges = spec.get(key) or []
    if required:
        _require(isinstance(edges, list) and edges, f"{where} needs a non-empty {key!r} list")
    for i, edge in enumerate(edges):
        _require(isinstance(edge, dict), f"{where}: {key}[{i}] must be a dict")
        for end in ("from", "to"):
            ref = _str(edge.get(end), f"{where}: {key}[{i}] {end}")
            # The whole reason this function exists: d2 silently invents a node for an
            # unknown endpoint rather than failing, so the typo ships as a blank box.
            _require(ref in ids,
                     f"{where}: {key}[{i}] {end}={ref!r} is not a node in this diagram "
                     f"(known: {', '.join(sorted(ids))})")
        if "label" in edge:
            _str(edge["label"], f"{where}: {key}[{i}] label")
        if "push" in edge:
            # Only a sequence message can be a push, because only there does an arrow mean
            # "A called B" in the first place — that is what makes "B never asked" a
            # distinction worth drawing. On a box-and-arrows diagram an edge is a
            # relationship, and `dashed` already covers the shades of one.
            _require(allow_push,
                     f"{where}: {key}[{i}] sets `push`, which is only meaningful on a "
                     "sequence message (an arrow there is a call; elsewhere it is a "
                     "relationship — use `dashed`)")
            _require(isinstance(edge["push"], bool),
                     f"{where}: {key}[{i}] push must be true or false")
    return edges


def _check_rows(spec, key, where, ids, row_key, row_extra=(), addressable=True):
    """Shared shape of the two table-like kinds (`er` tables, `class` classes).

    `addressable` is what separates them. An ER column is a real identifier and d2
    resolves `documents.owner_id` as an edge endpoint, which is how a relationship can
    point at one column instead of the whole table. A class member is free text — `+
    handleUpgrade()` — so it is a row label and nothing more; edges connect classes.
    """
    items = _list(spec, key, where)
    for item in items:
        _require(isinstance(item, dict), f"{where}: each {key[:-1]} must be a dict")
        tid = _str(item.get("id"), f"{where}: {key[:-1]} id")
        _require("." not in tid, f"{where}: {key[:-1]} id {tid!r} may not contain '.'")
        _one_of(item.get("role", "neutral"), ROLES, f"{where}: {tid} role")
        _check_note(item, f"{where}: {tid}")
        rows = item.get(row_key)
        _require(isinstance(rows, list) and rows,
                 f"{where}: {tid} needs a non-empty {row_key!r} list")
        seen = set()
        for row in rows:
            _require(isinstance(row, dict), f"{where}: {tid} {row_key} entries must be dicts")
            name = _str(row.get("name"), f"{where}: {tid} {row_key} name")
            _require(name not in seen, f"{where}: {tid} has duplicate {row_key} {name!r}")
            seen.add(name)
            if "type" in row:
                _require(isinstance(row["type"], str), f"{where}: {tid}.{name} type must be a string")
            for extra, allowed in row_extra:
                if extra in row:
                    _one_of(row[extra], allowed, f"{where}: {tid}.{name} {extra}")
            if addressable:
                ids.add(f"{tid}.{name}")
        ids.add(tid)
    return items


KEYS = ("pk", "fk", "unique")

# Node id the `steps` emitter claims for the caption box — see _check_steps.
CAPTION_ID = "caption"


def validate(spec):
    """Raise SpecError unless `spec` describes a renderable diagram. Returns the spec."""
    _require(isinstance(spec, dict), "a spec must be a dict")
    kind = _one_of(spec.get("kind"), KINDS, "kind")
    if "direction" in spec:
        _one_of(spec["direction"], DIRECTIONS, "direction")
    if "title" in spec:
        _str(spec["title"], "title")
    ids = set()

    if kind in ("architecture", "steps"):
        _check_nodes(_list(spec, "nodes", kind), kind, ids, allow_new=(kind == "steps"))
        _check_edges(spec, ids, kind)
        if kind == "steps":
            _check_steps(spec, ids)
    elif kind == "sequence":
        # Participants are columns and d2 orders them as written -- the one thing
        # graphviz could not do at all, and the reason d2 is the engine.
        _check_flat(_list(spec, "participants", kind), "sequence", "participants")
        _check_nodes(spec["participants"], kind, ids)
        for i, participant in enumerate(spec["participants"]):
            if "group" in participant:
                # Colour says one thing per diagram. With groups it says "same side of the
                # wire"; a `role` alongside would silently lose, and this codebase rejects
                # rather than ignores — see the container/`role` check above.
                _str(participant["group"], f"sequence: participants[{i}] group")
                _require("role" not in participant,
                         f"sequence: participants[{i}] sets both `group` and `role`. In a "
                         "sequence the lane colour means the group, so the role would be "
                         "ignored — drop it.")
            if "detail" in participant:
                # A lane may be a subsystem rather than one module — see SKILL.md on lane
                # altitude. `detail` is where the real names go, so the reader can still grep
                # for something after the lane itself has been given an abstract name.
                _str(participant["detail"], f"sequence: participants[{i}] detail")
        _check_edges(spec, ids, kind, key="messages", allow_push=True)
    elif kind == "er":
        _check_rows(spec, "tables", kind, ids, "columns", row_extra=(("key", KEYS),))
        _check_edges(spec, ids, kind, required=False)
    elif kind == "class":
        _check_rows(spec, "classes", kind, ids, "members", addressable=False)
        _check_edges(spec, ids, kind, required=False)
    elif kind == "state":
        _check_flat(_list(spec, "states", kind), "state", "states")
        _check_nodes(spec["states"], kind, ids, roles=STATE_ROLES)
        _check_edges(spec, ids, kind, key="transitions")
    return spec


def _check_flat(nodes, kind, key):
    """`sequence` and `state` have no containers — reject nesting before the generic
    tree check does, so the caller is told the actual rule rather than being sent off to
    remove a `role` that was never the problem."""
    for i, node in enumerate(nodes):
        if isinstance(node, dict):
            _require("children" not in node, f"{kind}: {key}[{i}] cannot have children")


def _check_steps(spec, ids):
    """The `steps` kind: a base diagram plus per-step deltas.

    Each step is a separate d2 board, and d2 animates between them. It earns its keep
    only when consecutive boards differ in *topology* — "both paths run at once, then
    the old one is removed" is a thing no single static diagram can say. d2 does not
    render board names into the SVG, so a caption is required: without it the reader
    cannot tell which phase is on screen.
    """
    steps = _list(spec, "steps", "steps")
    _str(spec.get("caption"), "steps: the base diagram needs a `caption` (phase 1's text)")
    # The emitter adds a borderless node called `caption` to carry the phase text, because
    # d2 does not render board names into the SVG. A node of the same name would be
    # silently merged into it and inherit its invisible styling.
    _require(CAPTION_ID not in ids,
             f"steps: {CAPTION_ID!r} is reserved — the renderer uses a node of that name "
             "to display the per-step caption. Rename the node.")
    known = set(ids)
    for i, step in enumerate(steps):
        where = f"steps: steps[{i}]"
        _require(isinstance(step, dict), f"{where} must be a dict")
        if "caption" in step:
            _str(step["caption"], f"{where}: caption")
        if step.get("add_nodes"):
            _check_nodes(step["add_nodes"], where, known, allow_new=True)
        for key in ("add_edges", "remove_edges", "relabel_edges", "emphasize_edges"):
            _check_edges(step, known, where, key=key, required=False)
        for edge in step.get("relabel_edges") or []:
            _str(edge.get("label"), f"{where}: relabel_edges needs a `label`")


# The cardinality vocabulary itself: digits, punctuation, and the n/m that stand for "many".
# Anything left over after removing these is a real word — i.e. the label names its entities.
# `n` and `m` have to be in here or "n : 1" passes for containing a letter, which it did.
_RATIO_CHARS = set("0123456789nm:.,?*|+-()[]{}<>/ \t")


def _names_something(label):
    """Whether `label` says more than a bare ratio."""
    return any(c not in _RATIO_CHARS for c in label.lower())


def content_warnings(spec):
    """Advisory limits — a rendering constraint, not an editorial one.

    d2 cannot compact a sprawling diagram after the fact, so anything over these is
    likely to fail the size gate or be scaled down until its text is unreadable. The
    fix is never "shrink it": it is to split the subject into two diagrams that each
    answer a narrower question. Advisory because the gate measures the real thing and
    a diagram with very short labels can carry a little more than these numbers.
    """
    out = []
    kind = spec.get("kind")

    def label_of(node):
        return node.get("label") or node.get("id") or ""

    def check_labels(items, what):
        for item in items:
            text = label_of(item)
            if len(text) > MAX_LABEL:
                out.append(f"{what} label is {len(text)} chars (>{MAX_LABEL}): {text!r} — "
                           "long labels widen the diagram, which scales it down and "
                           "shrinks every glyph with it")
            for ascii_arrow in ("->", "=>"):
                if ascii_arrow in text:
                    # Everything around it is typeset — italic edge labels, a real arrowhead
                    # at the end of the line — so an ASCII arrow inside the text reads as
                    # source code that escaped. `→` is one glyph, renders in d2's default
                    # font, and measured 4px NARROWER than `->` on the diagram that prompted
                    # this. Nothing here is wrong, hence a warning.
                    out.append(f"{what} label writes an arrow as {ascii_arrow!r}: {text!r} — "
                               "use → , which is one typeset glyph rather than two "
                               "characters of ASCII")

    def check_notes(items, what):
        for item in items:
            note = item.get("note")
            if note and len(note.split()) > MAX_NOTE_WORDS:
                out.append(f"{what} note is {len(note.split())} words "
                           f"(>{MAX_NOTE_WORDS}): {note!r} — a wide callout has fewer "
                           "places it can sit without being clipped")

    if kind in ("architecture", "steps"):
        nodes = spec.get("nodes") or []
        if len(nodes) > MAX_SIBLINGS:
            out.append(f"{len(nodes)} top-level nodes (>{MAX_SIBLINGS})")
        for nid, node in _walk(nodes):
            kids = node.get("children") or []
            if len(kids) > MAX_SIBLINGS:
                out.append(f"container {nid} has {len(kids)} children (>{MAX_SIBLINGS})")
        flat = [node for _, node in _walk(nodes)]
        check_labels(flat, "node")
        check_notes(flat, "node")
        check_labels(spec.get("edges") or [], "edge")
    elif kind == "sequence":
        msgs = spec.get("messages") or []
        if len(msgs) > MAX_MESSAGES:
            # This used to be arithmetic: d2 hardcodes a ~86px row pitch, and nothing it
            # accepts as input moves it (font-size barely, `--dagre-nodesep` and
            # `--layout elk` not at all — sequence diagrams have their own layout engine).
            # compact.py now re-stacks the rows to ~43px in the rendered SVG, so height is
            # no longer the binding reason and this is an editorial limit: past seven
            # messages the reader is following a program, not reading a diagram.
            out.append(f"{len(msgs)} messages (>{MAX_MESSAGES}) — a sequence past this "
                       "is usually two questions, and reads as neither")
        check_labels(msgs, "message")
        participants = spec.get("participants") or []
        check_labels(participants, "participant")
        check_notes(participants, "participant")
        grouped = [p for p in participants if p.get("group")]
        if grouped and len(grouped) != len(participants):
            ungrouped = [p["id"] for p in participants if not p.get("group")]
            out.append(f"some lanes are grouped and some are not ({', '.join(ungrouped)} "
                       "left out) — a half-coloured row reads as an accident rather than as "
                       "two sides")
        seen = []
        for name in [p.get("group") for p in participants]:
            if name and (not seen or seen[-1] != name):
                if name in seen:
                    # A nudge, not a rule, and deliberately not a gate: a split group costs
                    # the reader a little reassembly, and lanes that talk to each other
                    # sitting far apart costs them a long arrow across the whole canvas. Which
                    # is worse depends on the flow, and only the author can see both.
                    out.append(f"group {name!r} is split across the row — usually worth "
                               "reordering so the block reads as one side, unless keeping "
                               "them apart is what keeps the arrows short")
                seen.append(name)
    elif kind == "state":
        states = spec.get("states") or []
        transitions = spec.get("transitions") or []
        if len(states) > MAX_STATES:
            out.append(f"{len(states)} states (>{MAX_STATES})")
        if states and len(transitions) > MAX_TRANSITIONS_PER_STATE * len(states):
            out.append(
                f"{len(transitions)} transitions across {len(states)} states "
                f"(>{MAX_TRANSITIONS_PER_STATE} per state) — check for a trigger you have "
                "drawn several times. If one state is reachable from everywhere on the same "
                "trigger, that is one fact, not one edge per source: draw the path that "
                "matters and put \"from any state\" in its note")
        check_labels(states, "state")
        check_notes(states, "state")
        check_labels(transitions, "transition")
    elif kind in ("er", "class"):
        # A relationship's cardinality is most of what an ER diagram tells a reader, and a bare
        # ratio does not carry it: "n : 1" leaves them working out which end is which and then
        # mapping it back to the table names. "1 doc : n sessions" reads by itself. This is
        # checkable because a label that names its entities necessarily contains a word.
        for edge in spec.get("edges") or []:
            label = (edge.get("label") or "").strip()
            where = f"{edge.get('from')} -> {edge.get('to')}"
            if not label:
                out.append(f"{where} has no label — an unlabelled relationship is the least "
                           "interesting half of the answer")
            elif kind == "er" and not _names_something(label):
                out.append(f"{where} is labelled {label!r} — name the entities instead "
                           "(\"1 doc : n sessions\"), so the reader does not have to work out "
                           "which end is which")
        key, row_key, noun = (("tables", "columns", "table") if kind == "er"
                              else ("classes", "members", "class"))
        items = spec.get(key) or []
        for item in items:
            rows = item.get(row_key) or []
            if len(rows) > MAX_ROWS:
                out.append(f"{item.get('id')} has {len(rows)} {row_key} (>{MAX_ROWS}) — "
                           f"show the {row_key} the change touches, not everything")
        check_labels(items, noun)
        check_notes(items, noun)
        check_labels(spec.get("edges") or [], "edge")

    if kind == "steps" and len(spec.get("steps") or []) > MAX_STEPS:
        out.append(f"{len(spec['steps'])} steps (>{MAX_STEPS}) — an animation the reader "
                   "cannot hold in their head explains less than a few well-chosen boards")
    return out
