# v0.4.0 — laya decides in the loop

A local System-1 judge for oh-my-pi, and the release where it stopped waiting
to be called: it now decides at the harness's own decision points, and every
decision it makes is visible as a card.

## What changed

**The model is a setting, not a fork.** The sidecar already spoke
`POST /v1/systemone`, so the judge behind it is selected by environment:

```sh
LAYA_BACKEND=systemone LAYA_UPSTREAM_URL=... LAYA_UPSTREAM_KEY_ENV=... # von, jev
LAYA_MODEL=multilingual ...                                             # faster, less sure
```

`/info` reports which backend actually answers, so the banner never claims
laya while something else is doing the work. The key is named indirectly
(`LAYA_UPSTREAM_KEY_ENV`), never stored in a config file.

**Arithmetic is exact and never leaves the machine.** `core.resolve_arithmetic`
settles parity, primality, divisibility and threshold comparisons, and claims
a question only when the state carries one distinct number and the instruction
names an operation on it. The bench measured why this had to exist: both
parity misses sat at confidence 0.80–0.84, above the escalation gate, so no
threshold could have caught them.

**The gates gate on the calibrated number.** laya returns two confidence
fields; `confidence` is `1 - H(p)/log k` for choice and score, `answer_confidence`
is `max(p)` everywhere. The gates were reading the first. Switched: local
auto-accept **7/12 → 9/12**, false-accept still **0%**.

**A block needs the host's agreement.** The bool head denied two plain source
files at 0.88 and 0.93. A write/edit block now also requires a
secret-shaped path or a credential-shaped payload; uncorroborated verdicts
stop at the card, which is free.

## Measured

| | 12-case bench | 56-case set |
|---|---|---|
| accuracy | 10/12 (was 8/12) | 33/56 |
| escaped misses | 0 (was 2) | — |
| false-accept | 0% (was 29%) | — |
| latency | mean 402ms, p50 238ms | median 242ms, p95 491ms |
| LLM tokens | 0 | 0 |

The 56-case set exists because 12 cases could not separate two models and
were blind to where this layer actually fails: severity 3/11, the mixed
call shape 3/6, and **Turkish 0/7 on every backend tried, including laya's
multilingual checkpoint**. That last one is a real gap, not a bad pick.

## Fixes worth listing

Every one was found by measuring the running layer, not by reading it:
the notes gate re-recommending the same notes forever; repeated decisions
printed one per line instead of collapsed with a count; `/laya-decisions`
printing `[object Object]`; `judge_batch` answering questions against an
empty state while reporting success; the focused-checks gate being
unreachable because its allowlist was end-anchored; `sonic` routing matching
7 of 12 mechanical prompts; `__tests__/x.js` not recognised as a test file;
a `NameError` that 500'd every systemone request wider than 8 questions.

## Reproduce

```sh
python benchmark/run.py                  # 12-case accuracy + latency
python benchmark/calibrate.py            # gate metrics
python benchmark/compare_backends.py --backend laya --backend laya:multilingual
python benchmark/live_feed_gif.py         # assets/live-feed.gif, from live answers
```

## Thanks

The confidence-field finding and the benchmark harness are the kind of
contribution laya's CONTRIBUTING.md calls first class ("share benchmarks,
evaluations, or integration reports"). Happy to open either as a PR.
