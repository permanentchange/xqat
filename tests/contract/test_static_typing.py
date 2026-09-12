from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _assert_mypy_clean(module: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "mypy", module],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_schema_module_typechecks_in_locked_developer_environment() -> None:
    """Catches omission of jsonschema stubs from the hash-locked dev environment."""
    _assert_mypy_clean("src/xqatexp/artifacts/schemas.py")


def test_daily_advice_module_typechecks_with_locked_mypy() -> None:
    """Catches optional managed value escaping its explicit guard."""
    _assert_mypy_clean("src/xqatexp/daily/advice.py")
