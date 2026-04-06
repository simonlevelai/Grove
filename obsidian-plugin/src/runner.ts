import { ChildProcess, spawn } from "child_process";
import { Notice } from "obsidian";

// ---------------------------------------------------------------------------
// NDJSON event types — matches ARCH.md contract
// ---------------------------------------------------------------------------

export interface ProgressEvent {
	type: "progress";
	step: string;
	pct: number;
	detail?: string;
}

export interface CompileResult {
	articles_created: number;
	articles_updated: number;
	cost_usd: number;
}

export interface QueryResult {
	answer: string;
	citations: string[];
	follow_ups?: string[];
}

export interface HealthResult {
	passed: boolean;
	blocking_failures: string[];
	warnings: string[];
}

export interface DryRunResult {
	estimated_tokens: number;
	estimated_cost_usd: number;
	source_count: number;
}

export interface ResultEvent {
	type: "result";
	data: CompileResult | QueryResult | HealthResult | DryRunResult;
}

export interface ErrorEvent {
	type: "error";
	message: string;
	code: string;
	recoverable: boolean;
}

export interface WarningEvent {
	type: "warning";
	message: string;
}

export type GroveEvent = ProgressEvent | ResultEvent | ErrorEvent | WarningEvent;

// ---------------------------------------------------------------------------
// Listener types
// ---------------------------------------------------------------------------

export type GroveEventListener = (event: GroveEvent) => void;

// ---------------------------------------------------------------------------
// GroveRunner — spawns the grove CLI and streams NDJSON events
// ---------------------------------------------------------------------------

export class GroveRunner {
	private cliPath: string;
	private cwd: string;
	private apiKey: string;
	private activeProcess: ChildProcess | null = null;

	constructor(cliPath: string, cwd: string, apiKey: string) {
		this.cliPath = cliPath;
		this.cwd = cwd;
		this.apiKey = apiKey;
	}

	/** Update runner configuration without creating a new instance. */
	setCwd(cwd: string): void {
		this.cwd = cwd;
	}

	setCliPath(cliPath: string): void {
		this.cliPath = cliPath;
	}

	setApiKey(apiKey: string): void {
		this.apiKey = apiKey;
	}

	/** Returns true if a subprocess is currently running. */
	isRunning(): boolean {
		return this.activeProcess !== null;
	}

	/**
	 * Run a grove CLI command with `--json` flag and stream NDJSON events.
	 *
	 * @param args  CLI arguments, e.g. ["compile"] or ["query", "--deep", "What is..."]
	 * @param onEvent  Callback invoked for each parsed NDJSON event
	 * @returns A promise that resolves when the process exits normally,
	 *          or rejects on a non-zero exit code / crash.
	 */
	run(args: string[], onEvent: GroveEventListener): Promise<void> {
		return new Promise<void>((resolve, reject) => {
			if (this.activeProcess) {
				reject(new Error("A grove process is already running. Please wait for it to finish."));
				return;
			}

			const fullArgs = [...args, "--json"];

			const env: Record<string, string> = { ...process.env } as Record<string, string>;
			if (this.apiKey) {
				env["ANTHROPIC_API_KEY"] = this.apiKey;
			}

			let child: ChildProcess;
			try {
				child = spawn(this.cliPath, fullArgs, {
					cwd: this.cwd,
					env,
					stdio: ["ignore", "pipe", "pipe"],
				});
			} catch (err) {
				reject(new Error(`Failed to start grove CLI at "${this.cliPath}": ${String(err)}`));
				return;
			}

			this.activeProcess = child;

			let stdoutBuffer = "";
			let stderrChunks = "";

			child.stdout?.on("data", (chunk: Buffer) => {
				stdoutBuffer += chunk.toString("utf-8");
				const lines = stdoutBuffer.split("\n");
				// Keep the last (possibly incomplete) line in the buffer
				stdoutBuffer = lines.pop() ?? "";

				for (const line of lines) {
					const trimmed = line.trim();
					if (!trimmed) continue;
					try {
						const event = JSON.parse(trimmed) as GroveEvent;
						onEvent(event);
					} catch {
						// Non-JSON output — ignore (CLI may emit human-readable text
						// before switching to JSON mode)
					}
				}
			});

			child.stderr?.on("data", (chunk: Buffer) => {
				stderrChunks += chunk.toString("utf-8");
			});

			child.on("error", (err: Error) => {
				this.activeProcess = null;
				reject(new Error(`Grove CLI process error: ${err.message}`));
			});

			child.on("close", (code: number | null, signal: string | null) => {
				this.activeProcess = null;

				// Flush any remaining stdout buffer
				if (stdoutBuffer.trim()) {
					try {
						const event = JSON.parse(stdoutBuffer.trim()) as GroveEvent;
						onEvent(event);
					} catch {
						// Not valid JSON — discard
					}
				}

				if (signal) {
					reject(new Error(`Grove CLI was killed by signal ${signal}`));
				} else if (code !== null && code !== 0) {
					const stderrMsg = stderrChunks.trim();
					reject(
						new Error(
							`Grove CLI exited with code ${code}${stderrMsg ? `: ${stderrMsg}` : ""}`
						)
					);
				} else {
					resolve();
				}
			});
		});
	}

	/** Kill the active subprocess, if any. */
	kill(): void {
		if (this.activeProcess) {
			this.activeProcess.kill("SIGTERM");
			this.activeProcess = null;
		}
	}
}

// ---------------------------------------------------------------------------
// Auto-detect the grove CLI path
// ---------------------------------------------------------------------------

/**
 * Attempts to locate the `grove` CLI binary by checking common install paths
 * and falling back to `which grove`.
 */
export async function detectGroveCli(): Promise<string | null> {
	const { execFile } = await import("child_process");
	const { promisify } = await import("util");
	const execFileAsync = promisify(execFile);

	// Common paths to check first
	const candidates = [
		"/usr/local/bin/grove",
		"/opt/homebrew/bin/grove",
		`${process.env["HOME"]}/.local/bin/grove`,
	];

	const { existsSync } = await import("fs");
	for (const candidate of candidates) {
		if (existsSync(candidate)) {
			return candidate;
		}
	}

	// Fallback: ask the shell
	try {
		const { stdout } = await execFileAsync("which", ["grove"]);
		const path = stdout.trim();
		if (path) return path;
	} catch {
		// `which` failed — grove is not on PATH
	}

	return null;
}

/**
 * Shows a non-blocking Obsidian notice for a runner error.
 * Extracts a user-friendly message from common failure modes.
 */
export function notifyRunnerError(err: unknown): void {
	const msg = err instanceof Error ? err.message : String(err);
	if (msg.includes("ENOENT") || msg.includes("Failed to start")) {
		new Notice("Grove CLI not found. Check the CLI path in Grove settings.", 8000);
	} else if (msg.includes("already running")) {
		new Notice("A Grove process is already running. Please wait.", 5000);
	} else {
		new Notice(`Grove error: ${msg}`, 8000);
	}
}
