#!/usr/bin/env python3
"""How fast the renderer is, measured the same way every time, against a recorded baseline.

    python3 measure_speed.py              # measure, compare to the baseline, write and open it
    python3 measure_speed.py --no-open    # write it only
    python3 measure_speed.py --quick      # one repeat per scenario — a sanity check, not a record
    python3 measure_speed.py --update     # rewrite speed_baseline.json from this run
    python3 measure_speed.py --from /tmp/diagram-speed.json    # rebuild the report, measure nothing

Takes about **4 minutes**, or 6 on a slow tree. `--quick` roughly halves it and must not be used
to update the baseline: it skips the repeat that catches a contaminated run.

## Why this exists rather than a note in a handoff

Every number about this renderer has been wrong at least once, and never because the code was
misread — always because the measurement was taken differently the second time. The rules below
are each a mistake somebody already made.

**Warm up, and throw the first run away.** The first render in a fresh process costs ~25s more
than the second. A cold before against a warm after once read as a 34% win that was really 41%,
measured the other way round it would have read as a loss.

**Best of N, never the mean.** The machine is shared with whatever else is running. The minimum
is the sample least contaminated by other work; a mean folds the contamination in and a single
outlier moves it.

**Compare back to back, alternating.** If you are testing a change, run old, new, old, new — not
all the olds then all the news. The machine drifts as it warms and a block design charges the
whole drift to whichever side ran second.

**Trust the counts, not the seconds.** The three counts are exact and identical run to run; the
seconds swing. A change in a count is always real and always worth explaining. A change in
seconds under ~10% is noise, which is why `SAME_WITHIN` exists.

The counts keep their internal names in this file's data — `compiles`, `launches`, `pages` — and
are reported as **layout candidates**, **browser starts** and **inspections**. `CONTEXT.md` is
what those mean; the mapping is deliberate and the report never shows the internal ones.

**Count launches, not calls.** One `browser.measure()` call becomes several node+Chrome processes
once a batch passes `browser.SHARD_MIN`, so this instruments `browser._measure` — the private one.
Counting the public call is what once made a figure read 3 processes where there were 10.

**Do not run anything else while it measures.** Including another copy of this.

**These numbers are specific to one machine.** The baseline records what it was taken on and this
warns when they differ. It does not refuse to run — a relative comparison on a different machine
is still useful — but `--update` then needs `--force`, because overwriting a baseline with
numbers from another machine destroys the only thing it is for.

**Adding a field is free; renaming one is not.** Every committed baseline is a past measurement,
so anything reading them across time gets `None` where a renamed field used to be, with nothing
to say so. `idle_share` became `core_usage` before any baseline had been committed, which is the
only reason that rename cost nothing. From here a rename means migrating the recorded entries or
reading both spellings.

## What it measures, and why each one is here

The four jobs are the shapes the engine actually runs in, and they behave differently enough
that a single figure hides regressions:

  * **one diagram** is the `/visualize` path and the only one a person waits on;
  * **all 10 sample diagrams** is what a full check of a change costs, and the number any
    renderer work is judged against;
  * **layout only** isolates the arrangement search from note placement and the legibility
    checks, which is where a regression in that search would show first;
  * **legibility checks only** is the batched case — many inspections, one browser.

Plus the two costs that explain all of the above and are worth watching on their own: what it
costs to START a browser, and what one more inspection costs in one already running. The ratio
of those two IS `browser.SHARD_MIN`, so when either moves, that constant is wrong.

`core_usage` is the headroom number: how much of the machine a job kept busy. It is not a speed,
it is where the next win is — and it is reported as usage rather than as idleness so that high
is unambiguously good.
"""
import argparse
import collections
import contextlib
import datetime
import html
import importlib.util
import json
import os
import platform
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from lib import parallel                                      # noqa: E402
from lib.diagram import browser, figure, render               # noqa: E402
from lib.diagram.examples import REFERENCE                    # noqa: E402
from lib.diagram.examples_repo import REPO                    # noqa: E402
from lib.diagram.gates import clipping                        # noqa: E402

BASELINE = os.path.join(HERE, "speed_baseline.json")
OUT = "/tmp/diagram-speed.html"
# Every run's raw numbers land beside the report, so the report can be rebuilt — or the run
# recorded as a baseline — without paying the four minutes again. `--from` is what reads it.
RUN_OUT = "/tmp/diagram-speed.json"

# Seconds swing run to run even warm and even best-of-N, so a difference smaller than this is
# reported as unchanged rather than as a win nobody can reproduce. Counts get no such tolerance:
# they are exact, and any movement in one is real.
SAME_WITHIN = 0.10

# How many measured runs per scenario, after the warm-up run that is always discarded. Two is
# enough for the expensive ones because the spread between warm repeats is well under
# `SAME_WITHIN`; the cheap ones can afford more and benefit, since a launch is short enough for
# one scheduling hiccup to dominate it.
REPEATS = 3
REPEATS_EXPENSIVE = 2


# --------------------------------------------------------------------------- machine


def machine():
    """What the numbers below are true of. Compared, not just recorded — see `same_machine`."""
    return {
        "system": platform.system(),
        "arch": platform.machine(),
        "cores": os.cpu_count(),
        "python": platform.python_version(),
        "node": _version(["node", "--version"]),
        "d2": render.d2_version(),
        "workers": parallel.WORKERS,
    }


def _version(command):
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return "?"
    return (done.stdout or done.returncode and done.stderr or "").strip() or "?"


# The fields that make a number incomparable. `python` is deliberately NOT among them: the
# renderer's cost is d2, Chrome and core count, and a patch release of Python has never moved it.
DECIDING = ("system", "arch", "cores", "node", "d2", "workers")


def same_machine(now, before):
    """The fields that differ between two machine records, empty when they agree."""
    return [field for field in DECIDING if (before or {}).get(field) != now.get(field)]


# --------------------------------------------------------------------------- probe


