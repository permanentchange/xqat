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
  - `declaration.py`: 价格历史窗口（默认 20 日）与 state requirement
  - `strategy.py`: 首次建仓、下跌加仓、整体持仓止盈与 diagnostics

该策略用于单一证券的分阶段下跌买入和整体成本止盈。它不使用财务因子、股票横截面排名或市场 regime。

策略只产生 TradeIntent。Backtest 中的实际成交、费用、滑点、交易手数、现金约束和 T+1 由共享执行层处理；Daily 模式下，真实成交必须通过 confirmed fill 回写 Strategy State。

## 2. 核心逻辑

### 2.1 首次买入

当前无持仓时，取最近 `lookback_trade_days` 个交易日的 `research_close`。

默认窗口包含 20 个收盘价、19 个相邻日收益，必须同时满足：

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

默认 gross 买入预算为初始资金的 10%，不是当前现金或净资产的 10%；成交费用另计。

#### 可选入场确认

`entry_confirmation_mode="none"` 为默认值，缓慢下跌信号当天即可产生首次 BUY。
`entry_confirmation_mode="ma_rebound"` 时，空仓首次买入须同时满足：

- 最近 `entry_confirmation_window_days` 个交易日内或当天产生过缓慢下跌信号；
- 当天 `research_close` 严格高于最近 `entry_confirmation_ma_days` 日均值（包含当天）；
- 当天 `research_close` 严格高于前一交易日 `research_close`。

默认使用 MA5、最多等待 10 个交易日。最新信号当天为第 0 日，第 10 日仍有效，第 11 日过期；
新的下跌信号刷新期限。确认日不必继续满足下跌条件，确认后仍在下一交易日 NEXT_OPEN 执行。
价格等于均线或前收盘不确认。该条件只控制首次建仓，加仓和止盈沿用各自规则。

机会由历史价格重算，不保存为策略状态；未成交时下一决策日重新检查，不根据建议推测持仓。
清仓后排除早于最后确认成交日期的下跌信号，清仓当天的新信号仍可使用。
diagnostics 包含 entry_setup_date、entry_setup_age_trade_days、entry_confirmation_ma、
entry_confirmation_above_ma、entry_confirmation_price_up、entry_confirmation_passed 和 entry_waiting。
有持仓时不选择入场机会。确认条件是研究假设，不保证提高夏普率。

### 2.2 连续买入

已有持仓且未触发止盈时，只有同时满足：

- 上一次 confirmed trade 是 BUY，或 `allow_add_after_sell=true` 时为 SELL；
- 有 `last_buy_price`；
- 当前 `close_raw` 相比上一次实际买入成交价下跌达到阈值；
- 当前持仓周期仍有累计买入额度；

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

加仓不要求重新满足首次买入的缓慢下跌条件。默认部分卖出后，`last_trade_side` 为 SELL，
会阻止加仓，直到新的 confirmed BUY 将其改回 BUY。按本策略自身信号，剩余持仓不会因为价格下跌
而重新买入；清仓后再次满足首次买入条件才开始新周期。
公司行为调整后的 `last_buy_price` 作为后续比较锚点。

可选 `allow_add_after_sell=true` 仅解除卖出后的禁令，不使用卖出价作为新锚点。
最后实际买入价再跌10%且有累计额度时，可追加；卖出不归还额度，现金和成交限制仍适用。
确认 BUY 后，通用 reducer 重置 exit_base_quantity / exit_sold_quantity，剩余持仓重启分级止盈；
发出建议或未成交不会重置。持仓周期累计投入仍保留，直到清仓后下一次 BUY 才重新累计。
diagnostics 的 add_decline_reached、add_blocked_after_sell、add_budget_remaining 和
add_budget_exhausted 解释价格、卖后禁令和预算约束；signal 表示最终优先级选择。

### 2.3 最大投入

Strategy State 保存当前持仓周期的 `cumulative_buy_notional`。

最大 gross 买入金额：

```text
maximum = initial_capital * max_capital_fraction
remaining = maximum - cumulative_buy_notional
```

默认 `max_capital_fraction = 1.00`，即当前持仓周期累计实际 BUY gross notional 不超过初始资金的 100%。

