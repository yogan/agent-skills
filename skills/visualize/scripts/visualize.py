#!/usr/bin/env python3
"""Render a diagram spec: place its callouts, check it against the gates, open the image.

A thin CLI over `lib/diagram`. Everything interesting — the spec vocabulary, the d2 recipe,
the gates — lives in the library, so the explainer skills get identical output without going
through this script (and without needing this skill installed).

    python3 visualize.py spec.json                    # -> a standalone SVG + PNG, opened
    python3 visualize.py spec.json --theme light      # ... baked light instead of dark
    python3 visualize.py spec.json --format embed     # -> a themeable SVG for a host page
    python3 visualize.py --format css                 # -> the CSS that page must ship

The default output is a **standalone image**: colours baked to one theme, the page background
painted, and the CSS a callout's `<foreignObject>` text needs carried inside the file. Both an
SVG and a PNG are written, and the PNG is what gets opened — macOS renders SVG through Quick
Look, which ignores the canvas and crops the drawing square, so handing a viewer the SVG shows
the reader something we never rendered. No HTML wrapper either way. A page around a single
figure is the explainers' format, and wrapping one here would only put the drawing back inside
a content column it then has to be squeezed into. A file has no width to fit and no theme
toggle, which is why it is shown at full size and why it has to pick a theme.

`--format embed` is the other direction: an SVG whose colours are `var(--d-…)` references, for
a page that supplies them and follows its own light/dark toggle. That one is not a preview —
opened on its own its callout text clips and its shapes render unpainted.
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys

# Repo root, 4 levels up from skills/visualize/scripts/visualize.py — needed so `lib/`,
# which lives outside this skill's own directory, is importable regardless of how this
# script is invoked (direct, or symlinked into ~/.claude/skills/).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.realpath(__file__)))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from lib.diagram import figure, overview, render                  # noqa: E402
from lib.diagram.gates import report                              # noqa: E402
from lib.diagram.spec import SpecError, validate                  # noqa: E402


def die(message, code=2):
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(code)


SLUG_MAX = 40


def slugify(text):
    """A filename-safe short name, also used to namespace the SVG's ids.

    Capped because a title makes a long slug, and this ends up both in a filename and as a
    prefix on every id inside the SVG.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    if len(slug) > SLUG_MAX:
        slug = slug[:SLUG_MAX].rstrip("-")
    return slug or "diagram"


def load_spec(path):
    try:
        raw = sys.stdin.read() if str(path) == "-" else open(path, encoding="utf-8").read()
    except OSError as exc:
        die(f"could not read the spec: {exc}")
    try:
        return json.loads(raw)
    except ValueError as exc:
        die(f"the spec is not valid JSON: {exc}")


