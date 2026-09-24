/**
 * laya-judge live feed: see every local decision as it lands.
 *
 * - tool_result on the judge tools. omp delivers every MCP call twice with
 *   identical payloads: a native tool_result (name surfaced underscored as
 *   mcp__laya_judge_judge, or dashed mcp__laya-judge__judge) and the
 *   write-device bridge result (write -> xd://mcp__laya_judge_judge).
 *   The paired duplicate is counted once (same payload -> one queue entry).
 *     footer status line (last decision, snake-panel style):
 *       ⚡ laya ▸ billing 0.74 · english · 185ms
 *     ⚠ marks confidence < 0.6 (the laya-auto escalation gate).
 * - turn_end: one card summarizing this turn's decisions. The raw tool
 *   results are already in the transcript, so the card is display-only
 *   noise-control for the user - the LLM does not need a duplicate.
 */
import type { HookAPI } from "@oh-my-pi/pi-coding-agent/extensibility/hooks";

type Answer = {
	choice?: string;
	bool?: number;
	score?: number;
	probabilities?: Record<string, number>;
	confidence?: number;
	legend?: Record<string, string>;
};

const KEY = "laya-live";
let queued: { core: string; ms: number; text: string }[] = [];

/** "billing 0.74" / "bool 0.86" / "medium 0.13⚠" - label, confidence, escalation gate. */
function fmt(a: Answer): string {
	let head: string;
	if (a.choice !== undefined) {
		head = a.choice;
	} else if (typeof a.bool === "number") {
		head = `bool ${a.bool.toFixed(2)}`;
	} else if (typeof a.score === "number") {
		head = a.legend?.[String(Math.round(a.score))] ?? `score ${a.score.toFixed(1)}`;
	} else {
		head = "ok";
	}
	if (typeof a.confidence === "number") {
		// bool: confidence IS P(true), already in the head - don't print it twice.
		if (typeof a.bool !== "number") head += ` ${a.confidence.toFixed(2)}`;
		if (a.confidence < 0.6) head += "⚠"; // laya-auto: escalate to the LLM
	}
	return head;
}

/** First balanced JSON object in `text` - tool results can carry stray suffix junk
 * (a quoted fragment + markdown fence arrives as a second content part). */
function extractBalancedJson(text: string): string {
	const start = text.indexOf("{");
	if (start < 0) return text;
	let depth = 0;
	let inStr = false;
	let esc = false;
	let i = start;
	for (const ch of text.slice(start)) {
		if (esc) esc = false;
		else if (inStr && ch === "\\") esc = true;
		else if (ch === '"') inStr = !inStr;
		else if (!inStr) {
			if (ch === "{") depth++;
			else if (ch === "}") {
				depth--;
				if (depth === 0) return text.slice(start, i + 1);
			}
		}
		i++;
	}
	return text;
}

function parseResult(text: string): { core: string; ms: number; model?: string } {
	const payload = JSON.parse(extractBalancedJson(text));
	if (Array.isArray(payload.results)) {
		// judge_batch: {results: [judge payload, ...], count, latency_ms}
		const firstAnswers: Answer[] = Object.values(payload.results[0]?.answers ?? {});
		const core = firstAnswers.length
			? `×${payload.results.length} ${fmt(firstAnswers[0])}`
			: `×${payload.results.length}`;
		return { core, ms: payload.latency_ms ?? 0 };
	}
	const answers: Answer[] = Object.values(payload.answers ?? {});
	const core = answers.slice(0, 2).map(fmt).join(" · ") || "ok";
	const model = String(payload.routing?.model ?? payload.model ?? "laya").replace(/^laya\//, "");
	return { core, ms: payload.latency_ms ?? 0, model };
}

export default function hook(pi: HookAPI): void {
	pi.on("session_start", (_event, ctx) => {
		queued = [];
		ctx.ui.setStatus(KEY, undefined);
	});

	pi.on("tool_result", (event, ctx) => {
		// Native tool name (underscored or dashed spelling) or the write-device
		// bridge omp routes MCP through (write -> xd://mcp__laya_judge_judge).
		// Collapse separator runs so every spelling canonicalizes identically.
		const norm = (s: string) => s.replace(/^xd:\/\//, "").replace(/[-_]+/g, "_");
		const JUDGE = norm("mcp__laya-judge__judge");
		const JUDGE_BATCH = norm("mcp__laya-judge__judge_batch");
		let judgeTool = norm(event.toolName) === JUDGE || norm(event.toolName) === JUDGE_BATCH;
		if (!judgeTool && event.toolName === "write" && typeof event.input.path === "string") {
			judgeTool = norm(event.input.path) === JUDGE || norm(event.input.path) === JUDGE_BATCH;
		}
		if (!judgeTool) return;
		let text = "";
		for (const part of event.content) {
			if (part.type === "text") text += part.text;
		}
		if (event.isError) {
			// Surface the failure live; it is not a decision, so never queue it.
			ctx.ui.setStatus(KEY, `⚠ laya ▸ ${text.replace(/\s+/g, " ").slice(0, 70)}`);
			return;
		}
		let parsed: { core: string; ms: number; model?: string };
		try {
			parsed = parseResult(text);
		} catch {
			ctx.ui.setStatus(KEY, "⚠ laya ▸ unparseable result");
			return;
		}
		ctx.ui.setStatus(
			KEY,
			`⚡ laya ▸ ${parsed.core}${parsed.model ? ` · ${parsed.model}` : ""} · ${parsed.ms}ms`,
		);
		// The native event and its write-bridge twin carry identical payloads -
		// count a back-to-back duplicate once.
		if (queued.at(-1)?.text !== text) queued.push({ core: parsed.core, ms: parsed.ms, text });
	});

	pi.on("turn_end", () => {
		if (queued.length === 0) return;
		const shown = queued.slice(0, 3).map((q) => q.core).join(" · ");
		const more = queued.length > 3 ? ` +${queued.length - 3} more` : "";
		const avg = Math.round(queued.reduce((s, q) => s + q.ms, 0) / queued.length);
		pi.sendMessage({
			customType: "laya-live",
			display: true,
			content: `⚡ laya ▸ ${queued.length} decision${queued.length > 1 ? "s" : ""} this turn — ${shown}${more} · avg ${avg}ms`,
		});
		queued = [];
	});
}
