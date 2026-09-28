# XQatExp 架构解耦与扩展能力改造计划

## 1. 目标

本计划用于把当前“单一周频目标权重策略驱动”的实现，演进为可以稳定承载多策略、多决策模型与显式策略状态的本地模块化单体。

核心目标：

1. 修复表面可配置、实际写死的假扩展点。
2. 保持现有 `weekly_market_guard_rank_v1` 的交易路径和回测结果不变。
3. 建立真正的 `StrategySpec -> StrategyDecision -> ExecutionInstruction -> ExecutionRecord` 边界。
4. 让 Research readiness 由策略声明的数据需求驱动。
5. 把策略专属诊断信息从公共 `TargetPortfolio` 中剥离。
6. 收敛 Backtest 与 Daily 的组合规划逻辑。
7. 为后续成交反馈驱动的 stateful strategy 建立显式、不可变、可追溯的状态模型。
8. 保持 Linux 优先并继续兼容 Windows。

## 2. 最终目标架构

```text
Resolved Run Spec
        |
        v
Strategy Registry
        |
        v
Strategy Spec
   +----+-------------+----------------+
   |                  |                |
   v                  v                v
Parameters       Requirements       Schedule
   |                  |                |
   +------------------+----------------+
                      |
                      v
                   Strategy
                      |
          ResearchDataView + StateView
                      |
                      v
              StrategyDecision
                /             \
               v               v
     AllocationDecision   TradeIntentDecision
               |               |
               v               v
     AllocationExecutor   IntentExecutor
               \               /
                +------v-------+
                       |
                       v
             ExecutionInstruction
                       |
                       v
               ExecutionSimulator
                       |
                       v
                ExecutionRecord
                 /             \
                v               v
       SimulatedAccount   StrategyStateReducer
```

## 3. 改造原则

- 先修假扩展点，再增加扩展点。
- 纯架构重构不得改变现有策略业务结果。
- 不建设动态插件系统、DI 容器、事件总线或微服务。
- 公共领域对象只包含公共语义。
- Decision -> Instruction -> Execution 的因果链必须从源头保存，Reporting 不反推业务事实。
- Research 的 `available_from`、时间可见性与 Raw/Research 边界保持不变。
- Stateful strategy 不直接读取或修改 `SimulatedAccount`。

## 4. 分阶段实施

### Phase 0: 重构安全网

- 固定现有 weekly strategy 的关键 golden/characterization behavior。
- 增加 unknown strategy、wrong version、unsupported execution model/fee schedule 的失败用例。
- 保持时间可见性、T+1、现金/持仓守恒、涨跌停、停牌、Artifact 原子发布测试。

验收：此阶段不改变生产业务逻辑。

### Phase 1: Strategy Registry 与真实策略分派

新增静态 `StrategyRegistry` / `StrategySpec`。

`strategy_id` 与 `strategy_version` 必须真实决定 Strategy、Declaration 与参数解析器；未知 ID 或不支持版本在配置阶段失败。

### Phase 2: Typed Strategy Parameters

将当前 `_STRATEGY_DEFAULTS` 与 `_normalize_strategy` 从全局 config 中迁到 weekly strategy 专属参数模块。

Strategy 参数只解析一次；Strategy 不再同时从构造器和 `generate_target(..., parameters)` 接收两份参数源。

### Phase 3: Run Spec 类型化

逐步把一个包含大量 Optional 字段的 `ResolvedRunContext` 收敛为按 RunMode 校验明确的 run spec。优先避免新增 stateful 能力继续污染统一 Context。

### Phase 4: Decision Schedule

新增明确的 DecisionSchedule 抽象，至少支持：

- `DAILY_CLOSE`
- `WEEKLY_LAST_TRADING_DAY_CLOSE`

BacktestEngine 不再内置 `_is_weekly_close` 作为策略调度规则。

### Phase 5: Requirement-driven Declaration

让 StrategyDeclaration 的数据需求成为权威来源。现有 `DataRequirement` 升级为可执行的数据需求描述，而不是闲置 DTO。

