"""Spec -> d2 source. This is where the measured d2 recipe lives.

Most of what follows is behaviour that is not in d2's documentation. It was found by
rendering diagrams, measuring the result and reading the SVG — so each rule is written
next to the code that depends on it, with what it costs if you get it wrong. If a d2
upgrade changes one of them, the gates in `gates/` are what will tell you: several rules
here are load-bearing and none of them is guaranteed by d2's API.

The emitter only produces source. Running d2, and the post-processing that the SVG needs
before a page can theme it, are `render.py`'s job.

What is deliberately NOT here:

  * **Sizing.** d2 exposes no `ranksep`, so a diagram that comes out too big has to be
    authored smaller — there is nothing to turn down here. The limits live in `spec.py` as
    warnings and in `gates/size.py` as a measurement. The exception is a sequence diagram's
    row pitch, which d2 hardcodes and `compact.py` re-stacks in the rendered SVG afterwards;
    `ACTOR_HEIGHT` below is the only part of that spacing d2 accepts as input.
  * **Change marking by styling.** A thick accent border says "something here is
    special" and never says what, and there is no legend to look it up in. Changes are
    marked with `note`, which becomes a permanently-visible callout carrying the words.
"""
from . import palette
from .spec import CAPTION_ID, validate

# Base font for ordinary text. Edge labels get the same 13 rather than something smaller:
# at 11 they were the single cause of every sub-11px glyph in the prototype, and raising
# the *node* font instead is self-defeating — a wider diagram is scaled down further, so
# the text ends up the same size with more whitespace around it.
BASE_FONT = 13

# Height of a sequence participant's box. d2 defaults it to 62px for a single 13px line —
# ~45 of them empty — and unlike the row pitch it does honour an explicit `height`, down to
# well below this. 34 keeps ~9px of air above and under the label, so the box still reads as
# a box; the rest of a sequence diagram's dead height is `compact.py`'s job.
ACTOR_HEIGHT = 34

# The same box when any participant carries a `detail` second line. Applied to EVERY
# participant in that diagram, not just the ones with a detail: a row of boxes at two
# different heights reads as an accident. Two lines need ~26px of text (13px + an 11px line
# 13px below it) and this keeps the same ~9px of air above and under as the one-line box.
ACTOR_HEIGHT_DETAIL = 48

# Default `direction` per kind, as (embedded, standalone). A spec's own `direction` wins over
# both. The two targets want opposite layouts and only one of them has a width to fit into:
#
#   Embedded, a landscape drawing is scaled into the ~777px content column until its text
#   breaks the 11px floor — measured on the reference corpus at `right`: er 9.7px, state
#   7.6px, architecture 9.1px. So `down` is not a preference there, it is what the size gate
#   leaves. Standalone has no column and is opened full-screen on a landscape monitor, where
#   the same three read better wide: an ER diagram runs along its foreign keys instead of
#   swooping between stacked tables, and a state machine's terminal sits at the end.
#
#   `architecture` is the exception, and it is the only kind with containers: dagre packs
#   nested groups differently, and `right` leaves a dead quadrant while crowding the
#   callouts. It stays `down` for both.
#
# `sequence` is absent because d2's sequence engine ignores `direction` entirely, and `steps`
# because its boards are authored with a direction in mind.
DIRECTION = {
    "er": ("down", "right"),
    "class": ("down", "right"),
    "state": ("down", "right"),
    "architecture": ("down", "down"),
}

# A table's own base font. d2 renders a sql_table/class header at ~1.3x this and ignores
# the global `**.style.font-size` for it, so 14 gives 14px rows and an ~18px header —
# both under the 19px body text. At the global 13 the header lands at 17px, which reads
# as a heading next to body copy.
TABLE_FONT = 14

# Sentinels, not colours. d2 couples three visual roles of a sql_table to two properties:
#   fill       -> border + header background + member text
#   stroke     -> the BODY background          (not the border, as the name suggests)
#   font-color -> the header title only
# There is no single value that keeps the header title, the header background and the
# member text all readable in both themes, so the body background and the title are given
# their own near-white sentinels. They mean nothing to d2; `palette.py` recognises them
# and maps each to its own CSS var, which is what lets the three theme independently.
TABLE_BODY = "#fffffe"
TABLE_TITLE = "#fffffd"

KEY_CONSTRAINT = {"pk": "primary_key", "fk": "foreign_key", "unique": "unique"}


