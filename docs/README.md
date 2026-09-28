# XQatExp 文档

本目录只描述当前 `main` 分支已经实现的系统。这里不保存重构计划、阶段实施记录、验收报告、讨论稿或历史方案。

## 文档地图

- [总体架构](architecture.md)：系统边界、模块分层、核心数据流、策略与执行架构。
- [详细设计索引](detailed-design/README.md)：详细设计文档入口。
- [领域模型与策略](detailed-design/01-domain-and-strategy.md)：共享合同、Strategy Registry、两类策略与显式策略状态。
- [数据与研究层](detailed-design/02-data-and-research.md)：Tushare Raw、Research Artifact、ResearchSession、因子与 readiness。
- [应用层与 CLI](detailed-design/03-application-and-cli.md)：命令树、配置解析、Workflow 编排与错误边界。
- [执行与回测](detailed-design/04-execution-and-backtest.md)：组合规划、撮合、费用、账户、公司行为、回测循环与绩效。
- [Daily 与策略状态](detailed-design/05-daily-state-and-advice.md)：Daily Target、Daily Decision、AccountSnapshot、TradeAdvice 与 state 维护。
- [Artifact、Schema 与报告](detailed-design/06-artifacts-and-reporting.md)：Manifest、原子发布、物理 Schema、Result 文件集合与报告。
- [工程基线](detailed-design/17-engineering-baseline.md)：Python、依赖、平台、资源、CI 与本地验证。

## 事实来源

文档必须和当前实现保持一致。发生冲突时，优先级为：

1. `src/xqatexp/` 中的当前实现；
2. `schemas/` 中的版本化物理合同；
3. `tests/` 中的当前可执行行为合同；
4. 本目录中的说明。

文档中的策略参数、文件集合、命令和错误语义都应可以在上述实现中找到对应依据。新增能力时应更新对应设计文档；已经废弃的设计应直接删除，不在本目录保留“历史兼容说明”。

## 当前系统摘要

XQatExp 是本地运行的 A 股量化研究与决策工具，Python 3.12，Linux 优先并兼容 Windows。系统将 Tushare 原始响应保存为不可变 Raw Artifact，经确定性转换生成包含七张 Parquet 表的 Research Artifact，再由受限 ResearchSession 提供给策略。

当前内置两类策略：

- `weekly_market_guard_rank_v1@1.0.0`：周频 allocation 策略，输出 `AllocationDecision -> TargetPortfolio`。
- `staged_drawdown_v1@1.0.0`：日频 stateful 策略，输出 `TradeIntentDecision`，并要求显式的 confirmed execution state。

Backtest 与 Daily 共用策略声明、数据 readiness、决策、执行和 Artifact 契约。回测撮合使用 NEXT_OPEN 日线模型，不模拟 9:15–9:25 集合竞价订单簿微观过程。

策略源码采用“共享框架 + strategy-id 子包”结构：`src/xqatexp/strategy/` 根目录只保存 Registry、Decision/Intent、Schedule 与 State；具体策略位于 `src/xqatexp/strategy/strategies/<strategy_id>/`。详细边界见 [领域模型与策略](detailed-design/01-domain-and-strategy.md)。
