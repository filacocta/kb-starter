import { App, Editor, Modal, Notice } from "obsidian";
import { KbBotSettings } from "./types";
import { askClaude, buildPrompt, formatAsCallout } from "./api";

export class AskModal extends Modal {
	private editor: Editor;
	private settings: KbBotSettings;

	constructor(app: App, editor: Editor, settings: KbBotSettings) {
		super(app);
		this.editor = editor;
		this.settings = settings;
	}

	onOpen(): void {
		const { contentEl } = this;
		contentEl.createEl("h2", { text: "Ask KB Bot" });

		const input = contentEl.createEl("textarea", {
			attr: { rows: "4", style: "width:100%; margin-bottom:8px; box-sizing:border-box;" },
		});
		input.focus();

		const btn = contentEl.createEl("button", { text: "Send" });
		btn.onclick = () => this.submit(input.value.trim());

		// Also submit on Ctrl/Cmd+Enter
		input.onkeydown = (e: KeyboardEvent) => {
			if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
				this.submit(input.value.trim());
			}
		};
	}

	private async submit(question: string): Promise<void> {
		if (!question) return;

		if (!this.settings.apiKey) {
			new Notice("KB Bot: add your API key in Settings → KB Bot.");
			this.close();
			return;
		}

		const selectedText = this.editor.getSelection();
		const prompt = buildPrompt(question, selectedText);

		new Notice("KB Bot is thinking…");
		this.close();

		try {
			const answer = await askClaude(prompt, this.settings);
			const insertion = formatAsCallout(answer);
			const cursor = this.editor.getCursor("to");
			this.editor.replaceRange(insertion, cursor);
		} catch (e: unknown) {
			const msg = e instanceof Error ? e.message : String(e);
			new Notice(`KB Bot error: ${msg}`);
		}
	}

	onClose(): void {
		this.contentEl.empty();
	}
}
