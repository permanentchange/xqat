# 16 物理 Schema 注册表

本文件是 Artifact 信封、运行输入以及 Result 结构化文件物理 Schema 的唯一权威定义。统一研究表由 [05](05-research-data-and-factors.md) 定义，用户自定义因子输入由 [06](06-research-access.md) 定义；本文件登记其版本但不复制字段定义。

## 1. 版本注册

| Schema ID | 当前版本 | 物理格式 | 生产者 | 消费者 |
|---|---:|---|---|---|
| `artifact_manifest` | 1.0 | JSON | ArtifactPublisher | 全部读取方 |
| `raw_request` | 1.0 | JSON | TushareAdapter | Raw 校验/审计 |
| `raw_response` | 1.0 | JSONL.GZ | TushareAdapter | DataPreparation |
| `research_tables` | 1.0 | Parquet | DataPreparation | ResearchSession |
| `custom_factor_input` | 1.0 | CSV | 用户 | CustomFactorView |
| `account_snapshot` | 1.0 | JSON | 用户 | DailyAdviceService |
| `resolved_config` | 1.0 | JSON | Use Case | 报告/复现 |
| `target_portfolio` | 1.0 | JSON | Strategy/Annotator | Rebalance/Result |
| `target_positions` | 1.0 | CSV | ResultWriter | 用户 |
| `target_history` | 1.0 | Parquet | BacktestEngine | Performance/报告 |
| `portfolio_daily` | 1.0 | Parquet | SimulatedAccount | Performance/报告 |
| `trades` | 1.0 | Parquet | ExecutionSimulator | Performance/报告 |
| `unfilled` | 1.0 | Parquet | ExecutionSimulator | 报告 |
| `metrics` | 1.0 | JSON | PerformanceAnalyzer | 报告 |
| `period_metrics` | 1.0 | CSV | PerformanceAnalyzer | 报告 |
| `trade_advice` | 1.0 | JSON/CSV | DailyAdviceService | 用户 |
| `issues` | 1.0 | JSON | 全部模块 | 报告/CLI |
| `failure_diagnostic` | 1.0 | JSON | Use Case | 用户/CLI |

版本使用 `major.minor`。未知 Major 必须拒绝；同 Major 的读取方必须忽略未知可选字段，但生产者仍按本注册表固定排序输出。Schema 的任何字段删除、改名、类型改变或语义改变均升级 Major；只增加可选字段可升级 Minor。

## 2. 通用物理类型与规范

| 逻辑类型 | JSON | CSV | Parquet | 规则 |
|---|---|---|---|---|
| date | ISO `YYYY-MM-DD` string | ISO string | `date32` | 无时区。 |
| datetime | RFC 3339 string | RFC 3339 string | `timestamp[us, UTC]` | 正式结果统一 UTC；用户输入必须带时区。 |
| money | JSON number | 十进制定点文本 | `decimal128(20,4)` | CNY；禁止指数形式，计算用 Decimal。 |
| price | JSON number | 十进制定点文本 | `decimal128(18,6)` | CNY/份或 CNY/股。 |
| weight/rate | JSON number | 十进制定点文本 | `decimal128(18,12)` | 比例而非百分数。 |
| quantity | integer | 十进制整数 | `int64` | 股/份；非负，卖出方向由 side 表达。 |
| score/factor | JSON number | round-trip float 文本 | `float64` | 必须有限，禁止 NaN/Inf。 |
| SHA-256 | 64 位小写十六进制 string | 同左 | `string` | 对文件原始字节计算。 |
| code/id | string | UTF-8 string | `string` | 不做本地化。 |
| string list | JSON string array | JSON array 文本 | `list<string>` | 元素顺序有业务意义时保持稳定。 |

JSON 使用 UTF-8、无 BOM、键按字典序、两空格缩进、LF 换行，文件末尾一个换行；解析数字时必须直接构造 Decimal，不能先经过二进制浮点。CSV 使用 UTF-8、无 BOM、逗号、双引号转义、LF，首行为本文件给出的精确列顺序；空值为空字段，空字符串必须写成 `""`。Parquet 使用 ZSTD、禁止写 pandas index，列顺序固定为表中顺序。

所有日期范围两端均包含。主键列不得为空。输出 JSON 不允许 NaN/Infinity。输入类 JSON/CSV 拒绝未知字段；输出类同 Major 读取方忽略未知可选字段。

## 3. Artifact Manifest 1.0

根对象字段按下表输出：

