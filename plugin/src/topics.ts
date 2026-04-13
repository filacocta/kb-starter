/**
 * Pure functions for KB topic folder management.
 * No Obsidian API dependencies — fully testable.
 */

import { slugify } from "./ingest";

const KB_ROOT = "knowledgebase";

/** Returns true if a string is a valid topic slug (lowercase, hyphenated, no special chars). */
export function isValidTopicSlug(slug: string): boolean {
	return /^[a-z0-9]+(-[a-z0-9]+)*$/.test(slug) && slug.length > 0 && slug.length <= 60;
}

/** Convert a human-readable topic name to a vault slug. */
export function slugifyTopic(name: string): string {
	return slugify(name);
}

/** Vault-relative path to a topic's folder. */
export function topicPath(topic: string): string {
	return `${KB_ROOT}/topics/${topic}`;
}

/** Vault-relative path to a topic's _summary.md. */
export function topicSummaryPath(topic: string): string {
	return `${topicPath(topic)}/_summary.md`;
}

/** Vault-relative path to a topic's sources folder. */
export function topicSourcesPath(topic: string): string {
	return `${topicPath(topic)}/sources`;
}

/** Vault-relative path to a topic's notes folder. */
export function topicNotesPath(topic: string): string {
	return `${topicPath(topic)}/notes`;
}

/** Vault-relative path to a topic's reading-list.md. */
export function topicReadingListPath(topic: string): string {
	return `${topicPath(topic)}/reading-list.md`;
}

/** Vault-relative path to a topic's _discussion.md. */
export function topicDiscussionPath(topic: string): string {
	return `${topicPath(topic)}/_discussion.md`;
}

/** Vault-relative path for a source note. */
export function sourceNotePath(topic: string, filename: string): string {
	return `${topicSourcesPath(topic)}/${filename}`;
}

export interface TopicStats {
	noteCount: number;
	sourceCount: number;
	lastUpdated: string; // YYYY-MM-DD
}

/** Build a new topic _summary.md from scratch. */
export function buildTopicSummary(topic: string, date: string): string {
	return [
		"---",
		"author: kb-bot",
		`created: ${date}`,
		`modified: ${date}`,
		"type: summary",
		`topic: ${topic}`,
		"tags: []",
		"---",
		"",
		`# ${topic}`,
		"",
		"*This summary is kept current as notes are added. Agents: read this before diving into sub-notes.*",
		"",
		"## Overview",
		"",
		"*No notes yet.*",
		"",
		"## Key Concepts",
		"",
		"*Will be populated as notes are synthesised.*",
		"",
		"## Sources",
		"",
		"*No sources ingested yet.*",
		"",
		"## Gaps & Open Questions",
		"",
		"*Will be identified as the topic develops.*",
		"",
		"---",
		"",
		`*Last updated: ${date} — 0 notes, 0 sources*`,
		"",
	].join("\n");
}

/**
 * Update or insert a topic row in the _index.md topic table.
 * The table is expected to have columns: Topic | Summary | Notes | Sources | Last updated
 * If the topic row already exists, it is updated in place.
 * If it doesn't exist, it is appended to the table.
 */
export function updateIndexTopicRow(
	indexContent: string,
	topic: string,
	stats: TopicStats
): string {
	const summaryLink = `[[topics/${topic}/_summary]]`;
	const newRow = `| [[topics/${topic}\\|${topic}]] | ${summaryLink} | ${stats.noteCount} | ${stats.sourceCount} | ${stats.lastUpdated} |`;

	// Try to update an existing row for this topic
	const existingRowPattern = new RegExp(
		`^\\|\\s*\\[\\[topics/${topic}(?:\\\\\\|[^\\]]+)?\\]\\].*$`,
		"m"
	);

	if (existingRowPattern.test(indexContent)) {
		return indexContent.replace(existingRowPattern, newRow);
	}

	// Insert before the closing of the table (first blank line after the header row)
	// Find the table header row and insert new row after it (and after the separator)
	const tablePattern = /(\| Topic \| Summary \| Notes \| Sources \| Last updated \|\n\|[-| ]+\|\n)/;
	if (tablePattern.test(indexContent)) {
		return indexContent.replace(tablePattern, `$1${newRow}\n`);
	}

	// Fallback: append to content
	return indexContent.trimEnd() + "\n" + newRow + "\n";
}

/**
 * Update the KB Statistics section in _index.md.
 */
export function updateIndexStats(
	indexContent: string,
	topicCount: number,
	totalNotes: number,
	totalSources: number,
	lastIngestion: string
): string {
	return indexContent
		.replace(/^- Topics: .+$/m, `- Topics: ${topicCount}`)
		.replace(/^- Total notes: .+$/m, `- Total notes: ${totalNotes}`)
		.replace(/^- Total sources ingested: .+$/m, `- Total sources ingested: ${totalSources}`)
		.replace(/^- Last ingestion: .+$/m, `- Last ingestion: ${lastIngestion}`);
}
