# 01 共享合同

本文件是跨模块类型、状态、接口和字段名称的唯一权威定义。物理研究表字段由 [05 统一研究数据与系统因子](05-research-data-and-factors.md) 定义。

## 1. 核心枚举

| 枚举 | 允许值 |
|---|---|
| `RunMode` | `BACKTEST`、`DAILY_TARGET`、`DAILY_ADVICE` |
| `RunStatus` | `SUCCESS`、`FAILED` |
| `Severity` | `ERROR`、`WARNING` |
| `AssetType` | `A_SHARE`、`CSI300_ETF`、`CASH`、`CSI300_INDEX` |
| `MarketRegime` | `STRONG`、`NEUTRAL`、`WEAK` |
| `TargetTransition` | `NEW`、`RETAIN`、`INCREASE`、`DECREASE`、`EXIT`、`INITIAL` |
| `TradeAction` | `BUY`、`SELL`、`INCREASE`、`DECREASE`、`HOLD`、`UNRESOLVED` |
| `OrderSide` | `BUY`、`SELL` |
| `FillStatus` | `FILLED`、`PARTIALLY_FILLED`、`UNFILLED` |
| `UnfilledReason` | `SUSPENDED`、`LIMIT_UP_LOCKED`、`LIMIT_DOWN_LOCKED`、`NO_EXECUTION_PRICE`、`VOLUME_CAP`、`CASH_INSUFFICIENT`、`HOLDING_INSUFFICIENT`、`SELLABLE_INSUFFICIENT`、`LOT_TOO_SMALL`、`DELISTED` |
| `PriceKind` | `RESEARCH`、`EXECUTION`、`VALUATION`、`REFERENCE` |
| `OverwritePolicy` | `ERROR`、`SKIP`、`OVERWRITE` |
| `PositionCompleteness` | `COMPLETE`、`PARTIAL`、`UNKNOWN` |
| `AccountScope` | `STRATEGY_MANAGED` |
| `AccountScopeCompleteness` | `COMPLETE`、`PARTIAL`、`UNKNOWN` |

## 2. ResolvedRunContext

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `run_id` | string | 是 | UUIDv7 字符串。 |
| `mode` | RunMode | 是 | 当前用例。 |
| `strategy_id` | string | 是 | 内置策略稳定标识。 |
| `strategy_version` | string | 是 | 策略语义版本。 |
| `parameters` | object | 是 | 已验证、已填默认值的最终参数。 |
| `research_artifact_path` | absolute path | 是 | 明确研究数据输入。 |
| `research_artifact_sha256` | string | 是 | Manifest 摘要。 |
| `custom_factor_inputs` | array | 是 | 可为空；每项含路径和摘要。 |
| `decision_date` | date | 条件 | Daily 必填；Backtest 由日期区间逐日产生。 |
| `start_date` / `end_date` | date | 条件 | Backtest 必填。 |
| `account_snapshot_path` | absolute path | 条件 | Daily Advice 可选；缺失时仅生成可支持字段。 |
| `previous_target_path` | absolute path | 否 | 仅用于目标变化注释。 |
| `output_path` | absolute path | 是 | 用户明确指定。 |
| `execution_assumptions` | object | 是 | 价格、滑点、费用与成交规则的最终值。 |
| `generated_at` | datetime | 是 | 用例层注入 UTC 时间。 |

## 3. StrategyDeclaration 与 DataRequirement

`StrategyDeclaration` 字段：`strategy_id`、`strategy_version`、`parameter_schema_version`、`decision_frequency`、`lookback_trade_days`、`required_fields`、`required_system_factors`、`required_custom_factors`、`missing_policies`。

每个 `DataRequirement` 包含：

| 字段 | 类型 | 说明 |
|---|---|---|
| `requirement_id` | string | 稳定标识。 |
| `dataset` | string | 逻辑数据集名。 |
| `fields` | list[string] | 精确字段集合。 |
| `date_range` | DateRange | 依赖日期范围。 |
| `security_scope` | string | `ALL_ELIGIBLE`、显式证券集或基准。 |
| `required` | bool | 缺失是否失败。 |
| `missing_policy` | string | 只允许策略静态声明的策略。 |

## 4. ResearchDataView、ResearchDataSlice 与 CustomFactorView

三者是只读、关闭任意 SQL 和任意路径访问的 Protocol。`ResearchDataView` 表示本次 Strategy 调用可访问的完整、受限历史窗口；`ResearchDataSlice(H)` 表示严格按历史决策日 H 截断可见性的快照。Strategy 的历史重放必须先取得 Slice，不能用 D 日状态重放 H 日。

