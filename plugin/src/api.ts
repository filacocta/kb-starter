import { KbBotSettings } from "./types";
import { ANTHROPIC_API_URL, ANTHROPIC_VERSION } from "./constants";
import { requestUrl } from "obsidian";

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
	if (!settings.apiKey.trim() || !settings.model.trim()) {
		throw new Error("Configure an Anthropic API key and an available model ID in KB Bot settings.");
	}
	const response = await requestUrl({
		url: ANTHROPIC_API_URL,
		method: "POST",
		throw: false,
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

	if (response.status < 200 || response.status >= 300) {
		throw new Error(`API error: ${response.status}`);
	}

	const data: unknown = response.json;
	if (typeof data !== "object" || data === null || !("content" in data) || !Array.isArray(data.content)) {
		throw new Error("Invalid API response: expected message content.");
	}
	const first: unknown = data.content[0];
	if (typeof first !== "object" || first === null || !("type" in first) || first.type !== "text" || !("text" in first) || typeof first.text !== "string") {
		throw new Error("Invalid API response: expected a text block.");
	}
	return first.text;
}
