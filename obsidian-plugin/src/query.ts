import {
	ItemView,
	MarkdownRenderer,
	Notice,
	WorkspaceLeaf,
	setIcon,
} from "obsidian";
import type GrovePlugin from "./main";
import {
	GroveEvent,
	QueryResult,
	notifyRunnerError,
} from "./runner";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

export const QUERY_VIEW_TYPE = "grove-query-panel";
const GROVE_RIBBON_ICON = "search"; // Lucide icon name

// ---------------------------------------------------------------------------
// Session-persistent state (survives panel close/reopen within a session)
// ---------------------------------------------------------------------------

interface QuerySessionState {
	lastQuestion: string;
	lastMode: "quick" | "deep";
	lastAnswer: string;
	lastCitations: string[];
	lastFollowUps: string[];
}

let sessionState: QuerySessionState = {
	lastQuestion: "",
	lastMode: "deep",
	lastAnswer: "",
	lastCitations: [],
	lastFollowUps: [],
};

// ---------------------------------------------------------------------------
// QueryPanel — right-panel leaf view
// ---------------------------------------------------------------------------

export class QueryPanel extends ItemView {
	plugin: GrovePlugin;
	private inputEl: HTMLTextAreaElement | null = null;
	private modeSelectEl: HTMLSelectElement | null = null;
	private submitBtn: HTMLButtonElement | null = null;
	private fileBtn: HTMLButtonElement | null = null;
	private answerContainerEl: HTMLDivElement | null = null;
	private spinnerEl: HTMLDivElement | null = null;
	private isQuerying = false;

	constructor(leaf: WorkspaceLeaf, plugin: GrovePlugin) {
		super(leaf);
		this.plugin = plugin;
	}

	getViewType(): string {
		return QUERY_VIEW_TYPE;
	}

	getDisplayText(): string {
		return "Grove Query";
	}

	getIcon(): string {
		return GROVE_RIBBON_ICON;
	}

