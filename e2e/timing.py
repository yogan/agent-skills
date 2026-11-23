#!/usr/bin/env python3
"""Where a verify run's time went, and one set of runs against another.

    python3 timing.py DIR            # the runs under DIR (drive.py's --out; default .runs)
    python3 timing.py BEFORE AFTER   # two such folders, part by part
    python3 timing.py --update       # timing_baseline.json from the last push-level run
    python3 timing.py --update --force   # ... even though it ran on another machine

`drive.py verify` leaves a `timing.json` in each run's folder: the reset before it, claude's
start, and every step with its kind. This adds them up by part, the fastest passing run of
each scenario where a folder holds several.

**Comparing two versions of the driver, or of anything a scenario runs through**, is the
method of `measure_speed.py`, applied to scenarios: run the two alternately, never a block of
one and then a block of the other, each into a folder of its own (`--out`), several times;
then compare the folders. The fastest run stands for each scenario, since everything that
disturbs a run makes it slower and nothing makes it faster. A scenario is the same every time
on its recording, so what differs between two runs of one version is the machine.

## The baseline

`timing_baseline.json` is what the scenarios cost at the last re-recording, the way
`speed_baseline.json` is for the diagram engine: each scenario's seconds by part, from one
passing push-level run (`check_push.py`), and that run's wall clock for its scenarios with how
many ran at a time. `check_push.py` compares every scenario it ran against it and prints what
got slower or faster; it never fails on time. `--update` re-records it from the last passing
push-level run, which `check_push.py` keeps in its pass record, so updating runs nothing.

It is re-recorded in the same commit as the change that moved the time — a new scenario, a
slower step — so `git log -p e2e/timing_baseline.json` is the history, each entry beside its
cause. A scenario that run did not reach keeps its entry; one that no longer exists is dropped.
The wall clock is replaced only by a run that ran every scenario, since a partial run's is not
the same quantity.

**One run, three at a time, is noisier than best of N alone.** `measure_speed.py` treats a
change under 10% as noise for a best-of-N taken with nothing else running. A push-level run
times each scenario once, beside two others sharing one GitLab, so twice that, and never under
`SAME_SECONDS`: the shortest scenario has moved two seconds between two push-level runs of the
same code.

**These numbers are specific to one machine**, as in `measure_speed.py`: the comparison warns
when the baseline came from another, and `--update` then needs `--force`.

**Adding a field is free; renaming one is not.** Every committed baseline is a past
measurement, read by whatever reads the history.
"""
import datetime
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

E2E = Path(__file__).resolve().parent
BASELINE = E2E / "timing_baseline.json"
# Where check_push.py records its passes, each with the times of the scenarios it ran.
PUSH_RECORD = E2E.parent / ".check_push_passed.json"

PARTS = ("reset", "start", "prompt", "click", "expect", "check", "other")

# A scenario's change in seconds within this share of its baseline, or within SAME_SECONDS,
# is reported as unchanged: twice measure_speed.py's SAME_WITHIN, since this is one run beside
# two others and not the best of several alone (module docstring).
SAME_WITHIN = 0.20
SAME_SECONDS = 3.0


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


# --------------------------------------------------------------------------- the baseline


def machine():
    """What the numbers are true of. Mirrors measure_speed.py's machine(), with its field names,
    less the diagram engine's d2 and worker count and plus Claude Code, which every scenario
    runs; only DECIDING makes a baseline another machine's."""
    return {"system": platform.system(), "arch": platform.machine(), "cores": os.cpu_count(),
            "python": platform.python_version(), "node": _version(["node", "--version"]),
            "claude": _version(["claude", "--version"])}


def _version(command):
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return "?"
    return done.stdout.strip() or "?"


# A newer node or Claude Code moves the times as any change does, and is what a re-recording
# is for; another kind of machine makes them incomparable.
DECIDING = ("system", "arch", "cores")


def other_machine(now, before):
    """The deciding fields that differ between two machine records, empty when they agree."""
    return [f for f in DECIDING if (before or {}).get(f) != now.get(f)]


