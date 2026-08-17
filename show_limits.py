#!/usr/bin/env python3
"""The evidence for the renderer's legibility and height limits, on the page it is about.

Nothing here computes a limit. A limit is a judgement about what a person can read and how far
they will scroll, so all this does is show real drawings at a ladder of text sizes and a ladder
of heights, in the place they will really be seen, with the numbers written beside them — and it
prints the limits as they stand today straight out of `lib/diagram/gates/size.py`, so the
document can never quote a value the code has stopped holding.

    python3 show_limits.py               # write it and open it
    python3 show_limits.py --no-open     # write it only, for looking at it some other way

**It takes its LAYOUT from the explainer and nothing else.** The page is built by the explainer's
own renderer, so the column, the body font at its real size and the diagram cards are the real
thing rather than an imitation of it — and that is the whole reason the numbers can be trusted. A
review sheet with a body font of its own once hid a bug for a session: a diagram's group legend
printing in the host page's serif, invisible because every sheet it was reviewed on had a sans
body.

What it deliberately is NOT is a real article. There is no explaining to do here, so the prose is
only as long as it takes to be a ruler for the figures beside it, and the two ladders are not
laid out end to end down the page — each is one figure with a switcher in the margin, which is
this document's own addition and not something an explainer has. Judging a text size means
looking at one drawing carefully, not scrolling past ten.

Re-run it when the explainer's page design changes. A different body width, root font size or
body font is the one thing that legitimately invalidates every number these limits were set
from; nothing else here does.
"""
import html
import importlib.util
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from lib.diagram import figure, render                       # noqa: E402
from lib.diagram.examples import REFERENCE                   # noqa: E402
from lib.diagram.examples_repo import REPO                   # noqa: E402
from lib.diagram.gates import size as size_gate              # noqa: E402

CORPORA = {"reference": REFERENCE, "repo": REPO}
OUT = "/tmp/diagram-limits.html"

# px of RENDERED primary text — the size a box's name comes out at after the content column has
# scaled the drawing down. Comfortable at the top, plainly too small at the bottom, so the floor
# being judged is never at either end of the ladder.
TEXT_RUNGS = (14, 13, 12, 11, 10, 9, 8)

# px of ELK spacing between one row of boxes and the next. It is the cheapest way to make one
# spec taller without stretching it — every rung is a real drawing of the same diagram — and
# these four were chosen because they straddle both height lines. If a corpus change stops them
# doing that the captions say so, since every number on them is measured off the drawing above.
HEIGHT_RUNGS = (15, 40, 55, 85)

# The spec the height ladder is built from: the one figure in either corpus that carries no
# callout, so it can be drawn straight through `render.render` at a pinned spacing and still be
# exactly what ships — a callout's position is chosen by a search that `render.render` alone
# does not run, and four rungs of a figure with its notes parked on top of its own labels would
# put a defect in front of a question about height.
TALL = ("repo/class", REPO["class"])

# What the switcher writes under a variant it wants to distinguish. `SHIPS` is load-bearing
# beyond the label: `panel` opens on the variant carrying it.
SHIPS = "what ships"
UNDER_FLOOR = "under the floor"

# What a value cell shows when the selection says nothing about it — a figure with no
# subtitles, for one. A literal character rather than an entity, because it is written
# in through `textContent` and an entity would show as its own source.
NOT_SET = "—"

# The rulers: one paragraph of the article's own body copy under each figure, at its real size.
# The question is never "can I read 9px text" in the abstract — it is whether a figure's labels
# still belong beside the prose they are explaining, and that needs the prose to be there.
#
# Two different paragraphs and not one repeated, because a page that says the same thing twice
# reads as boilerplate and stops being looked at, which is the opposite of what a ruler is for.
TEXT_RULER = ("The renderer picks a layout by measuring it: it draws the figure more than one "
              "way and keeps the one whose smallest glyph survives the column. These floors are "
              "what that check is made against.")
HEIGHT_RULER = ("Body copy resumes here, which is how far down the page the figure above has "
                "pushed it. A tall figure is scrolled past rather than read at a glance.")

