"""The clipping gate: is anything actually cut off?

The only gate here that needs a browser, and it needs one for reasons that were established
by writing the static version twice and throwing it away twice:

  * it ignored `transform="translate()"`, which reimplementing correctly means
    reimplementing SVG's coordinate system;
  * it could not see `<foreignObject>` content — the callout text — at all;
  * it could not account for a CSS `drop-shadow`'s spread, which no geometry API reports.

That last one produced a green result that was simply false: a callout flush with the edge
measured as fitting while its glow was being cut, and the gate said zero clipped.

Two boundaries, deliberately different, because they are different questions:

  * **non-callout geometry** is held to the `<svg>` box. The drawing should live inside its
    own canvas.
  * **a callout** is held to the CARD (`overflow:hidden`), which is the surface that really
    clips on the page. d2 reserves no canvas space for a callout at all, so it is normal and
    fine for one to reach into the card's padding; what is not fine is escaping the card.

So this gate is lenient about a callout bleeding a few px past the viewBox and strict about
anything leaving the card. Keeping geometry inside the viewBox is `place.py`'s job, scored
there; being visibly cut off is the failure here.
"""
from .. import browser as browser_mod
from .. import render as render_mod
from . import GateError, Result

# Overflow below this is measurement noise from sub-pixel layout, not a clip.
TOLERANCE_PX = 1.0


def check_many(svgs, theme="light", standalone=False):
    """Check several diagrams in one browser launch. `svgs` maps name -> SVG text.

    Batched because launching Chrome costs far more than measuring one more page.
    `standalone=True` measures against the SVG's own canvas instead of a page card — see
    render.harness_html.
    """
    if not svgs:
        return []
    jobs = [{"key": name,
             "html": render_mod.harness_html(svg, theme=theme, standalone=standalone)}
            for name, svg in svgs.items()]
    try:
        results = browser_mod.measure(jobs)
    except browser_mod.BrowserError as exc:
        # A gate that cannot run must fail, never pass. Everything this gate exists to
        # catch is invisible to the other five, so quietly skipping it would leave a
        # diagram with its callout text sliced off looking entirely green.
        raise GateError(f"the clipping gate could not run: {exc}") from exc

    out = []
    for result in results:
        over = result["overflow"]
        hits = {side: value for side, value in over.items() if value > TOLERANCE_PX}
        problems = []
        if hits:
            where = "  ".join(f"{side} {value:.0f}px" for side, value in sorted(hits.items()))
            culprits = ", ".join(
                f"{o['tag']}{'(callout)' if o['callout'] else ''}"
                + (f" “{o['text']}”" if o["text"] else "")
                for o in result["offenders"][:3])
            problems.append(f"CLIPPED {where}"
                            + (f" — {culprits}" if culprits else "")
                            + " — shorten the note text or pick another anchor; a narrower "
                              "callout fits where a wide one cannot")
        # Text a reader cannot read. Held to zero rather than to a tolerance: a diagram exists
        # to say something, and there is no amount of a word being unreadable that is fine.
        # `render._pick_layout` already widens the layer spacing to clear this, so reaching the
        # gate means no spacing on the ladder was enough — which is an editorial problem, the
        # same as a note too long to place.
        if result.get("hiddenText"):
            buried = ", ".join(f"“{h['text']}” {h['fraction'] * 100:.0f}%"
                               for h in result.get("hidden", [])[:3])
            problems.append(
                f"HIDDEN TEXT {result['hiddenText']:.0f}px² — {buried} — a label is overlapping "
                "geometry it is not inside, so it is either covered or printed on the wrong "
                "background; shorten the label or the diagram")
        detail = (f"{result['svg']['width']:.0f}x{result['svg']['height']:.0f} "
                  f"{len(result['callouts'])} callout(s)")
        out.append(Result(result["key"], "clipping", problems, detail))
    return out


def check(svg, name="diagram", theme="light", standalone=False):
    """Single-diagram convenience wrapper. Prefer check_many for a whole document."""
    return check_many({name: svg}, theme=theme, standalone=standalone)[0]
