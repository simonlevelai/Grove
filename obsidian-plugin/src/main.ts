import { Plugin } from "obsidian";
import { GroveSettings, DEFAULT_SETTINGS, GroveSettingTab } from "./settings";
import { GroveRunner } from "./runner";
import { registerCompileCommand } from "./compile";
import { registerQueryPanel, QUERY_VIEW_TYPE } from "./query";
import { resolveGroveCwd } from "./grove-detector";

/**
 * Grove — Obsidian plugin entry point.
 *
 * Thin TypeScript client that spawns the Grove Python CLI as a subprocess
 * for compile, query, and health operations. All heavy lifting stays in
 * the Python engine; this plugin handles UI, settings, and subprocess
 * lifecycle only.
 */
export default class GrovePlugin extends Plugin {
	settings: GroveSettings = DEFAULT_SETTINGS;
	private runner: GroveRunner | null = null;

	async onload(): Promise<void> {
		await this.loadSettings();

		// Initialise the subprocess runner
		this.initRunner();

		// Register the settings tab
		this.addSettingTab(new GroveSettingTab(this.app, this));

		// Register commands
		registerCompileCommand(this);

		// Register the query sidebar panel and ribbon icon
		registerQueryPanel(this);

		console.log("Grove plugin loaded");
	}

	async onunload(): Promise<void> {
		// Kill any active subprocess
		if (this.runner) {
			this.runner.kill();
		}

		// Detach query panel leaves
		this.app.workspace.detachLeavesOfType(QUERY_VIEW_TYPE);

		console.log("Grove plugin unloaded");
	}

	// ── Settings persistence ──────────────────────────────────────────

	async loadSettings(): Promise<void> {
		const data = await this.loadData();
		this.settings = Object.assign({}, DEFAULT_SETTINGS, data);
	}

	async saveSettings(): Promise<void> {
		await this.saveData(this.settings);
		// Reconfigure the runner when settings change
		this.initRunner();
	}

	// ── Runner lifecycle ──────────────────────────────────────────────

	/**
	 * (Re)initialise the GroveRunner with current settings.
	 * Called on load and whenever settings are saved.
	 */
	private initRunner(): void {
		const cwd = resolveGroveCwd(this);
		const cliPath = this.settings.groveCliPath;
		const apiKey = this.settings.anthropicApiKey;

		if (this.runner) {
			this.runner.setCliPath(cliPath);
			this.runner.setCwd(cwd);
			this.runner.setApiKey(apiKey);
		} else {
			this.runner = new GroveRunner(cliPath, cwd, apiKey);
		}
	}

	/**
	 * Returns the shared GroveRunner instance.
	 * Commands and panels use this to execute grove CLI calls.
	 */
	getRunner(): GroveRunner | null {
		return this.runner;
	}
}
