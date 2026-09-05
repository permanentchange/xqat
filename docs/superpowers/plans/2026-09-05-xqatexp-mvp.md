# XQatExp MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a complete installable pure-Python XQatExp MVP that turns explicit Tushare Raw artifacts into Research data, runs one shared strategy in backtest and daily modes, and publishes validated deterministic results.

**Architecture:** A local modular monolith uses explicit immutable Artifacts, Arrow/Parquet storage, DuckDB-backed restricted research views, a pure Strategy Core, and separate simulated backtest versus user-supplied daily account paths. Application services compose modules; only `data fetch` may access the network, and all other use cases operate from explicit local inputs.

**Tech Stack:** CPython 3.12, standard-library argparse/TOML/dataclasses/Decimal, httpx, pyarrow, duckdb, numpy, jsonschema, pytest, hypothesis, ruff, mypy, pip-tools.

**Spec:** `task.txt`; `PRD.txt`; `总体架构设计.md`; `docs/detailed-design/README.md` and numbered documents 00–18.

## Global Constraints

- Use CPython `>=3.12,<3.13` in project-root `.venv`; never depend on global site-packages.
- Package and CLI name are `xqatexp`; use src layout and dependencies from detailed design 17.
- Preserve `PRD.txt` and `总体架构设计.md` byte-for-byte and recheck their recorded SHA-256 values at final acceptance.
- Keep Raw and Research separate; all input/output paths are explicit; never discover `latest`.
- Strategy is pure and consumes only restricted views; Backtest and Daily call the same `weekly_market_guard_rank_v1` implementation.
- Money, prices, weights, rates, quantities, time and sorting follow documents 00, 05, 07–10 and 16 exactly.
- Read Tushare credentials only from `TUSHARE_TOKEN`; never persist or log a secret or a secret fragment.
- Ordinary automated tests are offline; live Tushare tests require both the marker and explicit CLI option.
- The workspace is an initialized Git repository on `master`; the user explicitly approved in-place edits and commits. Preserve unrelated changes, stage exact task files, and commit only after the corresponding verification checkpoint passes.

---

### Task 1: Installable project, domain primitives, and CLI shell

**Files:**
- Create: `pyproject.toml`, `src/xqatexp/__init__.py`, `src/xqatexp/__main__.py`, `src/xqatexp/cli.py`
- Create: `src/xqatexp/domain/enums.py`, `src/xqatexp/domain/contracts.py`, `src/xqatexp/domain/issues.py`, `src/xqatexp/domain/numeric.py`
- Test: `tests/unit/test_numeric.py`, `tests/contract/test_domain_contracts.py`, `tests/integration/test_cli.py`

**Interfaces:**
- Produces: `main(argv: Sequence[str] | None) -> int`, `Issue`, immutable Target/Account/Rebalance dataclasses, `quantize_money`, `quantize_price`, and all enums from design 01.
- Consumes: no earlier production task.

- [ ] Write tests asserting Decimal ROUND_HALF_UP money/tick behavior, finite factor rejection, enum values, immutable contracts, and identical help behavior for `python -m xqatexp` and `xqatexp`.
- [ ] Run `python -m pytest tests/unit/test_numeric.py tests/contract/test_domain_contracts.py tests/integration/test_cli.py -q`; expect import/entry-point failures.
- [ ] Implement the exact enums and contracts from design 01 and a CLI parser containing every command in design 02. Command handlers may return the documented exit code for missing required application wiring, but no command may report success without executing its use case.
- [ ] Re-run the three test files; expect all tests to pass and `python -m xqatexp --help` to list `self-check`, `data`, `factor`, `backtest`, `daily`, and `result`.
- [ ] Run Ruff format/check and mypy on the new files; record the checkpoint in the plan.

### Task 2: Versioned schemas, canonical serialization, and artifact safety

**Files:**
- Create: `schemas/*.schema.json`, `src/xqatexp/artifacts/schemas.py`, `manifest.py`, `readers.py`, `publisher.py`
- Test: `tests/contract/test_json_schemas.py`, `test_parquet_schemas.py`, `test_artifact_roundtrip.py`, `tests/unit/test_publisher.py`

**Interfaces:**
- Consumes: Task 1 contracts and `Issue`.
- Produces: `SchemaRegistry.validate_json(schema_id, value)`, `canonical_json_bytes(value)`, `ArtifactReader.open(path)`, and `ArtifactPublisher.publish(staging_builder, target, existing_policy)`.

