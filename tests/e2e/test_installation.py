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
    assert pyproject["tool"]["setuptools"]["data-files"]["schemas"] == ["schemas/*.json"]


def test_readme_documents_every_public_command_and_secret_boundary() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for command in (
        "self-check",
        "data capabilities",
        "data fetch",
        "data check-raw",
        "data build",
        "data check-research",
        "factor check",
        "backtest run",
        "daily target",
        "daily advise",
        "result show",
    ):
        assert command in readme
    assert "TUSHARE_TOKEN" in readme
    assert "<在本机填写你的 Token>" in readme
