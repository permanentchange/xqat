from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from xqatexp.research.custom_factors import CsvCustomFactorView, CustomFactorError


def _write(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8", newline="")
    return path


def test_custom_factor_exact_date_values_are_stable_and_scoped(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "factor.csv",
        "factor_name,security_id,factor_date,factor_value\n"
        "quality_score,600001.SH,2026-09-04,0.2\n"
        "quality_score,600000.SH,2026-09-04,0.8\n",
    )
    view = CsvCustomFactorView.load(
        path,
        decision_date=date(2026, 9, 4),
        known_security_ids={"600000.SH", "600001.SH"},
        required_factor_ids={"quality_score"},
    )
    assert view.values_at(date(2026, 9, 4), ("quality_score",), ("600001.SH", "600000.SH")) == {
        "quality_score": {"600000.SH": 0.8, "600001.SH": 0.2}
    }


@pytest.mark.parametrize(
    "body,code",
    [
        (
            "factor_name,security_id,factor_date,factor_value\n"
            "quality_score,600000.SH,2026-09-04,1\n"
            "quality_score,600000.SH,2026-09-04,2\n",
            "FACTOR_DUPLICATE_KEY",
        ),
        (
            "factor_name,security_id,factor_date,factor_value\n"
            "quality_score,600000.SH,2026-09-04,nan\n",
            "FACTOR_VALUE_NONFINITE",
        ),
        (
            "factor_name,security_id,factor_date,factor_value,extra\n"
            "quality_score,600000.SH,2026-09-04,1,no\n",
            "FACTOR_SCHEMA_INVALID",
        ),
    ],
)
def test_custom_factor_rejects_invalid_input(tmp_path: Path, body: str, code: str) -> None:
    with pytest.raises(CustomFactorError, match=code):
        CsvCustomFactorView.load(
            _write(tmp_path / "factor.csv", body),
            decision_date=date(2026, 9, 4),
            known_security_ids={"600000.SH"},
            required_factor_ids={"quality_score"},
        )
