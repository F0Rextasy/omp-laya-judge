# omp-laya-judge

Local System-1 judge for [oh-my-pi](https://github.com/can1357/oh-my-pi), powered by
[laya](https://github.com/NandhaKishorM/laya). Typed decisions (`choice`/`bool`/`score`)
in ~1s on CPU, **0 LLM tokens burned**, nothing leaves the machine.

![before/after](assets/before-after.gif)

Measured head-to-head on the same classification questions (real model via
`omp -p` vs this plugin on CPU, no GPU):

| | LLM `judge()` | laya-judge |
|---|---|---|
| latency / question | ~18s (16–25s sampled) | ~0.35s (0.2–0.5s) |
| tokens / question | ~700 | 0 |
| accuracy (12-case bench) | n/a (reference) | 8/12, misses escalate < 0.6 confidence |

Checkpoint A/B/C + random (`benchmark/compare_models.py`):

| | EN 12-case | TR 4-case | mean conf | mean ms |
|---|---|---|---|---|
| english | 8 | 2 | 0.68 | 210 |
| typed-decisions | 8 | 3 | 0.54 | 198 |
| multilingual | 6 | 2 | 0.76 | 111 |
| random (coin flip) | 6 | 0 | — | 0 |

Default stays `english`: tied-best on EN with the highest usable confidence
(above the 0.6 auto-accept line), and the server already routes non-Latin
scripts to `multilingual` per request. TR sample is small (4); the honest
read is laya >> chance (2–3 vs 0), not a checkpoint coronation.

![benchmark](assets/benchmark.svg)

![quiz game](assets/quiz.gif)

Grounded quiz (`python demo/quiz.py`): **6/8, mean 282ms, 0 tokens** — both
misses came in under 0.2 confidence, exactly the cases the escalate rule
covers.

![snake](assets/snake.gif)

Snake (`python demo/snake.py`): **score 12, 300 moves, alive, 12 shield
interventions, ~0.9s/move on CPU, 0 tokens**. Watch every decision in the
browser: open `web/snake.html` (canvas replay with live probability bars,
play/pause/speed/scrub); quiz at `web-quiz.html`. Recipe (from
[laya-mlx](https://github.com/mizorewww/laya-mlx)): a deterministic planner
describes each direction over a tiny state (`Safe route: yes. Food reachable:
a safety shield executes the best SAFE move. Short parallel criteria are the
whole trick — greedy end-to-end choice without the planner scores 0 (see
`demo/snake.py` header for the measured failure modes). Honest calibration:
per-move confidences on game states run at noise level (0.005-0.05; the quiz
gets 0.2-1.0 on text) — navigation is planner + shield with laya ranking, the
same division of labor as laya-mlx. The maze variant (`demo/maze.py`) is a
documented negative: corridors trap the noise walk (1 crumb/200 steps).

![tetris](assets/tetris.gif)

Tetris (`python demo/tetris.py`): **score 5720, 43 lines, 120 pieces, alive,
~1s/placement on CPU, 0 tokens**. Per piece the planner enumerates every
legal landing, shortlists 6 by classic features (lines, holes, height,
bumpiness), laya picks one in a batched call, and a shield keeps the stack
out of the top-4 danger zone. Replay in browser: `web/tetris.html` (colored
board, candidate cards with live probabilities, scrub).

## Languages

The server lets laya route per request: English goes to the `english`
checkpoint, other scripts to `multilingual` (downloaded once, ~0.7GB extra).
Turkish works today (e.g. fatura/departman routing correct) but with lower
confidence than English — treat Turkish answers under 0.6 as escalate, same
rule as everything else. Set `LAYA_MODEL=english` in `.mcp.json` to pin.

## Install (step by step)

Requirements: Python 3.10+, `pip`, ~3GB disk (checkpoints), oh-my-pi.

```sh
pip install -r server/requirements.txt   # laya, mcp<2, torch, transformers>=4.48,<5
omp plugin marketplace add F0Rextasy/omp-marketplace
omp plugin install laya-judge@forextasy  # or: omp plugin link ./omp-laya-judge
omp plugin list                          # laya-judge should show ● enabled
```

Verify inside omp:

```text
Call the tool mcp__laya-judge__judge_info and reply with its exact JSON output.
```

Expected: `{"model": "laya/english", "device": "cpu", ...}`. First judgment
takes ~30s (checkpoint load), then ~0.3s each. If the server times out on
connect, raise `timeout` in `.mcp.json` (default 30s is shorter than the load).

Troubleshooting (all hit during development, all fixed in this repo):

- `transformers` < 4.48 cannot load ModernBERT → pin `transformers>=4.48,<5`.
- `mcp>=2` renamed FastMCP → pin `mcp<2`.
- On Windows, torch must init in the main thread → the server preloads eagerly.
- `python.EXE`/`py.EXE` uppercase spawn failures → use the shipped `.cmd` wrapper.

## Use

The plugin exposes `mcp__laya-judge__judge(state, questions)` plus the
`laya-judge` skill (triage → pre-filter → escalate). Question shapes mirror
oh-my-pi's eval `judge()`:

- `choice`: `criteria: {label: rubric}` → `{choice, probabilities, confidence}`
- `bool`: yes/no statement → `{bool: P(true)}` (laya `noul` head is `[false, true]`)
- `score`: `criteria: [lowest … highest]` → `{score, legend, probabilities, confidence}`

Every reply carries `model: "laya/english"`, per-call `latency_ms`, and laya's
own `usage`/`routing` block so provenance survives in the transcript.

## Status: tool + skill, not yet the agent loop

Honest scope: this plugin makes laya callable from any oh-my-pi session today.
It is **not** yet wired into the eval `judge()` chain — the agent won't call it
on its own; you (or your prompt) invoke it explicitly. Auto-routing cheap
judgments to laya inside `resolveJudge` is the planned next step, as a PR to
oh-my-pi core.

## Layout

- `.mcp.json` — stdio server declaration (relative paths, lengthened timeout)
- `server/server.py` — FastMCP server, eager checkpoint load
- `server/laya-judge.cmd` — Windows launcher
- `skills/laya-judge/SKILL.md` — when to use / when to escalate
- `benchmark/` — reproducible accuracy + latency proof
- `assets/` — `before-after.gif`, `benchmark.svg`, `quiz.gif`
