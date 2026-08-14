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
  * **Animation.** d2 can do it — `steps: { "1": { ... } }` declares one board per beat and
    `--animate-interval` cycles them in the SVG — and this module emitted it until we watched
    people read the result. Boards that differ in *topology* animate into an unreadable
    comparison: only one is on screen at a time, so the reader has to hold the previous one in
    their head, and two independent readers said the same thing unprompted. Before/after is two
    diagrams side by side. The form that might have earned it — one fixed graph with a
    highlight walking through it, narrating a flow — has never been the thing anyone asked for
    here, so the capability is noted rather than built. Do not re-add it without that use case.
"""
from . import palette
from .spec import validate

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

# The same box when any participant carries a `detail`. Applied to EVERY participant in that
# diagram, not just the ones with a detail: a row of boxes at two different heights reads as an
# accident. Two lines need ~26px of text (13px + an 11px line 13px below it) and this keeps the
# same ~9px of air above and under as the one-line box; each further line adds LINE_H.
ACTOR_HEIGHT_DETAIL = 48
LINE_H = 14

# Characters per detail line before it wraps. A detail is the real module names behind an
# abstract lane, and left on one line it sets the box's width: a lane reading
# "Procrastinate worker: discover→ingest (creates Document)→extract→ocr→enrich" stretched its box
# to ~450px and shoved every other lane sideways. Wrapping trades that width for height, which a
# sequence diagram has to spare — d2 sizes a box to its longest line, so the cap is the box.
DETAIL_WRAP = 34

# Wrap widths for an edge label, gentlest first. An edge label sits in the gap between two
# boxes, so in a left-to-right layout its full width is added to the diagram's: three labels
# averaging 17 characters were 300px of the 1160px a four-box chain measured. Wrapped they are
# 998px at 14 and 912px at 8 — so this is the cheapest width in the renderer, ahead of dropping
# content, and height is nearly free since the label sits beside a line that is already there.
# `render._pick_layout` tries them in order and keeps the gentlest one that fits, because each
# step down buys width by spending lines, and a four-line edge label is its own kind of ugly.
EDGE_WRAPS = (14, 8)

# Lines a detail may occupy after wrapping — four in the box counting the title. Past this the
# problem is editorial rather than typographic (spec.py warns): a lane whose subtitle needs five
# lines is a diagram of its own trying to hide inside a label.
MAX_DETAIL_LINES = 3

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
#   `architecture` is the exception, and it is the only kind with containers: nested groups
#   are packed differently from plain boxes, and `right` leaves a dead quadrant while
#   crowding the callouts. It stays `down` for both, and still measures that way under ELK.
#
# The per-kind numbers above were measured under dagre, before the renderer settled on one
# engine. They are kept because they are what the defaults were chosen from, not because they
# describe today's output — and the embedded default is now only a starting point anyway,
# since `render._pick_layout` measures both orientations and may pick the other.
#
# `sequence` is absent because d2's sequence engine ignores `direction` entirely — and, as it
# turns out, ignores the layout engine too: dagre and ELK produce byte-identical output there.
DIRECTION = {
    "er": ("down", "right"),
    "class": ("down", "right"),
    "state": ("down", "right"),
    "architecture": ("down", "down"),
}

# The layout engine, and the spacing it is given.
#
# `elk`, not d2's default `dagre`, for every box-and-arrow diagram here. Three reasons, and only
# the last is a matter of taste:
#
#   * **it can anchor an edge to a single ROW of a table, and dagre cannot.** dagre accepts
#     `documents.owner_id` as an endpoint and silently ignores it — proven by rendering `T`,
#     `T.r1` and `T.r3` and getting a byte-identical edge path. So every ER diagram here spent
#     a long time asserting a column-level relationship that was drawn table-to-table, and a
#     class diagram could not say WHICH method raises. That is a correctness gap, not a look.
#   * **bigger text.** Measured across the reference corpus, elk needs less width, so less of
#     it is scaled away in the content column: architecture 11.4px -> 13.0px, class 12.1px ->
#     14.0px, ER 11.6px -> 12.6px, state unchanged at 13.0px.
#   * orthogonal routes read more cleanly than dagre's curves.
#
# What it costs: elk spends page height where dagre spent width. Architecture is 887x771 under
# dagre but only 675px tall once scaled into the column, against elk's 579x767 at full size.
# That is the trade, made deliberately in favour of legible text.
#
# Running BOTH and picking by measurement was built first and then removed. It doubled the
# candidate count for a decision that came out the same way on every figure anyone preferred,
# and it made row anchoring conditional on a measurement — a diagram could silently lose its
# column-level arrows because some other candidate measured shorter. One engine, always, means
# `documents.owner_id` draws what it says every time.
#
# ELK's own spacing defaults are built for a canvas with room to spare and make the reference
# architecture 1306px tall. These are the measured replacements; `edgeNodeBetweenLayers` is the
# one with real leverage — at ELK's default 40 that drawing is 1117px, at 15 it is 767px.
#
# What is NOT tunable, researched rather than assumed: d2 exposes exactly five ELK options
# (`ConfigurableOpts` in d2elklayout). The ones that would fix the last two cosmetic flaws —
# `elk.edgeLabels.inline` for an edge label landing on a container's border, and the port and
# routing options for a 90-degree turn arriving too close to an arrowhead — are set internally
# by d2 and never exposed. Reaching them needs a fork, so those two are accepted as they are.
ELK_OPTS = {
    "nodeNodeBetweenLayers": 15,   # ELK default 70
    "edgeNodeBetweenLayers": 15,   # ELK default 40 — the expensive one
    "padding": 10,                 # ELK default 50, applied to every container alike
}

# Layer spacings to try, tightest first, when the tight one leaves TEXT UNREADABLE.
#
# 15 is right for four of the five reference figures and wrong for one, because the gap between
# layers is also where ELK puts an edge label: the ER cardinality `n sessions : 1 doc` needs 30,
# or it overlaps the `presence_sessions` table and its first glyph sits grey-on-purple.
#
# Escalated rather than raised, because it is not free — but what it costs depends entirely on
# the target, and only one of the two pays:
#
#   * EMBEDDED, every px of width is scaled back out of the glyphs inside the content column.
#     ER goes 862x257 at 12.6px to 892x257 at 12.2px.
#   * STANDALONE, the image is shown at its natural size and there is nothing to scale it.
#     ER goes 886x281 to 916x281 with its text unmoved at 12.5px.
#
# Wrapping the label instead does NOT help — measured, it changes the covered area not at all,
# since a folded label is still wider than a 15px gap. That is what demoted the unconditional
# colon fold to a fallback; see `wrap_edge_label`.
#
# 40 is headroom, not a rung anything in the corpus reaches. An earlier version of the
# hidden-text check flagged the architecture's `GraphQL` and `WebSocket` where they stray onto
# the Kubernetes container's pale fill, and needed 40 to clear them — but that model asked only
# whether text was CONTAINED, and readable text on a pale fill is not a defect. Under the rule
# that shipped, where paint order decides which test applies, the architecture is clean at 15.
ELK_SPACING_LADDER = (15, 30, 40)

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
    # The one place styling carries meaning, and only ever alongside the `note` spec.py
    # insists on: the box a change added. Stroke-only so it composes with any role class
    # (`class: [svc; new]`) and the role still says what the thing IS. A table cannot use
    # this — there `stroke` is the body fill — so `_table` accents its `fill` instead.
    lines.append(f'  new: {{ style: {{ stroke: "{palette.ACCENT}"; stroke-width: 4 }} }}')
    grp_fill, grp_stroke = palette.vars_for("neutral")
    lines.append(f'  grp: {{ style: {{ fill: "{grp_fill}"; stroke: "{grp_stroke}"; '
                 f'stroke-width: 2; font-color: "{palette.ACCENT}" }} }}')
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
#
# `stuck` takes `svc`, the one entry the other four left unused, and it is the one state colour
# with no convention behind it — see spec.STATE_ROLES for why that is the point rather than a
# compromise. It also means the five are mutually distinguishable, which is what the whole
# vocabulary is for: a diagram cannot now paint two different meanings the same colour without
# the author having written the same role twice.
STATE_CLASS = {"working": "client", "steady": "store", "transient": "cache",
               "stuck": "svc", "terminal": "ext"}


def wrap_detail(text, width=None):
    """Break a detail into lines of at most `width` characters, greedily.

    `width` defaults to `DETAIL_WRAP` by lookup rather than as a default argument value, which
    would bind at import and make the constant unpatchable — including from a test.

    The author's own newlines are kept as breaks, so an explicit two-line detail stays as
    written. A single token longer than `width` is left over-long rather than cut: these are
    module and job names, and a name broken in half is worse than a wide box.
    """
    width = DETAIL_WRAP if width is None else width
    lines = []
    for paragraph in str(text).split("\n"):
        current = ""
        for word in paragraph.split():
            candidate = f"{current} {word}".strip()
            if current and len(candidate) > width:
                lines.append(current)
                current = word
            else:
                current = candidate
        lines.append(current)
    return [line for line in lines if line]


def detail_lines(participants):
    """The most lines any one participant's detail needs — what the row's height must fit."""
    return max((len(wrap_detail(p["detail"])) for p in participants if p.get("detail")),
               default=0)


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


def _classes_for(node):
    """The d2 class carrying this box's colour. A `state`'s role names what is being
    signalled and reuses an architectural role's palette entry — see STATE_CLASS."""
    role = node.get("role", "neutral")
    name = STATE_CLASS.get(role, role)
    return f"[{name}; new]" if node.get("new") else name


