"""Driving a headless browser to measure rendered diagrams.

A browser is in the render loop by decision, not by accident. Three things need one and
have no workable substitute:

  * **the clipping gate.** A static SVG checker was written twice and abandoned twice: it
    ignores `transform="translate()"` and it cannot see a CSS drop-shadow's spread.
  * **callout placement.** d2 takes one of eight fixed anchors and does no overlap
    avoidance, so choosing well means rendering the alternatives and looking at them.
  * **callout text size.** It is HTML in a `<foreignObject>`, laid out with the host page's
    CSS. Nothing outside a browser knows how big it comes out.

Cost, measured on the machine this was developed on, so nobody has to re-derive it: about
10s for automatic placement of 7 callouts, 12s for an exhaustive two-callout search, 3.6s
for the clipping gate over six diagrams. Fine for generating a document once; not fine per
keystroke, so keep this in the build step and never in a preview path.

Deliberately `puppeteer-core` (29 MB, no bundled browser) driving the system Chrome, rather
than `puppeteer` (which downloads its own ~550 MB Chromium). `js/measure.js` holds the
resolution logic and the env overrides.
"""
import json
import os
import shutil
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
MEASURE_JS = os.path.join(HERE, "js", "measure.js")

# The drop-shadow allowance, in px. Light mode spends offset 2 + blur 5; dark mode's accent
# glow spends blur 5 with no offset. 8 covers both with a little room. It exists because
# getBoundingClientRect() excludes shadow spread entirely — without it a callout sitting
# flush with the edge measures as fitting while its glow is cut off, and the gate reports a
# confident zero.
SHADOW_PX = 8

# What an overlap costs, by what it damages. Covering a label makes it unreadable; covering
# an edge hides a relationship; covering the body of a shape is nearly free. Without these
# weights the placement search optimises for total area and cheerfully buries a label to
# keep off a big rectangle.
OVERLAP_WEIGHTS = {
    "text": 6, "foreignobject": 6,
    "path": 2,
    "rect": 0.3, "ellipse": 0.3, "circle": 0.3, "polygon": 0.3,
}

VIEWPORT = {"width": 1200, "height": 1000}


class BrowserError(RuntimeError):
    """The browser could not be run, or could not measure what it was asked to.

    Distinct from a gate *finding*: this means we learned nothing, and a caller must not
    turn it into a pass. See gates/__init__.py on why that distinction is written down.
    """


def node_available():
    return shutil.which("node") is not None


def available():
    """Whether a measurement run is possible at all. Cheap; does not launch anything."""
    return node_available() and os.path.exists(MEASURE_JS)


def requirements():
    """Human-readable list of what is missing, empty when everything is present."""
    problems = []
    if not node_available():
        problems.append("node is not on PATH (needed to drive the browser)")
    if not os.path.exists(MEASURE_JS):
        problems.append(f"missing {MEASURE_JS}")
    return problems


def measure(jobs, viewport=None, shadow=SHADOW_PX, weights=None, timeout=180):
    """Measure a batch of harness pages in one browser launch.

    `jobs` is a list of `{"key": ..., "html": ...}`. Returns a list of measurement dicts in
    the same order, each carrying the `key` back.

    Batched on purpose: launching Chrome costs far more than measuring one more page, and
    the placement search wants to compare eight candidates at a time. Raises BrowserError
    rather than returning partial results — a half-measured placement search would silently
    pick a worse anchor.
    """
    jobs = list(jobs)
    if not jobs:
        return []
    problems = requirements()
    if problems:
        raise BrowserError("; ".join(problems))

    payload = json.dumps({
        "viewport": viewport or VIEWPORT,
        "shadow": shadow,
        "weights": weights if weights is not None else OVERLAP_WEIGHTS,
        "jobs": jobs,
    })
    try:
        proc = subprocess.run(["node", MEASURE_JS], input=payload, capture_output=True,
                              text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise BrowserError(f"the browser did not finish measuring {len(jobs)} page(s) "
                           f"within {timeout}s")
    if proc.returncode != 0:
        raise BrowserError(proc.stderr.strip() or
                           f"node exited {proc.returncode} with no message")
    try:
        results = json.loads(proc.stdout)["results"]
    except (ValueError, KeyError) as exc:
        raise BrowserError(f"could not read the measurement output ({exc}): "
                           f"{proc.stdout[:400]!r}")
    if len(results) != len(jobs):
        raise BrowserError(f"asked for {len(jobs)} measurement(s), got {len(results)}")
    for result in results:
        if result.get("error"):
            raise BrowserError(f"{result.get('key')}: {result['error']}")
    return results
