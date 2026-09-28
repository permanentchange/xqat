# 03 应用层、配置与 CLI 详细设计

## 1. CLI 命令树

当前公开命令：

- `self-check --offline`
- `data capabilities`
- `data fetch`
- `data check-raw`
- `data build`
- `data update`
- `data check-research`
- `factor check`
- `backtest run`
- `daily target`
- `daily decide`
- `daily advise`
- `state init`
- `state apply-fill`
- `state apply-stock-adjustment`
- `result show`

全局可选 `--log-file` 只记录脱敏 JSONL 运行事件。

## 2. RunMode

RunMode 当前为：

- BACKTEST
- DAILY_TARGET
- DAILY_DECISION
- DAILY_ADVICE

Backtest 使用 start/end date；所有 Daily mode 必须有 decision date。

## 3. TOML 配置

配置 `schema_version` 固定为 `1.0`。顶层允许字段：

`mode`, `strategy_id`, `strategy_version`, `research_artifact`, `start_date`, `end_date`, `decision_date`, `output`, `account_snapshot`, `previous_target`, `strategy_state`, `custom_factors`, `strategy`, `execution`, `analysis_periods`。

未知字段直接失败。配置中任何 key 命中 token/secret/password/credential/authorization 都被拒绝。

Strategy 参数由 Strategy Registry 中对应 normalizer 解析，而不是由 Config 模块维护策略专用字段。

## 4. Execution assumptions

当前固定支持：

| 字段 | 默认值 |
| --- | --- |
| initial_cash | 1,000,000 |
| price_model | NEXT_OPEN |
| slippage_bps | 10 |
| max_volume_participation | 0.10 |
| fee_schedule_id | cn_cash_market_default_v1 |
| dividend_tax_model | PROVIDER_AFTER_TAX |
| dividend_tax_rate | 0 |

`price_model` 当前只支持 NEXT_OPEN；`fee_schedule_id` 当前只支持默认中国现金市场费用模型。Dividend tax model 支持 PROVIDER_AFTER_TAX 或 FLAT_RATE。

## 5. ResolvedRunContext

`resolve_config` 完成以下工作：

1. 读取 TOML 和拒绝秘密字段；
2. 校验 RunMode；
3. 通过 Strategy Registry 解析策略并标准化参数；
4. 标准化 execution assumptions；
5. 解析日期、Research Manifest SHA、custom factors 和可选 account/target/state；
6. 校验 analysis periods；
7. 校验输入与输出路径完全不重叠；
8. 生成 ResolvedRunContext。

Result 中保存的是 resolved config，而不是未经校验的原始 TOML。

## 6. StrategyWorkflowService

Application 层是 CLI 与领域服务之间的唯一用例编排入口。

### backtest

解析 StrategySpec 和 declaration，创建 ResearchSession，执行 readiness，构造 strategy；stateful strategy 自动获得 StrategyStateReducer。完成后交给 ResultArtifactPublisher。

### daily target

只允许不要求 confirmed state 的 allocation 策略。可读取 previous target 来计算 transition；生成 target 后执行通用 target invariants 和 weekly product policy 校验。

### daily decide

只允许要求 confirmed state 的策略。必须读取 strategy state，并校验 state identity 与 strategy identity 一致。AccountSnapshot 可选，只用于一致性检查。

### daily advise

读取已发布 target；可选读取 AccountSnapshot；使用 target decision date 的 raw close 作为参考价格，结合 lot rule 和费用模型生成 TradeAdvice。

## 7. State 命令

`state init` 为已注册策略创建空 StrategyStateSnapshot。

`state apply-fill` 使用已确认的 execution date、side、quantity、execution price 和费用构造 TradeFilled，再交给 StrategyStateReducer。

`state apply-stock-adjustment` 只接受 STOCK_DISTRIBUTION 或 SPLIT 的已确认新增数量，保持成本基础不变并调整 last buy price。

State 文件写入前执行 strategy_state JSON Schema 校验，并使用“目标不存在才写入”的原子文件写法。

## 8. 输出覆盖策略

Artifact 命令的 `--existing` 支持 error / skip / overwrite。普通 state/check 报告使用显式新路径；目标已存在时不会静默覆盖。

CLI 在解析前后都检查输入/输出路径图，拒绝相等、包含或被包含关系，避免输出破坏输入。

## 9. 退出码与错误边界

CLI 将配置/参数类错误映射为退出码 2，数据 readiness/安全类常见错误映射为 3，Artifact 发布错误为 4，provider 错误为 5，未处理异常只输出 correlation id 并返回 10。

Backtest/Daily 可通过 `--failure-report` 单独发布 Failure Diagnostic Artifact；失败诊断不伪装成成功 Result。
