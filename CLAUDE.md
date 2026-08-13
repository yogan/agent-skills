Guidance for working on this repo's own code. Not for a skill's runtime behavior — that's
`SKILL.md`/`REFERENCE.md` inside each skill — and not project setup — that's
[README.md](README.md).

## Layout

- `skills/<name>/` — one skill each, independently symlink-installable (see README.md).
  `scripts/` holds its Python implementation.
- `hooks/` — cross-skill runtime infra (the paste-gate Stop hook). See
  [hooks/README.md](hooks/README.md).
- `lib/` — Python shared repo-wide (skill scripts and `e2e/` both import from it).
  Only for logic that's provably identical across its consumers, modulo a trivial
  parameter — see below.
- `lib/diagram/` — the diagram renderer (D2), shared by the explainers and `visualize`.
  The one place in `lib/` that isn't pure Python: `lib/diagram/js/` holds a Node script
  because measuring a rendered diagram needs a real browser — see that module's docstring
  for why there's no substitute. The Python side decides; the JS only measures.
- `e2e/` — local GitLab demo/test rig for `review-mr`/`rework-mr` (see `e2e/README.md`).

## Sharing vs. duplication

`skills/review-mr/scripts/findings.py` and `skills/rework-mr/scripts/threads.py`
implement related but distinct workflows (reviewing someone else's MR vs. working
through your own), and some of their functions are genuinely identical while others
only look similar but encode a different per-skill state machine.

- **Identical modulo a trivial parameter** → belongs in `lib/`, imported by both.
- **Diverges because the underlying domain differs** (e.g. topic status vocabularies) →
  stays duplicated, with a `# Mirrors <other file>'s <name>` comment pointing at its
  counterpart, so a change to one prompts a check of the other.

Don't force the second kind into `lib/` — that trades duplication for a worse problem:
per-skill conditionals inside code meant to be shared.

## Testing

Plain `unittest`, colocated as `test_<module>.py` next to the module it covers. Run one
directly, e.g.:

    python3 skills/rework-mr/scripts/test_threads.py

To run everything: `python3 run_tests.py` (~30s). While editing, run less:

- `python3 run_tests.py --changed` — resolves your uncommitted edits through the repo's import
  graph and runs every test that reaches them. Touching `lib/gitlab.py` runs 4 files in 0.4s;
  touching `lib/diagram/palette.py` runs 14, because that is genuinely how far it reaches. It
  **declines and runs everything** whenever it cannot map a changed file, since a selector that
  quietly skips the failing test is worse than a slow one. `test_run_tests.py` exists to pin
  that, and every case in it is a real under-selection that was once shipped.
- `python3 run_tests.py compact` — plain substring filter on the path, when you know what you
  want.

Neither replaces the full fast suite before you commit. Don't reach for `python3 -m unittest
discover`: it only finds tests under directories with an `__init__.py`, which is just `lib/`
here, so it silently runs a fraction of the suite with no error.

**`--slow` costs about four minutes. Do not put it in an edit-test loop.** A `*_slow.py` file
is one whose cost is irreducible, and there are two, which is the thing to know before typing
the flag:

| file | cost | why it cannot be faster |
|---|---|---|
| `lib/diagram/test_place_slow.py` | ~3m50s | every assertion is a real callout-placement search — 64 d2 compiles per two-callout diagram, each measured in a real browser. Faking it would test the fake. |
| `hooks/test_paste_gate_slow.py` | ~36s | real `time.sleep()` in a subprocess, unmockable from the test. |

Run `--slow` **once**, at the end, and only if you touched `place.py`, the harness geometry,
callout anchoring, or the paste gate. Otherwise the plain fast suite is the gate. Every run
prints seconds per file and names the slowest three, so a regression in test cost is visible
without anyone having to go looking for it — that reporting exists because a session once spent
40 of its 76 minutes re-running `--slow` five times over minor edits.
