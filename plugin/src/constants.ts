import { KbBotSettings } from "./types";

export const DEFAULT_SETTINGS: KbBotSettings = {
	apiKey: "",
	model: "",
	maxTokens: 1024,
};

export const ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages";
export const ANTHROPIC_VERSION = "2023-06-01";
