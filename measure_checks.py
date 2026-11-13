#!/usr/bin/env python3
"""How long one of the repo's checks takes, before a change against after it, measured the way
`measure_speed.py` measures the renderer.

    python3 measure_checks.py HEAD -- python3 run_tests.py --force
    python3 measure_checks.py HEAD -n 3 --count 'OK: \\d+ file\\(s\\), (\\d+) tests' -- python3 run_tests.py --force
    python3 measure_checks.py HEAD --after-args -j12 -- python3 run_tests.py --force

"Before" is the named revision, checked out in a throwaway worktree; "after" is this working
tree, uncommitted edits included. The command runs in each, from the tree's root.

`measure_speed.py`'s docstring is the method, and this applies the three rules of it that are
not about the renderer:

  * **warm up, and throw the first run away** — one run per side before anything is kept;
  * **best of N, never the mean** — the minimum is the sample least disturbed by other work;
  * **alternate, never two blocks** — the sides take turns, and which goes first in a round
    alternates too, so a machine warming up or a neighbour's load is charged to both.

**Counts beat seconds.** `--count REGEX` reads a number from each run's output (its groups, as
integers) — the tests run, say. A speed-up that changed one checks something else than before,
and the report says so before it says anything about time.

Do not run anything else while it measures. `--after-args` appends arguments to the after
side only, for a change that is a flag rather than an edit (a worker count).

The worktree gets what git does not carry and a check needs (`check_commits.copy_untracked`),
plus the test runner's timings, so both sides split and order their tests from the same record.
It is removed at the end, and only it.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import check_commits  # noqa: E402

# Gitignored files a side needs to behave like this checkout: the runner's per-file seconds
# decide which files are split and in what order they start.
CARRIED = (".run_tests_timings.json", ".run_tests_tmpdir")


def run(argv, cwd, count):
    started = time.monotonic()
    r = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                       env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")})
    seconds = time.monotonic() - started
    counts = None
    if count:
        m = re.search(count, r.stdout + r.stderr)
        counts = [int(g) for g in m.groups()] if m else None
    return {"seconds": round(seconds, 2), "rc": r.returncode, "counts": counts}


def best(samples):
    return min(s["seconds"] for s in samples)


def median(samples):
    xs = sorted(s["seconds"] for s in samples)
    return xs[len(xs) // 2]


def measure(before_tree, after_tree, argv, after_argv, rounds, count, log=print):
    sides = {"before": (before_tree, argv), "after": (after_tree, argv + after_argv)}
    for name, (tree, cmd) in sides.items():
        log(f"warm-up {name} …")
        run(cmd, tree, count)
    kept = {"before": [], "after": []}
    for i in range(rounds):
        order = ("before", "after") if i % 2 == 0 else ("after", "before")
        for name in order:
            tree, cmd = sides[name]
            sample = run(cmd, tree, count)
            kept[name].append(sample)
            log(f"round {i + 1} {name:6} {sample['seconds']:7.1f}s  rc={sample['rc']}"
                + (f"  counts={sample['counts']}" if count else ""))
    return kept


def summary(kept, count):
    lines = []
    b, a = kept["before"], kept["after"]
    if count:
        cb = {tuple(s["counts"] or ()) for s in b}
        ca = {tuple(s["counts"] or ()) for s in a}
        same = cb == ca and len(cb) == 1
        lines.append(f"counts: before {sorted(cb)}, after {sorted(ca)}"
                     + ("" if same else "  ** DIFFERENT: the two sides do not check the same **"))
    failed = [n for n, ss in kept.items() if any(s["rc"] for s in ss)]
    if failed:
        lines.append(f"** a run failed on: {', '.join(failed)} **")
    lines.append(f"before: best {best(b):.1f}s, median {median(b):.1f}s")
    lines.append(f"after:  best {best(a):.1f}s, median {median(a):.1f}s")
    lines.append(f"after/before, best of {len(a)}: {best(a) / best(b):.2f}")
    return lines


def main(argv):
    if "--" not in argv:
        print("usage: measure_checks.py <before-rev> [-n N] [--count RE] [--after-args ...] -- <command>",
              file=sys.stderr)
        return 2
    cut = argv.index("--")
    ap = argparse.ArgumentParser()
    ap.add_argument("before")
    ap.add_argument("-n", type=int, default=5, help="kept runs per side (default 5)")
    ap.add_argument("--count", help="regex whose groups are this check's counts")
    ap.add_argument("--after-args", nargs=argparse.REMAINDER, default=[])
    ap.add_argument("--json", help="also write every sample here")
    a = ap.parse_args(argv[:cut])
    cmd = argv[cut + 1:]

    rev = subprocess.run(["git", "rev-parse", "--verify", a.before], cwd=HERE,
                         capture_output=True, text=True).stdout.strip()
    if not rev:
        print(f"not a revision: {a.before}", file=sys.stderr)
        return 2
    root = tempfile.mkdtemp(prefix="measure-checks-")
    tree = os.path.join(root, rev[:12])
    try:
        subprocess.run(["git", "worktree", "add", "-q", "--detach", tree, rev], cwd=HERE,
                       check=True, capture_output=True)
        check_commits.copy_untracked(tree)
        for name in CARRIED:
            if os.path.exists(os.path.join(HERE, name)):
                shutil.copy2(os.path.join(HERE, name), os.path.join(tree, name))
        kept = measure(tree, HERE, cmd, a.after_args, a.n, a.count)
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", tree], cwd=HERE,
                       capture_output=True)
        shutil.rmtree(root, ignore_errors=True)
    for line in summary(kept, a.count):
        print(line)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump({"before": rev, "command": cmd, "after_args": a.after_args, "samples": kept},
                      fh, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
