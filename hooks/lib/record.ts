export const LAYA_STATUS_KEY = "laya-live";
export const SIDECAR_URL = "http://127.0.0.1:3777";

import { readFileSync } from "node:fs";
import * as path from "node:path";

/**
 * Build stamp from the manifest.
 *
 * Hooks load once per omp process, so a window opened before an update keeps
 * the old code in memory. Printing the version on the session banner *and* on
 * every card means any card identifies the code that produced it, instead of
 * the user having to guess which window is current.
 */
let cached: string | undefined;

export function version(): string {
	if (cached !== undefined) return cached;
	cached = "dev";
	try {
		const manifest: unknown = JSON.parse(readFileSync(path.resolve(import.meta.dir, "../../package.json"), "utf-8"));
		if (manifest && typeof manifest === "object" && "version" in manifest && typeof manifest.version === "string") {
			cached = manifest.version;
		}
	} catch {
		// A missing or unreadable manifest leaves the dev stamp in place.
	}
	return cached;
}

export interface DecisionRecord {
	core: string;
	ms: number;
	model: string;
	conf: number;
	bars?: DecisionBar[];
	/**
	 * Column label. Harness picks are classified from their question id
	 * (`kindOfDecision`); an MCP judge answer is classified by who asked, so
	 * the most common card on screen is not filed under "other".
	 */
	kind?: string;
}

/** One probability row: an option label, its mass, and whether it won. */
export interface DecisionBar {
	label: string;
	p: number;
	picked: boolean;
}

/** Logged System One picks and MCP judge answers, in either wire shape. */
export type LoggedAnswer =
	| string
	| { pick?: string; probs?: Record<string, number>; conf?: number; p?: number }
	| { choice?: string; probabilities?: Record<string, number>; confidence?: number; bool?: number; score?: number };

/**
 * Distribution rows for one answer: every option the model scored, winner
 * marked. Choice answers expand to one row per label; a yes/no answer is a
 * single gauge on its own id. Anything without a distribution yields no rows,
 * so callers never have to pre-check the shape.
 */
export function barsFromAnswer(label: string, answer: LoggedAnswer): DecisionBar[] {
	if (typeof answer !== "object" || answer === null) return [];
	const probs = ("probs" in answer ? answer.probs : undefined)
		?? ("probabilities" in answer ? answer.probabilities : undefined);
	const pick = ("pick" in answer ? answer.pick : undefined)
		?? ("choice" in answer ? answer.choice : undefined);
	if (probs !== undefined && probs !== null && typeof probs === "object" && Object.keys(probs).length > 0) {
		return Object.entries(probs)
			.map(([name, value]) => ({ label: name, p: Number(value) || 0, picked: name === pick }))
			.sort((a, b) => b.p - a.p);
	}
	const single = ("p" in answer && typeof answer.p === "number") ? answer.p
		: ("bool" in answer && typeof answer.bool === "number") ? answer.bool : NaN;
	return Number.isNaN(single) ? [] : [{ label, p: single, picked: true }];
}

/**
 * Ten-cell rows every decision card draws alike: label, bar, mass, winner.
 *
 * The label column is 22 wide because the card is drawn in a normal terminal,
 * not a narrow pane. Truncating to fit a bar (the earlier 11-character cut)
 * produced half-words like `CHANGELOG.m`; an ellipsis reads as a deliberate
 * cut, a raw slice does not.
 */
const LABEL_WIDTH = 22;

function labelCell(text: string): string {
	const clean = text.replace(/\s+/g, " ").trim();
	return clean.length <= LABEL_WIDTH ? clean.padEnd(LABEL_WIDTH) : `${clean.slice(0, LABEL_WIDTH - 1)}…`;
}

export function renderBars(bars: DecisionBar[]): string[] {
	const rows: string[] = [];
	for (const item of bars.slice(0, 6)) {
		const p = Math.max(0, Math.min(1, item.p));
		const filled = Math.round(p * 10);
		rows.push(`   ${labelCell(item.label)}${"█".repeat(filled)}${"░".repeat(10 - filled)} ${p.toFixed(2)}${item.picked ? " ◀" : ""}`);
	}
	return rows;
}

/** Question ids the harness uses, grouped the way the decision panel labels them. */
const DECISION_KINDS: Record<string, RegExp> = {
	effort: /^(level|bucket|effort)$/,
	route: /^route:/,
	model: /^(model|route_model)$/,
	step: /^(step|next|focus|recovery|completion|stopped)(:|$)/,
	tool: /^(pick|tool|q\d+|candidate|act|risk)(:|$)|^risky_change$/,
	notes: /^(notes?|note_\d+)$/,
	compact: /^(compact|compaction|keep_)/,
};

export function kindOfDecision(pick: string): string {
	for (const [kind, pattern] of Object.entries(DECISION_KINDS)) {
		if (pattern.test(pick)) return kind;
	}
	return "other";
}

/**
 * Card head row. The kind column is dropped when the core text already opens
 * with it: a harness pick reads `notes=README.md`, and a leading `notes`
 * column would print the same word twice.
 */
export function decisionHead(kind: string, core: string, ms: number): string {
	const lead = core.toLowerCase().startsWith(kind.toLowerCase()) ? core : `${kind.padEnd(8)}${core}`;
	return `   ${lead.slice(0, 66).padEnd(66)}${formatMs(ms).padStart(7)}`;
}

/**
 * Card rows for a queue: every head, distribution bars for the first `withBars`.
 *
 * Identical decisions collapse into one row with a count and their summed
 * time. A turn of fourteen file edits is one decision, not fourteen lines of
 * `act:edit ok 0.20` - the repetition was what made the feed read as noise.
 */
export function decisionRows(entries: DecisionRecord[], withBars = 2): string[] {
	const kindOf = (entry: DecisionRecord): string => entry.kind ?? kindOfDecision(entry.core.split(/[ =]/)[0] ?? "");
	const groups = new Map<string, { entry: DecisionRecord; count: number; ms: number }>();
	for (const entry of entries) {
		const key = `${kindOf(entry)}|${entry.core}`;
		const existing = groups.get(key);
		if (existing) {
			existing.count += 1;
			existing.ms += entry.ms;
		} else {
			groups.set(key, { entry, count: 1, ms: entry.ms });
		}
	}
	return [...groups.values()].flatMap((group, index) => {
		const count = group.count > 1 ? ` ×${group.count}` : "";
		const head = decisionHead(kindOf(group.entry), group.entry.core + count, group.ms);
		return index < withBars && group.entry.bars !== undefined && group.entry.bars.length > 0 ? [head, ...renderBars(group.entry.bars.slice(0, 4))] : [head];
	});
}

let records: DecisionRecord[] = [];
let lastDedupeKey: string | undefined;

/** Append one display decision. MCP callers may pass the raw result as a dedupe key. */
export function record(entry: DecisionRecord, dedupeKey?: string): boolean {
	if (dedupeKey !== undefined && dedupeKey === lastDedupeKey) return false;
	records.push(entry);
	lastDedupeKey = dedupeKey;
	return true;
}

export function resetRecords(): void {
	records = [];
	lastDedupeKey = undefined;
}

/** The turn-end card is the sole consumer and drains the shared queue once. */
export function takeRecords(): DecisionRecord[] {
	const drained = records;
	records = [];
	lastDedupeKey = undefined;
	return drained;
}

/** Sub-second reads better in ms; a judgment that took seconds should not lie about it. */
export function formatMs(ms: number): string {
	return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`;
}

export function formatStatus(entry: DecisionRecord): string {
	return `⚡ laya ▸ ${entry.core} · ${formatMs(entry.ms)}`;
}
