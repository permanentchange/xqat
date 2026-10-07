# 应用层、配置与 CLI

## 1. CLI 命令树

当前公开命令：

- `self-check --offline`
- `data capabilities`
- `data fetch`
- `data fetch-batch`
- `data collection index`
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

StrategyWorkflowService 编排策略运行；数据、因子和 state 命令由 CLI 调用各自服务。

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

## 9. 数据配置与命令

数据获取使用独立 ProviderConfig 和 BatchPlan，均不要求策略配置的 schema_version 字段。
Provider 默认 200/min、4 workers、5 attempts、30 秒 timeout，可配置已注册 API 的独立预算。
BatchPlan 配置交易日期、datasets、财务期、辅助请求和刷新窗口。字段及约束见
[数据操作指南](../data.md)。

| 命令 | 输入与行为 |
| --- | --- |
| data fetch | dataset/start/end/output；可指定单个 security-id、VIP api/period、provider-config、existing |
| data fetch-batch | plan/output；可指定 provider-config、bootstrap/update mode、离线 dry-run |
| data collection index | input 目录；校验并索引已存在 Raw |
| data capabilities | output；可指定 trade-date 和 provider-config，探测普通注册接口 |
| data build | raw-collection 或重复 raw-root，互斥；config/output/existing |
| data update | base 和可选重复 raw-root；config/output/existing，不接受 raw-collection |
| data check-raw / check-research | input/report；只读检查后写新报告 |

Research build config 只消费 schema_version=1.0、start_date/end_date 和 strategy.csi300_etf_id。
fetch-batch 不使用 Artifact existing 选项，成功分区通过身份/哈希验证续跑，刷新写新代次。
Collection 完整性验证失败时拒绝 build。dry-run 不创建 Runtime、不读取 Token、不写目录。

## 10. 退出码与错误边界

| 退出码 | 当前用途 |
| --- | --- |
| 0 | 成功；无参数显示顶层 help |
| 2 | argparse、输入输出路径、配置/参数，以及命令内部处理的普通 OSError/ValueError |
| 3 | Raw/Research/factor 检查未通过、self-check 失败、SecurityError 或策略指定的 readiness 错误 |
| 4 | Artifact 发布/输出已存在、日志写入或 result 读取失败 |
| 5 | TushareError，或 fetch-batch 的 failed/interrupted/incomplete |
| 10 | 未处理异常；仅输出 correlation id |

data build 的 Collection 校验错误由数据命令映射到 2；是否是 provider 错误以实际异常类型为准。
Backtest/Daily 的 DATA_REQUIRED_MISSING、DATA_COVERAGE_INSUFFICIENT、STRATEGY_WARMUP_INSUFFICIENT
和 FACTOR_COVERAGE_INSUFFICIENT 返回 3；其余已捕获配置/数据错误返回 2。

Backtest/Daily 可用 --failure-report 发布独立 Failure Diagnostic。
未处理异常不会输出 provider 原文或完整 traceback；结构化日志通过全局 --log-file 写入。
