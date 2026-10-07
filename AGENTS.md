# Repository Guidelines

## Project Structure & Module Organization
XQatExp is a local A-share quantitative research and decision CLI. Source lives in `src/xqatexp/`, organized into domain contracts, providers, research, strategy, portfolio, backtest, daily workflows, and reporting. Versioned JSON schemas live in `schemas/`; configuration and offline examples live in `examples/`. Read `docs/architecture.md` and `docs/detailed-design/` before changing architectural boundaries.

Tests are grouped under `tests/{unit,contract,integration,property,e2e,live}/`. Put new strategies in `src/xqatexp/strategy/strategies/<strategy_id>/`, with parameters, declaration, implementation, and README; register them in `strategy/registry.py`. Keep shared strategy mechanisms separate from individual implementations.

## Build, Test, and Development Commands
Use an independent CPython 3.12 environment, preferably `conda activate xqat`. Install locked dependencies and the editable package:

```bash
python -m pip install --require-hashes -r requirements-build.lock
python -m pip install --require-hashes -r requirements-dev.lock
python -m pip install --no-deps --no-build-isolation -e .
```

Run these from the repository root:

```bash
python -m pytest -m "not live_tushare" -q
python -m ruff check src/xqatexp tests
python -m mypy src/xqatexp
python -m xqatexp self-check --offline
python examples/generate_offline_example.py --root .example-work
python -m pip wheel --no-deps --no-build-isolation . --wheel-dir dist
```

These run offline tests, lint, strict type checking, installation diagnostics, example generation, and wheel building, respectively.

## Coding Style & Naming Conventions
Use four-space indentation, type annotations, `snake_case` functions/modules, and `PascalCase` classes. Ruff targets Python 3.12 with a 100-character line limit and import-order checks. Mypy runs in strict mode. Follow existing module patterns; CI has no formatting gate. Preserve deterministic artifact serialization and use `pathlib` for cross-platform paths.

## Testing Guidelines
Use pytest and Hypothesis; name files `test_*.py` and functions `test_*`. Add tests in the matching suite, including schema/contract checks for artifact changes. Keep strategy unit tests under `tests/unit/strategy/strategies/<strategy_id>/` with unique module names. Offline CI requires 80% coverage via `--cov=xqatexp --cov-fail-under=80` and excludes `tests/contract/test_documentation.py`. Live Tushare tests require explicit authorization, `TUSHARE_TOKEN`, and `--live-tushare`.

## Commit & Pull Request Guidelines
Follow recent history: concise imperative subjects prefixed with `docs:`, `test:`, `refactor:`, or `chore:`. PR descriptions should explain the change, link relevant issues, and report validation commands/results. Update documentation for CLI, schema, or strategy behavior changes. Linux CI is blocking; Windows compatibility checks, Ruff, and Mypy are advisory.

## Security & Configuration
Read Tushare credentials only from `TUSHARE_TOKEN`; never place tokens in configuration, source, or command arguments. Keep generated data and artifacts in ignored directories such as `data/`, `.local/`, and `.example-work/`. Keep input and output paths disjoint and avoid symlinked artifact outputs.
