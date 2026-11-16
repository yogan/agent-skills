import { describe, expect, test } from "bun:test";
import { NoteBook, type EditorNote, type KeptNote, type StoreNote } from "./notebook";

// Shaped like what Hunk 0.25.0 (extension API 33) emitted for a note typed in the viewer:
// the editor's note_created, then the store's note_changed, 7 ms apart.
const editor: EditorNote = {
  id: "user:1700000000000-1",
  fileId: "/work/demo:0:alpha.txt",
  filePath: "alpha.txt",
  hunkIndex: 0,
  side: "new",
  line: 1,
  newRange: [1, 1],
  body: "alpha needs care",
  draft: false,
} as EditorNote;

const stored: StoreNote = {
  id: "user:1700000000000-1",
  source: "user",
  fileKey: "file:0123456789abcdef0123456789abcdef",
  anchor: { newRange: [1, 1], preferred: { side: "new", line: 1 } },
  summary: "alpha needs care",
  createdAt: "2026-01-02T03:04:05.000Z",
};

const kept: KeptNote = {
  id: "user:1700000000000-1",
  source: "user",
  file: "alpha.txt",
  fileKey: "file:0123456789abcdef0123456789abcdef",
  side: "new",
  line: 1,
  end: 1,
  text: "alpha needs care",
  createdAt: "2026-01-02T03:04:05.000Z",
};

describe("NoteBook", () => {
  test("a saved note is kept with its path, whichever event comes first", () => {
    const editorFirst = new NoteBook();
    expect(editorFirst.fromEditor(editor)).toBe(true);
    expect(editorFirst.fromStore("created", stored)).toBe(true);
    expect(editorFirst.list()).toEqual([kept]);

    const storeFirst = new NoteBook();
    expect(storeFirst.fromStore("created", stored)).toBe(true);
    expect(storeFirst.list()[0]?.file).toBe("");
    expect(storeFirst.fromEditor(editor)).toBe(true);
    expect(storeFirst.list()).toEqual([kept]);
  });

  test("a draft is not kept, however much of it is typed", () => {
    const book = new NoteBook();
    expect(book.fromEditor({ ...editor, id: "draft:x", body: "half a tho", draft: true })).toBe(
      false,
    );
    expect(book.list()).toEqual([]);
  });

  test("an edit replaces the text, and an unchanged repeat is not a change", () => {
    const book = new NoteBook();
    book.fromEditor(editor);
    book.fromStore("created", stored);
    const edited = { ...stored, summary: "alpha needs more care", updatedAt: "2026-01-02T03:05:00.000Z" };
    expect(book.fromEditor({ ...editor, body: "alpha needs more care" })).toBe(true);
    expect(book.fromStore("updated", edited)).toBe(true);
    expect(book.fromStore("updated", edited)).toBe(false);
    expect(book.list()[0]?.text).toBe("alpha needs more care");
    expect(book.list()[0]?.updatedAt).toBe("2026-01-02T03:05:00.000Z");
  });

  test("a delete, from the viewer or the CLI, takes the note out", () => {
    const book = new NoteBook();
    book.fromEditor(editor);
    book.fromStore("created", stored);
    expect(book.fromStore("removed", stored)).toBe(true);
    expect(book.list()).toEqual([]);
    expect(book.fromStore("removed", stored)).toBe(false);
  });

  test("the agent's notes are kept too, with the path of the review file their key names", () => {
    const book = new NoteBook();
    const agent = {
      ...stored,
      id: "mcp:abc",
      source: "agent",
      author: "agent",
      fileKey: "file:beta",
      summary: "why this changed",
    };
    expect(book.fromStore("created", agent)).toBe(true);
    expect(book.list()[0]).toMatchObject({ source: "agent", author: "agent", file: "" });
    // A load names the file: the note gains its path, and keeps it once the file is hidden.
    expect(book.learnPaths(new Map([["file:beta", "beta.txt"]]))).toBe(true);
    expect(book.learnPaths(new Map())).toBe(false);
    expect(book.list()[0]?.file).toBe("beta.txt");
    book.fromStore("created", { ...agent, id: "mcp:def" });
    expect(book.list()[1]?.file).toBe("beta.txt");
    expect(book.fromStore("removed", agent)).toBe(true);
  });

  test("a key the review and the editor name differently is reported", () => {
    const book = new NoteBook();
    book.learnPaths(new Map([[stored.fileKey, "other.txt"]]));
    book.fromStore("created", stored);
    expect(book.disagreements.size).toBe(0);
    book.fromEditor(editor);
    expect(book.disagreements.has(stored.fileKey)).toBe(true);
    expect(book.list()[0]?.file).toBe("alpha.txt");
  });

  test("a note on a removed line is kept on the old side", () => {
    const book = new NoteBook();
    book.fromEditor({ ...editor, side: "old", line: 2, newRange: undefined, oldRange: [2, 3] });
    expect(book.list()[0]).toMatchObject({ side: "old", line: 2, end: 3 });
  });

  test("a reply keeps the note it answers", () => {
    const book = new NoteBook();
    book.fromStore("created", { ...stored, id: "user:2", parentId: "mcp:abc" });
    expect(book.list()[0]?.parentId).toBe("mcp:abc");
  });

  test("a book seeded from the viewer's own file keeps those notes through the next change", () => {
    const book = new NoteBook();
    book.seed([kept]);
    book.fromStore("created", { ...stored, id: "user:4", summary: "another" });
    expect(book.list().map((n) => n.id)).toEqual(["user:1700000000000-1", "user:4"]);
    expect(book.fromStore("removed", stored)).toBe(true);
    expect(book.list().map((n) => n.id)).toEqual(["user:4"]);
  });

  test("a store note the editor never named takes the path of its file's other notes", () => {
    const book = new NoteBook();
    book.fromEditor(editor);
    book.fromStore("created", stored);
    book.fromStore("created", { ...stored, id: "user:3", summary: "and here" });
    expect(book.list()[1]).toMatchObject({ id: "user:3", file: "alpha.txt" });
  });
});
