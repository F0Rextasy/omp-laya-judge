/**
 * laya-judge live feed: see every local decision as it lands.
 *
 * MCP judge results and internal decision hooks share the queue in lib/record.
 * The turn-end card remains the single user-facing summary for that queue.
 */
import type { HookAPI } from "@oh-my-pi/pi-coding-agent/extensibility/hooks";
import { SIDECAR_URL, barsFromAnswer, decisionRows, formatMs, formatStatus, kindOfDecision, record, resetRecords, takeRecords, version } from "../lib/record";
import type { DecisionBar, LoggedAnswer } from "../lib/record";

type Answer = {
	choice?: string;
	bool?: number;
	score?: number;
	probabilities?: Record<string, number>;
	confidence?: number;
	legend?: Record<string, string>;
};
type ParsedDecision = { core: string; ms: number; model: string; conf: number; bars?: DecisionBar[]; kind?: string };

const KEY = "laya-live";

/** "billing 0.74" / "bool 0.86" / "medium 0.13⚠" - label, confidence, escalation gate. */
function fmt(answer: Answer): string {
	let head: string;
	if (answer.choice !== undefined) {
		head = answer.choice;
	} else if (typeof answer.bool === "number") {
		head = `bool ${answer.bool.toFixed(2)}`;
	} else if (typeof answer.score === "number") {
		head = answer.legend?.[String(Math.round(answer.score))] ?? `score ${answer.score.toFixed(1)}`;
	} else {
		head = "ok";
	}
	if (typeof answer.confidence === "number") {
		// bool: confidence IS P(true), already in the head - don't print it twice.
		if (typeof answer.bool !== "number") head += ` ${answer.confidence.toFixed(2)}`;
		if (answer.confidence < 0.6) head += "⚠";
	}
	return head;
}

/** First balanced JSON object in text; tool results can carry suffix junk. */
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

