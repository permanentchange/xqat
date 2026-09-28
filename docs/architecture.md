# XQatExp 总体架构

## 1. 系统定位

XQatExp 是一个本地优先、Artifact 驱动的 A 股量化研究与决策系统。核心目标是把数据获取、研究数据、策略决策、执行模拟、每日决策和结果发布拆成清晰的模块，并通过版本化合同连接。

系统不是券商 OMS，不直接提交真实订单，也不承诺实时行情或真实集合竞价微观撮合。所有正式结果都由显式输入生成并发布为可校验 Artifact。

## 2. 顶层数据流

```mermaid
flowchart LR
    T[Tushare] --> R[Raw Artifact]
    R --> B[ResearchBuilder]
    B --> U[Research Artifact]
    U --> S[ResearchSession]
    S --> G[Strategy Registry]
    G --> A[AllocationDecision]
    G --> I[TradeIntentDecision]
    A --> X[DecisionExecutorRouter]
    I --> X
    X --> E[Execution Simulator / Account]
    A --> D[Daily Target / Advice]
    I --> Q[Daily Decision]
    E --> P[Backtest Result]
    D --> O[Result Artifact]
    Q --> O
    P --> O
```

数据和结果均通过 Manifest、SHA-256 和版本化 Schema 校验。策略不直接读取 Raw 文件；回测和 Daily 不直接访问网络。

## 3. 分层与包边界

| 层 | 主要包 | 当前职责 |
| --- | --- | --- |
| CLI / Application | `cli.py`, `application/` | 参数解析、配置解析、用例编排、退出码与失败诊断 |
| Provider / Raw | `providers/tushare/` | Tushare 调用、能力探测、Raw 抓取与 Raw 校验 |
| Artifact | `artifacts/` | Canonical JSON、Manifest、Schema Registry、原子发布与校验读取 |
| Research | `research/` | Raw→Research 转换、系统因子、受限查询、readiness、自定义因子 |
| Domain | `domain/` | 不可变领域合同、枚举、Issue、数值规范 |
| Strategy | `strategy/` | Strategy Registry、参数、声明、schedule、allocation/stateful 策略与 state reducer |
| Portfolio | `portfolio/` | Target 校验、transition、手数/现金规划、allocation rebalance |
| Execution / Backtest | `backtest/` | 决策执行、撮合、费用、模拟账户、公司行为、回测主循环 |
| Daily | `daily/` | Daily Target、stateful Daily Decision、AccountSnapshot 与 TradeAdvice |
| Performance | `performance/` | NAV 收益、回撤、Sharpe、Calmar、贡献与阶段分析 |
| Reporting | `reporting/` | Result 组装、结构化输出、Markdown、Failure Diagnostic |
| Operations / Security | `operations/`, `security.py` | 脱敏运行日志、Token 边界、路径隔离 |

## 4. 核心架构不变量

### 4.1 数据可见性

Research 数据包含 `available_from`，ResearchSession 的查询始终受 decision date 和 strategy declaration 限制。系统因子的 `input_end_date` 不允许晚于 `factor_date`。策略只能读取决策时点已经可见的数据。

### 4.2 Strategy Registry 是唯一策略入口

`strategy/registry.py` 当前注册：

- `weekly_market_guard_rank_v1@1.0.0` + `WeeklyLastTradingDayCloseSchedule`；
- `staged_drawdown_v1@1.0.0` + `DailyCloseSchedule`。

Config、Backtest 和 Daily 都通过 Registry 解析策略，不在 Engine 中按 strategy id 写专用分支。

### 4.3 两种策略决策

系统的 `StrategyDecision` 是：

- `AllocationDecision`：携带通用 `TargetPortfolio`；
- `TradeIntentDecision`：携带一个或多个 `TradeIntent`。

`DecisionExecutorRouter` 根据决策类型分派到 `AllocationDecisionExecutor` 或 `IntentExecutor`。BacktestEngine 不负责策略专用 sizing。

### 4.4 显式策略状态

需要成交状态的策略通过 `StateRequirement.CONFIRMED_EXECUTION_STATE` 声明。状态由 `StrategyStateReducer` 从 typed AccountEvent 推进，只接受实际成交和已确认公司行为；Daily 不从历史建议推测成交。

### 4.5 决策与执行分离

决策日 D 产生的 allocation target 或 trade intent 必须 `effective_from > D`。当前执行模型为 NEXT_OPEN，使用下一交易日 `open_raw` 加滑点、价格边界、成交量和账户约束生成实际成交。

### 4.6 Artifact 是运行边界

Raw、Research、Backtest Result、Daily Result 和 Failure Diagnostic 都是自包含 Artifact。Artifact 在发布前完整校验，Manifest 记录文件 SHA-256；覆盖写使用 staging/backup/swap 恢复协议。

## 5. 主要运行路径

### 5.1 Raw → Research

```mermaid
sequenceDiagram
    participant CLI
    participant Provider as Tushare Provider
    participant Raw as Raw Artifact
    participant Builder as ResearchBuilder
    participant Research as Research Artifact
    CLI->>Provider: data fetch
    Provider->>Raw: request.json + response.jsonl.gz + manifest
    CLI->>Raw: data check-raw
    CLI->>Builder: data build/update
    Builder->>Research: 7 Parquet tables + manifest
    CLI->>Research: data check-research
```

### 5.2 Backtest

BacktestWorkflow 打开 ResearchSession，执行 readiness，然后将 Strategy、schedule、execution assumptions 和可选 state reducer 交给 BacktestEngine。Engine 按交易日推进账户、公司行为、待执行决策、估值和新决策，最后发布 Result Artifact。

### 5.3 Daily

- allocation 策略：`daily target` 生成 TargetPortfolio；`daily advise` 可结合 AccountSnapshot 生成参考数量。
- stateful 策略：`daily decide` 必须读取 StrategyStateSnapshot，生成 TradeIntentDecision；真实成交后由 `state apply-fill` 更新 state。

## 6. 扩展点

新增 allocation 策略需要提供参数 normalizer、declaration、strategy factory 和 schedule，并注册到 Strategy Registry。新增 stateful 策略还需声明 confirmed state requirement，并产生 TradeIntentDecision。只要使用现有 Decision/Intent 合同，不需要修改 BacktestEngine 主循环。

新增数据源或数据集应通过 Provider/Raw 层进入，ResearchBuilder 仍是进入策略可见数据的唯一正规转换路径。

## 7. 安全与平台

Tushare Token 只允许通过当前进程的 `TUSHARE_TOKEN` 环境变量注入。配置禁止 token/secret/password/credential/authorization 类字段。输入输出路径必须互不重叠。

运行时为 CPython 3.12。Linux x86_64 是主平台，Windows 10/11 x64 是兼容平台；具体工程基线见 [工程基线](detailed-design/17-engineering-baseline.md)。
