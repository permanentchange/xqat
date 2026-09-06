from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from xqatexp import __version__
from xqatexp.artifacts.manifest import canonical_json_bytes
from xqatexp.artifacts.publisher import ArtifactPublisher, PublishedArtifact
from xqatexp.artifacts.schemas import SchemaRegistry
from xqatexp.domain.enums import OverwritePolicy


class FailureDiagnosticPublisher:
    """Publish an explicit failure without creating any success-shaped output."""

    def __init__(self) -> None:
        self._publisher = ArtifactPublisher()
        self._schemas = SchemaRegistry()

    def publish(
        self,
        *,
        output: Path,
        run_id: str,
        mode: str,
        failed_stage: str,
        error: Exception,
        input_references: Sequence[Mapping[str, object]],
        generated_at: datetime,
    ) -> PublishedArtifact:
        code = str(error).split(":", 1)[0] or "INTERNAL_ERROR"
        issue = {
            "code": code,
            "severity": "ERROR",
            "stage": failed_stage,
            "scope": "RUN",
            "message": str(error),
        }
        suggested = [self._suggested_action(code)]
        value: dict[str, Any] = {
            "schema_version": "1.0",
            "run_id": run_id,
            "mode": mode,
            "failed_stage": failed_stage,
            "primary_issue": issue,
            "issues": [issue],
            "input_references": [dict(item) for item in input_references],
            "suggested_actions": suggested,
            "generated_at": generated_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        }
        self._schemas.validate_json("failure_diagnostic", value)
        failure_bytes = canonical_json_bytes(value)
        report_bytes = (
            "# XQatExp 失败诊断\n\n"
            f"- 错误代码: `{code}`\n"
            f"- 失败阶段: `{failed_stage}`\n"
            f"- 影响范围: 本次 {mode} 运行未生成成功结果。\n"
            f"- 建议动作: {suggested[0]}\n"
        ).encode()

        def build(staging: Path) -> None:
            payloads = {"failure.json": failure_bytes, "report.md": report_bytes}
            for name, payload in payloads.items():
                (staging / name).write_bytes(payload)
            files = [
                {
                    "path": name,
                    "media_type": "application/json" if name.endswith(".json") else "text/markdown",
                    "size_bytes": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "row_count": None,
                    "schema_id": "failure_diagnostic" if name == "failure.json" else None,
                    "schema_version": "1.0" if name == "failure.json" else None,
                }
                for name, payload in sorted(payloads.items())
            ]
            manifest = {
                "schema_version": "1.0",
                "artifact_type": "FAILURE_DIAGNOSTIC",
                "artifact_id": run_id,
                "created_at": generated_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                "producer": {"name": "xqatexp", "version": __version__},
                "run": {"mode": mode, "strategy_id": "weekly_market_guard_rank_v1"},
                "inputs": [dict(item) for item in input_references],
                "date_scope": {"start": None, "end": None},
                "files": files,
                "issues": [issue],
                "limitations": [],
            }
            self._schemas.validate_json("artifact_manifest", manifest)
            (staging / "manifest.json").write_bytes(canonical_json_bytes(manifest))

        return self._publisher.publish(build, output, OverwritePolicy.ERROR)

    @staticmethod
    def _suggested_action(code: str) -> str:
        if code.startswith("DATA_") or code.startswith("ARTIFACT_"):
            return "检查显式输入 Artifact 的完整性、覆盖范围和路径后重试。"
        if code.startswith("FACTOR_"):
            return "修正自定义因子 Schema、日期或覆盖范围后重试。"
        if code.startswith("CONFIG_"):
            return "修正配置文件或命令行参数后重试。"
        return "根据错误代码检查输入与运行条件后重试。"
