import { describe, expect, test } from "bun:test";
import { fileKey } from "./filekey";
import { reviewOf, type LoadedFile } from "./review";

const a: LoadedFile = { path: "alpha.txt", stats: { additions: 1, deletions: 1 } };
const b: LoadedFile = { path: "beta.txt", previousPath: "old.txt", stats: { additions: 2, deletions: 0 } };

describe("reviewOf", () => {
  test("a file the filter took out is in the review, marked hidden, with its path by key", () => {
    const { review, paths } = reviewOf("cs:1", "/work/demo", [b], { id: "cs:1", sourceLabel: "/work/demo", files: [a, b] }, 5);
    expect(review).toEqual({
      sourceLabel: "/work/demo",
      loadedAt: 5,
      files: [
        { path: "alpha.txt", additions: 1, deletions: 1, hidden: true },
        { path: "beta.txt", previousPath: "old.txt", additions: 2, deletions: 0, hidden: false },
      ],
    });
    expect(paths.get(fileKey("/work/demo", "alpha.txt", undefined, 0))).toBe("alpha.txt");
  });

  test("another load's sighting counts for nothing: only what is shown, nothing hidden", () => {
    for (const other of [
      { id: "cs:0", sourceLabel: "/work/demo", files: [a, b] },
      { id: "cs:1", sourceLabel: "/elsewhere", files: [a, b] },
      null,
    ]) {
      const { review } = reviewOf("cs:1", "/work/demo", [b], other, 5);
      expect(review.files.map((f) => [f.path, f.hidden])).toEqual([["beta.txt", false]]);
    }
  });
});
