import tsparser from "@typescript-eslint/parser";
import { defineConfig } from "eslint/config";
import obsidianmd from "eslint-plugin-obsidianmd";
import globals from "globals";

export default defineConfig([
	{ ignores: ["**/tests/**", "node_modules/", "*.mjs", "main.js"] },

	...obsidianmd.configs.recommended,

	{
		files: ["**/*.{js,ts}"],

		languageOptions: {
			globals: {
				...globals.browser,
			},
			parser: tsparser,
			parserOptions: { project: "./tsconfig.json" },
		},
		plugins: {
			obsidianmd,
		},
		rules: {
			"obsidianmd/sample-names": "off",
			"obsidianmd/ui/sentence-case": ["warn", { allowAutoFix: true }],
		},
	},
]);