class Probe:
    """Counts every subprocess a block of work starts, and times how long each one ran.

    Counting and timing come from two different places on purpose.

    The COUNTS wrap the four entry points that cost real time — one d2 compile, and the three
    ways a node+Chrome gets started. `browser._measure` rather than `browser.measure`: the
    public one shards into several processes and counting it undercounts.

    The TIMELINE comes from `parallel.slot`, which is held for exactly as long as a subprocess
    is running. Timing the entry points instead would start the clock when a call is made
    rather than when it gets to run, and every call queued behind the concurrency cap would be
    counted as though it were working — which reads as a machine busier than it is, on a run
    that is in fact waiting. That distinction did not exist until diagrams began overlapping;
    before that nothing ever queued.
    """

    def __init__(self):
        self.counts = collections.Counter()
        self.spans = []
        self._lock = threading.Lock()
        self._patched = []

    def _wrap(self, module, name, kind, pages_of):
        original = getattr(module, name)

        def wrapper(*args, **kwargs):
            try:
                return original(*args, **kwargs)
            finally:
                with self._lock:
                    self.counts[kind] += 1
                    self.counts["pages"] += pages_of(args)
        self._patched.append((module, name, original))
        setattr(module, name, wrapper)

    def _time_slots(self):
        original = parallel.slot

        @contextlib.contextmanager
        def timed():
            with original():
                start = time.time()
                try:
                    yield
                finally:
                    with self._lock:
                        self.spans.append((start, time.time()))
        self._patched.append((parallel, "slot", original))
        parallel.slot = timed

    def __enter__(self):
        self._wrap(render, "compile_source", "compiles", lambda args: 0)
        self._wrap(browser, "_measure", "launches", lambda args: len(args[0]))
        self._wrap(browser, "text_widths", "launches", lambda args: 1)
        self._wrap(browser, "rasterise", "launches", lambda args: 1)
        self._time_slots()
        return self

    def __exit__(self, *exc):
        for module, name, original in reversed(self._patched):
            setattr(module, name, original)
        self._patched.clear()
        return False

    def core_usage(self, wall_start, wall_end, cores=None):
        """How much of the machine the job used: mean things-in-flight over cores available.

        Reported this way round on purpose. The same fact stated as "idle" needs the reader to
        work out which end is good, and the answer is not even always the same end — so it is
        stated as a fraction of the machine used, where 100% is everything busy and low is
        work left on the table.

        Counting subprocesses rather than sampling real CPU: everything expensive here is a
        separate process, so how many are in flight IS the utilisation to within the accuracy
        anyone would act on. Clamped at 1.0, since more processes than cores does not mean more
        than a full machine.
        """
        window = wall_end - wall_start
        cores = cores or os.cpu_count() or 1
        if window <= 0:
            return 0.0
        edges = sorted([(s, 1) for s, _e in self.spans] + [(e, -1) for _s, e in self.spans])
        area, depth, previous = 0.0, 0, wall_start
        for at, delta in edges:
            area += depth * (min(at, wall_end) - previous)
            depth += delta
            previous = at
        return max(0.0, min(1.0, area / (window * cores)))


# --------------------------------------------------------------------------- scenarios


def ten_drawings():
    for group, specs in (("reference", REFERENCE), ("repo", REPO)):
        for name, spec in specs.items():
            render.render(spec, name=f"{group}-{name}")


def both_corpora():
    for specs in (REFERENCE, REPO):
        figure.draw(specs, target="file")


def one_figure():
    figure.draw({"arch": REFERENCE["arch"]}, target="file")


def clipping_gate(_svgs=[]):                                  # noqa: B006  (deliberate cache)
    """The batched-browser shape: five figures measured in one launch.

    The drawings are built once and reused, because this scenario is about the GATE and folding
    ten d2 compiles into it would measure something else.
    """
    if not _svgs:
        _svgs.extend([{name: render.render(spec, name=f"r-{name}")
                       for name, spec in REFERENCE.items()}])
    clipping.check_many(_svgs[0], theme="light", standalone=False)


# Named and described in CONTEXT.md's words, because these labels are read by a person and
# nothing else. "figure", "corpus", "callout" and "gate" are what the code calls these and are
# exactly what must not appear here.
# Two things every job must carry, because a number without them is not actionable:
#
#   `what`  reads on its own. Never "the same as above" or "everything above" — a reader looks
#           first at whatever caught their eye, and a row that only makes sense after reading
#           the one before it sends them hunting for the antecedent.
#   `usage` says whether this job's share of the machine is a problem or simply the shape of
#           the work. A low percentage is not automatically bad and a high one is not always
#           reachable, so each job states which it is. Facts about the job, not about a run.
#
# `opportunity` is optional and describes the JOB, not any single figure of it — a marker in the
# row saying there is a win here worth reading about. Kept separate from `usage` so that a future
# opportunity having nothing to do with cores still has somewhere to live.
SCENARIOS = [
    {"key": "one_figure", "label": "One diagram",
     "what": "one picture from scratch: choose an arrangement, position the notes, check it "
             "is legible — what /visualize does",
     "usage": "Good. The candidate positions for the notes are measured across several "
              "browsers at once, which is the bulk of this job. The rest is the arrangement "
              "search, which has to run one step at a time.",
     "run": one_figure, "repeats": REPEATS},
    {"key": "corpus", "label": "All 10 sample diagrams",
     "what": "all 10 sample diagrams from scratch, notes and legibility checks included — "
             "what a full check of a change costs",
     "usage": "Reasonable. The 10 diagrams are drawn at the same time rather than one after "
              "another, which is what fills the machine; a single diagram on its own leaves "
              "most of it idle, and there are only so many diagrams to overlap.",
     "run": both_corpora, "repeats": REPEATS_EXPENSIVE},
    {"key": "ten_drawings", "label": "Layout only",
     "what": "all 10 sample diagrams arranged, but with no notes positioned and no legibility "
             "checks run — the arrangement search on its own",
     "usage": "Lower than a full check, and largely unavoidable: without note positioning "
              "there is little inside a diagram that can run alongside anything else, so all "
              "the overlap there is comes from drawing the ten together.",
     "run": ten_drawings, "repeats": REPEATS_EXPENSIVE},
    {"key": "clipping_gate", "label": "Legibility checks only",
     "what": "5 already-finished diagrams checked for text that is cut off, hidden or too "
             "small to read",
     "usage": "Low and entirely fine. This is a single browser checking all 5 diagrams in one "
              "batch; there is nothing to run beside it, and at well under a second there is "
              "nothing worth splitting up.",
     "run": clipping_gate, "repeats": REPEATS},
]