# This page's own furniture, and not the explainer's.
#
# Both rails are parked in the margins rather than inside the column, because the column IS the
# measurement: a control that narrowed it would change the thing being judged. Each rides a rail
# spanning the figure, which is what lets it be `sticky` — it holds near the top of the viewport
# while a figure taller than the screen scrolls past, and leaves with the rail's bottom rather
# than lingering beside the next section.
#
# A rail is anchored to the FIGURE and not to anything above it, so all three line up at the top
# whatever a caption wraps to. That is why every part of a variant is a switched block of its own.
#
# ONE sticky offset for both rails, as a variable, and it is not a tidiness point: two offsets
# were tried and the rails drifted apart the moment they both stuck, so the sizes no longer lined
# up with the switcher they are meant to be read beside. A shared number makes them align by
# construction.
#
# Which is why the explainer's theme button moves to the bottom on this page. It is fixed in the
# top right corner there, and the right rail reaches under it below about 1516px of viewport —
# every common laptop. Bending the rails around it was tried first and cost the alignment; moving
# one borrowed button, in a document that is not an article, costs nothing.
PAGE_CSS = """
#theme-toggle{top:auto;bottom:1.2rem;right:1.2rem}
.panel{position:relative}
.slot{position:relative}
.variant[hidden]{display:none}
.rail{position:absolute;top:0;bottom:0;width:10.5rem;--stick:1.4rem}
.rail-left{right:100%;margin-right:1.3rem}
.rail-right{left:100%;margin-left:1.3rem}
.switch,.aside{position:sticky;top:var(--stick)}
.switch-group+.switch-group{margin-top:.8rem}
.switch-label{display:block;font:600 .58rem system-ui,sans-serif;letter-spacing:.09em;
  text-transform:uppercase;color:var(--muted);margin-bottom:.3rem;text-align:right}
.switch-items{display:block}
.switch button{display:block;width:100%;text-align:right;margin:.2rem 0;padding:.28rem .55rem;
  border:1px solid var(--border);border-radius:6px;background:var(--surface);color:var(--fg);
  font:.72rem/1.3 system-ui,sans-serif;cursor:pointer}
.switch button:hover{background:var(--surface-hover)}
.switch button.on{border-color:var(--accent);color:var(--accent);font-weight:600}
.switch .mark{display:block;font-size:.6rem;color:var(--muted);font-weight:400}
.switch button.on .mark{color:var(--accent)}
/* One block per kind of text, the size a variant renders at beside the floor it is judged
   against. The same arrangement in the rail and in the flow, so nothing about the comparison
   changes when the layout does. */
.sizes{display:flex;flex-direction:column;gap:.55rem;
  font-family:system-ui,-apple-system,sans-serif}
.size .k{display:block;font:600 .55rem system-ui,sans-serif;letter-spacing:.09em;
  text-transform:uppercase;color:var(--muted)}
.size .v{font-size:.86rem;font-weight:600}
/* A number's integer part right-aligned in a box of its own and the fraction left in
   another, which is what lines a column up on the decimal point. Tabular figures so
   the digits themselves are one width — proportional ones defeat the boxes. */
/* Tabular figures so a column of numbers does not jitter as it changes, and that is
   all. Aligning them on the decimal point was tried: with `9px`, `7.5px` and
   `820px` in one column it needs a reserved fraction box, and the result reads more
   ragged than the plain numbers it was meant to tidy. */
.size .v,.size .f,td{font-variant-numeric:tabular-nums}
/* The two numeric columns of the values table, right-aligned so their units line up.
   Enough, and nothing about the digits pretends to be a decimal column. */
table td:nth-child(3),table td:nth-child(4),
table th:nth-child(3),table th:nth-child(4){text-align:right}
.size .f{font-size:.68rem;color:var(--muted);margin-left:.4rem}
.size.warn .v,.size.warn .f{color:var(--accent)}
.size.bad .v,.size.bad .f{color:var(--diff-del-fg)}
/* What a variant IS, under the numbers rather than beside the drawing. It used to be a pill in
   the content column, where it read as part of the article; here it is an aside about the aside,
   which is what it always was. */
.note{margin-top:.9rem;padding-top:.7rem;border-top:1px solid var(--border);
  font-family:system-ui,-apple-system,sans-serif}
.note .k{display:block;font:600 .55rem system-ui,sans-serif;letter-spacing:.09em;
  text-transform:uppercase;color:var(--accent);margin-bottom:.15rem}
.note .t{display:block;font-size:.7rem;line-height:1.35;color:var(--muted)}
.note.bad .k{color:var(--diff-del-fg)}
/* The picked values, echoed into the table at the foot of the page. */
.call{font-family:system-ui,-apple-system,sans-serif;font-weight:600;color:var(--accent)}
/* No contents list: four screens with two headings do not need one, and it is the
   one piece of explainer furniture that only makes sense for an article. */
.toc{display:none}
/* Not enough margin for two rails: both drop into the flow above the figure, sizes first
   because they describe what is on screen and the switcher changes it. */
@media (max-width:1400px){
  .slot{display:flex;flex-direction:column}
  .rail{position:static;width:auto;margin:0 0 1rem}
  .rail-right{order:-2}
  .rail-left{order:-1}
  .switch,.aside{position:static}
  .switch{display:flex;gap:1.6rem;flex-wrap:wrap}
  .switch-group+.switch-group{margin-top:0}
  /* `stretch` is what squares the boxes off: every button on one wrapped line takes the height
     of the tallest, so a "what ships" note under one does not leave its neighbours ragged. Their
     text stays at the top because a button is a column, not a centred box. */
  .switch-items{display:flex;flex-wrap:wrap;gap:.4rem;align-items:stretch}
  .switch button{display:flex;flex-direction:column;width:auto;margin:0;
    text-align:left}
  .switch-label{text-align:left}
  .sizes{flex-direction:row;flex-wrap:wrap;gap:1.6rem}
}
"""

