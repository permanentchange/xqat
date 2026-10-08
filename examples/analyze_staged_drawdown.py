"""Run the fixed offline staged-drawdown research protocol without parameter search."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import pyarrow.parquet as pq

from xqatexp.application.workflows import StrategyWorkflowService
from xqatexp.artifacts.manifest import canonical_json_bytes
from xqatexp.artifacts.readers import ArtifactReader
from xqatexp.config import resolve_config
from xqatexp.domain.enums import OverwritePolicy, RunMode
from xqatexp.strategy.strategies.staged_drawdown_v1.parameters import (
    normalize_staged_drawdown_parameters,
)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)


def write_csv(path: Path, rows: Sequence[Mapping[str, object]], columns: Sequence[str]) -> None:
    with path.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def trials() -> list[dict[str, object]]:
    result: list[dict[str, object]] = [
        {
            "id": "reference_100_bps10",
            "group": "reference",
            "confirmation": "none",
            "allow_add": False,
            "capital_cap": Decimal("1"),
            "slippage_bps": 10,
        }
    ]
    for bps in (10, 20, 30):
        for group, confirmation, allow_add in (
            ("A", "none", False),
            ("B", "ma_rebound", False),
            ("C", "none", True),
            ("D", "ma_rebound", True),
        ):
            result.append(
                {
                    "id": f"{group}_bps{bps}",
                    "group": group,
                    "confirmation": confirmation,
                    "allow_add": allow_add,
                    "capital_cap": Decimal("0.30"),
                    "slippage_bps": bps,
                }
            )
    return result


def event_study(
    research: Path,
    diagnostics: Sequence[Mapping[str, Any]],
    security_id: str,
    start: date,
    end: date,
) -> tuple[list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]]:
    """Evaluate future outcomes separately from the bounded decision diagnostics."""
    calendar = pq.read_table(research / "tables/trade_calendar.parquet").to_pylist()
    days = sorted({row["calendar_date"] for row in calendar if row["is_open"]})
    index = {day: i for i, day in enumerate(days)}
    prices = {
        row["trade_date"]: row
        for row in pq.read_table(research / "tables/market_daily.parquet").to_pylist()
        if row["security_id"] == security_id
    }
    signal_indices = sorted(
        {
            index[date.fromisoformat(str(item["decision_date"]))]
            for item in diagnostics
            if item["diagnostics"]["values"]["slow_decline"]
            and start <= date.fromisoformat(str(item["decision_date"])) <= end
        }
    )
    groups: list[list[int]] = []
    for i in signal_indices:
        if not groups or i - groups[-1][-1] > 10:
            groups.append([])
        groups[-1].append(i)
    signals = [
        {"group": number, "signal_date": days[i]}
        for number, group in enumerate(groups, 1)
        for i in group
    ]
    summaries: list[dict[str, object]] = []
    outcomes: list[dict[str, object]] = []

    def price(i: int, field: str, as_of: date | None = None) -> Decimal | None:
        if i < 0 or i >= len(days) or days[i] > end:
            return None
        row = prices.get(days[i])
        if row is None or row["available_from"] > (as_of or days[i]):
            return None
        value = row.get(field)
        return value if isinstance(value, Decimal) and value.is_finite() and value > 0 else None

    for number, group in enumerate(groups, 1):
        confirmed: int | None = None
        for i in range(group[0], min(group[-1] + 11, len(days))):
            if days[i] > end:
                break
            seen = [s for s in group if s <= i]
            if not seen or i - seen[-1] > 10:
                continue
            closes = [price(j, "research_close", days[i]) for j in range(i - 4, i + 1)]
            previous = price(i - 1, "research_close", days[i])
            if all(p is not None for p in closes) and previous is not None:
                current = closes[-1]
                assert current is not None
                ma = sum(p for p in closes if p is not None) / 5
                if current > previous and current > ma:
                    confirmed = i
                    break
        direct_entry = group[0] + 1
        confirmed_entry = None if confirmed is None else confirmed + 1
        direct_price = price(direct_entry, "research_open")
        confirmed_price = (
            None if confirmed_entry is None else price(confirmed_entry, "research_open")
        )
        summaries.append(
            {
                "group": number,
                "first_signal_date": days[group[0]],
                "last_signal_date": days[group[-1]],
                "signal_days": len(group),
                "confirmation_date": None if confirmed is None else days[confirmed],
                "delay_trade_days": None if confirmed is None else confirmed - group[0],
                "direct_entry_price": direct_price,
                "confirmed_entry_price": confirmed_price,
                "entry_price_difference": None
                if direct_price is None or confirmed_price is None
                else confirmed_price / direct_price - 1,
            }
        )
        for mode, entry in (("direct", direct_entry), ("confirmed", confirmed_entry)):
            for horizon in (20, 60, 120):
                opening = None if entry is None else price(entry, "research_open")
                last = None if entry is None else entry + horizon - 1
                closes = (
                    []
                    if entry is None or last is None
                    else [
                        price(j, "research_close") for j in range(entry, min(last + 1, len(days)))
                    ]
                )
                complete = (
                    entry is not None
                    and last is not None
                    and last < len(days)
                    and days[entry] <= end
                    and days[last] <= end
                    and opening is not None
                    and len(closes) == horizon
                    and all(p is not None for p in closes)
                )
                valid = [p for p in closes if p is not None]
                outcomes.append(
                    {
                        "group": number,
                        "mode": mode,
                        "horizon_trade_days": horizon,
                        "entry_date": None if entry is None or entry >= len(days) else days[entry],
                        "end_date": None if last is None or last >= len(days) else days[last],
                        "status": "complete"
                        if complete
                        else "no_confirmation"
                        if entry is None
                        else "incomplete",
                        "return": valid[-1] / opening - 1
                        if complete and opening is not None
                        else None,
                        "max_adverse_return": min(Decimal("0"), min(valid) / opening - 1)
                        if complete and opening is not None
                        else None,
                    }
                )
    return signals, summaries, outcomes


def holding_cycles(
    trial_id: str,
    daily: Sequence[Mapping[str, Any]],
    trades: Sequence[Mapping[str, Any]],
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    begin: int | None = None
    for i, row in enumerate(daily):
        if begin is None and row["gross_exposure"] > 0:
            begin = i
        closed = begin is not None and row["gross_exposure"] == 0
        if begin is not None and (closed or i == len(daily) - 1):
            first = daily[begin]["valuation_date"]
            last = row["valuation_date"]
            previous_nav = daily[max(0, begin - 1)]["nav"]
            cycle_trades = [t for t in trades if first <= t["execution_date"] <= last]
            buys = sum(
                (t["gross_amount"] for t in cycle_trades if t["side"] == "BUY"), Decimal("0")
            )
            result.append(
                {
                    "trial": trial_id,
                    "cycle": len(result) + 1,
                    "entry_date": first,
                    "end_date": last,
                    "closed": closed,
                    "holding_trade_days": i - begin + (0 if closed else 1),
                    "nav_pnl": row["nav"] - previous_nav,
                    "cumulative_buy_notional": buys,
                    "ending_market_value": row["etf_market_value"],
                }
            )
            begin = None
    return result


def summarize(
    root: Path,
    trial: Mapping[str, Any],
) -> tuple[dict[str, object], list[dict[str, object]], list[dict[str, object]]]:
    ArtifactReader().open(root)
    metrics = read_json(root / "metrics.json")
    daily = pq.read_table(root / "portfolio_daily.parquet").to_pylist()
    trades = pq.read_table(root / "trades.parquet").to_pylist()
    decisions = read_json(root / "strategy_diagnostics.json")["decisions"]
    cycles = holding_cycles(str(trial["id"]), daily, trades)
    trace = []
    waiting, longest = 0, 0
    for decision in decisions:
        values = decision["diagnostics"]["values"]
        after_sell = values["position_quantity"] > 0 and values["last_trade_side"] == "SELL"
        waiting = waiting + 1 if after_sell else 0
        longest = max(longest, waiting)
        trace.append(
            {
                "trial": trial["id"],
                "decision_date": decision["decision_date"],
                **{
                    key: values[key]
                    for key in (
                        "signal",
                        "position_quantity",
                        "last_trade_side",
                        "last_buy_price",
                        "add_decline_reached",
                        "add_blocked_after_sell",
                        "add_budget_remaining",
                        "add_budget_exhausted",
                        "cumulative_buy_notional",
                    )
                },
                "after_sell_holding": after_sell,
            }
        )
    state = read_json(root / "strategy_state.json")
    summary = {
        "trial": trial["id"],
        "group": trial["group"],
        "confirmation": trial["confirmation"],
        "allow_add_after_sell": trial["allow_add"],
        "capital_cap": trial["capital_cap"],
        "slippage_bps": trial["slippage_bps"],
        **{
            key: metrics[key]
            for key in (
                "cumulative_return",
                "annualized_return",
                "sharpe",
                "max_drawdown",
                "calmar",
                "max_drawdown_peak_date",
                "max_drawdown_trough_date",
                "max_drawdown_recovery_date",
                "one_way_turnover",
                "total_commission",
                "total_slippage_cost",
            )
        },
        "mean_exposure": sum((r["gross_exposure"] for r in daily), Decimal("0")) / len(daily),
        "max_exposure": max(r["gross_exposure"] for r in daily),
        "max_cycle_buy_notional": max(
            (c["cumulative_buy_notional"] for c in cycles), default=Decimal("0")
        ),
        "trade_count": len(trades),
        "completed_cycles": sum(bool(c["closed"]) for c in cycles),
        "longest_after_sell_wait_trade_days": longest,
        "blocked_add_days": sum(bool(r["add_blocked_after_sell"]) for r in trace),
        "ending_quantity": sum(p["quantity"] for p in state["positions"]),
        "ending_market_value": daily[-1]["etf_market_value"],
    }
    return summary, cycles, trace


def comparison_report(
    rows: Sequence[Mapping[str, Any]],
    groups: Sequence[Mapping[str, Any]],
    cycles: Sequence[Mapping[str, Any]],
    years: Sequence[Mapping[str, Any]],
) -> str:
    def percent(value: Any) -> str:
        return "不可计算" if value is None or value == "" else f"{float(value) * 100:.2f}%"

    def ratio(value: Any) -> str:
        return "不可计算" if value is None or value == "" else f"{float(value):.3f}"

    lines = [
        "# 分级下跌策略研究",
        "",
        "## 主实验",
        "",
        "A/C 不启用入场确认, B/D 启用 MA5 上涨确认; C/D 允许止盈后按最后买入价再跌10%加仓。",
        "四组均使用30%持仓周期累计投入上限, 每次买入10%, 卖出不返还额度。",
        "",
        "| 实验 | 累计收益 | 年化收益 | 夏普 | 最大回撤 | Calmar | 平均仓位 | 成交数 | 期末数量 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        if row["slippage_bps"] == 10:
            lines.append(
                f"| {row['trial']} | {percent(row['cumulative_return'])} "
                f"| {percent(row['annualized_return'])} | {ratio(row['sharpe'])} "
                f"| {percent(row['max_drawdown'])} | {ratio(row['calmar'])} "
                f"| {percent(row['mean_exposure'])} "
                f"| {row['trade_count']} | {row['ending_quantity']} |"
            )
    lines.extend(
        [
            "",
            "## 机制对照与成本压力",
            "",
            "差值为描述性对照, 不是统计显著性检验; 原100%基线单独列示。",
            "",
            "| 滑点 bps | 对照 | 累计收益差 | 夏普差 | 最大回撤差 |",
            "|---:|---|---:|---:|---:|",
        ]
    )
    for bps in (10, 20, 30):
        matched = {
            r["group"]: r for r in rows if r["slippage_bps"] == bps and r["group"] != "reference"
        }
        for left, right in (("C", "A"), ("D", "B"), ("B", "A"), ("D", "C")):
            a, b = matched[left], matched[right]
            sharpe = (
                None if a["sharpe"] is None or b["sharpe"] is None else a["sharpe"] - b["sharpe"]
            )
            lines.append(
                f"| {bps} | {left}-{right} "
                f"| {percent(a['cumulative_return'] - b['cumulative_return'])} "
                f"| {ratio(sharpe)} | {percent(a['max_drawdown'] - b['max_drawdown'])} |"
            )
    lines.extend(
        [
            "",
            "## 信号事件",
            "",
            "相邻信号间隔不超过10个交易日归为同组; 每组仅以首个信号和首次有效确认为代表。",
            "事件收益独立计算, 不加仓、不止盈、不含交易费用。详见 event_outcomes.csv。",
            "",
            "| 事件组 | 首次信号 | 末次信号 | 信号日数 | 首次确认 | 延迟交易日 | 买入价差 |",
            "|---|---|---|---:|---|---:|---:|",
        ]
    )
    for group in groups:
        lines.append(
            f"| {group['group']} | {group['first_signal_date']} | {group['last_signal_date']} "
            f"| {group['signal_days']} | {group['confirmation_date']} "
            f"| {group['delay_trade_days']} "
            f"| {percent(group['entry_price_difference'])} |"
        )
    lines.extend(
        [
            "",
            "## 逐年对照",
            "",
            "主实验使用连续账户, 年度切分保留跨年持仓; 不把各年视为独立试验。",
            "",
            "| 实验 | 年份 | 收益 | 最大回撤 | 夏普 |",
            "|---|---|---:|---:|---:|",
        ]
    )
    for row in years:
        if row["trial"] in {"A_bps10", "B_bps10", "C_bps10", "D_bps10"}:
            lines.append(
                f"| {row['trial']} | {row['period_label']} | {percent(row['cumulative_return'])} "
                f"| {percent(row['max_drawdown'])} | {ratio(row['sharpe'])} |"
            )
    lines.extend(
        [
            "",
            "## 逐周期对照",
            "",
            "未平仓周期收益包含期末估值; 持有日数按有仓位的交易日计。",
            "",
            "| 实验 | 周期 | 买入日 | 结束日 | 已平仓 | 持有日数 | 净值损益 | 累计买入金额 |",
            "|---|---:|---|---|---|---:|---:|---:|",
        ]
    )
    for row in cycles:
        if row["trial"] in {"A_bps10", "B_bps10", "C_bps10", "D_bps10"}:
            lines.append(
                f"| {row['trial']} | {row['cycle']} | {row['entry_date']} | {row['end_date']} "
                f"| {row['closed']} | {row['holding_trade_days']} | {row['nav_pnl']:.2f} "
                f"| {row['cumulative_buy_notional']:.2f} |"
            )
    lines.extend(
        [
            "",
            "## 研究限制",
            "",
            "当前结果仅描述给定样本, 不选择最佳参数或自动启用新策略。",
            "原始数据可能缺少ETF分红; 现金收益和年化无风险利率为0。事件组很少, 未来窗口可能重叠。",
            "当前历史已反复查看, 属于开发样本; 这些对照不构成样本外证据。",
            "事件结果只使用截至研究结束日的完整窗口, 缺失结果不缩短周期或填补价格。",
            "",
        ]
    )
    return "\n".join(lines)


def run_study(config: Path, start: date, end: date, output: Path) -> Path:
    base = resolve_config(
        {"output": output, "start_date": start, "end_date": end},
        config,
        run_id=str(uuid4()),
        generated_at=datetime.now(UTC),
    )
    if base.strategy_id != "staged_drawdown_v1" or base.mode is not RunMode.BACKTEST:
        raise ValueError("CONFIG_VALUE_INVALID: staged_drawdown_v1 BACKTEST config required")
    ArtifactReader().open(base.research_artifact_path)
    output = base.output_path
    output.mkdir(parents=True, exist_ok=False)
    config_bytes = config.read_bytes()
    (output / "input-config.toml").write_bytes(config_bytes)
    protocol = {
        "schema_version": "1.0",
        "status": "running",
        "start_date": start,
        "end_date": end,
        "research_path": str(base.research_artifact_path),
        "research_manifest_sha256": base.research_artifact_sha256,
        "config_sha256": hashlib.sha256(config_bytes).hexdigest(),
        "trials": trials(),
        "completed_trials": [],
        "event_group_gap_trade_days": 10,
        "event_horizons_trade_days": [20, 60, 120],
        "limitations": [
            "DEVELOPMENT_SAMPLE",
            "FEW_EVENT_GROUPS",
            "CASH_RETURN_ZERO",
            "ETF_DIVIDEND_COVERAGE_NOT_VERIFIED",
            "EVENT_RETURNS_BEFORE_COSTS",
        ],
    }
    manifest = output / "study_manifest.json"
    manifest.write_bytes(canonical_json_bytes(protocol))
    summaries, cycles, traces, years = [], [], [], []
    try:
        for trial in trials():
            parameters = normalize_staged_drawdown_parameters(
                {
                    **{
                        k: v
                        for k, v in base.parameters.items()
                        if k not in {"take_profit_threshold", "sell_fraction"}
                    },
                    "entry_confirmation_mode": trial["confirmation"],
                    "entry_confirmation_ma_days": 5,
                    "entry_confirmation_window_days": 10,
                    "allow_add_after_sell": trial["allow_add"],
                    "max_capital_fraction": trial["capital_cap"],
                    "buy_fraction": Decimal("0.10"),
                    "take_profit_mode": "tiered",
                    "take_profit_levels": (Decimal("0.1"), Decimal("0.2"), Decimal("0.3")),
                    "take_profit_sell_fractions": (Decimal("0.3"), Decimal("0.3"), Decimal("0.4")),
                },
                {},
            )
            context = replace(
                base,
                run_id=str(uuid4()),
                parameters=parameters,
                output_path=output / str(trial["id"]),
                execution_assumptions={
                    **dict(base.execution_assumptions),
                    "slippage_bps": Decimal(str(trial["slippage_bps"])),
                },
            )
            root = StrategyWorkflowService().backtest(context, OverwritePolicy.ERROR)
            summary, trial_cycles, trace = summarize(root, trial)
            summaries.append(summary)
            cycles.extend(trial_cycles)
            traces.extend(trace)
            with (root / "period_metrics.csv").open(encoding="utf-8", newline="") as stream:
                years.extend(
                    {"trial": trial["id"], **row}
                    for row in csv.DictReader(stream)
                    if row["period_type"] == "CALENDAR_YEAR"
                )
            protocol["completed_trials"].append(str(trial["id"]))
            manifest.write_bytes(canonical_json_bytes(protocol))
            print(f"TRIAL_WRITTEN trial={trial['id']} sharpe={summary['sharpe']}", flush=True)
        diagnostics = read_json(output / "A_bps10/strategy_diagnostics.json")["decisions"]
        signals, groups, outcomes = event_study(
            base.research_artifact_path,
            diagnostics,
            str(base.parameters["security_id"]),
            start,
            end,
        )
        write_csv(output / "signals.csv", signals, ("group", "signal_date"))
        write_csv(
            output / "event_groups.csv",
            groups,
            (
                "group",
                "first_signal_date",
                "last_signal_date",
                "signal_days",
                "confirmation_date",
                "delay_trade_days",
                "direct_entry_price",
                "confirmed_entry_price",
                "entry_price_difference",
            ),
        )
        write_csv(
            output / "event_outcomes.csv",
            outcomes,
            (
                "group",
                "mode",
                "horizon_trade_days",
                "entry_date",
                "end_date",
                "status",
                "return",
                "max_adverse_return",
            ),
        )
        write_csv(output / "comparison.csv", summaries, tuple(summaries[0]))
        write_csv(
            output / "cycles.csv",
            cycles,
            (
                "trial",
                "cycle",
                "entry_date",
                "end_date",
                "closed",
                "holding_trade_days",
                "nav_pnl",
                "cumulative_buy_notional",
                "ending_market_value",
            ),
        )
        write_csv(output / "decision_trace.csv", traces, tuple(traces[0]))
        write_csv(output / "annual_comparison.csv", years, tuple(years[0]))
        (output / "comparison.json").write_bytes(
            canonical_json_bytes(
                {"trials": summaries, "event_groups": groups, "event_outcomes": outcomes}
            )
        )
        (output / "report.md").write_text(
            comparison_report(summaries, groups, cycles, years), encoding="utf-8"
        )
        protocol["status"] = "complete"
        protocol["signal_count"] = len(signals)
        protocol["event_group_count"] = len(groups)
        protocol["files"] = {
            str(path.relative_to(output)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(output.rglob("*"))
            if path.is_file() and path != manifest
        }
    except (Exception, KeyboardInterrupt) as error:
        protocol["status"] = "failed"
        protocol["failure"] = {"type": type(error).__name__, "message": str(error)}
        raise
    finally:
        manifest.write_bytes(canonical_json_bytes(protocol))
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("examples/config-staged-etf.toml"))
    parser.add_argument("--start-date", type=date.fromisoformat, required=True)
    parser.add_argument("--end-date", type=date.fromisoformat, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = run_study(args.config, args.start_date, args.end_date, args.output)
    print(f"STUDY_WRITTEN output={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
