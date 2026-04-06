import { App, Notice, Setting } from "obsidian";
import type GrovePlugin from "./main";
import * as path from "path";
import * as fs from "fs";

// ---------------------------------------------------------------------------
// GroveDetector — scans the vault for directories containing .grove/config.yaml
// ---------------------------------------------------------------------------

export interface DetectedGrove {
	/** Absolute path to the directory containing .grove/ */
	rootPath: string;
	/** Display label (relative to vault root) */
	label: string;
}

export class GroveDetector {
	private app: App;
	private detectedGroves: DetectedGrove[] = [];

	constructor(app: App) {
		this.app = app;
	}

	/**
	 * Scan the vault filesystem for directories containing `.grove/config.yaml`.
	 * Searches up to 3 levels deep to avoid excessive traversal on large vaults.
	 */
	async scan(): Promise<DetectedGrove[]> {
		this.detectedGroves = [];
		const vaultRoot = this.getVaultBasePath();
		if (!vaultRoot) return [];

		await this.scanDirectory(vaultRoot, vaultRoot, 0, 3);
		return this.detectedGroves;
	}

	/** Returns the last scan results without re-scanning. */
	getDetectedGroves(): DetectedGrove[] {
		return this.detectedGroves;
	}

	/** Returns the vault's absolute filesystem path. */
	private getVaultBasePath(): string | null {
		const adapter = this.app.vault.adapter;
		if ("getBasePath" in adapter && typeof adapter.getBasePath === "function") {
			return adapter.getBasePath() as string;
		}
		return null;
	}

	/**
	 * Recursively scan directories looking for .grove/config.yaml.
	 * Skips hidden directories (except .grove itself), node_modules, and similar.
	 */
	private async scanDirectory(
		dir: string,
		vaultRoot: string,
		depth: number,
		maxDepth: number,
	): Promise<void> {
		if (depth > maxDepth) return;

		const groveConfigPath = path.join(dir, ".grove", "config.yaml");
		try {
			if (fs.existsSync(groveConfigPath)) {
				const relativePath = path.relative(vaultRoot, dir);
				this.detectedGroves.push({
					rootPath: dir,
					label: relativePath || "(vault root)",
				});
			}
		} catch {
			// Permission error or similar — skip
			return;
		}

		// Recurse into subdirectories
		let entries: fs.Dirent[];
		try {
			entries = fs.readdirSync(dir, { withFileTypes: true });
		} catch {
			return;
		}

		const skipDirs = new Set([
			".git",
			".obsidian",
			".grove",
			"node_modules",
			"__pycache__",
			".venv",
			"venv",
		]);

		for (const entry of entries) {
			if (!entry.isDirectory()) continue;
			if (entry.name.startsWith(".") && !skipDirs.has(entry.name)) continue;
			if (skipDirs.has(entry.name)) continue;

			await this.scanDirectory(
				path.join(dir, entry.name),
				vaultRoot,
				depth + 1,
				maxDepth,
			);
		}
	}
}

// ---------------------------------------------------------------------------
// Settings integration — adds a grove selector when multiple groves exist
// ---------------------------------------------------------------------------

/**
 * Adds a grove selector dropdown to the settings tab container
 * if multiple groves are detected. Called from GroveSettingTab.display().
 */
export function addGroveSelector(
	containerEl: HTMLElement,
	plugin: GrovePlugin,
	detectedGroves: DetectedGrove[],
): void {
	if (detectedGroves.length === 0) {
		new Setting(containerEl)
			.setName("Detected groves")
			.setDesc(
				"No grove directories found in this vault. " +
				"Run 'grove init' in a directory to create one, or set the path manually below."
			);
		return;
	}

	if (detectedGroves.length === 1) {
		const grove = detectedGroves[0];
		new Setting(containerEl)
			.setName("Detected grove")
			.setDesc(`Found: ${grove.label}`);

		// Auto-set if the user has not configured a path
		if (!plugin.settings.defaultGrovePath) {
			plugin.settings.defaultGrovePath = grove.rootPath;
			plugin.saveSettings();
		}
		return;
	}

	// Multiple groves — show a dropdown
	new Setting(containerEl)
		.setName("Active grove")
		.setDesc(
			`${detectedGroves.length} grove directories found in this vault. ` +
			"Select which one to use for compile and query commands."
		)
		.addDropdown((dropdown) => {
			for (const grove of detectedGroves) {
				dropdown.addOption(grove.rootPath, grove.label);
			}

			// Set current value
			const current = plugin.settings.defaultGrovePath;
			if (current && detectedGroves.some((g) => g.rootPath === current)) {
				dropdown.setValue(current);
			} else {
				// Default to the first detected grove
				dropdown.setValue(detectedGroves[0].rootPath);
				plugin.settings.defaultGrovePath = detectedGroves[0].rootPath;
				plugin.saveSettings();
			}

			dropdown.onChange(async (value) => {
				plugin.settings.defaultGrovePath = value;
				await plugin.saveSettings();
				new Notice(`Active grove set to: ${value}`);
			});
		});
}

/**
 * Resolves the working directory for subprocess calls.
 * If the configured grove path exists, uses that.
 * Otherwise falls back to the vault root.
 */
export function resolveGroveCwd(plugin: GrovePlugin): string {
	const configured = plugin.settings.defaultGrovePath;

	// If user has configured a path (possibly outside the vault), use it as-is
	if (configured && fs.existsSync(configured)) {
		return configured;
	}

	// Fallback: vault root
	const adapter = plugin.app.vault.adapter;
	if ("getBasePath" in adapter && typeof adapter.getBasePath === "function") {
		return adapter.getBasePath() as string;
	}

	return process.cwd();
}
