# weekly_market_guard_rank_v1

## 1. 策略身份

- Strategy ID: `weekly_market_guard_rank_v1`
- Strategy Version: `1.0.0`
- 决策类型: `AllocationDecision -> TargetPortfolio`
- 决策频率: 每周最后一个交易日收盘后
- 生效时间: 下一交易日
- Strategy State: 不需要 confirmed execution state
- 主要实现:
  - `parameters.py`: 参数默认值、标准化与校验
  - `declaration.py`: 数据需求声明
  - `strategy.py`: 股票池、持仓滞回、资产预算与目标组合
  - `scoring.py`: 横截面评分
  - `market_regime.py`: STRONG / NEUTRAL / WEAK 市场状态
  - `drawdown.py`: 理论组合回撤 overlay

该策略是周频的 A 股多因子 allocation 策略。它先用沪深300 ETF 判断市场状态，再从满足资格条件的 A 股中做横截面评分和持仓滞回，最后决定股票、ETF 与现金的目标权重。

策略本身只产生目标组合。Backtest 中的实际成交、费用、滑点、T+1、现金约束和 NEXT_OPEN 撮合由共享执行层处理。

## 2. 决策流程

### 2.1 数据窗口

策略声明 `lookback_trade_days = 320`。

实际生成决策时：

1. 至少需要 313 个交易日，否则报 `STRATEGY_WARMUP_INSUFFICIENT`。
2. 使用最近 252 个交易日作为正式重放窗口。
3. 在这 252 个交易日中取每周最后一个交易日作为内部周决策点。
4. 当前外部 decision date 本身也必须是周内最后一个交易日，否则拒绝生成目标。

### 2.2 市场状态

使用配置的 `csi300_etf_id` 的以下系统因子：

- `etf_ma20_v1`
- `etf_ma60_v1`
- `etf_slope60_v1`
- `etf_drawdown60_v1`
- `etf_volatility20_v1`

当前判定规则：

**WEAK**，满足任一条件：

- close < MA60 且 MA20 < MA60 且 60 日斜率 <= 0；
- 60 日回撤 <= -12%；
- 20 日年化波动率 >= 40% 且 close < MA20。

**STRONG**，同时满足：

- close > MA20 > MA60；
- 60 日斜率 > 0；
- 60 日回撤 > -8%；
- 20 日年化波动率 < 35%。

其他情况为 **NEUTRAL**。

### 2.3 A 股资格过滤

候选证券必须：

- 是 A_SHARE；
- 已上市；
- 非 ST；
- 非全日停牌；
- 上市交易日数不少于 `min_listing_trade_days`；
- 市值百分位 >= `min_size_percentile`；
- 20 日成交额百分位 >= `min_amount_percentile`；
- TTM 净利润为正；
- 不满足连续两个季度亏损；
- 10 个股票 system factor 都存在；
- 不包含 `DELISTING_PERIOD`、`TERMINATION_CONFIRMED`、`DATA_CONFLICT`、`ST_COVERAGE_MISSING` 风险标记。

### 2.4 横截面评分

基础评分包含 6 个组件：

| 评分组件 | System factor | 默认权重 | 方向 |
| --- | --- | ---: | --- |
| momentum_60_ex5 | `momentum_60_ex5_v1` | 35% | 越高越好 |
| momentum_40 | `momentum_40_v1` | 20% | 越高越好 |
| trend_stability_60 | `trend_stability_60_v1` | 15% | 越高越好 |
| volume_price_confirm_20 | `volume_price_confirm_20_v1` | 10% | 越高越好 |
| low_volatility_20 | `volatility_20_v1` | 10% | 越低越好 |
| profitability | `roe_annualized_v1` | 10% | 越高越好 |

每个组件先做 Type-7 winsorize，再做横截面 average-rank percentile，然后按权重合成。

启用 custom factor 时：

- 基础 6 因子的总权重缩放为 `1 - custom_factor_weight`；
- custom factor 占 `custom_factor_weight`；
- `LOWER_BETTER` 会反转 custom percentile；
- 当前实现中其他 direction 字符串按 higher-better 处理。

