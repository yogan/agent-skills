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
KINDS = ("architecture", "sequence", "er", "class", "state")

# A role is what a box *is*, not what colour it is. The same role must mean the same
# thing in every figure of a document, which is most of why a set of diagrams reads as
# one system rather than six unrelated pictures.
ROLES = ("client", "svc", "store", "cache", "ext", "neutral")

# A `state` takes its own vocabulary. The architectural roles describe what a thing *is* — a
# datastore, a cache, something outside our control — and a state is none of them, so tagging
# `live` as `store` and `backoff` as `cache` (which this corpus did) picks the colour and
# invents a justification. These name what is actually being signalled, and they inherit the
# same palette entries: readers decode green-steady / amber-retrying / red-terminal by
# convention, without a legend, which is the one case where colour carries meaning here.
#
# `stuck` is the exception and was added after a real figure failed on it. A file-journey
# machine had "deferred" (waiting out a grace window, returns by itself) and "imported, still
# there" (the delete failed, the next scan has to clean it up) both tagged `transient`, and the
# first reader asked why two states that mean such different things were the same colour. They
# are not the same kind of state: one is on its way, the other is somewhere it should not be.
#
# It takes purple, the one palette entry no state role had claimed. Purple carries no
# convention — which is the honest description of what it says: "a different kind of state from
# the ones around it", and no more. So the LABEL has to name the problem, the way `deferred`
# and `imported, still there` already do. A `stuck` state called `state_4` says nothing at all.
STATE_ROLES = ("working", "steady", "transient", "stuck", "terminal", "neutral")

# Shapes that survive the literal -> CSS-var substitution, each verified by rendering it
# and checking for unmapped colour literals (that is the `theming` gate). Adding one
# means re-running that gate: `shape: code` is excluded because it failed it, bringing
# its own two-colour syntax theme that no palette entry claims.
SHAPES = (
    "rectangle", "cylinder", "hexagon", "queue", "document", "stored_data",
    "person", "diamond", "oval", "circle", "package", "step", "page", "cloud",
)

# d2 accepts exactly these `tooltip.near` constants and rejects everything else --
# `right-center` and `outside-*` are both errors, and so is `near: <some other object>`,
# which this file used to blame on the dagre layout engine. It is not engine-specific: d2
# refuses it at compile time under both engines ("invalid \"near\" field"), which is worth
# knowing before anyone tries to anchor a callout to a neighbour. The placement pass
# (gates/browser side) picks one of these by measuring; a spec may also pin one explicitly.
#
# THE ORDER IS THE TIE-BREAK, and it is deliberate. Several anchors on one diagram routinely
# cover nothing at all — on the reference ER, seven of these eight do — and among those the
# search has nothing left to measure, so it keeps the first one it saw. Reading order (top row
# first, left first) is what that resolves to, and the effect is that callouts which are all
# equally free land on the same side as each other rather than scattering. See `place._score`.
NEAR = (
    "top-left", "top-center", "top-right",
    "center-left", "center-right",
    "bottom-left", "bottom-center", "bottom-right",
)

# The layout axis. NOT a key an author may write, and the constant is kept because the
# renderer sets it internally — `render._pick_at_spacing` and `place._measure_candidates` put
# one of these on a COPY of the spec to tell the emitter which way to rank the graph, after
# validation has run. See `validate`.
DIRECTIONS = ("up", "down", "left", "right")

# What a sequence message did, when the flow's point is that it can go two ways. Colour is
# allowed to carry this — the one other place it carries anything, alongside a `state`'s roles
# — because red-for-failed and green-for-succeeded is a convention the reader already has, and
# the label says it in words anyway. Contrast with `push`, where colour was rejected precisely
# because a reader has no convention for "the receiver did not ask for this".
OUTCOMES = ("ok", "error")

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

