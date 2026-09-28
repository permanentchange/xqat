# 01 领域模型与策略详细设计

## 1. 共享领域合同

核心不可变合同定义在 `src/xqatexp/domain/contracts.py`。

### 1.1 ResolvedRunContext

ResolvedRunContext 是一次策略运行的已解析上下文，包含 run id、RunMode、strategy id/version、标准化参数、Research Artifact 路径和 Manifest hash、custom factor 输入、输出路径、execution assumptions、决策/回测日期、AccountSnapshot、previous target、analysis periods 和 strategy state 路径。

### 1.2 StrategyDeclaration 与 DataRequirement

StrategyDeclaration 描述策略所需数据，而不是由 Engine 猜测依赖。DataRequirement 包含 dataset、fields、lookback、security scope、required、missing policy、minimum coverage、sampling frequency 和 failure code。

`StateRequirement.NONE` 表示纯 allocation 策略；`CONFIRMED_EXECUTION_STATE` 表示策略必须读取已确认执行状态。

### 1.3 TargetPortfolio

TargetPortfolio 是策略无关的 allocation 合同，仅包含 strategy identity、decision/effective date、positions、transition records、cash weight 和 explanations。TargetPosition 只保留通用字段：security、asset type、target weight、transition、explanation codes 和可选 planning priority。

周频策略专属的 market regime、rank、score、holding age、drawdown 不进入 TargetPortfolio，而进入 StrategyDiagnostics。

### 1.4 TradeIntentDecision

TradeIntentDecision 包含 decision identity、strategy identity、decision/effective date、TradeIntent 列表和可选 diagnostics。

Intent sizing 当前支持：

- `FixedNotional`
- `InitialCapitalFraction`
- `CurrentPositionFraction`
- `FullPosition`

## 2. Strategy Registry

`src/xqatexp/strategy/registry.py` 是策略解析的单一入口。每个 StrategySpec 绑定：

- strategy id/version；
- declaration factory；
- strategy factory；
- parameter normalizer；
- decision schedule。

当前只有两个内置策略。

## 3. weekly_market_guard_rank_v1

### 3.1 调度与数据

版本 `1.0.0`，周频，使用每周最后一个交易日收盘决策。声明 lookback 320 个交易日，正式重放使用最近 252 日，最低 warmup 313 个交易日。

主要 requirement：

- trading days；
- A 股 market status；
- financial snapshot；
- 10 个股票 system factors；
- 5 个沪深300ETF system factors；
- 可选 custom factor。

### 3.2 主要股票因子

股票系统因子包括市值百分位、20 日成交额百分位、60 日剔除最近 5 日动量、40 日动量、60 日趋势稳定度、20 日量价确认、20 日波动率、年化 ROE、TTM 盈利正值和连续亏损标志。

默认评分权重：

| 因子 | 权重 |
| --- | ---: |
| momentum_60_ex5 | 0.35 |
| momentum_40 | 0.20 |
| trend_stability_60 | 0.15 |
| volume_price_confirm_20 | 0.10 |
| low_volatility_20 | 0.10 |
| profitability | 0.10 |

Custom factor 可选，启用时权重必须在 (0, 0.20]。

### 3.3 股票池与持有滞回

默认 entry rank 20、exit rank 40、最短持有 2 周、最长持有 8 周、最低上市 252 个交易日；同时排除 ST、全日停牌、退市风险、数据冲突和关键因子缺失证券。

持仓使用 rank hysteresis：新进入要求达到 entry rank；已有持仓在最短持有期内优先保留，之后可在 exit rank 内保留，超过最长持有期后需重新达到 entry rank。

### 3.4 市场状态与资产预算

MarketRegime 为 STRONG / NEUTRAL / WEAK。

基础预算：

| Regime | 股票预算 | ETF | 其余现金 |
| --- | ---: | ---: | ---: |
| STRONG | 75% | 15% | 剩余 |
| NEUTRAL | 40% | 30% | 剩余 |
| WEAK | 0% | 10% | 90% |

单股默认最大权重 5%。滚动理论组合回撤通过 DrawdownOverlay 进一步降风险：默认 -8% 进入 CAUTION，-12% 进入 DEFENSIVE，回撤恢复到 -5% 并持续指定周数后恢复。DEFENSIVE 固定为 0% 股票、10% ETF、90% 现金。

## 4. staged_drawdown_v1

### 4.1 调度与依赖

版本 `1.0.0`，日频，每个交易日收盘决策。只要求指定证券的 `research_close` 和 `close_raw` 以及足够交易日，不依赖财务数据或 system factors。

策略声明 `CONFIRMED_EXECUTION_STATE`，因此 Backtest 自动创建 reducer，Daily 必须显式提供 state。

### 4.2 默认参数

| 参数 | 默认值 |
| --- | ---: |
| lookback_trade_days | 20 |
| cumulative_decline_threshold | 10% |
| single_day_crash_threshold | 5% |
| minimum_down_days | 12 |
| add_buy_decline_threshold | 10% |
| buy_fraction | 初始资金 10% |
| max_capital_fraction | 初始资金 100% |
| take_profit_threshold | 10% |
| sell_fraction | 当前持仓 20% |

`security_id` 必须是 `000000.SH` 或 `000000.SZ` 形式。

### 4.3 信号顺序

1. 若已有持仓且按当前 raw close 计算的整体持仓收益率达到 take-profit threshold，产生 SELL intent，大小为当前持仓比例。
2. 否则，若当前无持仓且 20 日满足缓慢下跌条件，产生首次 BUY intent。
3. 否则，若已有持仓、上一次实际交易为 BUY、当前 raw close 相比 `last_buy_price` 再跌达到阈值，则产生追加 BUY intent。
4. 其他情况无 intent。

“缓慢下跌”使用复权后的 `research_close`：累计跌幅达到阈值、最差单日收益不低于负的 crash threshold、下跌日数达到 minimum_down_days。

加仓和止盈使用 `close_raw`，从而与实际成交价保持同一价格口径。

### 4.4 最大投入

StrategyState 保存 `cumulative_buy_notional`。买入从空仓开始时重置本轮累计 gross notional；后续加仓不能超过 `initial_capital * max_capital_fraction`。不足一个标准 10% 档位时使用剩余额度的 FixedNotional。

IntentExecutor 使用与撮合器一致的 modeled execution price 向下计算股数，再按买入手数向下取整，避免滑点使 gross notional 超过该 intent 的预算。

## 5. StrategyState

StrategyPositionState 保存 quantity、remaining cost basis、last trade side/date/quantity/price、last buy price 和 cumulative buy notional。

- BUY：成本基础增加 gross + fees；last buy price 使用实际 execution price。
- SELL：按卖出前平均成本比例释放 remaining cost basis。
- 送股/拆分：成本基础不变；数量增加；last buy price 按数量扩张比例调整。
- 估值事件更新 state 的 as-of date。
- 早于当前 state as-of 的成交会被拒绝。

Daily state 的人工维护只允许通过显式 confirmed fill 或 confirmed stock adjustment，不从 advice 或 target 推导。