### 2.5 持仓滞回

排名后通过 `select_holdings` 做持仓滞回：

- 新证券只有 rank <= `entry_rank` 才能进入；
- 已持有证券在 `min_holding_weeks` 以内优先保留；
- 超过最短持有期但未达到最长持有期时，只要 rank <= `exit_rank` 可继续持有；
- 达到或超过 `max_holding_weeks` 后，需要重新进入 `entry_rank` 才保留。

目标股票数量：

- STRONG: `strong_stock_count`
- NEUTRAL: `neutral_stock_count`
- WEAK: 0

### 2.6 基础资产预算

| Market regime | 股票预算 | 沪深300 ETF | 现金 |
| --- | ---: | ---: | ---: |
| STRONG | 75% | 15% | 剩余 |
| NEUTRAL | 40% | 30% | 剩余 |
| WEAK | 0% | 10% | 90% |

单只股票目标权重为：

```text
min(股票预算 / 实际选中数量, single_stock_max_weight)
```

未分配部分自动保留为现金。

### 2.7 理论组合回撤 Overlay

策略在最近 252 日内部重放理论组合，并使用最近最多 60 个日度 NAV 观察计算回撤。

默认状态：

- 回撤 <= -8%: 从 NONE 进入 CAUTION；
- 回撤 <= -12%: 进入 DEFENSIVE；
- 回撤恢复到 >= -5%，且市场不是 WEAK，连续满足 `recovery_weeks` 个周决策后回到 NONE。

CAUTION：

- 股票总权重最多 40%；
- ETF 权重最多 20%；
- 剩余为现金。

DEFENSIVE：

- 股票 0%；
- ETF 10%；
- 现金 90%。

## 3. 参数

| 参数 | 默认值 | 当前作用 |
| --- | ---: | --- |
| `csi300_etf_id` | 无，必填 | 用于市场状态、ETF 因子和目标 ETF |
| `entry_rank` | 20 | 新进入和最长持有后重新入选的排名阈值 |
| `exit_rank` | 40 | 已有持仓的退出滞回阈值；必须大于 entry_rank |
| `min_holding_weeks` | 2 | 最短持有周数 |
| `max_holding_weeks` | 8 | 最长滞回周数；不得小于 min_holding_weeks |
| `min_listing_trade_days` | 252 | 最低上市交易日数 |
| `min_size_percentile` | 0.20 | 最低总市值横截面百分位 |
| `min_amount_percentile` | 0.20 | 最低 20 日成交额横截面百分位 |
| `strong_stock_count` | 20 | STRONG 状态目标股票数 |
| `neutral_stock_count` | 10 | NEUTRAL 状态目标股票数 |
| `single_stock_min_weight` | 0.03 | 当前仅用于校验其不高于 max；不参与实际权重分配 |
| `single_stock_max_weight` | 0.05 | 单股实际权重上限 |
| `caution_drawdown` | -0.08 | 进入 CAUTION 的理论组合回撤 |
| `defensive_drawdown` | -0.12 | 进入 DEFENSIVE 的理论组合回撤 |
| `recovery_drawdown` | -0.05 | Overlay 恢复阈值 |
| `recovery_weeks` | 2 | 恢复所需连续周决策次数 |
| `custom_factor_name` | null | 可选 custom factor 名称 |
| `custom_factor_weight` | 0 | custom factor 权重；启用时必须在 (0, 0.20] |
| `custom_factor_direction` | HIGHER_BETTER | `LOWER_BETTER` 时反转 custom factor 排名 |
| `custom_factor_missing_policy` | EXACT | 当前参数保留；当前 declaration 的 custom requirement 固定使用 EXACT |
| `score_weights` | 见上表 | 六个基础评分权重；必须非负且合计为 1 |

## 4. 数据要求

StrategyDeclaration 当前要求：