| 字段 | 类型 | 必填 | 约束 |
|---|---|---:|---|
| `schema_version` | string | 是 | 固定 `1.0`。 |
| `artifact_type` | string enum | 是 | `RAW_DATA`、`RESEARCH_DATA`、`BACKTEST_RESULT`、`DAILY_TARGET_RESULT`、`DAILY_ADVICE_RESULT`、`FAILURE_DIAGNOSTIC`。 |
| `artifact_id` | string | 是 | UUIDv7；不参与业务结果。 |
| `created_at` | datetime | 是 | UTC。 |
| `producer` | Producer | 是 | 生产者名称和版本。 |
| `run` | RunDescriptor/null | 条件 | Result 和 Failure 必填，Raw/Research 为 null。 |
| `inputs` | list[InputReference] | 是 | 可为空，按 `alias` 升序。 |
| `date_scope` | DateScope | 是 | `start`、`end`，允许均为 null 的非日期 Artifact。 |
| `files` | list[FileEntry] | 是 | 不含根 Manifest 自身，按 `path` 升序。 |
| `issues` | list[Issue] | 是 | 可为空，稳定排序规则见 12。 |
| `limitations` | list[string] | 是 | 去重后按代码升序。 |

`Producer`：`name:string`、`version:string`。`DateScope`：`start:date/null`、`end:date/null`，必须同空或 `start<=end`。

`InputReference` 字段：`alias:string`、`artifact_type:string`、`manifest_sha256:string`、`path_hint:string/null`。`path_hint` 只能是用户提供的别名或相对路径，禁止秘密、Token、环境变量值和不必要的本机绝对路径。

`FileEntry` 字段：`path:string`、`media_type:string`、`size_bytes:int64`、`sha256:string`、`row_count:int64/null`、`schema_id:string/null`、`schema_version:string/null`。路径必须是使用 `/` 的 Artifact 根相对路径，禁止 `..`、绝对路径和符号链接逃逸。

`RunDescriptor` 字段：`run_id:string`、`run_status:string`、`mode:RunMode`、`strategy_id:string`、`strategy_version:string`、`parameters:object`、`date_scope:DateScope`、`custom_factors:list[CustomFactorReference]`、`account_input:AccountInputReference/null`、`execution_assumptions:ExecutionAssumptions`、`generated_at:datetime`。

`CustomFactorReference`：`factor_name`、`file_sha256`、`date_start`、`date_end`、`security_count`、`coverage_ratio`、`visibility_verified=false`。`AccountInputReference`：`file_sha256`、`as_of`、`scope_completeness`、`positions_completeness`，不复制账户金额或持仓。

`ExecutionAssumptions`：`price_model`、`slippage_bps`、`max_volume_participation`、`fee_schedule_id`、`fee_schedule_effective_from`、`commission_rate`、`minimum_commission`、`transfer_fee_rate`、`sell_stamp_duty_rate`、`buy_lot_size`、`sell_lot_size`、`price_tick`。适用数值分别使用 rate、money、quantity 和 price 类型，不允许任意扩展键。

## 4. Raw 文件

### 4.1 request.json (`raw_request` 1.0)

字段：`schema_version`、`request_id`、`provider`、`api_name`、`requested_fields`、`parameters`、`page_number`、`offset`、`limit`、`started_at`、`completed_at`、`attempt_count`、`response_row_count`。`parameters` 只允许该接口登记的非秘密参数；Token 和认证头禁止出现。日期仍按供应商请求格式保存，以忠实记录请求。

### 4.2 response.jsonl.gz (`raw_response` 1.0)

Gzip 内为 UTF-8 JSON Lines，每行一个供应商原始记录；键名和值不改写。每行附加保留键 `_xqat_request_id`、`_xqat_page_number`、`_xqat_row_number`，这三个键若与供应商字段冲突则请求失败。业务唯一键、分页完整性和字段集合由 [04](04-raw-data.md) 的接口注册表校验。

## 5. AccountSnapshot 1.0

