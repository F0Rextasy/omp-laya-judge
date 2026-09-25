# Changelog

## [Unreleased]

### Changed

- Model ownership moved out of the stdio MCP process. `server/sidecar.py`
  now binds before loading the single resident router, serves status while it
  loads, and exits after an idle window; `server/bridge.py` keeps the three
  MCP tools as a model-free HTTP forwarder and starts the sidecar on demand.
- Decision cards read as one layer now: harness-side judgments (the `act:`,
  `recovery:`, `notes` gates) carry their distributions, so a bool verdict
  draws a gauge instead of a bare text row, and the row builder is shared
  between the before-generation and turn-end cards. Three rendering defects
  measured against live cards are fixed: a `recovery:` pick classified as
  `other` (the pattern was end-anchored while its sibling `route:` was not), a
  12-character label ran into its own bar, and a pick whose text already opens
  with its kind (`notes=CHANGELOG.md`) printed that word twice.
- Arithmetic questions no longer reach the model. `core.resolve_arithmetic`
  settles parity, primality, divisibility and threshold comparisons exactly,
  and only claims a question when the state carries a single distinct number
  and the instruction names an operation on it. The bench measured why: both
  parity misses sat at confidence 0.80-0.84, above the 0.6 gate, so no
  threshold could have caught them. Measured after the change: **10/12
  correct** (was 8/12), **0 escapes**, **false-accept 0%** (was 29%), and the
  two remaining misses are `score` questions the gate now catches. Wired into
  all three surfaces — `/judge`, `/v1/systemone` and `judge_batch`.

### Fixed

- `POST /v1/systemone` with more than `LAYA_MAX_QUESTIONS` questions raised
  `NameError: merged` (the chunked path referenced a variable that was never
  assigned) and returned 500 for every wide request - the `find` cascade asks
  58 questions in one call, so this was a live route.

- Sidecar startup is cache-first by default: `HF_HUB_OFFLINE=1` keeps local
  operation independent of the hub (set `HF_HUB_OFFLINE=0` only to bootstrap
  a fresh install). The HTTP server binds before model loading, so status stays
  available and a load failure is reported without taking the sidecar down.
- `judge_batch`: nested `state`/`questions` accepted as JSON strings, mirroring
  `judge()`'s inputs (`'str' object has no attribute 'items'` before — the
  contract test only ever sent dicts).
- An oversized judgment request no longer monopolizes the model lock: over
  `LAYA_MAX_TOTAL_QUESTIONS` (default 256) the sidecar answers 422, which the
  client treats as non-transient so the role chain moves on. A timeout would
  have thrown instead, taking the whole judgment with it.
- Client disconnects (judgment timeout, aborted turn) no longer dump a
  `ConnectionResetError` traceback per request.
- The sidecar's idle window is 1 hour, not 10 minutes. Reloading the
  checkpoints costs ~20s and that cost lands entirely on the next turn's
  first judgment, which made the 10-minute default a routine stall. It still
  exits eventually, so an abandoned sidecar does not leak its RAM; set
  `LAYA_SIDECAR_IDLE` in seconds, or `0` to disable the idle exit.
- The risk gate speaks the typed escalation contract: `deny` stops the action
  with a reason, `escalate` hands an uncertain judgement back to the model,
  `allow` stays silent and lets the normal permission flow decide — the layer
  can only ever tighten. A 3-level `rate` framing was measured and **rejected**
  before adopting it: it flattened the signal (bad 0.43 vs routine 0.45, and
  safe commands rose 0.21 -> 0.36), so the bool head stays.

### Added


- **laya is now a native System One backend for oh-my-pi.** The sidecar
  serves `POST /v1/systemone` and `GET /v1/models`, which is the wire the
  harness's `judge` role chain speaks for native backends (the same slot
  TypeSafe `jev` and OpenRouter Decisions occupy). With `TYPESAFE_BASE_URL`
  pointed at the sidecar and `modelRoles.judge: typesafe/jev-latest`, the
  harness routes its own judgments to laya — the main model never calls a
  judge tool. Verified live: oh-my-pi's `find` cascade sent a 58-question
  `systemone` request directly to the sidecar.
