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
- `backtest opt`
- `backtest opt-report`
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

解析 StrategySpec 和 declaration，创建 ResearchSession，按请求区间最后一个交易日
执行 readiness 并限定自定义因子日期；请求区间可以以休市日为端点，无交易日时失败。
Engine 和 resolved config 保留请求的 start/end，估值与成交仅发生在区间内交易日。
stateful strategy 自动获得 StrategyStateReducer。完成后交给 ResultArtifactPublisher。

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

## 11. 参数优化

`backtest opt` 必须提供 `--config`、`--opt-config` 和 `--output`。沿用 range 日期、
custom factor、existing 和 failure report 参数，增加 workers、dry-run、resume 和
shard-count/shard-index。CLI 分派到 Optimization 应用服务，不进入普通运行分支。
日期优先级为 CLI > 优化配置 > 普通配置；未搜索的策略和执行字段继承普通配置。
优化 TOML 的固定覆盖与候选必须通过 Registry 校验。

Application 为每个候选构造独立 BACKTEST context，清空附加 analysis periods，
按训练窗口调用 StrategyWorkflowService。优化库处理通用网格、evaluator 多进程调用、
检查点和排名，不直接依赖 Application 或具体策略。指标读取现有结果；trade_count
统计 BUY/SELL 实际成交记录。所有输入与实验输出必须互不包含，包括 opt-config。

`backtest opt-report` 接受重复 `--input`、`--output` 和 `--existing`，验证兼容性后
合并去重结果。优化根目录为实验记录，其下试验目录遵循原 Result Artifact 合同。
提供可选 `--opt-config` 时，应用层切换到单个完整原始实验的复筛流程；只允许目标、
约束及 workers 变化，使用保存的实际训练区间。校验完整候选、检查点、全部试验及
基准后重新排名，不调用 StrategyWorkflowService 或工作进程。派生 Manifest 记录
`operation = "reselect"`、原回测代码指纹和来源身份，CSV 与最佳结果保存原试验路径；
不作为续跑、合并或复筛输入。`--dry-run` 支持合并和复筛，禁止日志及失败报告写入。
试验失败或合并不完整返回 5；中断返回 130。运行协议见
[参数优化指南](../optimization.md)。
