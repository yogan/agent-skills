Guidance for working on this repo's own code. Not for a skill's runtime behavior — that's
`SKILL.md`/`REFERENCE.md` inside each skill — and not project setup — that's
[README.md](README.md).

## Layout

- `skills/<name>/` — one skill each, independently symlink-installable (see README.md).
  `scripts/` holds its Python implementation.
- `hooks/` — cross-skill runtime infra (the paste-gate Stop hook). See
  [hooks/README.md](hooks/README.md).
- `lib/` — pure Python shared repo-wide (skill scripts and `e2e/` both import from it).
  Only for logic that's provably identical across its consumers, modulo a trivial
  parameter — see below.
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

To run everything: `python3 run_tests.py` (add `--slow` for the slower files too — see
below). Don't reach for `python3 -m unittest discover`: it only finds tests under
directories with an `__init__.py`, which is just `lib/` here, so it silently runs a
fraction of the suite with no error.

A `test_*.py` file named `*_slow.py` (currently only `hooks/test_paste_gate_slow.py`)
holds cases that are genuinely slow for a real reason (real `time.sleep()` in a
subprocess that can't be mocked from the test) and is skipped by `run_tests.py` unless
you pass `--slow`. Run it directly when you're actually touching that code path.
