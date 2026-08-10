"""Derive the GitLab diff-between-versions URL for a topic's changes.

    …/merge_requests/<iid>/diffs?diff_id=<latest version>&start_sha=<head before the fix>

NEVER a commit URL: fixup + force-push rewrites commit hashes, so a commit link
rots. The versions API is newest-first; diff_id is the version your push created,
start_sha is the head of the version just before the topic's first push — so the
diff shows exactly the topic's change, even across several pushes.

Subcommands:
  baseline   print head_commit_sha of the current latest version.
             Capture this BEFORE a topic's first push and store it as the
             topic's start_sha (threads.py set <t> --start-sha <sha>).
  url        print the diff URL. --start-sha = the stored baseline (spans all of
             the topic's pushes). Omitted → uses the previous version's head
             (i.e. only the most recent single push).

Was skills/<name>/scripts/diff-url.py, byte-identical in both review-mr and
rework-mr — no per-skill logic here at all, unlike findings.py/threads.py, so it
moved here wholesale rather than being split by what's shared vs. duplicated. Each
skill keeps a thin diff-url.py shim so `$SD/diff-url.py` still works as documented in
its own SKILL.md.
"""
import argparse

from lib.gitlab import context, die, mr_object, mr_view, versions, web_base


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("baseline").add_argument("--iid", type=int)
    u = sub.add_parser("url")
    u.add_argument("--iid", type=int)
    u.add_argument("--start-sha",
                   help="head of the version before the topic's first push")
    args = ap.parse_args()

    ctx = context()
    iid = args.iid or mr_view()["iid"]
    vs = versions(ctx, iid)
    if not vs:
        die("no MR versions yet — push the branch first")

    if args.cmd == "baseline":
        print(vs[0]["head_commit_sha"])
        return

    start = args.start_sha
    if not start:
        if len(vs) < 2:
            die("only one version exists — capture and pass --start-sha")
        start = vs[1]["head_commit_sha"]
    # The MR's own web_url is authoritative for scheme/host/port/install path;
    # ctx["web"] is only a reconstruction from the remote. Fetched here, not at the
    # top, so the `baseline` subcommand stays a single API call.
    web = web_base(mr_object(ctx, iid).get("web_url")) or ctx["web"]
    print(f"{web}/-/merge_requests/{iid}/diffs"
          f"?diff_id={vs[0]['id']}&start_sha={start}")


if __name__ == "__main__":
    main()