### Phase 6: Requirement-driven Readiness

ReadinessChecker 只执行策略声明的 Requirement，不再内置：

- 313/252 等 weekly strategy 常量
- 周频采样规则
- financial 固定 coverage
- ETF factor 名称前缀约定

只需要价格历史的新策略不得因无关 financial/system factor 缺失失败。

### Phase 7: Generic TargetPortfolio + Strategy Diagnostics

公共 `TargetPortfolio` 保留：

- strategy id/version
- decision/effective date
- positions
- cash weight
- explanations

将以下 weekly strategy 专属信息迁出公共模型：

- market_regime
- theoretical_drawdown
- drawdown overlay
- rank
- score
- holding_age_weeks

通过独立 StrategyDiagnostics Artifact 输出。

Schema 采用明确版本迁移，不长期双写。

### Phase 8: Portfolio Validation 分层

拆分：

1. PortfolioInvariantValidator：权重、唯一性、effective date 等公共不变量。
2. ProductPortfolioPolicy：MVP 可交易资产边界。
3. StrategyDecisionValidator：当前策略专属约束。

### Phase 9: Backtest / Daily 共享 Portfolio Planning Kernel

统一：

- target amount
- lot rounding
- target quantity
- fee-aware affordability
- buy priority

避免 `RebalancePlanner` 与 `DailyAdviceService` 各自维护重复计算。

### Phase 10: StrategyDecision

引入通用 StrategyDecision。现有 weekly strategy 先输出 `AllocationDecision`，保持业务结果不变。

### Phase 11: DecisionExecutor

BacktestEngine 不直接理解 TargetPortfolio/RebalancePlanner。

```text
StrategyDecision
      |
      v
DecisionExecutor
      |
      v
ExecutionInstruction[]
```

现有路径由 `AllocationDecisionExecutor` 实现。

### Phase 12: Execution Provenance

从产生时保存：

- decision_id
- instruction_id

并传播到 ExecutionRecord。Reporting 不再根据执行日期反推所属决策。

### Phase 13: SimulatedAccount 封装

外部模块不直接修改 account 内部 dict/cash。

CorporateActionProcessor 负责解释公司行为；具体账户状态变化由 SimulatedAccount 自己执行。

### Phase 14: Typed Account Events

逐步替换 `list[tuple[str, object]]` ledger，至少包括：

- TradeFilled
- SellableReleased
- CashDividendDeclared
- CashDividendPaid
- StockDistributionApplied
- SplitApplied

不要求完整 Event Sourcing。

### Phase 15: Strategy State

新增：

- `StrategyStateSnapshot`
- `StrategyStateView`
- `StrategyStateReducer`
- `StateRequirement.NONE`
- `StateRequirement.CONFIRMED_EXECUTION_STATE`

StrategyState 与 AccountSnapshot/SimulatedAccount 明确分离。

### Phase 16: TradeIntentDecision + IntentExecutor

支持显式 sizing intent，例如：

- FixedNotional
- InitialCapitalFraction
- CurrentPositionFraction
- FullPosition

Strategy 决定“要做什么”，Planner/Executor 根据执行日价格、手数、费用、现金与可卖数量计算 requested quantity。

### Phase 17: Stateful Daily

真实 Daily 模式必须显式输入 StrategyStateSnapshot，不根据历史建议推测成交，也不在本地静默维护 shadow account。

StrategyState 与 AccountSnapshot 做一致性检查，冲突必须显式告警或失败。

### Phase 18: Reporting 拆分

把当前 ResultArtifactPublisher 中的：

- report model assembly
- performance calculation
- provenance 推断
- serialization
- artifact publication

拆成明确层次。ArtifactPublisher 底层原子发布机制保持不变。

### Phase 19: 实现 staged drawdown strategy

基础架构完成后，再实现：

- 首次 20 日下跌信号
- 按上次实际买入成交价继续加仓
- 每次投入初始资金固定比例
- 按剩余成本/持仓收益止盈
- 部分成交、T+1 与公司行为后的状态更新

