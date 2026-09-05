# 00 详细设计基线

## 1. 已批准的关键决策

| 决策编号 | 决策 |
|---|---|
| DD-001 | 采用本地 Python 模块化单体、单进程、固定业务用例，不引入服务化或工作流引擎。 |
| DD-002 | Markdown 是详细设计事实来源；图示使用 Mermaid，不生成 JPG。 |
| DD-003 | 内置策略采用“周频综合排名 + 沪深300ETF三档风险状态 + 个股/ETF/现金动态配置”。 |
| DD-004 | 组合回撤指由研究数据和策略参数确定性重建、以最近 60 个正式重放交易日峰值计算的理论基础组合回撤；不使用真实或模拟账户，预热区间不进入理论净值。 |
| DD-005 | 回测和每日运行调用同一个 Strategy Core；相同输入必须产生相同 TargetPortfolio。 |
| DD-006 | D 日收盘后决策，最早于下一交易日执行；ResearchDataView 与 ExecutionDataView 严格分离。 |
| DD-007 | 所有运行输入显式指定，不扫描目录自动选择 `latest`。 |
| DD-008 | 正式 Artifact 采用临时生成、校验、原子发布；已有输出默认报错。 |
| DD-009 | Daily Advice 不把预期卖出所得视为已知现金；未知值用空值加原因码表达。 |
| DD-010 | Backtest 只有 SUCCESS 或 FAILED；成功可携带 WARNING，不存在部分成功。 |

## 2. 时间语义

| 名称 | 定义 |
|---|---|
| `calendar_date` | 公历日期，ISO `YYYY-MM-DD`。 |
| `trade_date` | 沪深交易日历中的开放交易日。 |
| `decision_date` | 周内最后一个交易日 D；策略在 D 日收盘后运行。 |
| `execution_date` | `decision_date` 之后的第一个交易日。 |
| `available_from` | 字段或记录最早可进入 ResearchDataView 的决策日。 |
| `generated_at` | 带时区的 ISO-8601 时间戳，统一保存为 UTC。 |

可见性规则：

- D 日日线、复权因子、停牌和涨跌停事实在 D 日收盘后可用。
- 只有可靠公告日期、没有可靠公告时刻的财务记录，从公告日后的第一个交易日开始可见。
- 同一报告期存在多次披露时，按 `available_from` 选择当时最新版本，禁止使用后来修订值覆盖历史可见版本。
- 用户因子以其 `factor_date` 为业务日期；系统只校验覆盖，不替用户证明未来信息约束。

## 3. 标识、类型与精度

- 证券标识使用供应商无关的 `security_id`，格式为六位代码加交易所后缀，例如 `600000.SH`、`000001.SZ`；指数使用 `000300.SH`。
- 资产类型限定为 `A_SHARE`、`CSI300_ETF`、`CASH`、`CSI300_INDEX`。
- 金额和费用计算使用十进制定点数；持久化金额单位为人民币元，保留 4 位小数，展示保留 2 位。
- 权重使用十进制比例 `[0, 1]`，持久化至少 10 位有效数字；展示为百分比。
- 收益率内部使用十进制比例，禁止在同一字段混用百分数。
- 数量为非负整数股；现金没有证券数量。
- 缺失值使用 Arrow/Parquet null 或 JSON `null`，禁止用 `0`、空字符串或特殊日期代替未知。

## 4. 逻辑包边界

```text
xqatexp/
  domain/          # 共享不可变领域合同、枚举、Issue 和数值规则
  artifacts/       # Artifact Schema、读取、摘要和发布
  providers/       # 外部数据获取；MVP 仅 Tushare
  research/        # DuckDB 查询、View、依赖和用户因子
  strategy/        # 唯一 Strategy Core 与内置策略
  portfolio/       # 组合合法性和调仓规划
  backtest/        # 历史推进、执行、公司行为和模拟账户
  performance/     # 指标、贡献和阶段分析
  daily/           # Daily Run 与 Daily Advice
  reporting/       # 结构化结果到 Markdown 的无重算展示
  application/     # 用例编排
  cli.py           # 唯一命令入口与 composition root
```

物理文件布局以 17 §3 为准。依赖方向只能从用例层指向领域能力；`strategy` 不依赖 `providers`/`research` 的文件实现、`backtest` 账户或 `daily` 账户。

## 5. 本地目录约定

```text
workspace/
  data/
    raw/<provider>/<dataset>/...
    research/<research_dataset_id>/...
  inputs/
    custom-factors/...
    accounts/...
  results/<用户显式指定的输出目录>/...
```

这些是默认相对路径，不代表自动发现。每次运行解析后都必须得到明确绝对路径并写入运行上下文。

## 6. 六项架构不变量的设计约束

1. **历史可见性：** 查询必须强制 `available_from <= decision_date`；Strategy API 不提供任意日期查询。
2. **策略同源性：** Backtest 与 Daily Run 只注入同一个 `Strategy` 实例和同一个参数解析器。
3. **策略确定性：** Strategy 禁止 I/O、系统时间、随机源和账户访问；排序使用稳定最终键 `security_id ASC`。
4. **模拟账户守恒：** 账户只接受已定义事件；事件前后必须通过守恒断言。
5. **依赖局部性：** 就绪检查从当前 StrategyDeclaration 计算依赖闭包，不运行全库强制检查。
6. **真实账户隔离：** AccountSnapshot 每次显式输入；不保存为系统推演状态，也不从历史建议重建。

## 7. 确定性规范

- 所有集合在进入业务算法前显式排序。
- 相同得分以 `security_id ASC` 决胜。
- 配置解析后的最终值写入结果，计算过程不再读取环境中的普通业务参数。
- 任何需要当前时间的标识由用例层一次性注入，领域计算不自行读取时钟。
- 内容摘要使用 SHA-256；`run_id` 使用应用层生成的 UUIDv7 字符串，但不参与业务结果计算。
