# Daily、策略状态与建议

## 1. Daily Target

`daily target` 面向不要求 confirmed state 的 allocation 策略。

流程：

1. 解析 config 与 StrategySpec；
2. 可选读取 previous TargetPortfolio；
3. 打开 ResearchSession；
4. 执行 declaration-driven readiness；
5. 调用策略产生 AllocationDecision；
6. annotate target transitions；
7. 校验 target 权重、产品范围、单股上限和 next-trade-day effective date；
8. 发布 DAILY_TARGET_RESULT。

Target 是策略目标，不是订单。Daily Target 未单独执行 StrategySpec.schedule 检查；周频策略应由用户选择真实周末交易日。

## 2. Daily Decision

`daily decide` 面向 `StateRequirement.CONFIRMED_EXECUTION_STATE` 策略。

必须提供 StrategyStateSnapshot。Workflow 校验 state strategy id/version 与 config 一致，并拒绝 state.as_of 晚于 decision date。

可选 AccountSnapshot 只用于数量一致性检查：

- 对 state 与 account 同时出现的证券，quantity 必须一致；
- 若 AccountSnapshot positions completeness 为 COMPLETE，则完整正持仓集合必须和 state 一致；
- 系统不会从 AccountSnapshot 推导 last buy price、成本基础或累计买入金额。

输出 TradeIntentDecision，不代表成交确认。

## 3. StrategyStateSnapshot

JSON schema version 当前为 1.0。顶层包含 strategy id/version、initial capital、as-of date 和 positions。

每个 position 保存：

- quantity
- remaining_cost_basis
- last_trade_side
- last_trade_date
- last_trade_quantity
- last_trade_price
- last_buy_price
- cumulative_buy_notional

State 不是券商账户的副本；它只保存策略决策需要、且能由 confirmed events 确定重放的状态。

## 4. State 维护命令

### state init

只允许已注册策略。initial capital 必须正数，生成空 position state。

### state apply-fill

输入必须是真实确认成交事实：execution date、security、BUY/SELL、filled quantity、actual execution price 和实际费用。

BUY 会更新 quantity、remaining cost basis、last trade、last buy price 和 cumulative buy notional；SELL 按原平均成本比例释放 remaining cost basis。

早于当前 state.as_of 的成交被拒绝。

### state apply-stock-adjustment

支持已确认的 STOCK_DISTRIBUTION 和 SPLIT。added quantity 必须正数。成本基础保持不变，last buy price 按数量变化比例调整；effective date 更新 as-of。

## 5. AccountSnapshot

AccountSnapshot schema version 为 1.0，账户范围固定为 `STRATEGY_MANAGED`。它显式区分：

- scope completeness：COMPLETE / PARTIAL / UNKNOWN；
- positions completeness：COMPLETE / PARTIAL / UNKNOWN；
- available cash；
- managed total assets；
- excluded asset value；
- positions 的 quantity、sellable quantity、market value、reference price/date。

缺失事实保持缺失，不由 DailyAdviceService 猜测。

## 6. Daily Advice

`daily advise` 读取一个已发布 TargetPortfolio，再可选结合 AccountSnapshot 和 Research execution rows 生成研究参考建议。

计算原则：

- target amount = managed total assets × target weight；
- theoretical target quantity 按参考价和 buy lot 向下取整；
- BUY/INCREASE 受 available cash、目标 cash weight 和费用约束；
- SELL/DECREASE 受 sellable quantity 和 sell lot 约束；
- 完全退出时可卖全部实际数量；
- 账户范围或持仓不完整时相关数量降级为 unresolved，而不是伪造。

常见 limitation 包括 ACCOUNT_NOT_PROVIDED、POSITIONS_UNKNOWN/PARTIAL、ACCOUNT_SCOPE_INCOMPLETE、MANAGED_TOTAL_ASSETS_UNKNOWN、AVAILABLE_CASH_UNKNOWN、ACCOUNT_STALE、REFERENCE_PRICE_UNKNOWN、SELLABLE_QUANTITY_UNKNOWN。

TradeAdvice 固定包含“仅供研究参考; 不是订单或投资承诺。”声明。

## 7. 三种 Daily 产物的边界

| 命令 | 输入核心 | 输出语义 |
| --- | --- | --- |
| `daily target` | Research + allocation strategy | 目标权重 |
| `daily decide` | Research + stateful strategy + confirmed state | 下一交易日 trade intents |
| `daily advise` | 已发布 target + 可选 AccountSnapshot | 研究参考数量建议 |

Daily Advice 不适用于 TradeIntentDecision；stateful 策略通过 `daily decide` 直接表达意图，并由外部真实执行结果通过 state 命令回写。

## 8. 数据准备与产物读取

Daily 从显式 Research Artifact 读取数据并执行 readiness，不联网补抓。
更新 Raw Collection 后先构建完整 Research，再修改运行配置中的 research_artifact 和 decision_date。
批次更新、构建和覆盖检查见 [数据操作指南](../data.md)。

三类 Daily Artifact 文件集合见 [Artifact 设计](06-artifacts-and-reporting.md#7-daily-result)。
state 命令写新 JSON；Daily Decision 内的 strategy_state.json 是本次输入快照，
stateful Backtest Result 内的同名文件则是模拟执行后的最终状态。
