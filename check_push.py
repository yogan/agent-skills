#!/usr/bin/env python3
"""The push level: everything the top commit has to pass before it is pushed, run once.

    python3 check_push.py                  # check HEAD against its upstream, record the pass
    python3 check_push.py --scenarios all  # every recorded scenario, whatever the change
    python3 check_push.py --scenarios none # no scenario (say so when you report)
    python3 check_push.py --dry-run        # say what would run, run nothing
    python3 check_push.py --verify SHA...  # the pre-push hook: has each of these passed?

The repo's checks come in three levels, so that the expensive ones are paid once and not in
every loop (CLAUDE.md, "Testing"):

  * **loop**   — `run_tests.py --changed`, and the one scenario a change shows in;
  * **commit** — the commit hook, and `check_commits.py` after a rewrite: ruff, private words,
    the tests each commit's own change reaches;
  * **push**   — this: the commit level for every unpushed commit, the whole unit suite on the
    top commit, and the recorded scenarios the change can reach (`e2e/drive.py verify`).

A pass is recorded against the top commit's TREE, so an amend that changes nothing, or a
second run, is free; and after an amend that does change something, the scenarios are picked
from what changed since the last tree that passed. `.githooks/pre-push` refuses a push whose
commit has no recorded pass (`git push --no-verify` overrides it, as for any hook).

It checks the committed tree, so it refuses to start with uncommitted changes: a pass would
otherwise be recorded for something else than what gets pushed.

Which scenarios a change reaches: a scenario reaches the skills whose slash command its steps
type; a skill's scripts reach the `lib/` modules they import. The mod, the e2e harness and the
paste-gate hook reach every scenario; documentation, the repo's own tooling and the skills no
scenario runs reach none; and anything else reaches all of them, since a picker that skips the
scenario that would have failed is worse than a slow one.
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "e2e"))

import run_tests  # noqa: E402

RECORD = HERE / ".check_push_passed.json"
KEEP = 30           # recorded passes kept; a pass older than these is simply run again

# What the scenarios exercise, by path. Matched in order; the first match decides.
ALL, NONE = "all", "none"
DOC = run_tests.DOCS
# The repo's own tooling, and skills no scenario runs: nothing a session under test reads.
UNREACHED = (
    re.compile(r"^(?:run_tests|check_commits|check_push|measure_speed|measure_checks|"
               r"compare_figures|show_limits|fails_without|private_terms)\.py$"),
    re.compile(r"^(?:speed_baseline\.json|ruff\.toml|\.gitignore|LICENSE)$"),
    re.compile(r"^(?:\.githooks|docs|prototypes)/"),
    re.compile(r"^skills/(?:visualize|explain-diff|split-handoff|self-review)/"),
    re.compile(r"(?:^|/)test_[^/]*\.py$"),
)
# Skills a scenario reaches through another one: review-mr hands work to these.
VIA = {"explain-branch": "review-mr", "review-branch": "review-mr"}


def git(*args):
    r = subprocess.run(["git", *args], cwd=HERE, capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def scenarios():
    """(name, skills it types, mods it loads, recording it plays) for every scenario a verify
    runs: the ones handing over to a person are showcase only."""
    import drive
    import scenarios as sc_mod
    out = []
    for sc in sc_mod.SCENARIOS:
        if any(isinstance(s, drive.Handover) for s in sc.steps):
            continue
        typed = {m.group(1) for s in sc.steps if isinstance(s, drive.Prompt)
                 for m in [re.match(r"/([\w-]+)", s.text)] if m}
        out.append((sc.name, typed, bool(sc.mods), sc.recording or sc.name))
    return out


def skill_modules():
    """Each skill -> every repo Python file its scripts reach through imports."""
    every = {p for p in HERE.rglob("*.py") if "__pycache__" not in p.parts}
    graph = run_tests.import_graph(every)
    reach = {}
    for skill in (HERE / "skills").iterdir():
        todo = [p for p in every if skill / "scripts" in p.parents]
        seen = set(todo)
        while todo:
            for nxt in graph.get(todo.pop(), ()):
                if nxt not in seen:
                    seen.add(nxt)
                    todo.append(nxt)
        reach[skill.name] = {str(p.relative_to(HERE)) for p in seen}
    return reach


def reached(paths, scens, reach):
    """The scenario names a change to `paths` can affect, in the order they are defined."""
    names = [n for n, _, _, _ in scens]
    hit = set()
    for path in paths:
        if Path(path).suffix.lower() in DOC and not path.startswith("skills/"):
            continue
        if any(rx.search(path) for rx in UNREACHED):
            continue
        m = re.match(r"^e2e/recordings/([\w-]+)\.json$", path)
        if m:
            hit |= {n for n, _, _, rec in scens if rec == m.group(1)}
            continue
        if path.startswith(("e2e/", "hooks/", ".claude-plugin/")):
            return names
        if path.startswith("mods/"):
            hit |= {n for n, _, mods, _ in scens if mods}
            continue
        m = re.match(r"^skills/([\w-]+)/", path)
        if m:
            skill = VIA.get(m.group(1), m.group(1))
            hit |= {n for n, typed, _, _ in scens if skill in typed}
            continue
        if path.startswith("lib/"):
            skills = {s for s, files in reach.items() if path in files}
            if not skills and path.endswith(".py"):
                continue                      # a lib module no skill script reaches
            if not skills:
                return names                  # a lib data file: cannot tell who reads it
            hit |= {n for n, typed, _, _ in scens
                    if typed & {VIA.get(s, s) for s in skills}}
            continue
        return names                          # anything else: cannot tell, so everything
    return [n for n in names if n in hit]


def load_record():
    try:
        return json.loads(RECORD.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"passed": []}


def passed_trees():
    return {p["tree"] for p in load_record()["passed"]}


def tree_of(rev):
    return git("rev-parse", f"{rev}^{{tree}}")


def record_pass(tree, commit, ran):
    rec = load_record()
    rec["passed"] = [p for p in rec["passed"] if p["tree"] != tree][-(KEEP - 1):] + [
        {"tree": tree, "commit": commit, "at": time.strftime("%Y-%m-%d %H:%M"), "ran": ran}]
    RECORD.write_text(json.dumps(rec, indent=1), encoding="utf-8")


def verify(shas):
    """For the pre-push hook: 0 when every commit to be pushed has a recorded pass."""
    known = passed_trees()
    missing = [s for s in shas if set(s) != {"0"} and tree_of(s) not in known]
    if missing:
        print(f"pre-push: {', '.join(s[:12] for s in missing)} has not passed the push level.\n"
              "  run: python3 check_push.py    (git push --no-verify skips this check)",
              file=sys.stderr)
        return 1
    return 0


def dirty():
    return git("status", "--porcelain", "--untracked-files=normal")


def run(step, argv):
    print(f"\n==> {step}: {' '.join(argv)}", flush=True)
    started = time.monotonic()
    r = subprocess.run(argv, cwd=HERE)
    print(f"    {step}: {'ok' if r.returncode == 0 else 'FAILED'} in "
          f"{time.monotonic() - started:.0f}s", flush=True)
    return r.returncode == 0


def main(argv):
    if argv[:1] == ["--verify"]:
        return verify(argv[1:])
    mode = "auto"
    if "--scenarios" in argv:
        mode = argv[argv.index("--scenarios") + 1]
    dry = "--dry-run" in argv

    if dirty() and not dry:
        print("check_push: the working tree has uncommitted changes; commit or stash them first, "
              "so what passes is what gets pushed.", file=sys.stderr)
        return 2
    head, tree = git("rev-parse", "HEAD"), tree_of("HEAD")
    if tree in passed_trees() and not dry:
        print(f"check_push: {head[:12]}'s tree has already passed; nothing to run.")
        return 0
    base = git("rev-parse", "--abbrev-ref", "@{u}") or "origin/main"
    since, why = base, f"since {base}"
    for p in reversed(load_record()["passed"]):
        if git("cat-file", "-t", p["tree"]) == "tree":
            since, why = p["tree"], f"since the last tree that passed ({p['commit'][:12]})"
            break
    changed = (git("diff", "--name-only", since, "HEAD") or "").split()
    scens = scenarios()
    if mode == "all":
        picked = [n for n, _, _, _ in scens]
    elif mode == "none":
        picked = []
    else:
        picked = reached(changed, scens, skill_modules())
    print(f"check_push: {head[:12]}, {len(changed)} changed file(s) {why}; "
          f"scenarios: {', '.join(picked) or 'none'}")
    if dry:
        return 0

    ok = run("every unpushed commit, commit level", [sys.executable, "check_commits.py", base])
    ok = run("the whole unit suite", [sys.executable, "run_tests.py"]) and ok
    if picked:
        ok = run(f"{len(picked)} scenario(s)",
                 [sys.executable, "e2e/drive.py", "verify", *picked]) and ok
    if not ok:
        print("\ncheck_push: FAILED — nothing recorded")
        return 1
    record_pass(tree, head, {"scenarios": picked, "since": why})
    print(f"\ncheck_push: {head[:12]} passed; recorded, the pre-push hook will let it through")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