- 至少 313 个交易日；
- 252 日 A 股 market status，周末采样覆盖率 >= 98%；
- 252 日 point-in-time financial，周末采样覆盖率 >= 90%；
- 10 个 A 股 system factor，覆盖率 >= 98%；
- 5 个指定 ETF system factor，覆盖率 100%；
- 若启用 custom factor，252 日周末采样覆盖率 >= 98%。

Research Artifact 必须在运行前通过 `data check-research`。策略不会直接读取 Raw Artifact 或访问 Tushare。

## 5. 配置示例

仓库提供 `examples/config-offline.toml`。核心配置：

```toml
schema_version = "1.0"
mode = "BACKTEST"
strategy_id = "weekly_market_guard_rank_v1"
strategy_version = "1.0.0"
research_artifact = ".example-work/research"

[strategy]
csi300_etf_id = "510300.SH"

[execution]
initial_cash = 1000000
price_model = "NEXT_OPEN"
slippage_bps = 10
max_volume_participation = 0.10
fee_schedule_id = "cn_cash_market_default_v1"
dividend_tax_model = "PROVIDER_AFTER_TAX"
```

未写出的 strategy 参数使用本 README 第 3 节默认值。

## 6. 怎么运行

### 6.1 离线示例数据

先生成仓库自带的离线 Research Artifact：

```bash
python examples/generate_offline_example.py --root .example-work
xqatexp data check-research   --input .example-work/research   --report .example-work/research-check.json
```

### 6.2 Backtest

```bash
xqatexp backtest run   --config examples/config-offline.toml   --start-date 2026-08-31   --end-date 2026-09-07   --output .example-work/weekly-backtest
```

查看：

```bash
xqatexp result show   --input .example-work/weekly-backtest   --format markdown
```

### 6.3 Daily Target

decision date 必须是该周最后一个交易日：

```bash
xqatexp daily target   --config examples/config-offline.toml   --decision-date 2026-09-04   --output .example-work/weekly-target
```

若要提供上一期目标以生成 transition：

```bash
xqatexp daily target   --config examples/config-offline.toml   --decision-date 2026-09-04   --previous-target /path/to/previous-target   --output .example-work/weekly-target
```

### 6.4 Custom Factor

CSV 固定列：

```text
factor_name,security_id,factor_date,factor_value
```

先检查：

```bash
xqatexp factor check   --file /path/to/custom-factor.csv   --research /path/to/research   --strategy weekly_market_guard_rank_v1   --start 2025-01-01   --end 2026-01-01   --report .local/custom-factor-check.json
```

然后在配置中设置 `custom_factor_name` 和非零 `custom_factor_weight`，运行命令时增加：

```bash
--custom-factor /path/to/custom-factor.csv
```

## 7. 使用真实 Tushare 数据回测

weekly 策略和单证券策略不同：它需要完整 A 股横截面、财务快照、市场状态以及沪深300 ETF 因子。因此真实回测的数据准备量明显更大。

下面以：

```text
沪深300 ETF: 510300.SH
正式回测:    2025-01-01 ~ 2026-09-01
```

为例。

### 7.1 Warmup 范围

weekly 策略要求至少 313 个交易日，并在每个决策日内部重放最近 252 个交易日。

因此，如果正式回测从 2025-01-01 开始，**只下载 2024-01-01 之后的数据不够**。

推荐：

```text
市场/行情 Raw: 2023-08-01 ~ 2026-09-10
正式回测:      2025-01-01 ~ 2026-09-01
```

其中 2023-08 至 2024 年主要用于 warmup；2026-09-01 之后多保留几个交易日，用于最后决策日解析下一交易日。

如果坚持只准备 2024-01-01 之后的数据，就必须把正式回测起点推迟到 Research Artifact 已累计至少 313 个交易日之后。

### 7.2 检查 Tushare 权限

```bash
conda activate xqat
export TUSHARE_TOKEN="<在本机填写你的 Token>"
test -n "$TUSHARE_TOKEN" && echo "TUSHARE_TOKEN is set"

mkdir -p .local
xqatexp data capabilities --output .local/tushare-capabilities.json
```

