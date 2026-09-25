import { spawn } from "node:child_process";
import { readdir, stat } from "node:fs/promises";
import * as path from "node:path";
import type { HookAPI, HookContext } from "@oh-my-pi/pi-coding-agent/extensibility/hooks";
import { LAYA_STATUS_KEY, SIDECAR_URL, barsFromAnswer, formatStatus, record } from "../lib/record";
import type { LoggedAnswer } from "../lib/record";


const JUDGE_TIMEOUT_MS = 1_500;
const HEALTH_TIMEOUT_MS = 500;
const RE_SPAWN_INTERVAL_MS = 60_000;
const AUTO_ACCEPT = 0.6;
// A model nudge is only worth the extra turn it causes when laya is fairly
// sure. Measured over a two-run A/B, notes in the 0.6-0.8 band cost the model
// turns and changed no outcome; the hard block at 0.85 is kept either way.
const REVIEW_NOTE = 0.8;
const DANGER_BLOCK = 0.85;
const SERVER_DIR = path.resolve(import.meta.dir, "../../server");

type RawAnswer = { choice?: string; bool?: number; noul?: number; score?: number; confidence?: number };
type Questions = Record<string, { type: "bool" | "choice"; instructions: string; criteria?: Record<string, string> }>;
type JudgePayload = { answers: Record<string, RawAnswer>; model?: string; routing?: { model?: string }; latency_ms?: number };
type DecisionCore = string | ((payload: JudgePayload) => string);
type StopEvent = { last_assistant_message?: unknown; stop_hook_active: boolean };

let lastEnsureAt = 0;
let launchInFlight = false;
const blockedOnce = new Set<string>();
let completionChecked = false;

function excerpt(value: string, max: number): string {
	const compact = value.replace(/\s+/g, " ").trim();
	return compact.length <= max ? compact : `${compact.slice(0, max - 1)}…`;
}

function answerConfidence(answer: RawAnswer | undefined): number {
	if (!answer) return 0;
	if (typeof answer.bool === "number") return answer.bool;
	return typeof answer.confidence === "number" ? answer.confidence : 0;
}

async function fetchTimed(url: string, init: RequestInit, timeoutMs: number): Promise<Response> {
	const controller = new AbortController();
	const timer = setTimeout(() => controller.abort(), timeoutMs);
	try {
		return await fetch(url, { ...init, signal: controller.signal });
	} finally {
		clearTimeout(timer);
	}
}

async function ask(state: string, questions: Questions): Promise<JudgePayload | undefined> {
	try {
		const response = await fetchTimed(`${SIDECAR_URL}/judge`, {
			method: "POST",
			headers: { "content-type": "application/json" },
			body: JSON.stringify({ state, questions }),
		}, JUDGE_TIMEOUT_MS);
		if (!response.ok) return undefined;
		const payload = (await response.json()) as JudgePayload;
		if (!payload || typeof payload !== "object" || !payload.answers || typeof payload.answers !== "object") return undefined;
		// The sidecar serves laya's raw heads; the bool head arrives as `noul`,
		// which is P(true) (core.py unpack_judge_answer owns that contract).
		// Every gate below reads `.bool`, so normalize once here - without this
		// every bool gate silently reads undefined and fails open.
		for (const [qid, answer] of Object.entries(payload.answers)) {
			if (typeof answer.noul === "number" && typeof answer.bool !== "number") {
				payload.answers[qid] = { ...answer, bool: answer.noul };
			}
		}
		return payload;
	} catch {
		return undefined;
	}
}

function showDecision(ctx: HookContext, entry: { core: string; ms: number; model: string; conf: number }): void {
	try {
		ctx.ui.setStatus(LAYA_STATUS_KEY, formatStatus(entry));
	} catch {
		// Display is best-effort.
	}
}