- [ ] Write failing golden and invalid cases for every schema ID in design 16, including unknown Major, extra input field, invalid null, duplicate Parquet key, unstable row order, digest mismatch, existing target, interrupted new publish, and recoverable Windows overwrite.
- [ ] Run `python -m pytest tests/contract/test_json_schemas.py tests/contract/test_parquet_schemas.py tests/contract/test_artifact_roundtrip.py tests/unit/test_publisher.py -q`; expect missing registry/publisher failures.
- [ ] Implement machine-readable schemas, canonical JSON/CSV helpers, exact Arrow schemas, digest verification, explicit-path readers, same-parent staging, and the recoverable swap protocol from design 17 §7.
- [ ] Re-run the artifact tests; require producer output to be read by the real consumer and require the last verified target to survive every injected publish failure.
- [ ] Scan generated fixtures for absolute-path leakage and rerun Ruff/mypy.

### Task 3: Configuration, secret boundary, redaction, and self-check

**Files:**
- Create: `src/xqatexp/config.py`, `src/xqatexp/security.py`, `src/xqatexp/application/services.py`
- Modify: `src/xqatexp/cli.py`
- Test: `tests/unit/test_config.py`, `test_security.py`, `tests/e2e/test_self_check.py`

**Interfaces:**
- Consumes: Task 1 contracts and Task 2 schema registry.
- Produces: `resolve_config(cli_values, toml_path) -> ResolvedRunContext`, non-revealing `SecretValue`, `redact(value, known_secrets)`, and `SelfCheckService.run(offline=True)`.

- [ ] Write tests for CLI-over-TOML precedence, cross-field strategy constraints, full score-weight validation, explicit output, forbidden secret keys, missing environment Token, canary redaction from nested data/exceptions/files, and offline self-check network denial.
- [ ] Run the focused tests; expect missing configuration/security services.
- [ ] Implement deterministic TOML normalization, path alias/digest handling, secret loading only at the composition root, recursive redaction, documented exit-code mapping, and offline self-check of versions/schemas/temp writes/golden numeric vectors.
- [ ] Re-run focused tests and execute `python -m xqatexp self-check --offline`; require exit 0 without `TUSHARE_TOKEN` and no network attempt.
- [ ] Scan source, tests, examples and captured output for the canary and rerun Ruff/mypy.

### Task 4: Tushare client, capability probes, Raw publication, and Raw checks

**Files:**
- Create: `src/xqatexp/providers/tushare/client.py`, `registry.py`, `capability.py`, `raw.py`
- Modify: `src/xqatexp/application/services.py`, `src/xqatexp/cli.py`
- Test: `tests/unit/providers/test_tushare_client.py`, `test_registry.py`, `tests/integration/test_raw_artifact.py`, `tests/live/test_tushare_capabilities.py`

**Interfaces:**
- Consumes: Task 2 Artifact publisher and Task 3 `SecretValue`.
- Produces: `TushareClient.query(api_name, fields, params)`, `CapabilityProbe.run(dataset_ids)`, `RawFetchService.fetch(request)`, and `RawCheckService.check(path)`.

- [ ] Write mocked transport tests for each dataset registration, stable paging, rate limiting, retryable versus permission errors, anomalous empty responses, schema mismatch, and guaranteed omission of token from request artifacts/errors.
- [ ] Run all non-live provider tests; expect missing provider implementation.
- [ ] Implement the exact registry in design 04, direct HTTPS JSON requests, bounded exponential retries, request-rate control, pagination completeness, gzip JSONL Raw records, request metadata and atomic Raw Artifact publication.
- [ ] Re-run provider and Raw round-trip tests, then run `data capabilities` without a token and require exit 3 with `SECURITY_SECRET_MISSING` and no output artifact.
- [ ] When `TUSHARE_TOKEN` is present, run only the minimal live capability test and save the redacted capability result; when absent, record the external blocker without marking the test passed.

### Task 5: Raw-to-Research deterministic preparation and system factors

**Files:**
- Create: `src/xqatexp/research/tables.py`, `preparation.py`, `factors.py`
- Test: `tests/unit/research/test_factors.py`, `test_financial_versions.py`, `tests/integration/test_research_build.py`, `test_research_update.py`
- Fixtures: `tests/fixtures/raw_minimal/`, `tests/golden/research_minimal/`

