import { describe, it } from "node:test";
import { expect } from "expect";
import {
	slugify,
	buildSourceNoteFilename,
	buildSourceNoteFrontmatter,
	buildSourceNoteBody,
	buildSourceNote,
	markReadingListItemDone,
	addToReadingList,
	buildReadingList,
	appendDiscussionEntry,
	updateCheckpointItem,
	completeCheckpoint,
	buildCheckpoint,
	type SourceNoteData,
} from "../src/ingest";

const baseData: SourceNoteData = {
	title: "Attention Is All You Need",
	origin: "https://arxiv.org/abs/1706.03762",
	type: "article",
	topic: "large-language-models",
	tags: ["transformers", "attention"],
	date: "2026-04-09",
	summary: "The paper that introduced the Transformer architecture.",
	keyPoints: ["Self-attention replaces recurrence", "Parallelisable training"],
};

describe("slugify", () => {
	it("lowercases and hyphenates", () => {
		expect(slugify("Hello World")).toBe("hello-world");
	});

	it("strips diacritics", () => {
		expect(slugify("Café")).toBe("cafe");
	});

	it("collapses multiple non-alphanumeric chars into one hyphen", () => {
		expect(slugify("foo -- bar")).toBe("foo-bar");
	});

	it("strips leading and trailing hyphens", () => {
		expect(slugify("  hello  ")).toBe("hello");
	});

	it("truncates to 60 characters", () => {
		const long = "a".repeat(80);
		expect(slugify(long).length).toBe(60);
	});

	it("handles empty string", () => {
		expect(slugify("")).toBe("");
	});
});

describe("buildSourceNoteFilename", () => {
	it("produces YYYY-MM-DD-slug.md", () => {
		expect(buildSourceNoteFilename("2026-04-09", "Attention Is All You Need"))
			.toBe("2026-04-09-attention-is-all-you-need.md");
	});

	it("handles special characters in title", () => {
		const result = buildSourceNoteFilename("2026-04-09", "GPT-4: Technical Report");
		expect(result).toBe("2026-04-09-gpt-4-technical-report.md");
	});
});

describe("buildSourceNoteFrontmatter", () => {
	it("includes all required fields", () => {
		const result = buildSourceNoteFrontmatter(baseData);
		expect(result).toContain("author: kb-bot");
		expect(result).toContain("type: source");
		expect(result).toContain("topic: large-language-models");
		expect(result).toContain('origin: "https://arxiv.org/abs/1706.03762"');
		expect(result).toContain('tags: ["transformers", "attention"]');
	});

	it("escapes double quotes in origin", () => {
		const result = buildSourceNoteFrontmatter({
			...baseData,
			origin: 'https://example.com/page?q="test"',
		});
		expect(result).toContain('origin: "https://example.com/page?q=\\"test\\""');
	});

	it("starts and ends with ---", () => {
		const result = buildSourceNoteFrontmatter(baseData);
		expect(result.startsWith("---\n")).toBe(true);
		expect(result.endsWith("\n---")).toBe(true);
	});
});

describe("buildSourceNoteBody", () => {
	it("includes title as h1", () => {
		const result = buildSourceNoteBody(baseData);
		expect(result).toContain("# Attention Is All You Need");
	});

	it("includes origin, date, and type", () => {
		const result = buildSourceNoteBody(baseData);
		expect(result).toContain("**Origin:** https://arxiv.org/abs/1706.03762");
		expect(result).toContain("**Ingested:** 2026-04-09");
		expect(result).toContain("**Type:** article");
	});

	it("includes summary", () => {
		const result = buildSourceNoteBody(baseData);
		expect(result).toContain("The paper that introduced the Transformer architecture.");
	});

	it("renders key points as bullet list", () => {
		const result = buildSourceNoteBody(baseData);
		expect(result).toContain("- Self-attention replaces recurrence");
		expect(result).toContain("- Parallelisable training");
	});

	it("omits Key Points section when empty", () => {
		const result = buildSourceNoteBody({ ...baseData, keyPoints: [] });
		expect(result).not.toContain("## Key Points");
	});

	it("renders section index when provided", () => {
		const result = buildSourceNoteBody({
			...baseData,
			sectionIndex: [{ section: "00:04:30", topic: "Attention mechanisms" }],
		});
		expect(result).toContain("## Section Index");
		expect(result).toContain("| 00:04:30 | Attention mechanisms |");
	});

	it("omits section index when not provided", () => {
		const result = buildSourceNoteBody(baseData);
		expect(result).not.toContain("## Section Index");
	});
});

