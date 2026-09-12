from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_wheel_is_platform_neutral_and_contains_top_level_schemas(tmp_path: Path) -> None:
    """Catches schemas routed through a platform install-data path in the wheel."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--no-isolation",
            "--outdir",
            str(tmp_path),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    wheels = tuple(tmp_path.glob("*.whl"))
    assert len(wheels) == 1
    wheel = wheels[0]
    assert wheel.name.endswith("-py3-none-any.whl")
    with zipfile.ZipFile(wheel) as archive:
        assert any(name.startswith("schemas/") for name in archive.namelist())
