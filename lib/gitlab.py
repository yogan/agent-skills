"""Shared GitLab / glab helpers for review-mr and rework-mr.

glab-only: no MCP dependency, so both skills work anywhere glab is set up. Previously
duplicated per skill as `_gl.py` — genuinely identical apart from docstring framing
(each skill emphasized the field it cared about) and `mr_head`, which only review-mr
used. Folded into lib/ wholesale rather than split, since a "talk to GitLab" module is
one cohesive concern, not two — see CLAUDE.md's "Sharing vs. duplication".
"""
import functools
import json
import subprocess
from urllib.parse import quote

from lib.cli import die


# How long a GitLab call may take before it counts as failed, in seconds. Without a limit a
# call that never answers (a network gone, a VPN half up) held a view, a press or the mod's
# update check for good, and nothing on screen said why.
GLAB_TIMEOUT = 60
TIMED_OUT = 124       # the exit status a call that ran out of time is given, as timeout(1) does


def run(cmd, input=None):
    """`cmd`'s result, its output as text. A `glab` call that runs out of time comes back as a
    failure (`TIMED_OUT`) that says so, which every caller already handles."""
    timeout = GLAB_TIMEOUT if cmd and cmd[0] == "glab" else None
    try:
        return subprocess.run(cmd, capture_output=True, text=True, input=input, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            cmd, TIMED_OUT, "", f"GitLab did not answer within {GLAB_TIMEOUT} seconds")


def remote_url():
    r = run(["git", "remote", "get-url", "origin"])
    if r.returncode != 0:
        die("not a git repo, or no 'origin' remote")
    return r.stdout.strip()


def parse_remote(url):
    """Return (host, path) with any '.git' suffix and credentials stripped."""
    u = url[:-4] if url.endswith(".git") else url
    if "://" in u:
        rest = u.split("://", 1)[1]
        if "@" in rest.split("/", 1)[0]:
            rest = rest.split("@", 1)[1]
        host, path = rest.split("/", 1)
    elif "@" in u and ":" in u:
        host, path = u.split("@", 1)[1].split(":", 1)
    else:
        die(f"cannot parse remote url: {url}")
    return host, path.strip("/")


def api_protocol(host):
    """Scheme glab uses for `host`. NEVER derive this from the remote URL: an SSH
    remote carries no scheme at all, and `git_protocol=ssh` with
    `api_protocol=https` is a perfectly normal setup — so the remote is a guess
    while glab's own config is the answer. Falls back to https."""
    r = run(["glab", "config", "get", "api_protocol", "--host", host])
    p = r.stdout.strip() if r.returncode == 0 else ""
    return p if p in ("http", "https") else "https"


def web_base(web_url):
    """Project web base from an MR's own `web_url` — the authoritative source, since
    it carries scheme, host, port and any install path. Returns None if unusable."""
    marker = "/-/merge_requests/"
    if web_url and marker in web_url:
        return web_url.split(marker, 1)[0]
    return None


def project_slug():
    """The name this project's state directory is keyed by — from the git remote alone.

    Split out of `context()` because that one also builds `web`, which asks glab for the
    host's API scheme: a subprocess and a config read, on a helper with no timeout. Callers
    that only need to find the state directory — naming a viewer window, say — must not pay
    a glab round-trip per render for it, nor hang when glab does.
    """
    return parse_remote(remote_url())[1].replace("/", "-").replace(".", "-")


def context():
    host, path = parse_remote(remote_url())
    return {
        "path": path,
        "enc": quote(path, safe=""),
        "web": f"{api_protocol(host)}://{host}/{path}",
        "slug": path.replace("/", "-").replace(".", "-"),
    }


def api(endpoint, paginate=False):
    cmd = ["glab", "api", endpoint]
    if paginate:
        cmd.append("--paginate")
    r = run(cmd)
    if r.returncode != 0:
        die(f"glab api {endpoint} failed: {r.stderr.strip() or r.stdout.strip()}")
    out = r.stdout.strip()
    if not out:
        return []
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        # --paginate concatenates one JSON array per page: "][" -> ","
        return json.loads(out.replace("][", ","))


def api_post(endpoint, body, method="POST"):
    """Send `body` (a dict, nested as the API takes it) to `endpoint` as JSON, the parsed
    answer back. Dies on failure.

    The only writes these skills make, and each one is a person's press: posting a drafted
    comment, a reply, a resolve. Nothing calls this unasked.
    """
    cmd = ["glab", "api", "-X", method, "-H", "Content-Type: application/json",
           endpoint, "--input", "-"]
    r = run(cmd, input=json.dumps(body))
    if r.returncode == TIMED_OUT:
        # A write that ran out of time may have been made all the same: posting it again could
        # post it twice.
        die(f"glab api {method} {endpoint}: {r.stderr} — it may or may not have gone through: "
            "check on GitLab before trying again")
    if r.returncode != 0:
        die(f"glab api {method} {endpoint} failed: {r.stderr.strip() or r.stdout.strip()}")
    out = r.stdout.strip()
    return json.loads(out) if out else {}


