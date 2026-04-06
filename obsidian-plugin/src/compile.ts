import { Modal, App, Notice } from "obsidian";
import type GrovePlugin from "./main";
import {
	GroveEvent,
	CompileResult,
	DryRunResult,
	notifyRunnerError,
} from "./runner";

// ---------------------------------------------------------------------------
// Dry-run estimate modal — shown instead of compiling when dry-run is on
// ---------------------------------------------------------------------------

class DryRunModal extends Modal {
	private result: DryRunResult;

	constructor(app: App, result: DryRunResult) {
		super(app);
		this.result = result;
	}

	onOpen(): void {
		const { contentEl } = this;
		contentEl.createEl("h2", { text: "Grove — Dry-run Estimate" });

		const table = contentEl.createEl("table", { cls: "grove-dry-run-table" });

		const rows: Array<[string, string]> = [
			["Sources", String(this.result.source_count)],
			["Estimated tokens", this.result.estimated_tokens.toLocaleString()],
			[
				"Estimated cost",
				`$${this.result.estimated_cost_usd.toFixed(2)} USD`,
			],
		];

		for (const [label, value] of rows) {
			const tr = table.createEl("tr");
			tr.createEl("td", { text: label, cls: "grove-label" });
			tr.createEl("td", { text: value, cls: "grove-value" });
		}

		contentEl.createEl("p", {
			text: "Disable dry-run in Grove settings to perform a real compilation.",
			cls: "grove-dry-run-hint",
		});
	}

	onClose(): void {
		this.contentEl.empty();
	}
}

// ---------------------------------------------------------------------------
// Error remedy map — provides user-facing guidance for known error codes
// ---------------------------------------------------------------------------

const ERROR_REMEDIES: Record<string, string> = {
	rate_limit: "The Anthropic API rate limit was reached. Wait a few minutes and try again.",
	no_sources: "No sources found. Run 'grove ingest' first to add source material.",
	ratchet_failed: "Quality checks failed. Run 'grove health' to see which checks need attention.",
	budget_exceeded: "Daily budget limit exceeded. Adjust the limit in .grove/config.yaml.",
	no_api_key: "No API key configured. Add your Anthropic API key in Grove settings.",
	config_error: "Configuration error. Check .grove/config.yaml is valid.",
};

function getRemedy(code: string): string {
	return ERROR_REMEDIES[code] ?? "Check the grove CLI output for details.";
}

// ---------------------------------------------------------------------------
// registerCompileCommand — wires up the "Grove: Compile" palette command
// ---------------------------------------------------------------------------

export function registerCompileCommand(plugin: GrovePlugin): void {
	plugin.addCommand({
		id: "grove-compile",
		name: "Compile",
		callback: () => {
			runCompile(plugin);
		},
	});
}

async function runCompile(plugin: GrovePlugin): Promise<void> {
	const runner = plugin.getRunner();
	if (!runner) {
		new Notice("Grove runner is not initialised. Check Grove settings.", 5000);
		return;
	}

	if (runner.isRunning()) {
		new Notice("A Grove process is already running. Please wait.", 5000);
		return;
	}

	const isDryRun = plugin.settings.dryRunByDefault;
	const args = isDryRun ? ["compile", "--dry-run"] : ["compile"];

	// Show a progress notice that updates in-place
	const progressNotice = new Notice("Grove: Starting compilation...", 0);
	let lastResult: GroveEvent | null = null;

	const onEvent = (event: GroveEvent): void => {
		switch (event.type) {
			case "progress": {
				const detail = event.detail ?? event.step;
				const pctText = event.pct > 0 ? ` (${event.pct}%)` : "";
				setNoticeMessage(progressNotice, `Grove: ${detail}${pctText}`);
				break;
			}
			case "result":
				lastResult = event;
				break;
			case "warning":
				new Notice(`Grove warning: ${event.message}`, 6000);
				break;
			case "error":
				// Errors are handled after the process exits
				lastResult = event;
				break;
		}
	};

	try {
		await runner.run(args, onEvent);

		// Process completed — dismiss the progress notice
		progressNotice.hide();

		if (lastResult?.type === "result") {
			const data = lastResult.data;
			if (isDryRun && isDryRunResult(data)) {
				new DryRunModal(plugin.app, data).open();
			} else if (isCompileResult(data)) {
				new Notice(
					`Grove: Compilation complete — ` +
					`${data.articles_created} created, ${data.articles_updated} updated` +
					` — cost: $${data.cost_usd.toFixed(2)}`,
					8000
				);
			} else {
				new Notice("Grove: Compilation complete.", 5000);
			}
		} else if (lastResult?.type === "error") {
			const errEvent = lastResult;
			new Notice(
				`Grove error [${errEvent.code}]: ${errEvent.message}\n${getRemedy(errEvent.code)}`,
				10000
			);
		} else {
			new Notice("Grove: Compilation finished (no result data received).", 5000);
		}
	} catch (err) {
		progressNotice.hide();
		notifyRunnerError(err);
	}
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Update the text content of an existing Notice (Obsidian does not expose a public setter). */
function setNoticeMessage(notice: Notice, message: string): void {
	// Obsidian's Notice exposes `noticeEl` as the root DOM element
	const el = (notice as unknown as { noticeEl: HTMLElement }).noticeEl;
	if (el) {
		el.textContent = message;
	}
}

function isCompileResult(data: unknown): data is CompileResult {
	return (
		typeof data === "object" &&
		data !== null &&
		"articles_created" in data &&
		"articles_updated" in data
	);
}

function isDryRunResult(data: unknown): data is DryRunResult {
	return (
		typeof data === "object" &&
		data !== null &&
		"estimated_tokens" in data &&
		"estimated_cost_usd" in data
	);
}
