"""Driving a headless browser to measure rendered diagrams.

A browser is in the render loop by decision, not by accident. Three things need one and
have no workable substitute:

  * **the clipping gate.** A static SVG checker was written twice and abandoned twice: it
    ignores `transform="translate()"` and it cannot see a CSS drop-shadow's spread.
  * **callout placement.** d2 takes one of eight fixed anchors and does no overlap
    avoidance, so choosing well means rendering the alternatives and looking at them.
  * **callout text size.** It is HTML in a `<foreignObject>`, laid out with the host page's
    CSS. Nothing outside a browser knows how big it comes out.

Cost. Two numbers decide every other one here: **starting a node+Chrome that measures nothing,
against measuring one more harness page in an already-started one.** They are of the order of
0.7s and 0.03s respectively — twenty to one — and the pair actually recorded is in
`speed_baseline.json`, because quoted here they go stale, and twice have.

**So a browser is kept and reused, and that is what `_Browser` and the pool below are for.**
The work arrives as many small batches rather than one big one — a note's anchors, a spacing
rung, a gate over five drawings — and a process per batch spent most of a full check booting
Chrome. Recorded: a full check of the eleven sample diagrams sends the same 31 batches as it
always did and starts 5 browsers where it started 31; one diagram from scratch sends 5 and
starts 1. Nothing survives the command — `shutdown` is registered with `atexit`, and a browser
is a child process, so it cannot outlive the run that started it.

Fine for generating a document once; not fine per keystroke, so keep this in the build step
and never in a preview path.

The per-page figure was ~0.39s until `js/measure.js` stopped re-sampling every connection
once per text label. If it ever climbs back, measure `measureInPage`'s sections before
blaming the browser — the launch is the honest cost here and the page rarely is.

Deliberately `puppeteer-core` (29 MB, no bundled browser) driving the system Chrome, rather
than `puppeteer` (which downloads its own ~550 MB Chromium). `js/measure.js` holds the
resolution logic and the env overrides.
"""
import atexit
import collections
import contextlib
import json
import os
import select
import shutil
import subprocess
import threading
import time

from .. import parallel

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


# Pages before a batch is worth splitting across several browsers at once instead of measuring
# them one after another on one. Chrome instances are independent — the test runner has relied
# on that from the start.
#
# What it trades has changed twice, and it is worth knowing which question the number is
# answering. It began as "is another batch worth another Chrome", a ratio of the two costs
# above, and was 8 while a page cost ~0.39s — a figure set by `measureInPage` re-sampling every
# connection once per label, which it no longer does. Now that a browser is kept, a shard is
# usually a browser that already exists, so the question is the milder "is this batch long
# enough that measuring it serially is the thing to beat".
#
# Nothing reaches it today. Note placement, which it was raised to 24 for, no longer offers
# more than eight candidates at once (see `place._sweep`), and the clipping gate sends one page
# per diagram. It is left in place because a batch big enough WILL turn up — an explainer with
# thirty diagrams gates them together — and because sharding is now nearly free, so the cost of
# it being a little wrong is small. What is no longer true is that this number governs how many
# browsers a check starts; the pool does.
SHARD_MIN = 24

# Idle browsers, kept for the next batch. Borrowed under a slot and handed back when the batch
# is done, so the pool never holds more than `parallel.WORKERS` and never fewer than the work
# actually needs at once.
_IDLE = []
_IDLE_LOCK = threading.Lock()


