# XQatExp 详细设计

本目录描述当前代码的详细设计，不记录历史实施过程。

## 文档

1. [领域模型与策略](01-domain-and-strategy.md)
2. [数据与研究层](02-data-and-research.md)
3. [应用层、配置与 CLI](03-application-and-cli.md)
4. [执行与回测](04-execution-and-backtest.md)
5. [Daily、策略状态与建议](05-daily-state-and-advice.md)
6. [Artifact、Schema 与报告](06-artifacts-and-reporting.md)
7. [工程与运行基线](17-engineering-baseline.md)

总体模块关系见 [总体架构](../architecture.md)。

## 设计约束

详细设计只描述当前实现：

- 不把未来计划写成现有能力；
- 不保留已经完成的重构阶段记录；
- 不复制验证报告；
- 参数默认值必须和 strategy parameter normalizer 一致；
- Artifact 文件集合必须和 publisher/schema registry 一致；
- CLI 命令必须和 `xqatexp.cli` 一致；
- 平台和 CI 描述必须和当前工程配置一致。