```python
class ResearchDataView(Protocol):
    @property
    def decision_date(self) -> date: ...
    @property
    def earliest_date(self) -> date: ...
    def trading_days(self, start: date, end: date) -> Sequence[date]: ...
    def slice(self, as_of_date: date) -> ResearchDataSlice: ...

class ResearchDataSlice(Protocol):
    @property
    def as_of_date(self) -> date: ...
    def universe(self) -> Sequence[SecuritySnapshot]: ...
    def history(self, security_ids: Sequence[str], fields: Sequence[str],
                start: date, end: date) -> Table: ...
    def system_factors(self, factor_ids: Sequence[str],
                       security_ids: Sequence[str]) -> Table: ...
    def benchmark_history(self, fields: Sequence[str],
                          start: date, end: date) -> Table: ...

class CustomFactorView(Protocol):
    @property
    def decision_date(self) -> date: ...
    def values_at(self, factor_date: date, factor_ids: Sequence[str],
                  security_ids: Sequence[str]) -> Table: ...
```

`slice(H)` 必须满足 `earliest_date <= H <= decision_date`，否则拒绝。Slice 的 `universe()`、`system_factors()` 和全部历史查询都必须在内部强制业务日期不晚于 H、版本 `available_from <= H`；`history(..., end)` 还必须拒绝 `end > H`。这条约束也适用于后来修订的财务记录：H 日 Slice 只能看到 H 日当时已经公开的版本。

`CustomFactorView.values_at(H, ...)` 只返回 `factor_date=H` 的精确值；只有 StrategyDeclaration 明确声明 `LAST_AVAILABLE(max_age_trade_days=N)` 时才能执行受限 as-of 连接。系统不为用户因子推断 `available_from`，但必须把未验证历史可见性的限制写入结果。

## 5. TargetPortfolio

头部字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `strategy_id` / `strategy_version` | string | 目标来源。 |
| `decision_date` | date | 决策日。 |
| `effective_from` | date | 下一交易日。 |
| `market_regime` | MarketRegime | ETF 风险状态。 |
| `theoretical_drawdown` | decimal | 理论基础组合回撤。 |
| `drawdown_window_trade_days` | int | 固定为 60。 |
| `drawdown_observations` | int | 本次回撤实际使用的正式重放估值点数，范围 1–60。 |
| `drawdown_overlay_level` | string | `NONE`、`CAUTION`、`DEFENSIVE`。 |
| `positions` | list[TargetPosition] | 非现金目标。 |
| `transition_records` | list[TargetTransitionRecord] | 可为空；下游根据显式上一目标填写。 |
| `cash_weight` | decimal | 现金目标。 |
| `explanations` | list[Explanation] | 组合级解释。 |

`TargetPosition` 字段：`security_id`、`asset_type`、`target_weight`、`rank`（ETF 可空）、`score`（ETF 可空）、`transition`（可空，由下游目标变化注释器填写）、`explanation_codes`、`holding_age_weeks`（ETF 可空）。

`TargetTransitionRecord` 字段：`security_id`、`previous_weight`、`current_weight`、`transition`、`explanation_codes`。EXIT 只存在于该列表，不以零权重证券伪装成当前目标持仓。

`Explanation` 字段：`code`、`message`、`values`；`values` 只含用于解释的标量，不含完整研究数据行。

合同约束：所有权重非负；非现金权重加 `cash_weight` 在 `1 ± 1e-10` 内；证券唯一；不得包含不支持资产；Strategy 不产生金额、股数或订单。

## 6. AccountSnapshot

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `schema_version` | string | 是 | 当前为 `1.0`；未知 Major 拒绝。 |
| `as_of` | datetime | 是 | 用户声明的快照时点。 |
| `currency` | string | 是 | 固定 `CNY`。 |
| `account_scope` | AccountScope | 是 | 固定 `STRATEGY_MANAGED`，表示所有金额只覆盖本策略管理资产。 |
| `scope_completeness` | AccountScopeCompleteness | 是 | 管理资产范围是否完整。 |
| `positions_completeness` | PositionCompleteness | 是 | 明确区分已确认空仓、部分持仓和持仓未知。 |
| `available_cash` | decimal/null | 否 | 可用现金。 |
| `managed_total_assets` | decimal/null | 否 | 仅含现金、A 股和指定沪深300ETF的策略管理总资产。 |
| `excluded_asset_value` | decimal/null | 否 | 券商账户中明确排除在策略管理范围外的资产价值，仅用于提示，不参与计算。 |
| `positions` | list[AccountPosition] | 是 | 可为空；含义由 `positions_completeness` 决定。 |
| `source_note` | string | 是 | 用户对券商事实来源的说明。 |

