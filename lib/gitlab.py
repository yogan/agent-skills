"""Shared GitLab / glab helpers for review-mr and rework-mr.

glab-only: no MCP dependency, so both skills work anywhere glab is set up. Previously
duplicated per skill as `_gl.py` — genuinely identical apart from docstring framing
(each skill emphasized the field it cared about) and `mr_head`, which only review-mr
used. Folded into lib/ wholesale rather than split, since a "talk to GitLab" module is
one cohesive concern, not two — see CLAUDE.md's "Sharing vs. duplication".
"""
import json
import subprocess
from urllib.parse import quote

from lib.cli import die


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


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


def current_user():
    """glab-authenticated username — the reviewer in review-mr's flow, the MR
    author in rework-mr's."""
    r = run(["glab", "api", "user"])
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout).get("username")
    except json.JSONDecodeError:
        return None