# Transitions per state, above which a state diagram stops being readable even though every
# individual state and label is fine. What actually goes wrong is repetition: when one
# terminal is reachable from everywhere on the same trigger, the same word appears on three
# edges and the reader can no longer tell which label belongs to which line. Calibrated on
# two diagrams — the reference socket lifecycle sits at 1.4 and reads cleanly, a derived
# four-status machine with three `resolved` edges sits at 2.25 and does not. Advisory, like
# the rest of this file, so being wrong costs a line of output.
MAX_TRANSITIONS_PER_STATE = 2

# Disconnected components above which a diagram is worth splitting. Two is ordinary — a schema
# can hold an unrelated table. At three the picture is several unrelated graphs sharing a canvas,
# packed along the CROSS axis, so the width is the sum of all of them and no `direction` fixes
# it: a real CI pipeline came out 3433x693 (aspect 5.0) as four components, most of the canvas
# being jobs with no edges at all. That was measured under dagre; the layout engine has changed
# since, and the packing is a property of hierarchical layout rather than of one engine, so the
# shape of the problem holds even though the exact numbers would differ. Advisory either way,
# because sometimes the answer really is "here is everything at once".
MAX_COMPONENTS = 2

# Boxes below which a barely-connected diagram is left alone. Counting groups is not enough on
# its own: in any chain every edge is a bridge, so `a → b → c → d` looks like four groups joined
# by three cuttable edges, and splitting that would be absurd. Width only hurts once there is
# real content — the pipelines that prompted this carried 18 boxes across their groups, where the
# reference architecture holds three per area. Two full rows' worth (2 x MAX_SIBLINGS) is
# the line.
MIN_SPLIT_BOXES = 12


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


def _check_new(item, where):
    """`new` marks the one thing a change added, and it may not speak alone.

    Colour without words says "something here is special" and never says what, and there is no
    legend to look it up in — which is why this was rejected outright for a while. Pairing it
    with a `note` is what makes it legible: the accent carries the eye, and two words say why.
    """
    if "new" not in item:
        return
    _require(isinstance(item["new"], bool), f"{where}: `new` must be true or false")
    _require(not item["new"] or item.get("note"),
             f"{where} sets `new` without a `note`. The accent colour has no legend, so it "
             "cannot say WHAT is new on its own — add a note of a word or two (\"new\", "
             "\"added\", \"gains a revision column\").")


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


def _check_nodes(nodes, where, ids, roles=ROLES):
    """Validate a node tree and add every node's *fully qualified* id to `ids`.

    Only the outermost call populates `ids`, from a single `_walk` of the whole tree.
    Letting the recursive calls do it too was a real bug: a nested subtree walked from
    itself yields bare `editor` alongside the correct `browser.editor`, so an edge to
    the unqualified name passed validation and then drew d2's stray blank box — exactly
    the failure this module exists to prevent.
    """
    _check_tree(nodes, where, roles)
    ids.update(nid for nid, _ in _walk(nodes))


def _check_tree(nodes, where, roles=ROLES):
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
        if "detail" in node:
            # Smaller lines under the name. On a sequence lane it carries the modules behind an
            # abstract lane; on an architecture box, the members behind an aggregated area.
            _str(node["detail"], f"{where}: {nid} detail")
        _check_new(node, f"{where}: {nid}")
        _check_note(node, f"{where}: {nid}")
        children = node.get("children")
        if children is not None:
            _require(isinstance(children, list) and children,
                     f"{where}: {nid} has an empty `children` list; omit it instead")
            _require("role" not in node,
                     f"{where}: {nid} has children, so it is a container and takes the "
                     "container styling; remove its `role`")
            _require(not node.get("new"),
                     f"{where}: {nid} has children, so it is a container and takes the "
                     "container styling — `new` would be silently ignored. Mark the child "
                     "the change added, or say it in the container's `note`.")
            _check_tree(children, f"{where}: {nid}")


