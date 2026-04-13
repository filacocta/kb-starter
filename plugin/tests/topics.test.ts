import { describe, it } from "node:test";
import { expect } from "expect";
import {
	isValidTopicSlug,
	slugifyTopic,
	topicPath,
	topicSummaryPath,
	topicSourcesPath,
	topicNotesPath,
	topicReadingListPath,
	sourceNotePath,
	buildTopicSummary,
	updateIndexTopicRow,
	updateIndexStats,
} from "../src/topics";

describe("isValidTopicSlug", () => {
	it("accepts valid slugs", () => {
		expect(isValidTopicSlug("large-language-models")).toBe(true);
		expect(isValidTopicSlug("math")).toBe(true);
		expect(isValidTopicSlug("ai-safety")).toBe(true);
		expect(isValidTopicSlug("gpt4")).toBe(true);
	});

	it("rejects slugs with uppercase letters", () => {
		expect(isValidTopicSlug("LLMs")).toBe(false);
	});

	it("rejects slugs with spaces", () => {
		expect(isValidTopicSlug("large language models")).toBe(false);
	});

	it("rejects slugs with leading or trailing hyphens", () => {
		expect(isValidTopicSlug("-llms")).toBe(false);
		expect(isValidTopicSlug("llms-")).toBe(false);
	});

	it("rejects slugs with consecutive hyphens", () => {
		expect(isValidTopicSlug("llms--models")).toBe(false);
	});

	it("rejects empty string", () => {
		expect(isValidTopicSlug("")).toBe(false);
	});

	it("rejects slugs longer than 60 characters", () => {
		expect(isValidTopicSlug("a".repeat(61))).toBe(false);
	});
});

describe("slugifyTopic", () => {
	it("converts a human-readable name to a slug", () => {
		expect(slugifyTopic("Large Language Models")).toBe("large-language-models");
	});

	it("strips special characters", () => {
		expect(slugifyTopic("Philosophy of Mind & Consciousness")).toBe(
			"philosophy-of-mind-consciousness"
		);
	});
});

describe("path helpers", () => {
	it("topicPath", () => {
		expect(topicPath("llms")).toBe("knowledgebase/topics/llms");
	});

	it("topicSummaryPath", () => {
		expect(topicSummaryPath("llms")).toBe("knowledgebase/topics/llms/_summary.md");
	});

	it("topicSourcesPath", () => {
		expect(topicSourcesPath("llms")).toBe("knowledgebase/topics/llms/sources");
	});

	it("topicNotesPath", () => {
		expect(topicNotesPath("llms")).toBe("knowledgebase/topics/llms/notes");
	});

	it("topicReadingListPath", () => {
		expect(topicReadingListPath("llms")).toBe("knowledgebase/topics/llms/reading-list.md");
	});

	it("sourceNotePath", () => {
		expect(sourceNotePath("llms", "2026-04-09-transformers.md")).toBe(
			"knowledgebase/topics/llms/sources/2026-04-09-transformers.md"
		);
	});
});

describe("buildTopicSummary", () => {
	it("includes valid frontmatter", () => {
		const result = buildTopicSummary("large-language-models", "2026-04-09");
		expect(result).toContain("author: kb-bot");
		expect(result).toContain("type: summary");
		expect(result).toContain("topic: large-language-models");
	});

	it("uses the topic name as the h1 heading", () => {
		const result = buildTopicSummary("large-language-models", "2026-04-09");
		expect(result).toContain("# large-language-models");
	});

	it("includes standard section headings", () => {
		const result = buildTopicSummary("llms", "2026-04-09");
		expect(result).toContain("## Overview");
		expect(result).toContain("## Key Concepts");
		expect(result).toContain("## Sources");
		expect(result).toContain("## Gaps & Open Questions");
	});
});

describe("updateIndexTopicRow", () => {
	const baseIndex = `# Knowledge Base Index

## Topics

| Topic | Summary | Notes | Sources | Last updated |
|---|---|---|---|---|

## Projects`;

	it("inserts a new row for a new topic", () => {
		const result = updateIndexTopicRow(baseIndex, "llms", {
			noteCount: 3,
			sourceCount: 5,
			lastUpdated: "2026-04-09",
		});
		expect(result).toContain("[[topics/llms");
		expect(result).toContain("| 3 | 5 | 2026-04-09 |");
	});

	it("updates an existing row in place", () => {
		const withRow = updateIndexTopicRow(baseIndex, "llms", {
			noteCount: 3,
			sourceCount: 5,
			lastUpdated: "2026-04-09",
		});
		const updated = updateIndexTopicRow(withRow, "llms", {
			noteCount: 5,
			sourceCount: 8,
			lastUpdated: "2026-04-10",
		});
		// Old stats should be gone, new stats present
		expect(updated).not.toContain("| 3 | 5 | 2026-04-09 |");
		expect(updated).toContain("| 5 | 8 | 2026-04-10 |");
		// Exactly one data row for this topic (table header + separator don't count)
		const dataRows = updated.split("\n").filter(l => l.startsWith("| [[topics/llms"));
		expect(dataRows.length).toBe(1);
	});
});

describe("updateIndexStats", () => {
	const baseIndex = `## KB Statistics

- Topics: 0
- Total notes: 0
- Total sources ingested: 0
- Last ingestion: —`;

	it("updates all statistics fields", () => {
		const result = updateIndexStats(baseIndex, 2, 10, 5, "2026-04-09");
		expect(result).toContain("- Topics: 2");
		expect(result).toContain("- Total notes: 10");
		expect(result).toContain("- Total sources ingested: 5");
		expect(result).toContain("- Last ingestion: 2026-04-09");
	});
});