物理字段与 [共享合同 §6](01-shared-contracts.md#6-accountsnapshot) 完全一致，固定输出顺序为：`schema_version`、`as_of`、`currency`、`account_scope`、`scope_completeness`、`positions_completeness`、`available_cash`、`managed_total_assets`、`excluded_asset_value`、`source_note`、`positions`。

每个 Position 固定字段顺序：`security_id`、`quantity`、`sellable_quantity`、`market_value`、`reference_price`、`reference_price_date`。可空字段允许 JSON null 或省略，规范化后必须补成显式 null。条件约束由共享合同和 [11](11-daily-run-and-advice.md) 定义。

## 6. resolved_config.json 1.0

字段与 `ResolvedRunContext` 对应，但路径保存为用户输入别名和 SHA-256，不保存 Token 或秘密环境变量。固定字段：`schema_version`、`mode`、`strategy_id`、`strategy_version`、`parameters`、`research_input`、`custom_factor_inputs`、`decision_date`、`start_date`、`end_date`、`account_input`、`previous_target_input`、`output_alias`、`execution_assumptions`。不保存 `run_id`、`generated_at` 等非业务值，以便配置摘要只反映业务输入。

## 7. Target 文件

### 7.1 target_portfolio.json 1.0

字段顺序：`schema_version`、`strategy_id`、`strategy_version`、`decision_date`、`effective_from`、`market_regime`、`theoretical_drawdown`、`drawdown_window_trade_days`、`drawdown_observations`、`drawdown_overlay_level`、`positions`、`transition_records`、`cash_weight`、`explanations`。嵌套字段与 [共享合同 §5](01-shared-contracts.md#5-targetportfolio) 一致。`drawdown_window_trade_days` 必须为 60，`drawdown_observations` 必须为 1–60。Positions 按 `asset_type`（A_SHARE 在前、CSI300_ETF 在后）、`target_weight DESC`、`security_id ASC`；transition_records 按 `security_id ASC`；explanations 按 `code ASC`。

### 7.2 target_positions.csv 1.0

| 列（固定顺序） | 类型 | 可空 |
|---|---|---:|
| `decision_date` | date | 否 |
| `effective_from` | date | 否 |
| `security_id` | code | 否 |
| `asset_type` | AssetType | 否 |
| `target_weight` | weight | 否 |
| `rank` | int32 | 是 |
| `score` | score | 是 |
| `holding_age_weeks` | int32 | 是 |
| `transition` | TargetTransition | 是 |
| `explanation_codes` | string list | 否 |

只包含非现金 positions；现金权重在 JSON 和报告中表达。

### 7.3 target_history.parquet 1.0

主键：`decision_date, security_id`。每个决策日包含所有非现金目标和一行 `security_id=CASH`、`asset_type=CASH`。

| 列（固定顺序） | Parquet 类型 | 可空 |
|---|---|---:|
| `decision_date` | date32 | 否 |
| `effective_from` | date32 | 否 |
| `strategy_id` | string | 否 |
| `strategy_version` | string | 否 |
| `market_regime` | string | 否 |
| `theoretical_drawdown` | decimal128(18,12) | 否 |
| `drawdown_window_trade_days` | int32 | 否 |
| `drawdown_observations` | int32 | 否 |
| `drawdown_overlay_level` | string | 否 |
| `security_id` | string | 否 |
| `asset_type` | string | 否 |
| `target_weight` | decimal128(18,12) | 否 |
| `rank` | int32 | 是 |
| `score` | float64 | 是 |
| `holding_age_weeks` | int32 | 是 |
| `transition` | string | 是 |
| `explanation_codes` | list<string> | 否 |

现金行的 rank、score、holding_age_weeks、transition 为空。每个决策日权重和必须在 `1 ± 1e-10` 内。

## 8. Backtest 表

### 8.1 portfolio_daily.parquet 1.0

主键：`valuation_date`。

| 列 | Parquet 类型 | 可空 |
|---|---|---:|
| `valuation_date` | date32 | 否 |
| `nav` | decimal128(20,4) | 否 |
| `daily_return` | decimal128(18,12) | 是（首日） |
| `running_peak` | decimal128(20,4) | 否 |
| `drawdown` | decimal128(18,12) | 否 |
| `cash_available` | decimal128(20,4) | 否 |
| `cash_receivable` | decimal128(20,4) | 否 |
| `stock_market_value` | decimal128(20,4) | 否 |
| `etf_market_value` | decimal128(20,4) | 否 |
| `stock_return_contribution` | decimal128(18,12) | 是（首日） |
| `etf_return_contribution` | decimal128(18,12) | 是（首日） |
| `cash_cost_contribution` | decimal128(18,12) | 是（首日） |
| `gross_exposure` | decimal128(18,12) | 否 |
| `one_way_turnover` | decimal128(18,12) | 否 |
| `two_way_adjustment_turnover` | decimal128(18,12) | 否 |

账户恒等式：`nav = cash_available + cash_receivable + stock_market_value + etf_market_value`，允许的 Decimal 舍入误差不超过 0.0001 CNY。

### 8.2 trades.parquet 1.0

主键：`execution_id`。稳定排序：`execution_date, priority, security_id, execution_id`。

| 列 | Parquet 类型 | 可空 |
|---|---|---:|
| `execution_id` | string | 否 |
| `instruction_id` | string | 否 |
| `decision_date` | date32 | 否 |
| `execution_date` | date32 | 否 |
| `security_id` | string | 否 |
| `side` | string | 否 |
| `priority` | int32 | 否 |
| `requested_quantity` | int64 | 否 |
| `filled_quantity` | int64 | 否 |
| `execution_price` | decimal128(18,6) | 否 |
| `reference_price` | decimal128(18,6) | 否 |
| `gross_amount` | decimal128(20,4) | 否 |
| `commission` | decimal128(20,4) | 否 |
| `transfer_fee` | decimal128(20,4) | 否 |
| `stamp_duty` | decimal128(20,4) | 否 |
| `total_fees` | decimal128(20,4) | 否 |
| `slippage_cost` | decimal128(20,4) | 否 |
| `status` | string | 否 |

`total_fees = commission + transfer_fee + stamp_duty`；FILLED/PARTIALLY_FILLED 的 filled_quantity 必须大于零。

### 8.3 unfilled.parquet 1.0

主键：`instruction_id, execution_date, reason`。

| 列 | Parquet 类型 | 可空 |
|---|---|---:|
| `instruction_id` | string | 否 |
| `decision_date` | date32 | 否 |
| `execution_date` | date32 | 否 |
| `security_id` | string | 否 |
| `side` | string | 否 |
| `requested_quantity` | int64 | 否 |
| `filled_quantity` | int64 | 否 |
| `unfilled_quantity` | int64 | 否 |
| `reason` | UnfilledReason | 否 |
| `evidence` | string | 否 |

`unfilled_quantity = requested_quantity - filled_quantity > 0`。`evidence` 是键按字典序的紧凑 JSON 对象文本，不含秘密。

## 9. 绩效文件

### 9.1 metrics.json 1.0

字段：`schema_version`、`formula_version`、`date_start`、`date_end`、`valuation_points`、`return_intervals`、`initial_nav`、`final_nav`、`cumulative_return`、`annualized_return`、`annualized_volatility`、`max_drawdown`、`max_drawdown_peak_date`、`max_drawdown_trough_date`、`max_drawdown_recovery_date`、`sharpe`、`calmar`、`one_way_turnover`、`two_way_adjustment_turnover`、`total_commission`、`total_transfer_fee`、`total_stamp_duty`、`total_slippage_cost`、`stock_return_contribution`、`etf_return_contribution`、`cash_cost_contribution`、`limitations`。指标不可计算时为 null，并必须有稳定 limitation code。

### 9.2 period_metrics.csv 1.0

固定列：`period_type,period_label,start_date,end_date,valuation_points,return_intervals,cumulative_return,annualized_return,annualized_volatility,max_drawdown,sharpe,calmar,one_way_turnover,total_fees,total_slippage_cost,limitations`。主键 `period_type,period_label`；按 `start_date, period_type, period_label` 排序。

## 10. Trade Advice 文件

### 10.1 trade_advice.json 1.0

字段：`schema_version`、`decision_date`、`effective_from`、`account_as_of`、`account_scope`、`scope_completeness`、`positions_completeness`、`managed_total_assets`、`available_cash`、`items`、`issues`、`limitations`、`non_order_disclaimer`。Items 按 `action` 的 SELL/DECREASE/BUY/INCREASE/HOLD/UNRESOLVED 顺序，再按 `security_id` 升序。

Item 字段与 [共享合同 §7](01-shared-contracts.md#7-rebalanceplanexecution-与-tradeadvice) 一致；所有可空金额或数量字段必须显式为 null，且 `limitations` 说明原因。

### 10.2 trade_advice.csv 1.0

固定列：`decision_date,effective_from,security_id,action,current_quantity,target_weight,target_amount,theoretical_target_quantity,suggested_quantity,max_confirmed_sell_quantity,unresolved_quantity,reference_price,reference_price_date,reason_codes,limitations`。CSV 是 JSON items 的扁平化视图，不增加或重新计算业务字段。

## 11. issues.json 1.0

根对象字段：`schema_version`、`issues`。Issue 字段固定顺序：`code`、`severity`、`stage`、`scope`、`decision_date`、`security_id`、`field`、`message`、`evidence`、`suggested_action`。排序：`severity`（ERROR 在前）、`stage`、`code`、`decision_date`、`security_id`、`field`；null 排在非 null 前。代码目录由 [12](12-results-and-issues.md) 唯一定义。

## 12. Failure Diagnostic 1.0

独立 Artifact 只含 `manifest.json`、`failure.json`、`report.md`。`failure.json` 字段：`schema_version`、`run_id`、`mode`、`failed_stage`、`primary_issue`、`issues`、`input_references`、`suggested_actions`、`generated_at`。不得包含 Target、净值、交易、建议等伪成功结果；输入只保存别名与摘要。

## 13. 生产者—消费者合同测试

每个 Schema 必须有：有效黄金文件、每个必填字段缺失样例、未知 Major、非法枚举、非法 null、重复主键、错误排序、非有限数、Decimal 越界和额外输入字段测试。合同测试必须先用生产者写出，再由真实消费者读取；只分别测试序列化器和解析器不足以证明兼容。

报告只能读取这里定义的结构化文件。报告展示值必须与源字段逐值相同，允许格式化小数位但不能重新计算产生另一套事实。
