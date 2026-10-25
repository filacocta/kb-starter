import { App, PluginSettingTab, Setting } from "obsidian";
import type KbBotPlugin from "./KbBotPlugin";

export class KbBotSettingTab extends PluginSettingTab {
	private plugin: KbBotPlugin;

	constructor(app: App, plugin: KbBotPlugin) {
		super(app, plugin);
		this.plugin = plugin;
	}

	display(): void {
		const { containerEl } = this;
		containerEl.empty();

		new Setting(containerEl)
			.setName("Anthropic API key")
			.setDesc("Your key from console.anthropic.com — stored locally in data.json.")
			.addText(text =>
				text
					.setPlaceholder("sk-ant-…")
					.setValue(this.plugin.settings.apiKey)
					.onChange(async value => {
						this.plugin.settings.apiKey = value;
						await this.plugin.saveSettings();
					})
			);

		new Setting(containerEl)
			.setName("Model")
			.setDesc("An available Anthropic model ID for your account. No model is selected automatically.")
			.addText(text =>
				text
					.setPlaceholder("Enter your available model ID")
					.setValue(this.plugin.settings.model)
					.onChange(async value => {
						this.plugin.settings.model = value;
						await this.plugin.saveSettings();
					})
			);

		new Setting(containerEl)
			.setName("Max tokens")
			.setDesc("Maximum tokens in the response (default 1024).")
			.addText(text =>
				text
					.setPlaceholder("1024")
					.setValue(String(this.plugin.settings.maxTokens))
					.onChange(async value => {
						const parsed = parseInt(value, 10);
						if (!isNaN(parsed) && parsed > 0) {
							this.plugin.settings.maxTokens = parsed;
							await this.plugin.saveSettings();
						}
					})
			);
	}
}