describe("buildSourceNote", () => {
	it("combines frontmatter and body with a blank line", () => {
		const result = buildSourceNote(baseData);
		expect(result).toContain("---\n\n# Attention Is All You Need");
	});

	it("ends with a newline", () => {
		const result = buildSourceNote(baseData);
		expect(result.endsWith("\n")).toBe(true);
	});
});

describe("markReadingListItemDone", () => {
	const list = "## Queue\n\n- [ ] https://example.com/a\n- [ ] https://example.com/b\n";

	it("marks the correct item as done", () => {
		const result = markReadingListItemDone(list, "https://example.com/a", "2026-04-09");
		expect(result).toContain("- [ ] https://example.com/a — processed 2026-04-09");
		expect(result).toContain("- [ ] https://example.com/b");
	});

	it("does not modify content if URL is not found", () => {
		const result = markReadingListItemDone(list, "https://nothere.com", "2026-04-09");
		expect(result).toBe(list);
	});
});

describe("addToReadingList", () => {
	it("adds a new URL to the queue", () => {
		const content = "## Queue\n\n*No items queued.*\n\n## Notes\n";
		const result = addToReadingList(content, "https://new.com");
		expect(result).toContain("- [ ] https://new.com");
	});

	it("does not add a duplicate URL", () => {
		const content = "## Queue\n\n- [ ] https://existing.com\n";
		const result = addToReadingList(content, "https://existing.com");
		expect(result.match(/https:\/\/existing\.com/g)?.length).toBe(1);
	});

	it("inserts before completed items", () => {
		const content = "## Queue\n\n- [x] https://done.com\n";
		const result = addToReadingList(content, "https://new.com");
		expect(result.indexOf("https://new.com")).toBeLessThan(result.indexOf("https://done.com"));
	});
});

describe("buildReadingList", () => {
	it("creates a valid frontmatter block", () => {
		const result = buildReadingList("large-language-models", "2026-04-09");
		expect(result).toContain("author: kb-bot");
		expect(result).toContain("topic: large-language-models");
	});

	it("includes provided URLs in the queue", () => {
		const result = buildReadingList("llms", "2026-04-09", ["https://a.com", "https://b.com"]);
		expect(result).toContain("- [ ] https://a.com");
		expect(result).toContain("- [ ] https://b.com");
	});

	it("shows placeholder text when queue is empty", () => {
		const result = buildReadingList("llms", "2026-04-09");
		expect(result).toContain("*No items queued.*");
	});
});

describe("appendDiscussionEntry", () => {
	it("appends a dated entry", () => {
		const base = "# Discussion Log\n\n*Log starts here.*\n";
		const result = appendDiscussionEntry(base, "2026-04-09", "This is wrong", "Fixed note X");
		expect(result).toContain("## 2026-04-09 — Correction");
		expect(result).toContain("**User:** This is wrong");
		expect(result).toContain("**Change made:** Fixed note X");
	});

	it("appends to existing entries rather than replacing them", () => {
		const base = "# Discussion Log\n\n## 2026-04-08 — Correction\n\n**User:** old\n";
		const result = appendDiscussionEntry(base, "2026-04-09", "new", "updated");
		expect(result).toContain("## 2026-04-08 — Correction");
		expect(result).toContain("## 2026-04-09 — Correction");
	});
});

describe("checkpoint functions", () => {
	const checkpoint = buildCheckpoint(
		"Queue ingestion — large-language-models",
		"2026-04-09T10:00:00",
		[
			{ url: "https://a.com", done: false },
			{ url: "https://b.com", done: false },
		]
	);

	it("buildCheckpoint creates a valid checkpoint with in-progress status", () => {
		expect(checkpoint).toContain("**Status:** in-progress");
		expect(checkpoint).toContain("- [ ] https://a.com");
	});

	it("updateCheckpointItem marks an item as complete", () => {
		const updated = updateCheckpointItem(checkpoint, "https://a.com", "2026-04-09T10:05:00");
		expect(updated).toContain("- [ ] https://a.com — completed 2026-04-09T10:05:00");
		expect(updated).toContain("**Last updated:** 2026-04-09T10:05:00");
	});

	it("completeCheckpoint sets status to complete", () => {
		const completed = completeCheckpoint(checkpoint, "2026-04-09T10:30:00");
		expect(completed).toContain("**Status:** complete");
		expect(completed).toContain("**Last updated:** 2026-04-09T10:30:00");
	});
});
