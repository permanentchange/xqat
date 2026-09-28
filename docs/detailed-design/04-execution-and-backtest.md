# 04 执行与回测详细设计

## 1. Allocation 组合规划

AllocationDecision 的 TargetPortfolio 先经过通用组合校验，再由 RebalancePlanner 转为 ExecutionInstruction。

规划输入包括当前持仓、可卖数量、portfolio value、参考价、lot rule 和现金预算。TargetPosition.planning_priority 用于现金受限时的确定性买入顺序。

AllocationDecisionExecutor 保持固定顺序：

1. 基于执行日开盘参考价规划卖出；
2. 执行卖出；
3. 使用卖出后的真实 cash_available 再规划买入；
4. 执行买入；
5. 计算 two-way adjustment turnover。

因此买入预算使用实际卖出净现金，而不是理论卖出金额。

## 2. TradeIntent 执行

IntentExecutor 接收 TradeIntentDecision 和 StrategyStateView。Intent 固定按 SELL 在前、BUY 在后，再按 priority、security id、intent id 排序。

买入 sizing：

- FixedNotional：固定金额；
- InitialCapitalFraction：state.initial_capital 的比例。

卖出 sizing：

- CurrentPositionFraction：当前实际持仓比例；
- FullPosition：全部持仓；
- FixedNotional：按参考价换算数量。

买入股数使用 ExecutionSimulator 的 modeled execution price 计算预算，再按 buy lot 向下取整。卖出非全清仓按 sell lot 向下取整；全清仓允许 odd-lot 全部退出。

## 3. ExecutionSimulator

ExecutionFacts 包含 asset type、open/high/low、涨跌停、volume、price tick、停牌和涨跌停锁定状态。

执行顺序：

1. 全日停牌直接 UNFILLED/SUSPENDED；
2. BUY 遇涨停锁定、SELL 遇跌停锁定直接未成交；
3. NEXT_OPEN 价格模型：open_raw 加/减 slippage bps；
4. 按 price tick 量化，并限制在当日 high/low 与 up/down limit 范围；
5. max_volume_participation 限制可成交数量；
6. SELL 再受实际 holding 和 sellable quantity 限制；
7. BUY 再受 cash + fees 限制；
8. 生成 FILLED 或 PARTIALLY_FILLED ExecutionRecord。

执行记录保存 decision id、instruction id、reference price、actual execution price、gross、费用和 unfilled reason。

## 4. 费用模型

默认 fee schedule id 为 `cn_cash_market_default_v1`。

当前费率：

- A 股佣金 0.03%，最低 5 元；
- 当前 A 股过户费 0.001%；
- 当前 A 股卖出印花税 0.05%；
- ETF 佣金 0.03%，最低 5 元；无过户费和印花税。

A 股历史费率按生效日切换，覆盖 2008-09-19、2022-04-29、2023-08-28 三个边界。费用最终按分量和总额以分为单位量化。

## 5. SimulatedAccount

账户维护：

- `cash_available`
- `cash_receivable`
- `positions`
- `sellable_quantities`
- `pending_sellable`
- `entitlements`
- typed `ledger`

BUY 扣除 gross + fees，增加持仓，并把成交数量放入下一交易日 release 的 pending sellable，实现 A 股 T+1。

SELL 要求 filled quantity 不超过 sellable quantity，成交净现金当日立即进入 cash_available，可被同日后续买入复用。

账户每次交易、可卖释放和公司行为后检查现金非负、持仓非负以及 `0 <= sellable <= position`。

## 6. Typed AccountEvent

Ledger 事件当前包括：

CashInitialized、TradeFilled、SellableReleased、DividendEntitlementRecorded、CashDividendDeclared、CashDividendPaid、StockDistributionApplied、SplitApplied、ValuationRecorded。

StrategyStateReducer 只消费其中与策略状态相关的事件，因此 account 是账户真相，strategy state 是策略所需的派生执行状态，两者职责分离。

## 7. 公司行为

回测支持：

- CASH_DIVIDEND
- STOCK_DISTRIBUTION
- SPLIT

record date 记录 entitlement；ex date 增加 cash receivable 和/或股份；pay date 把 receivable 转为 available cash；stock list date 释放送股可卖数量。Split 在 ex date 同步调整总持仓与可卖数量。

持仓遇未支持 action type、分数股送转或分数可卖拆分时明确失败，不静默近似。

Dividend tax model：

- PROVIDER_AFTER_TAX：直接使用 provider after-tax cash，并在 Result 标记 `DIVIDEND_TAX_NOT_PERSONALIZED`；
- FLAT_RATE：用 before-tax cash 和显式税率计算。

## 8. BacktestEngine 日循环

每个交易日按以下顺序推进：

1. 释放到期 T+1 sellable；
2. 应用 ex-date entitlement；
3. 应用 pay-date cash 和 stock-list-date release；
4. 执行当日到期 StrategyDecision；
5. 使用 raw close / last reliable valuation close 估值；
6. 计算 NAV、benchmark、回撤、turnover、贡献；
7. 记录 ValuationRecorded；
8. 按 record date 记录新的 corporate-action entitlement；
9. 若 schedule 判定为 decision day，则同步 confirmed events 到 strategy state，并生成新决策，排入未来 effective date。

所有新决策必须 forward effective，重复 effective date 被拒绝。

## 9. 绩效

PerformanceAnalyzer 基于每日 NAV 计算 cumulative return、252 日年化收益、年化波动率、最大回撤及峰谷/恢复日期、Sharpe、Calmar。少于 60 个 return intervals 时标记 `SHORT_PERFORMANCE_SAMPLE`。

BacktestReportAssembler 另外汇总 benchmark return、excess return、turnover、fees、slippage、仓位暴露、贡献和用户定义 analysis periods。报告层只组装已经产生的业务事实，不重新运行策略。