weekly 全市场流程涉及的接口较多，尤其是 `stock_st_status`、财务和 ETF 数据。开始大批量下载前，应先确认当前 Tushare 账户具有相应权限。

### 7.3 创建目录

```bash
mkdir -p data/raw/weekly-real/market
mkdir -p data/raw/weekly-real/financial
mkdir -p data/raw/weekly-real/dividend
mkdir -p data/research
mkdir -p .local/raw-checks
```

### 7.4 下载全市场基础和行情数据

weekly 策略当前需要以下全市场事实：

- `stock_basic`: A 股证券主数据；
- `trade_calendar`: 交易日历；
- `stock_daily`: A 股行情；
- `stock_adj_factor`: A 股复权因子；
- `stock_daily_basic`: 总市值，用于 `total_mv_pct_v1`；
- `stock_suspend`: 全日停牌状态；
- `stock_price_limit`: 涨跌停价格和执行约束；
- `stock_st_status`: ST 状态；
- `fund_basic`: ETF 主数据；
- `fund_daily`: `510300.SH` ETF 行情；
- `fund_adj_factor`: ETF 复权因子；
- `index_daily`: `000300.SH` 回测基准。

执行：

```bash
xqatexp data fetch \
  --dataset stock_basic \
  --start 2023-08-01 \
  --end 2026-09-10 \
  --output data/raw/weekly-real/market/stock-basic

xqatexp data fetch \
  --dataset trade_calendar \
  --start 2023-08-01 \
  --end 2026-09-10 \
  --output data/raw/weekly-real/market/trade-calendar

xqatexp data fetch \
  --dataset stock_daily \
  --start 2023-08-01 \
  --end 2026-09-10 \
  --output data/raw/weekly-real/market/stock-daily

xqatexp data fetch \
  --dataset stock_adj_factor \
  --start 2023-08-01 \
  --end 2026-09-10 \
  --output data/raw/weekly-real/market/stock-adj-factor

xqatexp data fetch \
  --dataset stock_daily_basic \
  --start 2023-08-01 \
  --end 2026-09-10 \
  --output data/raw/weekly-real/market/stock-daily-basic

xqatexp data fetch \
  --dataset stock_suspend \
  --start 2023-08-01 \
  --end 2026-09-10 \
  --output data/raw/weekly-real/market/stock-suspend

xqatexp data fetch \
  --dataset stock_price_limit \
  --start 2023-08-01 \
  --end 2026-09-10 \
  --output data/raw/weekly-real/market/stock-price-limit

xqatexp data fetch \
  --dataset stock_st_status \
  --start 2023-08-01 \
  --end 2026-09-10 \
  --output data/raw/weekly-real/market/stock-st-status

xqatexp data fetch \
  --dataset fund_basic \
  --start 2023-08-01 \
  --end 2026-09-10 \
  --output data/raw/weekly-real/market/fund-basic

xqatexp data fetch \
  --dataset fund_daily \
  --security-id 510300.SH \
  --start 2023-08-01 \
  --end 2026-09-10 \
  --output data/raw/weekly-real/market/fund-daily-510300

xqatexp data fetch \
  --dataset fund_adj_factor \
  --security-id 510300.SH \
  --start 2023-08-01 \
  --end 2026-09-10 \
  --output data/raw/weekly-real/market/fund-adj-factor-510300

xqatexp data fetch \
  --dataset index_daily \
  --start 2023-08-01 \
  --end 2026-09-10 \
  --output data/raw/weekly-real/market/index-daily
```

不传 `--security-id` 的股票日频数据会按当前 CLI 的全市场查询方式抓取并分页，因此数据量会显著大于单证券 staged 回测。

### 7.5 生成需要财务数据的证券列表

当前 CLI 对 `income`、`fina_indicator` 和 `dividend` 要求显式 `--security-id`，所以全市场财务需要逐证券抓取。

先从 `stock_basic` Raw Artifact 中提取与研究区间有交集的证券：