累计金额不含手续费，部分卖出不归还买入额度；清仓后下一次 BUY 重新累计。
剩余额度大于零但不足一个标准买入档位时，加仓使用 `FixedNotional(remaining)`；额度耗尽时不产生加仓 intent。

实际数量由 IntentExecutor 使用 modeled execution price 和交易手数向下计算，因此滑点不会把 gross notional 推高到 intent 预算之外。

### 2.4 止盈（repeat 模式）

`take_profit_mode="repeat"` 为默认值，沿用以下重复部分止盈规则。

有持仓时，使用当前 `close_raw` 和 Strategy State 中的 `remaining_cost_basis` 计算整体持仓收益率：

```text
profit_rate =
    current_close * quantity / remaining_cost_basis - 1
```

其中 remaining cost basis 包含 BUY 成交费用。该指标衡量当前剩余持仓的账面收益，
不包含历史已实现收益、现金分红或预计 SELL 费用。

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

即产生卖出当前持仓 20% 的意图。共享执行层按卖出单位向下取整，再应用可卖数量和成交量约束；
实际成交不一定达到 20%。默认部分卖出的数量不足一个卖出单位时，不生成执行指令。

止盈判断优先于首次买入和追加买入判断。

策略没有“已止盈”标记或冷却期；后续交易日若收益率仍达标，会继续产生止盈 intent。
没有成功成交的信号不会改变 state，也不会更新加仓锚点。

### 2.5 分级止盈（tiered 模式）

`take_profit_mode="tiered"` 使用相同的整体持仓收益率，按最高达标档位计算累计应卖数量：

| 盈利阈值 | 本档比例 | 累计卖出目标 |
| --- | --- | --- |
| 10% | 30% | 首次止盈前持仓的约 30% |
| 20% | 30% | 首次止盈前持仓的约 60% |
| 30% | 40% | 全部清仓 |

首次实际 SELL 成交前的持仓数量为数量基准。发出建议或未成交不会锁定基准、推进档位；
确认部分成交后，只记录实际卖出量。前两档累计目标按证券主表的 `sell_lot_size` 向下取整，
每次只补卖累计目标与已确认卖出数量之间的差额。同一档完成后不会继续每天卖出。
价格回落后，按当前仍达标的最高档计算目标，不保留未成交的历史高档建议。

一次跨档时直接执行最高达标档：首次盈利 25% 时累计卖出约 60%，35% 时直接清仓。
最高档使用 `FullPosition`；其他档位若应卖数量为正但不足一个卖出单位，也提前清仓。
首次目标不足一手时直接清仓；已完成的、向下取整后的档位不会因此追加清仓。
非清仓建议使用 `FixedQuantity`，实际成交仍受共享执行层约束。

默认首笔实际 SELL 后继续禁止加仓；可选开关按第 2.2 节重新买入。清仓后，下一决策日重新满足首次买入条件即可开始新周期，
不增加冷却期。分级档位是研究参数，不保证提高夏普率。

### 2.6 价格口径

- `research_close`: 用于判断缓慢下跌（默认 20 日窗口），避免公司行为扭曲跨期走势；
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
- exit_base_quantity（可选，首次确认 SELL 前的数量基准）
- exit_sold_quantity（可选，基准锁定后累计确认卖出的数量）

Snapshot 还保存 `initial_capital`、`as_of` 和策略身份。Backtest 从 execution 的 `initial_cash`
创建空仓 state；Daily 使用显式输入 state 的 `initial_capital`，不会用配置中的初始现金替换它。

关键语义：

- `last_buy_price` 来自实际 confirmed BUY fill，不是信号价格；
- BUY 时 remaining cost basis 增加 gross + fees；
- SELL 时按卖出前平均成本比例释放 remaining cost basis；
- BUY 重置减仓统计；首次 SELL 锁定基准，后续 SELL 累加实际数量，清仓后清空统计；
- 从空仓重新 BUY 时，新的持仓周期会重新开始累计 buy notional；
- 已确认送股/拆分保持总成本基础不变，并调整数量和 last buy price；
- 早于 state.as_of 的成交不能回放进当前 state。

