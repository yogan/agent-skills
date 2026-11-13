#!/usr/bin/env python3
"""The rig's GitLab container: up when something needs it, paused and then stopped when nothing
has for a while.

    python3 rig.py up          # unpause or start it, wait until GitLab answers, note the use
    python3 rig.py up --no-wait [--no-start]   # the same without waiting (tmux-demo.sh waits itself)
    python3 rig.py used        # note a use, nothing else
    python3 rig.py idle        # pause or stop it if unused long enough (what the idle check runs)
    python3 rig.py status
    python3 rig.py install     # the idle check: launchd runs `idle` every few minutes
    python3 rig.py uninstall

GitLab runs alone in a Colima VM of its own (profile `gitlab`), not beside other containers:
a stopped container's memory stays with the VM it ran in, and only stopping the VM gives it
back to macOS. Everything here talks to that VM's Docker context, whatever the person's own
default is; `E2E_DOCKER_CONTEXT` names another one.

Starting the VM and GitLab cold costs about two minutes, so it is not stopped soon after use.
A pause freezes GitLab in place: it costs no CPU and comes back at once, but keeps its memory.
So after `PAUSE_AFTER` without use it is paused, and only after `STOP_AFTER` (a night) is the
VM stopped, which frees the memory and costs the next use one boot.

A use is anything that calls `up` or `used`: `fixture.py`, `drive.py verify` on a rig
scenario, `tmux-demo.sh`. A demo or driver session still open in tmux counts as in use, so a
person trying a kept session is never frozen under their hands.
"""
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib import launchd  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CONTAINER = "e2e-gitlab"
PROFILE = "gitlab"
CONTEXT = os.environ.get("E2E_DOCKER_CONTEXT") or f"colima-{PROFILE}"
# For every docker call made here and by whatever this process starts (compose, bootstrap.py's
# `docker exec`): never the person's default context, which holds other things.
os.environ["DOCKER_CONTEXT"] = CONTEXT
URL = "http://gitlab.test/users/sign_in"   # /-/readiness is allowlisted to 127.0.0.1 only
STAMP = os.path.join(HERE, ".cache", "rig-last-used")
LOG = os.path.join(HERE, ".cache", "rig-idle.log")

PAUSE_AFTER = 60 * 60
STOP_AFTER = 8 * 60 * 60
CHECK_EVERY = 5 * 60
BOOT_TIMEOUT = 900

LABEL = "agent-skills.e2e-rig-idle"
PLIST = launchd.plist_path(LABEL)

# Mirrors drive.py's DEMO_SESSION and its `drive-<scenario>` sessions.
DEMO_SESSION = "agentic-review-skills-demo"


def docker(*args):
    return subprocess.run(["docker", *args], capture_output=True, text=True)


def vm_running():
    """Whether the GitLab VM is up. Only Colima's own profile is asked: another context named
    by E2E_DOCKER_CONTEXT is someone else's to start and stop, and counts as up."""
    if not CONTEXT.startswith("colima-"):
        return True
    try:
        r = subprocess.run(["colima", "status", "--profile", PROFILE], capture_output=True,
                           text=True)
    except FileNotFoundError:
        return False
    return r.returncode == 0


def state():
    """running, paused, exited, … or None when there is no such container (or no docker, or
    the VM is down)."""
    if not vm_running():
        return None
    try:
        r = docker("inspect", "-f", "{{.State.Status}}", CONTAINER)
    except FileNotFoundError:
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def note_use():
    launchd.note_use(STAMP)


def unused_for():
    return launchd.unused_for(STAMP)


def sessions_open():
    """A demo or a kept driver session in tmux: someone may be using the rig right now."""
    try:
        r = subprocess.run(["tmux", "ls", "-F", "#{session_name}"], capture_output=True, text=True)
    except FileNotFoundError:
        return False
    names = r.stdout.split() if r.returncode == 0 else []
    return any(n == DEMO_SESSION or n.startswith("drive-") for n in names)


def idle_action(unused, status, busy):
    """What `idle` does: "stop", "pause" or None."""
    if busy or status not in ("running", "paused"):
        return None
    if unused >= STOP_AFTER:
        return "stop"
    if unused >= PAUSE_AFTER and status == "running":
        return "pause"
    return None


def answering():
    try:
        with urllib.request.urlopen(URL, timeout=5) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError):
        return False


def up(wait=True, start=True, log=print):
    """Bring the container to running and note the use. False when it could not be."""
    note_use()
    if not vm_running():
        if not start:
            return False
        log(f"the {PROFILE} VM is stopped: starting it")
        r = subprocess.run(["colima", "start", "--profile", PROFILE], capture_output=True,
                           text=True)
        if r.returncode:
            log(r.stdout + r.stderr)
            return False
    s = state()
    if s == "paused":
        log(f"{CONTAINER} was paused: unpausing")
        docker("unpause", CONTAINER)
    elif s != "running":
        if not start:
            return False
        log(f"{CONTAINER} is {s or 'missing'}: starting it (a cold boot, about a minute or more)")
        r = subprocess.run(["docker", "compose", "up", "-d"], cwd=HERE, capture_output=True,
                           text=True)
        if r.returncode:
            log(r.stdout + r.stderr)
            return False
    if not wait:
        return True
    started = time.time()
    while not answering():
        if time.time() - started > BOOT_TIMEOUT:
            log(f"{URL} still not answering after {BOOT_TIMEOUT}s")
            return False
        time.sleep(3)
    return True


def idle(log=print):
    busy = sessions_open()
    if busy:
        note_use()
    s = state()
    action = idle_action(unused_for(), s, busy)
    if action == "pause":
        docker("pause", CONTAINER)
    elif action == "stop":
        if s == "paused":
            docker("unpause", CONTAINER)
        docker("stop", CONTAINER)
        if CONTEXT.startswith("colima-"):
            # The VM, not just the container: only that hands its memory back to macOS.
            subprocess.run(["colima", "stop", "--profile", PROFILE], capture_output=True)
    if action:
        log(f"{time.strftime('%Y-%m-%d %H:%M')} {action}: unused for {unused_for() / 3600:.1f}h")


def plist():
    """The idle check, as a launchd job: `idle` every CHECK_EVERY seconds."""
    return launchd.plist(LABEL, [os.path.abspath(__file__), "idle"], CHECK_EVERY, LOG,
                         ("docker", "tmux", "colima"))


def install():
    if not launchd.install(plist()):
        return 1
    print(f"installed {PLIST}: pauses {CONTAINER} after {PAUSE_AFTER // 60} min unused, stops "
          f"it after {STOP_AFTER // 3600} h; log {LOG}")
    return 0


def uninstall():
    print(f"removed {launchd.uninstall(LABEL)}")
    return 0


def main(argv):
    cmd = argv[0] if argv else "status"
    if cmd == "up":
        return 0 if up(wait="--no-wait" not in argv, start="--no-start" not in argv) else 1
    if cmd == "used":
        note_use()
        return 0
    if cmd == "idle":
        idle()
        return 0
    if cmd == "install":
        return install()
    if cmd == "uninstall":
        return uninstall()
    if cmd == "status":
        unused = unused_for()
        vm = "" if vm_running() else f" (the {PROFILE} VM is stopped)"
        print(f"{CONTAINER}: {state() or 'missing'}{vm}, last used "
              + ("never" if unused == float("inf") else f"{unused / 60:.0f} min ago")
              + (", idle check installed" if os.path.exists(PLIST) else ", idle check not installed"))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