def _node(node, indent=0, height=None):
    """One box, plus its children if it is a container.

    A `detail` becomes extra label lines under the name, which `compact.style_detail_lines`
    then shrinks and mutes. On a leaf box d2 sizes the shape to its label, so unlike a sequence
    lane there is no height to compute — the box simply grows.
    """
    pad = " " * indent
    head = f"{pad}{_q(node['id'])}"
    label = node.get("label")
    if node.get("detail"):
        lines = "\n".join(wrap_detail(node["detail"]))
        label = f"{label or node['id']}\n{lines}"
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
        # d2 centres a container's label along its top edge, which is exactly where an edge
        # entering the container from above arrives — so "Kubernetes cluster" and the arrow
        # into the box beneath it collided. Pushing the label into the corner costs no canvas
        # at all, which is the whole reason it is done this way rather than with padding.
        body.append(f"{pad}  label.near: top-left")
    else:
        body.append(f"{pad}  class: {_classes_for(node)}")
        # d2 sets node labels bold. Turned off because a box already announces itself with a
        # filled shape and a coloured border, so the weight is a third signal saying the same
        # thing — and next to it a muted subtitle reads as a different class of text rather
        # than as the same text made quieter.
        body.append(f"{pad}  style.bold: false")
    if node.get("shape"):
        body.append(f"{pad}  shape: {node['shape']}")
    body += _note(node, indent + 2)
    for child in children:
        body += _node(child, indent + 2)
    if not body:
        return [head]
    return [head + " {"] + body + [pad + "}"]


