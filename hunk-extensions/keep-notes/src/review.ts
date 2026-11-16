/**
 * The review a viewer holds, hidden files included, from two sightings of one load: the
 * changeset as it reached this extension's transform, and the changeset Hunk then loaded,
 * after every filter.
 */
import type { Review } from "./disk";
import { fileKeys } from "./filekey";

export interface LoadedFile {
  path: string;
  previousPath?: string;
  stats: { additions: number; deletions: number };
}

/** What the transform saw of one changeset. */
export interface Unfiltered {
  id: string;
  sourceLabel: string;
  files: LoadedFile[];
}

/**
 * The review of the changeset `id` that Hunk loaded as `shown`, and the path of each of its
 * files by Hunk's key.
 *
 * `unfiltered` belongs to this load only when it saw the same changeset (filters keep its
 * id). Without it — this extension loaded after the filter — the review is what is shown
 * and nothing is hidden: a reader then sees exactly what `hunk session` reports, never more.
 */
export function reviewOf(
  id: string,
  sourceLabel: string,
  shown: readonly LoadedFile[],
  unfiltered: Unfiltered | null,
  now = Date.now(),
): { review: Review; paths: Map<string, string> } {
  const shownKeys = new Set(fileKeys(sourceLabel, shown));
  const all =
    unfiltered && unfiltered.id === id && unfiltered.sourceLabel === sourceLabel
      ? unfiltered.files
      : shown;
  const keys = fileKeys(sourceLabel, all);
  return {
    review: {
      sourceLabel,
      loadedAt: now,
      files: all.map((f, i) => ({
        path: f.path,
        ...(f.previousPath !== undefined ? { previousPath: f.previousPath } : {}),
        additions: f.stats.additions,
        deletions: f.stats.deletions,
        hidden: !shownKeys.has(keys[i]!),
      })),
    },
    paths: new Map(keys.map((key, i) => [key, all[i]!.path])),
  };
}