@functools.lru_cache(maxsize=64)
def file_at(project, sha, path):
    """The text of `path` at commit `sha` as GitLab holds it, or None — never fatal.

    For a commit the local clone no longer has: a force-push drops the version a review
    comment was made on from every branch, and `git fetch` brings back branch heads only.
    GitLab keeps any commit a comment is anchored to, so the file the comment saw can
    still be read from it. Cached because a commit never changes, and a view renders the
    same thread more than once (the block, then the fingerprint `--refine` compares).
    """
    if not (project and sha and path):
        return None
    r = run(["glab", "api", f"projects/{quote(project, safe='')}/repository/files/"
             f"{quote(path, safe='')}/raw?ref={sha}"])
    return r.stdout if r.returncode == 0 else None


def mr_view():
    r = run(["glab", "mr", "view", "--output", "json"])
    if r.returncode != 0:
        die("no MR for the current branch (glab mr view failed) — pass --iid")
    return json.loads(r.stdout)


def mr_object(ctx, iid):
    """Full MR object by iid — carries the authoritative `web_url` and
    `diff_refs.head_sha` (the branch tip, for detecting an author push/force-push
    against a stored last-reviewed head). Fetching by iid never misfires on the
    ambient branch the way `glab mr view` can."""
    return api(f"projects/{ctx['enc']}/merge_requests/{iid}")


def mr_status(ctx, iid, me=None, mr=None):
    """Where the MR stands as a whole, for the review-ui mod's heading and inbox: open,
    merged or closed (with who and when), whether auto-merge is set, and who approved it
    (`me` marks the user's own approval). Approvals are read where the instance has them;
    without, the list is empty rather than the check failing."""
    mr = mr or mr_object(ctx, iid)
    r = run(["glab", "api", f"projects/{ctx['enc']}/merge_requests/{iid}/approvals"])
    try:
        ap = json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else {}
    except json.JSONDecodeError:
        ap = {}
    users = [x.get("user") or {} for x in ap.get("approved_by") or []]
    return {
        "state": mr.get("state"),
        "merged_at": mr.get("merged_at"),
        "merged_by": (mr.get("merged_by") or mr.get("merge_user") or {}).get("name"),
        "closed_at": mr.get("closed_at"),
        "closed_by": (mr.get("closed_by") or {}).get("name"),
        "auto_merge": bool(mr.get("merge_when_pipeline_succeeds") or mr.get("auto_merge_enabled")),
        "approved_by": [{"name": u.get("name"), "me": bool(me) and u.get("username") == me} for u in users],
        "approvals_left": ap.get("approvals_left"),
    }


def mr_head(ctx, iid):
    """Current branch-tip SHA of the MR (survives force-push: it's whatever the
    tip is now, not a commit count)."""
    mr = mr_object(ctx, iid)
    refs = mr.get("diff_refs") or {}
    return refs.get("head_sha") or mr.get("sha")


def mr_base(ctx, iid):
    """GitLab's own merge-base for this MR's latest diff — the authoritative scope
    boundary. Deliberately NOT re-derived locally (e.g. `git merge-base HEAD
    origin/main`): that guess silently drifts from GitLab's answer whenever the
    target isn't main/master, or the locally known target-branch tip is behind
    where the MR actually forked from, sweeping unrelated already-on-target
    commits into what looks like the MR's diff."""
    mr = mr_object(ctx, iid)
    refs = mr.get("diff_refs") or {}
    return refs.get("base_sha")


def versions(ctx, iid):
    """MR diff versions, newest first — one per push (survives force-push). Used to
    derive a stable diff-between-versions URL (see lib/diff_url.py) and, in review-mr's
    findings.py, to detect a rebase between two of the reviewer's baselines."""
    return api(f"projects/{ctx['enc']}/merge_requests/{iid}/versions?per_page=100",
               paginate=True)


def remember_user(state, me):
    """`me` (a `current_user()` answer) kept in `state`, or the one kept there before when
    GitLab could not say. Every comment's "yours or theirs" comes from it, so one failed
    lookup would otherwise turn the user's own replies into someone else's for that run: news
    that is not news, and every plan agreed on the thread out of date."""
    if me:
        state["me"] = me
    return me or state.get("me")


def current_user():
    """glab-authenticated username — the reviewer in review-mr's flow, the MR
    author in rework-mr's. None when GitLab cannot say: see `remember_user`."""
    r = run(["glab", "api", "user"])
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout).get("username")
    except json.JSONDecodeError:
        return None
