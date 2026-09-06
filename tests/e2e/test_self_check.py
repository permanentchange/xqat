from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_offline_self_check_succeeds_without_token_or_network() -> None:
    """Catches self-check coupling to secrets or external availability."""
    environment = os.environ.copy()
    environment.pop("TUSHARE_TOKEN", None)
    completed = subprocess.run(
        [sys.executable, "-m", "xqatexp", "self-check", "--offline"],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "SELF_CHECK_OK" in completed.stdout
    assert "TUSHARE_TOKEN" not in completed.stdout + completed.stderr
