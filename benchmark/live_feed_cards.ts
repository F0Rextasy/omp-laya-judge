// Collect the decisions the harness actually asks for, and render them with
// the shipped card renderer. Prints JSON for benchmark/live_feed_gif.py to
// draw, so the pixels come from real answers and the real row builder - no
// mock-ups and no hand-written card text.
import { decisionRows, formatMs, version } from "../hooks/lib/record";
import { barsFromAnswer } from "../hooks/lib/record";
import type { DecisionBar, LoggedAnswer } from "../hooks/lib/record";

const SIDECAR = process.env.LAYA_SIDECAR_URL ?? "http://127.0.0.1:3777";

interface Scenario {
	label: string;
	kind: string;
	state: unknown;
	questions: Record<string, Record<string, unknown>>;
	/** The card core the hook would build from the answer. */
	core: (answers: Record<string, Record<string, unknown>>) => string;
}

// The sidecar's raw noul answer carries `noul`, which barsFromAnswer does not
// read; the product normalizes it in sidecar._record_decision before the
// feed sees it. Mirror that exactly, or the cards drawn here would not be the
// cards users get.
function asLoggedAnswer(answer: Record<string, unknown>): LoggedAnswer {
	const type = answer.type as string | undefined;
	if (type === "noul") {
		const value = Number(answer.noul ?? 0);
		return { pick: value.toFixed(2), p: value };
	}
	if (type === "score") {
		return { pick: String(Number(answer.score ?? 0).toFixed(1)), p: Number(answer.confidence ?? 0) };
	}
	return {
		pick: String(answer.choice ?? ""),
		probs: (answer.probabilities ?? {}) as Record<string, number>,
		conf: Number(answer.confidence ?? 0),
	};
}


const SCENARIOS: Scenario[] = [
	{
		label: "notes gate · before generation",
		kind: "notes",
		state: { text: "fix the login bug and update the changelog" },
		questions: {
			note_0: { type: "bool", instructions: "this project note is relevant to the request above" },
			note_1: { type: "bool", instructions: "this project note is relevant to the request above" },
			note_2: { type: "bool", instructions: "this project note is relevant to the request above" },
		},
		core: (a) => {
			const hits = ["CHANGELOG.md", "README.md", "demo/tetris.py"]
				.map((label, index) => ({ label, p: Number((a[`note_${index}`] as { noul?: number })?.noul ?? 0) }))
				.filter(entry => entry.p >= 0.6);
			return `notes ${hits.length}/3${hits.length > 0 ? ` ${hits.map(entry => entry.label).join(" ")}` : ""}`;
		},
	},
	{
		label: "edit gate · every file action",
		kind: "tool",
		state: { tool: "edit", input: "server/core.py · replace 40 lines" },
		questions: { risky_change: { type: "bool", instructions: "this file change is destructive, irreversible, or exposes secrets" } },
		core: (a) => {
			const p = Number((a.risky_change as { noul?: number })?.noul ?? 0);
			return `act:edit ${p >= 0.85 ? "deny" : p >= 0.8 ? "review" : "ok"} ${p.toFixed(2)}`;
		},
	},
	{
		label: "bash gate · destructive command",
		kind: "tool",
		state: { command: "rm -rf ./build && git clean -fdx" },
		questions: { is_this_dangerous: { type: "bool", instructions: "this command is destructive, irreversible, or security-sensitive" } },
		core: (a) => {
			const p = Number((a.is_this_dangerous as { noul?: number })?.noul ?? 0);
			return `risk:${p >= 0.85 ? "deny" : p >= 0.8 ? "escalate" : "allow"} ${p.toFixed(2)}`;
		},
	},
	{
		label: "recovery gate · a tool just failed",
		kind: "step",
		state: "tool: bash\ninput: py -3 -m pytest -q\nerror: ModuleNotFoundError: No module named 'core'",
		questions: {
			recovery: {
				type: "choice", instructions: "choose the next action",
				criteria: {
					fix_and_retry: "correct the failure and retry",
					read_source_or_docs: "inspect source or documentation first",
					narrow_scope: "reduce the failing scope first",
					ask_user: "request a missing decision or credential",
				},
			},
		},
		core: (a) => {
			const answer = a.recovery as { choice?: string; confidence?: number } | undefined;
			return `recovery:${answer?.choice ?? "none"} ${(answer?.confidence ?? 0).toFixed(2)}`;
		},
	},
];

interface Card { label: string; core: string; rows: string[]; ms: number; kind: string }

const cards: Card[] = [];
for (const scenario of SCENARIOS) {
	const started = Date.now();
	const response = await fetch(`${SIDECAR}/v1/systemone`, {
		method: "POST",
		headers: { "content-type": "application/json" },
		body: JSON.stringify({ state: scenario.state, questions: scenario.questions }),
	});
	if (!response.ok) {
		console.error(`sidecar refused ${scenario.label}: ${response.status}`);
		continue;
	}
	const payload = await response.json() as {
		answers?: Record<string, { probabilities?: Record<string, number>; pick?: string; confidence?: number }>;
		latency_ms?: number;
	};
	const answers = payload.answers ?? {};
	const core = scenario.core(answers as never);
	const bars = Object.entries(answers)
		.flatMap(([qid, answer]) => barsFromAnswer(qid, asLoggedAnswer(answer as Record<string, unknown>)))
		.slice(0, 6);
	const record = { core, ms: payload.latency_ms ?? Date.now() - started, model: "laya", conf: 0, bars: bars as DecisionBar[] };
	cards.push({
		label: scenario.label,
		kind: scenario.kind,
		core,
		ms: record.ms,
		rows: decisionRows([record]),
	});
}

const total = cards.reduce((sum, card) => sum + card.ms, 0);
console.log(JSON.stringify({ version: version(), total, cards }, null, 1));