async function decide(ctx: HookContext, core: DecisionCore, state: string, questions: Questions, confidence: (payload: JudgePayload) => number): Promise<JudgePayload | undefined> {
	const payload = await ask(state, questions);
	if (!payload) return undefined;
	const label = typeof core === "function" ? core(payload) : core;
	const bars = Object.entries(payload.answers ?? {}).flatMap(([qid, answer]) => barsFromAnswer(qid, answer as LoggedAnswer)).slice(0, 6);
	const entry = { core: label, ms: Number(payload.latency_ms ?? 0), model: String(payload.routing?.model ?? payload.model ?? "laya").replace(/^laya\//, ""), conf: confidence(payload), bars };
	showDecision(ctx, entry);
	return payload;
}

function launchPythonFallback(): void {
	if (launchInFlight) return;
	launchInFlight = true;
	try {
		const child = spawn("python3", ["sidecar.py"], { cwd: SERVER_DIR, detached: true, stdio: "ignore", windowsHide: true });
		child.once("spawn", () => { launchInFlight = false; child.unref(); });
		child.once("error", () => { launchInFlight = false; });
	} catch {
		launchInFlight = false;
	}
}

function launchSidecar(): void {
	if (launchInFlight) return;
	launchInFlight = true;
	try {
		const child = spawn("py", ["-3", "sidecar.py"], { cwd: SERVER_DIR, detached: true, stdio: "ignore", windowsHide: true });
		child.once("spawn", () => { launchInFlight = false; child.unref(); });
		child.once("error", () => { launchInFlight = false; launchPythonFallback(); });
	} catch {
		launchInFlight = false;
		launchPythonFallback();
	}
}

async function ensureSidecar(): Promise<void> {
	try {
		const response = await fetchTimed(`${SIDECAR_URL}/info`, { method: "GET" }, HEALTH_TIMEOUT_MS);
		if (response.ok) return;
	} catch {
		// An unreachable sidecar is the spawn path.
	}
	launchSidecar();
}

function scheduleSidecarCheck(): void {
	const now = Date.now();
	if (now - lastEnsureAt < RE_SPAWN_INTERVAL_MS) return;
	lastEnsureAt = now;
	void ensureSidecar();
}

// Full-suite runners belong here too: the focused-checks gate only runs on
// commands this prefilter calls benign, so omitting cargo/go/make would make
// their branches in FULL_SUITE unreachable.
const BENIGN_SEGMENT = /^(?:git\s+(?:status|diff|log|show|branch(?:\s+--?[\w-]+)?|rev-parse)(?:\s+-{1,2}[\w-]+(?:=\S+)?|\s+\S+)*|ls|pwd|cat|grep|rg|head|tail|wc|find|du|df|file|stat|(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?test|python3?\s+-m\s+pytest|py\s+-3\s+-m\s+pytest|pytest|jest|vitest|cargo\s+test|go\s+test(?:\s+\S+)*|make\s+test)$/i;
const SUSPICIOUS = /(?:\brm\s+|\brmdir\b|\bunlink\b|Remove-Item\b|\bdel\s+\/s|\b(?:chmod|chown|setfacl|takeown|icacls)\b|\b(?:reg|regedit)\s+(?:add|delete|import)|\bSet-ItemProperty\b.*Registry|\bhosts\b.*(?:>|Set-Content|Add-Content)|\b(?:taskkill|Stop-Process|pkill|killall)\b|\bkill\s+-[^\n]*\d|\bgit\s+push\b[^\n]*(?:--force|-f)\b|\b(?:Invoke-Expression|iex|eval)\b|\b(?:bash|sh)\s+<\(\s*(?:curl|wget)|\b(?:curl|wget)\b[^\n|]*\|\s*(?:bash|sh|python))/i;

function bashRiskKind(command: string): "suspicious" | "benign" | "skip" {
	const segments = command.split(/[;&|]\s*/).map(part => part.trim()).filter(Boolean);
	if (segments.length === 0) return "skip";
	if (SUSPICIOUS.test(command)) return "suspicious";
	return segments.every(segment => BENIGN_SEGMENT.test(segment)) ? "benign" : "skip";
}

const MECHANICAL_TASK = /\b(?:rename|reformat|format(?:ting)?|whitespace|sort(?:ed|ing)?|list|bump\s+(?:the\s+)?version|version\s+bump|lockfile|typo|simple\s+move|move\s+(?:the\s+)?file)\b/i;
function taskText(input: Record<string, unknown>): string {
	for (const key of ["prompt", "description", "task"]) if (typeof input[key] === "string" && input[key]) return input[key] as string;
	return "";
}

function isJudgeTool(toolName: string, input: Record<string, unknown>): boolean {
	const normalize = (value: string) => value.replace(/^xd:\/\//, "").replace(/[-_]+/g, "_");
	const judge = normalize("mcp__laya-judge__judge");
	const batch = normalize("mcp__laya-judge__judge_batch");
	if (normalize(toolName) === judge || normalize(toolName) === batch) return true;
	return toolName === "write" && typeof input.path === "string" && (normalize(input.path) === judge || normalize(input.path) === batch);
}

function textFromContent(content: unknown): string {
	if (typeof content === "string") return content;
	if (!Array.isArray(content)) return "";
	return content.filter(part => !!part && typeof part === "object").map(part => {
		const value = part as { type?: string; text?: string };
		return value.type === "text" && typeof value.text === "string" ? value.text : value.type === "image" ? "[image]" : "";
	}).join("\n");
}

function textFromMessage(message: unknown): string {
	if (!message || typeof message !== "object") return "";
	const value = message as { customType?: unknown; content?: unknown };
	if (typeof value.customType === "string" && value.customType.toLowerCase().includes("laya")) return "";
	return textFromContent(value.content).split("\n").filter(line => !/laya\s*▸|laya completion check/i.test(line)).join("\n");
}

const FULL_SUITE = /^(?:(?:py(?:\s+-3)?|python3?)\s+-m\s+)?pytest(?:\s+(?:-[^\s]+|--\S+))*$|^(?:npm|pnpm|yarn)(?:\s+run)?\s+test(?:\s+(?:-[^\s]+|--\S+))*$|^cargo\s+test(?:\s+(?:-[^\s]+|--\S+))*$|^go\s+test\s+\.\/\.\.\.(?:\s+(?:-[^\s]+|--\S+))*$|^make\s+test(?:\s+(?:-[^\s]+|--\S+))*$|^(?:jest|vitest)(?:\s+(?:-[^\s]+|--\S+))*$|^bun\s+test(?:\s+(?:-[^\s]+|--\S+))*$/i;
function testCandidate(pathname: string): boolean {
	const normalized = pathname.replace(/\\/g, "/");
	return /(?:^|\/)tests?\//i.test(normalized) || /(?:^|\/)test_[^/]+\.py$/i.test(normalized) || /_test\.py$/i.test(normalized) || /\.(?:test|spec)\.[^/]+$/i.test(normalized);
}
const GIT_TIMEOUT_MS = 1_500;
async function changedTestFiles(pi: HookAPI): Promise<string[]> {
	try {
		// status --porcelain covers modified AND untracked files: a freshly
		// written test is usually untracked, so `git diff HEAD` would miss the
		// exact files a focused run should target.
		const result = await pi.exec("git", ["status", "--porcelain", "--untracked-files=all"], { timeout: GIT_TIMEOUT_MS });
		if (result.code !== 0) return [];
		return result.stdout
			.split(/\r?\n/)
			.map(line => line.slice(3).trim())
			.map(line => (line.includes("->") ? line.slice(line.indexOf("->") + 2) : line).trim())
			.filter(name => name.length > 0 && testCandidate(name))
			.slice(0, 6);
	} catch {
		return [];
	}
}
/** File-selector command for runners that accept a path. Undefined when
 * appending a file would be invalid - cargo/go/make take crate or target
 * names, so the block reason names the file instead of inventing a command. */
function focusedCommand(command: string, candidate: string): string | undefined {
	const trimmed = command.trim();
	if (/^pytest\b|^py\b.*-m\s+pytest\b|^python3?\b.*-m\s+pytest\b/i.test(trimmed)) return `pytest ${candidate}`;
	const packageTest = trimmed.match(/^(npm|pnpm|yarn)(?:\s+run)?\s+test/i);
	if (packageTest) return `${packageTest[1]} test -- ${candidate}`;
	const runner = trimmed.match(/^(bun\s+test|jest|vitest)/i);
	return runner ? `${runner[1]} ${candidate}` : undefined;
}

async function noteCandidates(cwd: string): Promise<string[]> {
	const entries: string[] = [];
	for (const directory of [cwd, path.join(cwd, "docs")]) {
		try {
			const files = await readdir(directory, { withFileTypes: true });
			for (const entry of files.sort((a, b) => a.name.localeCompare(b.name))) {
				if (!entry.isFile() || !entry.name.toLowerCase().endsWith(".md")) continue;
				const full = path.join(directory, entry.name);
				if ((await stat(full)).size <= 64 * 1024) entries.push(directory === cwd ? entry.name : `docs/${entry.name}`);
			}
		} catch {
			// Missing note directories are not an error.
		}
	}
	return [...new Set(entries)].slice(0, 10);
}

export default function hook(pi: HookAPI): void {
	pi.on("session_start", () => scheduleSidecarCheck());
	pi.on("before_agent_start", async (event, ctx) => {
		scheduleSidecarCheck();
		try {
			const candidates = await noteCandidates(ctx.cwd);
			if (candidates.length < 2) return;
			const questions: Questions = {};
			for (let index = 0; index < candidates.length; index++) questions[`note_${index}`] = { type: "bool", instructions: "this project note is relevant to the request above" };
			const payload = await decide(ctx, p => {
				const hits = candidates.filter((_, index) => (p.answers[`note_${index}`]?.bool ?? 0) >= AUTO_ACCEPT);
				return `notes ${hits.length}/${candidates.length}${hits.length > 0 ? ` ${hits.join(" ")}` : ""}`;
			}, `${excerpt(event.prompt, 600)}\nnotes: ${candidates.join(", ")}`, questions, p => Math.max(...Object.values(p.answers).map(answerConfidence), 0));
			if (!payload) return;
			const relevant = candidates.flatMap((candidate, index) => {
				const answer = payload.answers[`note_${index}`];
				return typeof answer?.bool === "number" && answer.bool >= AUTO_ACCEPT ? [`${candidate} (${answer.bool.toFixed(2)})`] : [];
			});
			if (relevant.length === 0) return;
			return { message: { customType: "laya-decide", content: `Relevant project notes (laya, local): ${relevant.join("; ")} — read before acting if applicable.`, display: true, attribution: "agent" as const } };
		} catch {
			return;
		}
	});
	pi.on("turn_start", () => { blockedOnce.clear(); completionChecked = false; });
	pi.on("tool_call", async (event, ctx) => {
		try {
			if (event.toolName === "bash") {
				const command = typeof event.input.command === "string" ? event.input.command : "";
				const risk = bashRiskKind(command);
				if (risk === "suspicious") {
					const payload = await decide(ctx, p => { const probability = p.answers.is_this_dangerous?.bool ?? 0; return `risk:${probability >= DANGER_BLOCK ? "deny" : probability >= REVIEW_NOTE ? "escalate" : "allow"} ${probability.toFixed(2)}`; }, command, { is_this_dangerous: { type: "bool", instructions: "this command is destructive, irreversible, or security-sensitive" } }, p => answerConfidence(p.answers.is_this_dangerous));
					const probability = payload?.answers.is_this_dangerous?.bool;
					if (typeof probability !== "number") return;
					// Typed escalation, the way the layer is meant to behave: deny
					// stops the action, escalate hands the judgement back to the
					// model, allow stays silent and lets the normal permission
					// flow decide. The gate can only ever tighten, never loosen.
					if (probability >= DANGER_BLOCK) return { block: true, reason: `laya deny (${probability.toFixed(2)}): ${JSON.stringify(command)} is destructive or security-sensitive. Propose a safer alternative before executing.` };
					if (probability >= REVIEW_NOTE) return { additionalContext: `laya escalate (${probability.toFixed(2)}): laya is not confident enough to stop ${JSON.stringify(command)}, but flags it as possibly destructive — re-check the intent before continuing.` };
				} else if (risk === "benign" && blockedOnce.size === 0 && FULL_SUITE.test(command.trim())) {
					const candidates = await changedTestFiles(pi);
					if (candidates.length === 0) return;
					const criteria: Record<string, string> = { run_full: "no subset is worth running first" };
					for (const candidate of candidates) criteria[candidate] = "a focused changed-file subset is worth running first";
					const payload = await decide(ctx, p => `focus:${p.answers.focused_checks?.choice ?? "none"} ${answerConfidence(p.answers.focused_checks).toFixed(2)}`, `command: ${command}\nchanged test files: ${candidates.join(", ")}`, { focused_checks: { type: "choice", instructions: "which check should run first", criteria } }, p => answerConfidence(p.answers.focused_checks));
					const answer = payload?.answers.focused_checks;
					if (!answer || typeof answer.choice !== "string" || answer.choice === "run_full" || answerConfidence(answer) < AUTO_ACCEPT) return;
					blockedOnce.add(command);
					const focused = focusedCommand(command, answer.choice);
					return { block: true, reason: focused ? `laya suggests focused checks first: ${focused} (${answerConfidence(answer).toFixed(2)}). Run that subset, then the full suite.` : `laya suggests focusing on ${answer.choice} first (${answerConfidence(answer).toFixed(2)}). Run that file's test target, then the full suite.` };
				}
			}
			if (event.toolName === "task" && (event.input.agent === undefined || event.input.agent === "")) {
				const task = excerpt(taskText(event.input), 800);
				if (!task || !MECHANICAL_TASK.test(task)) return;
				const payload = await decide(ctx, p => `route:${p.answers.agent?.choice ?? "none"} ${answerConfidence(p.answers.agent).toFixed(2)}`, task, { agent: { type: "choice", instructions: "choose the worker", criteria: { sonic: "mechanical, exact instructions, no judgment", task: "requires reasoning, planning, or tradeoffs" } } }, p => answerConfidence(p.answers.agent));
				const answer = payload?.answers.agent;
				if (answer?.choice !== "sonic" || answerConfidence(answer) < AUTO_ACCEPT) return;
				return { input: { ...event.input, agent: "sonic" } };
			}
		// Universal observer: every file action goes past laya before it runs.
		// The verdict always lands on the card (display-only, free). A note is
		// pushed into the model's context only when laya is confident the change
		// is risky: the mid-band nudge cost the model extra turns and, measured
		// over a two-run A/B, changed no outcome.
		if ((event.toolName === "edit" || event.toolName === "write") && !isJudgeTool(event.toolName, event.input)) {
			const detail = excerpt(JSON.stringify(event.input), 400);
			if (!detail) return;
			const payload = await decide(ctx, p => { const probability = p.answers.risky_change?.bool ?? 0; return `act:${event.toolName} ${probability >= DANGER_BLOCK ? "deny" : probability >= REVIEW_NOTE ? "review" : "ok"} ${probability.toFixed(2)}`; }, `tool: ${event.toolName}\ninput: ${detail}`, { risky_change: { type: "bool", instructions: "this file change is destructive, irreversible, or exposes secrets" } }, p => answerConfidence(p.answers.risky_change));
			const probability = payload?.answers.risky_change?.bool;
			if (typeof probability !== "number") return;
			if (probability >= DANGER_BLOCK) return { block: true, reason: `laya deny (${probability.toFixed(2)}): this ${event.toolName} looks destructive or exposes secrets. Propose a safer alternative before executing.` };
			if (probability >= REVIEW_NOTE) return { additionalContext: `laya escalate (${probability.toFixed(2)}): this ${event.toolName} may need a second look — re-check the target before continuing.` };
		}
		} catch {
			return;
		}
	});
	pi.on("tool_result", async (event, ctx) => {
		try {
			if (event.isError !== true || isJudgeTool(event.toolName, event.input)) return;
			const error = textFromContent(event.content);
			if (/(?:user.?abort|aborted by user|cancell?ed by user|interrupted by user)/i.test(error)) return;
			const state = [`tool: ${event.toolName}`, `input: ${excerpt(JSON.stringify(event.input), 300)}`, `error: ${excerpt(error || (event.content.length > 0 && event.content.every(part => part.type === "image") ? "image-only error output" : "no text error output"), 600)}`].join("\n");
			const payload = await decide(ctx, p => `recovery:${p.answers.recovery?.choice ?? "none"} ${Math.max(answerConfidence(p.answers.recovery), answerConfidence(p.answers.needs_more_effort)).toFixed(2)}`, state, { recovery: { type: "choice", instructions: "choose the next action", criteria: { fix_and_retry: "correct the failure and retry", read_source_or_docs: "inspect source or documentation first", narrow_scope: "reduce the failing scope first", ask_user: "request a missing decision or credential" } }, needs_more_effort: { type: "bool", instructions: "this failure needs deeper analysis, not just a corrected retry" } }, p => Math.max(answerConfidence(p.answers.recovery), answerConfidence(p.answers.needs_more_effort), 0));
			const answer = payload?.answers.recovery;
			const confidence = answerConfidence(answer);
			if (!answer || typeof answer.choice !== "string" || confidence < AUTO_ACCEPT) return;
			const escalate = (payload.answers.needs_more_effort?.bool ?? 0) >= AUTO_ACCEPT;
			return { content: [...event.content, { type: "text" as const, text: `laya ▸ recovery: ${answer.choice} (${confidence.toFixed(2)})${escalate ? " · escalate effort" : ""}` }] };
		} catch {
			return;
		}
	});
	pi.on("session.compacting", async (event, ctx) => {
		try {
			const state = event.messages.slice(-8).map(textFromMessage).filter(Boolean).map(text => excerpt(text, 300)).join("\n");
			if (!state) return;
			const payload = await decide(ctx, p => `compaction:${Object.values(p.answers).some(answer => (answer.bool ?? 0) >= AUTO_ACCEPT) ? "keep" : "none"} ${Math.max(...Object.values(p.answers).map(answerConfidence), 0).toFixed(2)}`, state, { keep_file_paths: { type: "bool", instructions: "preserve file paths in the summary" }, keep_test_results: { type: "bool", instructions: "preserve test results in the summary" }, keep_tool_output: { type: "bool", instructions: "preserve tool output in the summary" }, keep_plan_todos: { type: "bool", instructions: "preserve plan and todos in the summary" } }, p => Math.max(...Object.values(p.answers).map(answerConfidence), 0));
			if (!payload) return;
			const labels: string[] = [];
			for (const [key, label] of [["keep_file_paths", "file paths"], ["keep_test_results", "test results"], ["keep_tool_output", "tool output"], ["keep_plan_todos", "plan todos"]] as const) if ((payload.answers[key]?.bool ?? 0) >= AUTO_ACCEPT) labels.push(label);
			return labels.length > 0 ? { context: [`Compaction guidance (local laya): keep ${labels.join(", ")}`] } : undefined;
		} catch {
			return;
		}
	});

	// HookAPI's public overloads predate session_stop, but runtime on() and the
	// runner both support the verified event. Register through that runtime shape.
	const onRuntimeEvent = pi.on.bind(pi) as unknown as (event: "session_stop", handler: (event: StopEvent, ctx: HookContext) => Promise<{ continue: true; additionalContext: string } | undefined>) => void;
	onRuntimeEvent("session_stop", async (event, ctx) => {
		try {
			if (event.stop_hook_active || completionChecked) return;
			const last = textFromMessage(event.last_assistant_message);
			if (!last) return;
			completionChecked = true;
			const payload = await decide(ctx, p => `completion:${(p.answers.fully_complete?.bool ?? 0) >= AUTO_ACCEPT ? "yes" : "uncertain"} ${(p.answers.fully_complete?.bool ?? 0).toFixed(2)}`, excerpt(last, 1500), { fully_complete: { type: "bool", instructions: "the request's acceptance criteria are fully met; nothing actionable remains" } }, p => answerConfidence(p.answers.fully_complete));
			const probability = payload?.answers.fully_complete?.bool;
			if (typeof probability !== "number" || probability >= AUTO_ACCEPT) return;
			return { continue: true, additionalContext: `laya completion check: uncertain (P=${probability.toFixed(2)}) — verify the last reply against the request before finishing` };
		} catch {
			return;
		}
	});
}
