#!/usr/bin/env python3
"""Run every test_*.py in the repo and report one aggregate result.

    python3 run_tests.py               # fast tests only (the default), in parallel
    python3 run_tests.py --changed     # only what your uncommitted edits can affect
    python3 run_tests.py compact spec  # only files whose path contains one of these
    python3 run_tests.py --slow        # fast + slow. TWO MINUTES. See below.
    python3 run_tests.py -j1           # serial, for when a parallel failure is confusing

`--changed` reads the working tree against HEAD, resolves each changed module through the
repo's own import graph, and runs every test that reaches it transitively. It declines and
runs everything whenever it cannot map a changed file, because a selector that quietly skips
the test that would have failed is worse than a slow one. It is for the edit loop; the full
fast suite is still what you run before committing.

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

  * `lib/diagram/test_place_slow.py` — ~2 minutes. Every assertion is a real callout-placement
    search: dozens of d2 compiles, each measured in a real browser. Nothing here can be faked
    down without testing the fake instead. Run it when you touch `place.py`, the harness
    geometry, or anything that moves a callout — not otherwise.
  * `hooks/test_paste_gate_slow.py` — ~36 seconds of real `time.sleep()` in a subprocess that
    cannot be mocked from the test.

So: iterate with a filter (`run_tests.py compact` is well under a second), run the plain fast
suite before you call something done, and run `--slow` once at the end if you touched what it
covers. Not in a loop. Ever.
"""
import ast
import json
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

# Per-file seconds from the last run, so the next one knows what to shard. Gitignored: it is
# a cache of this machine's timings, not a fact about the repo.
TIMINGS = REPO_ROOT / ".run_tests_timings.json"

# Seconds above which a file is split into one job per test class. Comfortably above the
# handful of files in the 5-8s range, so only the genuinely dominant ones are split.
SHARD_OVER = 9.0


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


def run_one(job):
    path, klass = job
    argv = [sys.executable, str(path)] + ([klass] if klass else [])
    started = time.monotonic()
    proc = subprocess.run(argv, capture_output=True, text=True)
    return job, proc, time.monotonic() - started


