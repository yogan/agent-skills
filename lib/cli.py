"""Generic helpers for standalone CLI scripts — currently just `die`, which turned up
identically in lib/gitlab.py, e2e/bootstrap.py, and e2e/fixture.py. Split out on its
own rather than folded into gitlab.py, since it isn't a GitLab concern — e2e's scripts
have nothing to do with GitLab's API, only with git/glab setup for the demo rig.
"""
import sys


def die(msg):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)
