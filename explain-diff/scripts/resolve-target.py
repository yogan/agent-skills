#!/usr/bin/env python3
"""Resolve an explain-diff target spec into a diff range.

Usage: resolve-target.py [spec]
  spec="":              current branch since it diverged from origin/main|master
  spec="branch:<name>":  a local or remote branch since it diverged from origin/main|master
  spec="mr:<number>":     a GitLab MR's source branch since it diverged from its target branch (via glab)
  spec="commit:<ref>":    a single commit's own diff (ref defaults to HEAD, i.e. "last commit")

Prints shell-sourceable variables: BASE, HEAD_REF, LABEL, IS_SINGLE_COMMIT, MR_NUM, MR_URL, MR_TITLE
MR_NUM/MR_URL/MR_TITLE are only populated for an mr: spec; empty otherwise.
"""

import json
import re
import shlex
import shutil
import subprocess
import sys


def die(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def run(args: list) -> str:
    """Run a command, return stripped stdout. Raises CalledProcessError on non-zero exit."""
    return subprocess.run(args, capture_output=True, text=True, check=True).stdout.strip()


def run_ok(args: list) -> bool:
    return subprocess.run(args, capture_output=True, text=True).returncode == 0


def find_main_ref() -> str:
    result = subprocess.run(["git", "branch", "-r"], capture_output=True, text=True)
    for line in result.stdout.splitlines():
        name = line.strip()
        if name in ("origin/main", "origin/master"):
            return name
    return ""


def merge_base(head_ref: str, main_ref: str) -> str:
    return run(["git", "merge-base", head_ref, main_ref])


def resolve_blank() -> dict:
    main_ref = find_main_ref()
    if not main_ref:
        die("Cannot find origin/main or origin/master. Are you in a git repo with a remote?")

    head_ref = run(["git", "rev-parse", "HEAD"])
    base = merge_base(head_ref, main_ref)

    branch_result = subprocess.run(
        ["git", "symbolic-ref", "--short", "HEAD"], capture_output=True, text=True
    )
    if branch_result.returncode == 0:
        branch_name = branch_result.stdout.strip()
    else:
        tag_result = subprocess.run(
            ["git", "describe", "--exact-match", "--tags", "HEAD"],
            capture_output=True,
            text=True,
        )
        branch_name = (
            tag_result.stdout.strip() if tag_result.returncode == 0 else f"detached@{head_ref[:7]}"
        )

    return {"BASE": base, "HEAD_REF": head_ref, "LABEL": branch_name, "IS_SINGLE_COMMIT": "false"}


def resolve_branch(value: str) -> dict:
    if not value:
        die("branch spec requires a name, e.g. branch:feat/foo")

    main_ref = find_main_ref()
    if not main_ref:
        die("Cannot find origin/main or origin/master.")

    subprocess.run(["git", "fetch", "origin", value, "--quiet"], capture_output=True)

    if run_ok(["git", "show-ref", "--verify", "--quiet", f"refs/remotes/origin/{value}"]):
        head_ref = f"origin/{value}"
    elif run_ok(["git", "show-ref", "--verify", "--quiet", f"refs/heads/{value}"]):
        head_ref = value
    else:
        die(f"Branch '{value}' not found locally or on origin.")

    base = merge_base(head_ref, main_ref)
    return {"BASE": base, "HEAD_REF": head_ref, "LABEL": value, "IS_SINGLE_COMMIT": "false"}


def resolve_mr(value: str) -> dict:
    num = "".join(ch for ch in value if ch.isdigit())
    if not num:
        die("mr spec requires a number, e.g. mr:123")

    if not shutil.which("glab"):
        die("glab CLI not found. Install/auth glab to resolve MRs (see README).")

    mr_result = subprocess.run(
        ["glab", "mr", "view", num, "-F", "json"], capture_output=True, text=True
    )
    if mr_result.returncode != 0:
        combined = (mr_result.stdout + mr_result.stderr).strip()
        die(f"glab could not fetch MR !{num}: {combined}")

    mr = json.loads(mr_result.stdout)
    src = mr.get("source_branch", "")
    tgt = mr.get("target_branch", "")
    title = mr.get("title", "")
    web_url = mr.get("web_url", "")

    if not src or not tgt:
        die(f"Could not parse source/target branch for MR !{num}.")

    subprocess.run(["git", "fetch", "origin", src, tgt, "--quiet"], capture_output=True)
    head_ref = f"origin/{src}"
    base = merge_base(head_ref, f"origin/{tgt}")
    label = f"mr-{num}-{title or src}"

    return {
        "BASE": base,
        "HEAD_REF": head_ref,
        "LABEL": label,
        "IS_SINGLE_COMMIT": "false",
        "MR_NUM": num,
        "MR_URL": web_url,
        "MR_TITLE": title,
    }


def resolve_commit(value: str) -> dict:
    ref = value or "HEAD"

    if not run_ok(["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"]):
        die(f"Commit '{ref}' not found.")

    head_ref = run(["git", "rev-parse", ref])

    base_result = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{ref}^"], capture_output=True, text=True
    )
    if base_result.returncode != 0:
        die(f"Commit '{ref}' has no parent (nothing to diff against).")

    return {
        "BASE": base_result.stdout.strip(),
        "HEAD_REF": head_ref,
        "LABEL": f"commit-{head_ref[:7]}",
        "IS_SINGLE_COMMIT": "true",
    }


def slugify(label: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", label).strip("-")


def main() -> None:
    spec = sys.argv[1] if len(sys.argv) > 1 else ""
    kind, _sep, value = spec.partition(":")

    resolvers = {
        "": resolve_blank,
        "branch": lambda: resolve_branch(value),
        "mr": lambda: resolve_mr(value),
        "commit": lambda: resolve_commit(value),
    }

    resolver = resolvers.get(kind)
    if resolver is None:
        die(f"Unknown spec kind '{kind}'. Use branch:, mr:, commit:, or leave blank for current branch.")

    result = resolver()

    output = {
        "BASE": result["BASE"],
        "HEAD_REF": result["HEAD_REF"],
        "LABEL": slugify(result["LABEL"]),
        "IS_SINGLE_COMMIT": result["IS_SINGLE_COMMIT"],
        "MR_NUM": result.get("MR_NUM", ""),
        "MR_URL": result.get("MR_URL", ""),
        "MR_TITLE": result.get("MR_TITLE", ""),
    }

    # shlex.quote (not naive quoting): MR_TITLE can contain apostrophes/quotes that would
    # otherwise break sourcing the output as shell.
    for name, val in output.items():
        print(f"{name}={shlex.quote(val)}")


if __name__ == "__main__":
    main()
