# Artifact、Schema 与报告

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

- account_snapshot 1.0
- artifact_manifest 1.0
- failure_diagnostic 1.0
- issues 1.0
- metrics 1.0
- raw_request 1.0
- raw_collection 1.0
- batch_result 1.0
- resolved_config 1.0
- strategy_diagnostics 1.0
- strategy_state 1.0
- target_portfolio 2.0 writer；reader 接受 1.0 和 2.0
- trade_intents 1.0
- trade_advice 1.0

未知 major version 直接拒绝，其他版本也需满足对应物理 Schema 的 schema_version 约束。

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

## 9. TargetPortfolio 读写合同

writer 生成 schema 2.0，仅包含通用 target 字段和可选 planning priority；策略诊断单独保存。
reporting.readers.load_target 接受 1.0 / 2.0；读取 1.0 时将 rank 转换为 planning priority。
这属于当前输入合同，writer 不生成 1.0。

## 10. Reporting

BacktestResultAssembler 负责把 BacktestResult 组装成 Arrow tables、metrics 和 period metrics。ResultArtifactPublisher 负责 Schema 校验、文件编码和 Artifact 发布；Markdown renderer 只格式化已经组装的事实。

`result show --format summary|markdown|json` 只读取已发布 Artifact，不重新计算策略或指标。

## 11. Failure Diagnostic

Backtest 和 Daily 命令可用 `--failure-report` 发布独立 Failure Diagnostic。它记录 run id、mode、失败 stage、错误和脱敏 input references。失败产物与成功 Result 是不同 artifact type，不会混合。

## 12. Raw Collection 与 Batch Report

Collection 是目录型 Raw Artifact 的索引，索引/报告本身不带 Artifact Manifest。

- collection.json：schema_version、complete、expected_partitions、entries；entry 包含 partition_id、generation、path、identity 和 manifest_sha256。
- batch-result.json：run/mode/fingerprint、开始/结束时间、complete、任务状态/诊断、runtime 配置和 metrics。
- artifacts/<partition>/<generation>：普通 Raw Artifact；每次刷新发布新目录。

两个 JSON 按对应 Schema 校验，经临时文件、fsync 和 os.replace 原子写入；协调线程先写索引再写报告。
同目录 OS 文件锁约束单写入者；读取验证相对路径、安全边界、哈希及预期覆盖。
失败/缺口 Collection 不能展开为 Research 输入。注册与代次选择见
[数据设计](02-data-and-research.md#4-raw-collection)。
