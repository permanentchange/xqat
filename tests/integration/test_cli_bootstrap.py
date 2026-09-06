from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from xqatexp.artifacts.readers import ArtifactReader
from xqatexp.cli import main

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


def test_factor_check_requires_explicit_inputs() -> None:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(PROJECT_ROOT / "src")

    completed = subprocess.run(
        [sys.executable, "-m", "xqatexp", "factor", "check"],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 2
    assert "required" in completed.stderr.lower()


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


def test_explicit_failure_report_is_a_valid_standalone_artifact(tmp_path: Path) -> None:
    report = tmp_path / "failure"
    result = main(
        [
            "backtest",
            "run",
            "--config",
            str(tmp_path / "missing.toml"),
            "--output",
            str(tmp_path / "success-must-not-exist"),
            "--failure-report",
            str(report),
        ]
    )
    assert result == 2
    opened = ArtifactReader().open(report)
    assert opened.manifest["artifact_type"] == "FAILURE_DIAGNOSTIC"
    assert set(opened.verified_files) == {"failure.json", "report.md"}
    assert not (tmp_path / "success-must-not-exist").exists()
