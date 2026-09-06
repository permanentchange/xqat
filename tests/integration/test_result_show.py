from __future__ import annotations

from tests.contract.test_result_artifacts import _context
from tests.unit.portfolio.test_validation import target
from xqatexp.cli import main
from xqatexp.domain.enums import OverwritePolicy
from xqatexp.reporting.publisher import ResultArtifactPublisher


def test_result_show_uses_published_facts_without_recalculation(tmp_path, capsys) -> None:
    context = _context(tmp_path)
    ResultArtifactPublisher().publish_daily_target(context, target(), (), (), OverwritePolicy.ERROR)
    assert main(["result", "show", "--input", str(context.output_path)]) == 0
    output = capsys.readouterr().out
    assert "DAILY_TARGET_RESULT" in output
    assert "600000.SH" in output