def measure_scenario(scenario, quick=False):
    """Best-of-N seconds and the exact counts, after a discarded warm-up run.

    The counts are asserted stable across the repeats. If they are not, the renderer is deciding
    something differently on a second identical call and every seconds figure here is meaningless
    — so it is reported rather than averaged away.
    """
    scenario["run"]()                                          # warm up, discarded
    repeats = 1 if quick else scenario["repeats"]
    best, counts, share, seen = None, {}, None, set()
    for _ in range(repeats):
        with Probe() as probe:
            start = time.time()
            scenario["run"]()
            wall = time.time() - start
        seen.add((probe.counts["compiles"], probe.counts["launches"], probe.counts["pages"]))
        if best is None or wall < best:
            best, counts = wall, dict(probe.counts)
            # Taken from the same repeat as the winning time, never averaged: it describes how
            # THAT run used the machine, and blending it with a slower run's would describe
            # neither.
            share = probe.core_usage(start, start + wall)
    result = {"seconds": round(best, 2), "compiles": counts.get("compiles", 0),
              "launches": counts.get("launches", 0), "pages": counts.get("pages", 0),
              "core_usage": round(share, 3)}
    if len(seen) > 1:
        result["unstable"] = sorted(str(item) for item in seen)
    return result


def launch_floor(repeats=5):
    """Seconds for a node+Chrome that measures nothing — the cost of merely starting one."""
    payload = json.dumps({"jobs": []})
    best = None
    for _ in range(repeats):
        start = time.time()
        subprocess.run(["node", browser.MEASURE_JS], input=payload,
                       capture_output=True, text=True)
        wall = time.time() - start
        best = wall if best is None else min(best, wall)
    return best


def page_cost(big=32):
    """Seconds to measure one MORE page in a browser that is already running.

    Taken as a slope between one page and `big` rather than as `total / pages`, which would fold
    the launch into it and report a per-page cost several times the real one.
    """
    svg = render.render(REFERENCE["arch"], name="speed-arch")
    page = render.harness_html(svg, theme="light")

    def run(count):
        payload = json.dumps({"viewport": browser.VIEWPORT, "shadow": browser.SHADOW_PX,
                              "weights": browser.OVERLAP_WEIGHTS,
                              "jobs": [{"key": f"k{i}", "html": page} for i in range(count)]})
        best = None
        for _ in range(2):
            start = time.time()
            done = subprocess.run(["node", browser.MEASURE_JS], input=payload,
                                  capture_output=True, text=True)
            if done.returncode != 0:
                raise SystemExit(f"the browser would not measure {count} page(s): "
                                 f"{done.stderr[:300]}")
            best = min(best, time.time() - start) if best else time.time() - start
        return best
    one, many = run(1), run(big)
    return (many - one) / (big - 1)


def measure(quick=False, summary=None, why=None):
    """Every job plus the two underlying costs, as one recordable run."""
    results = {}
    for scenario in SCENARIOS:
        print(f"  {scenario['label']}...", flush=True)
        results[scenario["key"]] = measure_scenario(scenario, quick=quick)
    print("  browser start and inspection cost...", flush=True)
    floor, per_inspection = launch_floor(), page_cost()
    results["launch_floor"] = {"seconds": round(floor, 3)}
    results["page_cost"] = {"seconds": round(per_inspection, 4)}
    results["pages_per_launch"] = {
        "value": round(floor / per_inspection, 1) if per_inspection else 0}
    return {
        "recorded": datetime.date.today().isoformat(),
        "machine": machine(),
        "scenarios": results,
        # Whoever ran this says what they changed. The report cannot work out a cause from
        # timings, and a wrong guess about one is worse than none — see `build`.
        "summary": summary,
        "why": why,
    }


# --------------------------------------------------------------------------- comparing


def verdict(now, before, lower_is_better=True):
    """`(word, fraction)` for one number against its baseline.

    `fraction` is signed change, so the report can size a bar without recomputing it.
    """
    if before in (None, 0) or now is None:
        return "new", 0.0
    change = (now - before) / before
    if abs(change) < SAME_WITHIN:
        return "same", change
    faster = change < 0 if lower_is_better else change > 0
    return ("better" if faster else "worse"), change

def rows(current, baseline):
    """One row per job, current against baseline, ready for the report."""
    was = (baseline or {}).get("scenarios", {})
    out = []
    for scenario in SCENARIOS:
        key = scenario["key"]
        now, before = current["scenarios"].get(key, {}), was.get(key, {})
        word, change = verdict(now.get("seconds"), before.get("seconds"))
        out.append({
            "key": key, "label": scenario["label"], "what": scenario["what"],
            "now": now.get("seconds"), "before": before.get("seconds"),
            "verdict": word, "change": change, "usage_note": scenario["usage"],
            "opportunity": scenario.get("opportunity"),
            "usage": now.get("core_usage"), "usage_before": before.get("core_usage"),
            "counts": {name: (now.get(name), before.get(name))
                       for name in ("compiles", "launches", "pages")},
            "unstable": now.get("unstable"),
        })
    return out


def costs(current, baseline):
    """The two machine costs that explain the jobs above. Shown only in the closing section."""
    was = (baseline or {}).get("scenarios", {})
    out = []
    for key, label, what, digits in (
            ("launch_floor", "Starting a browser",
             "before it is asked to do anything at all", 2),
            ("page_cost", "One inspection",
             "reading back one drawing in a browser that is already open", 3)):
        now, before = current["scenarios"].get(key, {}), was.get(key, {})
        word, change = verdict(now.get("seconds"), before.get("seconds"))
        out.append({"key": key, "label": label, "what": what, "digits": digits,
                    "now": now.get("seconds"), "before": before.get("seconds"),
                    "verdict": word, "change": change})
    return out


# --------------------------------------------------------------------------- report


