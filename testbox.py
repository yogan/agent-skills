#!/usr/bin/env python3
"""The test box: a Linux container in a Colima VM of its own, where the loop and commit levels
run most of the unit tests.

    python3 testbox.py status
    python3 testbox.py up          # start the VM, build the image, start the container
    python3 testbox.py idle        # stop the VM if unused long enough (what launchd runs)
    python3 testbox.py install     # that idle check, as a launchd job
    python3 testbox.py uninstall

**Why.** On the owner's Mac every process a test starts and every file it writes is inspected
by the security software. A test file that builds throwaway git repos took 12 to 19 seconds
there and half a second in Linux, with the same tests passing: the cost was the inspection, not
the tests. Linux in a VM is out of its sight.

**What runs here.** Only what is listed in `BOX`: the test files (and, for `test_threads.py`,
the test classes) verified to pass in Linux with nothing skipped. Everything else runs on the
Mac: the diagram and mod tests need its browser, fonts, d2 or the `claude` CLI, and some tests
need `glab`, `hunk` or `tmux`. A test file nobody listed runs on the Mac, so a new one is never
silently weaker; listing it is a speed-up to earn by running it here first.

**What it trades.** Inside Linux, git and the shell tools are Linux's, not the Mac's: a test
of a shell script could pass here and fail there. So the push level runs the whole suite on the
Mac (`run_tests.py --host`), once per top commit, before anything is pushed.

Each run copies the working tree (what git tracks, and what it would track: never ignored
files) into a folder of its own in the container, runs the listed tests there in parallel, and
removes the folder. Several runs at once (`check_commits.py`'s worktrees) each get their own.
"""
import hashlib
import json
import os
import subprocess
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from lib import launchd  # noqa: E402

PROFILE = "tests"
CONTEXT = f"colima-{PROFILE}"
CONTAINER = "agent-skills-tests"
VM_ARGS = ["--vm-type", "vz", "--cpu", "6", "--memory", "4", "--disk", "20", "--activate=false"]
DOCKERFILE = """FROM python:3.14-slim
RUN apt-get update -qq && apt-get install -y -qq git >/dev/null && rm -rf /var/lib/apt/lists/* \\
 && git config --global user.name box && git config --global user.email box@example.invalid \\
 && git config --global init.defaultBranch main
"""
IMAGE = f"agent-skills-tests:{hashlib.sha256(DOCKERFILE.encode()).hexdigest()[:12]}"
WORKERS = 6

STAMP = os.path.join(HERE, ".cache", "testbox-last-used")
LOG = os.path.join(HERE, ".cache", "testbox-idle.log")
LABEL = "agent-skills.testbox-idle"
STOP_AFTER = 8 * 60 * 60
CHECK_EVERY = 5 * 60

# Verified to pass in Linux with nothing skipped (each one run there). A path alone is the whole
# file; for a file listed in SPLIT, its classes run here except the ones named.
BOX = {
    "e2e/test_layout.py", "e2e/test_screenshot.py", "e2e/test_transcript.py", "e2e/test_rig.py",
    "hooks/test_paste_gate.py",
    "lib/diagram/gates/test_contrast.py", "lib/diagram/gates/test_gates.py",
    "lib/diagram/gates/test_size.py", "lib/diagram/gates/test_theming.py",
    "lib/diagram/test_callout.py", "lib/diagram/test_d2.py", "lib/diagram/test_figure.py",
    "lib/diagram/test_overview.py", "lib/diagram/test_palette.py", "lib/diagram/test_place.py",
    "lib/diagram/test_render.py", "lib/diagram/test_route.py", "lib/diagram/test_spec.py",
    "lib/test_change.py", "lib/test_checks.py", "lib/test_cli.py", "lib/test_code_view.py",
    "lib/test_critical_manifest.py", "lib/test_critical_manifest_clients.py",
    "lib/test_diff_url.py", "lib/test_diffsize.py", "lib/test_fences.py", "lib/test_fix_refs.py",
    "lib/test_focus.py", "lib/test_gitlab.py", "lib/test_glyphs.py", "lib/test_hunk.py",
    "lib/test_launchd.py", "lib/test_mr_common.py", "lib/test_parallel.py", "lib/test_posted.py",
    "lib/test_snippet.py", "lib/test_tmux.py", "lib/test_ui.py", "lib/test_worddiff.py",
    "skills/explain-diff/scripts/test_resolve_target.py",
    "skills/rework-mr/scripts/test_threads.py",
    "skills/self-review/scripts/test_selfreview.py",
    "skills/split-handoff/scripts/test_spinoff.py",
    "test_check_commits.py", "test_compare_figures.py", "test_fails_without.py",
    "test_measure_checks.py", "test_measure_speed.py", "test_private_terms.py",
    "test_show_limits.py", "test_testbox.py",
}
# Classes of a listed file that need the Mac: `hunk` inside tmux, and a check that runs it.
SPLIT = {"skills/rework-mr/scripts/test_threads.py": {"TestFixAgainstThePlan", "TestPushFix"}}


def in_box(rel, klass):
    """Whether the job (a file, or one class of it) runs in the box."""
    if rel not in BOX:
        return False
    if rel in SPLIT:
        return klass is not None and klass not in SPLIT[rel]
    return True


def docker(*args, **kw):
    return subprocess.run(["docker", "--context", CONTEXT, *args], capture_output=True,
                          text=True, **kw)


