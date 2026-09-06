from __future__ import annotations

from collections.abc import Mapping


def daily_target_markdown(value: Mapping[str, object]) -> str:
    positions = value["positions"]
    assert isinstance(positions, list)
    lines = [
        "# 每日策略目标",
        "",
        f"- 决策日: {value['decision_date']}",
        f"- 生效日: {value['effective_from']}",
        f"- 市场状态: {value['market_regime']}",
        f"- 理论组合回撤: {value['theoretical_drawdown']}",
        f"- 现金权重: {value['cash_weight']}",
        "",
        "## 目标持仓",
        "",
        "| 证券 | 资产类型 | 目标权重 | 排名 | 变化 |",
        "|---|---|---:|---:|---|",
    ]
    for position in positions:
        assert isinstance(position, dict)
        lines.append(
            "| {security_id} | {asset_type} | {target_weight} | {rank} | {transition} |".format(
                **position
            )
        )
    lines.extend(
        [
            "",
            "## 说明",
            "",
            "本报告仅格式化同目录结构化结果, 不重新计算目标或指标。",
            "",
        ]
    )
    return "\n".join(lines)


def daily_advice_markdown(target: Mapping[str, object], advice: Mapping[str, object]) -> str:
    items = advice["items"]
    assert isinstance(items, list)
    lines = [
        "# 每日参考建议",
        "",
        f"- 决策日: {advice['decision_date']}",
        f"- 生效日: {advice['effective_from']}",
        f"- 市场状态: {target['market_regime']}",
        "",
        "## 参考建议",
        "",
        "| 证券 | 动作 | 当前数量 | 目标权重 | 建议数量 | 未解决数量 |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for item in items:
        assert isinstance(item, dict)
        lines.append(
            "| {security_id} | {action} | {current_quantity} | {target_weight} | "
            "{suggested_quantity} | {unresolved_quantity} |".format(**item)
        )
    lines.extend(
        [
            "",
            "## 非订单声明",
            "",
            str(advice["non_order_disclaimer"]),
            "",
            "报告只展示同目录结构化事实, 未重新计算建议数量。",
            "",
        ]
    )
    return "\n".join(lines)


def backtest_markdown(metrics: Mapping[str, object], trade_count: int) -> str:
    return "\n".join(
        [
            "# 回测结果",
            "",
            f"- 区间: {metrics['date_start']} 至 {metrics['date_end']}",
            f"- 初始净值: {metrics['initial_nav']}",
            f"- 最终净值: {metrics['final_nav']}",
            f"- 累计收益: {metrics['cumulative_return']}",
            f"- 沪深300基准累计收益: {metrics['benchmark_cumulative_return']}",
            f"- 相对基准收益差: {metrics['excess_return']}",
            f"- 最大回撤: {metrics['max_drawdown']}",
            f"- 成交记录数: {trade_count}",
            "",
            "本报告只格式化 metrics.json 与结构化交易事实, 不重新运行策略。",
            "",
        ]
    )
