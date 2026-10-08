# Chinese report punctuation is intentional.
# ruff: noqa: RUF001
from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
import pyarrow as pa

from .config import Settings
from .features import FEATURES, PAIRS, array


def write_csv(path: Path, rows: list[dict]) -> None:
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
                    for k, v in row.items()
                }
            )


def plot_reports(path: Path, features: pa.Table, conditional: dict, s: Settings) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    dates = features["date"].to_pylist()
    valid = np.array([s.start <= d <= s.end for d in dates])
    fig, axes = plt.subplots(4, 4, figsize=(14, 10))
    for ax, name in zip(axes.ravel(), FEATURES, strict=False):
        values = array(features, name)[valid]
        values = values[np.isfinite(values)]
        ax.hist(values, bins=40)
        ax.set_title(name)
    for ax in axes.ravel()[len(FEATURES) :]:
        ax.set_visible(False)
    fig.tight_layout()
    fig.savefig(path / "feature_distributions.png", dpi=150)
    plt.close(fig)
    matrix = np.array(
        [
            r["spearman"] if r["spearman"] is not None else np.nan
            for r in conditional["feature_correlations"]
        ]
    ).reshape(14, 14)
    fig, ax = plt.subplots(figsize=(9, 8))
    im = ax.imshow(matrix, vmin=-1, vmax=1, cmap="RdBu_r")
    ax.set_xticks(range(14), FEATURES, rotation=45)
    ax.set_yticks(range(14), FEATURES)
    fig.colorbar(im, ax=ax)
    fig.tight_layout()
    fig.savefig(path / "feature_correlations.png", dpi=150)
    plt.close(fig)
    for h in s.analysis["joint_horizons"]:
        fig, axes = plt.subplots(1, 5, figsize=(16, 4))
        for ax, pair in zip(axes, PAIRS, strict=True):
            rows = [
                r
                for r in conditional["joint"]
                if r["window"] == "development" and r["pair"] == pair and r["horizon"] == h
            ]
            grid = np.array([r["mean"] if r["mean"] is not None else np.nan for r in rows]).reshape(
                3, 3
            )
            limit = (
                max(0.001, float(np.nanmax(np.abs(grid)))) if np.any(np.isfinite(grid)) else 0.001
            )
            image = ax.imshow(grid, cmap="RdBu_r", vmin=-limit, vmax=limit)
            for i, row in enumerate(rows):
                value = "missing" if row["mean"] is None else f"{row['mean']:.2%}"
                ax.text(
                    i % 3, i // 3, f"{value}\nn={row['n']}", ha="center", va="center", fontsize=8
                )
            ax.set_title(f"{pair}: {PAIRS[pair][0]} / {PAIRS[pair][1]}, H={h}")
            ax.set_xticks(range(3), ("Low", "Mid", "High"))
            ax.set_yticks(range(3), ("Low", "Mid", "High"))
            ax.set_xlabel("Volume state")
            ax.set_ylabel("Price/risk state")
            fig.colorbar(image, ax=ax, shrink=0.7)
        fig.tight_layout()
        fig.savefig(path / f"joint_H{h}.png", dpi=150)
        plt.close(fig)


def markdown(quality: dict, statistics: list[dict], candidates: list[dict], s: Settings) -> str:
    volume = [r for r in candidates if r["kind"] == "joint"]
    lines = [
        "# ETF 量价研究报告（P0）",
        "",
        f"标的：{s.security_id}；开发区间：{s.start} 至 {s.end}。",
        f"保留区间起点：{s.holdout}；未加载其 OHLCV 用于研究。"
        "此前已接触该段历史，属于回顾性保留集。",
        "",
        "## 数据与收益口径",
        "",
        f"开发区间行情 {quality['development_rows']} 行；输入哈希及基本数据检查通过。",
        f"声明范围：{quality['declared_scope']}；实际日期范围：{quality['observed_scope']}。",
        "未来收益为复权开盘价格收益代理，不包含正式撮合、费用、现金利息和经事件核验的实际分红收益。",
        "成交量使用份额单位；未把分红复权因子用于调整成交量。拆分口径仍需事件核验。",
        f"检查提示：{', '.join(quality['warnings']) or '无'}。",
        "",
        "## 研究方法",
        "",
        "14 个特征；分组使用截至昨日的历史有效观测，边界不会使用未来全样本数据。",
        "所有描述窗口的标签均要求退出日期位于该窗口内；缺失和退化分组未填零。",
        f"预登记检验共 {len(statistics)} 项，全部纳入 BY-FDR，包括无法估计的检验（p=1）。",
        "HAC 保留原始交易日间隔，滞后 H−1；区块 Bootstrap、年度与非重叠事件复核提供补充证据。",
        "控制检验为组内 OLS 均值差，控制连续价格特征和 R1；控制结果属于条件关联证据，不证明因果。",
        "",
        "## 候选假设",
        "",
        f"通过研究门槛的价格/成交量单特征假设 {len(candidates) - len(volume)} 项；"
        f"同时通过原始与控制检验的成交量增量假设 {len(volume)} 项。",
        "这些是后续验证候选，不是已确认的可交易优势；尚未完成成本、策略与独立样本外验证。",
        "",
        "| 假设 | 期限 | 效应 | BY p | 有效样本 | 非重叠样本 | 年度同向比例 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in candidates:
        lines.append(
            f"| {r['id']} | {r['horizon']} | {r['effect']:.4%} | {r['p_by']:.4g} | "
            f"{r['n']} | {r['nonoverlap_n']} | {r['year_direction_fraction']:.1%} |"
        )
    if not volume:
        lines += ["", "当前未发现通过预设门槛的可靠成交量增量证据；不自动扩大参数或假设搜索。"]
    lines += [
        "",
        "## 结果文件",
        "",
        "- `data/quality.json`：简要数据检查及限制。",
        "- `features/features.parquet`、`labels/labels.parquet`：开发期特征与有界标签。",
        "- `conditional/`：分组收益、年度/五年滚动分析、Spearman 和分布变化。",
        "- `statistics/tests.json` / `tests.csv`：全部检验、置信区间及未通过原因。",
        "- `statistics/candidates.json`：候选假设清单。",
        "- `report/`：分布、相关与量价条件收益图。",
        "",
        "## 下一阶段",
        "",
        "仅根据保留规律定义规则及纯价格消融，补充分红、现金收益和同 ETF 买入持有对照。",
        "固定训练选择方法后复用网格优化，再实现账户连续的滚动验证；最后打开保留集。",
        "",
        "方法参考：[多重尝试与回测过拟合](https://www.davidhbailey.com/dhbpapers/overfitting.pdf)。",
        "",
    ]
    return "\n".join(lines)