`AccountPosition` 字段：`security_id`、`quantity`、`sellable_quantity`（可空）、`market_value`（可空）、`reference_price`（可空）、`reference_price_date`（可空）。

条件约束：

- `positions_completeness=COMPLETE` 且 `positions=[]` 才表示用户确认空仓；
- `PARTIAL` 表示列表中的持仓为真但不能推断未列证券数量为零；`UNKNOWN` 要求 `positions=[]`；
- `scope_completeness` 非 `COMPLETE` 时不得由分项派生完整管理总资产，也不得生成全组合确定性买入数量；
- `managed_total_assets` 不得包含 `excluded_asset_value`，后者为空不代表范围外资产为零；
- AccountSnapshot 只描述一次性外部事实，任何服务不得补写、滚动或把建议反推为下一次真实持仓。

## 7. RebalancePlan、Execution 与 TradeAdvice

`RebalanceInstruction`：`security_id`、`side`、`current_quantity`、`theoretical_target_quantity`、`requested_quantity`、`lot_size`、`priority`、`reference_price`、`reason_codes`。

`ExecutionRecord`：`execution_date`、`security_id`、`side`、`requested_quantity`、`filled_quantity`、`execution_price`、`gross_amount`、`fees`、`status`、`unfilled_reason`（UnfilledReason，可空）。

`TradeAdviceItem`：`security_id`、`action`、`current_quantity`（可空）、`target_weight`、`target_amount`（可空）、`suggested_quantity`（可空）、`max_confirmed_sell_quantity`（可空）、`unresolved_quantity`（可空）、`reference_price`（可空）、`reference_price_date`（可空）、`reason_codes`、`limitations`。

## 8. Issue

| 字段 | 类型 | 说明 |
|---|---|---|
| `code` | string | 稳定错误码。 |
| `severity` | Severity | ERROR 或 WARNING。 |
| `stage` | string | 产生阶段。 |
| `scope` | string | 数据集、运行或字段范围。 |
| `decision_date` | date/null | 相关决策日。 |
| `security_id` | string/null | 相关证券。 |
| `field` | string/null | 相关字段。 |
| `message` | string | 用户可理解信息，不含秘密。 |
| `evidence` | object | 可序列化事实。 |
| `suggested_action` | string | 用户下一步。 |

错误码前缀固定为 `CONFIG_`、`DATA_`、`FACTOR_`、`STRATEGY_`、`PORTFOLIO_`、`BACKTEST_`、`ADVICE_`、`ARTIFACT_`、`SECURITY_`。

## 9. ResultManifest

字段：`schema_version`、`artifact_type`、`artifact_id`、`run_id`、`run_status`、`mode`、`strategy_id`、`strategy_version`、`parameters`、`date_scope`、`input_artifacts`、`custom_factors`、`account_input`、`execution_assumptions`、`generated_at`、`producer`、`files`、`issues`、`limitations`。完整类型、条件必填、Decimal 表达和文件 Schema 由 [16 物理 Schema 注册表](16-physical-schemas.md) 唯一定义。

`files` 中每项必须含相对路径、媒体类型、字节数和 SHA-256。Manifest 不得含凭证、环境变量值或完整本机秘密路径；对外报告中路径以输入别名展示。

## 10. 应用服务接口

```python
class Strategy(Protocol):
    declaration: StrategyDeclaration
    def generate_target(self, research: ResearchDataView,
                        custom: CustomFactorView,
                        parameters: Mapping[str, object]) -> TargetPortfolio: ...

class BacktestService(Protocol):
    def run(self, context: ResolvedRunContext) -> BacktestResult: ...

class DailyRunService(Protocol):
    def generate_target(self, context: ResolvedRunContext) -> TargetPortfolio: ...

class DailyAdviceService(Protocol):
    def generate(self, context: ResolvedRunContext,
                 target: TargetPortfolio,
                 account: AccountSnapshot | None) -> TradeAdvice: ...

class ArtifactPublisher(Protocol):
    def publish(self, output_path: Path,
                payload: StructuredResult,
                overwrite: OverwritePolicy) -> PublishedArtifact: ...
```

`BacktestResult`、`TradeAdvice` 和其他 `StructuredResult` 都由对应领域主体、`issues`、`limitations` 和生成上下文组成；`PublishedArtifact` 只返回正式路径、Manifest 摘要和文件摘要。`RunFailure` 只包含 ERROR Issue、脱敏上下文和建议动作，不携带伪成功领域结果。
