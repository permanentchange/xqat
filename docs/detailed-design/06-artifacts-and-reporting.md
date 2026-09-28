# 06 Artifact、Schema 与报告详细设计

## 1. Artifact 通用模型

所有目录型 Artifact 都有 `manifest.json`。Manifest schema version 当前为 1.0，包含：

- artifact type/id；
- created_at；
- producer name/version；
- run identity；
- input references；
- date scope；
- 文件 path、media type、size、SHA-256、row count、schema id/version；
- issues；
- limitations。

Canonical JSON 对 object key 排序，Decimal 使用十进制文本数值，datetime 统一 UTC Z 表示，拒绝 NaN/Infinity 和无时区 datetime。

## 2. 原子发布

ArtifactPublisher 的发布流程：

1. 拒绝 symlink/junction 输出路径、文件系统根和当前工作目录；
2. 要求输出父目录至少 1GB 可用空间；
3. 在同一父目录创建唯一 staging；
4. builder 写完整 Artifact；
5. ArtifactReader 对 staging 做 Manifest/hash 校验；
6. 新输出直接 rename；
7. overwrite 模式使用 target→backup、staging→target 的 swap 过程；
8. swap record 支持进程中断后的恢复；
9. 成功后清理 backup/staging。

ERROR / SKIP / OVERWRITE 是唯一覆盖策略。

## 3. JSON Schema

当前 JSON Schema：

- account_snapshot 1.x
- artifact_manifest 1.x
- failure_diagnostic 1.x
- issues 1.x
- metrics 1.x
- raw_request 1.x
- resolved_config 1.x
- strategy_diagnostics 1.x
- strategy_state 1.x
- target_portfolio 2.x writer；reader 兼容 legacy 1.x
- trade_intents 1.x
- trade_advice 1.x

未知 major version 直接拒绝。

## 4. Arrow / CSV Schema

Arrow 表由 SchemaRegistry 以精确字段、类型、nullable、primary key 和排序规则校验。当前包含七张 Research 表，以及 target_history、portfolio_daily、trades、unfilled。

CSV 固定列合同包括 custom_factor_input、target_positions、period_metrics、trade_advice。

## 5. Raw Artifact

文件：

- `manifest.json`
- `request.json`
- `response.jsonl.gz`

Raw 层保留 provider 原始字段和分页 provenance。

## 6. Research Artifact

文件：

- `manifest.json`
- `tables/security_master.parquet`
- `tables/trade_calendar.parquet`
- `tables/market_daily.parquet`
- `tables/security_status_daily.parquet`
- `tables/financial_snapshot.parquet`
- `tables/system_factor_daily.parquet`
- `tables/corporate_action.parquet`

ResearchCheck 要求 verified payload 集合精确等于七张表。

## 7. Daily Result

### DAILY_TARGET_RESULT

- resolved_config.json
- target_portfolio.json
- target_positions.csv
- strategy_diagnostics.json
- issues.json
- report.md
- manifest.json

### DAILY_DECISION_RESULT

- resolved_config.json
- trade_intents.json
- strategy_state.json
- strategy_diagnostics.json
- issues.json
- report.md
- manifest.json

其中 strategy_state.json 是“产生该决策时使用的 confirmed state 快照”，不会包含尚未发生的 intent 成交。

### DAILY_ADVICE_RESULT

- resolved_config.json
- target_portfolio.json
- target_positions.csv
- trade_advice.json
- trade_advice.csv
- issues.json
- report.md
- manifest.json

## 8. Backtest Result

固定业务文件：

- resolved_config.json
- target_history.parquet
- portfolio_daily.parquet
- trades.parquet
- unfilled.parquet
- metrics.json
- period_metrics.csv
- strategy_diagnostics.json
- issues.json
- report.md

stateful backtest 额外保存最终 `strategy_state.json`。allocation backtest 不产生该文件。

target_history 是通用 target schema；weekly 专属 rank/score/regime/drawdown 位于 strategy_diagnostics，不写回 target history。

trades 表保存 execution/decision/instruction provenance；unfilled 表保存请求、已成交/未成交数量、reason 和 evidence。

## 9. Target schema 演进

当前 writer 只写 TargetPortfolio schema 2.0。2.0 已移除 weekly strategy 专属字段，并新增通用 planning priority。

`reporting.readers.load_target` 仍能读取旧 1.x target，并将 legacy rank 映射到 planning priority；这是唯一保留的旧 target reader compatibility。新结果不再写 1.x。

## 10. Reporting

BacktestResultAssembler 负责把 BacktestResult 组装成 Arrow tables、metrics 和 period metrics。ResultArtifactPublisher 负责 Schema 校验、文件编码和 Artifact 发布；Markdown renderer 只格式化已经组装的事实。

`result show --format summary|markdown|json` 只读取已发布 Artifact，不重新计算策略或指标。

## 11. Failure Diagnostic

Backtest 和 Daily 命令可用 `--failure-report` 发布独立 Failure Diagnostic。它记录 run id、mode、失败 stage、错误和脱敏 input references。失败产物与成功 Result 是不同 artifact type，不会混合。
