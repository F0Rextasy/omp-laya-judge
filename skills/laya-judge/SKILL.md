# Laya Judge skill

Use the local Laya System-1 judge (`mcp__laya-judge__judge`, or
`judge_batch` for many states) for cheap deterministic classification
before spending LLM calls.

## When to use

- Triage: route, label, or rank items (tickets, emails, files, review findings).
- Pre-filter: shrink a large candidate set locally, then judge only the
  ambiguous survivors with the LLM `judge()` chain.
- Confidence check: Laya returns calibrated-ish probabilities; distrust any
  answer under ~0.6 and escalate it.

## When NOT to use

- **Multi-step arithmetic, counting, or symbolic logic** — parity,
  primality, divisibility and plain comparisons are answered exactly before
  the model is consulted (`server/core.py:resolve_arithmetic`), but only for a
  state with a single unambiguous number. Wider computation is a confident
  guess, not a measurement: send it to the LLM.
- Nuanced reasoning, long context (>512-1024 tokens), or generation.
- Final verdicts on high-stakes decisions — Laya is a first pass.

## Harness-side gates (no call needed)

The plugin also decides on its own. `hooks/pre/laya-decide.ts` consults the
same local sidecar before some tool calls and at session boundaries, so a
decision can arrive as a `blocked` tool result, a risk caution, an appended
recovery hint, or an injected note list. You do not have to request these.

They are fail-open and measured: on the shipped checkpoint only the
destructive-command caution acts, and it warns rather than blocks. Treat a
`laya ▸` line as a cheap local opinion to weigh, not as a verified fact — the
README publishes the per-gate numbers.

## Calling

`judge(state, questions)` — state is text or a JSON document; questions map
an id to `{type, instructions, criteria?}`:

- `choice`: `criteria: {label: rubric}` → `{choice, confidence}`
- `bool`: → `{bool: P(yes)}`
- `score`: `criteria: [lowest … highest]` → `{score, confidence}`

`judge_batch([{state, questions}, ...])` returns `{results, count,
latency_ms}` — one local call for a whole candidate set.

## The 0.6 gate, honestly

- The escalation threshold is **0.6 by default**, but confidence runs
  checkpoint-dependent: english averages ~0.68, typed-decisions ~0.54 on the
  12-case bench (see `benchmark/compare.json`). Under a pinned non-default
  checkpoint, re-check the gate against `benchmark/calibration.json` before
  trusting it — the auto-accept/false-accept split is published there.
- laya ≥ 0.3.20 warns at load that one temperature entry in the english
  checkpoint is out of range and its confidences are uncalibrated; treat the
  gate as a triage heuristic, not a guarantee.

## Framing (measured, not style)

- Keep criteria SHORT and PARALLEL (`"invoices, payments, refunds"`). Long,
  uneven criteria collapse to prior noise; short ones decided 0.76 vs 0.43
  in A/B tests.
- Align STATE words with LABELS: the head matches them, not the criteria
  prose. State "food is below" + label `down` works; "west" + `left` does not.
- Few options: accuracy falls off past ~20 labels (fixed token budget per
  label). Split large sets coarse-to-fine instead.
- Never echo a previous answer into the state ("Last move was X" flips the
  next pick even when it means death). State facts, not history.

The tool replies with `{answers, model: "laya/<checkpoint or auto>",
routing, usage, latency_ms}`. No data leaves the machine; no tokens are
burned.