PAGE_CSS = """
/* A table of contents for three short sections is noise. */
.toc{display:none}
body{font-size:16px}
.lead{font-family:system-ui,-apple-system,sans-serif;font-size:.82rem;line-height:1.65;
  color:var(--fg);margin:1rem 0 1.4rem}
.lead dl{display:grid;grid-template-columns:auto 1fr;gap:.28rem .8rem;margin:.5rem 0 0}
.lead dt{font-weight:650;white-space:nowrap}
.lead dd{margin:0;color:var(--muted)}
.job-head{align-items:flex-start}
.demo{border:2px dashed var(--diff-del-fg);color:var(--diff-del-fg);border-radius:9px;
  padding:.6rem .9rem;font:700 .9rem system-ui,sans-serif;margin:1rem 0;text-align:center}
/* One block per job: heading line, description, then before and after as two bars of the
   same scale so their lengths can be compared directly. */
.jobs{font-family:system-ui,-apple-system,sans-serif;margin:1.2rem 0 0}
.job{padding:.85rem 0;border-top:1px solid var(--border)}
.job:first-child{border-top:0}
.job-head{display:flex;align-items:baseline;justify-content:space-between;gap:1rem}
.job-name{font-size:.95rem;font-weight:650}
.job-what{font-size:.74rem;color:var(--muted);line-height:1.45;margin:.5rem 0 .55rem}
.pair{display:grid;grid-template-columns:3.2rem 1fr 4.2rem;gap:.6rem;align-items:center;
  margin:.22rem 0}
.pair .k{font-size:.68rem;color:var(--muted);text-align:right}
.pair .v{font-size:.75rem;font-variant-numeric:tabular-nums;color:var(--muted)}
.pair.after .v{color:var(--fg);font-weight:600}
.track{position:relative;height:.72rem;background:var(--th-bg);border-radius:4px}
.track i{position:absolute;left:0;top:0;height:100%;border-radius:4px;display:block}
/* The before bar has to be plainly visible, not a hint: it is half the comparison. */
.track .b-before{background:var(--muted);opacity:.55}
.track .b-after{background:var(--muted)}
.track .b-after.better{background:var(--diff-add-fg)}
.track .b-after.worse{background:var(--diff-del-fg)}
.chip{display:inline-block;padding:.05rem .4rem;border-radius:4px;font-size:.7rem;
  font-weight:700;margin-left:.4rem;vertical-align:middle}
.chip.better{background:var(--diff-add-bg);color:var(--diff-add-fg)}
.chip.worse{background:var(--diff-del-bg);color:var(--diff-del-fg)}
.chip.same,.chip.new,.chip.moved{background:var(--th-bg);color:var(--muted)}
/* Time and core usage share one right-hand group, usage first, so the two numbers describing
   a job read together instead of one being a stray footnote under the bars. */
/* A GRID, not a flex row. Right-aligning a flex group lines up its right edge and lets every
   number inside float to wherever the chip beside it happens to end, which is what put four
   different figures at four different offsets.
   Numbers sit RIGHT in their column and changes sit LEFT in theirs. Right-aligning both would
   reopen the gap from the other side: a narrow change leaves its slack between itself and the
   number it belongs to, which is exactly the space that read as too wide. */
.job-figures{display:grid;align-items:baseline;white-space:nowrap;
  grid-template-columns:auto 2.5rem 5.4rem 1.4rem 3.4rem 4.4rem;column-gap:.3rem}
.job-figures>*{justify-self:start}
.uval,.jf-now{justify-self:end}
.uval{font-size:.78rem;font-weight:650;color:var(--fg);font-variant-numeric:tabular-nums}
.jf-now{font-size:1.05rem;font-weight:700;font-variant-numeric:tabular-nums}
/* The dotted rule marks only what is hoverable, and is held off the description underneath. */
.usage{position:relative;font-size:.72rem;color:var(--muted);cursor:help;
  border-bottom:1px dotted var(--muted);padding-bottom:.12rem}
/* Loud on purpose: the sentence it points at is the most useful one on the page and it is
   hidden in a tooltip. */
.flag{position:relative;margin-left:.5rem;font:700 .58rem system-ui,sans-serif;
  letter-spacing:.05em;text-transform:uppercase;color:var(--accent);
  border:1px solid var(--accent);border-radius:4px;padding:.08rem .35rem;
  vertical-align:middle;cursor:help}
/* Numbers right-aligned in a fixed box, changes left-aligned in another, so a column of them
   reads straight down instead of stepping with the width of each figure. */
/* Left-aligned like every other column, with the number in a fixed right-aligned box so the
   digits still line up down the page. The change beside it needs no reserved width once the
   block is left-anchored — the number's position no longer depends on what follows it, which
   is the only thing a fixed slot was buying. */
td .pair{display:inline-grid;grid-template-columns:2.4rem auto;column-gap:.45rem;
  align-items:baseline}
td .n{text-align:right;font-variant-numeric:tabular-nums}
td .d{text-align:left}
/* A real tooltip rather than the `title` attribute: that one waits about a second, cannot be
   styled, and never appeared at all for the person this was built for. */
.usage::after,.flag::after{content:attr(data-tip);position:absolute;bottom:calc(100% + .55rem);right:0;
  width:21rem;white-space:normal;text-align:left;background:var(--surface);
  border:1px solid var(--border);border-radius:8px;padding:.55rem .7rem;
  font:.72rem/1.55 system-ui,sans-serif;color:var(--fg);opacity:0;visibility:hidden;
  transition:opacity .12s;z-index:9;box-shadow:0 8px 22px rgba(0,0,0,.4)}
.usage:hover::after,.flag:hover::after{opacity:1;visibility:visible}
.flag::after{left:0;right:auto;text-transform:none;letter-spacing:normal;font-weight:400}
.opening{font-family:system-ui,-apple-system,sans-serif;font-size:.92rem;line-height:1.65}
.hl{font-weight:700}
.hl.better{color:var(--diff-add-fg)}
.hl.worse{color:var(--diff-del-fg)}
/* Definitions are for looking up, not for reading, so they stay shut until wanted. */
.terms{margin:1.3rem 0 0;border:1px solid var(--border);border-radius:9px;
  background:var(--surface);font-family:system-ui,-apple-system,sans-serif}
.terms>summary{cursor:pointer;padding:.5rem .8rem;font-size:.76rem;color:var(--muted);
  list-style:none}
.terms>summary::-webkit-details-marker{display:none}
.terms>summary::before{content:"";display:inline-block;width:.4rem;height:.4rem;
  border-right:1.5px solid var(--muted);border-bottom:1.5px solid var(--muted);
  transform:rotate(-45deg);margin:0 .6rem .1rem .1rem;vertical-align:middle;
  transition:transform .15s ease}
.terms[open]>summary::before{transform:rotate(45deg)}
.terms>summary:hover{color:var(--fg)}
.terms>summary:hover::before{border-color:var(--fg)}
.terms dl,.terms p,.terms ul{font-size:.78rem;line-height:1.6;margin:.1rem .9rem .7rem}
.terms dl{display:grid;grid-template-columns:auto 1fr;gap:.3rem .8rem}
.terms dt{font-weight:650;white-space:nowrap}
.terms dd{margin:0;color:var(--muted)}
.terms ul{padding-left:1.1rem;color:var(--muted)}
.terms li{margin:.25rem 0}
.terms p{color:var(--muted)}
/* The two machine costs, in the closing section. */
.costs{display:grid;grid-template-columns:repeat(auto-fit,minmax(13rem,1fr));gap:.7rem;
  margin:.9rem 0;font-family:system-ui,-apple-system,sans-serif}
.cost{border:1px solid var(--border);border-radius:9px;background:var(--surface);
  padding:.65rem .8rem}
.cost .k{display:block;font-size:.78rem;font-weight:650}
.cost .w{display:block;font-size:.68rem;color:var(--muted);line-height:1.4;margin:.1rem 0 .3rem}
.cost .v{font-size:1.15rem;font-weight:700;font-variant-numeric:tabular-nums}
.cost .was{font-size:.68rem;color:var(--muted);font-variant-numeric:tabular-nums}
.note{font-family:system-ui,-apple-system,sans-serif;font-size:.82rem;line-height:1.6;
  border-left:3px solid var(--accent);padding:.1rem 0 .1rem .8rem;margin:1rem 0}
.meta{font:.7rem/1.6 system-ui,sans-serif;color:var(--muted);margin-top:1.4rem}
.meta b{color:var(--fg);font-weight:600}
.warn{border:1px solid var(--diff-del-fg);background:var(--diff-del-bg);color:var(--diff-del-fg);
  border-radius:8px;padding:.55rem .8rem;font:.76rem/1.5 system-ui,sans-serif;margin:1rem 0}
table{font-size:.8rem}
"""


