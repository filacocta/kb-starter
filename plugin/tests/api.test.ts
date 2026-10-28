import { describe, it, mock } from "node:test";
import { requestUrl } from "obsidian";
import { expect } from "expect";
import { askClaude, buildPrompt, formatAsCallout } from "../src/api";
import { DEFAULT_SETTINGS } from "../src/constants";
import KbBotPlugin from "../src/KbBotPlugin";
import { App } from "obsidian";

mock.method(globalThis, "fetch", async () => { throw new Error("Network disabled in tests"); });

describe("saved settings", () => {
	it("keeps defaults for malformed persisted settings", async () => {
		for (const saved of [null, [], { apiKey: 42, model: false, maxTokens: -1 }, { maxTokens: Infinity }]) {
			const plugin = new KbBotPlugin(new App(), { id: "kb-bot" });
			plugin.loadData = async () => saved;
			await plugin.loadSettings();
			expect(plugin.settings).toEqual(DEFAULT_SETTINGS);
		}
	});
	it("preserves valid explicit settings without unknown fields", async () => {
		const plugin = new KbBotPlugin(new App(), { id: "kb-bot" });
		plugin.loadData = async () => ({ apiKey: "fixture-key", model: "fixture-model", maxTokens: 512, extra: true });
		await plugin.loadSettings();
		expect(plugin.settings).toEqual({ apiKey: "fixture-key", model: "fixture-model", maxTokens: 512 });
	});
});

describe("Obsidian request adapter", () => {
	const settings = { ...DEFAULT_SETTINGS, apiKey: "fixture-key", model: "fixture-model" };
	it("uses Obsidian transport and returns a validated text block", async () => {
		requestUrl.mock.mockImplementationOnce(async () => ({ status: 200, json: { content: [{ type: "text", text: "Answer" }] } }));
		expect(await askClaude("Question", settings)).toBe("Answer");
		expect(requestUrl.mock.calls.at(-1)?.arguments[0]).toMatchObject({ method: "POST", throw: false });
	});
	it("reports HTTP errors without consuming a malformed response", async () => {
		requestUrl.mock.mockImplementationOnce(async () => ({ status: 429, json: null }));
		await expect(askClaude("Question", settings)).rejects.toThrow("API error: 429");
	});
	it("rejects malformed successful responses", async () => {
		for (const json of [null, {}, { content: [] }, { content: [{ type: "text", text: 42 }] }]) {
			requestUrl.mock.mockImplementationOnce(async () => ({ status: 200, json }));
			await expect(askClaude("Question", settings)).rejects.toThrow("Invalid API response");
		}
	});
});

describe("explicit model selection", () => {
	it("has no prescribed model and rejects unconfigured calls before networking", async () => {
		expect(DEFAULT_SETTINGS.model).toBe("");
		await expect(askClaude("test", DEFAULT_SETTINGS)).rejects.toThrow("Configure");
		await expect(askClaude("test", { ...DEFAULT_SETTINGS, apiKey: "example-key" })).rejects.toThrow("Configure");
	});
});

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
