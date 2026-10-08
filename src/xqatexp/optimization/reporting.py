from __future__ import annotations

import csv
import io
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path

from xqatexp.artifacts.manifest import canonical_json_bytes

from .models import StudySpec, TrialResult
from .storage import atomic_write, write_json


def write_report(
    output: Path,
    spec: StudySpec,
    results: list[TrialResult],
    total: int,
    baseline: TrialResult | None = None,
    *,
    notes: Sequence[str] = (),
    artifact_paths: Mapping[str, str] | None = None,
) -> TrialResult | None:
    eligible = sorted((item for item in results if item.status == "succeeded"), key=spec.rank_key)
    complete = len(results) == total and all(item.status != "failed" for item in results)
    metric_names = sorted({key for item in results for key in item.metrics})
    parameter_names = sorted({key for item in results for key in item.parameters})
    columns = ["id", "status", "reasons", *parameter_names, *metric_names]
    if artifact_paths is not None:
        columns.append("artifact_path")

    def levels_text(item: TrialResult) -> str:
        levels = item.parameters.get("take_profit_levels", "")
        if isinstance(levels, (list, tuple)):
            return "[" + ", ".join(str(value) for value in levels) + "]"
        return str(levels)

    def csv_bytes(rows: list[TrialResult]) -> bytes:
        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(buffer, fieldnames=columns)
        writer.writeheader()
        for item in rows:
            parameters = {
                key: canonical_json_bytes(value).decode().strip()
                if isinstance(value, (list, tuple))
                else value
                for key, value in item.parameters.items()
            }
            writer.writerow(
                {
                    "id": item.id,
                    "status": item.status,
                    "reasons": "; ".join(item.reasons),
                    **parameters,
                    **item.metrics,
                    **({"artifact_path": artifact_paths[item.id]} if artifact_paths else {}),
                }
            )
        return buffer.getvalue().encode("utf-8")

    atomic_write(output / "results.csv", csv_bytes(sorted(results, key=lambda item: item.id)))
    atomic_write(output / "leaderboard.csv", csv_bytes(eligible))
    lines = [
        "# 参数优化结果",
        "",
        f"- 请求区间: {spec.start_date} 至 {spec.end_date}",
        f"- 已记录组合: {len(results)} / {total}",
        f"- 合格组合: {len(eligible)}",
        f"- 失败组合: {sum(item.status == 'failed' for item in results)}",
        f"- 目标: {spec.direction} {spec.metric}",
        "- 约束: "
        + "; ".join(f"{item.metric} {item.operator} {item.value}" for item in spec.constraints),
        "",
        *notes,
        "",
        "完整网格已完成。"
        if complete
        else "搜索未覆盖全部成功结果; 仅报告已完成组合中的最佳结果。",
        "",
        "## 原配置对照",
        "",
    ]
    if baseline is not None:
        lines.extend(
            [
                f"- 实际区间: {baseline.metrics.get('date_start')} 至 "
                f"{baseline.metrics.get('date_end')}",
                f"- 夏普率: {baseline.metrics.get('sharpe')}",
                f"- 年化收益: {baseline.metrics.get('annualized_return')}",
                f"- 最大回撤: {baseline.metrics.get('max_drawdown')}",
                f"- 成交次数: {baseline.metrics.get('trade_count')}",
                "",
            ]
        )
    else:
        lines.extend(["未提供对照。", ""])
    if eligible:
        best = eligible[0]
        write_json(
            output / "best.json",
            {
                **asdict(best),
                "complete_search": complete,
                **({"artifact_path": artifact_paths[best.id]} if artifact_paths else {}),
            },
        )
        if artifact_paths:
            lines.extend([f"最佳组合原始回测目录: `{artifact_paths[best.id]}`", ""])
        target_label = "夏普率" if spec.metric == "sharpe" else f"目标值 ({spec.metric})"
        lines.extend(
            [
                "## 前十名",
                "",
                f"| 排名 | MA 天数 | 窗口天数 | 止盈档位 | {target_label} | "
                "年化收益 | 最大回撤 | 成交次数 |",
                "|---:|---:|---:|---|---:|---:|---:|---:|",
            ]
        )
        for rank, item in enumerate(eligible[:10], 1):
            p, m = item.parameters, item.metrics
            lines.append(
                f"| {rank} | {p.get('entry_confirmation_ma_days', '')} | "
                f"{p.get('entry_confirmation_window_days', '')} | "
                f"{levels_text(item)} | {m.get(spec.metric)} | "
                f"{m.get('annualized_return')} | {m.get('max_drawdown')} | {m.get('trade_count')} |"
            )
        pairs: dict[tuple[object, object], TrialResult] = {}
        for item in eligible:
            pair = (
                item.parameters.get("entry_confirmation_ma_days"),
                item.parameters.get("entry_confirmation_window_days"),
            )
            pairs.setdefault(pair, item)
        if all(left is not None and right is not None for left, right in pairs):
            lines.extend(
                [
                    "",
                    "## 各均线与窗口的最佳合格结果",
                    "",
                    "| MA 天数 | 窗口天数 | 最佳止盈档位 | 目标值 |",
                    "|---:|---:|---|---:|",
                ]
            )
            for pair, item in sorted(pairs.items(), key=lambda value: str(value[0])):
                lines.append(
                    f"| {pair[0]} | {pair[1]} | {levels_text(item)} | "
                    f"{item.metrics.get(spec.metric)} |"
                )
    else:
        best = None
        for name in ("best.json", "best-config.toml"):
            (output / name).unlink(missing_ok=True)
        lines.extend(["没有满足全部约束的组合; 未放宽条件。", ""])
    lines.extend(
        [
            "",
            "收益和回撤为小数比例。年化按 252 个交易日, 夏普率沿用回测的无风险利率 0。",
            "结果仅用于设定训练区间内的参数比较, 不代表训练区间外表现。",
            "",
        ]
    )
    atomic_write(output / "report.md", "\n".join(lines).encode("utf-8"))
    return best