送股/拆股按剩余持仓扩张比例同步调整减仓统计，历史数量转换为等价数量，保留 12 位小数。
基准数量减累计已卖等价数量始终等于当前持仓数量。
旧 State 1.0 无新增字段仍可读取；分级模式遇到正持仓、最近成交为 SELL 且缺少减仓统计时，
报 `STRATEGY_STATE_INVALID`，需从新初始化的 state 按时间顺序重放已确认成交与公司行为。
不从旧建议推测档位。回测从空仓生成新 state，不需要迁移旧回测的最终 state。

策略读取 state，但不直接修改 state。共享 `StrategyStateReducer` 根据已确认成交及公司行为
更新持仓和成本；回测还通过估值事件推进 `as_of`。

## 4. 参数

| 参数 | 默认值 | 作用 |
| --- | ---: | --- |
| `security_id` | 无，必填 | 单一目标证券，格式必须为 `000000.SH` 或 `000000.SZ` |
| `lookback_trade_days` | 20 | 每个缓慢下跌信号的价格窗口；整数且至少为 2 |
| `cumulative_decline_threshold` | 0.10 | 首次买入要求的累计跌幅 |
| `single_day_crash_threshold` | 0.05 | “无单日暴跌”的最大允许单日跌幅 |
| `minimum_down_days` | 12 | 下跌日数阈值；整数且 `1 <= minimum_down_days < lookback_trade_days` |
| `add_buy_decline_threshold` | 0.10 | 相比上一次实际买入价再次下跌多少时加仓 |
| `buy_fraction` | 0.10 | 每个标准 BUY intent 占初始资金比例 |
| `max_capital_fraction` | 1.00 | 当前持仓周期累计 BUY gross notional 上限 |
| `take_profit_threshold` | 0.10 | repeat 模式整体持仓止盈收益率 |
| `sell_fraction` | 0.20 | repeat 模式每次止盈卖出当前持仓比例 |
| `take_profit_mode` | repeat | repeat 或 tiered |
| `take_profit_levels` | [0.10, 0.20, 0.30] | tiered 模式盈利阈值 |
| `take_profit_sell_fractions` | [0.30, 0.30, 0.40] | tiered 模式各档卖出比例，末档清仓 |
| `entry_confirmation_mode` | none | none 或 ma_rebound，仅控制首次建仓 |
| `entry_confirmation_ma_days` | 5 | 复权收盘均线周期，整数且至少为 2 |
| `entry_confirmation_window_days` | 10 | 距最新下跌信号的最大交易日数，非负整数，0 表示必须当天确认 |
| `allow_add_after_sell` | false | 是否允许止盈后继续按最后买入价加仓，仅接受布尔值 |

七个比例参数必须在 `(0, 1]`，且 `buy_fraction <= max_capital_fraction`。
分级数组须非空、等长；阈值须有限、为正且严格递增；各档比例须为正且合计为 1。
tiered 模式禁止显式配置 `take_profit_threshold` 或 `sell_fraction`；其有效配置不保存这两个旧模式参数。
修改 `lookback_trade_days` 时也要检查 `minimum_down_days`；例如窗口改为 10 时，默认 12 必须同时调小。

## 5. 数据要求

StrategyDeclaration 的共同要求：

- 完整声明价格窗口（未启用入场确认为 `lookback_trade_days`）；
- 指定 `security_id` 的 `research_close`；
- 指定 `security_id` 的 `close_raw`；
- 覆盖率 100%；
- 不需要 system factors；
- 不需要 custom factors；
- 必须提供 confirmed execution state（Daily 模式）。

tiered 模式额外声明 `SECURITY_RULES` 要求，通过受限 `security_rules` 查询读取目标证券的
`sell_lot_size`。证券交易规则的 `rule_effective_from` 不得晚于查询日；覆盖率为 100%。
该查询不依赖股票状态表，支持只有基金主表与 ETF 行情的 Research。

虽然策略信号只依赖上述价格字段，Backtest 共享执行层还会使用 Research Artifact 中的 open/high/low、成交量、证券交易单位和可用状态事实来模拟实际成交。