def number(value, unit="s", digits=1):
    if value is None:
        return "—"
    return f"{value:.{digits}f}{unit}"


def chip(word, change):
    if word == "new":
        return '<span class="chip new">no baseline</span>'
    if word == "same":
        return '<span class="chip same">~same</span>'
    return f'<span class="chip {word}">{change * 100:+.0f}%</span>'


def arrow(now, before, lower_is_better=None, suffix=""):
    """`↓ was N` for a number that moved, coloured only where a direction means something.

    `lower_is_better=None` marks a number that is neither good nor bad when it moves — how
    many drawings a job produced is a statement about how much work was asked for, and
    painting it green or red would assert something the report has not established.
    """
    if before is None or now is None or now == before:
        return ""
    mark = "↓" if now < before else "↑"
    if lower_is_better is None:
        klass = "moved"
    else:
        improved = (now < before) if lower_is_better else (now > before)
        klass = "better" if improved else "worse"
    shown = f"{before}{suffix}"
    return f'<span class="chip {klass}">{mark} was {shown}</span>'


def headline(reported):
    """One sentence over the whole run, derived from the jobs so it cannot contradict them.

    Deliberately not the author's own summary: that says what was CHANGED, and this says what
    the numbers did. When those two disagree the reader needs to see both.
    """
    counted = collections.Counter(row["verdict"] for row in reported)
    better, worse = counted["better"], counted["worse"]
    total = len(reported)
    full = next((r for r in reported if r["key"] == "corpus"), None)
    if not (better or worse):
        return "same", "None of the four jobs moved — every one is within the noise of the " \
                       "last measurement."
    # Always says WHAT was counted. "4 faster" on its own is a number with no noun.
    if better == total:
        parts = [f"all {total} jobs got faster"]
    elif worse == total:
        parts = [f"all {total} jobs got slower"]
    else:
        parts = []
        if better:
            parts.append(f"{better} of the {total} jobs got faster")
        if worse:
            parts.append(f"{worse} got slower")
        if counted["same"]:
            parts.append(f"{counted['same']} did not move")
    # The full run is the number anybody actually feels, so it both leads the sentence and
    # decides the colour. A tally would paint this green on "2 faster, 1 slower" even when the
    # one that got slower is the whole job and the two that got faster are parts of it.
    tail = ""
    if full and full["before"] and full["verdict"] != "new":
        # The size of the change is the one phrase worth finding without reading, so it is
        # emphasised rather than the whole sentence being coloured — a fully coloured paragraph
        # sets the same alarm for "roughly the same" as for a real regression.
        if full["verdict"] == "same":
            moved = "almost exactly as long as before"
        else:
            direction = "quicker" if full["change"] < 0 else "slower"
            moved = (f'<strong class="hl {full["verdict"]}">'
                     f'{abs(full["change"]) * 100:.0f}% {direction}</strong> — '
                     f'<strong>{number(full["before"])}</strong> '
                     f'{"down" if full["change"] < 0 else "up"} to '
                     f'<strong class="hl {full["verdict"]}">{number(full["now"])}</strong>')
        tail = f" A full check of all 10 sample diagrams now takes {moved}."
        word = full["verdict"]
    else:
        word = "worse" if worse > better else ("better" if better else "same")
    return word, f"{html.escape(', '.join(parts))}.{tail}"


