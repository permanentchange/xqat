# 领域模型与策略

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

### 2.1 代码组织

共享策略框架固定留在 `src/xqatexp/strategy/` 根目录：

- `registry.py`：全局 StrategySpec 注册与解析；
- `decision.py`：稳定 decision/instruction/intent identity；
- `intents.py`：StrategyDecision、TradeIntentDecision 与 sizing 类型；
- `schedule.py`：Daily/Weekly decision schedule；
- `state.py`：StrategyStateSnapshot/View/Reducer；
- `state_io.py`、`state_tools.py`：state 序列化与 confirmed-event 维护。

具体策略只放在 `src/xqatexp/strategy/strategies/<strategy_id>/`。

当前目录：

```text
strategies/
├── weekly_market_guard_rank_v1/
│   ├── __init__.py
│   ├── README.md
│   ├── parameters.py
│   ├── declaration.py
│   ├── strategy.py
│   ├── scoring.py
│   ├── market_regime.py
│   └── drawdown.py
└── staged_drawdown_v1/
    ├── __init__.py
    ├── README.md
    ├── parameters.py
    ├── declaration.py
    └── strategy.py
```

每个具体策略目录包含自己的 `README.md`，作为该策略当前逻辑、参数、数据要求和运行方式的源码邻近说明。具体策略模块可以依赖共享 Decision/Intent/State/Schedule 合同；共享框架不能反向依赖某个策略的内部算法。唯一允许聚合所有具体策略的共享模块是 `registry.py`。

策略专属 unit tests 镜像源码结构，位于 `tests/unit/strategy/strategies/<strategy_id>/`；共享 state 测试仍位于 `tests/unit/strategy/`。

### 2.2 新策略接入合同

新增 allocation 策略至少需要：

1. `parameters.py`：默认值、类型化参数和 normalizer；
2. `declaration.py`：StrategyDeclaration / DataRequirement；
3. `strategy.py`：产生 AllocationDecision 或 TargetPortfolio 的算法；
4. 在 `strategy/registry.py` 注册 StrategySpec 并绑定 schedule；
5. 对应 strategy-specific unit tests 和必要的 integration/backtest 测试。

新增 stateful 策略除上述内容外，还应声明 `CONFIRMED_EXECUTION_STATE`，并由 `strategy.py` 产生 TradeIntentDecision。除非需要新增通用能力，否则不应修改 BacktestEngine、IntentExecutor、ResearchSession、Account 或 Reporting。

## 3. 内置策略

| 策略 | 版本 | Schedule | 决策 | 状态要求 |
| --- | --- | --- | --- | --- |
| weekly_market_guard_rank_v1 | 1.0.0 | WeeklyLastTradingDayCloseSchedule | AllocationDecision | NONE |
| staged_drawdown_v1 | 1.0.0 | DailyCloseSchedule | TradeIntentDecision | CONFIRMED_EXECUTION_STATE |

周频策略声明 320 日 lookback，要求至少 313 日 warmup，重放最近 252 日。
它结合 ETF regime、股票系统因子、横截面评分、持仓滞回和滚动 60 日理论组合回撤产生目标权重。
具体默认值、覆盖率、资格过滤、评分和 overlay 见
[weekly 策略说明](../../src/xqatexp/strategy/strategies/weekly_market_guard_rank_v1/README.md)。

staged 策略按指定证券的 research_close 判断缓慢下跌，按 close_raw 和实际 confirmed state
判断加仓与止盈；只产生 intent，不直接修改状态。Backtest 自动创建 reducer，Daily 必须提供 state。
窗口、阈值、投入上限及信号优先级见
[staged 策略说明](../../src/xqatexp/strategy/strategies/staged_drawdown_v1/README.md)。

## 4. StrategyState

StrategyPositionState 保存 quantity、remaining cost basis、last trade side/date/quantity/price、last buy price 和 cumulative buy notional。

- BUY：成本基础增加 gross + fees；last buy price 使用实际 execution price。
- SELL：按卖出前平均成本比例释放 remaining cost basis。
- 可选 exit_base_quantity / exit_sold_quantity：首次 SELL 锁定数量基准并累计确认卖出量；
  BUY 或清仓重置。送股/拆股按剩余持仓比例调整为等价数量，保留 12 位小数。
- 送股/拆分：成本基础不变；数量增加；last buy price 按数量扩张比例调整。
- 估值事件更新 state 的 as-of date。
- 早于当前 state as-of 的成交会被拒绝。

Daily state 的人工维护只允许通过显式 confirmed fill 或 confirmed stock adjustment，不从 advice 或 target 推导。
