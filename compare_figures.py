#!/usr/bin/env python3
"""Two renders of the same corpus, side by side, with what changed written on them.

The renderer's output is judged by eye. Nothing else can judge it: every gate in `lib/diagram`
passed the five figures that prompted the layout work this tool was built for, because "a label
deletes the leg of the arrow it sits on" is not a gate's kind of defect. So the loop is render,
look, change, look again — and looking again is worthless without the first picture beside the
second one.

    python3 compare_figures.py capture before          # BEFORE you change anything
    ...edit lib/diagram...
    python3 compare_figures.py capture after
    python3 compare_figures.py sheet before after notes.json

A capture is a directory of SVGs under `/tmp/diagram-compare/<tag>/`, rendered through
`figure.draw` — the real pipeline, callout placement included — so what you compare is what a
skill would ship. Captures are cheap to keep and cost about 25s for both corpora; keep the one
from before a session's first edit and you can compare against it all day.

`notes.json` is what makes the sheet worth reading a week later. Without it the reader gets two
pictures and has to spot the difference; with it, each side says what it is:

    {"repo/arch":      {"title": "architecture — the run that prompted this",
                        "issues":  ["labels sit on the horizontal leg and mask it"],
                        "changes": ["label moved beside the line, arrow unbroken"]},
     "reference/arch": {"changes": ["same pass, no geometry change"]}}

Keys are `<corpus>/<name>`; every key is optional and so is the file itself. Gate problems are
added underneath on their own, from the capture, because a sheet that looks better while the
clipping gate is complaining is a sheet that is lying to you.

BOTH corpora are captured by default, and that is the point of there being two. `examples.py`
is the scenario the renderer was tuned against, so it is the one a change is least likely to
break; `examples_repo.py` is a real `/explain-branch` run nobody steered. A layout change that
improves one and ruins the other is the normal outcome, not the unlikely one.
"""
import argparse
import html as html_mod
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.diagram import browser, figure, render          # noqa: E402
from lib.diagram.examples import REFERENCE               # noqa: E402
from lib.diagram.examples_repo import REPO               # noqa: E402

CORPORA = {"repo": REPO, "reference": REFERENCE}
ROOT = "/tmp/diagram-compare"

# Widest a figure is shown at, in CSS px. Two of these sit side by side plus the page's own
# margins, so it is really a statement about the screen the sheet is opened on. A drawing wider
# than this is scaled down by `HOST_CSS`'s `max-width:100%`, exactly as the content column does
# it on a real page.
MAX_COL = 900
# Narrowest, so a small diagram still leaves its annotation somewhere to go.
MIN_COL = 300
GUTTER = 16
# The card's own padding, a side — the drawing does not sit flush against its edge.
CARD_PADDING = 9

# The sheet's own chrome, per theme. It has to follow the diagrams: a light-grey page behind a
# dark-mode render says the drawing is the wrong colour when it is the sheet that is.
CHROME = {"light": ("#eceae5", "#1a1a1a", "#fafaf8", "#3c3c3c", "#6b6b6b"),
          "dark": ("#101216", "#e8e6e1", "#1f2229", "#c8c6c1", "#9a9a9a")}

CSS = """
html,body{margin:0;padding:0;background:%(page)s;
  font:14px system-ui,-apple-system,'Segoe UI',sans-serif;color:%(ink)s}
.wrap{padding:16px 18px 24px;display:inline-block}
h2{margin:22px 0 8px;font-size:18px;letter-spacing:.2px}
h2:first-child{margin-top:0}
.pair{display:flex;gap:%(gutter)spx;align-items:flex-start}
.side{min-width:0}
.tag{font:700 11px system-ui;letter-spacing:.7px;text-transform:uppercase;
  margin:0 0 5px;color:%(faint)s}
.tag.after{color:#b5541f}
.card{background:%(card)s;border-radius:7px;padding:%(pad)spx;box-sizing:border-box}
.card svg{max-width:100%%;height:auto;display:block}
ul{margin:7px 0 0;padding-left:17px}
li{margin:2px 0;line-height:1.35;font-size:12.5px;color:%(note)s}
li.gate{color:#c0392b;list-style:square}
.none{margin:7px 0 0;font-size:12.5px;color:%(faint)s;font-style:italic}
"""


def capture(tag, corpora, only, target, theme):
    """Render every requested figure into `/tmp/diagram-compare/<tag>/` and report."""
    out = os.path.join(ROOT, tag)
    os.makedirs(out, exist_ok=True)
    meta = {}
    for corpus in corpora:
        specs = {n: s for n, s in CORPORA[corpus].items() if not only or n in only}
        if not specs:
            continue
        for fig in figure.draw(specs, target=target, theme=theme):
            key = f"{corpus}/{fig.name}"
            with open(os.path.join(out, f"{corpus}.{fig.name}.svg"), "w") as handle:
                handle.write(fig.svg)
            meta[key] = {"size": list(render.natural_size(fig.svg)),
                         "problems": fig.problems + fig.blocked}
            flags = "; ".join(meta[key]["problems"])
            print(f"{key:20} {meta[key]['size'][0]:.0f}x{meta[key]['size'][1]:.0f}  "
                  f"{flags or 'gates clean'}")
    with open(os.path.join(out, "meta.json"), "w") as handle:
        json.dump(meta, handle, indent=1)
    return out


