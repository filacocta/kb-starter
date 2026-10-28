import { Editor, MarkdownFileInfo, MarkdownView, Notice, Plugin } from "obsidian";
import { KbBotSettings } from "./types";
import { DEFAULT_SETTINGS } from "./constants";
import { AskModal } from "./AskModal";
import { KbBotSettingTab } from "./KbBotSettingTab";

export default class KbBotPlugin extends Plugin {
	settings: KbBotSettings;

	async onload(): Promise<void> {
		await this.loadSettings();

		this.addCommand({
			id: "ask",
			name: "Ask",
			editorCallback: (editor: Editor, _ctx: MarkdownView | MarkdownFileInfo) => {
				new AskModal(this.app, editor, this.settings).open();
			},
		});

		this.addRibbonIcon("bot", "Ask a question", () => {
			const view = this.app.workspace.getActiveViewOfType(MarkdownView);
			if (view) {
				new AskModal(this.app, view.editor, this.settings).open();
			} else {
				new Notice("Open a note first.");
			}
		});

		this.addSettingTab(new KbBotSettingTab(this.app, this));
	}

	onunload(): void {}

	async loadSettings(): Promise<void> {
		this.settings = { ...DEFAULT_SETTINGS };
		const saved: unknown = await this.loadData();
		if (typeof saved !== "object" || saved === null) return;
		if ("apiKey" in saved && typeof saved.apiKey === "string") this.settings.apiKey = saved.apiKey;
		if ("model" in saved && typeof saved.model === "string") this.settings.model = saved.model;
		if ("maxTokens" in saved && typeof saved.maxTokens === "number" && Number.isInteger(saved.maxTokens) && saved.maxTokens > 0) this.settings.maxTokens = saved.maxTokens;
	}

	async saveSettings(): Promise<void> {
		await this.saveData(this.settings);
	}
}