def _q(text):
    """Quote a label or id for d2.

    Everything is quoted, always. d2 accepts quoted ids in every position an unquoted one
    works — including each segment of a dotted path (`"browser"."editor"`) and inside an
    edge index (`("editor" -> "api")[0]`) — so quoting uniformly means a caller's id or
    label can contain a colon, a space or brackets without the emitter having to reason
    about which characters are safe where.

    A newline has to become the two-character escape: d2 rejects a real line break inside a
    quoted string outright ("double quoted strings must be terminated"), so a multi-line label
    is a compile error rather than something that renders oddly.
    """
    return ('"' + str(text).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
            + '"')


def _path(ref):
    """`browser.editor` -> `"browser"."editor"` — quote each segment, keep the dots."""
    return ".".join(_q(part) for part in str(ref).split("."))


def _prelude(background=None):
    """Global styling every diagram opens with.

    The root fill is the one line here that is not cosmetic, and it goes opposite ways for
    the two consumers:

    * **embedded in a page** (`background=None`) it must be `transparent`. d2 otherwise
      paints an opaque theme-coloured rect over the whole canvas, which shows up as a white
      slab behind the drawing in dark mode. Do NOT try to fix that afterwards by stripping
      the rect from the SVG: it breaks the `<mask>` elements d2 uses for table headers (they
      render as black slabs) and d2 writes `<rect …></rect>`, so naive removal also
      unbalances the XML.
    * **as a standalone image** it must be PAINTED, because there is no page behind it. Left
      transparent, a dark-themed drawing is composited onto whatever the viewer uses —
      white, in Preview and every browser — and the muted greys of the edge labels lose
      their contrast against it.
    """
    lines = [
        f'style.fill: "{background}"' if background else "style.fill: transparent",
        f"**.style.font-size: {BASE_FONT}",
        f'**.style.font-color: "{palette.FG}"',
        f'(** -> **)[*].style.stroke: "{palette.MUTED}"',
        f'(** -> **)[*].style.font-color: "{palette.MUTED}"',
        f"(** -> **)[*].style.font-size: {BASE_FONT}",
        "classes: {",
    ]
    for role in ("client", "svc", "store", "cache", "ext", "neutral"):
        fill, stroke = palette.vars_for(role)
        lines.append(f'  {role}: {{ style: {{ fill: "{fill}"; stroke: "{stroke}"; '
                     "stroke-width: 2 } }")
    grp_fill, grp_stroke = palette.vars_for("neutral")
    lines.append(f'  grp: {{ style: {{ fill: "{grp_fill}"; stroke: "{grp_stroke}"; '
                 f'stroke-width: 2; font-color: "{palette.ACCENT}" }} }}')
    # Stroke-only so it composes with any role class (`class: [svc; new]`). Only reachable
    # from a `steps` diagram, where the caption says what the emphasis means -- see
    # spec.py's check on the `new` flag. Note d2 rejects `double-border` on
    # sql_table/class shapes, and `new` cannot be used on a table at all, because there
    # `stroke` is the body fill rather than the border.
    lines.append(f'  new: {{ style: {{ stroke: "{palette.ACCENT}"; stroke-width: 4 }} }}')
    lines.append("}")
    return lines


def _note(obj, indent):
    """A `note` becomes a callout that is visible without hovering.

    `tooltip: text` on its own compiles to a native SVG `<title>`: correct, free, and
    useless here, because nothing on screen tells the reader there is anything to hover.
    Adding `tooltip.near` is what makes the callout permanent.

    Two consequences the rest of the pipeline has to absorb, both measured:
      * d2 reserves no canvas space for the callout, so an edge-anchored one is silently
        cut off. Which anchor to use is therefore a *measured* decision, not a guess —
        hence the browser placement pass and the clipping gate.
      * the callout text is a `<foreignObject>` (HTML inside SVG). Browsers render it;
        rasterisers like `rsvg-convert` silently drop it, so any check that goes through
        one cannot see callout text at all.
    """
    if not obj.get("note"):
        return []
    pad = " " * indent
    return [f"{pad}tooltip: {_q(obj['note'])}",
            f"{pad}tooltip.near: {obj.get('near', 'top-center')}"]


# Palette entries a sequence's `group` colours cycle through, in order of a group's first
# appearance. Groups exist because a per-lane role turns a seven-lane diagram into six colours
# that each mean something different and none of which the reader needs: what they want to know
# is which side of the wire a lane is on. Two or three groups is one colour per side, which is
# the same paint doing useful work.
#
# No new colours, and no legend: "these three are the same colour and sit together" needs no
# key, where "this one is amber" does. That is the line this stays on the right side of — a
# group says things belong together, it does not claim to say what the group IS. If the reader
# needs the name, it goes in a label or a `detail`.
GROUP_CLASSES = ("client", "svc", "store", "cache", "ext")


