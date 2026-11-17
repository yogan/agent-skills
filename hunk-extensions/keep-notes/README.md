# keep-notes

A [Hunk](https://hunk.dev) extension that keeps the notes in a Hunk viewer, and the list of
files it loaded, on disk: saved the moment a note is saved, edited or deleted, or the review
is loaded again.

Hunk keeps notes in memory only, and a program that reads a viewer from outside, with
`hunk session …`, misses what an extension hides:

- a note written just before the viewer was closed, because the notes go with the viewer;
- a note on a file that is not in the diff right now, such as a file that
  [hunk-mark-read](https://github.com/meldron/hunk-mark-read) hides as read. Hunk still
  holds the note and shows it again once the file is shown, but `comment list` leaves it out;
- the hidden file itself: `session review` and the file count of `session reload` leave it
  out too, so the viewer looks as if it held less than it was asked to load.

This extension keeps a record that has all three.

## Where it saves

In every viewer, into `$XDG_STATE_HOME/hunk-keep-notes/`, which is
`~/.local/state/hunk-keep-notes/` when `XDG_STATE_HOME` is not set (hunk-mark-read keeps its
marks beside it). The viewer's environment can change that:

| Variable | Effect |
| --- | --- |
| `HUNK_KEEP_NOTES_DIR=/some/dir` | save there instead; an absolute path, a relative one is ignored |
| `HUNK_KEEP_NOTES=off` | save nothing |
| `HUNK_KEEP_NOTES_TAG=text` | put `text` in the file as `tag`, so whoever opened the viewer can tell its files apart |

The skills in this repository read the files through `lib/hunk.py`, and tag every viewer
they open with the agent session that opened it.

## What it writes

One file per viewer process, `<pid>-<process start in ms>.json`, so two viewers never write
the same file and a new viewer never overwrites the notes of a closed one. The file is
rewritten whole on every change (a temporary file, then a rename). When the viewer closes,
the list of files goes, and the file with it if no note is left. Directory and files are
readable by you alone.

```json
{
 "version": 1,
 "pid": 4242,
 "startedAt": "2026-01-02T03:04:05.000Z",
 "cwd": "/work/demo",
 "repoRoot": "/work/demo",
 "tag": null,
 "updatedAt": "2026-01-02T03:05:00.000Z",
 "review": {
  "sourceLabel": "/work/demo",
  "loadedAt": 1767323100000,
  "files": [
   { "path": "alpha.txt", "additions": 1, "deletions": 1, "hidden": true },
   { "path": "beta.txt", "additions": 1, "deletions": 1, "hidden": false }
  ]
 },
 "notes": [
  { "id": "user:1700000000000-1", "source": "user", "file": "alpha.txt",
    "fileKey": "file:d883e58aa1dbe122204b7aea6429f576", "side": "new", "line": 1, "end": 1,
    "text": "stop, do not push", "createdAt": "2026-01-02T03:04:05.000Z" },
  { "id": "mcp:5", "parentId": "user:1700000000000-1", "source": "agent", "author": "agent",
    "file": "alpha.txt", "fileKey": "file:d883e58aa1dbe122204b7aea6429f576", "side": "new",
    "line": 1, "end": 1, "text": "it is on purpose", "createdAt": "2026-01-02T03:04:40.000Z" }
 ]
}
```

`repoRoot` is the git repository the viewer was started in (null outside one), asked once
at the first save.

`review` is the latest load: every file Hunk loaded, with `hidden` set on those another
extension's filter left out of what the viewer shows. `lib/hunk.py` reads it to tell where
the agent's note cannot go: before a note on a file hunk-mark-read hides as read, it takes
the file's read mark away (in hunk-mark-read's own files) and loads the review again. It sees the files before that filter
only when keep-notes is loaded first, which it is when it comes through `[extensions] paths`
and the filtering extension is installed under `~/.config/hunk/extensions/` (as
`hunk extension install` puts it). Loaded the other way round it lists only what is shown,
and nothing as hidden.

`notes` holds every saved note, yours (`source` "user") and those an agent added through
`hunk session comment …`. `id` is the id `hunk session comment list` and `comment rm` use;
a reply has `parentId`. Hunk tells an extension a note's file only by its own key for the
file; keep-notes turns the key into a path by computing Hunk's key for each file it loads
(copied from Hunk 0.25.0). Should a later Hunk change the key, a note you wrote still has
its path from the editor, while an agent's note on a file you never noted gets `"file": ""`,
and the viewer says once that it "names files differently". A draft you have not saved is
not kept, because Hunk does not report it when you throw a draft away.

A note leaves the file when Hunk deletes it: with `D` in the viewer, or with
`hunk session comment rm` / `comment clear` from outside, including for a note on a hidden
file. Closing the viewer leaves the file in place: deleting it is up to whoever reads it.

Each viewer, as it starts, removes the files nobody will read any more: those named as
above, last written more than 14 days ago, whose process is no longer running. Nothing else
in the directory is touched.

## Install

`hunk extension install` takes only the root of a git repository, so it cannot install a
folder inside this one. Load it from this checkout instead, by adding its path to
`~/.config/hunk/config.toml`:

```toml
[extensions]
paths = ["~/src/agent-skills/hunk-extensions/keep-notes"]
```

Use the path to your checkout; Hunk expands `~`. A symlink under `~/.config/hunk/extensions/` is not
picked up. Hunk loads the extension the next time a viewer starts, and every edit in the
checkout reaches the next viewer.

To remove it, delete that path from `paths`. The files it wrote stay where they are; delete
`~/.local/state/hunk-keep-notes/` (or your `$XDG_STATE_HOME/hunk-keep-notes/`) to remove them
too.

It needs Hunk with extension API 33 (Hunk 0.25.0) and coexists with other extensions,
`hunk-mark-read` included.

## Develop

```sh
bun test ./src                 # or: python3 test_keep_notes.py
bun install && bun run typecheck
HUNK_KEEP_NOTES_DIR=/tmp/kn hunk diff --extension ./   # try it without touching your own files
```

`bun install` puts `node_modules/` here. Delete it before committing.
