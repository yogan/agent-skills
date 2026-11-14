#!/usr/bin/env python3
"""Where a verify run's time went, and one set of runs against another.

    python3 timing.py DIR            # the runs under DIR (drive.py's --out; default .runs)
    python3 timing.py BEFORE AFTER   # two such folders, part by part

`drive.py verify` leaves a `timing.json` in each run's folder: the reset before it, claude's
start, and every step with its kind. This adds them up by part, the fastest passing run of
each scenario where a folder holds several.

**Comparing two versions of the driver, or of anything a scenario runs through**, is the
method of `measure_speed.py`, applied to scenarios: run the two alternately, never a block of
one and then a block of the other, each into a folder of its own (`--out`), several times;
then compare the folders. The fastest run stands for each scenario, since everything that
disturbs a run makes it slower and nothing makes it faster. A scenario is the same every time
on its recording, so what differs between two runs of one version is the machine.
"""
import json
import sys
from pathlib import Path

PARTS = ("reset", "start", "prompt", "click", "expect", "check", "other")


def parts_of(took):
    """One run's seconds by part: its reset, claude's start, and its steps by kind. A click or
    a key that starts a turn counts with the kind of its step, wait included."""
    out = dict.fromkeys(PARTS, 0.0)
    out["reset"], out["start"] = took.get("reset", 0.0), took.get("start", 0.0)
    for step in took.get("steps", []):
        kind = step["kind"] if step["kind"] in PARTS else "other"
        out[kind] += step["seconds"]
    return out


def fastest(folder):
    """scenario -> the parts of its fastest run under `folder` that ran every step it has
    a time for: a run that failed stops early and would win on that alone."""
    best = {}
    for f in sorted(Path(folder).glob("*/timing.json")):
        took = json.loads(f.read_text())
        if not took.get("passed"):
            continue
        parts = parts_of(took)
        name = took["scenario"]
        if name not in best or sum(parts.values()) < sum(best[name].values()):
            best[name] = parts
    return best


def table(runs):
    rows = [f"{'scenario':16}" + "".join(f"{p:>8}" for p in PARTS) + f"{'all':>8}"]
    total = dict.fromkeys(PARTS, 0.0)
    for name, parts in runs.items():
        rows.append(f"{name:16}" + "".join(f"{parts[p]:8.1f}" for p in PARTS)
                    + f"{sum(parts.values()):8.1f}")
        for p in PARTS:
            total[p] += parts[p]
    rows.append(f"{'all':16}" + "".join(f"{total[p]:8.1f}" for p in PARTS)
                + f"{sum(total.values()):8.1f}")
    return "\n".join(rows)


def compared(before, after):
    """Seconds by part over the scenarios both folders ran, and what changed."""
    both = [n for n in before if n in after]
    rows = [f"{len(both)} scenario(s) in both: {', '.join(both)}",
            f"{'part':8}{'before':>9}{'after':>9}{'change':>9}"]
    for p in (*PARTS, "all"):
        b = sum(sum(before[n].values()) if p == "all" else before[n][p] for n in both)
        a = sum(sum(after[n].values()) if p == "all" else after[n][p] for n in both)
        rows.append(f"{p:8}{b:9.1f}{a:9.1f}{a - b:+9.1f}" + (f"  ({(a - b) / b:+.0%})" if b else ""))
    return "\n".join(rows)


def main(argv):
    if len(argv) > 2 or any(a.startswith("-") for a in argv):
        print(__doc__, file=sys.stderr)
        return 2
    folders = argv or [str(Path(__file__).resolve().parent / ".runs")]
    runs = [fastest(f) for f in folders]
    print(table(runs[0]) if len(runs) == 1 else compared(*runs))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