- Every file action now goes past laya before it runs, not just suspicious
  shell commands. The verdict is always visible in the footer and reaches
  the model on its next step; hard blocks stay reserved for measured
  patterns. Verified live: emptying a scratch file drew `act:edit review
  0.82` plus an `escalate` advisory in the transcript, and the model
  completed the edit.
- System One requests are chunked at 8 questions and merged. Measured on CPU:
  8 questions ≤810ms, 58 questions 8.6s — inside the harness's 10s judgment
  budget, where a single 58-question pass overran it and wedged the model
  lock for every later caller.
- System One decisions are now visible in the chat. The sidecar keeps a
  ring-buffer log (`GET /v1/decisions?since=N`) and the turn-end card pulls
  what the harness chose, so the picks laya makes for oh-my-pi itself read
  the same way MCP calls already did: `⚡ laya ▸ 2 decisions this turn —
  level=xhigh · stopped=0.23 · avg 254ms`. Without it the native judge role
  was silent: the harness calls laya directly, so nothing reached the
  transcript.
- Decision cards now draw what the model chose *among*: the sidecar logs the
  full answer distribution and the cards render ten-cell probability bars
  with the winner marked (`xhigh ███████░░░ 0.66 ◀`). Applies to harness
  decisions, MCP judge calls, and `/laya-decisions` alike.
- Banner and `/laya` verdict are English (`● laya active`); decision kinds
  cover the hook prefixes (`act:`, `notes`, `risk:`, `route:`, `focus:`)
  instead of falling to `other`.


- `hooks/pre/laya-live.ts`: live decision feed — footer status line per laya
  call (`⚡ laya ▸ billing 0.74 · english · 185ms`, ⚠ when confidence < 0.6,
  errors surfaced too) plus one per-turn summary card; same display channel
  as the startup banner, driven by the `tool_result`/`turn_end` hook events.
  omp delivers every MCP call twice (native event + the `write xd://` bridge)
  with identical payloads — the pair counts as one decision, and any tool
  spelling (`mcp__laya-judge__judge`, underscored names, device paths)
  canonicalizes to the same match. Payloads carrying a stray second content
  part (quoted fragment + markdown fence) parse via balanced JSON extraction
  instead of failing the whole result.

- `hooks/pre/laya-decide.ts`: harness-side decision layer. The plugin no
  longer waits to be called — hooks ask the local sidecar directly at eight
  decision points (destructive-command caution, task routing, tool-error
  recovery, completion check, compaction guidance, note selection, focused
  checks, sidecar health). Every gate is fail-open: a timeout, dead sidecar,
  or malformed reply means no decision, never a block. `hooks/lib/record.ts`
  holds the decision queue so MCP results and internal decisions share the
  one turn-end card.

### Fixed

- Hook gates read `.bool` from sidecar replies, but the sidecar serves laya's
  raw heads — the yes/no head arrives as `noul` (which is P(true), per
  `core.unpack_judge_answer`). Without normalizing it every bool gate read
  `undefined` and failed open, so the layer was silent on every command
  including `rm -rf /`.
- `ensureSidecar` discarded its health probe and spawned a sidecar on every
  check; it now returns early when `/info` answers.
- The focused-checks gate derived candidates from `git diff HEAD`, which
  misses the newly written (untracked) test files it most often needs;
  it reads `git status --porcelain` now.
- `cargo`/`go`/`make` were in the full-suite list but not the benign
  prefilter, so their branch was unreachable.
- The focused-checks reason no longer fabricates file-append commands for
  runners that reject them (`go test ./... <file>`); it names the file instead.

### Measured