def _check_edges(spec, ids, where, key="edges", required=True, allow_push=False,
                 allow_both=False):
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
        if "bidirectional" in edge:
            # Architecture only, and for the same reason `push` is sequence only: it has to
            # mean something there and nothing anywhere else. On an architecture edge it means
            # traffic crosses both ways — `read · write` against a database, a request and its
            # reply — and drawing that as one arrow understates it while drawing two arrows
            # doubles the ink and the label. A `state` transition goes one way by definition, a
            # sequence message is one call, an ER foreign key has a direction, and a class
            # relationship that is symmetric is better said in the label.
            _require(allow_both,
                     f"{where}: {key}[{i}] sets `bidirectional`, which is only meaningful on "
                     "an architecture edge (a transition, a message and a foreign key all go "
                     "one way by definition)")
            _require(isinstance(edge["bidirectional"], bool),
                     f"{where}: {key}[{i}] bidirectional must be true or false")
        if "outcome" in edge:
            # Same reasoning as `push`: it is about a call succeeding or failing, and only a
            # sequence has calls. Elsewhere an edge is a relationship, which has no outcome.
            _require(allow_push,
                     f"{where}: {key}[{i}] sets `outcome`, which is only meaningful on a "
                     "sequence message — a relationship does not succeed or fail")
            _one_of(edge["outcome"], OUTCOMES, f"{where}: {key}[{i}] outcome")
    return edges


def _check_rows(spec, key, where, ids, row_key, row_extra=(), addressable=True):
    """Shared shape of the two table-like kinds (`er` tables, `class` classes).

    `addressable` used to be what separated them: an ER column is a plain identifier, while a
    class member is free text (`+ handleUpgrade()`), so members were labels and nothing more.
    That turned out to be a limitation of the LAYOUT ENGINE rather than of d2's syntax — quoted,
    a member resolves as an endpoint like any other row — and it cost a real figure: `raises`
    left the whole box, so a reader of four methods could not tell which one raised. Both kinds
    are addressable now, and the renderer's one layout engine honours it — see `d2.ELK_OPTS`.

    A name containing a dot is still not addressable, because an endpoint is split on dots to
    build the path — `a.b` as a member name would address a row called `b` inside a table
    called `a`. Rare enough to exclude rather than escape.
    """
    items = _list(spec, key, where)
    for item in items:
        _require(isinstance(item, dict), f"{where}: each {key[:-1]} must be a dict")
        tid = _str(item.get("id"), f"{where}: {key[:-1]} id")
        _require("." not in tid, f"{where}: {key[:-1]} id {tid!r} may not contain '.'")
        _one_of(item.get("role", "neutral"), ROLES, f"{where}: {tid} role")
        _check_new(item, f"{where}: {tid}")
        _check_note(item, f"{where}: {tid}")
        rows = item.get(row_key)
        # An empty box draws as a bare header with a zero-height body, which reads as a
        # rendering fault rather than as "this type has nothing worth showing". The case
        # that hits it is a base class drawn only so something can point at it, and the
        # answer there is the one member that says why it is being pointed at.
        _require(isinstance(rows, list) and rows,
                 f"{where}: {tid} needs a non-empty {row_key!r} list — for a base class you "
                 f"are only drawing so another box can point at it, give it the one member "
                 f"that earns it a place (`status = 400`), or drop the box and say "
                 f"\"extends X\" in the other one's label")
        seen = set()
        for row in rows:
            _require(isinstance(row, dict), f"{where}: {tid} {row_key} entries must be dicts")
            name = _str(row.get("name"), f"{where}: {tid} {row_key} name")
            _require(name not in seen, f"{where}: {tid} has duplicate {row_key} {name!r}")
            seen.add(name)
            if "type" in row:
                _require(isinstance(row["type"], str),
                         f"{where}: {tid}.{name} type must be a string")
            for extra, allowed in row_extra:
                if extra in row:
                    _one_of(row[extra], allowed, f"{where}: {tid}.{name} {extra}")
            if addressable and "." not in name:
                ids.add(f"{tid}.{name}")
        ids.add(tid)
    return items


KEYS = ("pk", "fk", "unique")


