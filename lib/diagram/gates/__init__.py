"""The gates: what turns "does this diagram look right" into something a test can answer.

The recipe in `d2.py` leans on several undocumented d2 behaviours. That is only a
tolerable risk because of what is in here — pin the d2 version, and let these catch the
drift when it happens.

Six checks, five of which need no browser:

    size       rendered height fits one viewport
    glyph      no glyph larger than an h2 (26.6px at a 19px root)
    body       the MODAL glyph size — the size most of the diagram is set in — is at
               most body text (19px); a diagram whose ordinary text out-sizes the prose
               reads as a poster dropped into the article
    legibility no glyph below ~11px
    contrast   WCAG AA in BOTH themes
    theming    every colour literal has a CSS-var mapping
    clipping   nothing cut off, shadow spread included -- needs a browser, lives with the
               placement pass

**A gate that cannot run must fail, not pass.** This is the rule the whole approach rests
on, and it is written down because it was learned the expensive way: during the prototype
a patch to a checker silently no-op'd — the marker string it searched for was not in the
file — and it still printed success. The green result was worthless, and nothing about it
looked wrong. So `GateError` exists for "I could not measure this", and it is distinct
from a finding of "I measured this and it is bad".
"""
import sys


class GateError(RuntimeError):
    """A gate could not run — an unmeasurable input, not a failed measurement.

    Never swallow this into a pass. An SVG with no parseable size, or with no text where
    text is expected, means the gate is blind, and a blind gate reporting success is
    worse than no gate at all.
    """


class Result:
    """One gate's verdict on one diagram."""

    def __init__(self, name, gate, problems=(), detail=""):
        self.name = name
        self.gate = gate
        self.problems = list(problems)
        self.detail = detail

    @property
    def ok(self):
        return not self.problems

    def __repr__(self):
        return (f"Result({self.name!r}, {self.gate!r}, "
                f"{'ok' if self.ok else self.problems})")


def report(results, stream=None):
    """Print a table of results and return the number that failed.

    Callers use the count as an exit code, so a gate run is a build step rather than
    something a human has to read carefully.
    """
    stream = stream or sys.stdout
    width = max([len(r.name) for r in results] + [7])
    failed = 0
    for result in results:
        if not result.ok:
            failed += 1
        verdict = "ok" if result.ok else "; ".join(result.problems)
        print(f"{result.name:<{width}}  {result.gate:<9}  {result.detail:<34}  {verdict}",
              file=stream)
    total = len(results)
    print(f"\n{total - failed}/{total} checks pass", file=stream)
    return failed
