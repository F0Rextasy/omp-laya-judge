# Laya Judge skill

Use the local Laya System-1 judge (`mcp__laya-judge__judge`) for cheap,
deterministic classification before spending LLM calls.

## When to use

- Triage: route, label, or rank items (tickets, emails, files, review findings).
- Pre-filter: shrink a large candidate set locally, then judge only the
  ambiguous survivors with the LLM `judge()` chain.
- Confidence check: Laya returns calibrated probabilities; distrust any
  answer under ~0.6 and escalate it.

## When NOT to use

- Nuanced reasoning, long context (>512-1024 tokens), or generation.
- Final verdicts on high-stakes decisions — Laya is a first pass.

## Calling

`judge(state, questions)` — state is text or a JSON document; questions map
an id to `{type, instructions, criteria?}`:

- `choice`: `criteria: {label: rubric}` → `{choice, confidence}`
- `bool`: → `{bool: P(yes)}`
- `score`: `criteria: [lowest … highest]` → `{score, confidence}`

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

The tool replies with `{answers, model: "laya/<checkpoint>", latency_ms}`.
No data leaves the machine; no tokens are burned.
