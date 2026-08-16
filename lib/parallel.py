"""Running independent work at the same time, where this repo has any.

Everything expensive here is a SUBPROCESS — a d2 compile, a node measurement, a Chrome launch —
so the GIL is not the constraint and threads are the right tool: a worker holds it only while
marshalling strings around the call it is waiting on. `run_tests.py` has run its files this way
from the start, and this is the same mechanism applied to the renderer's own fan-outs.

**Fan out at the innermost place that has independent work, not at the outermost.** The
callout search renders 64 candidates that know nothing about each other, and a document of six
figures renders six that know nothing about each other — doing both at once would put 8 x 8 d2
processes on an 8-core machine and spend the win on contention. The inner one is chosen because
it also helps the single-figure case, which is what `visualize` runs every time.

What must NOT be parallelised, and neither is an oversight:

  * **A ladder rung.** `render._climb_layers` and the edge ladder decide whether to try the next
    rung FROM the result of this one. Rendering them all speculatively would burn the cores on
    answers that are usually thrown away — most figures settle on the first rung.
  * **The browser measurement itself.** `browser.measure` already takes every page in one
    launch, and launching Chrome costs more than measuring another page in it.
"""
import os
from concurrent.futures import ThreadPoolExecutor

# How many subprocesses to have in flight. Measured end to end on the reference architecture as
# a standalone file — 64 callout candidates, the biggest fan-out in the repo — on a machine with
# 12 logical cores (8 performance, 4 efficiency): 56.2s at 1 worker, 33.3s at 4, 30.5s at 8,
# 30.3s at 12. The curve is flat past 8 because what remains is not compiling, and the browser
# batch is sharded separately (`browser.SHARD_MIN`).
#
# `cpu_count` rather than a flat 8, so a two-core machine is not oversubscribed, and capped
# because past the core count the compiles only queue.
WORKERS = max(1, min(12, os.cpu_count() or 4))


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