新增该策略时，不应再修改 BacktestEngine 主循环。

## 5. 必须保持的兼容性

在新增 stateful 策略前，以下必须持续成立：

- 现有 CLI 工作。
- weekly strategy target、trade、NAV 与 fee golden behavior 不变。
- T+1、涨跌停、停牌、volume cap、现金约束行为不变。
- Daily Advice 现有语义不变。
- Linux 测试通过；Windows CI 继续通过。
- Artifact hash、schema validation 与原子发布保证不变。

## 6. 架构合同测试

至少新增/保持：

- Strategy Registry 一致性：Config / Backtest / Daily / Manifest 使用同一 strategy id/version。
- Decision Schedule：fake daily/weekly strategy 在正确交易日被调用。
- Requirement locality：只声明价格数据的策略不依赖 financial/system factors。
- Decision kind independence：Engine 不直接绑定具体 TargetPortfolio planner。
- Execution provenance：每个 ExecutionRecord 可直接回溯 instruction_id/decision_id。
- Strategy state determinism：相同初始状态和事件序列产生相同最终状态。
- Partial fill：State 只根据真实 filled quantity/price 更新。
- Corporate action：拆股/送股后成本与数量状态经济含义正确。

## 7. 明确不做

本轮不建设：

- 动态插件加载
- Broker integration
- 实时/分钟级回测
- Event Bus
- 微服务
- 通用 OMS
- 通用资产类别系统
- 通用因子 DSL
- 数据库服务

不重写 DuckDB、Parquet、Tushare、Research temporal boundary、ExecutionSimulator 核心撮合规则与 ArtifactPublisher 原子发布机制。

## 8. 提交策略

直接在 `main` 上按小步提交。每个提交只覆盖一个可验证主题，提交前运行可执行的 Linux 测试；Windows 专属行为依赖 GitHub Actions 或用户本地 Windows 环境验证。

任何标记为“纯架构重构”的提交，如果改变现有 weekly strategy 的买卖时点、数量、成交价或 NAV，应视为回归并优先修复。

## 9. 当前执行状态

执行基线：`ede0ad6252cce7a47aa9f066f7c227894f71df62`（2026-09-27）。

状态说明：

- [x] Phase 0：重构安全网（现有测试与新增身份/调度/依赖局部性测试已覆盖主要护栏）。
- [x] Phase 1：Strategy Registry 与真实策略分派。
- [x] Phase 2：Typed Strategy Parameters。
- [x] Phase 3：Run Spec 类型化（工作流已使用 typed run spec；兼容性的 `ResolvedRunContext` 仍保留在外层配置合同）。
- [x] Phase 4：Decision Schedule。
- [x] Phase 5：Requirement-driven Declaration。
- [x] Phase 6：Requirement-driven Readiness。
- [x] Phase 7：Generic TargetPortfolio + Strategy Diagnostics。公共 Target 已移除 weekly 专属字段；writer 使用 target schema v2，reader 保留 v1 兼容；Daily Target / Backtest 输出独立 `strategy_diagnostics.json`。Linux 主 CI 已通过完整离线验证。
- [x] Phase 8：Portfolio Validation 分层。
- [x] Phase 9：Backtest / Daily 共享 Portfolio Planning primitives。
- [x] Phase 10：StrategyDecision（已建立 deterministic decision id 与 `AllocationDecision`）。
- [x] Phase 11：DecisionExecutor。已新增 `AllocationDecisionExecutor` 与 `DecisionExecutorRouter`，BacktestEngine 不再直接编排 RebalancePlanner/ExecutionSimulator。
- [x] Phase 12：Execution Provenance。
- [x] Phase 13：SimulatedAccount 封装。
- [x] Phase 14：Typed Account Events。
- [x] Phase 15：Strategy State。已新增不可变 `StrategyStateSnapshot/View`、纯 `StrategyStateReducer` 与 confirmed AccountEvent 回放；partial fill / corporate action 已有测试。
- [x] Phase 16：TradeIntentDecision + IntentExecutor。已支持 FixedNotional / InitialCapitalFraction / CurrentPositionFraction / FullPosition，并接入 BacktestEngine stateful 路径。
- [x] Phase 17：Stateful Daily。已新增 `DAILY_DECISION` / `daily decide`、显式 `StrategyStateSnapshot` 输入、可选 AccountSnapshot 一致性校验、`trade_intents.json` / `strategy_state.json` / diagnostics 输出；并新增 `state init` 与 `state apply-fill`，首次运行和真实成交后都无需手工编辑 state JSON。
- [x] Phase 18：Reporting 拆分。Backtest tables、metrics、period metrics 与 execution provenance 组装已抽到 `reporting/assemblers.py`；ResultArtifactPublisher 不再推导这些业务事实，并支持 stateful daily/backtest state artifact。
- [x] Phase 19：staged drawdown strategy。已注册 `staged_drawdown_v1@1.0.0`，使用 DailyCloseSchedule、price-only requirements、confirmed execution state 与 TradeIntent；覆盖首次缓慢下跌买入、按上次实际买入价再跌加仓、整体成本止盈分批卖出、单轮最大投入、partial fill/company action 状态推进。

