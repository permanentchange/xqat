# 12 结果、报告与问题语义

## 1. 成功、失败与业务未实现

- `SUCCESS`：当前用例可靠完成，可以包含 WARNING 和正常未成交。
- `FAILED`：无法可靠完成；不发布正式 Result Artifact。
- 停牌、锁板、现金不足、交易单位偏差、可卖不足属于成功结果中的业务事实，不等同系统失败。
- Backtest 不允许部分成功；Daily Advice 允许字段级降级，但不能污染已知值。

## 2. Issue 代码目录

Issue 字段见 [共享合同](01-shared-contracts.md#8-issue)。以下代码是唯一权威目录：

| 代码 | 默认级别 | 含义 |
|---|---|---|
| `CONFIG_SCHEMA_INVALID` | ERROR | 配置结构或版本非法。 |
| `CONFIG_VALUE_INVALID` | ERROR | 参数范围或交叉约束非法。 |
| `CONFIG_OUTPUT_REQUIRED` | ERROR | 正式运行未解析出明确输出。 |
| `DATA_PROVIDER_PERMISSION_DENIED` | ERROR | 当前依赖接口权限不足。 |
| `DATA_PROVIDER_TEMPORARY_FAILURE` | ERROR | 重试耗尽。 |
| `DATA_PROVIDER_EMPTY_RESPONSE` | ERROR | 必需请求得到异常空响应。 |
| `DATA_PROVIDER_SCHEMA_MISMATCH` | ERROR | 供应商返回字段与接口注册表不兼容。 |
| `DATA_PROVIDER_CAPABILITY_UNVERIFIED` | WARNING | 尚未完成真实 Token 的接口能力探测。 |
| `DATA_INPUT_CORRUPT` | ERROR | Artifact、压缩或记录损坏。 |
| `DATA_REQUIRED_MISSING` | ERROR | 当前依赖字段或记录缺失。 |
| `DATA_COVERAGE_INSUFFICIENT` | ERROR | 当前策略覆盖低于阈值。 |
| `DATA_VISIBILITY_UNKNOWN` | ERROR | 无法确定历史可见时点。 |
| `DATA_CONFLICT` | ERROR | 同业务键存在冲突事实。 |
| `DATA_STALE_OR_LIMITED` | WARNING | 数据可用但存在明确时效或覆盖限制。 |
| `DATA_ST_COVERAGE_INSUFFICIENT` | ERROR | 历史 ST 覆盖不能支持请求区间。 |
| `DATA_ETF_ADJ_COVERAGE_INSUFFICIENT` | ERROR | ETF 日线与复权因子覆盖不一致。 |
| `DATA_FINANCIAL_VERSION_AMBIGUOUS` | ERROR | 财务公告或修订顺序不能唯一确定。 |
| `FACTOR_SCHEMA_INVALID` | ERROR | 用户因子 Schema 非法。 |
| `FACTOR_DUPLICATE_KEY` | ERROR | 因子业务键重复。 |
| `FACTOR_COVERAGE_INSUFFICIENT` | ERROR | 必需因子覆盖不足。 |
| `FACTOR_VALUE_NONFINITE` | ERROR | 因子值为 NaN 或无穷大。 |
| `FACTOR_USER_VISIBILITY_UNVERIFIED` | WARNING | 用户因子的未来信息约束未验证。 |
| `STRATEGY_PARAMETER_INVALID` | ERROR | 策略参数非法。 |
| `STRATEGY_WARMUP_INSUFFICIENT` | ERROR | 历史预热无法确定状态。 |
| `STRATEGY_UNIVERSE_REDUCED` | WARNING | 个股缺失规则导致股票池缩小但仍达阈值。 |
| `STRATEGY_HISTORY_SLICE_VIOLATION` | ERROR | 历史重放请求了 H 日之后可见的事实。 |
| `PORTFOLIO_INVALID_TARGET` | ERROR | 目标权重或资产合同非法。 |
| `PORTFOLIO_LOT_ROUNDING_RESIDUAL` | WARNING | 交易单位产生目标偏差。 |
| `PORTFOLIO_REFERENCE_PRICE_MISSING` | WARNING | 个别目标数量缺少模式规定的参考价格。 |
| `BACKTEST_VALUATION_MISSING` | ERROR | 模拟账户无法可靠估值。 |
| `BACKTEST_ACCOUNT_CONSERVATION_BROKEN` | ERROR | 账户事件守恒失败。 |
| `BACKTEST_CORPORATE_ACTION_UNSUPPORTED` | ERROR | 持仓遇到无法可靠处理的公司行为。 |
| `BACKTEST_EXECUTION_CONSTRAINED` | WARNING | 存在未成交或部分成交。 |
| `PERFORMANCE_INSUFFICIENT_SAMPLE` | WARNING | 指标样本不足但可输出其余可靠指标。 |
| `PERFORMANCE_CONTRIBUTION_MISMATCH` | ERROR | 资产贡献之和与组合收益不守恒。 |
| `ADVICE_ACCOUNT_MISSING` | WARNING | 未提供账户快照。 |
| `ADVICE_ACCOUNT_VALUE_CONFLICT` | WARNING | 账户总资产与分项不一致。 |
| `ADVICE_CASH_UNKNOWN` | WARNING | 可用现金未知。 |
| `ADVICE_HOLDINGS_UNKNOWN` | WARNING | 当前持仓未知。 |
| `ADVICE_HOLDINGS_PARTIAL` | WARNING | 只提供部分持仓，未列证券不能视为零。 |
| `ADVICE_SCOPE_INCOMPLETE` | WARNING | 策略管理资产范围不完整。 |
| `ADVICE_ACCOUNT_STALE` | WARNING | 账户快照超过允许时效。 |
| `ADVICE_OUT_OF_SCOPE_ASSET_PRESENT` | WARNING | 快照声明存在策略范围外资产。 |
| `ADVICE_SELLABLE_UNKNOWN` | WARNING | 可卖数量未知。 |
| `ADVICE_REFERENCE_PRICE_MISSING` | WARNING | 个别参考价格未知。 |
| `ARTIFACT_OUTPUT_EXISTS` | ERROR | 目标已存在且未显式覆盖。 |
| `ARTIFACT_SCHEMA_INCOMPATIBLE` | ERROR | Artifact Major 版本不兼容。 |
| `ARTIFACT_HASH_MISMATCH` | ERROR | 文件摘要不匹配。 |
| `ARTIFACT_PUBLISH_FAILED` | ERROR | 原子发布失败。 |
| `SECURITY_SECRET_MISSING` | ERROR | 外部数据调用所需秘密缺失。 |
| `SECURITY_SECRET_EXPOSURE_BLOCKED` | ERROR | 检测到秘密即将写入普通输出。 |

同一代码默认级别不得由模块自行改变。若业务上下文不需要失败，应使用不同 Warning 代码，而不是降级 ERROR。

## 3. 原因码、解释码和限制码

三者不是 Issue，不带 severity，且使用独立命名空间：

### 3.1 Rebalance/Advice `reason_codes`

唯一允许值：`TARGET_INITIAL`、`TARGET_NEW`、`TARGET_EXIT`、`TARGET_INCREASE`、`TARGET_DECREASE`、`TARGET_RETAIN`、`LOT_ROUNDING_RESIDUAL`、`CASH_BUDGET_LIMITED`、`SELLABLE_LIMITED`、`ACCOUNT_FACTS_INCOMPLETE`、`REFERENCE_PRICE_MISSING`。按此目录顺序输出并去重。

### 3.2 Strategy `explanation_codes`

组合级：`REGIME_STRONG`、`REGIME_NEUTRAL`、`REGIME_WEAK`、`DRAWDOWN_NONE`、`DRAWDOWN_CAUTION`、`DRAWDOWN_DEFENSIVE`、`CUSTOM_FACTOR_ENABLED`。

证券级：`ENTRY_RANK`、`RETAIN_MIN_HOLD`、`RETAIN_EXIT_RANK`、`RETAIN_REENTERED`、`EXIT_HARD_FILTER`、`EXIT_RANK`、`EXIT_MAX_HOLD`、`EXIT_RISK_BUDGET`、`CUSTOM_FACTOR_APPLIED`。数值细节进入 Explanation.values，不把动态数值拼入代码。

### 3.3 字段级 `limitations`

唯一允许值：`ACCOUNT_NOT_PROVIDED`、`MANAGED_TOTAL_ASSETS_UNKNOWN`、`AVAILABLE_CASH_UNKNOWN`、`POSITIONS_UNKNOWN`、`POSITIONS_PARTIAL`、`ACCOUNT_SCOPE_INCOMPLETE`、`SELLABLE_QUANTITY_UNKNOWN`、`REFERENCE_PRICE_UNKNOWN`、`ACCOUNT_VALUE_CONFLICT`、`ACCOUNT_STALE`、`CUSTOM_FACTOR_VISIBILITY_UNVERIFIED`、`DIVIDEND_TAX_NOT_PERSONALIZED`、`SHORT_PERFORMANCE_SAMPLE`。按代码升序输出。

## 4. 失败诊断

失败时 CLI 必须输出首要错误、影响范围、证据摘要和建议动作。用户显式提供 `--failure-report PATH` 时，可以原子发布独立的 `artifact_type=FAILURE_DIAGNOSTIC`：

```text
failure-diagnostic/
  manifest.json
  failure.json
  report.md
```

物理字段见 [16 §12](16-physical-schemas.md#12-failure-diagnostic-10)。它不是 Result Artifact，不包含正常指标、目标或“成功”外观。

## 5. 报告结构

### 5.1 Backtest 报告

固定章节：运行身份 → 数据与因子 → 策略与参数 → 时间/成交/费用假设 → 收益与基准 → 风险 → 交易与未成交 → 持仓/现金 → 阶段表现 → 数据和模型限制 → 用户因子责任声明（适用时）。

### 5.2 Daily 报告

固定章节：决策日与有效日 → 市场状态和理论回撤 → 目标持仓 → 当前账户事实 → 参考建议 → 未解决差异 → Warning/限制 → 非订单声明。

报告层不得重新计算任何指标或数量，只读取结构化结果。

## 6. Result Manifest

使用 [ResultManifest](01-shared-contracts.md#9-resultmanifest)。`run_status` 在正式 Result 中只能为 SUCCESS；FAILED 只存在于 Failure Diagnostic。输入记录必须包含 Research、用户因子、上一目标和账户快照的路径别名与摘要，不能包含秘密或不必要的本机绝对路径。

## 7. Warning 聚合

- 同代码、同日期范围、同字段的大量证券问题在报告中聚合计数，并保留结构化明细。
- 不得因聚合丢失首尾日期、证券数量和代表样例。
- 影响研究结论的 Warning 必须同时出现在 `issues.json`、Manifest 摘要和 Markdown 报告限制章节。

## 8. 测试要点

- 每个 ERROR 代码至少有一个失败测试，每个 Advice Warning 有一个降级测试。
- 失败运行不能残留正式 Result 目录。
- 报告抽样值与结构化事实逐字段相同。
- 大量 Warning 聚合后总数等于结构化明细数。
- 自定义因子责任声明在所有使用该因子的正式结果中不可省略。