**Interfaces:**
- Consumes: verified Raw Artifacts from Task 4 and Arrow schemas from Task 2.
- Produces: `ResearchBuilder.build(raw_roots, config, output)`, `ResearchBuilder.update(base, raw_roots, output)`, and the seven Parquet tables in design 05.

- [ ] Write failing tests for unit conversions, adjusted research price, historical ST, listing days, full-day suspension, locked limits, announcement visibility, financial revision/YTD-quarter-TTM derivation, ROE annualization, all registered factors, Type-7 winsorization, average-rank percentile, OLS, and deterministic incremental equivalence.
- [ ] Run the research tests; expect missing builder/factor failures.
- [ ] Implement ordered Arrow transformations and NumPy float64 factor calculations while retaining Decimal at price/money boundaries; write ZSTD Parquet in exact schemas and publish only after coverage/key/source-hash checks.
- [ ] Re-run unit/integration tests and byte/row compare full build versus incremental update for the same logical input.
- [ ] Run `data check-research` against valid and deliberately corrupt fixtures and require exact Issue codes.

### Task 6: Restricted research session, readiness, and custom factors

**Files:**
- Create: `src/xqatexp/research/session.py`, `readiness.py`, `custom_factors.py`
- Test: `tests/unit/research/test_custom_factors.py`, `tests/integration/test_research_session.py`, `test_readiness.py`, `tests/e2e/test_custom_factor_path.py`

**Interfaces:**
- Consumes: Task 5 Research Artifact.
- Produces: `ResearchSession.view(decision_date)`, `ResearchDataView.slice(as_of_date)`, `ExecutionDataView`, `CustomFactorView.values_at`, and `ReadinessChecker.check(declaration, view, custom_factors)`.

- [ ] Write failing counterfactual tests proving that D+1 rows and later financial revisions cannot affect Slice H; assert rejection of history ending after H, missing 313-day input, incomplete historical ST/ETF adjustment coverage, and custom-factor duplicate/nonfinite/98%-coverage failures.
- [ ] Run the focused tests; expect missing session/readiness failures.
- [ ] Implement parameterized DuckDB queries with explicit projected fields, enforced date/availability predicates, detached sorted domain records, query budgets, dependency closure and exact-date custom-factor joins.
- [ ] Re-run focused and custom-factor E2E tests; disabling the factor must avoid opening its file, enabling weight 0.10 must change at least one score and add the fixed visibility limitation.
- [ ] Run the same tests with DuckDB thread counts 1 and 4 and require identical business output.

### Task 7: Shared weekly strategy, scoring, replay, and rolling drawdown

**Files:**
- Create: `src/xqatexp/strategy/declaration.py`, `scoring.py`, `market_regime.py`, `drawdown.py`, `replay.py`, `weekly_strategy.py`
- Test: `tests/unit/strategy/test_scoring.py`, `test_market_regime.py`, `test_drawdown.py`, `test_holdings.py`, `tests/integration/test_strategy.py`
- Golden: `tests/golden/strategy/*.json`

**Interfaces:**
- Consumes: Task 6 restricted views and custom factor view.
- Produces: `WeeklyMarketGuardRankStrategy.generate_target(view, params, custom_factor_view=None) -> TargetPortfolio` with stable ID `weekly_market_guard_rank_v1`.

- [ ] Write failing tests from design 18 for budgets, pool shortage and rolling points 59–62; add boundaries for ETF regimes, six-factor score, optional factor direction, filters, rank ties, 2–8 week holding, risk exits, recovery weeks, natural weight drift, 313/320 lookback and input-order determinism.
- [ ] Run strategy tests; expect missing strategy modules.
- [ ] Implement the declaration and pure modules using only resolved parameters, then replay each historical weekly H through `slice(H)` from an empty formal state and calculate the no-fee naturally drifting base NAV.
- [ ] Re-run strategy tests and compare Daily versus Backtest direct calls for byte-equivalent unannotated targets.
- [ ] Mutation check: modify future data, account data, simulated fills and previous result files; the same D target must remain identical.

### Task 8: Portfolio validation, transitions, and deterministic rebalance planning

**Files:**
- Create: `src/xqatexp/portfolio/validation.py`, `transitions.py`, `rebalance.py`
- Test: `tests/unit/portfolio/test_validation.py`, `test_transitions.py`, `test_rebalance.py`

**Interfaces:**
- Consumes: Task 1 contracts and Task 7 TargetPortfolio.
- Produces: `validate_target`, `annotate_transitions(current, previous)`, and `RebalancePlanner.plan(...) -> RebalancePlan`.