function parseResult(text: string): ParsedDecision {
	const firstConfidence = (answer: Answer | undefined): number => {
		if (typeof answer?.bool === "number") return answer.bool;
		return typeof answer?.confidence === "number" ? answer.confidence : 0;
	};
	const modelName = (payload: Record<string, unknown>): string =>
		String((payload.routing as Record<string, unknown> | undefined)?.model ?? payload.model ?? "laya").replace(/^laya\//, "");
	const payload = JSON.parse(extractBalancedJson(text)) as Record<string, unknown>;
	if (Array.isArray(payload.results)) {
		const firstPayload = payload.results[0] as Record<string, unknown> | undefined;
		const firstEntries = Object.entries((firstPayload?.answers ?? {}) as Record<string, Answer>);
		const core = firstEntries.length ? `×${payload.results.length} ${fmt(firstEntries[0][1])}` : `×${payload.results.length}`;
		const [qid, answer] = firstEntries[0] ?? [];
		return { core, ms: Number(payload.latency_ms ?? 0), model: "", conf: firstConfidence(answer), bars: answer === undefined ? [] : barsFromAnswer(qid ?? "q", answer) };
	}
	const entries = Object.entries((payload.answers ?? {}) as Record<string, Answer>);
	const core = entries.slice(0, 2).map(([, answer]) => fmt(answer)).join(" · ") || "ok";
	const bars = entries.flatMap(([qid, answer]) => barsFromAnswer(qid, answer)).slice(0, 6);
	return { core, ms: Number(payload.latency_ms ?? 0), model: modelName(payload), conf: Math.max(...entries.map(([, answer]) => firstConfidence(answer)), 0), bars };
}


/** Ring-buffer cursor into the sidecar's decision log; see pullHarnessDecisions. */
let harnessCursor = 0;

/**
 * The harness's own judgments never appear as tool results: once laya is the
 * judge role, oh-my-pi calls it directly over System One. The sidecar's
 * decision log is what makes those picks visible. Returns the records it
 * recorded so a caller can surface them immediately.
 */
async function pullHarnessDecisions(): Promise<ParsedDecision[]> {
	const pulled: ParsedDecision[] = [];
	try {
		const response = await fetch(`${SIDECAR_URL}/v1/decisions?since=${harnessCursor}`, { signal: AbortSignal.timeout(500) });
		if (!response.ok) return pulled;
		const payload = (await response.json()) as { next?: number; decisions?: { picks?: Record<string, LoggedAnswer>; model?: string; ms?: number }[] };
		if (typeof payload.next === "number") harnessCursor = payload.next;
		for (const decision of payload.decisions ?? []) {
			const entries = Object.entries(decision.picks ?? {});
			if (entries.length === 0) continue;
			const shown = entries.slice(0, 3).map(([id, value]) => `${id}=${typeof value === "string" ? value : value.pick ?? "?"}`).join(" ");
			const rest = entries.length > 3 ? ` +${entries.length - 3}` : "";
			const bars = entries.flatMap(([id, value]) => barsFromAnswer(id, value)).slice(0, 6);
			const entry = { core: `${shown}${rest}`, ms: decision.ms ?? 0, model: decision.model ?? "laya", conf: 0, bars };
			pulled.push(entry);
			record(entry);
		}
	} catch {
		// Best-effort display: a dead sidecar must never disturb the turn.
	}
	return pulled;
}


/** One logged pick as text. The ring stores objects (`{pick, probs}`), not
 * strings, so string-interpolating the value printed `[object Object]`. */
function pickLabel(value: LoggedAnswer): string {
	if (typeof value === "string") return value;
	if ("pick" in value && value.pick) return String(value.pick);
	if ("choice" in value && value.choice) return String(value.choice);
	if ("p" in value && typeof value.p === "number") return value.p.toFixed(2);
	if ("bool" in value && typeof value.bool === "number") return value.bool.toFixed(2);
	return "?";
}

/** `/laya-decisions` - the decision stream, the way the layer is meant to be watched. */
async function showDecisionStream(pi: HookAPI): Promise<void> {
	try {
		const response = await fetch(`${SIDECAR_URL}/v1/decisions?since=0`, { signal: AbortSignal.timeout(1000) });
		const payload = (await response.json()) as { decisions?: { picks?: Record<string, LoggedAnswer>; model?: string; ms?: number }[] };
		const decisions = payload.decisions ?? [];
		if (decisions.length === 0) {
			pi.sendMessage({ customType: "laya-decide", display: true, content: "⚡ laya ▸ no harness decisions recorded yet" });
			return;
		}
		const counts: Record<string, number> = { effort: 0, model: 0, step: 0, tool: 0, compact: 0, other: 0 };
		let totalMs = 0;
		for (const decision of decisions) {
			totalMs += decision.ms ?? 0;
			for (const pick of Object.keys(decision.picks ?? {})) {
				const kind = kindOfDecision(pick);
				counts[kind] = (counts[kind] ?? 0) + 1;
			}
		}
		const summary = Object.entries(counts)
			.filter(([, count]) => count > 0)
			.map(([kind, count]) => `${kind} ${count}`)
			.join(" · ");
		const recent = decisions.slice(-3)
			.map(decision => Object.entries(decision.picks ?? {}).slice(0, 2).map(([id, value]) => `${id}=${pickLabel(value)}`).join(" "))
			.join(" · ");
		pi.sendMessage({
			customType: "laya-decide",
			display: true,
			content: `⚡ laya ▸ ${decisions.length} decision${decisions.length > 1 ? "s" : ""} · ${totalMs}ms total · 0 tokens\n   ${summary}\n   recent: ${recent}`,
		});
	} catch {
		pi.sendMessage({ customType: "laya-decide", display: true, content: "⚠ laya ▸ sidecar unreachable - run /laya-judge:laya to check" });
	}
}
export default function hook(pi: HookAPI): void {
	pi.registerCommand("laya-decisions", {
		description: "laya decision stream: what the harness picked, by kind, with timing",
		handler: async () => showDecisionStream(pi),
	});
	pi.on("session_start", async (_event, ctx) => {
		resetRecords();
		ctx.ui.setStatus(KEY, undefined);
		// Skip whatever the sidecar logged before this session, so the first
		// card shows this turn's picks and not the ring buffer's history.
		harnessCursor = Number.MAX_SAFE_INTEGER;
		await pullHarnessDecisions();
	});

	// The harness decides before generation starts (auto-thinking picks the
	// effort level on the prompt path), so `agent_start` is the first moment
	// those picks exist. Surfacing them here is what makes the layer visible
	// while the model is still thinking, not only once the turn is over.
	pi.on("agent_start", async (_event, ctx) => {
		const pulled = await pullHarnessDecisions();
		if (pulled.length === 0) return;
		for (const entry of pulled) {
			ctx.ui.setStatus(KEY, formatStatus(entry));
		}
		const total = pulled.reduce((sum, entry) => sum + entry.ms, 0);
		const rows = decisionRows(pulled);
		pi.sendMessage({
			customType: "laya-decide",
			display: true,
			content: [
				`⚡ laya ▸ ${pulled.length} decision${pulled.length > 1 ? "s" : ""} before generation · ${formatMs(total)} · 0 tokens · v${version()}`,
				...rows,
			].join("\n"),
		});
	});

	pi.on("tool_result", (event, ctx) => {
		// Native tool name (underscored or dashed spelling) or the write-device
		// bridge omp routes MCP through (write -> xd://mcp__laya_judge_judge).
		const norm = (value: string) => value.replace(/^xd:\/\//, "").replace(/[-_]+/g, "_");
		const judge = norm("mcp__laya-judge__judge");
		const judgeBatch = norm("mcp__laya-judge__judge_batch");
		let isJudge = norm(event.toolName) === judge || norm(event.toolName) === judgeBatch;
		if (!isJudge && event.toolName === "write" && typeof event.input.path === "string") {
			isJudge = norm(event.input.path) === judge || norm(event.input.path) === judgeBatch;
		}
		if (!isJudge) return;
		let text = "";
		for (const part of event.content) if (part.type === "text") text += part.text;
		if (event.isError) {
			ctx.ui.setStatus(KEY, `⚠ laya ▸ ${text.replace(/\s+/g, " ").slice(0, 70)}`);
			return;
		}
		let parsed: ParsedDecision;
		try {
			parsed = parseResult(text);
		} catch {
			ctx.ui.setStatus(KEY, "⚠ laya ▸ unparseable result");
			return;
		}
		const entry: ParsedDecision = {
			core: parsed.core,
			ms: parsed.ms,
			model: parsed.model,
			conf: parsed.conf,
			bars: parsed.bars,
			kind: "judge",
		};
		ctx.ui.setStatus(KEY, formatStatus(entry));
		// Native and write-bridge events carry identical payloads; count the pair once.
		record(entry, text);
	});

	pi.on("turn_end", async () => {
		await pullHarnessDecisions();
		const queued = takeRecords();
		if (queued.length === 0) return;
		const rows = decisionRows(queued.slice(0, 6));
		const more = queued.length > 6 ? `   +${queued.length - 6} more` : "";
		const total = queued.reduce((sum, entry) => sum + entry.ms, 0);
		pi.sendMessage({
			customType: "laya-live",
			display: true,
			content: [
				`⚡ laya ▸ ${queued.length} decision${queued.length > 1 ? "s" : ""} · ${formatMs(total)} · 0 tokens · v${version()}`,
				...rows,
				more,
			].filter(Boolean).join("\n"),
		});
	});
}
