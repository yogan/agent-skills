"""Whether a run of the MR skills' scripts is the person's press on a review-ui button.

Posting, resolving, approving, merging, cleaning up and pushing happen only on a press. A
constant the press's environment carried would not say so: the model runs its shell commands
with whatever environment it writes, so anything it can read in the source it can also set.

**How a press proves itself.** For each press the mod (mods/review-ui) draws a random secret,
writes an empty file named after the secret's SHA-256 into `PRESSES`, and runs the script
with the secret in `ENV`, in that one process's environment and never on a command line. The
script hashes what it got and removes the file of that name: the file was there, so the
secret came from the mod. Removed, it proves nothing a second time. The model sees neither the
secret nor anything it could be computed from: the file names are hashes.
"""
import hashlib
import os
import time

ENV = "REVIEW_UI_PRESS"
PRESSES = os.path.expanduser("~/.claude/review-ui/presses")
# A press the script never came to claim (it failed to start, or died before its check)
# leaves its file behind; one older than this is cleared by the next claim. Longer than any
# press runs, a push included.
STALE_SECONDS = 24 * 3600

_claimed = {}


def claimed():
    """True when this process runs on a press of the mod's, checked once and then spent: the
    first call removes the press's file, and later calls in the same process answer the same."""
    secret = os.environ.get(ENV) or ""
    if secret not in _claimed:
        _claimed[secret] = bool(secret) and _spend(secret)
    return _claimed[secret]


def _spend(secret):
    name = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    try:
        os.remove(os.path.join(PRESSES, name))
    except OSError:
        return False
    _clear_stale()
    return True


def _clear_stale(now=None):
    """Remove each press file left unclaimed for STALE_SECONDS, by its own name: nothing else
    in the folder is touched."""
    now = time.time() if now is None else now
    try:
        names = os.listdir(PRESSES)
    except OSError:
        return
    for name in names:
        if len(name) != 64 or any(c not in "0123456789abcdef" for c in name):
            continue
        path = os.path.join(PRESSES, name)
        try:
            if now - os.path.getmtime(path) > STALE_SECONDS:
                os.remove(path)
        except OSError:
            pass
