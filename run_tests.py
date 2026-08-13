#!/usr/bin/env python3
"""Run every test_*.py in the repo and report one aggregate result.

    python3 run_tests.py               # fast tests only (the default), in parallel
    python3 run_tests.py compact spec  # only files whose path contains one of these
    python3 run_tests.py --slow        # fast + slow. SEVEN MINUTES. See below.
    python3 run_tests.py -j1           # serial, for when a parallel failure is confusing

Why this exists: `python3 -m unittest discover` only finds tests under directories that
have an __init__.py. Only lib/ does — hooks/ and each skill's scripts/ don't, since
they're not Python packages, they're standalone CLI scripts — so discover silently runs
a fraction of the suite with no error. This runner doesn't rely on package discovery at
all: it just finds every test_*.py by filename and runs each one exactly the way
CLAUDE.md already tells you to run a single file (`python3 <path>`), one subprocess per
file, so a file's own sys.path bootstrap is exercised for real too.

**Every run prints seconds per file, slowest first.** That is not a nicety. This suite has
two files that cost minutes rather than seconds, and for a long time nothing said so — so
`--slow` got run in an edit-test loop where it had no business being, and one session spent
about forty of its seventy-six minutes waiting for the same six-minute file five times.
A cost you cannot see is a cost you repeat.

The `*_slow.py` split, and what each one actually costs:

  * `lib/diagram/test_place_slow.py` — ~4 minutes. Every assertion is a real callout-placement
    search: dozens of d2 compiles, each measured in a real browser. Nothing here can be faked
    down without testing the fake instead. Run it when you touch `place.py`, the harness
    geometry, or anything that moves a callout — not otherwise.
  * `hooks/test_paste_gate_slow.py` — ~36 seconds of real `time.sleep()` in a subprocess that
    cannot be mocked from the test.

So: iterate with a filter (`run_tests.py compact` is well under a second), run the plain fast
suite before you call something done, and run `--slow` once at the end if you touched what it
covers. Not in a loop. Ever.
"""
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
RAN_RE = re.compile(r"Ran (\d+) tests?")

# Seconds above which a file is called out in the summary. Roughly "you would notice this in
# an edit-test loop": the whole fast suite in parallel is about this, so any single file at
# this cost is the thing setting the pace.
SLOW_ENOUGH_TO_MENTION = 5.0


def find_test_files(include_slow, patterns):
    for path in sorted(REPO_ROOT.rglob("test_*.py")):
        if "__pycache__" in path.parts:
            continue
        if path.name.endswith("_slow.py") and not include_slow:
            continue
        rel = str(path.relative_to(REPO_ROOT))
        if patterns and not any(p in rel for p in patterns):
            continue
        yield path


def run_one(path):
    started = time.monotonic()
    proc = subprocess.run([sys.executable, str(path)], capture_output=True, text=True)
    return path, proc, time.monotonic() - started


def main():
    args = sys.argv[1:]
    include_slow = "--slow" in args
    workers = 8
    patterns = []
    for arg in args:
        if arg == "--slow":
            continue
        if arg.startswith("-j"):
            workers = max(1, int(arg[2:] or 1))
        else:
            patterns.append(arg)

    files = list(find_test_files(include_slow, patterns))
    if not files:
        print(f"no test_*.py files matched {patterns or 'anything'}", file=sys.stderr)
        return 1

    # Threads, not processes: each one only waits on a subprocess, so the GIL is never the
    # constraint. Serial, the fast suite is the sum of 26 files; in parallel it is the longest
    # one. Files that drive a browser run concurrently too — Chrome instances are independent.
    wall = time.monotonic()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(run_one, files))
    wall = time.monotonic() - wall

    failures = []
    total_tests = 0
    for path, proc, seconds in results:
        rel = path.relative_to(REPO_ROOT)
        m = RAN_RE.search(proc.stdout + proc.stderr)
        count = m.group(1) if m else "?"
        ok = proc.returncode == 0
        print(f"{'ok  ' if ok else 'FAIL'}  {seconds:6.1f}s  {rel}  ({count} tests)")
        if m:
            total_tests += int(m.group(1))
        if not ok:
            failures.append((rel, proc.stdout, proc.stderr))

    print()
    worst = sorted(results, key=lambda r: -r[2])[:3]
    if worst and worst[0][2] >= SLOW_ENOUGH_TO_MENTION:
        listed = ", ".join(f"{p.relative_to(REPO_ROOT)} {s:.0f}s" for p, _, s in worst
                           if s >= SLOW_ENOUGH_TO_MENTION)
        print(f"slowest: {listed}")

    if failures:
        for rel, stdout, stderr in failures:
            print(f"=== {rel} ===")
            print(stdout + stderr)
        print(f"FAILED: {len(failures)}/{len(files)} file(s), {total_tests} tests attempted "
              f"in {wall:.0f}s")
        return 1

    print(f"OK: {len(files)} file(s), {total_tests} tests in {wall:.0f}s"
          + ("" if include_slow else " (fast only — see the docstring before you add --slow)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
