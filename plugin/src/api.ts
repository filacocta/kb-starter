import { KbBotSettings } from "./types";
import { ANTHROPIC_API_URL, ANTHROPIC_VERSION } from "./constants";

/** Build the user prompt, optionally prepending selected editor text as context. */
export function buildPrompt(question: string, selectedText?: string): string {
	if (selectedText && selectedText.trim()) {
		return `Context:\n${selectedText.trim()}\n\nQuestion: ${question}`;
	}
	return question;
}

/** Format the API response text as an Obsidian callout block. */
export function formatAsCallout(text: string): string {
	const body = text.replace(/\n/g, "\n> ");
	return `\n\n> [!note] KB Bot\n> ${body}\n`;
}

/** Call the Anthropic Messages API and return the assistant's reply text. */
export async function askClaude(prompt: string, settings: KbBotSettings): Promise<string> {
	const response = await fetch(ANTHROPIC_API_URL, {
		method: "POST",
		headers: {
			"Content-Type": "application/json",
			"x-api-key": settings.apiKey,
			"anthropic-version": ANTHROPIC_VERSION,
		},
		body: JSON.stringify({
			model: settings.model,
			max_tokens: settings.maxTokens,
			messages: [{ role: "user", content: prompt }],
		}),
	});

	if (!response.ok) {
		throw new Error(`API error: ${response.status} ${response.statusText}`);
	}

	const data = await response.json();
	return data.content[0].text as string;
}