- Gate calibration against the shipped checkpoint is published in the README.
  Short version: the destructive-command gate separates real signal
  (dangerous 0.43–0.78 vs read commands 0.12–0.34) but cannot rank deletions
  (`rm -rf build` 0.68 vs `rm -rf /` 0.75), so the block bar stays at 0.85 and
  the live behaviour is a caution. Routing, recovery, completion, and
  focused-check choices score 0.01–0.30 — below the 0.6 gate, so they are wired
  but do not act on this checkpoint.

## [0.3.0] - 2026-09-24

Numbers-first release: every figure the README, chart, or GIF publishes is
derived from a committed artifact (`benchmark/results.json`,
`benchmark/calibration.json`, `demo/*-stats.json`) and pinned by
`tests/test_readme_lint.py` + `tests/test_calibration.py`.

### Fixed

- `.mcp.json`: removed the `LAYA_MODEL=english` pin (it silently disabled
  the documented auto-routing for non-Latin scripts) and raised `timeout`
  from 120000 to 600000 ms — the cold start downloads ~0.7GB of
  checkpoints and could not finish inside 120s.
- `server/requirements.txt`: unquoted the specifiers (`"mcp<2"` →
  `mcp<2`), bumped `laya` 0.3.4 → 0.3.20 (thread-safe model lifecycle,
  upstream laya #100; `predict_batch` for whole-batch forwards).
- License mismatch: `plugin.json` said `Apache-2.0` while `package.json`
  and `LICENSE` say MIT. Unified on MIT; both versions aligned at `0.3.0`.
- Sidecar docstring told users to run `python server/http.py`; the file is
  `server/sidecar.py`.
- README latency claims (~1s intro, ~0.35s table) contradicted
  `results.json` (mean 1025ms) and each other. All latency text now comes
  from the schema-2 results file: mean 160ms, p50 151ms, p95 215ms.
- README claimed "misses escalate < 0.6 confidence" while `results.json`
  held two misses at confidence 0.80/0.84. Claim removed; the gate's real
  behavior is computed (`benchmark/calibrate.py`) and published instead:
  auto-accept 7/12, 2 of 4 misses caught, 2 escaped, false-accept 29%.
- Concurrent `judge()` calls crashed with laya's
  `RuntimeError: Already borrowed` (non-reentrant fast tokenizer,
  reproduced with 4 threads). Every router call now serializes through
  `core.PREDICT_LOCK`; torch still parallelizes inside one predict.
- `benchmark/chart.py` repeated the same falsified escalation line in
  `assets/benchmark.svg`; the footer now quotes `calibration.json`.

### Changed

- `benchmark/run.py` writes schema-2 results: `import_s`, `warmup_s`,
  `p50_ms`, `p95_ms`, `timestamp`, protocol block; stdout prints p50/p95.
- `rules/laya-auto.md` + `skills/laya-judge/SKILL.md`: explicit
  arithmetic/parity exclusion (both escaped bench misses are parity
  questions — the model is confidently wrong there, so the skill routes
  arithmetic to the LLM instead of trusting confidence).

### Added

- `benchmark/calibrate.py` → `benchmark/calibration.json`: reproducible
  0.6-gate metrics (recomputation is asserted by
  `tests/test_calibration.py`).
- `benchmark/before_after.py`: source for `assets/before-after.gif`,
  driven by `results.json` (the GIF previously had no generator).
- `demo/style.py`: one shared look for all media (truetype badges,
  colored probability bars, wrapped question text with markdown
  stripped, palette-quantized GIF saves). Every asset in `assets/` is
  re-rendered through it — the previous GIFs used PIL's 6px default
  font and showed raw `*asterisks*` from the question states.
- `tests/` — fast suite (core mapping, sidecar HTTP contract, repo
  hygiene, README-vs-artifact lint, calibration reproducibility) and
  slow suite (`LAYA_SLOW_TESTS=1`: model contract, MCP stdio handshake,
  routing tripwire incl. non-Latin + Turkish diacritics, concurrency).
- `.github/workflows/test.yml`: runs the fast suite on push/PR.
- This changelog.

## [0.2.0]

- First release: MCP stdio server, skill, benchmarks, demos (quiz/snake/
  tetris), HTTP sidecar skeleton.
