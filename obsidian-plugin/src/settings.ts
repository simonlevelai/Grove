import { App, Notice, PluginSettingTab, Setting } from "obsidian";
import type GrovePlugin from "./main";
import { detectGroveCli } from "./runner";
import { GroveDetector, addGroveSelector } from "./grove-detector";

// ---------------------------------------------------------------------------
// Settings interface — persisted to data.json via Plugin.loadData()
// ---------------------------------------------------------------------------

export interface GroveSettings {
	anthropicApiKey: string;
	groveCliPath: string;
	defaultGrovePath: string;
	dryRunByDefault: boolean;
	defaultQueryMode: "quick" | "deep";
}

export const DEFAULT_SETTINGS: GroveSettings = {
	anthropicApiKey: "",
	groveCliPath: "/usr/local/bin/grove",
	defaultGrovePath: "",
	dryRunByDefault: false,
	defaultQueryMode: "deep",
};

// ---------------------------------------------------------------------------
// Settings tab — displayed in Obsidian Settings > Community Plugins > Grove
// ---------------------------------------------------------------------------

export class GroveSettingTab extends PluginSettingTab {
	plugin: GrovePlugin;

	constructor(app: App, plugin: GrovePlugin) {
		super(app, plugin);
		this.plugin = plugin;
	}

	display(): void {
		const { containerEl } = this;
		containerEl.empty();

		containerEl.createEl("h2", { text: "Grove — Settings" });

		// ── API Key (masked) ──────────────────────────────────────────
		new Setting(containerEl)
			.setName("Anthropic API key")
			.setDesc(
				"Your API key is stored locally in this vault's plugin data. " +
				"It is passed to the grove CLI via an environment variable and never written to any grove-tracked file."
			)
			.addText((text) => {
				text.inputEl.type = "password";
				text.inputEl.autocomplete = "off";
				text
					.setPlaceholder("sk-ant-...")
					.setValue(this.plugin.settings.anthropicApiKey)
					.onChange(async (value) => {
						this.plugin.settings.anthropicApiKey = value;
						await this.plugin.saveSettings();
					});
			});

		// ── CLI Path ──────────────────────────────────────────────────
		const cliPathSetting = new Setting(containerEl)
			.setName("Grove CLI path")
			.setDesc(
				"Absolute path to the grove binary. " +
				"Use the auto-detect button to search common installation locations."
			)
			.addText((text) => {
				text
					.setPlaceholder("/usr/local/bin/grove")
					.setValue(this.plugin.settings.groveCliPath)
					.onChange(async (value) => {
						this.plugin.settings.groveCliPath = value;
						await this.plugin.saveSettings();
					});
			});

		cliPathSetting.addButton((button) => {
			button.setButtonText("Auto-detect").onClick(async () => {
				button.setDisabled(true);
				button.setButtonText("Searching...");
				const detected = await detectGroveCli();
				if (detected) {
					this.plugin.settings.groveCliPath = detected;
					await this.plugin.saveSettings();
					new Notice(`Grove CLI found: ${detected}`);
					this.display(); // refresh the settings page
				} else {
					new Notice(
						"Could not find the grove CLI. Install it with: pip install grove-kb",
						8000
					);
				}
				button.setDisabled(false);
				button.setButtonText("Auto-detect");
			});
		});

		// ── Grove Detector (multi-grove support) ──────────────────────
		containerEl.createEl("h3", { text: "Grove detection" });

		const detectorSection = containerEl.createDiv({ cls: "grove-detector-section" });
		detectorSection.createEl("p", {
			text: "Scanning vault for grove directories...",
			cls: "grove-detector-status",
		});

		// Run the scan asynchronously and update the UI when complete
		const detector = new GroveDetector(this.app);
		detector.scan().then((groves) => {
			detectorSection.empty();
			addGroveSelector(detectorSection, this.plugin, groves);

			// If only one grove found, also show it as the manual path
			if (groves.length === 1 && !this.plugin.settings.defaultGrovePath) {
				this.plugin.settings.defaultGrovePath = groves[0].rootPath;
				this.plugin.saveSettings();
			}
		});

		// Add a rescan button
		new Setting(containerEl).addButton((button) => {
			button.setButtonText("Rescan vault").onClick(async () => {
				button.setDisabled(true);
				button.setButtonText("Scanning...");
				const groves = await detector.scan();
				detectorSection.empty();
				addGroveSelector(detectorSection, this.plugin, groves);
				button.setDisabled(false);
				button.setButtonText("Rescan vault");
				new Notice(`Found ${groves.length} grove ${groves.length === 1 ? "directory" : "directories"}.`);
			});
		});

		// ── Active Grove Path (manual override) ───────────────────────
		new Setting(containerEl)
			.setName("Active grove path (manual override)")
			.setDesc(
				"The working directory for grove commands. " +
				"Leave blank to use auto-detected grove, or enter a path outside the vault."
			)
			.addText((text) => {
				text
					.setPlaceholder("(auto-detected)")
					.setValue(this.plugin.settings.defaultGrovePath)
					.onChange(async (value) => {
						this.plugin.settings.defaultGrovePath = value;
						await this.plugin.saveSettings();
					});
			});

		// ── Dry-run Toggle ────────────────────────────────────────────
		new Setting(containerEl)
			.setName("Dry-run by default")
			.setDesc(
				"When enabled, the Compile command shows estimated tokens and cost " +
				"without making LLM calls or modifying files."
			)
			.addToggle((toggle) => {
				toggle
					.setValue(this.plugin.settings.dryRunByDefault)
					.onChange(async (value) => {
						this.plugin.settings.dryRunByDefault = value;
						await this.plugin.saveSettings();
					});
			});

		// ── Default Query Mode ────────────────────────────────────────
		new Setting(containerEl)
			.setName("Default query mode")
			.setDesc(
				"Quick: searches the index only, faster but less thorough. " +
				"Deep: loads relevant articles and synthesises a full answer."
			)
			.addDropdown((dropdown) => {
				dropdown
					.addOption("quick", "Quick")
					.addOption("deep", "Deep")
					.setValue(this.plugin.settings.defaultQueryMode)
					.onChange(async (value) => {
						this.plugin.settings.defaultQueryMode = value as "quick" | "deep";
						await this.plugin.saveSettings();
					});
			});
	}
}
