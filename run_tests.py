#!/usr/bin/env python3
"""Run every test_*.py in the repo and report one aggregate result.

    python3 run_tests.py          # fast tests only (the default)
    python3 run_tests.py --slow   # fast + slow (e.g. hooks/test_paste_gate_slow.py)

Why this exists: `python3 -m unittest discover` only finds tests under directories that
have an __init__.py. Only lib/ does — hooks/ and each skill's scripts/ don't, since
they're not Python packages, they're standalone CLI scripts — so discover silently runs
a fraction of the suite with no error. This runner doesn't rely on package discovery at
all: it just finds every test_*.py by filename and runs each one exactly the way
CLAUDE.md already tells you to run a single file (`python3 <path>`), one subprocess per
file, so a file's own sys.path bootstrap is exercised for real too.

Files matching *_slow.py are skipped unless --slow is given — see
hooks/test_paste_gate.py's module docstring for why that split exists and what it costs
to skip it.
"""
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
RAN_RE = re.compile(r"Ran (\d+) tests?")


def find_test_files(include_slow):
    for path in sorted(REPO_ROOT.rglob("test_*.py")):
        if "__pycache__" in path.parts:
            continue
        if path.name.endswith("_slow.py") and not include_slow:
            continue
        yield path


def main():
    include_slow = "--slow" in sys.argv[1:]
    files = list(find_test_files(include_slow))
    if not files:
        print("no test_*.py files found", file=sys.stderr)
        return 1

    failures = []
    total_tests = 0
    for path in files:
        rel = path.relative_to(REPO_ROOT)
        proc = subprocess.run([sys.executable, str(path)], capture_output=True, text=True)
        m = RAN_RE.search(proc.stdout + proc.stderr)
        count = m.group(1) if m else "?"
        ok = proc.returncode == 0
        print(f"{'ok  ' if ok else 'FAIL'}  {rel}  ({count} tests)")
        if m:
            total_tests += int(m.group(1))
        if not ok:
            failures.append((rel, proc.stdout, proc.stderr))

    print()
    if failures:
        for rel, stdout, stderr in failures:
            print(f"=== {rel} ===")
            print(stdout + stderr)
        print(f"FAILED: {len(failures)}/{len(files)} file(s), {total_tests} tests attempted")
        return 1

    print(f"OK: {len(files)} file(s), {total_tests} tests"
         + ("" if include_slow else " (fast only — pass --slow for everything)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