入场确认模式声明 `max(lookback_trade_days + entry_confirmation_window_days,
entry_confirmation_ma_days, 2)` 个交易日，同时用于日历、行情 readiness 和历史查询。
默认确认配置需要 30 日历史；每个下跌信号仍只使用自身的 20 日窗口。
每次决策都会检查完整窗口，两种收盘价均须非缺失、有限且为正数；已有持仓或无信号时也不例外。
首个决策日及此前共需至少声明窗口长度的交易日数据；缺失时不缩短窗口或填补价格。
Research 日历还必须包含最后决策日之后的下一交易日；Daily 和回测最后一天的决策都需要它。
最后一天的信号会保存到 diagnostics，但对应下一交易日若在回测区间之外，不再模拟成交。

## 6. 配置示例

仓库提供 [config-staged-drawdown.toml](../../../../../examples/config-staged-drawdown.toml)。
以下是策略和执行字段片段，完整配置还需要 `schema_version`、`mode`、`research_artifact`、日期和输出路径：

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
真实 ETF 回测配置见 [config-staged-etf.toml](../../../../../examples/config-staged-etf.toml)。
该 ETF 示例关闭入场确认并启用分级止盈；其他未指定模式的配置不启用入场确认并保持 repeat。
ETF 示例的 `[strategy]` 片段如下，勿同时复制旧模式的两个止盈参数：

```toml
[strategy]
security_id = "510300.SH"
entry_confirmation_mode = "none"
entry_confirmation_ma_days = 5
entry_confirmation_window_days = 10
take_profit_mode = "tiered"
take_profit_levels = [0.10, 0.20, 0.30]
take_profit_sell_fractions = [0.30, 0.30, 0.40]
```
相对路径按运行时工作目录解析，因此示例命令应在仓库根目录执行。

### 6.1 固定离线研究协议

```bash
python examples/analyze_staged_drawdown.py --config examples/config-staged-etf.toml --start-date 2020-03-02 --end-date 2026-09-01 --output .local/staged-etf-mechanism-study
```

四组主实验固定累计上限30%、单次买入10%、止盈10%/20%/30%，只切换入场确认和卖后加仓。
入场确认固定 MA5、等待10日；加仓锚点为最后实际买入价。四组各用10/20/30 bps滑点，
另以100%上限复现原基线，共13次回测。其他下跌信号参数和执行约束继承输入配置。
默认开关仍为 false，脚本不会按实验成绩修改示例配置。

输出包括13个标准结果 Artifact、comparison.csv/json、annual_comparison.csv、cycles.csv、
decision_trace.csv、signals.csv、event_groups.csv、event_outcomes.csv 和 report.md。
study_manifest.json 保存输入配置/Research 哈希、试验参数、完成列表与输出哈希。
输出目录已存在时拒绝运行；失败保留状态和完成结果，不宣称完成。

相邻下跌信号间隔不超过10个交易日归为同组，各组只对比首次信号和首次有效确认。
事件收益从下一交易日复权开盘起算，到第20/60/120个持有日复权收盘，最大不利波动使用
期间收盘相对开盘的最低收益（不大于0）。事件不加仓、不止盈、不含费用，不替代完整回测。
未来窗口不足或行情不可用时标记 incomplete，不缩短窗口或填补价格。
逐周期净值损益包含费用与未平仓估值；卖后等待长度以连续有仓位且上次成交为 SELL 的决策日计。
事件组很少、可能重叠，ETF分红完整性未经验证；当前历史已反复查看，不作为样本外证据。

## 7. 从下载数据到回测和 Daily

以下主线使用真实 ETF `510300.SH`。从仓库根目录按步骤执行，每条命令成功后再执行下一条；
不需要先生成 `.example-work` 离线数据，也不需要下载全市场股票或 VIP 财务。

| 项目 | 本指南使用值 |
| --- | --- |
| 目标证券 | `510300.SH` |
| Raw / Research 数据范围 | 2020-01-01 至 2026-09-10 |
| 回测区间 | 2025-01-01 至 2026-09-01 |
| 模拟初始资金 | 1,000,000 元 |
| Raw 目录 | `data/raw/staged-etf` |
| Research 目录 | `data/research/staged-etf` |
| 回测结果目录 | `.local/staged-etf-backtest` |