# Terms sit UNDER the thing they explain, matching the counts section. Above the charts they
# were read first and meant nothing yet; below, they are there when a number raises a question.
JOB_TERMS = """<details class="terms"><summary>What these terms mean</summary><dl>
<dt>sample sets</dt><dd>two fixed collections of 5 diagrams each (architecture, sequence,
ER, class, state) — 10 in total, used to test every change</dd>
<dt>core usage</dt><dd>how much of the machine the job kept busy: the average number of things
running at once, as a share of this machine's cores. 100% would be every core working for the
whole job. Low means there is speed still available — though not always reachable, so each job
says which; hover the figure for that. A drop is <em>not</em> a fault on its own: making the
part that already ran in parallel cheaper leaves less of it to spread, which lowers the share
while the job gets faster.</dd>
</dl></details>"""


def jobs_block(reported):
    """Every job as a heading, a description, and two bars on one shared scale."""
    out = []
    for row in reported:
        now, before = row["now"], row["before"]
        top = max([v for v in (now, before) if v is not None] or [1]) or 1
        bars = []
        for kind, value, klass in (("before", before, "b-before"),
                                   ("after", now, f"b-after {row['verdict']}")):
            width = 100 * (value or 0) / top
            bars.append(
                f'<div class="pair {kind}"><span class="k">{kind}</span>'
                f'<span class="track"><i class="{klass}" style="width:{width:.1f}%"></i></span>'
                f'<span class="v">{number(value)}</span></div>')
        # Both default to empty: a run with no core-usage figure still has a name and a time,
        # and the marker is attached to the name rather than to the figure it describes.
        usage = flag = ""
        if row["usage"] is not None:
            before_pct = (round(row["usage_before"] * 100)
                          if row["usage_before"] is not None else None)
            # Sits with the time rather than under the bars, and carries its verdict as a
            # tooltip: the sentence explaining a percentage is longer than the row it belongs
            # to, and printed in full it competed with the number it was there to qualify.
            # Judged only where the direction is unambiguous. MORE of the machine used is
            # always an improvement. LESS of it is only a fault when the job did not get
            # faster for it — usage falls whenever the part that already ran in parallel gets
            # cheaper, and painting that red called a 28% speed-up a regression.
            now_pct = round(row["usage"] * 100)
            # Held to the same noise band as the seconds it is derived from. Compared exactly,
            # a two-point wobble between two runs of identical code read as a regression, on a
            # figure that cannot be more stable than the timings underneath it.
            usage_word, _ = verdict(now_pct, before_pct, lower_is_better=False)
            if usage_word in ("new", "same"):
                verdict_for_usage, before_pct = None, None
            elif now_pct > before_pct:
                verdict_for_usage = False          # higher is better
            else:
                verdict_for_usage = None if row["verdict"] == "better" else False
            moved = arrow(now_pct, before_pct, lower_is_better=verdict_for_usage, suffix="%")
            usage = (f'<span class="usage" data-tip="{html.escape(row["usage_note"])}">'
                     f'core usage</span><span class="uval">{now_pct}%</span>'
                     f'<span class="umove">{moved}</span>')
        # An opportunity belongs to the JOB, not to any one figure of it — core usage is merely
        # where today's happens to show. It rides with the name for that reason, and because
        # dropped in beside a percentage it knocked the figures out of their column.
        if row["opportunity"]:
            flag = (f'<span class="flag" data-tip="{html.escape(row["opportunity"])}">'
                    f'opportunity</span>')
        unstable = ('<div class="warn">The counts differed between repeats — something is '
                    'being decided differently on a second identical call, so the timings on '
                    'this row cannot be trusted.</div>') if row["unstable"] else ""
        out.append(
            f'<div class="job"><div class="job-head">'
            f'<span class="job-name">{html.escape(row["label"])}{flag}</span>'
            f'<span class="job-figures">{usage or "<span></span><span></span><span></span>"}'
            f'<span class="jf-gap"></span>'
            f'<span class="jf-now">{number(now)}</span>'
            f'<span class="jf-chip">{chip(row["verdict"], row["change"])}</span></span>'
            f'</div><div class="job-what">{html.escape(row["what"])}</div>'
            f'{"".join(bars)}{unstable}</div>')
    return f'<div class="jobs">{"".join(out)}</div>{JOB_TERMS}'


def counts_block(reported):
    """How much work each job did. Whole numbers, so a difference is never noise."""
    # The column NAME carries the browser rather than a spanning header above it: a header that
    # groups two columns has to be read before either one means anything, where a longer name is
    # read at the moment the number is.
    head = ('<tr><th>job</th><th>layout candidates</th>'
            "<th>inspections via browser</th><th>browser starts</th></tr>")
    body = []
    for row in reported:
        cells = []
        # Only browser starts have a direction: fewer is always better, because each costs the
        # same fixed amount however little it is then asked to do. How many candidates were
        # produced or inspected says how much work was asked for, and is neither good nor bad.
        for name, lower_better in (("compiles", None), ("pages", None), ("launches", True)):
            now, before = row["counts"][name]
            # Number and change get fixed slots so both line up down the column; without them
            # the chip pushes each number to wherever its own digits end.
            cells.append(f'<td><span class="pair"><span class="n">{now}</span>'
                         f'<span class="d">{arrow(now, before, lower_is_better=lower_better)}'
                         f'</span></span></td>')
        body.append(f'<tr><td>{html.escape(row["label"])}</td>{"".join(cells)}</tr>')
    return (
        '<p class="lead">How much work each job did, counted rather than timed. Run the same '
        'code twice and these come out identical, so when one of them moves the engine really '
        'is doing a different amount of work — which is the difference between "we made it '
        'faster" and "we made it do less".</p>'
        f'<table><thead>{head}</thead><tbody>{"".join(body)}</tbody></table>'
        '<details class="terms"><summary>What these terms mean</summary><dl>'
        '<dt>layout candidates</dt><dd>how many differently arranged versions of a picture were '
        'produced while searching for the best one. Most are discarded, and none of them opens '
        'a browser</dd>'
        '<dt>inspections via browser</dt><dd>how many times a finished drawing was opened and '
        'read back, to find where its text and boxes actually landed. This needs a real browser '
        '— nothing else can say how wide a word came out, or whether a shadow is cut off at the '
        'edge — which is what makes these the expensive column</dd>'
        '<dt>browser starts</dt><dd>how many separate browsers those inspections were spread '
        'across. Each one costs about the same however little it is asked to do, so fewer is '
        'always better</dd>'
        '</dl>'
        '<p>Candidates and inspections are not shares of one another. Every candidate note '
        'position is both laid out and inspected; arrangements of the diagram are compared '
        'without opening them, so many are produced and only the chosen one is inspected; and '
        'the legibility checks lay out nothing at all, adding inspections with no candidates '
        'behind them. Either total can be the larger.</p>'
        '</details>')


