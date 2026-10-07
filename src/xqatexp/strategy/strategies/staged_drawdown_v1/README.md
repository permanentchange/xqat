# staged_drawdown_v1

## 1. 策略身份

- Strategy ID: `staged_drawdown_v1`
- Strategy Version: `1.0.0`
- 决策类型: `TradeIntentDecision`
- 决策频率: 每个交易日收盘后
- 生效时间: 下一交易日
- Strategy State: 必须使用 `CONFIRMED_EXECUTION_STATE`
- 主要实现:
  - `parameters.py`: 参数默认值、标准化与校验
  - `declaration.py`: 20 日价格历史与 state requirement
  - `strategy.py`: 首次建仓、下跌加仓、整体持仓止盈与 diagnostics

该策略用于单一证券的分阶段下跌买入和整体成本止盈。它不使用财务因子、股票横截面排名或市场 regime。

策略只产生 TradeIntent。Backtest 中的实际成交、费用、滑点、交易手数、现金约束和 T+1 由共享执行层处理；Daily 模式下，真实成交必须通过 confirmed fill 回写 Strategy State。

## 2. 核心逻辑

### 2.1 首次买入

当前无持仓时，取最近 `lookback_trade_days` 个交易日的 `research_close`。

默认 20 日窗口必须同时满足：

1. 累计收益率 <= -10%；
2. 窗口内最差单日收益率 >= -5%，即没有单日跌幅超过 5%；
3. 19 个日收益中至少 12 个为负。

公式：

```text
cumulative_return = last_research_close / first_research_close - 1

slow_decline =
    cumulative_return <= -cumulative_decline_threshold
    AND worst_daily_return >= -single_day_crash_threshold
    AND down_days >= minimum_down_days
```

满足后产生：

```text
signal = INITIAL_ENTRY
side   = BUY
sizing = InitialCapitalFraction(buy_fraction)
```

默认买入初始资金的 10%。

### 2.2 连续买入

已有持仓时，只有同时满足：

- 上一次 confirmed trade 是 BUY；
- 有 `last_buy_price`；
- 当前 `close_raw` 相比上一次实际买入成交价下跌达到阈值；

才允许追加。

默认条件：

```text
current_close <= last_buy_price * (1 - 0.10)
```

即相对上一次实际 BUY 成交价再跌 10%。

加仓产生：

```text
signal = ADD_ON_DECLINE
side   = BUY
```

标准档位仍是初始资金的 10%。

### 2.3 最大投入

Strategy State 保存当前持仓周期的 `cumulative_buy_notional`。

最大 gross 买入金额：

```text
maximum = initial_capital * max_capital_fraction
remaining = maximum - cumulative_buy_notional
```

默认 `max_capital_fraction = 1.00`，即当前持仓周期累计实际 BUY gross notional 不超过初始资金的 100%。

如果剩余额度不足一个标准买入档位，策略使用 `FixedNotional(remaining)`。

实际数量由 IntentExecutor 使用 modeled execution price 和交易手数向下计算，因此滑点不会把 gross notional 推高到 intent 预算之外。

### 2.4 止盈

有持仓时，使用当前 `close_raw` 和 Strategy State 中的 `remaining_cost_basis` 计算整体持仓收益率：

```text
profit_rate =
    current_close * quantity / remaining_cost_basis - 1
```

其中 remaining cost basis 包含 BUY 成交费用。

默认：

```text
profit_rate >= 10%
```

时产生：

```text
signal = TAKE_PROFIT
side   = SELL
sizing = CurrentPositionFraction(0.20)
```

即卖出当前持仓的 20%。

止盈判断优先于首次买入和追加买入判断。

### 2.5 价格口径

- `research_close`: 只用于判断 20 日缓慢下跌，避免公司行为扭曲跨期走势；
- `close_raw`: 用于加仓锚点比较和整体持仓收益率，使判断与实际成交成本处于同一价格口径；
- Backtest 执行价: 当前共享执行模型为下一交易日 `NEXT_OPEN` 加滑点和执行约束。

## 3. Strategy State

策略读取通用 `StrategyStateSnapshot`，每个持仓保存：

- quantity
- remaining_cost_basis
- last_trade_side
- last_trade_date
- last_trade_quantity
- last_trade_price
- last_buy_price
- cumulative_buy_notional

关键语义：

- `last_buy_price` 来自实际 confirmed BUY fill，不是信号价格；
- BUY 时 remaining cost basis 增加 gross + fees；
- SELL 时按卖出前平均成本比例释放 remaining cost basis；
- 从空仓重新 BUY 时，新的持仓周期会重新开始累计 buy notional；
- 已确认送股/拆分保持总成本基础不变，并调整数量和 last buy price；
- 早于 state.as_of 的成交不能回放进当前 state。

策略读取 state，但不直接修改 state。State 只能由共享 `StrategyStateReducer` 根据 confirmed events 推进。

## 4. 参数