def _load(tag):
    directory = os.path.join(ROOT, tag)
    if not os.path.isdir(directory):
        sys.exit(f"no capture called {tag!r} — run: compare_figures.py capture {tag}")
    svgs = {}
    for name in sorted(os.listdir(directory)):
        if name.endswith(".svg"):
            corpus, figure_name, _ = name.split(".", 2)
            with open(os.path.join(directory, name)) as handle:
                svgs[f"{corpus}/{figure_name}"] = handle.read()
    meta_path = os.path.join(directory, "meta.json")
    meta = {}
    if os.path.exists(meta_path):
        with open(meta_path) as handle:
            meta = json.load(handle)
    return svgs, meta


def panel_order(before, after, notes, changed_only=True):
    """The figures to show, notes order first, then whatever both captures have left.

    The figure a change is FOR should be the one the reader sees first and the regression
    checks after it — which is the order the notes were written in, and never alphabetical.
    A note naming a figure neither capture holds is dropped rather than shown empty.

    `changed_only` drops the figures that came out byte-identical, because a sheet where eight
    of ten panels are the same picture twice buries the two that are not. What was dropped is
    PRINTED rather than silently omitted: "these did not change" is a result, and a sheet that
    quietly shows a subset reads as a sheet that shows everything.
    """
    shared = set(before) & set(after)
    if changed_only:
        shared = {k for k in shared if before[k] != after[k]}
    return [k for k in notes if k in shared] + sorted(shared - set(notes))


def column(sizes):
    """How wide one side of a panel is, in CSS px, given both drawings' natural sizes.

    From the WIDER of the two, so a change that grows a figure is visible as growth rather
    than hidden by the other side being scaled down to match it.
    """
    return max(MIN_COL, min(MAX_COL, max(w for w, _h in sizes) + CARD_PADDING * 2))


def _bullets(lines, gates):
    items = "".join(f"<li>{html_mod.escape(line)}</li>" for line in lines)
    items += "".join(f'<li class="gate">{html_mod.escape(g)}</li>' for g in gates)
    return f"<ul>{items}</ul>" if items else '<p class="none">nothing noted</p>'


def _side(kind, column, svg, lines, gates):
    return (f'<div class="side" style="width:{column:.0f}px">'
            f'<p class="tag {kind}">{kind}</p>'
            f'<div class="card diagram">{svg}</div>{_bullets(lines, gates)}</div>')


def sheet(before_tag, after_tag, notes, out, theme, changed_only=True):
    """Write one PNG comparing two captures, and open it."""
    before, before_meta = _load(before_tag)
    after, after_meta = _load(after_tag)
    keys = panel_order(before, after, notes, changed_only)
    same = sorted(k for k in set(before) & set(after) if k not in keys)
    if same:
        print(f"unchanged, not shown: {', '.join(same)}")
    if not keys:
        sys.exit(f"{before_tag} and {after_tag} differ in no figure at all")

    panels, width = [], 0
    for key in keys:
        note = notes.get(key, {})
        wide = column([render.natural_size(before[key]), render.natural_size(after[key])])
        width = max(width, wide * 2 + GUTTER)
        panels.append(
            f'<h2>{html_mod.escape(note.get("title", key))}</h2><div class="pair">'
            + _side("before", wide, before[key], note.get("issues", []),
                    _gates(before_meta, key))
            + _side("after", wide, after[key], note.get("changes", []),
                    _gates(after_meta, key))
            + "</div>")

    page_bg, ink, card, note_ink, faint = CHROME[theme]
    css = CSS % {"page": page_bg, "ink": ink, "card": card, "note": note_ink,
                 "faint": faint, "gutter": GUTTER, "pad": CARD_PADDING}
    page = (f'<!DOCTYPE html><html data-theme="{theme}"><meta charset="utf-8"><style>'
            f"html{{font-size:{render.ROOT_FONT_PX}px}}{css}{render.page_css()}</style>"
            f'<div class="wrap">{"".join(panels)}</div></html>')
    browser.rasterise(page, out, width + 40, height=900, full=True)
    print(out)
    if sys.platform == "darwin":
        subprocess.run(["open", out], check=False)
    return out


def _gates(meta, key):
    return (meta.get(key) or {}).get("problems") or []


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    grab = sub.add_parser("capture", help="render a corpus into a named snapshot")
    grab.add_argument("tag")
    grab.add_argument("--corpus", choices=sorted(CORPORA) + ["both"], default="both")
    grab.add_argument("--only", nargs="*", default=[], metavar="NAME",
                      help="figure names, e.g. arch er")
    grab.add_argument("--target", choices=("embed", "file"), default="embed")
    grab.add_argument("--theme", choices=("light", "dark"), default="light")

    show = sub.add_parser("sheet", help="one PNG comparing two snapshots, and open it")
    show.add_argument("before")
    show.add_argument("after")
    show.add_argument("notes", nargs="?", help="JSON, keyed <corpus>/<name>")
    show.add_argument("-o", "--out", default=os.path.join(ROOT, "sheet.png"))
    show.add_argument("--theme", choices=("light", "dark"), default="light")
    show.add_argument("--all", action="store_true",
                      help="show every figure, not only the ones that changed")

    args = parser.parse_args(argv)
    if args.command == "capture":
        corpora = sorted(CORPORA) if args.corpus == "both" else [args.corpus]
        print(capture(args.tag, corpora, set(args.only), args.target, args.theme))
        return 0
    notes = {}
    if args.notes:
        with open(args.notes) as handle:
            notes = json.load(handle)
    sheet(args.before, args.after, notes, args.out, args.theme,
          changed_only=not args.all)
    return 0


if __name__ == "__main__":
    sys.exit(main())
