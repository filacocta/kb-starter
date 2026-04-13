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
			id: "ask-kb-bot",
			name: "Ask KB Bot",
			editorCallback: (editor: Editor, _ctx: MarkdownView | MarkdownFileInfo) => {
				new AskModal(this.app, editor, this.settings).open();
			},
		});

		this.addRibbonIcon("bot", "Ask KB Bot", () => {
			const view = this.app.workspace.getActiveViewOfType(MarkdownView);
			if (view) {
				new AskModal(this.app, view.editor, this.settings).open();
			} else {
				new Notice("KB Bot: open a note first.");
			}
		});

		this.addSettingTab(new KbBotSettingTab(this.app, this));
	}

	onunload(): void {}

	async loadSettings(): Promise<void> {
		this.settings = Object.assign({}, DEFAULT_SETTINGS, await this.loadData());
	}

	async saveSettings(): Promise<void> {
		await this.saveData(this.settings);
	}
}
