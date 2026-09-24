# Changelog

## [Unreleased]

### Fixed

- `server.py` startup: hub revision round-trip removed by default —
  `HF_HUB_OFFLINE=1` cache-first (a local judge must answer with no network
  once checkpoints are cached; `HF_HUB_OFFLINE=0` bootstraps a fresh install)
  and `set_num_interop_threads` made idempotent so a failed eager start no
  longer poisons every retry — the retry died with "...after parallel work
  has started", masking the real network error as an interop crash.
- `judge_batch`: nested `state`/`questions` accepted as JSON strings, mirroring
  `judge()`'s inputs (`'str' object has no attribute 'items'` before — the
  contract test only ever sent dicts).

### Added

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