下载范围包含回看历史和最后决策日之后的交易日，回测日期不必与下载范围相同。
该范围用于跑通流程，不代表足以验证策略长期有效性。

### 7.1 准备环境

Linux 进入本仓库后激活环境：

```bash
cd /home/xfzhou/workspace/xqat
conda activate xqat
python --version
```

Python 应为 3.12。首次安装，或当前环境尚未安装本项目时执行：

```bash
python -m pip install --require-hashes -r requirements.lock
python -m pip install --require-hashes -r requirements-build.lock
python -m pip install --no-deps --no-build-isolation -e .
```

检查安装：

```bash
xqatexp self-check --offline
xqatexp data fetch --help
```

Windows PowerShell 先进入自己的仓库根目录，再执行相同 Conda/Python/CLI 命令。
除环境变量设置外，后续命令均为单行，可在两个平台使用。

### 7.2 设置 Tushare Token 与下载参数

Linux 在当前终端设置，替换占位符为本机 Token：

```bash
export TUSHARE_TOKEN="<在本机填写你的 Token>"
```

Windows PowerShell 使用：

```powershell
$env:TUSHARE_TOKEN = "<在本机填写你的 Token>"
```

不要把真实 Token 写入配置或提交到 Git。本指南使用
[provider-5000.toml](../../../../../examples/provider-5000.toml)：420 次/分钟、4 workers。
积分不保证所有接口均有权限；遇到权限错误时应核对账号权限，不反复重试。
这些单次下载命令依次执行，各命令内部处理分页和重试；不要同时启动多个进程争用同一请求预算。

### 7.3 下载五类 Raw 数据

依次下载 SSE 交易日历、场内基金主表、目标 ETF 日线、ETF 复权因子和沪深300基准指数：

```bash
xqatexp data fetch --dataset trade_calendar --start 2020-01-01 --end 2026-09-10 --provider-config examples/provider-5000.toml --output data/raw/staged-etf/trade-calendar --existing skip
xqatexp data fetch --dataset fund_basic --start 2020-01-01 --end 2026-09-10 --provider-config examples/provider-5000.toml --output data/raw/staged-etf/fund-basic --existing skip
xqatexp data fetch --dataset fund_daily --security-id 510300.SH --start 2020-01-01 --end 2026-09-10 --provider-config examples/provider-5000.toml --output data/raw/staged-etf/fund-daily --existing skip
xqatexp data fetch --dataset fund_adj_factor --security-id 510300.SH --start 2020-01-01 --end 2026-09-10 --provider-config examples/provider-5000.toml --output data/raw/staged-etf/fund-adj --existing skip
xqatexp data fetch --dataset index_daily --start 2020-01-01 --end 2026-09-10 --provider-config examples/provider-5000.toml --output data/raw/staged-etf/index-daily --existing skip
```

每条成功后输出 `RAW_WRITTEN`。`index_daily` 当前固定取 `000300.SH`；
`fund_basic` 取场内基金列表，只有 ETF 日线和复权因子需要指定 `--security-id`。
`--existing skip` 校验并复用匹配的 Raw，因此下载中断后可重跑这五条命令；
单份未完成 Raw 重新下载，不支持从其某一页续传。

每个 Raw 子目录应包含 `manifest.json`、`request.json` 和 `response.jsonl.gz`。
不要把不同日期范围的重复数据混放到这个目录；更换证券或扩大范围时按 7.8 节重新准备目录。

### 7.4 校验 Raw 并建立 Collection

首次检查执行：

```bash
xqatexp data check-raw --input data/raw/staged-etf/trade-calendar --report .local/staged-etf-checks/raw-calendar.json
xqatexp data check-raw --input data/raw/staged-etf/fund-basic --report .local/staged-etf-checks/raw-basic.json
xqatexp data check-raw --input data/raw/staged-etf/fund-daily --report .local/staged-etf-checks/raw-daily.json
xqatexp data check-raw --input data/raw/staged-etf/fund-adj --report .local/staged-etf-checks/raw-adj.json
xqatexp data check-raw --input data/raw/staged-etf/index-daily --report .local/staged-etf-checks/raw-index.json
```