# A sequence message the receiver never asked for: dashed line, open arrowhead. This is UML's
# async-signal mark and the one piece of styling that is allowed to carry meaning, on the
# grounds that the label supplies the words — the dash is not being asked to say anything on
# its own. Colour was considered and rejected: there is no legend, and unlike the dash a colour has
# no convention a reader can decode. Both properties are needed; a dash alone reads as UML's
# *reply* arrow, which is the opposite of what a push is.
PUSH_STYLE = "{style.stroke-dash: 4; target-arrowhead.shape: arrow}"

# A message's outcome, when a flow's point is that it can end two ways — a 409 turning the
# request back, or the 200 that means it went through. The text-grade role colours, so both
# read as text and both are already gate-approved in either theme. Unlike the push above, this
# colour IS decodable without a legend: red-failed / green-succeeded is the same convention a
# `state`'s roles lean on, and the label still carries the words.
OUTCOME_COLOUR = {"ok": palette.vars_for("store", table=True),
                  "error": palette.vars_for("ext", table=True)}


def wrap_edge_label(text, width=EDGE_WRAPS[0]):
    """Break a long edge label onto several lines, at the places a reader already sees.

    Three rules, each from a label that wrapped badly:

    * **Break after a `:` first.** A cardinality is two halves either side of it — "1 doc : n
      sessions" says one thing about docs and one about sessions — so that is where the reader
      would fold it. The colon stays on the first line and is never dropped: unlike the dots in
      "list · read · stat" it is not punctuation between equals, it *is* the ratio.
    * **Never strand a separator.** A `·`, `,` or `-` left at the end of a line separates a word
      from a line break rather than from the next word, so it reads as debris and costs width.
    * **Never leave a one- or two-character word alone on a line.** "1 doc :" / "n" / "sessions"
      puts the whole meaning of the cardinality on a line by itself; it joins its neighbour even
      when that overruns the target width, which is a smaller cost than the orphan.

    The author's own newlines are kept as written.

    This is the ONLY thing that folds a label, and that is a correction. An `er` cardinality
    used to be folded at its colon unconditionally, on the belief that it was what stopped the
    reference diagram's label running under the `presence_sessions` table. It was not: the fix
    for that is the layer spacing (`ELK_SPACING_LADDER`), and once the spacing was right the
    fold was spending a line to change the covered area by nothing. So a cardinality reads on
    one line when one line fits, and reaches this ladder when it does not — which costs about
    half a pixel of glyph on the reference ER (12.7px folded, 12.2px on one line), for a label
    that reads as the single phrase it is.
    """
    out = []
    for paragraph in str(text).split("\n"):
        words = paragraph.split()
        head, rest = _split_at_colon(words)
        lines = [" ".join(head)] if head else []
        current = ""
        for word in rest:
            candidate = f"{current} {word}".strip()
            if current and len(candidate) > width:
                lines.append(current)
                current = word
            else:
                current = candidate
        if current:
            lines.append(current)
        out += _tidy(lines)
    return "\n".join(out)