```bash
python - <<'PY'
import gzip
import json
from datetime import date
from pathlib import Path

path = Path("data/raw/weekly-real/market/stock-basic/response.jsonl.gz")
start = date.fromisoformat("2023-08-01")
end = date.fromisoformat("2026-09-10")

codes = set()
with gzip.open(path, "rt", encoding="utf-8") as f:
    for line in f:
        row = json.loads(line)
        listed = date(
            int(row["list_date"][:4]),
            int(row["list_date"][4:6]),
            int(row["list_date"][6:8]),
        )
        delist_raw = row.get("delist_date")
        delisted = None
        if delist_raw:
            delisted = date(
                int(delist_raw[:4]),
                int(delist_raw[4:6]),
                int(delist_raw[6:8]),
            )
        if listed <= end and (delisted is None or delisted >= start):
            codes.add(row["ts_code"])

out = Path(".local/weekly-security-ids.txt")
out.write_text("\n".join(sorted(codes)) + "\n", encoding="utf-8")
print("security_count =", len(codes))
print("written =", out)
PY
```

### 7.6 逐证券下载财务数据

为了生成：

- `roe_annualized_v1`
- `profit_positive_ttm_v1`
- `consecutive_loss_2_v1`

需要 `income` 和 `fina_indicator`。

为保证 2024 年及之后的 TTM 推导有上一年度和同期数据，财务查询建议从 2022-01-01 开始：

```bash
while IFS= read -r code; do
  mkdir -p "data/raw/weekly-real/financial/$code"

  xqatexp data fetch \
    --dataset income \
    --security-id "$code" \
    --start 2022-01-01 \
    --end 2026-09-10 \
    --output "data/raw/weekly-real/financial/$code/income"

  xqatexp data fetch \
    --dataset fina_indicator \
    --security-id "$code" \
    --start 2022-01-01 \
    --end 2026-09-10 \
    --output "data/raw/weekly-real/financial/$code/fina-indicator"
done < .local/weekly-security-ids.txt
```

这是一个大批量真实数据任务。具体能否全部完成取决于 Tushare 权限、积分、接口限频和网络条件；失败的证券不要静默跳过，因为 weekly readiness 对财务和 factor coverage 有明确要求。

### 7.7 可选但推荐：下载公司行为

公司行为不是 weekly StrategyDeclaration 的入场 readiness 条件，但对实际 Backtest NAV、现金分红和送转股处理很重要。

为了更接近真实持仓收益，建议同时抓：

```bash
while IFS= read -r code; do
  mkdir -p "data/raw/weekly-real/dividend/$code"

  xqatexp data fetch \
    --dataset dividend \
    --security-id "$code" \
    --start 2023-08-01 \
    --end 2026-09-10 \
    --output "data/raw/weekly-real/dividend/$code/dividend"
done < .local/weekly-security-ids.txt
```

如果省略这一步，策略仍可能通过 readiness，但回测账户不会拥有缺失的现金分红/送转股事实，因此不能把结果解释为完整公司行为口径的真实回测。

### 7.8 校验所有 Raw Artifact

可以递归检查已经下载的 Artifact：

```bash
find data/raw/weekly-real -name manifest.json -print0 |
while IFS= read -r -d '' manifest; do
  dir="${manifest%/manifest.json}"
  key="$(printf '%s' "$dir" | tr '/.' '__')"
  xqatexp data check-raw \
    --input "$dir" \
    --report ".local/raw-checks/${key}.json"
done
```

任何 `RAW_CHECK valid=false` 都应先处理，再构建 Research Artifact。

### 7.9 创建 Research Build 配置

```bash
cat > .local/weekly-research-build.toml <<'EOF'
schema_version = "1.0"
start_date = "2023-08-01"
end_date = "2026-09-10"

[strategy]
csi300_etf_id = "510300.SH"
EOF
```

### 7.10 构建 Research Artifact

`data build` 当前要求把每一个 Raw Artifact 作为一个 `--raw-root` 显式传入。