- [ ] Write failing tests for weight conservation/tolerance, supported asset uniqueness, INITIAL/NEW/RETAIN/INCREASE/DECREASE/EXIT, full-exit odd lots, partial-sell lots, target amount/quantity vector in design 18, missing value/price, sell ordering, relative-gap buy ordering and fee-aware cash allocation.
- [ ] Run portfolio tests; expect missing modules.
- [ ] Implement pure Decimal calculations, stable sorts, separate Issue and reason namespaces, and linear per-candidate lot allocation; never mutate the Target.
- [ ] Re-run portfolio tests and Hypothesis properties asserting nonnegative cash, quantities within target/current/sellable bounds and invariant output under input permutation.
- [ ] Verify Daily planning never counts expected sells as cash and Backtest exposes separate SELL and BUY planning entry points.

### Task 9: Execution, fees, corporate actions, and simulated account

**Files:**
- Create: `src/xqatexp/backtest/fees.py`, `execution.py`, `corporate_actions.py`, `account.py`
- Test: `tests/unit/backtest/test_fees.py`, `test_execution.py`, `test_corporate_actions.py`, `test_account.py`

**Interfaces:**
- Consumes: Task 8 Rebalance instructions and Task 6 ExecutionDataView.
- Produces: `FeeModel.calculate`, `ExecutionSimulator.execute`, `CorporateActionProcessor.apply`, and event-sourced `SimulatedAccount`.

- [ ] Write failing tests for design 18 fee vectors, slippage/tick/clamp, suspension/locked limit/no price, volume capacity/partial fill, zero gross, actual-sell cash availability, T+1 sellable release, cash dividend record/ex/pay, integer stock distribution, split and unsupported rights failure.
- [ ] Run focused backtest-domain tests; expect missing modules.
- [ ] Implement exact Decimal execution and fee formulas, stable execution IDs, explicit unfilled reasons, entitlements and the eight-step corporate action order from design 09.
- [ ] Re-run focused tests plus account conservation properties after every event; any altered or omitted event must trigger `BACKTEST_ACCOUNT_CONSERVATION_BROKEN`.
- [ ] Verify all generated Trade and Unfilled rows conform to design 16 schemas.

### Task 10: Backtest engine and performance analysis

**Files:**
- Create: `src/xqatexp/backtest/engine.py`, `src/xqatexp/performance/metrics.py`, `periods.py`, `contribution.py`
- Test: `tests/integration/test_backtest_engine.py`, `tests/unit/performance/test_metrics.py`, `test_contribution.py`, `test_periods.py`
- Golden: `tests/golden/backtest_minimal/`

**Interfaces:**
- Consumes: Tasks 6–9.
- Produces: `BacktestEngine.run(context) -> BacktestResult` and `PerformanceAnalyzer.analyze(...) -> PerformanceResult`.

- [ ] Write failing sequence tests for D close → T corporate actions → open mark → SELL execute → updated BUY plan/execute → close valuation, and golden tests for NAV, trades, unfilled rows, benchmark, return intervals, Sharpe/Calmar, turnover, max-drawdown tie rules, rolling periods and design 18 contribution vector.
- [ ] Run backtest/performance tests; expect missing engine/analyzer failures.
- [ ] Implement chronological orchestration, valuation and the exact formulas from design 10; assert daily account and contribution conservation before accepting a result.
- [ ] Re-run focused tests and an offline historical fixture backtest; require all schema readers to consume the output and the report layer to use those facts without recomputation.
- [ ] Repeat the run twice and compare every business file after excluding Manifest identity/time fields.

### Task 11: Daily target, AccountSnapshot degradation, and advice

**Files:**
- Create: `src/xqatexp/daily/account_snapshot.py`, `target.py`, `advice.py`
- Test: `tests/unit/daily/test_account_snapshot.py`, `test_advice.py`, `tests/e2e/test_daily.py`
- Fixtures: `examples/account-complete.json`, `account-empty.json`, `account-partial.json`, `account-unknown.json`

**Interfaces:**
- Consumes: Tasks 2, 6–8 and the same Task 7 strategy instance.
- Produces: `DailyTargetService.run`, `parse_account_snapshot`, and `DailyAdviceService.run`.

