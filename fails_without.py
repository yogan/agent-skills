#!/usr/bin/env python3
"""Prove a test discriminates: take the fix out, run the test, and put the fix back.

    python3 fails_without.py <file> <fixed text> <unfixed text> -- <test command...>

`<fixed text>` must occur exactly once in `<file>`; it is replaced by `<unfixed text>` for
the one run of the command, and the file is restored whatever happens: the command passing,
failing, or being interrupted. Exit 0 when the command FAILED without the fix (the test
holds), 1 when it passed anyway (the test proves nothing), 2 when it could not try.

The file is changed in place for the length of the run, so keep the command short: in a
checkout another session shares, it sees the unfixed file meanwhile. Before this, sessions
did the same with `cp`, `sed -i` and a copy back by hand, a dozen times, once leaving a
backup in /tmp to hunt down and twice needing a second `sed` to land the revert.
"""
import subprocess
import sys


def main(argv):
    if "--" not in argv or argv.index("--") != 3 or len(argv) < 5:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2
    path, fixed, unfixed, cmd = argv[0], argv[1], argv[2], argv[4:]
    with open(path, encoding="utf-8") as fh:
        original = fh.read()
    if original.count(fixed) != 1:
        print(f"{path}: the fixed text occurs {original.count(fixed)} times, not once",
              file=sys.stderr)
        return 2
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(original.replace(fixed, unfixed))
        r = subprocess.run(cmd)
    finally:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(original)
    if r.returncode == 0:
        print(f"the command PASSED without the fix in {path}: the test does not prove it")
        return 1
    print(f"the command failed without the fix in {path} (exit {r.returncode}), as it should; "
          "the fix is back")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
