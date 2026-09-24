---
description: Route cheap classifications through the local laya judge first.
alwaysApply: true
---

Before spending an LLM call on a classification, routing, labeling, ranking,
or yes/no judgment, call the local judge first: `mcp__laya-judge__judge`
(0 tokens, ~0.3s, nothing leaves the machine). Use its answer directly when
confidence is 0.6 or above; escalate to the LLM `judge()` chain only for
answers under 0.6, nuanced reasoning, long context, or high-stakes verdicts.
Always record the `model` field (`laya/<checkpoint>`) next to any laya answer
so provenance survives in the transcript.
