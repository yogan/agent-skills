/**
 * The files the extension keeps notes in: where they go, what one holds, and when an old
 * one is removed.
 *
 * One file per viewer process, named `<pid>-<process start in ms>.json`, so two viewers
 * never write the same file, and a viewer opened later for the same review starts a file of
 * its own instead of overwriting the notes of one that was closed before anybody read them.
 *
 * Written whole on every change, through a temporary file and a rename, so a reader sees
 * the old notes or the new ones and never half a file. Removed once it holds no notes.
 */
import { mkdirSync, readdirSync, readFileSync, renameSync, statSync, unlinkSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, isAbsolute, join } from "node:path";
import type { KeptNote } from "./notebook";

/** Bumped only by a change a reader of the file has to know about. */
export const FORMAT_VERSION = 1;

/** Overrides the directory the files go in; must be absolute. */
export const DIR_ENV = "HUNK_KEEP_NOTES_DIR";
/** `off` keeps nothing at all. */
export const SWITCH_ENV = "HUNK_KEEP_NOTES";
/** Free text the opener of a viewer can put in its file, to tell its own viewers' files apart. */
export const TAG_ENV = "HUNK_KEEP_NOTES_TAG";

/** A file whose viewer is gone is removed at the next viewer's start once it is this old. */
export const PRUNE_AFTER_MS = 14 * 24 * 60 * 60 * 1000;

/** Exactly the names this extension gives its files. Nothing else in the directory is touched. */
const KEPT_NAME = /^(\d+)-(\d+)\.json$/;

export interface Viewer {
  pid: number;
  /** When the viewer process started, as an ISO time. */
  startedAt: string;
  /** The directory the viewer runs in. */
  cwd: string;
  /** The git repository `cwd` is in, or null outside one. */
  repoRoot: string | null;
  /** What the opener put in `HUNK_KEEP_NOTES_TAG`, or null. */
  tag: string | null;
}

/** One file of the review as Hunk loaded it, before any extension filtered the changeset. */
export interface ReviewFile {
  path: string;
  previousPath?: string;
  additions: number;
  deletions: number;
  /** Left out of what the viewer shows by an extension's filter (hunk-mark-read hiding it as read). */
  hidden: boolean;
}

/** The review the viewer holds, as of its latest load. */
export interface Review {
  sourceLabel: string;
  /** When the load finished, in ms since the epoch: a reader tells a stale record by it. */
  loadedAt: number;
  files: ReviewFile[];
}

export interface KeptFile extends Viewer {
  version: number;
  updatedAt: string;
  review?: Review;
  notes: KeptNote[];
}

type Env = Record<string, string | undefined>;

/**
 * The directory to keep notes in, or null when keeping is switched off.
 *
 * `$XDG_STATE_HOME/hunk-keep-notes`, else `~/.local/state/hunk-keep-notes` (where
 * hunk-mark-read keeps its marks too); `HUNK_KEEP_NOTES_DIR` replaces it. A relative path
 * in either variable is ignored, as the XDG spec says, rather than resolved against
 * whatever directory the viewer happened to start in.
 */
export function keepDir(env: Env, home = homedir()): string | null {
  if ((env[SWITCH_ENV] ?? "").toLowerCase() === "off") {
    return null;
  }
  const own = env[DIR_ENV];
  if (own && isAbsolute(own)) {
    return own;
  }
  const state = env.XDG_STATE_HOME;
  return join(state && isAbsolute(state) ? state : join(home, ".local", "state"), "hunk-keep-notes");
}

export function keptPath(dir: string, viewer: Pick<Viewer, "pid" | "startedAt">): string {
  return join(dir, `${viewer.pid}-${Date.parse(viewer.startedAt)}.json`);
}

/** The notes a file at `path` holds, or [] when there is none or it cannot be read. */
export function load(path: string): KeptNote[] {
  try {
    const doc = JSON.parse(readFileSync(path, "utf8")) as Partial<KeptFile>;
    return doc.version === FORMAT_VERSION && Array.isArray(doc.notes) ? doc.notes : [];
  } catch {
    return [];
  }
}

/**
 * Make the file at `path` say `notes` and `review`: write it, or remove it when it would
 * say nothing (no notes, and no review: the viewer is closing).
 */
export function save(
  path: string,
  viewer: Viewer,
  notes: KeptNote[],
  review?: Review,
  now = new Date(),
) {
  if (notes.length === 0 && !review) {
    try {
      unlinkSync(path);
    } catch (error) {
      if ((error as { code?: string }).code !== "ENOENT") {
        throw error;
      }
    }
    return;
  }
  const doc: KeptFile = {
    version: FORMAT_VERSION,
    ...viewer,
    updatedAt: now.toISOString(),
    ...(review ? { review } : {}),
    notes,
  };
  // Private like the notes themselves: they can say anything about the code under review.
  mkdirSync(dirname(path), { recursive: true, mode: 0o700 });
  const tmp = `${path}.tmp`;
  writeFileSync(tmp, JSON.stringify(doc, null, 1) + "\n", { mode: 0o600 });
  renameSync(tmp, path);
}

/** Whether a process with this pid exists (one owned by another user counts). */
export function processAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return (error as { code?: string }).code === "EPERM";
  }
}

/**
 * Remove the files in `dir` that nobody will read any more: named as this extension names
 * its files, last written more than `PRUNE_AFTER_MS` ago, and from a viewer no longer
 * running. Returns the names removed. Never fails: a file it cannot remove stays.
 */
export function prune(dir: string, now = Date.now(), alive = processAlive): string[] {
  let names: string[];
  try {
    names = readdirSync(dir);
  } catch {
    return [];
  }
  const removed: string[] = [];
  for (const name of names) {
    const m = KEPT_NAME.exec(name);
    if (!m) {
      continue;
    }
    const path = join(dir, name);
    try {
      if (now - statSync(path).mtimeMs <= PRUNE_AFTER_MS || alive(Number(m[1]))) {
        continue;
      }
      unlinkSync(path);
      removed.push(name);
    } catch {
      // Gone already, or not ours to remove: either way, nothing to do.
    }
  }
  return removed;
}
