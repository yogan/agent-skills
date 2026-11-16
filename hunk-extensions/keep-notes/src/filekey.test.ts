import { describe, expect, test } from "bun:test";
import { fileKey, fileKeys } from "./filekey";

describe("fileKey", () => {
  test("is Hunk's", () => {
    // Hunk 0.25.0 named its files by these keys in `note_changed`, checked against a review
    // in a scratch directory; these two are the same algorithm for an invented directory.
    expect(fileKey("/work/demo", "alpha.txt", undefined, 0)).toBe(
      "file:d883e58aa1dbe122204b7aea6429f576",
    );
    expect(fileKey("/work/demo", "beta.txt", "", 0)).toBe("file:54e393f94939215501b230d943d09621");
  });

  test("a path's second file of a review gets its own key", () => {
    const [a, b, c] = fileKeys("/w", [{ path: "x" }, { path: "x" }, { path: "y" }]);
    expect(a).toBe(fileKey("/w", "x", undefined, 0));
    expect(b).toBe(fileKey("/w", "x", undefined, 1));
    expect(c).toBe(fileKey("/w", "y", undefined, 0));
  });
});