# Words no longer than this are never left alone on a line — see wrap_edge_label.
ORPHAN = 2


def _split_at_colon(words):
    """`["1", "doc", ":", "n", "sessions"]` -> `(["1", "doc", ":"], ["n", "sessions"])`.

    Only a colon with something on both sides splits: a label that opens or closes with one has
    no two halves to separate, and would just get a blank line out of it.
    """
    for i, word in enumerate(words):
        if word.endswith(":") and 0 < i < len(words) - 1:
            return words[:i + 1], words[i + 1:]
    return [], words


def _tidy(lines):
    """Drop stranded separators, then pull orphaned short words back into a neighbour."""
    lines = [line.rstrip(" ·,-") if i < len(lines) - 1 else line
             for i, line in enumerate(lines)]
    lines = [line for line in lines if line.strip(" ·,-")]
    out, i = [], 0
    while i < len(lines):
        line = lines[i]
        orphan = len(line) <= ORPHAN and " " not in line
        if orphan and i + 1 < len(lines):
            # Down, not up: the line above may be the half ending in the colon, and pulling
            # "n" onto it would put "1 doc : n" / "sessions" back.
            lines[i + 1] = f"{line} {lines[i + 1]}"
        elif orphan and out:
            out[-1] = f"{out[-1]} {line}"
        else:
            out.append(line)
        i += 1
    return out