| 参数 | 默认值 | 作用 |
| --- | ---: | --- |
| `security_id` | 无，必填 | 单一目标证券，格式必须为 `000000.SH` 或 `000000.SZ` |
| `lookback_trade_days` | 20 | 首次买入的价格窗口；至少为 2 |
| `cumulative_decline_threshold` | 0.10 | 首次买入要求的累计跌幅 |
| `single_day_crash_threshold` | 0.05 | “无单日暴跌”的最大允许单日跌幅 |
| `minimum_down_days` | 12 | 窗口中至少多少个下跌日；必须小于 lookback |
| `add_buy_decline_threshold` | 0.10 | 相比上一次实际买入价再次下跌多少时加仓 |
| `buy_fraction` | 0.10 | 每个标准 BUY intent 占初始资金比例 |
| `max_capital_fraction` | 1.00 | 当前持仓周期累计 BUY gross notional 上限 |
| `take_profit_threshold` | 0.10 | 整体持仓止盈收益率 |
| `sell_fraction` | 0.20 | 每次止盈卖出当前持仓比例 |

除整数参数外，上述比例参数必须在 `(0, 1]`。并且 `buy_fraction <= max_capital_fraction`。

## 5. 数据要求

StrategyDeclaration 当前只要求：

- 最近 `lookback_trade_days` 个交易日；
- 指定 `security_id` 的 `research_close`；
- 指定 `security_id` 的 `close_raw`；
- 覆盖率 100%；
- 不需要 system factors；
- 不需要 custom factors；
- 必须提供 confirmed execution state（Daily 模式）。

虽然策略信号只依赖上述价格字段，Backtest 共享执行层还会使用 Research Artifact 中的 open/high/low、成交量、证券交易单位和可用状态事实来模拟实际成交。

## 6. 配置示例

仓库提供 `examples/config-staged-drawdown.toml`，其核心策略配置：

```toml
strategy_id = "staged_drawdown_v1"
strategy_version = "1.0.0"

[strategy]
security_id = "600000.SH"
lookback_trade_days = 20
cumulative_decline_threshold = 0.10
single_day_crash_threshold = 0.05
minimum_down_days = 12
add_buy_decline_threshold = 0.10
buy_fraction = 0.10
max_capital_fraction = 1.00
take_profit_threshold = 0.10
sell_fraction = 0.20

[execution]
initial_cash = 1000000
price_model = "NEXT_OPEN"
slippage_bps = 10
max_volume_participation = 0.10
fee_schedule_id = "cn_cash_market_default_v1"
dividend_tax_model = "PROVIDER_AFTER_TAX"
```

`security_id` 也可以是项目 Research Artifact 中存在的 ETF，例如 `510300.SH`。

## 7. 运行

从仓库根目录执行，先激活 `xqat` 环境并准备 Research 数据：

```bash
conda activate xqat
python examples/generate_offline_example.py --root .example-work
xqatexp backtest run --config examples/config-staged-drawdown.toml --start-date 2026-08-31 --end-date 2026-09-07 --output .example-work/staged-backtest
xqatexp state init --strategy-id staged_drawdown_v1 --initial-capital 1000000 --output .local/staged-state.json
xqatexp daily decide --config examples/config-staged-drawdown.toml --decision-date 2026-09-04 --state .local/staged-state.json --output .local/staged-decision
xqatexp result show --input .example-work/staged-backtest --format markdown
```

输出路径必须是新路径，或显式选择 Artifact 的覆盖策略。Backtest 自动创建并推进策略状态；
Daily 的 `--state` 必须来自已确认成交及公司行为。`daily target` / `daily advise` 使用 allocation 目标。

真实 ETF 数据准备见 [数据操作指南](../../../../../docs/data.md#单证券-etf-回测)，
状态维护命令见 [Daily 设计](../../../../../docs/detailed-design/05-daily-state-and-advice.md)。

## 8. 结果与诊断

`strategy_diagnostics.json` 包含 signal、raw/research close、窗口累计收益、最差单日收益、
下跌日数、slow_decline、持仓数量/收益率、last_buy_price 和 cumulative_buy_notional。
无信号也会发布决策结果；无成交时可结合 `unfilled.parquet` 检查执行限制。

信号值为 `NONE`、`INITIAL_ENTRY`、`ADD_ON_DECLINE`、`TAKE_PROFIT`。
Backtest Result 额外保存最终 `strategy_state.json`；Daily Decision 保存决策时输入的 state 快照。
数据不足、非正价格、缺少成本基础或 state identity 不一致时明确失败。

## 9. 源码与测试

默认值和校验见 [parameters.py](parameters.py)，数据依赖见 [declaration.py](declaration.py)，
信号实现见 [strategy.py](strategy.py)。策略测试位于
`tests/unit/strategy/strategies/staged_drawdown_v1/`，回测测试见
[集成测试](../../../../../tests/integration/test_staged_drawdown_backtest.py)。
