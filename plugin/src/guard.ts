import { App, TFile } from "obsidian";

const KB_AUTHOR = "kb-bot";
const KB_ROOT = "knowledgebase";

/** Parse the author field from a YAML frontmatter string. */
function parseAuthor(content: string): string | null {
	const match = content.match(/^---\n([\s\S]*?)\n---/);
	if (!match) return null;
	const authorLine = match[1].split("\n").find(l => l.startsWith("author:"));
	if (!authorLine) return null;
	return authorLine.replace("author:", "").trim().replace(/^["']|["']$/g, "");
}

/** Returns true if the file was created by kb-bot (safe to overwrite). */
export function isKbBotFile(content: string): boolean {
	return parseAuthor(content) === KB_AUTHOR;
}

/** Returns true if the path is within the KB sandbox. */
export function isInsideKb(path: string): boolean {
	const normalised = path.replace(/\\/g, "/").replace(/^\//, "");
	if (normalised.split("/").some(part => part === "." || part === "..")) return false;
	return normalised === KB_ROOT || normalised.startsWith(KB_ROOT + "/");
}

/**
 * Safe write: checks authorship and sandbox before allowing a write.
 * Throws a descriptive error if either check fails.
 * Returns the existing file content if the file exists, or null if it is new.
 */
export async function assertWritable(app: App, path: string): Promise<string | null> {
	if (!isInsideKb(path)) {
		throw new Error(
			`KB Bot authorship guard: "${path}" is outside the knowledgebase/ sandbox. Write aborted.`
		);
	}

	const file = app.vault.getAbstractFileByPath(path);
	if (!file) {
		return null;
	}

	if (!(file instanceof TFile)) {
		throw new Error(`KB Bot authorship guard: "${path}" is a folder, not a file. Write aborted.`);
	}

	const content = await app.vault.read(file);
	if (!isKbBotFile(content)) {
		throw new Error(
			`KB Bot authorship guard: "${path}" exists but was not created by kb-bot (missing author: kb-bot frontmatter). Write aborted.`
		);
	}

	return content;
}

/**
 * Write content to a vault path, enforcing the authorship guard.
 * Use this for all KB writes — never call app.vault.create/modify directly.
 */
export async function safeWrite(app: App, path: string, content: string): Promise<void> {
	const existing = await assertWritable(app, path);

	if (existing === null) {
		await app.vault.create(path, content);
	} else {
		const file = app.vault.getAbstractFileByPath(path);
		if (!(file instanceof TFile)) {
			throw new Error(`KB Bot authorship guard: "${path}" is no longer a file. Write aborted.`);
		}
		await app.vault.modify(file, content);
	}
}
