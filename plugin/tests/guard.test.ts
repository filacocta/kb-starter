import { describe, it } from "node:test";
import { expect } from "expect";
import { isKbBotFile, isInsideKb } from "../src/guard";

describe("isKbBotFile", () => {
	it("returns true when author is kb-bot", () => {
		const content = "---\nauthor: kb-bot\ncreated: 2026-04-09\n---\n# Note";
		expect(isKbBotFile(content)).toBe(true);
	});

	it("returns false when author is missing", () => {
		const content = "---\ncreated: 2026-04-09\n---\n# Note";
		expect(isKbBotFile(content)).toBe(false);
	});

	it("returns false when author is a different value", () => {
		const content = "---\nauthor: human\n---\n# Note";
		expect(isKbBotFile(content)).toBe(false);
	});

	it("returns false when there is no frontmatter", () => {
		const content = "# Just a note\nNo frontmatter here.";
		expect(isKbBotFile(content)).toBe(false);
	});

	it("handles quoted author value", () => {
		const content = '---\nauthor: "kb-bot"\n---\n# Note';
		expect(isKbBotFile(content)).toBe(true);
	});
});

describe("isInsideKb", () => {
	it("rejects traversal before a vault API can normalize it", () => {
		for (const path of ["knowledgebase/../journal/private.md", "knowledgebase/./note.md", "knowledgebase\\..\\journal\\private.md"]) {
			expect(isInsideKb(path)).toBe(false);
		}
	});
	it("returns true for a file directly in knowledgebase/", () => {
		expect(isInsideKb("knowledgebase/_index.md")).toBe(true);
	});

	it("returns true for a file in a subfolder", () => {
		expect(isInsideKb("knowledgebase/topics/llms/notes/transformers.md")).toBe(true);
	});

	it("returns true for the root folder itself", () => {
		expect(isInsideKb("knowledgebase")).toBe(true);
	});

	it("returns false for a file outside knowledgebase/", () => {
		expect(isInsideKb("journal/my-diary.md")).toBe(false);
	});

	it("returns false for a path that merely starts with 'knowledgebase' but is not inside it", () => {
		expect(isInsideKb("knowledgebase-backup/file.md")).toBe(false);
	});

	it("handles backslash paths (Windows)", () => {
		expect(isInsideKb("knowledgebase\\topics\\llms\\note.md")).toBe(true);
	});
});
