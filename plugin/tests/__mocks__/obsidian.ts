import { mock } from "node:test";

export const requestUrl = mock.fn(async (_request: unknown): Promise<{ status: number; json: unknown }> => {
	throw new Error("No request fixture configured");
});

export class TAbstractFile {
	path = "";
	name = "";
	parent: TFolder | null = null;
}

export class TFile extends TAbstractFile {
	basename = "";
	extension = "md";
	stat = { ctime: 0, mtime: 0, size: 0 };
}

export class TFolder extends TAbstractFile {
	children: TAbstractFile[] = [];
	isRoot() { return false; }
}

export class App {}
export class Modal {
	app: App;
	contentEl = {
		empty() {},
		addClass() {},
		createDiv() { return this; },
		createEl() { return this; },
		createSpan() { return this; },
		setText() { return this; },
		style: {},
		focus() {},
		onkeydown: null as ((e: KeyboardEvent) => void) | null,
	} as any;
	constructor(app: App) { this.app = app; }
	open() {}
	close() {}
}
export class PluginSettingTab { constructor(_app: App, _plugin: any) {} display() {} }
export class Setting {
	constructor(_containerEl: any) {}
	setName(_name: string) { return this; }
	setDesc(_desc: string) { return this; }
	addText(_cb: (text: any) => any) { return this; }
}
export class Notice { constructor(_msg: string) {} }
export class Plugin { constructor(_app: App, _manifest: any) {} }

export function normalizePath(path: string): string {
	return path.replace(/[\\/]+/g, "/").replace(/\u00A0/g, " ").normalize().replace(/^\/+|\/+$/g, "");
}
