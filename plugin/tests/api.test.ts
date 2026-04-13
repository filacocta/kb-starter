import { describe, it } from "node:test";
import { expect } from "expect";
import { buildPrompt, formatAsCallout } from "../src/api";

describe("buildPrompt", () => {
	it("returns the question alone when there is no selected text", () => {
		expect(buildPrompt("What is this?")).toBe("What is this?");
	});

	it("returns the question alone when selectedText is empty", () => {
		expect(buildPrompt("What is this?", "")).toBe("What is this?");
	});

	it("returns the question alone when selectedText is whitespace-only", () => {
		expect(buildPrompt("What is this?", "   ")).toBe("What is this?");
	});

	it("prepends context when selectedText is provided", () => {
		const result = buildPrompt("Summarise this.", "Some text here.");
		expect(result).toBe("Context:\nSome text here.\n\nQuestion: Summarise this.");
	});

	it("trims surrounding whitespace from selectedText", () => {
		const result = buildPrompt("Explain.", "  content  ");
		expect(result).toBe("Context:\ncontent\n\nQuestion: Explain.");
	});
});

describe("formatAsCallout", () => {
	it("wraps a single-line response in a callout block", () => {
		const result = formatAsCallout("Hello world.");
		expect(result).toBe("\n\n> [!note] KB Bot\n> Hello world.\n");
	});

	it("converts newlines so each line is prefixed with '> '", () => {
		const result = formatAsCallout("Line one.\nLine two.");
		expect(result).toBe("\n\n> [!note] KB Bot\n> Line one.\n> Line two.\n");
	});

	it("handles multi-line responses with blank lines", () => {
		const result = formatAsCallout("First.\n\nSecond.");
		expect(result).toBe("\n\n> [!note] KB Bot\n> First.\n> \n> Second.\n");
	});
});
