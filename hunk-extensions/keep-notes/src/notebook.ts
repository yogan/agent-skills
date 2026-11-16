/**
 * Every saved note in one Hunk viewer, the user's and the agent's, as told by Hunk's events.
 *
 * Two kinds of event describe a note:
 *
 * - `note_changed` comes from the review's store of saved notes. It reports every create,
 *   edit and delete, whoever made it (the user in the viewer, an agent through
 *   `hunk session comment …`), but names the file only by Hunk's key for it.
 * - `note_created` / `note_edited` come from the editor, for the user's notes only. They
 *   carry the file's path, and also fire for every keystroke of a draft.
 *
 * A key becomes a path through the review's own files (`learnPaths`, fed from every loaded
 * changeset, see filekey.ts), or through the editor naming the path of a note on it.
 * Hiding a file (an extension filtering it out of the changeset) or a reload that drops it
 * emits no event: the note stays in the store, and so it stays here, which is the point.
 *
 * Only the shapes this file reads are declared, so it runs and tests without Hunk's types.
 */

export type Side = "new" | "old";

/** One note as kept on disk. */
export interface KeptNote {
  id: string;
  parentId?: string;
  /** "user", "agent" or "ai", as Hunk says. */
  source: string;
  author?: string;
  /** The file's path; "" while neither the editor nor the review has named it. */
  file: string;
  fileKey?: string;
  side: Side;
  /** First line of the anchored range, one-based, on `side`; null where Hunk gave none. */
  line: number | null;
  /** Last line of that range. */
  end: number | null;
  text: string;
  createdAt?: string;
  updatedAt?: string;
}

type Range = readonly [number, number];

/** The part of `ExtensionReviewNote` (note_created / note_edited) used here. */
export interface EditorNote {
  id: string;
  parentId?: string;
  filePath: string;
  side: Side;
  line: number;
  oldRange?: Range;
  newRange?: Range;
  body: string;
  draft: boolean;
}

/** The part of `ExtensionReviewSnapshotNote` (note_changed) used here. */
export interface StoreNote {
  id: string;
  parentId?: string;
  source: string;
  author?: string;
  fileKey: string;
  anchor: {
    oldRange?: Range;
    newRange?: Range;
    preferred?: { side: Side; line: number };
  };
  summary: string;
  createdAt?: string;
  updatedAt?: string;
}

export type StoreChange = "created" | "updated" | "removed";

/**
 * Where a note sits. The new side wins when a range covers both, as in `lib/hunk.py`'s
 * reading of `comment list`, so a note reads the same from disk as from the live viewer.
 */
function place(
  newRange: Range | undefined,
  oldRange: Range | undefined,
  fallback: { side: Side; line: number } | undefined,
): Pick<KeptNote, "side" | "line" | "end"> {
  if (newRange) {
    return { side: "new", line: newRange[0], end: newRange[1] };
  }
  if (oldRange) {
    return { side: "old", line: oldRange[0], end: oldRange[1] };
  }
  if (fallback) {
    return { side: fallback.side, line: fallback.line, end: fallback.line };
  }
  return { side: "new", line: null, end: null };
}

export class NoteBook {
  private readonly notes = new Map<string, KeptNote>();
  /** Paths the editor named, by note id, kept after a delete in case a late event names it. */
  private readonly pathById = new Map<string, string>();
  /** Paths per Hunk file key, from the review's files and from the editor. */
  private readonly pathByFileKey = new Map<string, string>();
  /** Keys whose path the review and the editor named differently: a sign keys have changed. */
  readonly disagreements = new Set<string>();

  /**
   * Start from notes this same viewer saved before. Hunk re-runs an extension's factory
   * (after a trust grant, or a change of directory) in the same process, and a fresh book
   * would otherwise rewrite the viewer's file without them on its next change.
   */
  seed(notes: readonly KeptNote[]) {
    for (const note of notes) {
      if (!this.notes.has(note.id)) {
        this.notes.set(note.id, { ...note, source: note.source ?? "user" });
        if (note.file) {
          this.pathById.set(note.id, note.file);
          if (note.fileKey) {
            this.pathByFileKey.set(note.fileKey, note.file);
          }
        }
      }
    }
  }

  /** Every kept note, oldest first. */
  list(): KeptNote[] {
    return [...this.notes.values()].map((note) => ({ ...note }));
  }

  /**
   * The paths of a loaded review's files, by key. Kept across loads, so a note on a file a
   * later load leaves out still has its path. True when a kept note gained its path.
   */
  learnPaths(paths: ReadonlyMap<string, string>): boolean {
    let changed = false;
    for (const [key, path] of paths) {
      this.pathByFileKey.set(key, path);
    }
    for (const note of this.notes.values()) {
      if (!note.file && note.fileKey && paths.has(note.fileKey)) {
        note.file = paths.get(note.fileKey)!;
        changed = true;
      }
    }
    return changed;
  }

  /** A note_created / note_edited payload. True when what is kept changed. */
  fromEditor(note: EditorNote): boolean {
    if (note.draft) {
      // A draft is not a note yet: Esc throws it away without any event saying so.
      return false;
    }
    this.pathById.set(note.id, note.filePath);
    const before = this.notes.get(note.id);
    if (before?.fileKey) {
      this.learnKey(before.fileKey, note.filePath);
    }
    const next: KeptNote = {
      source: "user",
      ...before,
      id: note.id,
      ...(note.parentId ? { parentId: note.parentId } : {}),
      file: note.filePath,
      ...place(note.newRange, note.oldRange, { side: note.side, line: note.line }),
      text: note.body,
    };
    return this.put(before, next);
  }

  /** A note_changed payload. True when what is kept changed. */
  fromStore(kind: StoreChange, note: StoreNote): boolean {
    if (kind === "removed") {
      return this.notes.delete(note.id);
    }
    const before = this.notes.get(note.id);
    const named = this.pathById.get(note.id);
    if (named) {
      this.learnKey(note.fileKey, named);
    }
    const file = before?.file || named || this.pathByFileKey.get(note.fileKey) || "";
    const next: KeptNote = {
      id: note.id,
      ...(note.parentId ? { parentId: note.parentId } : {}),
      source: note.source,
      ...(note.author ? { author: note.author } : {}),
      file,
      fileKey: note.fileKey,
      ...place(note.anchor.newRange, note.anchor.oldRange, note.anchor.preferred),
      text: note.summary,
      ...(note.createdAt ? { createdAt: note.createdAt } : {}),
      ...(note.updatedAt ? { updatedAt: note.updatedAt } : {}),
    };
    return this.put(before, next);
  }

  private learnKey(key: string, path: string) {
    const known = this.pathByFileKey.get(key);
    if (known !== undefined && known !== path) {
      this.disagreements.add(key);
    }
    this.pathByFileKey.set(key, path);
  }

  private put(before: KeptNote | undefined, next: KeptNote): boolean {
    if (before && JSON.stringify(before) === JSON.stringify(next)) {
      return false;
    }
    this.notes.set(next.id, next);
    return true;
  }
}