# A `state`'s role names what is being signalled, not what the thing is (see spec.STATE_ROLES),
# and each one reuses an architectural role's palette entry rather than adding a colour: the
# reader is decoding green-steady / amber-retrying / red-terminal, which is a convention they
# already have. Only the *class* is shared — nothing else about `store` follows into `steady`.
STATE_CLASS = {"working": "client", "steady": "store",
               "transient": "cache", "terminal": "ext"}


def group_classes(participants):
    """`group` name -> palette class, assigned in order of first appearance.

    Order of appearance rather than alphabetical, so the author controls which side gets which
    colour by ordering their lanes — which they are doing anyway, since lanes read left to right
    and a group's lanes belong together.
    """
    out = {}
    for participant in participants:
        name = participant.get("group")
        if name and name not in out:
            out[name] = GROUP_CLASSES[len(out) % len(GROUP_CLASSES)]
    return out


def _classes_for(node, allow_new):
    role = node.get("role", "neutral")
    names = [STATE_CLASS.get(role, role)]
    if allow_new and node.get("new"):
        names.append("new")
    return f"[{'; '.join(names)}]" if len(names) > 1 else names[0]


def _node(node, indent=0, allow_new=False, height=None):
    """One box, plus its children if it is a container."""
    pad = " " * indent
    head = f"{pad}{_q(node['id'])}"
    label = node.get("label")
    if label:
        head += f": {_q(label)}"
    children = node.get("children") or []
    body = []
    if height:
        body.append(f"{pad}  height: {height}")
    if children:
        # A node with children is a container and takes the container styling; spec.py
        # rejects a `role` on one so the two cannot silently disagree.
        body.append(f"{pad}  class: grp")
    else:
        body.append(f"{pad}  class: {_classes_for(node, allow_new)}")
    if node.get("shape"):
        body.append(f"{pad}  shape: {node['shape']}")
    body += _note(node, indent + 2)
    for child in children:
        body += _node(child, indent + 2, allow_new)
    if not body:
        return [head]
    return [head + " {"] + body + [pad + "}"]


# A sequence message the receiver never asked for: dashed line, open arrowhead. This is UML's
# async-signal mark and the one piece of styling-as-meaning allowed outside `steps`, on the same
# grounds — the label supplies the words, so the dash is not being asked to say anything on its
# own. Colour was considered and rejected: there is no legend, and unlike the dash a colour has
# no convention a reader can decode. Both properties are needed; a dash alone reads as UML's
# *reply* arrow, which is the opposite of what a push is.
PUSH_STYLE = "{style.stroke-dash: 4; target-arrowhead.shape: arrow}"


def _edge(edge):
    line = f"{_path(edge['from'])} -> {_path(edge['to'])}"
    if edge.get("label"):
        line += f": {_q(edge['label'])}"
    if edge.get("push"):
        line += f" {PUSH_STYLE}"
    elif edge.get("dashed"):
        line += " {style.stroke-dash: 3}"
    return line


def _table(item, row_key, indent=0):
    """A sql_table, used for BOTH the `er` and `class` kinds.

    `shape: class` exists and is the obvious choice for a class diagram, but it spends
    ~47px of fixed padding on its header (74px vs 27px at the same font) and ignores
    `height:`. Rendering classes as sql_table rows instead took the reference class
    diagram from 617x770 down to 456x549 with its contrast unchanged — the difference
    between not fitting a viewport and fitting one.
    """
    pad = " " * indent
    role = item.get("role", "neutral")
    fill = palette.vars_for(role, table=True)
    style = (f'style: {{ fill: "{fill}"; stroke: "{TABLE_BODY}"; '
             f'font-color: "{TABLE_TITLE}"; font-size: {TABLE_FONT}')
    if item.get("stereotype"):
        # An interface reads as one because its box is dashed. Safe on a sql_table even
        # though `stroke` is the body fill there: `stroke-dash` still applies to the
        # outline, and `double-border` (the other obvious choice) is rejected outright.
        style += "; stroke-dash: 3"
    style += " }"
    head = f"{pad}{_q(item['id'])}"
    if item.get("label"):
        head += f": {_q(item['label'])}"
    body = [f"{pad}  shape: sql_table", f"{pad}  {style}"]
    body += _note(item, indent + 2)
    if item.get("stereotype"):
        # d2 has no stereotype concept, so it is a row with an empty value -- the
        # conventional «guillemets» spelling carries the meaning.
        body.append(f'{pad}  {_q("«" + item["stereotype"] + "»")}: ""')
    for row in item[row_key]:
        line = f"{pad}  {_q(row['name'])}: {_q(row.get('type', ''))}"
        if row.get("key"):
            line += f" {{constraint: {KEY_CONSTRAINT[row['key']]}}}"
        body.append(line)
    return [head + " {"] + body + [pad + "}"]


