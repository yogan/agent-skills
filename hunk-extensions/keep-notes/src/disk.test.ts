import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import {
  existsSync,
  mkdtempSync,
  readdirSync,
  readFileSync,
  rmSync,
  statSync,
  utimesSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { FORMAT_VERSION, keepDir, keptPath, load, prune, PRUNE_AFTER_MS, save, type Viewer } from "./disk";
import type { KeptNote } from "./notebook";

const viewer: Viewer = {
  pid: 4242,
  startedAt: "2026-01-02T03:04:05.000Z",
  cwd: "/work/demo/sub",
  repoRoot: "/work/demo",
  tag: "agent-7",
};
const note: KeptNote = { id: "user:1", source: "user", file: "alpha.txt", side: "new", line: 2, end: 2, text: "no" };

describe("keepDir", () => {
  test("the state home by default, the override when it is absolute, nothing when off", () => {
    expect(keepDir({}, "/home/u")).toBe("/home/u/.local/state/hunk-keep-notes");
    expect(keepDir({ XDG_STATE_HOME: "/st" }, "/home/u")).toBe("/st/hunk-keep-notes");
    expect(keepDir({ XDG_STATE_HOME: "rel" }, "/home/u")).toBe("/home/u/.local/state/hunk-keep-notes");
    expect(keepDir({ HUNK_KEEP_NOTES_DIR: "/mine", XDG_STATE_HOME: "/st" })).toBe("/mine");
    expect(keepDir({ HUNK_KEEP_NOTES_DIR: "rel" }, "/home/u")).toBe("/home/u/.local/state/hunk-keep-notes");
    expect(keepDir({ HUNK_KEEP_NOTES: "off", HUNK_KEEP_NOTES_DIR: "/mine" })).toBeNull();
    expect(keepDir({ HUNK_KEEP_NOTES: "OFF" })).toBeNull();
  });
});

describe("files", () => {
  let dir: string;
  beforeEach(() => {
    // Only this directory is ever written, and only it is removed.
    dir = mkdtempSync(join(tmpdir(), "keep-notes-test-"));
  });
  afterEach(() => {
    rmSync(dir, { recursive: true, force: true });
  });

  test("names the file by the viewer process, so viewers never share one", () => {
    expect(keptPath(dir, viewer)).toBe(join(dir, "4242-1767323045000.json"));
    expect(keptPath(dir, { ...viewer, pid: 4243 })).not.toBe(keptPath(dir, viewer));
  });

  test("writes the notes with the viewer they came from, and nothing else beside them", () => {
    const path = keptPath(join(dir, "nested"), viewer);
    save(path, viewer, [note], undefined, new Date("2026-01-02T03:05:00.000Z"));
    expect(JSON.parse(readFileSync(path, "utf8"))).toEqual({
      version: FORMAT_VERSION,
      ...viewer,
      updatedAt: "2026-01-02T03:05:00.000Z",
      notes: [note],
    });
    expect(readdirSync(join(dir, "nested"))).toEqual(["4242-1767323045000.json"]);
    expect(statSync(path).mode & 0o777).toBe(0o600);
    expect(load(path)).toEqual([note]);
  });

  test("removes the file once no note is left, and is fine when there was none", () => {
    const path = keptPath(dir, viewer);
    save(path, viewer, [note]);
    save(path, viewer, []);
    expect(existsSync(path)).toBe(false);
    save(path, viewer, []);
    expect(readdirSync(dir)).toEqual([]);
  });

  test("a file with a review stays while the viewer is open, and goes as it closes", () => {
    const path = keptPath(dir, viewer);
    const review = {
      sourceLabel: "/work/demo",
      loadedAt: 1767323046000,
      files: [{ path: "alpha.txt", additions: 1, deletions: 1, hidden: true }],
    };
    save(path, viewer, [], review);
    expect(JSON.parse(readFileSync(path, "utf8")).review).toEqual(review);
    save(path, viewer, []);
    expect(existsSync(path)).toBe(false);
  });

  test("a missing or unreadable file holds no notes", () => {
    expect(load(join(dir, "none.json"))).toEqual([]);
    writeFileSync(join(dir, "bad.json"), "{not json");
    expect(load(join(dir, "bad.json"))).toEqual([]);
  });

  test("prunes only its own old files of viewers that are gone", () => {
    const now = Date.parse("2026-03-01T00:00:00.000Z");
    const old = (now - PRUNE_AFTER_MS - 60_000) / 1000;
    const recent = (now - PRUNE_AFTER_MS + 60_000) / 1000;
    const files: Record<string, number> = {
      "100-1.json": old, // gone, old: removed
      "200-1.json": old, // still running: kept
      "300-1.json": recent, // gone, recent: kept
      "notes.json": old, // not a name it gives: kept
      "100-1.json.tmp": old, // nor this
    };
    for (const [name, t] of Object.entries(files)) {
      writeFileSync(join(dir, name), "{}");
      utimesSync(join(dir, name), t, t);
    }
    expect(prune(dir, now, (pid) => pid === 200)).toEqual(["100-1.json"]);
    expect(readdirSync(dir).sort()).toEqual(
      ["100-1.json.tmp", "200-1.json", "300-1.json", "notes.json"].sort(),
    );
    expect(prune(join(dir, "absent"), now)).toEqual([]);
  });
});