def validate(spec):
    """Raise SpecError unless `spec` describes a renderable diagram. Returns the spec."""
    _require(isinstance(spec, dict), "a spec must be a dict")
    kind = _one_of(spec.get("kind"), KINDS, "kind")
    # Checked, not rejected: `d2.emit` validates on every call, and by then the layout search
    # has set this on a copy of the spec. Rejecting it here breaks the search that sets it.
    # An AUTHOR may not write it, and that is enforced where authors come in — `figure.draw`.
    if "direction" in spec:
        _one_of(spec["direction"], DIRECTIONS, "direction")
    if "title" in spec:
        _str(spec["title"], "title")
    ids = set()

    if kind == "architecture":
        _check_nodes(_list(spec, "nodes", kind), kind, ids)
        # `edges` is optional, as it is for `er`. Requiring one made a legitimate diagram
        # unrenderable and only showed up when splitting a real pipeline: a CI stage whose five
        # jobs have no dependencies between them is a picture of that stage, and refusing it
        # forced the whole pipeline back into one 3433px-wide canvas.
        _check_edges(spec, ids, kind, required=False, allow_both=True)
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
        _check_rows(spec, "classes", kind, ids, "members")
        _check_edges(spec, ids, kind, required=False)
    elif kind == "state":
        _check_flat(_list(spec, "states", kind), "state", "states")
        _check_nodes(spec["states"], kind, ids, roles=STATE_ROLES)
        _check_start(spec["states"])
        _check_edges(spec, ids, kind, key="transitions")
    return spec


def _check_start(states):
    """`start: true` marks where the machine begins, and exactly one state may claim it.

    A reader cannot otherwise tell: being written first, or drawn at the top, is an accident of
    layout, and a state with no incoming transition looks like every other state. Two starts is
    a hard error rather than a "first one wins", because a machine with two entry points is
    either two machines or a spec that was edited without reading — and the renderer can only
    draw one dot.
    """
    marked = [s["id"] for s in states if s.get("start")]
    for state in states:
        if "start" in state:
            _require(isinstance(state["start"], bool),
                     f"state: {state['id']} `start` must be true or false")
    _require(len(marked) < 2,
             f"state: {' and '.join(repr(m) for m in marked)} both set `start`. A machine has "
             "one place it begins; if this really has two entry points, they are two diagrams.")


def _check_flat(nodes, kind, key):
    """`sequence` and `state` have no containers — reject nesting before the generic
    tree check does, so the caller is told the actual rule rather than being sent off to
    remove a `role` that was never the problem."""
    for i, node in enumerate(nodes):
        if isinstance(node, dict):
            _require("children" not in node, f"{kind}: {key}[{i}] cannot have children")


# The cardinality vocabulary itself: digits, punctuation, and the n/m that stand for "many".
# Anything left over after removing these is a real word — i.e. the label names its entities.
# `n` and `m` have to be in here or "n : 1" passes for containing a letter, which it did.
_RATIO_CHARS = set("0123456789nm:.,?*|+-()[]{}<>/ \t")


def _names_something(label):
    """Whether `label` says more than a bare ratio."""
    return any(c not in _RATIO_CHARS for c in label.lower())


# Shorter than this and a word is not enough to identify a table by: "id" would match
# anything, and a spec is free to name its tables `a` and `b`, where every label mentions both.
_STEM_MIN = 3


def _stem(word):
    """A word reduced to what a table name and a label can plausibly share.

    Just the plural: `docs`/`documents` do not match as prefixes, but `doc`/`document` do, and
    that pair is the whole reason this exists — an author writes the short form of a name.
    """
    word = "".join(c for c in word.lower() if c.isalnum() or c == "_")
    return word[:-1] if word.endswith("s") and len(word) > _STEM_MIN else word


def _mentions(half, table):
    """Whether the words in `half` name the table `table` — matched on stems, either way
    round, so `doc` finds `documents` and `documents` finds `doc`."""
    stems = {_stem(part) for part in str(table).split("_")}
    for word in str(half).split():
        word = _stem(word)
        if len(word) < _STEM_MIN:
            continue
        if any(len(stem) >= _STEM_MIN and (word.startswith(stem) or stem.startswith(word))
               for stem in stems):
            return True
    return False