def vm_running():
    try:
        r = subprocess.run(["colima", "status", "--profile", PROFILE], capture_output=True,
                           text=True)
    except FileNotFoundError:
        return False
    return r.returncode == 0


def usable():
    """Whether a box can be had here at all: Colima and docker installed, not switched off."""
    if os.environ.get("TESTBOX") == "off":
        return False
    from shutil import which
    return bool(which("colima") and which("docker"))


def up(log=lambda m: print(m, file=sys.stderr)):
    """VM, image and container ready. False with a reason logged when they cannot be."""
    launchd.note_use(STAMP)
    if not vm_running():
        log(f"test box: starting the {PROFILE} VM (once; it stops after {STOP_AFTER // 3600} h unused)")
        r = subprocess.run(["colima", "start", "--profile", PROFILE, *VM_ARGS],
                           capture_output=True, text=True)
        if r.returncode:
            log(f"test box: the VM did not start: {r.stderr.strip()[-300:]}")
            return False
    if docker("image", "inspect", IMAGE).returncode:
        log("test box: building its image (once)")
        r = docker("build", "-q", "-t", IMAGE, "-", input=DOCKERFILE)
        if r.returncode:
            log(f"test box: the image did not build: {r.stderr.strip()[-300:]}")
            return False
    state = docker("inspect", "-f", "{{.State.Running}}|{{.Config.Image}}", CONTAINER).stdout.strip()
    if state != f"true|{IMAGE}":
        docker("rm", "-f", CONTAINER)
        r = docker("run", "-d", "--name", CONTAINER, "--restart", "unless-stopped", IMAGE,
                   "sleep", "infinity")
        if r.returncode:
            log(f"test box: the container did not start: {r.stderr.strip()[-300:]}")
            return False
    return True


def tree_tar(root):
    """The working tree as git sees it, as a tar: tracked files and untracked ones not ignored."""
    names = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd=root,
                           capture_output=True).stdout
    present = b"\0".join(n for n in names.split(b"\0")
                         if n and os.path.exists(os.path.join(root, n.decode())))
    return subprocess.run(["tar", "--no-xattrs", "--null", "-cf", "-", "-T", "-"], cwd=root,
                          input=present, capture_output=True,
                          env={**os.environ, "COPYFILE_DISABLE": "1"}).stdout


def run(jobs, root):
    """Run `jobs` ([(relative path, class or None)]) in the box on a copy of `root`.

    Returns [(rel, klass, returncode, stdout, stderr, seconds)] in the order given, or None when
    the box could not run them (the caller then runs them on the Mac)."""
    if not jobs:
        return []
    work = f"/w/{uuid.uuid4().hex[:12]}"
    spec = json.dumps([[rel, klass] for rel, klass in jobs])
    script = (f"mkdir -p {work} && tar -xf - -C {work} 2>/dev/null && cd {work} && "
              f"python3 testbox.py inner {WORKERS} '{spec}'; rc=$?; cd / && rm -rf {work}; exit $rc")
    r = subprocess.run(["docker", "--context", CONTEXT, "exec", "-i", CONTAINER, "sh", "-c",
                        script], input=tree_tar(root), capture_output=True)
    try:
        out = json.loads(r.stdout.decode("utf-8", "replace").strip().splitlines()[-1])
    except (ValueError, IndexError):
        print(f"test box: no answer ({r.stderr.decode('utf-8', 'replace').strip()[-300:]})",
              file=sys.stderr)
        return None
    return [tuple(x) for x in out]


def inner(workers, spec):
    """Inside the box: run the jobs in parallel the way the runner does, answer in one JSON line."""
    import time
    from concurrent.futures import ThreadPoolExecutor
    env = {k: v for k, v in os.environ.items()}

    def one(job):
        rel, klass = job
        started = time.monotonic()
        p = subprocess.run([sys.executable, rel] + ([klass] if klass else []),
                           capture_output=True, text=True, env=env)
        return [rel, klass, p.returncode, p.stdout, p.stderr, time.monotonic() - started]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        print(json.dumps(list(pool.map(one, json.loads(spec)))))
    return 0


def idle():
    if vm_running() and launchd.unused_for(STAMP) >= STOP_AFTER:
        subprocess.run(["colima", "stop", "--profile", PROFILE], capture_output=True)
        import time
        print(f"{time.strftime('%Y-%m-%d %H:%M')} stopped the {PROFILE} VM")


def main(argv):
    cmd = argv[0] if argv else "status"
    if cmd == "inner":
        return inner(int(argv[1]), argv[2])
    if cmd == "up":
        return 0 if up() else 1
    if cmd == "idle":
        idle()
        return 0
    if cmd == "install":
        ok = launchd.install(launchd.plist(LABEL, [os.path.abspath(__file__), "idle"],
                                           CHECK_EVERY, LOG, ("docker", "colima")))
        print(f"installed the test box's idle check: stops its VM after {STOP_AFTER // 3600} h "
              "unused" if ok else "launchd did not take the job")
        return 0 if ok else 1
    if cmd == "uninstall":
        print(f"removed {launchd.uninstall(LABEL)}")
        return 0
    if cmd == "status":
        unused = launchd.unused_for(STAMP)
        print(f"test box: VM {'running' if vm_running() else 'stopped'}, last used "
              + ("never" if unused == float("inf") else f"{unused / 60:.0f} min ago")
              + (", idle check installed" if launchd.installed(LABEL) else ", idle check not installed"))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