def _classes(path):
    """The TestCase class names in `path`, for sharding it across the pool.

    Restricted to the `Test*` naming convention every file here follows: a shard is run as
    `python3 <file> <ClassName>`, and handing unittest the name of a plain helper class fails
    the whole shard for no reason.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return []
    return [n.name for n in tree.body
            if isinstance(n, ast.ClassDef) and n.name.startswith("Test")]


def _load_timings():
    try:
        return json.loads(TIMINGS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _jobs(files, timings):
    """One subprocess job per file, except slow files, which get one per test class.

    The pool parallelises across FILES, which does nothing for a suite whose cost is
    concentrated in one of them: `test_visualize.py` is 41 tests of about a second each —
    no hot spot to optimise, just per-test overhead 41 times — and it alone set the pace for
    every diagram change. unittest takes a class name on the command line, so a slow file can
    be split at no cost to the tests themselves.

    Which files are slow is read from the previous run rather than guessed. A file with one
    class cannot be split, and splitting a fast one would only pay interpreter startup per
    shard, so the threshold keeps this to the handful that earn it.
    """
    jobs = []
    for path in files:
        rel = str(path.relative_to(REPO_ROOT))
        classes = _classes(path)
        if timings.get(rel, 0) > SHARD_OVER and len(classes) > 1:
            jobs += [(path, name) for name in classes]
        else:
            jobs.append((path, None))
    return jobs


def _module_index(files):
    """Every importable name in the repo -> the file it resolves to.

    Two naming schemes, because the repo has two kinds of Python. `lib/` is a package and is
    imported by its dotted path. A skill's `scripts/` is not a package — those are standalone
    CLI scripts that import their neighbours by bare name — so a bare name is registered per
    directory, and resolution prefers the importer's own directory before falling back.
    """
    index = {}
    for path in files:
        rel = path.relative_to(REPO_ROOT)
        if rel.parts[0] == "lib":
            dotted = ".".join(rel.with_suffix("").parts)
            index[dotted] = path
            # A package is imported by its directory name, so `lib.diagram.gates` has to
            # resolve to gates/__init__.py — without this, editing a package's __init__
            # selected nothing at all.
            if path.name == "__init__.py":
                index[dotted.rsplit(".__init__", 1)[0]] = path
        index[(path.parent, path.stem)] = path
    return index


def _conventional_test(path, files):
    """The `test_<module>.py` beside `path`, if the repo's naming convention has one.

    Needed because two things break the import graph. A CLI script is named with a hyphen
    (`paste-gate.py`, `resolve-target.py`) and its test loads it through importlib rather than
    `import`, so no edge exists to follow; and a module can be exercised by a test that never
    imports it by name. The convention is what ties them, so the convention is an edge.
    """
    candidate = path.with_name("test_" + path.stem.replace("-", "_") + ".py")
    return candidate if candidate in files else None


def _imports(path, index):
    """The repo files `path` imports directly, resolved through `index`.

    Relative imports have to be handled explicitly, and getting that wrong is not a cosmetic
    miss: `from . import palette` parses with `module=None`, so skipping those dropped nearly
    every edge inside `lib/diagram` and the selector silently under-ran. Under-selection is
    the one failure mode this whole feature must not have.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return set()
    out = set()

    def resolve(name, base):
        # `from lib.diagram import render` arrives as both "lib.diagram" and
        # "lib.diagram.render"; whichever resolves is the real one, and both are cheap to try.
        if name in index:
            out.add(index[name])
        if (base, name.split(".")[0]) in index:
            out.add(index[(base, name.split(".")[0])])

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            base = path.parent
            for _ in range(max(0, node.level - 1)):
                base = base.parent
            if node.module:
                resolve(node.module, base)
                for alias in node.names:
                    resolve(f"{node.module}.{alias.name}", base)
                    if node.level:
                        # `from .gates import size` -> gates/size.py
                        sub = base / node.module.replace(".", "/")
                        resolve_sub = (sub, alias.name)
                        if resolve_sub in index:
                            out.add(index[resolve_sub])
            else:
                # `from . import palette`
                for alias in node.names:
                    resolve(alias.name, base)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                resolve(alias.name, path.parent)
    return out - {path}


def _dependents(changed, files):
    """Test files that reach any of `changed`, directly or through other repo modules."""
    index = _module_index(files)
    deps = {path: _imports(path, index) for path in files}
    for path in files:
        own = _conventional_test(path, files)
        if own:
            deps[own].add(path)

    reached = set(changed)
    while True:
        grown = {p for p, uses in deps.items() if uses & reached} | reached
        if grown == reached:
            return {p for p in reached if p.name.startswith("test_")}
        reached = grown


def changed_files():
    """Everything this working tree has touched relative to HEAD, tracked or not."""
    out = set()
    for cmd in (["git", "diff", "--name-only", "HEAD"],
                ["git", "ls-files", "--others", "--exclude-standard"]):
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT)
        if proc.returncode == 0:
            out.update(line for line in proc.stdout.split("\n") if line.strip())
    return out


def select_changed(files):
    """(test files to run, note) for `--changed`, or (None, why) to fall back to everything.

    Deliberately conservative: this is a way to make the edit loop cheap, NOT a substitute for
    the full suite before you commit. Anything it cannot reason about — a changed non-Python
    file, a changed file it cannot resolve — makes it decline and run everything, because a
    selector that quietly skips the test that would have failed is worse than a slow one.
    """
    raw = changed_files()
    if not raw:
        return None, "nothing changed against HEAD"
    changed, unknown = set(), []
    for rel in raw:
        path = (REPO_ROOT / rel).resolve()
        if path.suffix == ".py" and path in files:
            changed.add(path)
        elif path.suffix == ".js" and "diagram" in path.parts:
            # measure.js is the browser side of the diagram gates; nothing imports it, so
            # attribute it to the module that shells out to it.
            browser = REPO_ROOT / "lib" / "diagram" / "browser.py"
            if browser in files:
                changed.add(browser)
        else:
            unknown.append(rel)
    if unknown:
        return None, f"{len(unknown)} changed file(s) it cannot map ({unknown[0]}, ...)"
    return _dependents(changed, files), f"{len(changed)} changed module(s)"


