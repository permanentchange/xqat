# 10 绩效与阶段分析

## 1. 输入与原则

分析模块只消费已完成的结构化组合净值、持仓、交易、费用、目标和未成交记录，不重新运行 Strategy 或 Execution。日频净值在当日全部账户事件后、收盘估值时记录。

## 2. 收益序列

令 `NAV_t` 为 T 日收盘权益，MVP 不支持外部申赎现金流：

```text
daily_return_t = NAV_t / NAV_(t-1) - 1
cumulative_return = NAV_end / NAV_start - 1
valuation_points = count(NAV)
return_intervals = valuation_points - 1
annualized_return = (NAV_end / NAV_start)^(252 / return_intervals) - 1
```

不足两个估值点时收益指标失败；只有一个收益区间时累计和年化收益可计算，但波动、Sharpe 为 null。`return_intervals < 60` 时仍给累计和年化收益，同时携带短样本 WARNING。所有阶段均使用实际收益区间数量，不把估值点数量误作指数分母。

沪深300基准以 `000300.SH` 未复权指数收盘归一化为 1，使用与策略净值相同的起止交易日。`excess_return` 定义为策略累计收益减基准累计收益，不冒充可交易的主动收益组合。

## 3. 风险指标

| 指标 | 公式 |
|---|---|
| 年化波动 | 全部日收益的样本标准差（分母 `return_intervals-1`）× `sqrt(252)` |
| 运行峰值 | `peak_t = max(NAV_0...NAV_t)` |
| 日回撤 | `drawdown_t = NAV_t / peak_t - 1` |
| 最大回撤 | 全期 `min(drawdown_t)` |
| 回撤持续时间 | 从离开前高到首次恢复前高的交易日数；未恢复则截至期末 |
| Sharpe | `mean(daily_return - rf_daily) / sample_std(daily_return - rf_daily) × sqrt(252)`，其中 `rf_daily=(1+risk_free_rate)^(1/252)-1` |
| Calmar | `annualized_return / abs(max_drawdown)` |

`risk_free_rate` 是有效年化收益率，默认 0，必须大于 -1，最终值写入结果。标准差至少需要两个日收益；分母为零时指标为 null 并说明原因，不输出无穷大。Calmar 的分子仍使用同阶段年化收益，不使用 Sharpe 的算术年化均值。

## 4. 交易与现实性指标

- 交易次数、买入次数、卖出次数；
- 完全成交、部分成交、未成交次数及原因分布；
- 总佣金、过户费、印花税、总滑点成本；
- 费用占初始资产和成交额比例；
- 日单边换手：`Σ_i abs(trade_notional_t,i) / NAV_before_first_trade_t`；全期值为各交易日日单边换手之和；
- 日双边调整换手：在同一 T 日开盘参考价格上，`Σ_assets abs(weight_after_trade - weight_before_trade) / 2`，assets 包含现金；全期值为每日之和；
- 有调仓行为的周数和平均调仓间隔。

所有指标使用实际模拟成交，不使用 Target 假设成交。

## 5. 持仓、集中度与现金

- 日持仓数、平均持仓数、最大持仓数；
- 单一证券最大实际权重；
- Top-5 权重和；
- HHI：`Σ non_cash_weight_i²`；
- 平均现金权重、现金权重超过 50% 的交易日占比；
- 个股、ETF、现金/费用对日收益的算术贡献。

贡献直接从实际账户事实计算。令 `B_c/E_c` 为资产类别 c 的期初/期末市值，`Buy_c/Sell_c` 为该日该类别成交金额，`Div_c` 为当日新确认的现金分红应收，`C` 为可用现金加现金应收：

```text
asset_pnl_c = E_c - B_c - Buy_c + Sell_c + Div_c
cash_cost_pnl = C_end - C_begin + ΣBuy_c - ΣSell_c - ΣDiv_c
contribution_c = asset_pnl_c / NAV_(t-1)
cash_cost_contribution = cash_cost_pnl / NAV_(t-1)
```

在现金无利息、无外部申赎的 MVP 中，`cash_cost_pnl` 等于当日费用的负值。每日贡献之和必须在 `1e-10` 内等于 `daily_return_t`；不平衡使分析失败，不能写入残差项掩盖。

“现金持有影响”额外提供明确标注的反事实估计：

```text
cash_opportunity_cost_vs_benchmark
  = Σ(previous_day_cash_weight × benchmark_daily_return)
```

该值只是相对基准的机会成本估计，不改变正式组合收益。

## 6. 最差阶段与恢复

最差阶段由实际最大回撤区间确定：当多个谷值同为最低回撤时取最早谷值；峰值取该谷值前达到对应运行峰值的最早日期；恢复日为谷值后第一个 `NAV >= 峰值NAV` 的日期，没有则为 null。持续交易日是峰值到谷值的收益区间数，恢复交易日是谷值到恢复日的收益区间数。

滚动 N 日收益需要 N 个收益区间、N+1 个估值点，公式为 `NAV_t/NAV_(t-N)-1`；分别输出 N=20、60、120 的最小值，重复最小值取最早结束日，再取其对应起始日。

## 7. 分阶段分析

固定支持：

- 全区间；
- 按自然年度；
- 用户在运行配置中显式给出的互不要求覆盖全区间的日期段。

每个阶段包含边界日期内的估值点，使用与全区间相同公式独立计算，并同时标注 `valuation_points` 与 `return_intervals`。系统不自动识别牛熊市或根据结果反向选择“最有利阶段”。阶段重叠允许，但必须在报告中明确。

## 8. 目标与实际偏差

按每个执行日计算：

- `tracking_gap_l1 = Σ abs(target_weight - actual_weight)`，现金也作为一项；
- 由交易单位、未成交、现金不足分别解释的偏差；
- 目标持仓与实际持仓分开保存和展示。

执行限制不得改写历史 Target。

## 9. 输出

`metrics.json` 保存全期标量和公式版本；`period_metrics.csv` 保存阶段结果；日收益、回撤和贡献进入 `portfolio_daily.parquet`。报告只读取这些结构化文件。

## 10. 测试要点

- 用手算 5 日净值验证累计收益、波动、回撤和恢复。
- 不规则日历只使用实际开放日，年化系数固定 252。
- 无交易、全现金、零波动、未恢复回撤和短样本分别测试 null/Warning。
- 修改 Target 而不修改实际成交时，正式收益不变但目标偏差变化。
- 全区间交易费用等于交易明细费用逐笔求和。
- 两个估值点、三个估值点以及阶段切片必须验证年化指数使用收益区间数。
- 每日资产贡献之和必须逐日在 `1e-10` 内等于组合收益；删除一笔费用或分红事件时测试失败。