后续执行顺序按依赖调整为：

```text
Phase 17 Stateful Daily [DONE]
    ->
Phase 18 Reporting decomposition [DONE]
    ->
Phase 19 staged drawdown strategy [DONE]
```

每完成一个阶段，更新本节状态并保持提交可独立审查。Phase 11/15/16 已由 Linux 主 CI run #99 的完整离线测试、CLI help 与 offline self-check 验证通过。由于当前自动化执行环境无法直接通过网络克隆 GitHub 仓库，本轮验证优先使用仓库的 GitHub Actions Linux/Windows CI；若某项无法由 CI 覆盖，会在执行结果中明确列为需要本地验证。

### 9.1 Phase 19 实际语义

- 20 日趋势使用 `research_close`；与上次实际成交买入价比较、整体持仓收益率使用 `close_raw`，避免复权价与真实成交价混用。
- 默认“缓慢下跌”定义为 20 个收盘观察内累计跌幅至少 10%、任一单日跌幅不超过 5%、19 个日收益中至少 12 天下跌；均可配置。
- 首次/连续买入 sizing 为初始资金比例；IntentExecutor 使用与 ExecutionSimulator 相同的 modeled execution price 做向下手数取整，因此滑点不会让实际 gross 突破该档 notional 预算。
- 最大投入按当前持仓周期的实际累计买入 gross notional 约束；完全清仓后下一轮首次买入重置累计周期。买入费用计入剩余成本基础，但不计入 gross-notional 上限。
- 止盈收益率按 `当前 raw close * 持仓数量 / 剩余成本基础 - 1` 计算；剩余成本基础包含买入费用，部分卖出按持股比例释放成本。
- 止盈信号优先于加仓信号；卖出后如果后续仍满足止盈阈值，可继续按当前持仓 20% 产生下一次卖出 intent。
- 真实 Daily 运行不从历史建议猜成交：必须通过显式 state 输入；实际成交后用 `state apply-fill` 写入 confirmed fill。可选 AccountSnapshot 只做数量一致性校验，不修补 state。
- 股票送转/拆分在 backtest state reducer 中保持成本基础不变，并按数量变化调整 `last_buy_price` 锚点。

### 9.2 当前验证状态

本轮按用户要求不等待 CI 结果再推进代码。随后已由用户在 Linux Conda `xqat` 环境中完成非联网测试验证：`python -m pytest -m "not live_tushare" -q`，结果为 `238 passed, 1 deselected`。这确认当前 unit / integration / e2e / contract 非 live 测试全部通过。live Tushare 测试仍需要本地 `TUSHARE_TOKEN`；Ruff / Mypy 可作为独立开发检查继续运行。

Phase 17–19 生产代码 checkpoint：`e4ada4c1e4dedbbb8c1e9b9f0927ed109d6d3971`；使用说明同步 checkpoint：`9e3d4a924dbf8c699b8bda746355d8d6ca715286`。