Linux Bash 可以动态组装参数：

```bash
RAW_ARGS=()

while IFS= read -r -d '' manifest; do
  RAW_ARGS+=(--raw-root "${manifest%/manifest.json}")
done < <(find data/raw/weekly-real -name manifest.json -print0)

xqatexp data build \
  "${RAW_ARGS[@]}" \
  --config .local/weekly-research-build.toml \
  --output data/research/weekly-real-20230801-20260910
```

然后校验：

```bash
xqatexp data check-research \
  --input data/research/weekly-real-20230801-20260910 \
  --report .local/weekly-research-check.json
```

必须先看到：

```text
RESEARCH_CHECK valid=true
```

再进入策略回测。

如果操作系统报 `Argument list too long`，说明 Raw Artifact 数量已经超过当前 CLI 的单次参数承载范围；当前版本没有递归 `--raw-root` 目录发现功能，这是现有真实全市场数据工作流的工程限制。

### 7.11 创建真实 Backtest 配置

```bash
cat > .local/weekly-real-backtest.toml <<'EOF'
schema_version = "1.0"
mode = "BACKTEST"

strategy_id = "weekly_market_guard_rank_v1"
strategy_version = "1.0.0"

research_artifact = "data/research/weekly-real-20230801-20260910"

start_date = "2025-01-01"
end_date = "2026-09-01"

output = ".local/weekly-real-backtest-placeholder"

[strategy]
csi300_etf_id = "510300.SH"

[execution]
initial_cash = 1000000
price_model = "NEXT_OPEN"
slippage_bps = 10
max_volume_participation = 0.10
fee_schedule_id = "cn_cash_market_default_v1"
dividend_tax_model = "PROVIDER_AFTER_TAX"
EOF
```

未列出的 weekly strategy 参数使用第 3 节默认值。

### 7.12 运行真实回测

```bash
xqatexp backtest run \
  --config .local/weekly-real-backtest.toml \
  --start-date 2025-01-01 \
  --end-date 2026-09-01 \
  --output .local/weekly-real-backtest-20250101-20260901
```

查看：

```bash
xqatexp result show \
  --input .local/weekly-real-backtest-20250101-20260901 \
  --format markdown
```

### 7.13 重点检查结果

真实 weekly 回测建议重点查看：

- `strategy_diagnostics.json`: 每周 market regime、theoretical drawdown、overlay、股票 rank/score；
- `target_history.parquet`: 每次周决策的目标组合；
- `trades.parquet`: NEXT_OPEN 模拟成交；
- `unfilled.parquet`: 停牌、涨跌停、成交量或现金导致的未成交；
- `portfolio_daily.parquet`: 每日 NAV 和资产暴露；
- `metrics.json`: 收益、回撤、benchmark、费用等。

如果回测在 readiness 阶段失败，应先检查财务、market status 和 system factor coverage，而不是直接调整策略参数。

## 8. 主要输出与诊断

Backtest 主要关注：

- `target_history.parquet`
- `portfolio_daily.parquet`
- `trades.parquet`
- `unfilled.parquet`
- `metrics.json`
- `strategy_diagnostics.json`
- `report.md`

`strategy_diagnostics.json` 中记录当前周的：

- `market_regime`
- `theoretical_drawdown`
- `drawdown_overlay_level`
- 每个最终股票的 rank、score、holding_age_weeks

Daily Target 输出 TargetPortfolio，不代表真实订单；实际数量规划和成交由下游服务完成。

## 9. 当前边界

- 该策略不是 stateful execution strategy，不读取真实成交 state。
- 持有周数来自 252 日内部理论重放，不是从券商成交历史恢复。
- `single_stock_min_weight` 当前没有进入实际 allocation 算法。
- `custom_factor_missing_policy` 当前没有改变 declaration；启用 custom factor 时实际 readiness 仍为 EXACT。
- Backtest 成交使用共享执行层的 NEXT_OPEN 模型，不模拟集合竞价订单簿。