class _Browser:
    """One node+Chrome, kept alive across batches, spoken to a line at a time.

    The process is cheap to talk to and expensive to start, which is the whole reason this
    class exists: a full check of eleven diagrams asks for about thirty small batches — a
    note's anchors, a spacing rung, a gate over five drawings — and used to start a Chrome for
    every one of them.

    Reads go through `select` on the raw pipe rather than `readline` on a buffered one,
    because a buffered reader can be holding a complete line that `select` still calls
    unready, and then the timeout below fires on a browser that already answered.
    """

    def __init__(self):
        self.proc = subprocess.Popen(["node", MEASURE_JS], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        self.buffer = b""
        # Drained on a thread of its own, and kept, because node writes here only when
        # something is wrong: an undrained stderr pipe fills and stops the process mid-batch,
        # and the last thing written to it is usually the reason the batch failed.
        self.complaints = collections.deque(maxlen=40)
        threading.Thread(target=self._drain, daemon=True).start()

    def _drain(self):
        # Ends when the process does, or when `close` shuts the pipe under it — the second is
        # ordinary and must not print a stack trace from a daemon thread nobody is watching.
        with contextlib.suppress(OSError, ValueError):
            for line in iter(self.proc.stderr.readline, b""):
                self.complaints.append(line.decode("utf-8", "replace").rstrip())

    def alive(self):
        return self.proc.poll() is None

    def why(self):
        return "; ".join(self.complaints) or "it exited with no message"

    def ask(self, payload, timeout):
        """Send one request, return the parsed reply. Raises `_Gone` if the browser is not
        there to answer — which is a different thing from a batch it could not measure, and
        the only one worth trying again on a fresh browser."""
        try:
            self.proc.stdin.write(payload.encode("utf-8") + b"\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, ValueError, OSError):
            raise _Gone(self.why())
        return json.loads(self._reply(timeout))

    def _reply(self, timeout):
        # The caller's timeout covers the measuring only, where it used to cover a Chrome
        # starting up as well — so a batch has strictly more room than it had, on the same
        # number.
        deadline = time.monotonic() + timeout
        while b"\n" not in self.buffer:
            left = deadline - time.monotonic()
            if left <= 0:
                self.close()
                raise BrowserError(f"the browser did not answer within {timeout}s")
            if not select.select([self.proc.stdout], [], [], left)[0]:
                continue
            chunk = os.read(self.proc.stdout.fileno(), 1 << 16)
            if not chunk:
                raise _Gone(self.why())
            self.buffer += chunk
        line, _, self.buffer = self.buffer.partition(b"\n")
        return line

    def close(self):
        """Closing stdin is how the browser is asked to go; killing is for one that will not.

        Called from `atexit` as well as on failure, so it has to be safe twice and safe on a
        process that is already gone. Every pipe is closed either way: a browser discarded
        because it had already died still holds three file descriptors until someone does.
        """
        with contextlib.suppress(OSError, ValueError):
            self.proc.stdin.close()
        if self.proc.poll() is None:
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                with contextlib.suppress(subprocess.TimeoutExpired):
                    self.proc.wait(timeout=5)
        for pipe in (self.proc.stdout, self.proc.stderr):
            with contextlib.suppress(OSError, ValueError):
                pipe.close()


class _Gone(Exception):
    """The browser is not there to answer — as opposed to unable to measure what was asked."""


@contextlib.contextmanager
def _borrowed():
    """An idle browser, or a new one, returned to the pool unless it misbehaved.

    Borrowed INSIDE a `parallel.slot`, so the pool can only grow to the number of batches
    genuinely in flight at once, and a browser sitting idle holds no slot — which it must not,
    or the pool would deadlock against itself the moment it held `WORKERS` of them.
    """
    with _IDLE_LOCK:
        browser = _IDLE.pop() if _IDLE else None
    if browser is not None and not browser.alive():
        browser.close()
        browser = None
    if browser is None:
        browser = _Browser()
    try:
        yield browser
    except BaseException:
        browser.close()
        raise
    with _IDLE_LOCK:
        _IDLE.append(browser)


def _ask(payload, timeout, doing):
    """One request, on a pooled browser, with one retry on a browser that had gone.

    The retry is what makes reuse safe rather than merely cheap: a pooled browser can be found
    dead for reasons that have nothing to do with the request — Chrome crashed between batches,
    or something reaped it — and a caller must not see that as a failed diagram. A batch the
    browser genuinely could not measure is raised on the first attempt and never retried; the
    two are told apart by `_Gone`.
    """
    problems = requirements()
    if problems:
        raise BrowserError("; ".join(problems))
    try:
        return _once(payload, timeout, doing)
    except _Gone:
        pass                # whatever was in the pool had gone; the retry starts its own
    try:
        return _once(payload, timeout, doing)
    except _Gone as exc:
        raise BrowserError(f"the browser kept dying while {doing} ({exc})") from exc


def _once(payload, timeout, doing):
    """One request on one borrowed browser. The slot is held for the exchange and not for the
    browser's life, or an idle one would hold a place in the concurrency cap for ever."""
    with parallel.slot(), _borrowed() as browser:
        reply = browser.ask(payload, timeout)
    if reply.get("error"):
        raise BrowserError(f"{doing}: {reply['error']}")
    return reply.get("results") or []


def shutdown():
    """Close every idle browser. Registered with `atexit`, so nothing outlives the command.

    A browser is only ever idle-in-the-pool or borrowed by a thread that will close it on
    failure, and by the time the interpreter is exiting there are no borrowers left — the
    thread pools that could have held one are joined by `parallel.each`.
    """
    with _IDLE_LOCK:
        going, _IDLE[:] = list(_IDLE), []
    for browser in going:
        browser.close()


atexit.register(shutdown)


def measure(jobs, viewport=None, shadow=SHADOW_PX, weights=None, timeout=180):
    """Measure a batch of harness pages, in as few browser launches as pay for themselves.

    `jobs` is a list of `{"key": ..., "html": ...}`. Returns a list of measurement dicts in
    the same order, each carrying the `key` back.

    Batched on purpose: launching Chrome costs far more than measuring one more page, and the
    placement search wants to compare 64 candidates at a time. Past `SHARD_MIN` per browser the
    batch is split and the browsers run at once, because at 64 pages the per-page cost is what
    dominates and a single launch measures them one after another. Raises BrowserError rather
    than returning partial results — a half-measured placement search would silently pick a
    worse anchor.
    """
    jobs = list(jobs)
    if not jobs:
        return []
    problems = requirements()
    if problems:
        raise BrowserError("; ".join(problems))

    shards = min(parallel.WORKERS, max(1, len(jobs) // SHARD_MIN))
    if shards > 1:
        # Contiguous slices, so flattening restores the order every caller zips against.
        size = -(-len(jobs) // shards)
        chunks = [jobs[i:i + size] for i in range(0, len(jobs), size)]
        measured = parallel.each(
            lambda chunk: _measure(chunk, viewport, shadow, weights, timeout), chunks)
        return [result for chunk in measured for result in chunk]
    return _measure(jobs, viewport, shadow, weights, timeout)


def _measure(jobs, viewport, shadow, weights, timeout):
    """One batch of pages on one browser. The unit every count in `speed_baseline.json`
    calls an inspection is a page in here, not a call to this."""
    results = _ask(json.dumps({
        "viewport": viewport or VIEWPORT,
        "shadow": shadow,
        "weights": weights if weights is not None else OVERLAP_WEIGHTS,
        "jobs": jobs,
    }), timeout, f"measuring {len(jobs)} page(s)")
    if len(results) != len(jobs):
        raise BrowserError(f"asked for {len(jobs)} measurement(s), got {len(results)}")
    for result in results:
        if result.get("error"):
            raise BrowserError(f"{result.get('key')}: {result['error']}")
    return results


# Device pixels per CSS pixel when rasterising. 2 is what a Retina screen shows anyway, and it
# means the reader can zoom one step into the PNG before it softens. 3 was measurably no better
# on screen and made the files ~2x larger.
RASTER_SCALE = 2


def text_widths(html, timeout=60):
    """Rendered width, in CSS px, of every `[data-w]` element in `html`, in document order.

    The one measurement here that is about a STRING rather than about a drawing. It exists
    because d2 sizes a callout's box with its own font while the box is filled by the host
    page's, so the only program that knows how wide the note really is is the one laying it
    out — see `callout.py`.
    """
    results = _ask(json.dumps({"jobs": [], "widths": [{"key": "w", "html": html}]}),
                   timeout, "measuring text")
    try:
        return results[0]["widths"]
    except (KeyError, IndexError) as exc:
        raise BrowserError(f"could not read the measured widths ({exc}): {results!r:.400}")


def rasterise(html, out, width, height=None, scale=RASTER_SCALE, timeout=180, full=False):
    """Screenshot one page to `out` as a PNG. Returns the path.

    Only the standalone path needs this, and it needs it because macOS cannot render our SVG:
    Quick Look, which is what Preview uses for SVG, ignores the canvas and crops the drawing
    square. Handing the default image viewer a PNG we rendered in the same browser the gates
    measure in is the only way the file the reader opens matches the file they were promised.

    `width`/`height` are CSS px — the diagram's natural size — and the PNG comes out
    `scale`x that in device pixels.

    `full=True` shoots the whole scrolled page instead and `height` is only the viewport it is
    laid out in. That is for a page whose height nobody computed; a diagram's height is known
    and pinning it is the point, so the diagram path never asks for this.
    """
    _ask(json.dumps({"jobs": [], "shots": [
        {"key": "raster", "html": html, "out": str(out), "width": width,
         "height": height, "scale": scale, "fullPage": bool(full)}]}),
        timeout, f"rasterising {out}")
    if not os.path.exists(out):
        raise BrowserError(f"the browser reported success but wrote no file at {out}")
    return out