# `.panel` and not `.slot`: the sizes and the figure are separate switched blocks, so a click has
# to reach both. Scoping to the slot would swap the drawing and leave the numbers describing the
# one before it.
#
# `apply` is what makes the page an instrument rather than a document: a button carries the values
# its variant implies, and they are echoed into the table at the foot. Run for the buttons that
# start active as well, so the table is never blank or stale against what is on screen.
PAGE_JS = """
function apply(button) {
  Object.keys(button.dataset).forEach(function (key) {
    if (key.indexOf('call') !== 0 || key === 'call') return;
    var cell = document.querySelector('[data-cell="' + key.slice(4).toLowerCase() + '"]');
    if (cell) cell.textContent = button.dataset[key];
  });
}
document.querySelectorAll('.switch button').forEach(function (button) {
  button.addEventListener('click', function () {
    var panel = button.closest('.panel');
    panel.querySelectorAll('.variant').forEach(function (variant) {
      variant.hidden = variant.dataset.v !== button.dataset.v;
    });
    panel.querySelectorAll('.switch button').forEach(function (other) {
      other.classList.toggle('on', other === button);
    });
    apply(button);
  });
});
document.querySelectorAll('.switch button.on').forEach(apply);
"""


def explainer():
    """The explain-diff renderer, loaded by path.

    Not a plain `import`: this repo has two modules called `render` and `lib.diagram.render` is
    already one of them. Loading it under a name of its own is also what keeps this file honest
    about the coupling — it wants that renderer specifically, not a page of its own devising.
    """
    path = os.path.join(HERE, "skills", "explain-diff", "scripts", "render.py")
    spec = importlib.util.spec_from_file_location("explain_diff_render", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def scaled(svg, factor):
    """The same drawing with its intrinsic size multiplied — what the column does to a wide one.

    Only the OUTER `<svg>`'s width and height are touched and its viewBox is left alone, so the
    browser maps the same user units into a smaller box and everything inside, text included,
    shrinks with it. The inner `<svg>` d2 nests is laid out in those user units and follows.

    This is the real mechanism and not a CSS trick, which is what makes a variant honest: a
    figure wider than the column is scaled to fit it and every glyph goes down in proportion, so
    rewriting the intrinsic size is exactly as if the drawing had been that much wider.
    """
    tag = svg[svg.index("<svg"):svg.index(">", svg.index("<svg")) + 1]
    out = tag
    for attr in ("width", "height"):
        mark = f' {attr}="'
        start = out.index(mark) + len(mark)
        end = out.index('"', start)
        out = out[:start] + f"{float(out[start:end]) * factor:.2f}" + out[end:]
    return svg.replace(tag, out, 1)


def authored(svg):
    """(primary, edge, subtitle) smallest glyph as drawn, before the column scales anything.

    Measured with the column taken away rather than read off the spec, because the size a
    figure's text is authored at is an outcome of the layout search: the same spec wraps
    differently at a different width and picks a different font size with it.
    """
    m = size_gate.analyse(svg, avail_w=10_000)
    return m["fmin"], m["fmin_edge"], m["fmin_detail"]


def rungs_for(svg, rungs=TEXT_RUNGS):
    """(shown, skipped) of `rungs` for one drawing, as (target, factor, natural width).

    A rung is skipped when the drawing would have to be shown WIDER than the column to reach
    that text size, because the column will not do that — `max-width` caps it, and the variant
    would then be captioned with a size it is not rendered at. Which is not a gap in the
    evidence but a fact about the figure: a drawing already scaled down on the real page cannot
    reach a text size above the one it renders at there. The skipped ones are reported rather
    than dropped quietly, since a switcher silently offering five of seven sizes reads as a
    figure that only has five.
    """
    primary, _edge, _detail = authored(svg)
    nat_w = size_gate.analyse(svg, avail_w=10_000)["nat_w"]
    shown, skipped = [], []
    for target in rungs:
        factor = target / primary
        # The natural width that lands on this text size: the column scales a drawing by
        # AVAIL_W/W, so its text renders at primary*AVAIL_W/W and W = AVAIL_W*primary/target.
        # Computing it from the display factor instead is wrong, by the scale itself.
        (shown if nat_w * factor <= size_gate.AVAIL_W else skipped).append(
            (target, factor, size_gate.AVAIL_W * primary / target))
    return shown, skipped


# Mirrors the wrapper explain-diff's `render_diagrams_in_html` puts round a figure, deliberately
# rather than a card of this file's own: a variant has to be seen in the card it will ship in,
# and `test_show_limits.py` pins the two together so a change there is not silently missed here.
def card(svg, extra="", style=""):
    return ('<div class="diagram diagram-embed" role="button" tabindex="0"'
            f' aria-label="Enlarge diagram" style="{style}">{svg}{extra}</div>')


def rule(height, dashes, colour, label, side="right"):
    """One height line across a figure, at the px it really sits at.

    Positioned absolutely inside the card, so the offset is measured from the card's PADDING
    box — the drawing starts one card padding below that, which is what the `rem` term is. The
    height a line marks is the RENDERED one: the gate compares against the drawing after the
    column has scaled it, which for a portrait figure that fits is its natural height and for a
    wide one a good deal less.

    `side` puts the label left or right. It exists because the lines can land within a few pixels
    of each other — a figure 4px past the ceiling is exactly the case worth looking at — and two
    labels on the same edge then print on top of one another.

    A label is set in sans on purpose. It is chrome belonging to this document and not part of
    the article, and the one thing that must not happen on this page is a reader mistaking one
    for the other.
    """
    return (f'<div style="position:absolute;left:0;right:0;'
            f'top:calc({render.CARD_PADDING_REM}rem + {height:.0f}px);'
            f'border-top:2px {dashes} {colour}">'
            f'<span style="position:absolute;{side}:0;top:2px;'
            f'font:600 11px system-ui,sans-serif;'
            f'color:{colour}">{html.escape(label)}</span></div>')


def table(headers, rows):
    head = "".join(f"<th>{h}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def panel(groups):
    """One figure between two rails, from `[(group label, [variant, ...])]`.

    A variant is `(key, label, mark, call, aside, figure)`. `label`/`mark` are what the switcher
    shows, `call` the values selecting it implies for the table at the foot, and `aside`/`figure`
    the two places it replaces content — the right rail and the drawing. Every variant is in the
    document and all but one are hidden, so switching costs nothing and the zoom still works:
    each figure is its own card, which is what the lightbox binds to.

    Nothing a variant contributes lands in the content column. The column is the measurement, so
    it holds the drawing and the article's own prose and nothing else; what a variant IS goes in
    the rail beside its numbers.

    The switcher carries the marks as well as the sizes, because the list is where a reader
    decides what to look at: "this is the one the renderer picks" belongs there and not only
    beside a figure they have already chosen.
    """
    asides, figures, buttons = [], [], []
    # Opens on the drawing the renderer really produces, not on the first entry in the list. The
    # first entry is a forced variant, and a page that opens on one invites the reader to judge a
    # limit against a figure that does not exist before they have noticed the marks.
    first = next((key for _label, group in groups for key, _t, mark, *_rest in group
                  if mark == SHIPS), groups[0][1][0][0])
    for label, group in groups:
        items = []
        for key, text, mark, call, *_rest in group:
            data = "".join(f' data-call-{field}="{html.escape(value)}"'
                           for field, value in call.items())
            items.append(f'<button data-v="{key}"{data}'
                         f'{" class=on" if key == first else ""}>{html.escape(text)}'
                         + (f'<span class="mark">{html.escape(mark)}</span>' if mark else "")
                         + "</button>")
        buttons.append(f'<div class="switch-group"><span class="switch-label">'
                       f'{html.escape(label)}</span>'
                       f'<div class="switch-items">{"".join(items)}</div></div>')
    for _label, group in groups:
        for key, _text, _mark, _call, aside, figure in group:
            hide = "" if key == first else " hidden"
            for into, part in ((asides, aside), (figures, figure)):
                into.append(f'<div class="variant" data-v="{key}"{hide}>{part}</div>')
    return (f'<div class="panel"><div class="slot">'
            f'<div class="rail rail-left"><div class="switch">{"".join(buttons)}</div></div>'
            f'<div class="rail rail-right"><div class="aside">{"".join(asides)}</div></div>'
            f'{"".join(figures)}</div></div>')


def rail_note(label, text, bad=False):
    """What a variant is, as an aside under its numbers rather than a pill in the article."""
    return (f'<div class="note{" bad" if bad else ""}"><span class="k">{html.escape(label)}</span>'
            f'<span class="t">{text}</span></div>')


def px(value):
    """A pixel count with no trailing zero — `14px`, not `14.0px`."""
    return f"{value:.1f}".rstrip("0").rstrip(".")


# How a breached column is coloured. The two exist because not every limit is hard: `WARN` is the
# accent, matching the dashed target line on the drawing, and `BAD` is the red of the solid one.
# A reader who sees the same colour for both is being told the target is a wall, which it is not —
# a figure may run past it, and only the ceiling refuses.
WARN, BAD = "warn", "bad"


def sizes_grid(columns):
    """`[(label, value, note, breach)]` as a block each — what the variant measures beside the
    number it is judged against, so the comparison needs no arithmetic from the reader.

    `breach` is "", `WARN` or `BAD`, and the caller picks it rather than the grid comparing:
    "below its floor" and "past the ceiling" are opposite senses of the same reading, and how
    serious either is belongs to the limit, not to the layout.
    """
    blocks = []
    for label, value, note, breach in columns:
        blocks.append(f'<div class="size{" " + breach if breach else ""}">'
                      f'<span class="k">{label}</span>'
                      f'<span class="v">{value}</span>'
                      f'<span class="f">{note}</span></div>')
    return f'<div class="sizes">{"".join(blocks)}</div>'


def text_variants(key, svg):
    """Every text size this figure can honestly be shown at, as switcher variants.

    The one the renderer arrives at on its own is marked, and so is every one below the current
    floor — which is most of the bottom of the range, and unreachable in practice.

    Each variant's card is left to size itself. Holding them all to the tallest was tried and is
    worse: it puts a small drawing at the top of a mostly empty box, and the drawing's own size
    is part of what the reader is judging.
    """
    shown, skipped = rungs_for(svg)
    primary, edge, detail = authored(svg)
    ships = size_gate.analyse(svg)["fmin"]
    picked = min((target for target, _f, _w in shown), key=lambda t: abs(t - ships), default=None)

    out = []
    for target, factor, _natural in shown:
        kinds = [("primary", "primary", primary * factor, size_gate.MIN_READABLE)]
        if edge:
            kinds.append(("edge label", "edge", edge * factor, size_gate.MIN_READABLE_EDGE))
        if detail:
            kinds.append(("subtitle", "detail", detail * factor,
                          size_gate.MIN_READABLE_DETAIL))
        note, mark = "", ""
        if target == picked:
            note = rail_note(SHIPS, f"the size this figure really renders at, {px(ships)}px")
            mark = SHIPS
        elif primary * factor < size_gate.MIN_READABLE:
            note = rail_note("forced", f"under the {px(size_gate.MIN_READABLE)}px "
                             "floor for a box name, so the gate refuses it", bad=True)
            mark = UNDER_FLOOR
        # Selecting a text size states the smallest of each kind you accept, so that is what it
        # fills in below. EVERY field this panel governs, including the ones this figure has
        # nothing to say about: a variant that only wrote the fields it knew left the previous
        # figure's subtitle standing in the table, which is a number nobody selected.
        have = {field: value for _k, field, value, _f in kinds}
        call = {field: f"{px(have[field])}px" if field in have else NOT_SET
                for field in ("primary", "edge", "detail")}
        out.append((
            f"{key}-{target}", " / ".join(px(v) for _k, _f, v, _fl in kinds) + "px", mark, call,
            sizes_grid([(kind, f"{px(value)}px", f"floor {px(floor)}px",
                         BAD if value < floor else "")
                        for kind, _field, value, floor in kinds]) + note,
            card(scaled(svg, factor))))
    return out, skipped


def height_variants(rungs, ships):
    """Every height variant of the one spec, as switcher variants.

    `ships` is that figure as the real pipeline draws it, so the variant the renderer would
    arrive at on its own is identified by measurement rather than by being first in the list. If
    pinning the layout direction ever stopped reproducing the shipped drawing, nothing claims to
    be it — which is the answer, not a gap.
    """
    shipped_h = size_gate.analyse(ships)["rend_h"]
    out = []
    for spacing, svg in rungs:
        m = size_gate.analyse(svg)
        tall = m["rend_h"]
        if abs(tall - shipped_h) < 1:
            note = rail_note(SHIPS, f"the height this figure really reaches, {tall:.0f}px")
            mark = SHIPS
        else:
            note = rail_note("forced", f"{spacing}px between rows, which the layout "
                             "search would not choose", bad=True)
            mark = f"forced, {spacing}px rows"
        out.append((
            f"h{spacing}", f"{tall:.0f}px", mark, {"height": f"{tall:.0f}px"},
            sizes_grid([
                # The scale only when it is doing something, and no width at all: this whole
                # page is about how far down a figure reaches, and its width answers nothing. The
                # scale is 1.00 on every variant of this ladder anyway, the figure being narrower
                # than the column — a number that never varies is furniture, not a measurement.
                ("on the page", f"{tall:.0f}px",
                 f'scaled to {m["scale"]:.2f}' if m["scale"] < 1 else "", False),
                # Past the target is a budget spent, not a refusal, so it takes the
                # accent of the dashed line rather than the red of the solid one.
                ("target", f"{size_gate.MAX_H:.0f}px",
                 f"{abs(tall - size_gate.MAX_H):.0f}px "
                 f"{'past' if tall > size_gate.MAX_H else 'under'}",
                 WARN if tall > size_gate.MAX_H else ""),
                ("hard ceiling", f"{size_gate.MAX_TOTAL_H:.0f}px",
                 f"{abs(tall - size_gate.MAX_TOTAL_H):.0f}px "
                 f"{'past' if tall > size_gate.MAX_TOTAL_H else 'under'}",
                 BAD if tall > size_gate.MAX_TOTAL_H else "")]) + note,
            # Every card held open to the hard ceiling, so all three lines always land inside it
            # and the drawing's own bottom edge reads against them. Without it a line below a
            # short figure paints over the paragraph underneath — the card has no
            # `overflow:hidden` on the real page — and a red rule through the body copy reads as
            # a rendering fault.
            #
            # Three lines, and the figure's own is why: a card held open to 900px plus padding
            # gives no clue where the drawing actually stops, and "how tall is this" is the only
            # question on this ladder. Its label goes on the LEFT because at 904px it sits 4px
            # from the ceiling line, and two labels on one edge print over each other.
            card(svg,
                 rule(tall, "solid", "var(--muted)", f"{tall:.0f}px figure", side="left")
                 + rule(size_gate.MAX_H, "dashed", "var(--accent)",
                        f"{size_gate.MAX_H:.0f}px target")
                 + rule(size_gate.MAX_TOTAL_H, "solid", "var(--diff-del-fg)",
                        f"{size_gate.MAX_TOTAL_H:.0f}px ceiling"),
                 style=f"min-height:{size_gate.MAX_TOTAL_H + 12:.0f}px")))
    return out


def section_text(figures):
    """One figure, one switcher, every text size it can honestly be shown at."""
    groups, notes = [], []
    for key, svg, why in figures:
        variants, skipped = text_variants(key.replace("/", "-"), svg)
        groups.append((key, variants))
        note = f"<code>{key}</code> {why}"
        if skipped:
            note += (" No " + "/".join(f"{t:.0f}" for t, _f, _w in skipped)
                     + "px option: that would mean drawing it wider than the column.")
        notes.append(note)
    return {
        "id": "text", "heading": "Lower limits for text size",
        "html": f"""
<p>Pick the smallest text you would still accept, per kind. {" ".join(notes)} <b>The size is
exact; the drawing is not</b> — a variant is a smaller drawing shown smaller, not a wide one
squeezed in.</p>
{panel(groups)}
<p>{TEXT_RULER}</p>
""",
    }


def section_height(rungs, ships):
    """One figure, one switcher, four real heights with both lines drawn on."""
    key, _spec = TALL
    return {
        "id": "height", "heading": "Upper limit for figure height",
        "html": f"""
<p>Pick the tallest figure you would still put in an article. Real drawings of <code>{key}</code>
with more room between rows — nothing stretched, no text resized. <b>Dashed</b> at
{size_gate.MAX_H:.0f}px is the target, <b>solid</b> at {size_gate.MAX_TOTAL_H:.0f}px is what
nothing ships past, and the {size_gate.RESCUE_H:.0f}px between them may only be borrowed to
remove a defect.</p>
{panel([(key, height_variants(rungs, ships))])}
<p>{HEIGHT_RULER}</p>
""",
    }


def section_values():
    """What the selections above add up to, beside what the code holds now.

    The `data-cell` hooks are what the switchers write into. Filled by script rather than rendered
    here, so the column can never disagree with what is on screen — and labelled as coming from
    the menus, because a number in a results table that silently tracks a control elsewhere is
    worse than no number.
    """
    rows = [("smallest primary text", "MIN_READABLE",
             f"{px(size_gate.MIN_READABLE)}px", "primary"),
            ("smallest edge label", "MIN_READABLE_EDGE",
             f"{px(size_gate.MIN_READABLE_EDGE)}px", "edge"),
            ("smallest subtitle", "MIN_READABLE_DETAIL",
             f"{px(size_gate.MIN_READABLE_DETAIL)}px", "detail"),
            ("height a figure aims under", "MAX_H", f"{size_gate.MAX_H:.0f}px", "height")]
    return {
        "id": "values", "heading": "Selected values",
        "html": f"""
{table(["limit", "in <code>gates/size.py</code>", "today", "selected above"],
       [(what, f"<code>{const}</code>", now,
         f'<span class="call" data-cell="{cell}">{NOT_SET}</span>')
        for what, const, now, cell in rows])}
<p>The last column follows the two menus and nothing else. A subtitle stays blank while a figure
without one is selected.</p>
"""
    }


def build(measured, rungs, three_kinds, at_the_floor):
    shipped = dict(measured)[TALL[0]]
    figures = [
        three_kinds + ("carries all three kinds of text at once.",),
        at_the_floor + ("sits closest to a floor today.",)]
    return {
        "title": "Setting the renderer's limits",
        "subtitle": "What a person can read, and how far they will scroll — the evidence, in the "
                    "layout the decision is about",
        "sections": [section_text(figures), section_height(rungs, shipped),
                     section_values()],
        # This page's own furniture, and the reason it is injected rather than added to the
        # explainer: a switcher is not something a real article has, and the explainer's CSS is
        # imported here for its LAYOUT, not to be grown by a tool that borrows it.
        "extra_css": PAGE_CSS, "extra_js": PAGE_JS,
    }


def pick_three_kinds(measured):
    """The figure carrying all three kinds of text that can be shown at the most sizes.

    Most sizes rather than smallest or shortest, because this exhibit's whole job is to put the
    three floors beside each other over as much of the range as the column allows; a figure the
    page already scales down loses the top of the range before it starts.
    """
    carriers = [(key, svg) for key, svg in measured
                if size_gate.analyse(svg)["fmin_detail"] is not None]
    if not carriers:                       # no subtitle anywhere: two floors, not three
        carriers = list(measured)
    return max(carriers, key=lambda item: (len(rungs_for(item[1])[0]),
                                           -size_gate.analyse(item[1])["nat_h"]))


def pick_at_the_floor(measured):
    """The figure whose text sits closest to its own floor today, in floors' own units.

    Compared as a margin per kind rather than as a raw size, since the three floors are
    different numbers — an 11px edge label has 2px of room where an 11px name has 1.
    """
    def margin(svg):
        m = size_gate.analyse(svg)
        return min([m["fmin"] - size_gate.MIN_READABLE]
                   + ([m["fmin_edge"] - size_gate.MIN_READABLE_EDGE]
                      if m["fmin_edge"] is not None else [])
                   + ([m["fmin_detail"] - size_gate.MIN_READABLE_DETAIL]
                      if m["fmin_detail"] is not None else []))
    return min(measured, key=lambda item: margin(item[1]))


def write(spec, out=OUT):
    """Render `spec` through the explainer and add this page's own CSS and JS.

    Appended to the finished document rather than handed to the explainer, because the explainer
    has no notion of a page that borrows its layout — and giving it one would put this tool's
    furniture into every real article.
    """
    page = explainer().render(spec)
    page = page.replace("</head>", f'<style>{spec["extra_css"]}</style></head>', 1)
    page = page.replace("</body>", f'<script>{spec["extra_js"]}</script></body>', 1)
    with open(out, "w", encoding="utf-8") as handle:
        handle.write(page)
    return out


def main():
    measured = []
    for corpus, specs in CORPORA.items():
        # One call per corpus, not per figure: `figure.draw` batches its browser work across
        # everything it is handed, so this is one Chrome launch instead of five.
        for fig in figure.draw(specs, target="embed"):
            measured.append((f"{corpus}/{fig.name}", fig.svg))
            problems = fig.problems + fig.blocked
            print(f"{corpus}/{fig.name:10} {'; '.join(problems) or 'gates clean'}")

    # A pinned direction is what makes the row spacing settable at all — `render.render` only
    # forwards it once it no longer has a layout to choose. The figure ships portrait anyway, so
    # the first variant comes out as the drawing the search picks on its own.
    _key, spec = TALL
    portrait = dict(spec, direction="down")
    rungs = [(spacing, render.render(portrait, name=f"tall-{spacing}", layers=spacing))
             for spacing in HEIGHT_RUNGS]

    print(write(build(measured, rungs,
                      pick_three_kinds(measured), pick_at_the_floor(measured))))
    if sys.platform == "darwin" and "--no-open" not in sys.argv:
        subprocess.run(["open", OUT], check=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
