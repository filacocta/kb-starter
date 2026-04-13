/**
 * Pure functions for building KB source notes from fetched content.
 * No Obsidian API dependencies — all functions are plain TypeScript and fully testable.
 */

export type SourceType = "article" | "pdf" | "transcript" | "other";

export interface SourceNoteData {
	title: string;
	origin: string;
	type: SourceType;
	topic: string;
	tags: string[];
	date: string; // ISO-8601 YYYY-MM-DD
	summary: string;
	keyPoints: string[];
	sectionIndex?: Array<{ section: string; topic: string }>;
}

/** Convert arbitrary text to a lowercase hyphen-separated slug, max 60 chars. */
export function slugify(text: string): string {
	return text
		.toLowerCase()
		.normalize("NFD")
		.replace(/[\u0300-\u036f]/g, "") // strip diacritics
		.replace(/[^a-z0-9]+/g, "-")
		.replace(/^-+|-+$/g, "")
		.slice(0, 60);
}

/** Build the source note filename: `YYYY-MM-DD-<slug>.md` */
export function buildSourceNoteFilename(date: string, title: string): string {
	return `${date}-${slugify(title)}.md`;
}

/** Build the YAML frontmatter block for a source note. */
export function buildSourceNoteFrontmatter(data: SourceNoteData): string {
	const tags = data.tags.map(t => `"${t}"`).join(", ");
	const escapedOrigin = data.origin.replace(/"/g, '\\"');
	return [
		"---",
		"author: kb-bot",
		`created: ${data.date}`,
		`modified: ${data.date}`,
		"type: source",
		`topic: ${data.topic}`,
		`tags: [${tags}]`,
		"sources: []",
		"related: []",
		`origin: "${escapedOrigin}"`,
		"---",
	].join("\n");
}

/** Build the note body (everything after frontmatter) for a source note. */
export function buildSourceNoteBody(data: SourceNoteData): string {
	const lines: string[] = [
		`# ${data.title}`,
		"",
		`**Origin:** ${data.origin}`,
		`**Ingested:** ${data.date}`,
		`**Type:** ${data.type}`,
		"",
		"## Summary",
		"",
		data.summary,
	];

	if (data.keyPoints.length > 0) {
		lines.push("", "## Key Points", "");
		for (const point of data.keyPoints) {
			lines.push(`- ${point}`);
		}
	}

	if (data.sectionIndex && data.sectionIndex.length > 0) {
		lines.push("", "## Section Index", "");
		lines.push("| Section | Topic |");
		lines.push("|---|---|");
		for (const entry of data.sectionIndex) {
			lines.push(`| ${entry.section} | ${entry.topic} |`);
		}
	}

	return lines.join("\n");
}

/** Combine frontmatter and body into a complete source note. */
export function buildSourceNote(data: SourceNoteData): string {
	return buildSourceNoteFrontmatter(data) + "\n\n" + buildSourceNoteBody(data) + "\n";
}

/**
 * Mark a URL in a reading list as done.
 * Replaces `- [ ] <url>` with `- [x] <url> — processed <date>`.
 * Returns the updated content unchanged if the URL is not found.
 */
export function markReadingListItemDone(content: string, url: string, date: string): string {
	const escaped = url.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
	const pattern = new RegExp(`^(\\s*- \\[ \\] ${escaped})(\\s*)$`, "m");
	return content.replace(pattern, `$1 — processed ${date}$2`);
}

/**
 * Add a URL to the queue section of a reading list.
 * Inserts above the first `- [x]` completed item, or at the end of the Queue section.
 * Returns the updated content unchanged if the URL is already present.
 */
export function addToReadingList(content: string, url: string): string {
	if (content.includes(url)) return content;

	const newItem = `- [ ] ${url}`;

	// Insert before first completed item if one exists
	const completedPattern = /^(\s*- \[x\])/m;
	if (completedPattern.test(content)) {
		return content.replace(completedPattern, `${newItem}\n$1`);
	}

	// Otherwise append before the Notes section or at end of Queue section
	const notesSectionPattern = /^## Notes/m;
	if (notesSectionPattern.test(content)) {
		return content.replace(notesSectionPattern, `${newItem}\n\n## Notes`);
	}

	return content.trimEnd() + "\n" + newItem + "\n";
}

/** Build a new reading-list.md from scratch for a topic. */
export function buildReadingList(topic: string, date: string, items: string[] = []): string {
	const queue = items.length > 0
		? items.map(url => `- [ ] ${url}`).join("\n")
		: "*No items queued.*";

	return [
		"---",
		"author: kb-bot",
		`created: ${date}`,
		`modified: ${date}`,
		"type: meta",
		`topic: ${topic}`,
		"---",
		"",
		`# Reading List — ${topic}`,
		"",
		"## Queue",
		"",
		queue,
		"",
		"## Notes",
		"",
		"*Add context here for the agent to use when processing items above.*",
		"",
	].join("\n");
}

/** Build a new _discussion.md log file for a topic. */
export function buildDiscussionLog(topic: string, date: string): string {
	return [
		"---",
		"author: kb-bot",
		`created: ${date}`,
		`modified: ${date}`,
		"type: meta",
		`topic: ${topic}`,
		"---",
		"",
		`# Discussion Log — ${topic}`,
		"",
		"*Corrections and conversation notes are appended here chronologically.*",
		"",
	].join("\n");
}

/** Append a correction entry to a _discussion.md log. */
export function appendDiscussionEntry(
	content: string,
	date: string,
	userMessage: string,
	changeDescription: string
): string {
	const entry = [
		`## ${date} — Correction`,
		"",
		`**User:** ${userMessage}`,
		`**Change made:** ${changeDescription}`,
		"",
	].join("\n");

	return content.trimEnd() + "\n\n" + entry;
}

/** Build a _checkpoint.md file for a batch operation. */
export function buildCheckpoint(
	operation: string,
	startedAt: string,
	queue: Array<{ url: string; done: boolean; completedAt?: string }>
): string {
	const queueLines = queue.map(item => {
		if (item.done) {
			return `- [x] ${item.url}${item.completedAt ? ` — completed ${item.completedAt}` : ""}`;
		}
		return `- [ ] ${item.url}`;
	});

	return [
		"---",
		"author: kb-bot",
		`created: ${startedAt}`,
		`modified: ${startedAt}`,
		"type: meta",
		"---",
		"",
		"# Checkpoint",
		"",
		`**Operation:** ${operation}`,
		`**Started:** ${startedAt}`,
		`**Last updated:** ${startedAt}`,
		"**Status:** in-progress",
		"",
		"## Queue State",
		"",
		...queueLines,
		"",
		"## Pending Writes",
		"",
		"None",
		"",
	].join("\n");
}

/** Update the status and last-updated fields of an existing checkpoint. */
export function updateCheckpointItem(
	content: string,
	url: string,
	completedAt: string
): string {
	const escaped = url.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
	const pattern = new RegExp(`^(\\s*- \\[ \\] ${escaped})(\\s*)$`, "m");
	const withItem = content.replace(pattern, `$1 — completed ${completedAt}$2`);

	// Update "Last updated" field
	return withItem.replace(
		/^\*\*Last updated:\*\*.+$/m,
		`**Last updated:** ${completedAt}`
	);
}

/** Mark a checkpoint as complete. */
export function completeCheckpoint(content: string, completedAt: string): string {
	return content
		.replace(/^\*\*Status:\*\*.+$/m, "**Status:** complete")
		.replace(/^\*\*Last updated:\*\*.+$/m, `**Last updated:** ${completedAt}`);
}
