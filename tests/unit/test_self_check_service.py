from __future__ import annotations

from types import SimpleNamespace

import pytest

import xqatexp.application.services as services
import xqatexp.cli as cli_module
from xqatexp.application.services import SelfCheckService
from xqatexp.cli import main


def test_self_check_service_exercises_local_installation_contract() -> None:
    result = SelfCheckService().run(offline=True)
    assert result.checks == ("python", "schemas", "temporary_write", "numeric_golden")


def test_self_check_requires_explicit_offline_mode() -> None:
    with pytest.raises(ValueError, match="requires --offline"):
        SelfCheckService().run(offline=False)


def test_self_check_rejects_wrong_python_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(services, "sys", SimpleNamespace(version_info=(3, 11)))
    with pytest.raises(RuntimeError, match=r"CPython 3\.12"):
        SelfCheckService().run(offline=True)


def test_cli_dispatches_self_check_and_reports_missing_subcommands(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["self-check", "--offline"]) == 0
    assert main(["self-check"]) == 3
    assert main(["data"]) == 2
    assert main(["factor"]) == 2
    assert main(["backtest"]) == 2
    assert main(["daily"]) == 2
    assert main(["result"]) == 2
    captured = capsys.readouterr()
    assert "SELF_CHECK_OK" in captured.out
    assert "CONFIG_COMMAND_REQUIRED" in captured.err


def test_cli_converts_unexpected_failure_and_completes_explicit_log(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    log_path = tmp_path / "events.jsonl"
    monkeypatch.setattr(
        cli_module,
        "_dispatch",
        lambda _parser, _args: (_ for _ in ()).throw(RuntimeError("secret detail")),
    )

    assert main(["--log-file", str(log_path), "self-check", "--offline"]) == 10
    captured = capsys.readouterr()
    assert "INTERNAL_ERROR correlation_id=" in captured.err
    assert "secret detail" not in captured.err
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert '"event":"COMMAND_COMPLETED"' in lines[-1]
    assert '"exit_code":10' in lines[-1]