def why_block(current, baseline, reported, machine_costs):
    """How the change was made, what the counts prove, and what is still on the table.

    Deliberately does NOT repeat `summary`, which already said WHAT changed at the top of the
    report. This section is only the mechanism and the evidence.
    """
    parts = []
    told = (current.get("why") or "").strip()
    if told:
        parts.append(f'<div class="note">{html.escape(told)}</div>')

    # What the counts prove, independent of anything the author claimed.
    grew = [r for r in reported
            if r["counts"]["compiles"][1] is not None
            and r["counts"]["compiles"][0] != r["counts"]["compiles"][1]]
    if grew:
        parts.append(
            '<p class="lead">The engine is doing a <b>different amount of work</b> than when '
            'the baseline was recorded — the layout candidates above changed. A slower time is '
            'expected when the work grew, and is not a regression.</p>')
    else:
        parts.append(
            '<p class="lead">The engine did <b>exactly the same work</b> as when the baseline '
            'was recorded: same diagrams, same layout candidates, same inspections. So every '
            'change in time above is a change in how fast that work runs, not in how much '
            'there is.</p>')

    parts.append('<div class="costs">' + "".join(
        f'<div class="cost"><span class="k">{html.escape(cost["label"])}</span>'
        f'<span class="w">{html.escape(cost["what"])}</span>'
        f'<span class="v">{number(cost["now"], "s", cost["digits"])}</span>'
        f'{chip(cost["verdict"], cost["change"])}<br>'
        f'<span class="was">was {number(cost["before"], "s", cost["digits"])}</span></div>'
        for cost in machine_costs) + "</div>")

    ratio = current["scenarios"].get("pages_per_launch", {}).get("value")
    if ratio:
        parts.append(
            f'<p class="lead">Those two set everything else: one browser start is worth about '
            f'<b>{ratio:g} inspections</b>. When that ratio moves, the engine is spreading its '
            f'inspections across the wrong number of browsers and should be retuned.</p>')

    full = next((r for r in reported if r["key"] == "corpus"), None)
    if full and full["usage"] is not None:
        parts.append(
            f'<p class="lead"><b>How much of the machine this used.</b> A full check runs at '
            f'{full["usage"] * 100:.0f}% of this machine. What is left is not one big miss but '
            f'the shape of the work: a diagram spends much of its time on a single step that '
            f'nothing can run beside, and ten of them overlapping only fills the gaps so far. '
            f'Any further win has to come from making a step cheaper, not from running more of '
            f'them at once.</p>')
    return "".join(parts)


def machine_note(current, baseline, drifted):
    now = current["machine"]
    line = (f'<b>{now["cores"]} cores</b>, {now["system"]} {now["arch"]}, '
            f'node {now["node"]}, d2 {now["d2"]}')
    when = (baseline or {}).get("recorded")
    against = (f'compared against numbers recorded <b>{when}</b>' if when
               else "no baseline recorded yet — nothing to compare against")
    warning = ""
    if drifted:
        warning = (f'<div class="warn">These numbers were recorded on a different machine — '
                   f'<b>{html.escape(", ".join(drifted))}</b> differ. Read the direction of '
                   f'each change, not the absolute seconds.</div>')
    return warning + (f'<p class="meta">{line}<br>{against}. Timings are machine-specific; '
                      f'a change under {SAME_WITHIN * 100:.0f}% is treated as noise.</p>')


def build(current, baseline, drifted, demo=False):
    reported = rows(current, baseline)
    machine_costs = costs(current, baseline)
    word, sentence = headline(reported)
    banner = ('<div class="demo">EXAMPLE REPORT — every number below is made up, '
              'to show how the layout reads. Nothing here was measured.</div>') if demo else ""
    # The one place the verdict's colour is decided; the subtitle rule above reads it.
    colour = {"better": "var(--diff-add-fg)", "worse": "var(--diff-del-fg)"}.get(word, "var(--fg)")
    # The date is deliberately not repeated here: the footer already records when the numbers
    # being compared against were taken, and a date in the opening sentence reads as though it
    # were part of the finding.
    against = ("Comparing the current version against the last measurement: "
               if baseline else "No earlier measurement to compare against. ")
    opening = (current.get("summary") or "").strip()
    prose = f'<p class="opening">{html.escape(opening)}</p>' if opening else ""
    sections = [
        {"id": "summary", "heading": "Summary",
         "html": prose + f'<p class="opening stat">{html.escape(against)}{sentence}</p>'},
    ]
    sections += [
            {"id": "jobs", "heading": "How long each job takes",
             "html": banner + jobs_block(reported)},
            {"id": "work", "heading": "How much work was done",
             "html": counts_block(reported)},
            {"id": "why", "heading": "Where the change came from",
             "html": why_block(current, baseline, reported, machine_costs)
                     + machine_note(current, baseline, drifted)},
    ]
    return {
        "title": "Diagram Engine — Performance Report",
        "sections": sections,
        "extra_css": f":root{{--verdict-fg:{colour}}}\n{PAGE_CSS}", "extra_js": "",
    }