def open_file(path):
    """Open with the system's default handler. Never fatal."""
    opener = {"darwin": "open", "win32": "start"}.get(sys.platform, "xdg-open")
    try:
        subprocess.run([opener, str(path)], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("spec", nargs="?", help="path to the JSON diagram spec, or - for stdin")
    parser.add_argument("-o", "--output", help="where to write it (default: /tmp/<date>-diagram-<slug>)")
    parser.add_argument("--format", choices=("svg", "embed", "css"), default="svg",
                        help="svg: a standalone image, opened when it is done (default). "
                             "embed: a themeable SVG for a host page that ships the CSS. "
                             "css: print that CSS.")
    parser.add_argument("--theme", choices=("light", "dark"), default="dark",
                        help="which theme to bake into a standalone image (default: dark, "
                             "because the PNG is opened in the system image viewer, whose "
                             "chrome follows the OS appearance).")
    parser.add_argument("--overview", action="store_true",
                        help="collapse an architecture to one box per area: each container "
                             "becomes a single node listing its members, and the edges between "
                             "areas are merged. For \"how do these fit together\" on a spec too "
                             "big to read — render it twice, once with this and once without.")
    parser.add_argument("--drop", action="append", default=[], metavar="ID",
                        help="with --overview: leave this top-level node out entirely. For an "
                             "entry point that touches every area, where \"everything is "
                             "reachable from the API\" is a sentence rather than N arrows. "
                             "Repeatable.")
    parser.add_argument("--no-png", action="store_true",
                        help="write only the SVG, skipping the rasterise (one browser launch). "
                             "The SVG is the artifact; the PNG is what a viewer can show.")
    parser.add_argument("--no-open", action="store_true",
                        help="write the file without opening it — use this while iterating, "
                             "so only the finished diagram opens")
    parser.add_argument("--no-place", action="store_true",
                        help="trust the spec's own `near` values instead of measuring "
                             "every anchor (much faster, usually worse)")
    parser.add_argument("--no-gates", action="store_true",
                        help="skip the gates (they are the point; for debugging only)")
    args = parser.parse_args()

    # `--format css` is a standalone query about the library, not about any one diagram.
    if args.format == "css":
        print(render.page_css(), end="")
        return 0
    if not args.spec:
        parser.error("a spec is required (or use --format css)")

    spec = load_spec(args.spec)
    try:
        validate(spec)
    except SpecError as exc:
        die(str(exc))

    if args.overview:
        try:
            spec = overview.collapse(spec, drop=tuple(args.drop))
        except (SpecError, ValueError) as exc:
            die(str(exc))
    elif args.drop:
        die("--drop only applies with --overview")

    name = slugify(spec.get("slug") or spec.get("title") or spec.get("kind"))
    target = "file" if args.format == "svg" else "embed"
    standalone = target == "file"

    # One call: validated, placed, rendered and gated. The CLI states what the picture is FOR
    # and reports what came back; it does not know which gates that implies. See
    # lib/diagram/figure.py.
    try:
        drawn = figure.draw({name: spec}, target=target, theme=args.theme,
                            place_callouts=not args.no_place, gates=not args.no_gates)[0]
    except (SpecError, render.RenderError) as exc:
        die(str(exc))

    for line in drawn.advice:
        print(f"warning: {line}", file=sys.stderr)
    svg = drawn.svg

    failed = 0
    if drawn.results:
        failed = report(drawn.results, stream=sys.stderr)
    for problem in drawn.blocked:
        print(f"GATE COULD NOT RUN — {problem}", file=sys.stderr)
    failed += len(drawn.blocked)
    # Placement findings are not a gate verdict — no anchor fits and the remedy is editorial —
    # so they are said plainly rather than counted against the exit code.
    for problem in drawn.placement:
        print(f"warning: {problem}", file=sys.stderr)

    date = datetime.date.today().strftime("%Y-%m-%d")
    out_path = args.output or f"/tmp/{date}-diagram-{name}.svg"
    render.write_svg(svg, out_path)
    print(out_path)

    if standalone:
        # The SVG is the artifact; the PNG is what a person can actually be shown. macOS
        # renders SVG through Quick Look, which ignores our canvas and crops the drawing
        # square — the reference state machine loses its callout and two of its states — so
        # handing the default viewer an SVG shows the reader something we never rendered.
        png = None
        if not args.no_png:
            try:
                png = render.rasterise_standalone(
                    svg, out_path, title=spec.get("title"), theme=args.theme)
                print(png)
            except render.RenderError as exc:
                print(f"note: could not rasterise to PNG ({exc}) — opening the SVG instead, "
                      "which some viewers crop", file=sys.stderr)
        if not args.no_open:
            opened = png or out_path
            open_file(opened)
            # Named on stderr because stdout is paths only, and because the agent relaying
            # this got it wrong: it reported the SVG while the PNG was on screen.
            print(f"opened: {opened}", file=sys.stderr)
    else:
        # Saying this every time is deliberate: an SVG that looks broken in a viewer is the
        # single most likely confusion this tool can cause, and the cause is never the SVG.
        print("note: an --format embed SVG needs its host page to ship "
              "`visualize.py --format css` — on its own its callout text clips and its "
              "themed colours resolve to nothing. For a file to look at, drop --format.",
              file=sys.stderr)

    # Non-zero when a gate failed, so a caller notices — but the file is written and opened
    # either way. Showing a flawed diagram alongside a precise list of what is wrong with it
    # beats showing nothing at all.
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
