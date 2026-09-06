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
        [sys.executable, "-m", "xqatexp", "data", "build"],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 10
    assert "not implemented" in completed.stderr.lower()


def test_capabilities_without_token_fails_without_creating_output(tmp_path: Path) -> None:
    """Catches a fake capability success when no live credential is available."""
    output = tmp_path / "provider_capabilities.json"
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(PROJECT_ROOT / "src")
    environment.pop("TUSHARE_TOKEN", None)

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "xqatexp",
            "data",
            "capabilities",
            "--output",
            str(output),
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 3
    assert "SECURITY_SECRET_MISSING" in completed.stderr
    assert not output.exists()
