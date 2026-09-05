from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_python_module_exposes_help() -> None:
    """Catches a missing or broken ``python -m xqatexp`` entry point."""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(PROJECT_ROOT / "src")

    completed = subprocess.run(
        [sys.executable, "-m", "xqatexp", "--help"],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "self-check" in completed.stdout
    assert "data" in completed.stdout
    assert "factor" in completed.stdout
    assert "backtest" in completed.stdout
    assert "daily" in completed.stdout
    assert "result" in completed.stdout


def test_unwired_business_command_never_reports_success() -> None:
    """Catches a CLI shell that falsely returns success before a use case runs."""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(PROJECT_ROOT / "src")

    completed = subprocess.run(
        [sys.executable, "-m", "xqatexp", "data", "fetch"],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 10
    assert "not implemented" in completed.stderr.lower()