def _caption(text):
    """The phase label for a `steps` diagram.

    d2 does not render board names into the SVG, so without this the reader can see the
    topology change but not what phase they are looking at. A borderless, fill-free node
    relabelled per step supplies the text; `near: top-center` keeps it out of the layout.
    """
    return [
        f"{_q(CAPTION_ID)}: {_q(text)} {{",
        "  near: top-center",
        f'  style: {{ stroke-width: 0; fill: transparent; font-size: 15; '
        f'font-color: "{palette.ACCENT}"; bold: true }}',
        "}",
    ]


def _steps(spec):
    """Per-step deltas, each its own d2 board.

    The animation earns its place only when consecutive boards differ in topology — "both
    paths run at once, then the old one is removed" is something no single static picture
    can say. Flowing arrows over an unchanging drawing explain nothing.
    """
    lines = ["steps: {"]
    for i, step in enumerate(spec["steps"], start=1):
        lines.append(f"  {_q(str(i))}: {{")
        if step.get("caption"):
            lines.append(f"    {_q(CAPTION_ID)}.label: {_q(step['caption'])}")
        for node in step.get("add_nodes") or []:
            lines += _node(node, 4, allow_new=True)
        for edge in step.get("add_edges") or []:
            lines.append("    " + _edge(edge))
        for edge in step.get("relabel_edges") or []:
            lines.append(f"    ({_path(edge['from'])} -> {_path(edge['to'])})[0].label: "
                         f"{_q(edge['label'])}")
        for edge in step.get("emphasize_edges") or []:
            lines.append(f"    ({_path(edge['from'])} -> {_path(edge['to'])})[0]"
                         ".style.stroke-width: 3")
        for edge in step.get("remove_edges") or []:
            # `: null` deletes the edge on this board and every later one.
            lines.append(f"    ({_path(edge['from'])} -> {_path(edge['to'])})[0]: null")
        lines.append("  }")
    lines.append("}")
    return lines


def emit(spec, background=None, standalone=False):
    """Render `spec` to d2 source. Validates first — see spec.py on why that is loud.

    `background` paints the root rather than leaving it transparent; pass the page colour
    when the output is a standalone image. See `_prelude`.

    `standalone` picks the `direction` default out of `DIRECTION` — the two targets want
    opposite layouts, and only one of them has a width to fit into.
    """
    validate(spec)
    kind = spec["kind"]
    lines = list(_prelude(background))

    if kind == "sequence":
        # Participants become columns in the order written. This is the whole reason d2 is
        # the engine: graphviz reorders lifelines to minimise edge crossings, so the
        # columns come out in the wrong order with sloped arrows, and no amount of
        # tweaking fixes it because it is what `dot` is for.
        lines.append("shape: sequence_diagram")

    direction = spec.get("direction") or DIRECTION.get(kind, (None, None))[bool(standalone)]
    if direction:
        lines.append(f"direction: {direction}")

    if kind in ("architecture", "steps"):
        for node in spec["nodes"]:
            lines += _node(node, allow_new=(kind == "steps"))
        for edge in spec.get("edges") or []:
            lines.append(_edge(edge))
        if kind == "steps":
            lines += _caption(spec["caption"])
            lines += _steps(spec)
    elif kind == "sequence":
        detailed = any(p.get("detail") for p in spec["participants"])
        height = ACTOR_HEIGHT_DETAIL if detailed else ACTOR_HEIGHT
        groups = group_classes(spec["participants"])
        for node in spec["participants"]:
            if node.get("group"):
                node = dict(node, role=groups[node["group"]])
            # d2 renders a `\n` label as one <text> of two <tspan>s, which is what lets
            # `compact.style_detail_lines` shrink the second one afterwards — d2 itself has no
            # per-line styling. Every box gets the taller height so the row stays even.
            if node.get("detail"):
                node = dict(node, label=f"{node.get('label') or node['id']}\n{node['detail']}")
            lines += _node(node, height=height)
        for msg in spec["messages"]:
            lines.append(_edge(msg))
    elif kind == "er":
        for table in spec["tables"]:
            lines += _table(table, "columns")
        for edge in spec.get("edges") or []:
            lines.append(_edge(edge))
    elif kind == "class":
        for item in spec["classes"]:
            lines += _table(item, "members")
        for edge in spec.get("edges") or []:
            lines.append(_edge(edge))
    elif kind == "state":
        for node in spec["states"]:
            lines += _node(node)
        for edge in spec["transitions"]:
            lines.append(_edge(edge))

    return "\n".join(lines) + "\n"


def is_animated(spec):
    """Whether `render.py` must pass d2 an `--animate-interval`."""
    return spec.get("kind") == "steps"
