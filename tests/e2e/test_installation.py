from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_runtime_lock_is_hashed_and_wheel_includes_schemas() -> None:
    lock = (ROOT / "requirements.lock").read_text(encoding="utf-8")
    requirement_starts = [
        line
        for line in lock.splitlines()
        if line and not line[0].isspace() and not line.startswith(("#", "--"))
    ]
    assert requirement_starts
    assert all("==" in line and line.endswith("\\") for line in requirement_starts)
    assert len(re.findall(r"--hash=sha256:[0-9a-f]{64}", lock)) >= len(requirement_starts)
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["tool"]["setuptools"]["package-data"]["schemas"] == ["*.json"]
    assert pyproject["build-system"]["requires"] == [
        "setuptools==84.0.0",
        "wheel==0.48.0",
    ]
    build_lock = (ROOT / "requirements-build.lock").read_text(encoding="utf-8")
    assert "setuptools==84.0.0" in build_lock
    assert "wheel==0.48.0" in build_lock
    assert len(re.findall(r"--hash=sha256:[0-9a-f]{64}", build_lock)) == 6


def test_readme_documents_every_public_command_and_secret_boundary() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for command in (
        "self-check",
        "data capabilities",
        "data fetch",
        "data fetch-batch",
        "data collection index",
        "data check-raw",
        "data build",
        "data update",
        "data check-research",
        "factor check",
        "backtest run",
        "daily target",
        "daily decide",
        "daily advise",
        "state init",
        "state apply-fill",
        "state apply-stock-adjustment",
        "result show",
    ):
        assert command in readme
    assert "TUSHARE_TOKEN" in readme
    assert "<在本机填写你的 Token>" in readme
    assert "requirements-build.lock" in readme


def test_readme_is_linux_first_and_keeps_windows_powershell() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert readme.index("Linux") < readme.index("Windows PowerShell")
    assert "conda create -n xqat python=3.12 -y" in readme
    assert readme.count("conda activate xqat") >= 2
    assert "python -m pip install --require-hashes -r requirements.lock" in readme
    assert "python -m pip install --require-hashes -r requirements-dev.lock" in readme
    assert "xqatexp self-check --offline" in readme
    assert "Linux 与 Windows 应分别创建自己的 Conda 环境" in readme
    assert "不共享同一个环境目录" in readme
    assert "Linux 是主 CI 平台" in readme
    assert "Windows 是兼容性 CI 平台" in readme
