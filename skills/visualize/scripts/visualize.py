#!/usr/bin/env python3
"""Render a diagram spec: place its callouts, check it against the gates, open the image.

A thin CLI over `lib/diagram`. Everything interesting — the spec vocabulary, the d2 recipe,
the gates — lives in the library, so the explainer skills get identical output without going
through this script (and without needing this skill installed).

    python3 visualize.py spec.json                    # -> a standalone SVG, opened
    python3 visualize.py spec.json --theme dark       # ... baked dark instead of light
    python3 visualize.py spec.json --format embed     # -> a themeable SVG for a host page
    python3 visualize.py --format css                 # -> the CSS that page must ship

The default output is a **standalone image**: colours baked to one theme, the page background
painted, and the CSS a callout's `<foreignObject>` text needs carried inside the file. It is
opened directly, and that is the end of the run — no HTML wrapper. A page around a single
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

from lib.diagram import browser, place, render                    # noqa: E402
from lib.diagram.gates import GateError, clipping, contrast, report, size, theming  # noqa: E402
from lib.diagram.spec import SpecError, content_warnings, validate  # noqa: E402


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


def run_gates(svg, name, standalone=True, theme="light"):
    """Every gate that can run, as a list of Results.

    The two output modes get genuinely different gate sets, because most of the gates are
    statements about a host page:

    * **standalone** drops the page-width, viewport-height and glyph-vs-prose rules (see
      gates/size.py — applying them to a file is what makes a wide diagram "fail" and pushes
      an author into splitting one that was perfectly legible), checks contrast for the one
      theme that got baked in, and skips theming entirely since there are no vars left to
      follow a toggle. `render.standalone` verifies mappability itself, before baking.
    * **embedded** keeps all of them, and checks both themes, because it really does render
      both ways.

    A gate that cannot measure something raises GateError and is reported as blocked, never
    silently skipped — see lib/diagram/gates/__init__.py.
    """
    results, blocked = [], []

    def run(label, fn):
        try:
            results.append(fn())
        except GateError as exc:
            blocked.append(f"{label}: {exc}")

    run("size", lambda: size.check(svg, name, standalone=standalone))
    run("contrast", lambda: contrast.check(
        svg, name, themes=(theme,) if standalone else ("light", "dark")))
    if not standalone:
        run("theming", lambda: theming.check(svg, name))

    if browser.available():
        run("clipping", lambda: clipping.check_many(
            {name: svg}, theme=theme, standalone=standalone)[0])
    else:
        blocked.append("clipping: " + "; ".join(browser.requirements())
                       + " — this gate is the only one that can see a callout cut off, "
                         "so its absence is not a clean bill of health")
    return results, blocked


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
    parser.add_argument("--theme", choices=("light", "dark"), default="light",
                        help="which theme to bake into a standalone image (default: light, "
                             "because a file is viewed inside a frame it cannot paint — a "
                             "browser's white page — and a dark drawing fights it).")
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

    for warning in content_warnings(spec):
        print(f"warning: {warning}", file=sys.stderr)

    name = slugify(spec.get("slug") or spec.get("title") or spec.get("kind"))

    standalone = args.format == "svg"

    if not args.no_place:
        try:
            spec, report_rows = place.place(spec, name=name, theme=args.theme,
                                            standalone=standalone)
        except place.PlacementError as exc:
            # Not fatal: the spec's own anchors (or the default) still render. Worth saying
            # out loud, because the callouts are then placed by guess rather than measurement.
            print(f"warning: could not place callouts automatically ({exc}); "
                  "using the anchors in the spec", file=sys.stderr)
            report_rows = []
        for entry in place.unplaceable(report_rows):
            print(f"warning: callout {entry['index']} still clips by {entry['clip']:.0f}px at "
                  f"{entry['near']} — no anchor fits, so shorten its note text",
                  file=sys.stderr)

    try:
        if standalone:
            svg = render.standalone(spec, name=name, theme=args.theme)
        else:
            svg = render.render(spec, name=name)
    except render.RenderError as exc:
        die(str(exc))

    failed = 0
    if not args.no_gates:
        results, blocked = run_gates(svg, name, standalone=standalone, theme=args.theme)
        if results:
            failed = report(results, stream=sys.stderr)
        for problem in blocked:
            print(f"GATE COULD NOT RUN — {problem}", file=sys.stderr)
        failed += len(blocked)

    date = datetime.date.today().strftime("%Y-%m-%d")
    out_path = args.output or f"/tmp/{date}-diagram-{name}.svg"
    render.write_svg(svg, out_path)
    print(out_path)

    if standalone:
        # The whole point of this mode: the file works by itself, so opening it is the end of
        # the job. No HTML wrapper — a page around a single figure is the explainers' format,
        # and here it would only reintroduce a content column to be squeezed into.
        if not args.no_open:
            open_file(out_path)
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
