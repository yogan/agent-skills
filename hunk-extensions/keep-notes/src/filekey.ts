/**
 * Hunk's key for a file of a review, which is all a store note (`note_changed`) says about
 * the file it is on.
 *
 * A copy of `reviewFileKey` in Hunk 0.25.0 (packages/hunk/src/core/review/identity.ts): four
 * 32-bit FNV-style lanes over the review's source label, the path, the previous path and
 * how many files of the same path came before it. Hunk does not export it. Should a later
 * Hunk change it, no key matches, a note's `file` stays "" where the editor did not name
 * it, and `matches` below reports the disagreement wherever a user note names both.
 */

const LANE_SEEDS = [2166136261, 16777619, 2654435769, 2246822507];
const LANE_PRIMES = [16777619, 16777625, 16777607, 16777629];

function digest(parts: readonly string[]): string {
  const lanes = [...LANE_SEEDS];
  const mix = (code: number) => {
    for (let lane = 0; lane < lanes.length; lane += 1) {
      lanes[lane] = Math.imul(lanes[lane]! ^ code, LANE_PRIMES[lane]!);
    }
  };
  for (const part of parts) {
    for (const ch of `${part.length}:`) {
      mix(ch.charCodeAt(0));
    }
    for (let i = 0; i < part.length; i += 1) {
      mix(part.charCodeAt(i));
    }
    mix(31);
  }
  return lanes.map((v) => (v >>> 0).toString(16).padStart(8, "0")).join("");
}

export function fileKey(
  sourceLabel: string,
  path: string,
  previousPath: string | undefined,
  duplicateIndex: number,
): string {
  return `file:${digest([sourceLabel, path, previousPath ?? "", String(duplicateIndex)])}`;
}

/** Each file's key, in order: a path's second file has duplicate index 1, as in Hunk. */
export function fileKeys(
  sourceLabel: string,
  files: readonly { path: string; previousPath?: string }[],
): string[] {
  const seen = new Map<string, number>();
  return files.map((file) => {
    const n = seen.get(file.path) ?? 0;
    seen.set(file.path, n + 1);
    return fileKey(sourceLabel, file.path, file.previousPath, n);
  });
}
