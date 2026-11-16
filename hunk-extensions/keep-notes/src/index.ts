/**
 * keep-notes: keep a Hunk viewer's notes, and the review it holds, on disk the moment
 * either changes.
 *
 * Hunk keeps notes in memory alone, and an extension that hides files (hunk-mark-read's
 * "hide read files") takes them, and their notes, out of everything `hunk session` reports.
 * This file is the reader's copy of both: every saved note with its path, and every file of
 * the review with whether it is hidden. See README.md for where it goes and what is in it.
 */
import type { HunkExtensionAPI } from "hunkdiff/extension";
import { spawnSync } from "node:child_process";
import { keepDir, keptPath, load, prune, save, TAG_ENV, type Review, type Viewer } from "./disk";
import { NoteBook } from "./notebook";
import { reviewOf, type Unfiltered } from "./review";

/** The repository `cwd` is in, or null; asked once, at the first save, not at startup. */
function repoRootOf(cwd: string): string | null {
  try {
    const r = spawnSync("git", ["-C", cwd, "rev-parse", "--show-toplevel"], {
      encoding: "utf8",
      timeout: 5000,
    });
    return r.status === 0 ? r.stdout.trim() || null : null;
  } catch {
    return null;
  }
}

type Notify = (message: string, type?: "info" | "warning" | "error") => void;

export default function keepNotes(hunk: HunkExtensionAPI) {
  const dir = keepDir(process.env);
  if (!dir) {
    return;
  }
  prune(dir);
  // The process's own start, not the factory's: a factory Hunk runs again in the same
  // process keeps writing the same file.
  const startedAt = new Date(Math.round(performance.timeOrigin)).toISOString();
  const path = keptPath(dir, { pid: process.pid, startedAt });
  const book = new NoteBook();
  book.seed(load(path));
  let viewer: Viewer | null = null;
  let review: Review | undefined;
  /** The changeset as it reached this extension's transform, keyed by Hunk's file key. */
  let unfiltered: Unfiltered | null = null;
  let failing = false;
  let warnedKeys = false;

  function persist(notify: Notify, closing = false) {
    try {
      const cwd = process.cwd();
      if (!viewer || viewer.cwd !== cwd) {
        viewer = {
          pid: process.pid,
          startedAt,
          cwd,
          repoRoot: repoRootOf(cwd),
          tag: process.env[TAG_ENV] || null,
        };
      }
      save(path, viewer, book.list(), closing ? undefined : review);
      failing = false;
    } catch (error) {
      // Said once per run of failures; every later change tries the write again.
      if (!failing) {
        failing = true;
        notify(`keep-notes: cannot save your notes to disk (${String(error)})`, "warning");
      }
    }
    if (book.disagreements.size > 0 && !warnedKeys) {
      warnedKeys = true;
      notify("keep-notes: this Hunk names files differently; notes on hidden files may lack their path", "warning");
    }
  }

  // Runs before the filter of an extension loaded after this one: one from
  // `~/.config/hunk/extensions/`, when this one is loaded through `[extensions] paths`.
  // It changes nothing; it only remembers what was loaded.
  hunk.transformChangeset((changeset) => {
    try {
      unfiltered = {
        id: changeset.id,
        sourceLabel: changeset.sourceLabel,
        files: changeset.files.map((f) => ({
          path: f.path,
          ...(f.previousPath !== undefined ? { previousPath: f.previousPath } : {}),
          stats: { additions: f.stats.additions, deletions: f.stats.deletions },
        })),
      };
    } catch {
      unfiltered = null;
    }
    return changeset;
  });

  hunk.on("changeset_loaded", ({ changeset }, ctx) => {
    const seen = reviewOf(changeset.id, changeset.sourceLabel, changeset.files, unfiltered);
    review = seen.review;
    book.learnPaths(seen.paths);
    persist(ctx.notify);
  });
  hunk.on("note_created", ({ note }, ctx) => {
    if (book.fromEditor(note)) persist(ctx.notify);
  });
  hunk.on("note_edited", ({ note }, ctx) => {
    if (book.fromEditor(note)) persist(ctx.notify);
  });
  hunk.on("note_changed", ({ kind, note }, ctx) => {
    if (book.fromStore(kind, note)) persist(ctx.notify);
  });
  // A closed viewer's review is nobody's business; its notes are, until they are read.
  hunk.on("shutdown", (_payload, ctx) => persist(ctx.notify, true));
}
