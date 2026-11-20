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
import signal
import subprocess
import sys


def main(argv):
    if "--" not in argv or argv.index("--") != 3 or len(argv) < 5:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2
    path, cmd = argv[0], argv[4:]
    # Bytes throughout: the file goes back exactly as it was, CRLF line ends and all.
    fixed, unfixed = (a.encode("utf-8", "surrogateescape") for a in argv[1:3])
    with open(path, "rb") as fh:
        original = fh.read()
    if original.count(fixed) != 1:
        print(f"{path}: the fixed text occurs {original.count(fixed)} times, not once",
              file=sys.stderr)
        return 2
    # A timeout or a closed pane ends a run with SIGTERM or SIGHUP, whose default handling
    # skips `finally`: turned into an exit, the fix still goes back.
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, lambda signum, _frame: sys.exit(128 + signum))
    try:
        with open(path, "wb") as fh:
            fh.write(original.replace(fixed, unfixed))
        r = subprocess.run(cmd)
    except OSError as e:
        # A command that cannot start proves nothing either way: "could not try", not the
        # exit 1 of a traceback, which reads as "the test proves nothing".
        print(f"could not run {cmd[0]}: {e}", file=sys.stderr)
        return 2
    finally:
        with open(path, "wb") as fh:
            fh.write(original)
    if r.returncode == 0:
        print(f"the command PASSED without the fix in {path}: the test does not prove it")
        return 1
    print(f"the command failed without the fix in {path} (exit {r.returncode}), as it should; "
          "the fix is back")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
