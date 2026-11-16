"""Generic helpers for standalone CLI scripts: `die`, which turned up
identically in lib/gitlab.py, e2e/bootstrap.py, and e2e/fixture.py. Split out on its
own rather than folded into gitlab.py, since it isn't a GitLab concern — e2e's scripts
have nothing to do with GitLab's API, only with git/glab setup for the demo rig.
And `iid_option`, the one option both review scripts take on either side of their
subcommand.
"""
import argparse
import sys


def die(msg):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def iid_option(ap, sub):
    """`--iid <n>`, the MR a command is about, before the subcommand as well as after it, as
    git and glab take their global options: `threads.py --iid 3 sync` and `threads.py sync
    --iid 3` alike. Every subcommand takes it, one that needs no MR too (it ignores it), so
    no natural form fails. Call once every subcommand is added.

    The subcommands' own `--iid` has no default: argparse lets a subparser's default
    overwrite what the main parser read, so `--iid 3 sync` would come out without its MR.
    Given in both places, the one after the subcommand wins."""
    ap.add_argument("--iid", type=int, help="the MR, by its number (!<n>); else the current branch's")
    for p in sub.choices.values():
        own = next((a for a in p._actions if "--iid" in a.option_strings), None)
        if own is None:
            p.add_argument("--iid", type=int, default=argparse.SUPPRESS,
                           help="taken for symmetry: this command is not about one MR")
        else:
            own.default = argparse.SUPPRESS