def _reads_backwards(label, source, target):
    """Whether a cardinality names its two tables in the opposite order to its arrow.

    `presence_sessions -> documents` labelled "1 doc : n sessions" is true and reads
    right-to-left against an arrow that flies left-to-right, so the reader has to reconcile the
    two. "n sessions : 1 doc" says the same thing and agrees with the picture.

    Deliberately hard to trip. It fires only when BOTH ends are positively identified AND they
    are swapped, so anything it cannot read confidently — "belongs to", "n : m via
    email_documents", a self-edge, a name too short to match — passes silently. A warning about
    reading order that fired on a label it had misread would be worse than no warning: the
    remedy it suggests would make a correct label wrong.
    """
    left, colon, right = str(label).partition(":")
    if not colon:
        return False
    source, target = str(source).split(".")[0], str(target).split(".")[0]
    swapped = _mentions(left, target) and _mentions(right, source)
    correct = _mentions(left, source) and _mentions(right, target)
    return swapped and not correct


def components(names, edges):
    """Group top-level `names` into connected islands, ignoring edge direction.

    An edge endpoint may be a dotted path into a container (`k8s.api.pod`); only its first
    segment matters here, because a link reaching into a container joins that whole container to
    whatever it came from — which is what the layout engine packs. Returns one sorted list per
    island, in the order their first member appears.
    """
    parent = {name: name for name in names}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for edge in edges:
        ends = [str(edge.get(end, "")).split(".", 1)[0] for end in ("from", "to")]
        if all(end in parent for end in ends):
            a, b = find(ends[0]), find(ends[1])
            if a != b:
                parent[a] = b

    islands = {}
    for name in names:
        islands.setdefault(find(name), []).append(name)
    return [sorted(group) for group in islands.values()]


