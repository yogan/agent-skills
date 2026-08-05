# E2E / demo rig

A local GitLab CE with a seeded project, three MRs and pre-seeded review conversations, for
demoing and end-to-end testing `review-mr` and `rework-mr` without touching a real instance.

Design decisions and rationale: [PLAN.md](PLAN.md).

## Layout

| File | Purpose |
|---|---|
| `docker-compose.yml` | GitLab CE (arm64-native), `http://gitlab.test`, no CI runner |
| `bootstrap.py` | one-time: users, deterministic tokens, group, project, `glab` auth |
| `fixture.py` | resettable: branches, the three MRs, seeded threads, skill state |
| `.env.local` | generated token/host values (gitignored) |

## One-time setup

1. **hosts entry** (needs sudo, so do it yourself):

   ```sh
   sudo sh -c 'echo "127.0.0.1 gitlab.test" >> /etc/hosts'
   ```

2. **start GitLab** — first boot is 3-5 min:

   ```sh
   cd e2e && docker compose up -d
   ```

3. **bootstrap** — waits for readiness, then creates everything:

   ```sh
   python3 bootstrap.py
   ```

   Idempotent: safe to re-run — it reuses any token in `.env.local` that still
   authenticates. The root token is deterministic (minted via `gitlab-rails`); the user
   tokens are minted through the admin API, which returns a value only once, so they are
   persisted to `.env.local` rather than re-derived.

### Accounts

| User | Role | Login |
|---|---|---|
| `root` | admin — bootstrap only | `root` / `Rk8xQ2-mT9zV4-pB6nL1` |
| `frank` | **the live identity** — both skills, browser, `glab` | `frank` / `Ud5jH3-wN7cY2-qF4tM8` |
| `author-bot` | authors MR !1, !2; replies in !2's threads | fixture only |
| `reviewer-bot` | posts the review threads on MR !3 | fixture only |
| `peer-bot` | one extra thread on MR !2 | fixture only |

Display names use the enterprise `Last, First - ID` shape on purpose — `findings.py:131` parses
exactly that, so the fixture exercises the real path.

## Reset before every run

```sh
python3 fixture.py          # < 60 s, idempotent, offline
```

Deletes and recreates the *project* (that is what restarts MR IIDs at 1), re-pushes branches,
recreates the MRs, seeds threads, re-clones the local working copy, and wipes skill state under
`~/.claude/review-mr/` and `~/.claude/rework-mr/`.

## Talk day

Do **not** `docker compose down` — keep the container warm. Run `fixture.py` immediately before
the talk. Run-of-show: see [PLAN.md](PLAN.md) §6.

## Troubleshooting

```sh
docker compose logs -f gitlab                    # boot progress
docker exec e2e-gitlab gitlab-ctl status
curl -sS -o /dev/null -w '%{http_code}\n' http://gitlab.test/users/sign_in   # want 200
docker exec e2e-gitlab gitlab-psql -t -c 'select username from users'
glab api user                                    # should print frank
```

### Gotchas hit while building this (all fixed in the scripts, kept as a record)

| Symptom | Cause |
|---|---|
| container crash-loops, `Reading unsupported config value grafana` | omnibus **rejects unknown keys** as a hard failure. `grafana`, `mattermost`, `gitlab_ci` were all removed in 16.x/17.x. Keep `GITLAB_OMNIBUS_CONFIG` conservative |
| `/-/readiness` always 404 | that endpoint is IP-allowlisted to `127.0.0.1`; from the host you arrive via the docker gateway. Probe `/users/sign_in` instead |
| **whole VM dies**, `Cannot connect to the Docker daemon` | `gitlab-rails runner` boots a second Rails app (~1.5 GB) on top of puma+sidekiq. In a 6 GB Rancher VM that OOM-kills the VM, not just the container. `bootstrap.py` stops puma+sidekiq around its single rails call |
| `users` table empty, `no root user` | the first-boot seed died in that same OOM, and `gitlab-ctl reconfigure` will **not** retry it (its cache says done). Force it: `docker exec -e GITLAB_ROOT_PASSWORD=… e2e-gitlab gitlab-rake db:seed_fu` |
| seed prints `Password must not contain commonly used combinations of words and letters` | GitLab 19 strength-checks passwords, and the admin seed then **silently** creates no user. Passwords here are deliberately opaque |
| `Validation failed: Namespace can't be blank` | creating users with `User.new(...).save!` in rails skips personal-namespace creation. Use the REST API, which goes through GitLab's own service |

### VM memory

The rig ran in a **6 GB / 2 CPU** Rancher Desktop VM with Kubernetes enabled, but steady-state
GitLab sits at ~3.1 GiB with only ~1.3 GiB available. That is enough for normal operation
(git pushes and API calls are cheap; `review-branch` and `explain-branch` run on the host) but
it leaves no headroom for a rails spike.

For talk-day safety, consider raising the VM to 10-12 GB:

```sh
rdctl set --virtual-machine.memory-in-gb 12      # restarts the VM
```

Not done automatically — it restarts the VM and therefore every other container in it.
