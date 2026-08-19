"""Running independent work at the same time, where this repo has any.

Everything expensive here is a SUBPROCESS — a d2 compile, a node measurement, a Chrome launch —
so the GIL is not the constraint and threads are the right tool: a worker holds it only while
marshalling strings around the call it is waiting on. `run_tests.py` has run its files this way
from the start, and this is the same mechanism applied to the renderer's own fan-outs.

**Fan out wherever there is independent work, and cap the SUBPROCESSES rather than the levels.**
This file used to say the opposite — fan out at the innermost place only — on the reasoning that
overlapping a document's figures as well as each figure's own search would put many times more
processes on the machine than it has cores. The reasoning was right and the conclusion was
wrong: the fix is a cap, not a rule about where to fan out. `slot` below is that cap, and it is
what makes nesting safe.

The measurement that settled it: two thirds of a full check of the ten sample diagrams ran with
a SINGLE subprocess in flight — one d2 compile, or one browser reading one page — because a
diagram is mostly not parallel and they were drawn one after another. Overlapping them took that
run from ~42s to ~26s with the cap holding total processes at `WORKERS`.

What must NOT be parallelised, and neither is an oversight:

  * **A ladder rung.** `render._climb_layers` and the edge ladder decide whether to try the next
    rung FROM the result of this one. Rendering them all speculatively would burn the cores on
    answers that are usually thrown away — most figures settle on the first rung.
  * **The browser measurement itself.** `browser.measure` batches pages into as few launches
    as pay for themselves and shards only past `browser.SHARD_MIN`, because starting Chrome
    costs ~25x what measuring one more page in a running one does. Fanning out per page would
    buy a launch for every measurement.
"""
import contextlib
import os
import threading
from concurrent.futures import ThreadPoolExecutor

# How many subprocesses to have in flight. Measured end to end on the reference architecture as
# a standalone file — 64 callout candidates, the biggest fan-out in the repo — on a machine with
# 12 logical cores (8 performance, 4 efficiency): 35.5s at 1 worker, 19.7s at 2, 12.3s at 4,
# 9.3s at 8, 9.4s at 12. The curve is flat past 8 because what remains is not compiling, and the
# browser batch is sharded separately (`browser.SHARD_MIN`). The shape has held across a 4x
# speedup in what each worker does; the absolute seconds have not, so re-measure rather than
# reading them as current.
#
# `cpu_count` rather than a flat 8, so a two-core machine is not oversubscribed, and capped
# because past the core count the compiles only queue.
WORKERS = max(1, min(12, os.cpu_count() or 4))


_SLOTS = threading.BoundedSemaphore(WORKERS)


@contextlib.contextmanager
def slot():
    """Hold one of `WORKERS` places while a SUBPROCESS runs. Wrap the call, nothing wider.

    This is what makes it safe to fan out at more than one level at once. The cap has to sit on
    the subprocess rather than on the task that started it, and the difference is not a detail:
    a task holding a place while it waits for work it spawned will, once enough tasks do the
    same, leave every place held by something waiting for a place. Bounding the subprocess
    cannot deadlock, because a subprocess waits for nothing else here.

    So the rule for callers is narrow — take a place immediately around `subprocess.run`, and
    never around anything that will itself fan out.
    """
    _SLOTS.acquire()
    try:
        yield
    finally:
        _SLOTS.release()


def each(fn, items, workers=None):
    """`[fn(item) for item in items]`, run concurrently, in the order given.

    Order is part of the contract: every caller here zips the results back against the inputs
    that produced them. `ThreadPoolExecutor.map` keeps it, and re-raises whatever a worker
    raised — so a caller that wants a failed item skipped rather than fatal catches it inside
    `fn` and returns a sentinel, exactly as the serial loop did.

    One item runs inline. A pool for a single d2 compile is a thread and a queue to save
    nothing, and the single-callout figure is the common case.
    """
    items = list(items)
    if len(items) < 2:
        return [fn(item) for item in items]
    with ThreadPoolExecutor(max_workers=min(workers or WORKERS, len(items))) as pool:
        return list(pool.map(fn, items))