def bridges(names, edges):
    """Edges whose removal would split the graph — the `(from, to)` pairs, ignoring direction.

    A group joined to the rest by exactly one edge is still a candidate for its own diagram: cut
    there and one cross-reference replaces the arrow. Without this, a single speculative edge
    hides every split opportunity in a drawing — which is exactly what happened to a CI pipeline
    where "stage → stage: on success" was added between four otherwise disjoint stages.

    Standard lowlink search, iterative so a wide graph cannot recurse too deep.
    """
    adj = {name: [] for name in names}
    pairs = []
    for edge in edges:
        a, b = (str(edge.get(end, "")).split(".", 1)[0] for end in ("from", "to"))
        if a in adj and b in adj and a != b:
            index = len(pairs)
            pairs.append((a, b))
            adj[a].append((b, index))
            adj[b].append((a, index))

    seen, low, disc, found = {}, {}, {}, []
    timer = 0
    for root in names:
        if root in seen:
            continue
        stack = [(root, None, iter(adj[root]))]
        seen[root] = True
        disc[root] = low[root] = timer
        timer += 1
        while stack:
            node, via, neighbours = stack[-1]
            advanced = False
            for nxt, index in neighbours:
                if index == via:
                    continue
                if nxt not in seen:
                    seen[nxt] = True
                    disc[nxt] = low[nxt] = timer
                    timer += 1
                    stack.append((nxt, index, iter(adj[nxt])))
                    advanced = True
                    break
                low[node] = min(low[node], disc[nxt])
            if not advanced:
                stack.pop()
                if stack:
                    parent = stack[-1][0]
                    low[parent] = min(low[parent], low[node])
                    if low[node] > disc[parent]:
                        found.append(pairs[via])
    return found


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

    def check_edge_labels(edges, noun):
        """An edge with no label repeats the one thing the line already said.

        Shared by every kind that has one, which now includes `architecture`. It did not, and
        the reference corpus carried a bare `GraphQL API -> PostgreSQL` for a long time next to
        a `verify JWT` that says what its arrow is for — a gap nobody saw because the check
        that would have caught it lived inside the er/class branch.
        """
        for edge in edges:
            if not str(edge.get("label") or "").strip():
                out.append(f"{edge.get('from')} -> {edge.get('to')} has no label — an "
                           f"unlabelled {noun} is the least interesting half of the answer")

    def check_islands(names, edges, what, boxes):
        """Several barely-related graphs on one canvas is the widest a diagram ever gets, and
        the one oversize the author can always fix: they are nearly separate pictures already.

        Groups are counted with the single-edge joins cut, not just the absent ones. A first
        version only looked for fully disconnected groups and a real pipeline slipped straight
        past it: the author had added `stage → stage: on success` between four otherwise
        unconnected stages, and two speculative edges were enough to make the whole thing look
        like one graph.
        """
        cuts = bridges(names, edges)
        held = [e for e in edges
                if (str(e.get("from", "")).split(".", 1)[0],
                    str(e.get("to", "")).split(".", 1)[0]) not in cuts
                and (str(e.get("to", "")).split(".", 1)[0],
                     str(e.get("from", "")).split(".", 1)[0]) not in cuts]
        islands = components(names, held)
        if len(islands) <= MAX_COMPONENTS or boxes < MIN_SPLIT_BOXES:
            return
        listed = "; ".join(", ".join(group[:4]) + ("…" if len(group) > 4 else "")
                           for group in islands)
        joined = (f" They are held together by {len(cuts)} single edge(s), each of which a "
                  "cross-reference can replace." if cuts else "")
        out.append(f"{len(islands)} barely-connected groups of {what} ({listed}) — the layout "
                   "engine packs them side by side, so the width is the sum of all of them."
                   f"{joined} Consider one diagram per group, with a `note` pointing at the "
                   "other part wherever a link is cut")

    if kind == "architecture":
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
        check_edge_labels(spec.get("edges") or [], "connection")
        check_islands([n["id"] for n in nodes], spec.get("edges") or [], "nodes",
                      sum(1 for _, node in _walk(nodes) if not node.get("children")))
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
        for participant in participants:
            if participant.get("detail"):
                # Import here: spec.py is the vocabulary and d2.py the recipe, so the wrap width
                # (a property of how the box is drawn) lives there, not here.
                from .d2 import MAX_DETAIL_LINES, wrap_detail
                lines = wrap_detail(participant["detail"])
                if len(lines) > MAX_DETAIL_LINES:
                    out.append(
                        f"participant {participant['id']}'s detail wraps to {len(lines)} lines "
                        f"(>{MAX_DETAIL_LINES}): {participant['detail']!r} — at that length it "
                        "is not a subtitle any more. Name the lane and let a `note` or its own "
                        "diagram carry the rest")
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
        if states and not any(s.get("start") for s in states):
            out.append("no state is marked `start: true`, so nothing says where the machine "
                       "begins — the reader is left inferring it from the layout, which is "
                       "the one thing the layout does not mean")
        check_labels(states, "state")
        check_notes(states, "state")
        check_labels(transitions, "transition")
        check_islands([s["id"] for s in states], transitions, "states", len(states))
    elif kind in ("er", "class"):
        # A relationship's cardinality is most of what an ER diagram tells a reader, and a bare
        # ratio does not carry it: "n : 1" leaves them working out which end is which and then
        # mapping it back to the table names. "1 doc : n sessions" reads by itself. This is
        # checkable because a label that names its entities necessarily contains a word.
        check_edge_labels(spec.get("edges") or [], "relationship")
        for edge in spec.get("edges") or []:
            label = (edge.get("label") or "").strip()
            where = f"{edge.get('from')} -> {edge.get('to')}"
            if not label:
                continue
            if kind == "er" and not _names_something(label):
                out.append(f"{where} is labelled {label!r} — name the entities instead "
                           "(\"n sessions : 1 doc\"), so the reader does not have to work out "
                           "which end is which")
            elif kind == "er" and _reads_backwards(label, edge.get("from"), edge.get("to")):
                left, _, right = label.partition(":")
                out.append(f"{where} is labelled {label!r}, which names its tables in the "
                           f"opposite order to its arrow — write it as "
                           f"\"{right.strip()} : {left.strip()}\", so the end the arrow leaves "
                           "is the end the label starts with")
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

    return out