	async onOpen(): Promise<void> {
		const container = this.contentEl;
		container.empty();
		container.addClass("grove-query-panel");

		// ── Header ────────────────────────────────────────────────────
		container.createEl("h3", { text: "Grove Query" });

		// ── Question input ────────────────────────────────────────────
		this.inputEl = container.createEl("textarea", {
			cls: "grove-query-input",
			attr: {
				placeholder: "Ask your knowledge base a question...",
				rows: "3",
			},
		});
		this.inputEl.value = sessionState.lastQuestion;

		// ── Controls row ──────────────────────────────────────────────
		const controls = container.createDiv({ cls: "grove-query-controls" });

		// Mode selector
		const modeWrapper = controls.createDiv({ cls: "grove-mode-wrapper" });
		modeWrapper.createEl("label", {
			text: "Mode:",
			attr: { for: "grove-query-mode" },
		});
		this.modeSelectEl = modeWrapper.createEl("select", {
			cls: "grove-mode-select dropdown",
			attr: { id: "grove-query-mode" },
		});
		const quickOption = this.modeSelectEl.createEl("option", {
			text: "Quick",
			attr: { value: "quick" },
		});
		const deepOption = this.modeSelectEl.createEl("option", {
			text: "Deep",
			attr: { value: "deep" },
		});

		// Set initial mode from session or settings
		const initialMode = sessionState.lastMode || this.plugin.settings.defaultQueryMode;
		if (initialMode === "quick") {
			quickOption.selected = true;
		} else {
			deepOption.selected = true;
		}

		// Submit button
		this.submitBtn = controls.createEl("button", {
			text: "Ask",
			cls: "grove-submit-btn mod-cta",
		});
		this.submitBtn.addEventListener("click", () => this.handleSubmit());

		// ── Spinner ───────────────────────────────────────────────────
		this.spinnerEl = container.createDiv({ cls: "grove-spinner" });
		this.spinnerEl.style.display = "none";
		const spinnerIcon = this.spinnerEl.createDiv({ cls: "grove-spinner-icon" });
		setIcon(spinnerIcon, "loader");
		this.spinnerEl.createSpan({ text: "Querying..." });

		// ── Answer area ───────────────────────────────────────────────
		this.answerContainerEl = container.createDiv({ cls: "grove-answer-container" });

		// ── File button (hidden until an answer exists) ───────────────
		this.fileBtn = container.createEl("button", {
			text: "File this answer",
			cls: "grove-file-btn",
		});
		this.fileBtn.style.display = "none";
		this.fileBtn.addEventListener("click", () => this.handleFile());

		// Restore previous answer if one exists in session state
		if (sessionState.lastAnswer) {
			await this.renderAnswer(sessionState.lastAnswer);
			this.fileBtn.style.display = "block";
		}

		// Allow Ctrl/Cmd+Enter to submit
		this.inputEl.addEventListener("keydown", (e: KeyboardEvent) => {
			if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
				e.preventDefault();
				this.handleSubmit();
			}
		});
	}

	async onClose(): Promise<void> {
		// State is kept in the module-level sessionState — nothing to clean up
	}

	// ── Submit handler ────────────────────────────────────────────────

	private async handleSubmit(): Promise<void> {
		if (this.isQuerying) return;
		if (!this.inputEl || !this.modeSelectEl) return;

		const question = this.inputEl.value.trim();
		if (!question) {
			new Notice("Please enter a question.", 3000);
			return;
		}

		const runner = this.plugin.getRunner();
		if (!runner) {
			new Notice("Grove runner is not initialised. Check Grove settings.", 5000);
			return;
		}

		if (runner.isRunning()) {
			new Notice("A Grove process is already running. Please wait.", 5000);
			return;
		}

		const mode = this.modeSelectEl.value as "quick" | "deep";

		// Save to session
		sessionState.lastQuestion = question;
		sessionState.lastMode = mode;

		// UI: show spinner, disable controls
		this.setQuerying(true);
		this.clearAnswer();

		const modeFlag = mode === "quick" ? "--quick" : "--deep";
		const args = ["query", modeFlag, question];

		let queryResult: QueryResult | null = null;

		try {
			await runner.run(args, (event: GroveEvent) => {
				if (event.type === "result" && isQueryResult(event.data)) {
					queryResult = event.data;
				} else if (event.type === "error") {
					new Notice(`Grove error: ${event.message}`, 8000);
				} else if (event.type === "warning") {
					new Notice(`Grove warning: ${event.message}`, 6000);
				}
			});

			if (queryResult) {
				sessionState.lastAnswer = queryResult.answer;
				sessionState.lastCitations = queryResult.citations ?? [];
				sessionState.lastFollowUps = queryResult.follow_ups ?? [];
				await this.renderAnswer(queryResult.answer);
				if (this.fileBtn) {
					this.fileBtn.style.display = "block";
				}
			} else {
				new Notice("Grove: No answer received.", 5000);
			}
		} catch (err) {
			notifyRunnerError(err);
		} finally {
			this.setQuerying(false);
		}
	}

	// ── File handler ──────────────────────────────────────────────────

	private async handleFile(): Promise<void> {
		if (!sessionState.lastAnswer) {
			new Notice("No answer to file.", 3000);
			return;
		}

		const runner = this.plugin.getRunner();
		if (!runner) {
			new Notice("Grove runner is not initialised. Check Grove settings.", 5000);
			return;
		}

		if (runner.isRunning()) {
			new Notice("A Grove process is already running. Please wait.", 5000);
			return;
		}

		try {
			let filed = false;
			await runner.run(["file"], (event: GroveEvent) => {
				if (event.type === "result") {
					filed = true;
				} else if (event.type === "error") {
					new Notice(`Grove error: ${event.message}`, 8000);
				}
			});

			if (filed) {
				new Notice("Answer filed to wiki successfully.", 5000);
			}
		} catch (err) {
			notifyRunnerError(err);
		}
	}

	// ── Rendering helpers ─────────────────────────────────────────────

	private async renderAnswer(markdown: string): Promise<void> {
		if (!this.answerContainerEl) return;
		this.answerContainerEl.empty();
		await MarkdownRenderer.renderMarkdown(
			markdown,
			this.answerContainerEl,
			"",
			this,
		);
	}

	private clearAnswer(): void {
		if (this.answerContainerEl) {
			this.answerContainerEl.empty();
		}
		if (this.fileBtn) {
			this.fileBtn.style.display = "none";
		}
	}

	private setQuerying(active: boolean): void {
		this.isQuerying = active;
		if (this.spinnerEl) {
			this.spinnerEl.style.display = active ? "flex" : "none";
		}
		if (this.submitBtn) {
			this.submitBtn.disabled = active;
			this.submitBtn.textContent = active ? "Querying..." : "Ask";
		}
		if (this.inputEl) {
			this.inputEl.disabled = active;
		}
		if (this.modeSelectEl) {
			this.modeSelectEl.disabled = active;
		}
	}
}

// ---------------------------------------------------------------------------
// Registration helpers
// ---------------------------------------------------------------------------

/** Register the query panel view type and ribbon icon with the plugin. */
export function registerQueryPanel(plugin: GrovePlugin): void {
	plugin.registerView(
		QUERY_VIEW_TYPE,
		(leaf) => new QueryPanel(leaf, plugin),
	);

	plugin.addRibbonIcon(GROVE_RIBBON_ICON, "Open Grove Query", () => {
		activateQueryPanel(plugin);
	});

	plugin.addCommand({
		id: "grove-open-query",
		name: "Open query panel",
		callback: () => {
			activateQueryPanel(plugin);
		},
	});
}

/** Reveal the query panel in the right sidebar, creating a leaf if needed. */
async function activateQueryPanel(plugin: GrovePlugin): Promise<void> {
	const { workspace } = plugin.app;

	let leaf = workspace.getLeavesOfType(QUERY_VIEW_TYPE)[0];
	if (!leaf) {
		const rightLeaf = workspace.getRightLeaf(false);
		if (rightLeaf) {
			leaf = rightLeaf;
			await leaf.setViewState({
				type: QUERY_VIEW_TYPE,
				active: true,
			});
		}
	}
	if (leaf) {
		workspace.revealLeaf(leaf);
	}
}

// ---------------------------------------------------------------------------
// Type guard
// ---------------------------------------------------------------------------

function isQueryResult(data: unknown): data is QueryResult {
	return (
		typeof data === "object" &&
		data !== null &&
		"answer" in data
	);
}