def main():
    args = sys.argv[1:]
    include_slow = "--slow" in args
    only_changed = "--changed" in args
    workers = 8
    patterns = []
    for arg in args:
        if arg in ("--slow", "--changed"):
            continue
        if arg.startswith("-j"):
            try:
                workers = max(1, int(arg[2:] or 1))
            except ValueError:
                print(f"not a worker count: {arg} (use -j4)", file=sys.stderr)
                return 1
        else:
            patterns.append(arg)

    files = list(find_test_files(include_slow, patterns))
    if not files:
        print(f"no test_*.py files matched {patterns or 'anything'}", file=sys.stderr)
        return 1

    if only_changed:
        every = [p for p in REPO_ROOT.rglob("*.py") if "__pycache__" not in p.parts]
        selected, note = select_changed(set(every))
        if selected is None:
            print(f"--changed: running everything ({note})")
        else:
            files = [p for p in files if p in selected]
            print(f"--changed: {len(files)} of {len(selected)} affected test file(s) "
                  f"from {note}")
            if not files:
                print("nothing to run — but run the full suite before you commit")
                return 0

    # Threads, not processes: each one only waits on a subprocess, so the GIL is never the
    # constraint. Serial, the fast suite is the sum of 26 files; in parallel it is the longest
    # one. Files that drive a browser run concurrently too — Chrome instances are independent.
    timings = _load_timings()
    jobs = _jobs(files, timings)
    wall = time.monotonic()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(run_one, jobs))
    wall = time.monotonic() - wall

    # Shards are reported as the one file they came from: the split is a scheduling detail,
    # and eight lines saying `test_visualize.py [SomeClass]` would bury the other 26 files.
    per_file = {}
    failures = []
    total_tests = 0
    for (path, klass), proc, seconds in results:
        rel = str(path.relative_to(REPO_ROOT))
        m = RAN_RE.search(proc.stdout + proc.stderr)
        ok = proc.returncode == 0
        agg = per_file.setdefault(rel, {"seconds": 0.0, "wall": 0.0, "tests": 0, "ok": True,
                                        "shards": 0})
        agg["seconds"] += seconds                 # summed: what the file costs to run
        agg["wall"] = max(agg["wall"], seconds)   # longest shard: what it costs in parallel
        agg["shards"] += 1
        agg["tests"] += int(m.group(1)) if m else 0
        agg["ok"] &= ok
        if m:
            total_tests += int(m.group(1))
        if not ok:
            failures.append((f"{rel} [{klass}]" if klass else rel, proc.stdout, proc.stderr))

    for rel in sorted(per_file):
        agg = per_file[rel]
        split = f"  ({agg['shards']} shards)" if agg["shards"] > 1 else ""
        print(f"{'ok  ' if agg['ok'] else 'FAIL'}  {agg['wall']:6.1f}s  {rel}  "
              f"({agg['tests']} tests){split}")

    try:
        TIMINGS.write_text(json.dumps({r: round(a["seconds"], 2)
                                       for r, a in per_file.items()}, indent=0),
                           encoding="utf-8")
    except OSError:
        pass                                      # a missing cache only costs one slow run

    print()
    worst = sorted(per_file.items(), key=lambda kv: -kv[1]["wall"])[:3]
    listed = ", ".join(f"{r} {a['wall']:.0f}s" for r, a in worst
                       if a["wall"] >= SLOW_ENOUGH_TO_MENTION)
    if listed:
        print(f"slowest: {listed}")

    if failures:
        for rel, stdout, stderr in failures:
            print(f"=== {rel} ===")
            print(stdout + stderr)
        print(f"FAILED: {len(failures)}/{len(jobs)} job(s), {total_tests} tests attempted "
              f"in {wall:.0f}s")
        return 1

    print(f"OK: {len(files)} file(s), {total_tests} tests in {wall:.0f}s"
          + ("" if include_slow else " (fast only — see the docstring before you add --slow)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