def load_baseline(path=None):
    try:
        return json.loads(Path(path or BASELINE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def seconds(parts):
    return round(sum(parts.get(p, 0.0) for p in PARTS), 1)


def classified(baseline, ran, existing):
    """{kind: [(name, before parts or None, after parts or None)]} for kind in slower, faster,
    same, new, gone. Only the scenarios in `ran` are compared: one the run did not reach says
    nothing about itself. Gone is a baseline entry that is no longer a scenario (`existing`)."""
    base = (baseline or {}).get("scenarios", {})
    out = {k: [] for k in ("slower", "faster", "same", "new", "gone")}
    for name, after in ran.items():
        if name not in base:
            out["new"].append((name, None, after))
            continue
        b, a = base[name]["seconds"], seconds(after)
        kind = ("same" if abs(a - b) <= max(SAME_SECONDS, SAME_WITHIN * b)
                else "slower" if a > b else "faster")
        out[kind].append((name, base[name], after))
    out["gone"] = [(n, base[n], None) for n in base if n not in existing]
    return out


def _moved_most(before, after):
    part = max(PARTS, key=lambda p: abs(after.get(p, 0.0) - before.get(p, 0.0)))
    return f"mostly {part} {after.get(part, 0.0) - before.get(part, 0.0):+.1f}s"


def comparison(baseline, run, existing, now=None):
    """The short table check_push.py prints after its scenarios: `run` is what it noted of
    them (`{"scenarios": {name: parts}, "wall": s, "ran": [names], "jobs": n}`)."""
    if not baseline:
        return ("scenario time: no baseline yet — `python3 e2e/timing.py --update` records one "
                "from this run once it has passed")
    rows = [f"scenario time against e2e/timing_baseline.json ({baseline.get('recorded')}, "
            f"{(baseline.get('commit') or '?')[:12]}); a change within {SAME_WITHIN:.0%} or "
            f"{SAME_SECONDS:.0f}s is noise"]
    drifted = other_machine(now or {}, baseline.get("machine")) if now is not None else []
    if drifted:
        rows.append(f"  ! the baseline was taken on another machine ({', '.join(drifted)}): "
                    "the comparison says little")
    kinds = classified(baseline, run["scenarios"], existing)
    for kind in ("slower", "faster"):
        for name, b, a in sorted(kinds[kind], key=lambda r: seconds(r[2]) - r[1]["seconds"],
                                 reverse=kind == "slower"):
            change = (seconds(a) - b["seconds"]) / b["seconds"]
            rows.append(f"  {kind:7} {name:18} {b['seconds']:6.1f}s -> {seconds(a):6.1f}s "
                        f"{change:+5.0%}  {_moved_most(b, a)}")
    for name, _, a in kinds["new"]:
        rows.append(f"  {'new':7} {name:18} {seconds(a):6.1f}s  (not in the baseline)")
    for name, b, _ in kinds["gone"]:
        rows.append(f"  {'gone':7} {name:18} {b['seconds']:6.1f}s  (in the baseline, no longer "
                    "a scenario)")
    if kinds["same"]:
        rows.append(f"  {'same':7} {len(kinds['same'])} scenario(s)")
    not_run = [n for n in baseline.get("scenarios", {}) if n in existing and n not in run["scenarios"]]
    if not_run:
        rows.append(f"  {len(not_run)} scenario(s) in the baseline not run here, not compared")
    rows.append("  " + _wall(baseline, run))
    return "\n".join(rows)


def _wall(baseline, run):
    """The scenarios' wall clock against the baseline's, when the two timed the same thing."""
    wall, jobs = run.get("wall"), run.get("jobs")
    if wall is None:
        return "wall clock: not taken"
    line = f"wall clock {wall:.0f}s for {len(run.get('ran', []))} scenario(s), {jobs} at a time"
    b_wall, b_ran = baseline.get("wall_seconds"), baseline.get("wall_scenarios") or []
    if b_wall is None:
        return line + "; the baseline has none"
    if set(run.get("ran", [])) != set(b_ran) or jobs != baseline.get("jobs"):
        return (line + f"; not compared: the baseline's {b_wall:.0f}s is for {len(b_ran)} "
                f"scenario(s), {baseline.get('jobs')} at a time")
    return line + f"; baseline {b_wall:.0f}s ({(wall - b_wall) / b_wall:+.0%})"


def rebased(previous, run, existing, commit, now, today=None):
    """The new baseline: `previous` with each scenario `run` timed replacing its entry, the
    entries of scenarios no longer in `existing` dropped, and the wall clock replaced only when
    the run ran every scenario there is."""
    scens = {n: e for n, e in ((previous or {}).get("scenarios") or {}).items() if n in existing}
    for name, parts in run["scenarios"].items():
        scens[name] = {"seconds": seconds(parts), **{p: round(parts.get(p, 0.0), 1) for p in PARTS},
                       "commit": commit}
    out = {"recorded": today or datetime.date.today().isoformat(), "commit": commit,
           "machine": now, "jobs": (previous or {}).get("jobs"),
           "wall_seconds": (previous or {}).get("wall_seconds"),
           "wall_scenarios": (previous or {}).get("wall_scenarios") or [],
           "scenarios": dict(sorted(scens.items()))}
    if run.get("wall") is not None and set(existing) <= set(run.get("ran", [])):
        out.update(jobs=run["jobs"], wall_seconds=round(run["wall"], 1),
                   wall_scenarios=sorted(run["ran"]))
    return out


def last_push_run(record=None):
    """(commit, timing) of the newest recorded push-level pass that timed any scenario."""
    try:
        rec = json.loads(Path(record or PUSH_RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None
    for p in reversed(rec.get("passed", [])):
        timing = (p.get("ran") or {}).get("timing")
        if timing and timing.get("scenarios"):
            return p["commit"], timing
    return None, None


def update(force=False, existing=None, now=None):
    """Re-record the baseline from the last passing push-level run. 0 when written."""
    previous = load_baseline()
    now = now or machine()
    drifted = other_machine(now, previous["machine"]) if previous else []
    if drifted and not force:
        print(f"timing: the baseline was taken on another machine ({', '.join(drifted)}); pass "
              "--force if you really mean to replace it", file=sys.stderr)
        return 2
    commit, run = last_push_run()
    if not run:
        print("timing: no push-level run has timed a scenario yet; run python3 check_push.py",
              file=sys.stderr)
        return 1
    if existing is None:
        sys.path.insert(0, str(E2E.parent))
        import check_push
        existing = [n for n, *_ in check_push.scenarios()]
    new = rebased(previous, run, existing, commit, now)
    BASELINE.write_text(json.dumps(new, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"timing: {BASELINE.name} re-recorded from {commit[:12]}'s push-level run: "
          f"{len(run['scenarios'])} scenario(s) from it, "
          f"{len(new['scenarios']) - len(run['scenarios'])} kept from before")
    return 0


def main(argv):
    if argv and argv[0] == "--update" and set(argv[1:]) <= {"--force"}:
        return update(force="--force" in argv)
    if len(argv) > 2 or any(a.startswith("-") for a in argv):
        print(__doc__, file=sys.stderr)
        return 2
    folders = argv or [str(E2E / ".runs")]
    runs = [fastest(f) for f in folders]
    print(table(runs[0]) if len(runs) == 1 else compared(*runs))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
