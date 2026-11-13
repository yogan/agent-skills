"""A periodic job for the person's own launchd (macOS), and the stamp a job reads to tell how long
something has gone unused. Shared by the rig's idle check (`e2e/rig.py`) and the test box's
(`testbox.py`): each pauses or stops a VM nobody has used for a while.
"""
import os
import plistlib
import shutil
import subprocess
import sys
import time


def note_use(stamp):
    os.makedirs(os.path.dirname(stamp), exist_ok=True)
    with open(stamp, "a"):
        pass
    os.utime(stamp)


def unused_for(stamp):
    """Seconds since the last use; never used counts as unused since forever."""
    try:
        return time.time() - os.path.getmtime(stamp)
    except OSError:
        return float("inf")


def plist_path(label):
    return os.path.expanduser(f"~/Library/LaunchAgents/{label}.plist")


def plist(label, argv, every, log, tools):
    """A job running `argv` every `every` seconds, with a PATH that finds `tools` (launchd starts
    its jobs with almost none)."""
    dirs = []
    for tool in tools:
        found = shutil.which(tool)
        if found and os.path.dirname(found) not in dirs:
            dirs.append(os.path.dirname(found))
    return {
        "Label": label,
        "ProgramArguments": [sys.executable, *argv],
        "StartInterval": every,
        "RunAtLoad": True,
        "EnvironmentVariables": {"PATH": ":".join(dirs + ["/usr/bin", "/bin"])},
        "StandardOutPath": log,
        "StandardErrorPath": log,
    }


def install(job):
    """Install (or replace) the job `plist` built. True when launchd took it."""
    path = plist_path(job["Label"])
    os.makedirs(os.path.dirname(job["StandardOutPath"]), exist_ok=True)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", domain, path], capture_output=True)
    with open(path, "wb") as fh:
        plistlib.dump(job, fh)
    r = subprocess.run(["launchctl", "bootstrap", domain, path], capture_output=True, text=True)
    if r.returncode:
        print(r.stderr.strip(), file=sys.stderr)
    return r.returncode == 0


def uninstall(label):
    path = plist_path(label)
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", path], capture_output=True)
    if os.path.exists(path):
        os.remove(path)
    return path


def installed(label):
    return os.path.exists(plist_path(label))