五项均应输出 `RAW_CHECK valid=true`，再生成索引：

```bash
xqatexp data collection index --input data/raw/staged-etf
```

成功输出 `COLLECTION_INDEXED` 并生成 `data/raw/staged-etf/collection.json`。
索引用于统一引用 Raw；不会补齐漏下载的数据，后续仍需完成 Research 和策略检查。
检查报告必须使用新文件路径，重复检查可将报告目录改为 `.local/staged-etf-checks-rerun/`。

### 7.5 构建并检查 Research

先打开 [config-research-etf.toml](../../../../../examples/config-research-etf.toml)，确认：
`start_date="2020-01-01"`、`end_date="2026-09-10"`、`csi300_etf_id="510300.SH"`。
然后执行：

```bash
xqatexp data build --raw-collection data/raw/staged-etf --config examples/config-research-etf.toml --output data/research/staged-etf
xqatexp data check-research --input data/research/staged-etf --report .local/staged-etf-checks/research.json
```

依次应输出 `RESEARCH_WRITTEN` 和 `RESEARCH_CHECK valid=true`。
Research 目录包含 Manifest 和 `tables/` 中七张 Parquet 表，策略读取该 Artifact，不能直接读取 Raw。
检查通过表示数据合同有效；策略运行还会检查自己的回看和字段覆盖。

**当前数据限制：** 这五类下载不包含 ETF 分红事件，`corporate_action` 表可能为空。
复权因子用于价格研究，不等于账户已收到分红；回测不会自动发现遗漏的分红，也不会模拟其现金入账。
以下结果可用于流程和机制研究，正式评价完整投资收益前需要补齐并核验公司行为数据。
当前 CLI 没有注册专用 ETF 分红下载命令，不要用股票分红接口假定替代。

### 7.6 运行回测

打开 [config-staged-etf.toml](../../../../../examples/config-staged-etf.toml)，确认
`research_artifact="data/research/staged-etf"`、`security_id="510300.SH"`、
回测日期 2025-01-01 至 2026-09-01、`initial_cash=1000000`。
未填写的策略参数使用第 4 节默认值。

```bash
xqatexp backtest run --config examples/config-staged-etf.toml --output .local/staged-etf-backtest
```

命令成功后结果写入指定目录。Backtest 自行初始化空仓模拟 state，
此步骤不需要 `state init` 或真实账户 state。最后决策日之后的成交不在回测区间内执行。

### 7.7 读取结果与成交

先查看报告和指标：

```bash
xqatexp result show --input .local/staged-etf-backtest --format markdown
python -m json.tool .local/staged-etf-backtest/metrics.json
```

再查看最前面的成交记录，不需要额外安装 Pandas：

```bash
python -c "import pyarrow.parquet as pq; print(pq.read_table('.local/staged-etf-backtest/trades.parquet').slice(0, 10).to_pylist())"
```

| 文件 | 查看目的 |
| --- | --- |
| `report.md` / `metrics.json` | 收益、回撤、费用及基准比较 |
| `portfolio_daily.parquet` | 每日 NAV、现金、持仓市值和风险暴露 |
| `trades.parquet` | 实际模拟成交及费用 |
| `unfilled.parquet` | 未成交或部分成交原因 |
| `strategy_diagnostics.json` | 逐日信号、加仓锚点和持仓收益率 |
| `strategy_state.json` | 回测结束时的模拟持仓状态 |

分级模式的 diagnostics 保存模式、最高达标档、数量基准、是否已锁定、累计已卖、
累计目标、本次应卖数量和是否清仓。State 只保存已确认事实，不保存未成交建议。

如果没有成交，先检查 diagnostics 中是否出现 `INITIAL_ENTRY`；
若有 BUY/SELL 信号，再查看成交、未成交原因和交易单位。无信号或不足交易单位都可能得到零成交。
`result show --format json` 输出 Manifest，不是 `metrics.json`。

### 7.8 重复运行、延长历史和参数对照