- [ ] Write failing tests for confirmed empty versus UNKNOWN versus PARTIAL holdings, incomplete management scope, derivable versus conflicting managed total assets, stale account, outside assets, missing cash/sellable/reference price, no-account operation, no-change result and fixed non-order disclaimer.
- [ ] Run daily tests; expect missing daily services.
- [ ] Implement strict input normalization and the design 11 degradation matrix; preserve reliable target facts while setting forbidden precision fields to null with exact Issue/limitation/reason codes.
- [ ] Re-run tests and Hypothesis properties proving suggested sells never exceed confirmed sellable quantity and suggested buys plus worst-case fees never exceed current available cash.
- [ ] Compare same-D target from Daily and Backtest strategy calls for exact equality before annotation.

### Task 12: Structured results, Markdown reports, and CLI application wiring

**Files:**
- Create: `src/xqatexp/reporting/structured.py`, `markdown.py`
- Modify: `src/xqatexp/application/services.py`, `src/xqatexp/cli.py`
- Test: `tests/contract/test_result_artifacts.py`, `tests/integration/test_result_show.py`, `tests/e2e/test_offline_workflows.py`

**Interfaces:**
- Consumes: every earlier task.
- Produces: all commands from design 02, all Result/Failure Artifact layouts from design 03/16, and reports from structured facts only.

- [ ] Write failing contract/E2E tests for Backtest, Daily Target, Daily Advice, checks and Failure Diagnostic; assert exact file sets, schema versions, stable Issue order, visible assumptions/limitations, custom-factor responsibility and no partial success.
- [ ] Run result and workflow tests; expect missing orchestration/report failures.
- [ ] Wire each CLI command to an application service, serialize validated structures, generate Markdown by field lookup only, and atomically publish after schema/hash/secret scans.
- [ ] Re-run tests; exercise output-exists, explicit overwrite recovery, corrupt input, permission failure and individual Daily degradation without contaminating unrelated items.
- [ ] Run `result show` in summary/markdown/json modes and confirm it never recalculates a metric or advice quantity.

### Task 13: Deterministic offline example, user README, and dependency locks

**Files:**
- Create: `examples/config-offline.toml`, `examples/custom-factor.csv`, deterministic example Raw/Research input, root `README.md`, `requirements.in`, `requirements-dev.in`, `requirements.lock`, `requirements-dev.lock`
- Test: `tests/e2e/test_readme_commands.py`, `tests/e2e/test_installation.py`

**Interfaces:**
- Consumes: completed CLI.
- Produces: copy/paste Windows installation and full offline workflow, plus reproducible hash-locked environments.

- [ ] Write a test that executes the documented offline commands in a temporary workspace and checks Research, Backtest, Daily Target, complete-account Advice and custom-factor outputs.
- [ ] Run it before adding example assets/README; expect explicit missing-file or missing-command failure.
- [ ] Add non-secret example inputs and a README covering venv creation, installation, environment-token setup, capability probes, fetch/build/check, backtest, daily commands, result viewing, outputs and stable error remedies.
- [ ] Generate compatible hashed lock files with pip-tools, install them in the project `.venv`, and rerun the README workflow with network disabled.
- [ ] Scan every tracked workspace file except immutable upstream inputs for token-shaped/canary secrets and verify examples contain no real credentials.

### Task 14: Full quality gates, clean-environment acceptance, and final report

**Files:**
- Modify: `docs/detailed-design/15-verification-report.md`
- Create: `docs/final-verification-report.md`
- Test: entire repository.

**Interfaces:**
- Consumes: all tasks.
- Produces: final reproducible evidence and a directly usable tool.

- [ ] Run format, lint, strict mypy, all non-live tests with coverage, CLI help, offline self-check, schema/doc checks and complete offline E2E using design 17 §8 commands; preserve exact counts and failures.
- [ ] Resolve failures by root cause without weakening assertions or skipping required paths; rerun the entire gate until clean.
- [ ] Validate the resolved `.venv` path is exactly the project-root environment, recreate it, install only from hash locks, install the package, and repeat CLI help/self-check/offline E2E.
- [ ] If `TUSHARE_TOKEN` is present, run low-call capability probes and the smallest legal live chain; otherwise report `SECURITY_SECRET_MISSING` as an unexecuted external acceptance item, never as pass/skip success.
- [ ] Recompute both immutable upstream SHA-256 values, scan for secrets/caches/temp artifacts, remove only verified generated caches/temp paths, and write the final report with environment, commands, test counts, coverage, interface results, limitations and direct run steps.
