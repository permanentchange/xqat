# Repository Guidelines

## Project Structure & Module Organization
XQatExp is an A-share research and decision CLI. `src/xqatexp/` separates application/CLI, providers, artifacts, research, strategy, portfolio, backtest, daily, and reporting. Schemas live in `schemas/`; configuration and examples live in `examples/`.

Read [architecture](docs/architecture.md), [detailed designs](docs/detailed-design/README.md), and [data operations](docs/data.md) before changing boundaries. Place strategies in `src/xqatexp/strategy/strategies/<strategy_id>/` with parameters, declaration, implementation, and README; register in `strategy/registry.py`. Keep shared mechanisms separate.

## Build, Test, and Development Commands
Use CPython 3.12 and `conda activate xqat`. From the repository root:

```bash
python -m pip install --require-hashes -r requirements-build.lock
python -m pip install --require-hashes -r requirements-dev.lock
python -m pip install --no-deps --no-build-isolation -e .
python -m pytest -m "not live_tushare" --cov=xqatexp --cov-fail-under=80 -q
python -m ruff check src/xqatexp tests
python -m mypy src/xqatexp
python -m xqatexp self-check --offline
python examples/generate_offline_example.py --root .example-work
python -m pip wheel --no-deps --no-build-isolation . --wheel-dir dist
```

These install locked dependencies, then run tests, lint, typing, diagnostics, example generation, and packaging. Linux is primary; Windows has compatibility CI.

## Coding Style & Architecture Rules
Use four-space indentation, annotations, `snake_case` functions/modules, and `PascalCase` classes. Ruff targets Python 3.12, 100 columns, and import ordering; Mypy is strict.

Preserve schema validation, canonical serialization, hashes, and atomic publication. Strategies access ResearchSession under declaration and decision-date limits; backtest/Daily remain offline. Confirmed fills/actions advance state. Use `pathlib` and preserve Linux/Windows behavior.

## Data Acquisition & Configuration
Reuse `providers/tushare/runtime.py` for shared HTTP and configurable limits. Keep partition planning/resume in `batch.py` and indices/generations/locking in `collection.py`. Preserve Raw compatibility and canonical financial datasets across ordinary/VIP APIs. Keep provider TOML separate from strategy configuration; consult `examples/provider-5000.toml` and `examples/fetch-bootstrap.toml`. Test dry runs without network, credentials, or writes.

## Testing Guidelines
Use pytest/Hypothesis under `tests/{unit,contract,integration,property,e2e,live}/`; name tests `test_*`. Keep strategy tests under `tests/unit/strategy/strategies/<strategy_id>/` with unique filenames. Verify schema/wheel packaging when adding schemas and workflow compatibility when changing acquisition.

CI requires 80% coverage and excludes `tests/contract/test_documentation.py`; run documentation checks locally. Live tests require explicit authorization, `TUSHARE_TOKEN`, and `--live-tushare`. Linux tests block; Windows, Ruff, and Mypy are advisory.

## Commit & Pull Request Guidelines
Use imperative subjects with `feat:`, `fix:`, `docs:`, `test:`, `refactor:`, or `chore:`. Explain behavior, related issues, and validation. Update README/designs/examples with CLI, schema, or strategy changes; document current behavior without obsolete discussion.

## Security & Generated Files
Read credentials only from `TUSHARE_TOKEN`; never embed tokens in files, arguments, or logs. Keep artifacts in ignored `data/`, `.local/`, or `.example-work/`. Keep inputs/outputs disjoint; reject symlinked/junction collection paths.