| 操作 | 已有输出时怎么处理 |
| --- | --- |
| 相同请求下载 Raw | 保留 `--existing skip`，校验后复用 |
| 修复已索引的损坏 Raw | 使用新 Raw 根目录重做五类下载、索引和 Research；不要原地替换索引引用的文件 |
| 重新生成 Collection 索引 | 重跑 `data collection index` |
| 重建 Research / 回测 / Daily Decision | 使用新 `--output`，或显式添加 `--existing overwrite` |
| Raw / Research 检查报告 | 使用新 `--report`；不支持覆盖选项 |
| 初始化或更新 state | 使用新输出文件，不原地覆盖输入 |

Collection 索引保存成员哈希。对已索引 Raw 原地 overwrite 会使旧索引失效，
重跑 index 不会自动刷新已登记路径；本指南用新根目录处理修复和范围变更。

例如，明确替换旧回测结果：

```bash
xqatexp backtest run --config examples/config-staged-etf.toml --output .local/staged-etf-backtest --existing overwrite
```

需要延长历史时，复制两个 ETF TOML 到 `.local/`，同步修改下载日期、Research 范围和回测日期；
保留足够 warmup 和最后决策日之后的日历。五条下载命令使用新的 Raw 根目录，
再索引到新的 Research 目录，并更新策略配置的 `research_artifact`。不要将重叠的整段 Raw 放入同一 Collection。
这条五类单次下载路径不会自动增量更新；通用批次更新另见 [数据指南](../../../../../docs/data.md#增量更新)。

保留基线后，可复制 `examples/config-staged-etf.toml` 为 `.local/staged-etf-B.toml` 等独立配置，
只在 `[strategy]` 中增加或修改下面一个参数；使用相同 Research、日期和 execution：

| 配置 | 相对基线的唯一变化 | 目的 |
| --- | --- | --- |
| A：仓库示例 | 无 | 当前策略基线 |
| B：`.local/staged-etf-B.toml` | `max_capital_fraction = 0.10` | 关闭追加买入 |
| C：`.local/staged-etf-C.toml` | `sell_fraction = 1.00` | 达标时一次性清仓 |
| D：`.local/staged-etf-D.toml` | `max_capital_fraction = 0.30` | 降低累计投入上限 |

准备好三份配置后执行，结果分别保存：

```bash
xqatexp backtest run --config .local/staged-etf-B.toml --output .local/staged-etf-backtest-B
xqatexp backtest run --config .local/staged-etf-C.toml --output .local/staged-etf-backtest-C
xqatexp backtest run --config .local/staged-etf-D.toml --output .local/staged-etf-backtest-D
```

分别用 `result show` 和第 7.7 节文件查看方法比较收益、回撤、费用、资金占用及期末未退出持仓。
当前项目没有独立信号事件研究 CLI，这些对照回测不能替代入场信号的统计验证。

### 7.9 可选：真实 ETF 的 Daily 决策与成交回写

本策略使用 `daily decide`；`daily target` / `daily advise` 属于 allocation 工作流。
下面使用已构建的真实 Research，但初始化的是独立空仓 state，不会导入回测持仓。
仅在该策略账户确实尚未建仓时初始化；已有持仓应使用按已确认成交及公司行为维护的最新 state。

```bash
xqatexp state init --strategy-id staged_drawdown_v1 --strategy-version 1.0.0 --initial-capital 1000000 --output .local/staged-etf-state-initial.json
xqatexp daily decide --config examples/config-staged-etf.toml --decision-date 2026-09-01 --state .local/staged-etf-state-initial.json --output .local/staged-etf-decision-20260901
xqatexp result show --input .local/staged-etf-decision-20260901 --format markdown
```

`daily decide` 会覆盖 TOML 的运行 mode/决策日期，使用显式 `--state` 和 `--output`，
因此可复用回测配置。它输出 `trade_intents.json` 和输入 state 的快照，不模拟或提交真实订单。
示例日期是历史日期；换成其他决策日时，应先扩展数据，且该日期必须是 Research 日历中的交易日。

只有实际成交确认后才执行 `state apply-fill`。下面**假设** 2026-09-02 以 4.00 买入 10000 份、佣金 12；
这是演示成交值，不是行情或信号结果，使用时替换为真实数量、价格和全部成交费用：

```bash
xqatexp state apply-fill --input .local/staged-etf-state-initial.json --execution-date 2026-09-02 --security-id 510300.SH --side BUY --quantity 10000 --execution-price 4.00 --commission 12 --output .local/staged-etf-state-20260902.json
xqatexp daily decide --config examples/config-staged-etf.toml --decision-date 2026-09-02 --state .local/staged-etf-state-20260902.json --output .local/staged-etf-decision-20260902
```

无成交时继续使用原 state；同一成交只登记一次，更新命令不会自动去重。
下次决策必须读取更新后的 state，且决策日期不能早于 `state.as_of`。
可选 `daily decide --account` 只校验持仓数量一致性，不补全 state 或计算可执行订单数量。
公司行为更新见 [Daily 设计](../../../../../docs/detailed-design/05-daily-state-and-advice.md)。

### 7.10 可选：不联网的离线流程检查

如果只想检查安装和命令，不使用真实数据，运行独立的股票模拟示例；不要与上述 ETF 路径混用：

```bash
python examples/generate_offline_example.py --root .example-work
xqatexp backtest run --config examples/config-staged-drawdown.toml --start-date 2026-08-31 --end-date 2026-09-07 --output .example-work/staged-backtest
xqatexp result show --input .example-work/staged-backtest --format markdown
```

如果出现 `ARTIFACT_OUTPUT_EXISTS: research`，可复用 `.example-work/research`，跳过生成步骤；
明确重新生成时给脚本添加 `--overwrite`。已有回测结果按第 7.8 节处理。

### 7.11 常见失败的处理顺序

| 错误或现象 | 处理 |
| --- | --- |
| 找不到 `xqatexp` / Python 包 | 激活 `xqat`，执行第 7.1 节安装检查 |
| Token 缺失、认证或权限失败 | 在运行命令的同一终端设置 Token，并核对所需接口权限 |
| `ARTIFACT_OUTPUT_EXISTS` | 按第 7.8 节区分 Artifact、检查报告与 state，不删除整个数据目录 |
| `DATA_CONFLICT` / Collection 不完整 | 核对五类 Raw 是否齐全、有损坏或重叠范围，再索引 |
| `STRATEGY_WARMUP_INSUFFICIENT` / `DATA_COVERAGE_INSUFFICIENT` | 扩展首个决策日前历史并核对目标证券和复权因子 |
| `DATA_REQUIRED_MISSING: next trading day` | 下载并构建最后决策日之后的日历，再运行 |
| `STRATEGY_STATE_INVALID` | 核对策略身份、state 时间、成本和实际成交记录 |

接口、配置和完整错误说明见 [数据操作指南](../../../../../docs/data.md)。

## 8. 结果与诊断

`strategy_diagnostics.json` 的 `decisions[].diagnostics.values` 包含 `security_id`、`signal`、
`current_raw_close`、`current_research_close`、`lookback_cumulative_return`、`worst_daily_return`、
`down_days`、`slow_decline`、`position_quantity`、`position_profit_rate`、`last_buy_price`
和 `cumulative_buy_notional`。空仓时 `position_profit_rate` 为 null。

无信号也会发布决策结果；Daily 的 `trade_intents.json` 中 `intents` 为空数组。
Daily Decision 保存 `trade_intents.json` 和输入的 `strategy_state.json`，不模拟成交。
Backtest 保存成交 `trades.parquet`、未成交记录 `unfilled.parquet` 和最终 `strategy_state.json`；
逐日信号见 `strategy_diagnostics.json`，回测不输出 `trade_intents.json`。
不足交易单位而未生成指令的 intent 不会出现在 `unfilled.parquet` 中。

信号值为 `NONE`、`INITIAL_ENTRY`、`ADD_ON_DECLINE`、`TAKE_PROFIT`。
数据不足、非正价格、缺少成本基础或 state identity 不一致时明确失败。

## 9. 源码与测试

默认值和校验见 [parameters.py](parameters.py)，数据依赖见 [declaration.py](declaration.py)，
信号实现见 [strategy.py](strategy.py)。策略测试位于
`tests/unit/strategy/strategies/staged_drawdown_v1/`，回测测试见
[集成测试](../../../../../tests/integration/test_staged_drawdown_backtest.py)。