def explainer():
    """The explain-diff renderer, loaded by path — same borrowing as `show_limits.py`.

    Taken for its LAYOUT only. The document it builds here has almost no prose in it on purpose:
    this is a page of numbers, and an explainer's habit of introducing everything would bury the
    four figures anybody opens it for.
    """
    path = os.path.join(HERE, "skills", "explain-diff", "scripts", "render.py")
    spec = importlib.util.spec_from_file_location("explain_diff_render", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write(spec, out=OUT):
    page = explainer().render(spec)
    page = page.replace("</head>", f'<style>{spec["extra_css"]}</style></head>', 1)
    if spec.get("extra_js"):
        page = page.replace("</body>", f'<script>{spec["extra_js"]}</script></body>', 1)
    with open(out, "w", encoding="utf-8") as handle:
        handle.write(page)
    return out


# --------------------------------------------------------------------------- cli


def load_baseline(path=BASELINE):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def write_json(data, path):
    """Sorted and indented, so a baseline's diff between two runs is readable."""
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return path


def save_baseline(current, path=BASELINE):
    return write_json(current, path)


def demo_run():
    """`(current, baseline)` of invented numbers, for checking how the report reads.

    Built to contain one of each outcome at once — a clear win, a clear regression, and a
    change too small to mean anything — because a layout only tested against good news hides
    whether bad news is legible. The regression comes with grown counts, which is the case the
    closing section has to get right: more work was asked for, so a slower time is not a fault.
    """
    def job(seconds, compiles, launches, pages, usage):
        return {"seconds": seconds, "compiles": compiles, "launches": launches,
                "pages": pages, "core_usage": usage}

    baseline = {
        "recorded": "2026-08-18", "machine": machine(),
        "summary": "before the arrow-scanning fix",
        "scenarios": {
            "one_figure": job(12.89, 66, 10, 66, 0.62),
            "corpus": job(72.71, 193, 39, 194, 0.38),
            "ten_drawings": job(22.49, 62, 13, 13, 0.16),
            "clipping_gate": job(2.14, 0, 1, 5, 0.08),
            "launch_floor": {"seconds": 0.757}, "page_cost": {"seconds": 0.4095},
            "pages_per_launch": {"value": 1.8},
        },
    }
    current = {
        "recorded": datetime.date.today().isoformat(), "machine": machine(),
        "summary": "Two more diagrams were added to the sample sets, so a full check now has "
                   "more to do than the recorded numbers did — it is slower for that reason "
                   "and not because anything got worse. Everything measured per diagram got "
                   "faster.",
        "why": "Each arrow is now scanned once instead of once per label, which is what made "
               "a single inspection 13x cheaper.",
        "scenarios": {
            # Between them these cover every case the report can show: a clear win, a clear
            # regression, a change too small to mean anything, and all three ways core usage
            # is judged — up (good), down while getting faster (neutral), down without
            # getting faster (bad).
            "one_figure": job(9.05, 66, 4, 66, 0.55),          # faster, usage down: neutral
            "corpus": job(88.50, 231, 33, 232, 0.30),          # slower AND usage down: bad
            "ten_drawings": job(16.63, 74, 15, 15, 0.22),      # faster, usage up: good
            "clipping_gate": job(2.24, 0, 1, 6, 0.08),         # inside the noise band
            "launch_floor": {"seconds": 0.742}, "page_cost": {"seconds": 0.0301},
            "pages_per_launch": {"value": 24.7},
        },
    }
    return current, baseline


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--no-open", action="store_true", help="write the report but do not open it")
    parser.add_argument("--quick", action="store_true",
                        help="one repeat per scenario — a sanity check, not a record")
    parser.add_argument("--update", action="store_true",
                        help=f"rewrite {os.path.basename(BASELINE)} from this run")
    parser.add_argument("--force", action="store_true",
                        help="allow --update when the machine differs from the baseline's")
    parser.add_argument("--from", dest="reuse", metavar="RUN.json",
                        help=f"rebuild the report from a saved run (default {RUN_OUT}) "
                             "instead of measuring again")
    parser.add_argument("--summary", metavar="TEXT",
                        help="a short paragraph opening the report: what changed, or that "
                             "nothing did. The report cannot infer this from timings")
    parser.add_argument("--why", metavar="TEXT",
                        help="how the change was made, for the closing section. Keep it to the "
                             "mechanism — what changed is already said by --summary")
    parser.add_argument("--demo", action="store_true",
                        help="build a report from invented numbers, clearly marked as such, to "
                             "check how the layout reads. Measures nothing")
    args = parser.parse_args(argv)

    baseline = load_baseline()
    # Every reason to refuse is settled BEFORE measuring. Reading the machine is free; measuring
    # is four minutes, and a run that ends in an argument error has wasted all of them.
    #
    # No baseline is not a drifted baseline: there is nothing to disagree with, and the first
    # recording on a fresh checkout must not need --force to be allowed.
    drifted = same_machine(machine(), baseline["machine"]) if baseline else []
    if args.update:
        if args.demo:
            parser.error("--demo invents its numbers; a baseline is only ever recorded from a "
                         "real run")
        if args.quick:
            parser.error("--quick skips the repeat that catches a contaminated run; "
                         "do not record a baseline from it")
        if args.reuse:
            parser.error("--from rebuilds the report from a saved run; a baseline is only ever "
                         "recorded from a run taken now")
        if drifted and not args.force:
            parser.error(f"the baseline was taken on a different machine ({', '.join(drifted)}); "
                         "pass --force if you really mean to replace it")

    if args.demo:
        current, baseline = demo_run()
        drifted = []
        print("DEMO — invented numbers, nothing measured")
    elif args.reuse:
        with open(args.reuse, encoding="utf-8") as handle:
            current = json.load(handle)
        if args.summary:
            current["summary"] = args.summary
        if args.why:
            current["why"] = args.why
        print(f"rebuilt from {args.reuse} — nothing was measured")
    else:
        print("measuring (do not run anything else meanwhile)...", flush=True)
        current = measure(quick=args.quick, summary=args.summary, why=args.why)
        write_json(current, RUN_OUT)

    for key, result in current["scenarios"].items():
        if isinstance(result, dict) and result.get("unstable"):
            print(f"  ! {key}: counts differed between repeats: {result['unstable']}")

    if args.update:
        print(f"baseline updated: {save_baseline(current)}")

    print(write(build(current, baseline, drifted, demo=args.demo)))
    if sys.platform == "darwin" and not args.no_open:
        subprocess.run(["open", OUT], check=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