def _edge(edge, wrap=None):
    line = f"{_path(edge['from'])} -> {_path(edge['to'])}"
    if edge.get("label"):
        label = wrap_edge_label(edge["label"], wrap) if wrap else edge["label"]
        line += f": {_q(label)}"
    style = []
    if edge.get("push"):
        style += ["style.stroke-dash: 4", "target-arrowhead.shape: arrow"]
    elif edge.get("dashed"):
        style.append("style.stroke-dash: 3")
    if edge.get("outcome"):
        colour = OUTCOME_COLOUR[edge["outcome"]]
        style += [f'style.stroke: "{colour}"', f'style.font-color: "{colour}"']
    if style:
        line += " {" + "; ".join(style) + "}"
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
    # A table's `fill` is its border, its header background AND its member text (see the
    # sentinels above), so a new table is accented by swapping that one colour rather than by
    # a border it has no way to draw. It costs the role's colour on that box — in an `er`
    # diagram every table is a `store` anyway, and "this is the new one" is the fact the
    # reader came for.
    fill = palette.ACCENT if item.get("new") else palette.vars_for(role, table=True)
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


def effective_direction(spec, standalone=False):
    """The `direction` this spec will actually be laid out with, or None if the kind has no
    default (`sequence`, whose engine ignores it).

    Exposed because the post-processing in `compact.py` has to know which way the drawing
    runs — a state machine's start marker goes beside the first state laid out downward and
    above it laid out to the right — and the standalone target's direction is NOT in the spec:
    it comes out of `DIRECTION` here. Reading `spec["direction"]` there silently got it wrong
    in the one case it mattered, and the clipping gate is what caught it.
    """
    return spec.get("direction") or DIRECTION.get(spec["kind"], (None, None))[bool(standalone)]


def emit(spec, background=None, standalone=False, wrap_edges=None):
    """Render `spec` to d2 source. Validates first — see spec.py on why that is loud.

    `background` paints the root rather than leaving it transparent; pass the page colour
    when the output is a standalone image. See `_prelude`.

    `standalone` picks the `direction` default out of `DIRECTION` — the two targets want
    opposite layouts, and only one of them has a width to fit into.

    `wrap_edges` is the character width to wrap long edge labels to, or None to leave them
    as written. Off by default because it is not free — a wrapped label is taller and reads a
    beat slower — and `render.render()` turns it on only for the layout candidates that need
    the width to fit the column.
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

    direction = effective_direction(spec, standalone)
    if direction:
        lines.append(f"direction: {direction}")

    if kind == "architecture":
        for node in spec["nodes"]:
            lines += _node(node)
        for edge in spec.get("edges") or []:
            lines.append(_edge(edge, wrap=wrap_edges))
    elif kind == "sequence":
        extra = detail_lines(spec["participants"])
        height = ACTOR_HEIGHT if not extra else ACTOR_HEIGHT_DETAIL + (extra - 1) * LINE_H
        groups = group_classes(spec["participants"])
        for node in spec["participants"]:
            if node.get("group"):
                node = dict(node, role=groups[node["group"]])
            # d2 renders a `\n` label as one <text> of two <tspan>s, which is what lets
            # `compact.style_detail_lines` shrink the second one afterwards — d2 itself has no
            # per-line styling. Every box gets the taller height so the row stays even.
            lines += _node(node, height=height)
        for msg in spec["messages"]:
            lines.append(_edge(msg))
    elif kind == "er":
        for table in spec["tables"]:
            lines += _table(table, "columns")
        for edge in spec.get("edges") or []:
            lines.append(_edge(edge, wrap=wrap_edges))
    elif kind == "class":
        for item in spec["classes"]:
            lines += _table(item, "members")
        for edge in spec.get("edges") or []:
            lines.append(_edge(edge, wrap=wrap_edges))
    elif kind == "state":
        for node in spec["states"]:
            lines += _node(node)
        for edge in spec["transitions"]:
            lines.append(_edge(edge, wrap=wrap_edges))

    return "\n".join(lines) + "\n"


