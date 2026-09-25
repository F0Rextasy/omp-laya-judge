---
description: Check laya local judge status with a live smoke judgment.
---

Report laya-judge status:

1. Call the `judge_info` tool (`mcp__laya-judge__judge_info`) and show its exact output (model, device).
2. Run one live smoke judgment: call `mcp__laya-judge__judge` with state `{"message": "You were charged twice, refund please"}` and a `choice` question routing to billing/support/security. Show the picked label, confidence, and latency.
3. Verdict in one line: `● laya active` (both calls succeed) or the exact error otherwise. If confidence is under 0.6, say so and escalate policy applies.
