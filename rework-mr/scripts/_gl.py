"""Shared GitLab / glab helpers for the rework-mr skill.

glab-only: no MCP dependency, so the skill works anywhere glab is set up.
"""
import json
import subprocess
import sys
from urllib.parse import quote


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def die(msg):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


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


def context():
    host, path = parse_remote(remote_url())
    return {
        "path": path,
        "enc": quote(path, safe=""),
        "web": f"https://{host}/{path}",
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


def current_user():
    """glab-authenticated username (the MR author, in this skill's flow)."""
    r = run(["glab", "api", "user"])
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout).get("username")
    except json.JSONDecodeError:
        return None
