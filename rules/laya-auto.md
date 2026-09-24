---
description: Route cheap classifications through the local laya judge first.
alwaysApply: true
---

Before spending an LLM call on a classification, routing, labeling, ranking,
or yes/no judgment, call the local judge first: `mcp__laya-judge__judge`
(0 tokens, ~0.2s, nothing leaves the machine). Never send arithmetic, parity,
counting, or symbolic-logic questions to laya — a semantic encoder guesses
them confidently wrong (measured above the gate); those go straight to the
LLM. Use laya's answer directly when confidence is 0.6 or above; escalate to
the LLM `judge()` chain only for answers under 0.6, nuanced reasoning, long
context, or high-stakes verdicts. Always record the `model` field
(`laya/<checkpoint>`) next to any laya answer so provenance survives in the
transcript.
